// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use acc_telemetry_lib::setup_tray;
use tauri::{Emitter, Manager, RunEvent};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandChild;

struct Sidecar(Mutex<Option<CommandChild>>);

// ───────────────────────── 悬浮窗（最多 3 个槽位） ─────────────────────────

/// 悬浮窗槽位数：每个槽位独立主题 / 位置 / 尺寸，统一显隐、统一编辑态。
/// 窗口 label 约定为 `overlay-1` / `overlay-2` / `overlay-3`（capability 用 `overlay-*`）。
const OVERLAY_SLOTS: u8 = 3;

/// 当前启用的槽位。前端从 localStorage 推过来。
/// `None` = 前端还没推过（旧行为：只启用 1 号）；`Some([])` = 用户明确全关了。
static OVERLAY_ENABLED: Mutex<Option<Vec<u8>>> = Mutex::new(None);

/// 各槽位第一次显示时贴主显示器右上角（向下错开）；之后保留系统给的位置。
static OVERLAY_PLACED: Mutex<[bool; 3]> = Mutex::new([false; 3]);

/// 是否处于「锁定」态：true = 鼠标穿透到游戏（默认）。所有悬浮窗共用同一个编辑态。
static OVERLAY_LOCKED: AtomicBool = AtomicBool::new(true);

/// 全局快捷键：切换悬浮窗显示 / 隐藏（与面板按钮共用同一状态）。
const OVERLAY_SHORTCUT: &str = "Ctrl+Shift+O";

/// 全局快捷键：锁定 / 解锁悬浮窗（解锁后才能拖动和拉边缩放）。
const OVERLAY_EDIT_SHORTCUT: &str = "Ctrl+Shift+L";

fn overlay_label(slot: u8) -> String {
    format!("overlay-{slot}")
}

/// 从窗口 label 反推槽位（overlay-1 / overlay-2 / overlay-3）。
fn overlay_slot_of(label: &str) -> Option<u8> {
    let slot: u8 = label.strip_prefix("overlay-")?.parse().ok()?;
    (1..=OVERLAY_SLOTS).contains(&slot).then_some(slot)
}

fn slot_index(slot: u8) -> usize {
    (slot.clamp(1, OVERLAY_SLOTS) - 1) as usize
}

fn enabled_slots() -> Vec<u8> {
    OVERLAY_ENABLED
        .lock()
        .unwrap()
        .clone()
        .unwrap_or_else(|| vec![1])
}

fn set_enabled_slots(mut slots: Vec<u8>) {
    slots.retain(|s| (1..=OVERLAY_SLOTS).contains(s));
    slots.sort_unstable();
    slots.dedup();
    *OVERLAY_ENABLED.lock().unwrap() = Some(slots);
}

/// 当前已创建（可能隐藏）的悬浮窗。
fn overlay_windows<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
) -> Vec<(u8, tauri::WebviewWindow<R>)> {
    enabled_slots()
        .into_iter()
        .filter_map(|slot| {
            app.get_webview_window(&overlay_label(slot))
                .map(|w| (slot, w))
        })
        .collect()
}

fn any_overlay_visible<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> bool {
    overlay_windows(app)
        .iter()
        .any(|(_, w)| w.is_visible().unwrap_or(false))
}

/// 按需创建槽位窗口。**只能在非主线程调用**：Windows 上在主线程创建 WebView2
/// 会和事件循环死锁（见 Tauri 文档 `WebviewWindowBuilder::new` 的 Known issues）。
fn ensure_overlay_window<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    slot: u8,
) -> Option<tauri::WebviewWindow<R>> {
    let label = overlay_label(slot);
    if let Some(win) = app.get_webview_window(&label) {
        return Some(win);
    }
    let built = tauri::WebviewWindowBuilder::new(
        app,
        label.as_str(),
        tauri::WebviewUrl::App("overlay.html".into()),
    )
    .title(format!("ACC Overlay {slot}"))
    .inner_size(380.0, 150.0)
    .min_inner_size(220.0, 90.0)
    .decorations(false)
    .transparent(true)
    .always_on_top(true)
    .skip_taskbar(true)
    .shadow(false)
    .focused(false)
    .visible(false)
    .build();
    match built {
        Ok(win) => Some(win),
        Err(e) => {
            // 并发创建时后手会拿到 "window already exists"，再取一次就行
            eprintln!("[overlay] 创建 {label} 失败: {e}");
            app.get_webview_window(&label)
        }
    }
}

// ── 悬浮窗位置 / 尺寸持久化 ──
// 用户拖动 / 拉边缩放后记住几何，下次启动直接恢复（不再每次都贴右上角）。
// 存到 %LOCALAPPDATA%\acc-telemetry\window-state.json，和主题 / 配置同目录。
#[derive(serde::Serialize, serde::Deserialize, Clone, Copy)]
struct OverlayRect {
    x: f64,
    y: f64,
    w: f64,
    h: f64,
}

#[derive(serde::Serialize, serde::Deserialize, Default)]
struct WindowStateFile {
    /// 旧版单悬浮窗的几何（读出来当 1 号槽位，保证老用户升级后位置不丢）
    overlay: Option<OverlayRect>,
    /// 多悬浮窗：key = 槽位序号（"1" / "2" / "3"）
    #[serde(default)]
    overlays: std::collections::BTreeMap<String, OverlayRect>,
}

fn app_data_dir() -> Option<std::path::PathBuf> {
    let base = std::env::var_os("LOCALAPPDATA")?;
    Some(std::path::PathBuf::from(base).join("acc-telemetry"))
}

fn load_window_state() -> WindowStateFile {
    let Some(dir) = app_data_dir() else {
        return WindowStateFile::default();
    };
    match std::fs::read_to_string(dir.join("window-state.json")) {
        Ok(s) => serde_json::from_str(&s).unwrap_or_default(),
        Err(_) => WindowStateFile::default(),
    }
}

fn persist_window_state(st: &WindowStateFile) {
    let Some(dir) = app_data_dir() else { return; };
    let _ = std::fs::create_dir_all(&dir);
    if let Ok(s) = serde_json::to_string_pretty(st) {
        let _ = std::fs::write(dir.join("window-state.json"), s);
    }
}

fn load_overlay_rect(slot: u8) -> Option<OverlayRect> {
    let st = load_window_state();
    st.overlays
        .get(&slot.to_string())
        .copied()
        .or_else(|| (slot == 1).then_some(st.overlay).flatten())
}

fn place_overlay<R: tauri::Runtime>(win: &tauri::WebviewWindow<R>, slot: u8) {
    // 有保存过的几何就直接恢复（用户拖过 / 拉过之后记住），否则首次贴主显示器右上角
    if let Some(r) = load_overlay_rect(slot) {
        let _ = win.set_position(tauri::PhysicalPosition::new(r.x, r.y));
        let _ = win.set_size(tauri::PhysicalSize::new(r.w, r.h));
        return;
    }
    let Ok(Some(monitor)) = win.current_monitor() else {
        return;
    };
    let screen = monitor.size();
    let size = win
        .outer_size()
        .unwrap_or(tauri::PhysicalSize::new(380, 150));
    let margin = 20.0;
    let x = screen.width as f64 - size.width as f64 - margin;
    // 多个悬浮窗默认在右上角纵向排开，不互相盖住
    let y = margin + (slot as f64 - 1.0) * (size.height as f64 + 16.0);
    let _ = win.set_position(tauri::PhysicalPosition::new(x, y));
}

/// 编辑态工具条高度（逻辑像素）。
/// 解锁时窗口向上长高这么多、并上移同样的距离：工具条因此能独立占一行、浮在主题正上方，
/// 而主题区尺寸不变 —— 否则 contain 的缩放比会变，看起来就是「点调整按钮悬浮窗变小」。
const EDIT_BAR_HEIGHT: f64 = 22.0;

/// 各槽位是否已经为编辑态工具条增加了高度（保证重复调用幂等）。
static OVERLAY_PADDED: Mutex<[bool; 3]> = Mutex::new([false; 3]);
/// 解锁时为避免工具条顶出屏幕而少上移的像素（物理像素），锁定时原样还回去。
static OVERLAY_PAD_OFFSET: Mutex<[i32; 3]> = Mutex::new([0; 3]);

/// 给工具条腾一行：解锁时变高 + 上移，锁定时还原。失败就回滚标记，下次还能重试。
fn overlay_set_edit_pad<R: tauri::Runtime>(win: &tauri::WebviewWindow<R>, slot: u8, pad: bool) {
    let idx = slot_index(slot);
    let was = {
        let mut padded = OVERLAY_PADDED.lock().unwrap();
        let was = padded[idx];
        padded[idx] = pad;
        was
    };
    if was == pad {
        return;
    }
    let revert = |value: bool| {
        OVERLAY_PADDED.lock().unwrap()[idx] = value;
    };
    let Ok(scale) = win.scale_factor() else {
        revert(!pad);
        return;
    };
    let bar = (EDIT_BAR_HEIGHT * scale).round() as i32;
    let (Ok(size), Ok(pos)) = (win.inner_size(), win.outer_position()) else {
        revert(!pad);
        return;
    };
    if bar <= 0 {
        revert(!pad);
        return;
    }
    if pad {
        // 窗口贴着屏幕顶部时别把工具条顶出去，少上移一点，主题会相应下移
        let up = bar.min(pos.y.max(0));
        {
            let mut offsets = OVERLAY_PAD_OFFSET.lock().unwrap();
            offsets[idx] = up;
        }
        let _ = win.set_size(tauri::PhysicalSize::new(
            size.width,
            size.height + bar as u32,
        ));
        let _ = win.set_position(tauri::PhysicalPosition::new(pos.x, pos.y - up));
    } else {
        let up = {
            let mut offsets = OVERLAY_PAD_OFFSET.lock().unwrap();
            std::mem::replace(&mut offsets[idx], 0)
        };
        let height = size.height.saturating_sub(bar as u32).max(1);
        let _ = win.set_size(tauri::PhysicalSize::new(size.width, height));
        let _ = win.set_position(tauri::PhysicalPosition::new(pos.x, pos.y + up));
    }
}

/// 锁定 / 解锁单个悬浮窗。解锁时关掉穿透并抢焦点，让窗口能接收拖动与拉边缩放。
fn overlay_set_locked<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    win: &tauri::WebviewWindow<R>,
    slot: u8,
    locked: bool,
) {
    let _ = win.set_ignore_cursor_events(locked);
    if !locked {
        // 穿透时窗口从不获得焦点，解锁后要主动要一次，否则原生拖动 / 拉边不生效
        let _ = win.set_focus();
    }
    // 编辑态工具条单独占一行：窗口长高 / 复原都在这里统一处理
    overlay_set_edit_pad(win, slot, !locked);
    OVERLAY_LOCKED.store(locked, Ordering::Relaxed);
    // 每次都广播：状态万一和前端不同步（比如窗口被外部关掉又显示），
    // 幂等重发一次总比「按了键但工具条不出现」好排查。
    let _ = app.emit("overlay-lock", locked);
}

fn overlay_apply_locked<R: tauri::Runtime>(app: &tauri::AppHandle<R>, locked: bool) {
    for (slot, win) in overlay_windows(app) {
        overlay_set_locked(app, &win, slot, locked);
    }
}

/// 锁定 / 解锁全部悬浮窗。可能要先建窗口，所以从非主线程调用（快捷键回调里 spawn）。
fn overlay_toggle_locked<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    let next = !OVERLAY_LOCKED.load(Ordering::Relaxed);
    if !next && !any_overlay_visible(app) {
        // 按了编辑快捷键说明就是想调整：窗口没显示就先显示出来，
        // 否则用户完全看不到编辑条，会以为快捷键没生效。
        overlay_set_visible(app, true);
    }
    overlay_apply_locked(app, next);
}

/// 显示 / 隐藏全部启用的悬浮窗，并把可见性广播给前端（按钮据此同步 on/off）。
/// 显示时可能会按需创建窗口，所以**不能在主线程调用**（快捷键回调里要 spawn 线程）。
fn overlay_set_visible<R: tauri::Runtime>(app: &tauri::AppHandle<R>, show: bool) {
    for slot in enabled_slots() {
        let win = if show {
            ensure_overlay_window(app, slot)
        } else {
            app.get_webview_window(&overlay_label(slot))
        };
        let Some(win) = win else { continue };
        let idx = slot_index(slot);
        if show {
            let _ = win.set_always_on_top(true);
            // 每次显示都回到穿透态：平时永远不拦截游戏里的操作
            overlay_set_locked(app, &win, slot, true);
            let need_place = {
                let mut placed = OVERLAY_PLACED.lock().unwrap();
                let need = !placed[idx];
                placed[idx] = true;
                need
            };
            if need_place {
                place_overlay(&win, slot);
            }
            let _ = win.show();
        } else {
            overlay_set_locked(app, &win, slot, true);
            let _ = win.hide();
        }
    }
    // 广播「实际」可见状态：一个槽位都没启用时，显示请求不该让按钮亮起来
    let _ = app.emit("overlay-visibility", any_overlay_visible(app));
}

#[tauri::command]
async fn show_overlay(app: tauri::AppHandle) {
    overlay_set_visible(&app, true);
}

#[tauri::command]
async fn hide_overlay(app: tauri::AppHandle) {
    overlay_set_visible(&app, false);
}

#[tauri::command]
async fn toggle_overlay(app: tauri::AppHandle) -> bool {
    let next = !any_overlay_visible(&app);
    overlay_set_visible(&app, next);
    any_overlay_visible(&app)
}

/// 前端主动切换锁定态（编辑模式里的「锁定」按钮 / 主窗口的「调整悬浮窗」）。
/// 只作用于已经存在的窗口，不创建新窗口，所以同步命令也安全。
#[tauri::command]
fn set_overlay_locked(app: tauri::AppHandle, locked: bool) {
    overlay_apply_locked(&app, locked);
}

/// 是否有任意一个悬浮窗处于聚焦态。
/// 多开时窗口之间切换焦点是常态：前端「失焦 800ms 自动回锁」必须排除
/// 「焦点只是跑到另一个悬浮窗去了」的情况，否则点一下第二个窗口就会把所有窗口锁回去。
#[tauri::command]
fn overlay_any_focused(app: tauri::AppHandle) -> bool {
    overlay_windows(&app)
        .iter()
        .any(|(_, w)| w.is_focused().unwrap_or(false))
}

/// 设置页启用 / 停用悬浮窗槽位：新启用的会建好窗口（悬浮窗正在显示就直接跟着显示），
/// 停用的直接销毁，省内存和 WebView2 进程。
#[tauri::command]
async fn set_overlay_slots(app: tauri::AppHandle, slots: Vec<u8>) {
    let was_visible = any_overlay_visible(&app);
    set_enabled_slots(slots);
    let enabled = enabled_slots();

    for slot in 1..=OVERLAY_SLOTS {
        if enabled.contains(&slot) {
            continue;
        }
        if let Some(win) = app.get_webview_window(&overlay_label(slot)) {
            let _ = win.destroy();
            let idx = slot_index(slot);
            OVERLAY_PADDED.lock().unwrap()[idx] = false;
            OVERLAY_PAD_OFFSET.lock().unwrap()[idx] = 0;
            OVERLAY_PLACED.lock().unwrap()[idx] = false;
        }
    }

    for slot in enabled {
        let Some(win) = ensure_overlay_window(&app, slot) else {
            continue;
        };
        if !was_visible {
            continue;
        }
        let _ = win.set_always_on_top(true);
        overlay_set_locked(&app, &win, slot, true);
        let idx = slot_index(slot);
        let need_place = {
            let mut placed = OVERLAY_PLACED.lock().unwrap();
            let need = !placed[idx];
            placed[idx] = true;
            need
        };
        if need_place {
            place_overlay(&win, slot);
        }
        let _ = win.show();
    }
    let _ = app.emit("overlay-visibility", was_visible);
}

/// 主题声明了自己的尺寸后，由前端把窗口调整到对应大小。
/// 用逻辑像素：设计尺寸是 DPI 无关的，物理像素在 150% 缩放下会被放大 1.5 倍。
#[tauri::command]
fn set_overlay_size(window: tauri::WebviewWindow, width: f64, height: f64) {
    let _ = window.set_size(tauri::LogicalSize::new(
        width.max(200.0),
        height.max(80.0),
    ));
}

/// 悬浮窗拖动 / 拉边缩放结束后，由前端把最新几何（物理像素）写回磁盘。
/// 用物理像素：避免 125% / 150% 缩放下 innerSize/outerSize 的 DPI 换算坑。
#[tauri::command]
fn save_overlay_rect(window: tauri::WebviewWindow, x: f64, y: f64, w: f64, h: f64) {
    let Some(slot) = overlay_slot_of(window.label()) else {
        return;
    };
    let mut st = load_window_state();
    st.overlays
        .insert(slot.to_string(), OverlayRect { x, y, w, h });
    persist_window_state(&st);
}

/// 前端在安装更新前主动调用：先干掉 sidecar、释放 8008 端口。
/// updater 的 install 会直接结束主进程（不走 RunEvent::Exit），所以不能只依赖退出钩子。
///
/// 双保险：ACL 已经不给 overlay 窗口这条权限，这里再核一次调用来源，
/// 万一以后 capability 被改宽（或者 IPC 被注入到某个新窗口），也杀不掉 sidecar。
#[tauri::command]
fn stop_sidecar(app: tauri::AppHandle, window: tauri::WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        eprintln!("[Rust] 拒绝来自 {} 的 stop_sidecar 调用", window.label());
        return Err("not allowed from this window".into());
    }
    if let Some(state) = app.try_state::<Sidecar>() {
        if let Some(child) = state.0.lock().unwrap().take() {
            println!("[Rust] 收到前端指令，正在清理 sidecar 进程...");
            let _ = child.kill();
        }
    }
    Ok(())
}

// ───────────────────────── 用户主题：theme:// 协议 ─────────────────────────

/// 主题包里允许出现的文件类型（白名单，同时给出 Content-Type）。
const THEME_ASSET_TYPES: &[(&str, &str)] = &[
    ("html", "text/html; charset=utf-8"),
    ("css", "text/css; charset=utf-8"),
    ("js", "text/javascript; charset=utf-8"),
    ("mjs", "text/javascript; charset=utf-8"),
    ("json", "application/json; charset=utf-8"),
    ("svg", "image/svg+xml"),
    ("png", "image/png"),
    ("jpg", "image/jpeg"),
    ("jpeg", "image/jpeg"),
    ("webp", "image/webp"),
    ("gif", "image/gif"),
    ("woff2", "font/woff2"),
    ("woff", "font/woff"),
    ("ttf", "font/ttf"),
    ("otf", "font/otf"),
    // 模板包里带的 GUIDE.md 之类说明文件：主题不加载，但导入时要原样留在主题目录里
    ("md", "text/markdown; charset=utf-8"),
    ("txt", "text/plain; charset=utf-8"),
];

/// 用户主题根目录：`%LOCALAPPDATA%\acc-telemetry\themes`。
/// 必须和 Python 侧 `config.py::APP_DIR`（config.json / telemetry.db 所在处）同一个目录，
/// 否则用户在设置页里看到的主题和这里能加载的主题对不上。
fn user_themes_root() -> Option<std::path::PathBuf> {
    let base = std::env::var_os("LOCALAPPDATA")?;
    Some(
        std::path::PathBuf::from(base)
            .join("acc-telemetry")
            .join("themes"),
    )
}

fn theme_not_found() -> tauri::http::Response<Vec<u8>> {
    // 不区分「不存在」和「越权」：不泄露任何路径信息
    tauri::http::Response::builder()
        .status(tauri::http::StatusCode::NOT_FOUND)
        .header("Content-Type", "text/plain; charset=utf-8")
        .header("Cache-Control", "no-store")
        .header("Access-Control-Allow-Origin", "*")
        .body(b"theme asset not found".to_vec())
        .expect("static response")
}

/// 把 `theme://localhost/<id>/<file>`（Windows 下是 `http://theme.localhost/<id>/<file>`）
/// 映射到用户主题目录。内置主题不在这里 —— 它们编译进二进制，走 http://tauri.localhost 的资源协议。
fn serve_user_theme(request: &tauri::http::Request<Vec<u8>>) -> tauri::http::Response<Vec<u8>> {
    let Some(root) = user_themes_root() else {
        return theme_not_found();
    };
    let raw = request.uri().path().trim_start_matches('/');
    let Some((id, rel)) = raw.split_once('/') else {
        return theme_not_found();
    };
    // id 只允许安全字符：顺手挡住 ".." / "C:" / 空白 / 各种奇怪编码
    if id.is_empty()
        || !id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return theme_not_found();
    }
    let rel = percent_encoding::percent_decode_str(rel)
        .decode_utf8_lossy()
        .into_owned();
    if rel.is_empty()
        || rel.starts_with('/')
        || rel.contains('\\')
        || rel.contains('\0')
        || rel.split('/').any(|seg| seg == ".." || seg == ".")
    {
        return theme_not_found();
    }
    let ext = std::path::Path::new(&rel)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    let Some((_, mime)) = THEME_ASSET_TYPES.iter().find(|(e, _)| *e == ext) else {
        return theme_not_found();
    };

    // canonicalize 之后再比前缀：目录不存在（主题没装）或符号链接越界都会被拒
    let Ok(theme_dir) = root.join(id).canonicalize() else {
        return theme_not_found();
    };
    let Ok(full) = theme_dir.join(&rel).canonicalize() else {
        return theme_not_found();
    };
    if !full.starts_with(&theme_dir) {
        return theme_not_found();
    }
    match std::fs::read(&full) {
        Ok(bytes) => tauri::http::Response::builder()
            .status(tauri::http::StatusCode::OK)
            .header("Content-Type", *mime)
            // 主题是本地开发中的文件：别让 WebView 缓存，改完刷新就能看到
            .header("Cache-Control", "no-store")
            // 宿主页（tauri://localhost）要 fetch theme.json 读 design，跨源必须放行
            .header("Access-Control-Allow-Origin", "*")
            .body(bytes)
            .unwrap_or_else(|_| theme_not_found()),
        Err(_) => theme_not_found(),
    }
}

/// 主题 id 只允许 `[A-Za-z0-9_-]`：和 `serve_user_theme` 用同一套规则，
/// 否则会出现「列表里看得到、点开却 404」的鬼故事。
fn valid_theme_id(id: &str) -> bool {
    !id.is_empty()
        && id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

/// 用户主题的原始信息。校验规则**只写在前端一份**（`src/telemetry/themes.js`）：
/// Rust 这边只负责把文件找出来，避免两边规则漂移。
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct UserThemeEntry {
    id: String,
    /// theme.json 原文；文件不存在/N 不是 UTF-8 时为 None
    meta: Option<String>,
    has_index: bool,
}

#[tauri::command]
fn list_user_themes() -> Vec<UserThemeEntry> {
    let Some(root) = user_themes_root() else {
        return Vec::new();
    };
    let Ok(entries) = std::fs::read_dir(&root) else {
        return Vec::new();
    };
    let mut out: Vec<UserThemeEntry> = entries
        .flatten()
        .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
        .filter_map(|e| {
            let id = e.file_name().to_str()?.to_string();
            if !valid_theme_id(&id) {
                return None;
            }
            let dir = e.path();
            Some(UserThemeEntry {
                meta: std::fs::read_to_string(dir.join("theme.json")).ok(),
                has_index: dir.join("index.html").is_file(),
                id,
            })
        })
        .collect();
    out.sort_by(|a, b| a.id.cmp(&b.id));
    out
}

/// 在资源管理器里打开用户主题目录（没有就建）。路径由 Rust 自己算，不接受前端传入 —— 免得多一条任意路径打开的通道。
#[tauri::command]
fn open_themes_dir(app: tauri::AppHandle) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    let root = user_themes_root().ok_or("找不到 LOCALAPPDATA")?;
    std::fs::create_dir_all(&root).map_err(|e| format!("创建目录失败: {e}"))?;
    app.opener()
        .open_path(root.to_string_lossy().to_string(), None::<String>)
        .map_err(|e| format!("打开目录失败: {e}"))
}

/// 删除用户主题：走系统回收站（可恢复），不做永久删除。
/// 只允许删 `themes/<合法 id>` 这一层，canonicalize 之后再校验一次前缀。
#[tauri::command]
fn delete_user_theme(id: String) -> Result<(), String> {
    if !valid_theme_id(&id) {
        return Err("主题 id 不合法".into());
    }
    let root = user_themes_root().ok_or("找不到 LOCALAPPDATA")?;
    let root = root.canonicalize().map_err(|e| format!("主题目录不可用: {e}"))?;
    let dir = root
        .join(&id)
        .canonicalize()
        .map_err(|_| "主题不存在".to_string())?;
    if dir == root || !dir.starts_with(&root) {
        return Err("拒绝删除：路径越界".into());
    }
    trash::delete(&dir).map_err(|e| format!("移入回收站失败: {e}"))
}

// ───────────────────────── 主题模板导出 ─────────────────────────

/// 模板包编译时直接嵌进二进制：运行时不用去安装目录里找文件，开发模式和打包版本行为一致。
/// 源文件在 `public/templates/`，内容来自 `docs/theme-template*/`（改模板记得重新打包这两个 zip）。
const TEMPLATE_OVERLAY_ZIP: &[u8] = include_bytes!("../../public/templates/theme-template.zip");
const TEMPLATE_MONITOR_ZIP: &[u8] =
    include_bytes!("../../public/templates/theme-template-monitor.zip");

/// 把主题模板安装包另存到用户选的位置（设置页「导出模板」）。
/// kind 只认 overlay / monitor，避免这条命令变成「往任意路径写任意内容」的通道。
#[tauri::command]
fn export_theme_template(kind: String, path: String) -> Result<(), String> {
    let bytes = match kind.as_str() {
        "overlay" => TEMPLATE_OVERLAY_ZIP,
        "monitor" => TEMPLATE_MONITOR_ZIP,
        other => return Err(format!("未知的模板类型：{other}")),
    };
    std::fs::write(&path, bytes).map_err(|e| format!("写入失败: {e}"))
}

// ───────────────────────── 主题包导入（.zip） ─────────────────────────

/// 主题包上限：解压后总体积 / 单文件 / 文件数。正常主题只有几十 KB，
/// 这些上限只用来挡住损坏包和 zip bomb。
const THEME_ZIP_MAX_BYTES: u64 = 50 * 1024 * 1024;
const THEME_ZIP_MAX_FILE_BYTES: u64 = 20 * 1024 * 1024;
const THEME_ZIP_MAX_ENTRIES: usize = 500;

/// 把 zip 条目名拆成安全路径段。绝对路径 / 反斜杠 / `..` / 空段一律拒绝，
/// 这样即使 zip 里写了 `../../config.json` 也落不到主题目录外面。
///
/// 段里出现 `:` 也拒绝：Windows 上 `C:/x` 是绝对路径、`C:x` 是带前缀的路径，
/// `PathBuf::join` 遇到它们会**整个替换掉基准目录**，等于让压缩包往任意位置写文件。
fn zip_entry_segments(raw: &str) -> Option<Vec<&str>> {
    if raw.is_empty() || raw.starts_with('/') || raw.contains('\\') || raw.contains('\0') {
        return None;
    }
    let mut out = Vec::new();
    for seg in raw.split('/') {
        // `a/./b` 这种中间的 `.` 段忽略掉即可，不算越界
        if seg == "." {
            continue;
        }
        if seg.is_empty() || seg == ".." || seg.contains(':') {
            return None;
        }
        out.push(seg);
    }
    Some(out)
}

/// zip 条目名规整：反斜杠统一成 `/`，去掉开头的 `./`。
/// 有些打包工具会把 `./theme.json` 之类的名字写进包，不规整的话根目录判定会认不出来。
fn normalize_zip_name(raw: &str) -> String {
    let mut name = raw.replace('\\', "/");
    while let Some(rest) = name.strip_prefix("./") {
        name = rest.to_string();
    }
    name
}

/// 导入一个主题包（.zip）到用户主题目录：`themes/<theme.json 里的 id>/`。
///
/// 包里的 theme.json / index.html 可以在根目录，也可以套一层文件夹（`my-hud/theme.json`）。
/// 先解压到 `themes/.import-<pid>/`，校验通过后才原子改名成正式目录 ——
/// 导入失败不会留下半成品主题。
#[tauri::command]
fn import_theme_zip(path: String, mode: Option<String>) -> Result<String, String> {
    use std::io::Read;

    let src = std::path::Path::new(&path);
    let is_zip = src
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.eq_ignore_ascii_case("zip"))
        .unwrap_or(false);
    if !is_zip {
        return Err("只支持 .zip 格式的主题包".into());
    }
    let file = std::fs::File::open(src).map_err(|e| format!("打不开压缩包: {e}"))?;
    let mut archive =
        zip::ZipArchive::new(file).map_err(|e| format!("不是有效的 zip 压缩包: {e}"))?;
    if archive.len() > THEME_ZIP_MAX_ENTRIES * 4 {
        return Err("压缩包里的文件数量异常".into());
    }

    // 第一遍只看文件名：确认主题根目录在包里的位置（根目录 或 唯一的一层文件夹）。
    // 同时记下每个文件的条目下标 —— 后面按下标读 theme.json，不按名字查（名字规整过，可能对不上）。
    let mut entries: Vec<(usize, String)> = Vec::new();
    for i in 0..archive.len() {
        let entry = archive
            .by_index(i)
            .map_err(|e| format!("读取压缩包失败: {e}"))?;
        if entry.is_dir() {
            continue;
        }
        let raw = normalize_zip_name(entry.name());
        // macOS 打包会带 __MACOSX/ 和 .DS_Store，直接忽略
        if raw.starts_with("__MACOSX/") || raw.ends_with(".DS_Store") {
            continue;
        }
        entries.push((i, raw));
    }
    let names: Vec<&str> = entries.iter().map(|(_, n)| n.as_str()).collect();

    let mut roots: Vec<String> = Vec::new();
    for n in &names {
        if *n == "theme.json" {
            roots.push(String::new());
        } else if let Some(stem) = n.strip_suffix("/theme.json") {
            if !stem.contains('/') {
                roots.push(format!("{stem}/"));
            }
        }
    }
    roots.sort();
    roots.dedup();
    let prefix = match roots.len() {
        0 => return Err("压缩包里找不到 theme.json（应放在根目录，或在唯一的一层文件夹里）".into()),
        1 => roots.remove(0),
        _ => return Err("压缩包里有多份 theme.json，无法判断要导入哪一个".into()),
    };

    // theme.json 里的 id 决定目录名（目录名必须等于 id，见 theme-authoring.md）
    let meta_name = format!("{prefix}theme.json");
    let meta_idx = entries
        .iter()
        .find(|(_, n)| *n == meta_name)
        .map(|(i, _)| *i)
        .ok_or_else(|| "读取 theme.json 失败".to_string())?;
    let meta_text = {
        let entry = archive
            .by_index(meta_idx)
            .map_err(|e| format!("读取 theme.json 失败: {e}"))?;
        let mut text = String::new();
        // 限制读取量：zip 头里的大小字段不可信，别让一个坏包把内存吃光
        entry
            .take(1024 * 1024)
            .read_to_string(&mut text)
            .map_err(|e| format!("theme.json 不是 UTF-8: {e}"))?;
        text
    };
    let meta: serde_json::Value =
        serde_json::from_str(&meta_text).map_err(|e| format!("theme.json 不是合法 JSON: {e}"))?;
    let id = meta.get("id").and_then(|v| v.as_str()).unwrap_or_default();
    if !valid_theme_id(id) {
        return Err(format!(
            "theme.json 的 id 不合法：{id:?}（只允许字母、数字、- 和 _）"
        ));
    }
    let id = id.to_string();

    // 导入是按「当前打开的是哪个选择器」发起的：modes 不匹配就直接拒绝。
    // 否则悬浮窗主题从主面板导入后能装上、却不会出现在主面板列表里，用户会以为导入失败。
    if let Some(target) = mode.as_deref() {
        if target == "overlay" || target == "monitor" {
            let supports = meta
                .get("modes")
                .and_then(|v| v.as_array())
                .map(|arr| arr.iter().any(|m| m.as_str() == Some(target)))
                .unwrap_or(false);
            if !supports {
                return Err(if target == "monitor" {
                    format!("「{id}」是悬浮窗主题（modes 不含 monitor），请到悬浮窗的主题选择器里导入")
                } else {
                    format!("「{id}」是主面板主题（modes 不含 overlay），请在主面板主题选择器里导入")
                });
            }
        }
    }

    let index_name = format!("{prefix}index.html");
    if !names.iter().any(|n| *n == index_name) {
        // 把包内文件名一起报出来：多数情况是 index.html 和 theme.json 不在同一层，
        // 或压缩工具多套了一层目录，直接列出来比重开压缩包排查快。
        let found: Vec<&str> = names.iter().take(20).copied().collect();
        return Err(format!(
            "压缩包里缺少 {index_name}（包内文件：{}）",
            found.join("、")
        ));
    }

    let root = user_themes_root().ok_or("找不到 LOCALAPPDATA")?;
    std::fs::create_dir_all(&root).map_err(|e| format!("创建主题目录失败: {e}"))?;
    let target = root.join(&id);
    if target.exists() {
        return Err(format!(
            "已存在同名主题「{id}」，请先在设置页删除，或修改主题包里的 id"
        ));
    }

    // 临时目录名带 "."，不会被主题列表扫到（valid_theme_id 不允许点）
    let tmp = root.join(format!(".import-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(&tmp).map_err(|e| format!("创建临时目录失败: {e}"))?;

    let result = (|| -> Result<(), String> {
        let mut total = 0u64;
        let mut written = 0usize;
        for (idx, raw) in &entries {
            let entry = archive
                .by_index(*idx)
                .map_err(|e| format!("读取压缩包失败: {e}"))?;
            let Some(rel) = raw.strip_prefix(prefix.as_str()) else {
                continue;
            };
            if rel.is_empty() {
                continue;
            }
            let Some(segments) = zip_entry_segments(rel) else {
                return Err(format!("压缩包里有不安全的路径：{raw}"));
            };
            if segments.is_empty() {
                continue; // 形如 "./" 的条目，没有实际文件
            }
            // 只解出主题真能引用的文件类型（见 THEME_ASSET_TYPES / serve_user_theme）
            let ext = std::path::Path::new(rel)
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or_default()
                .to_ascii_lowercase();
            if !THEME_ASSET_TYPES.iter().any(|(e, _)| *e == ext) {
                continue;
            }
            // 压缩头里的大小字段不可信：先快速拒掉明显超限的，真正写入后再按实际字节数
            // 累计总量（否则 500 个「声明 1 字节、实际 20MB」的条目能写穿磁盘）
            if entry.size() > THEME_ZIP_MAX_FILE_BYTES {
                return Err(format!("{rel} 太大（单文件上限 20MB）"));
            }
            written += 1;
            if written > THEME_ZIP_MAX_ENTRIES {
                return Err("主题包里的文件数量过多".into());
            }

            // 只用校验过的路径段拼目标路径：直接 tmp.join(rel) 在 Windows 上遇到
            // 带盘符的条目会把基准目录整个替换掉。
            let mut dest = tmp.clone();
            for seg in segments {
                dest.push(seg);
            }
            if let Some(parent) = dest.parent() {
                std::fs::create_dir_all(parent).map_err(|e| format!("解压失败: {e}"))?;
            }
            let mut out = std::fs::File::create(&dest).map_err(|e| format!("解压失败: {e}"))?;
            // take 限制实际写出的字节数，防止压缩头里谎报大小
            let copied = std::io::copy(&mut entry.take(THEME_ZIP_MAX_FILE_BYTES + 1), &mut out)
                .map_err(|e| format!("解压失败: {e}"))?;
            if copied > THEME_ZIP_MAX_FILE_BYTES {
                return Err(format!("{rel} 太大（单文件上限 20MB）"));
            }
            total += copied;
            if total > THEME_ZIP_MAX_BYTES {
                return Err("主题包解压后超过 50MB".into());
            }
        }

        for required in ["theme.json", "index.html"] {
            if !tmp.join(required).is_file() {
                return Err(format!("压缩包里缺少 {required}"));
            }
        }
        std::fs::rename(&tmp, &target).map_err(|e| format!("写入主题目录失败: {e}"))?;
        Ok(())
    })();

    if let Err(e) = result {
        let _ = std::fs::remove_dir_all(&tmp);
        return Err(e);
    }
    Ok(id)
}

/// 打开「主题预览」独立窗口（label: theme-preview）。
/// 该窗口的 capability 只列 core 事件权限、不含任何 app 命令，
/// 第三方主题在预览里也调不到 stop_sidecar / delete_user_theme 等 —— 这是分发前的硬性安全前置。
/// 已存在窗口就切主题并置顶；不存在才新建。
///
/// **必须是 async**：Tauri 的同步命令在主线程执行，而 Windows 上 WebView2 的窗口
/// 创建会和在主线程里的同步命令死锁（wry#583，Tauri 文档 `WebviewWindowBuilder::new`
/// 的 Known issues 明确写了这一点）。死锁的症状极具迷惑性：预览窗口「能打开」但永远
/// about:blank，随后所有 IPC invoke 都不再返回 —— 主窗口关闭按钮、悬浮窗按钮全部失效，
/// 连原生标题栏的 × 都关不掉窗口。async 命令跑在异步运行时线程上，创建窗口才安全。
#[tauri::command]
async fn open_theme_preview(app: tauri::AppHandle, id: String, source: String, mode: String) {
    let url = format!("preview.html?id={id}&source={source}&mode={mode}");
    if let Some(win) = app.get_webview_window("theme-preview") {
        let _ = win.show();
        let _ = win.set_focus();
        // 复用已开的窗口换主题：让前端重新加载
        let _ = win.emit(
            "preview-load",
            serde_json::json!({ "id": id, "source": source, "mode": mode }),
        );
        return;
    }
    #[cfg(desktop)]
    {
        let _ = tauri::WebviewWindowBuilder::new(
            &app,
            "theme-preview",
            tauri::WebviewUrl::App(url.into()),
        )
        .title("ACC 主题预览")
        .inner_size(480.0, 320.0)
        .min_inner_size(240.0, 160.0)
        .decorations(true)
        .build();
    }
}

/// 监听用户主题目录：保存 theme.css / theme.json 后前端秒级重建 iframe（热重载）。
/// 一次保存常连发多条事件（创建临时文件 + 改名 + 属性变化），攒到静默 500ms 再抛一次，
/// 否则编辑器里按一下 Ctrl+S 会让主题重建三四轮。
fn spawn_theme_watcher(app: tauri::AppHandle) {
    use notify::{RecursiveMode, Watcher};
    use std::sync::mpsc::RecvTimeoutError;

    let Some(root) = user_themes_root() else {
        return;
    };
    std::thread::spawn(move || {
        let (tx, rx) = std::sync::mpsc::channel();
        let mut watcher = match notify::recommended_watcher(tx) {
            Ok(w) => w,
            Err(e) => {
                eprintln!("[theme] 文件监听不可用（热重载关闭）: {e}");
                return;
            }
        };
        // 用户可能刚把目录清空过，watch 之前先确保它在
        let _ = std::fs::create_dir_all(&root);
        if let Err(e) = watcher.watch(&root, RecursiveMode::Recursive) {
            eprintln!("[theme] 监听 {} 失败（热重载关闭）: {e}", root.display());
            return;
        }
        println!("[theme] 开始监听用户主题目录: {}", root.display());

        loop {
            let first = match rx.recv() {
                Ok(ev) => ev,
                Err(_) => return, // watcher 已回收
            };
            let mut events = vec![first];
            loop {
                match rx.recv_timeout(std::time::Duration::from_millis(500)) {
                    Ok(ev) => events.push(ev),
                    Err(RecvTimeoutError::Timeout) => break,
                    Err(RecvTimeoutError::Disconnected) => return,
                }
            }

            // 只把「受影响的主题 id」交给前端，前端据此判断要不要重建自己那个 iframe
            let mut ids: Vec<String> = Vec::new();
            for ev in events.into_iter().flatten() {
                for path in ev.paths {
                    let Ok(rel) = path.strip_prefix(&root) else {
                        continue;
                    };
                    let Some(id) = rel
                        .components()
                        .next()
                        .and_then(|c| c.as_os_str().to_str())
                    else {
                        continue;
                    };
                    if valid_theme_id(id) && !ids.iter().any(|x| x == id) {
                        ids.push(id.to_string());
                    }
                }
            }
            if ids.is_empty() {
                continue; // 只动了根目录里的零散文件，跟任何主题都无关
            }
            let _ = app.emit("themes-changed", ids);
        }
    });
}

fn main() {
    tauri::Builder::default()
        .register_uri_scheme_protocol("theme", |_ctx, request| serve_user_theme(&request))
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        // 导入主题包要用系统文件选择框（只在主窗口暴露）
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
    .invoke_handler(tauri::generate_handler![
        stop_sidecar,
        show_overlay,
        hide_overlay,
        toggle_overlay,
        set_overlay_locked,
        set_overlay_size,
        set_overlay_slots,
        overlay_any_focused,
        save_overlay_rect,
        open_theme_preview,
        list_user_themes,
        open_themes_dir,
        delete_user_theme,
        import_theme_zip,
        export_theme_template,
    ])
        .setup(|app| {
            // 系统托盘（关闭窗口后用于恢复 / 退出）
            setup_tray(app)?;

            // 用户主题目录提前建好，否则用户根本找不到该往哪儿放主题
            if let Some(dir) = user_themes_root() {
                if let Err(e) = std::fs::create_dir_all(&dir) {
                    eprintln!("[theme] 无法创建用户主题目录 {}: {e}", dir.display());
                }
            }

            // 用户主题热重载：改 CSS / theme.json 不用重启应用，也不用点「重试」
            spawn_theme_watcher(app.handle().clone());

            // 注册悬浮窗快捷键（失败不致命：面板按钮仍然可用）。
            // 失败原因（多半是被别的软件占用）必须让用户看得到，否则按了没反应完全无从下手。
            let gs = app.global_shortcut();
            if let Err(e) = gs.on_shortcut(OVERLAY_SHORTCUT, |app, _shortcut, event| {
                if event.state == ShortcutState::Pressed {
                    // 显示时可能要按需创建窗口，而这里跑在主线程上 ——
                    // Windows 上主线程建 WebView2 会死锁，必须丢到后台线程。
                    let app = app.clone();
                    std::thread::spawn(move || {
                        let next = !any_overlay_visible(&app);
                        overlay_set_visible(&app, next);
                    });
                }
            }) {
                eprintln!("[overlay] 显隐快捷键注册失败: {e}");
                let _ = app.emit(
                    "overlay-shortcut-error",
                    format!("{OVERLAY_SHORTCUT} 注册失败（可能被其他程序占用）：{e}"),
                );
            }
            if let Err(e) = gs.on_shortcut(OVERLAY_EDIT_SHORTCUT, |app, _shortcut, event| {
                if event.state == ShortcutState::Pressed {
                    // 同理：解锁前可能要先显示（= 建窗口），不能在主线程做
                    let app = app.clone();
                    std::thread::spawn(move || overlay_toggle_locked(&app));
                }
            }) {
                eprintln!("[overlay] 锁定快捷键注册失败: {e}");
                let _ = app.emit(
                    "overlay-shortcut-error",
                    format!("{OVERLAY_EDIT_SHORTCUT} 注册失败（可能被其他程序占用）：{e}"),
                );
            }

            // 启动 Python 打包好的 exe。
            // set_process_group(true) 让 Windows 把 sidecar 放进 Job Object，
            // PyInstaller onefile 会再派生一个 Python 子进程，只有终止整个 Job 才能全部清理掉。
            if std::env::var("MANUAL_PYTHON").is_err() {
                let sidecar_command = app
                    .shell()
                    .sidecar("telemetry_core")?
                    .set_process_group(true);
                let (mut rx, child) = sidecar_command.spawn()?;
                // 把 sidecar 的 stdout / stderr 转回主进程控制台。
                // 以前这里直接丢掉 Receiver，后端的「存储队列已满」「记录帧失败」
                // 「赛道几何解析失败」这些告警全被吞了，出问题只能靠猜。
                tauri::async_runtime::spawn(async move {
                    use tauri_plugin_shell::process::CommandEvent;
                    while let Some(event) = rx.recv().await {
                        match event {
                            CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                                let text = String::from_utf8_lossy(&line);
                                let text = text.trim_end();
                                if !text.is_empty() {
                                    println!("[sidecar] {text}");
                                }
                            }
                            CommandEvent::Terminated(payload) => {
                                println!(
                                    "[sidecar] 已退出: code={:?} signal={:?}",
                                    payload.code, payload.signal
                                );
                                break;
                            }
                            _ => {}
                        }
                    }
                });
                // Rust 侧 spawn 的 sidecar 不会进入插件的子进程表（那里只记录前端发起的进程），
                // 所以必须自己保存，并在应用退出时 kill；否则进程会残留并占着 8008 端口。
                app.manage(Sidecar(Mutex::new(Some(child))));
            } else {
                println!("[Rust] 检测到 MANUAL_PYTHON，跳过自动启动 sidecar。");
                app.manage(Sidecar(Mutex::new(None)));
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if matches!(event, RunEvent::Exit) {
                if let Some(state) = app_handle.try_state::<Sidecar>() {
                    if let Some(child) = state.0.lock().unwrap().take() {
                        // Windows 上 kill() 会终止整个 Job Object（PyInstaller 引导进程 + Python 子进程）
                        let _ = child.kill();
                    }
                }
            }
        });
}
