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

/// 悬浮窗第一次显示时贴到主显示器右上角；之后保留系统给的位置。
static OVERLAY_PLACED: AtomicBool = AtomicBool::new(false);

/// 是否处于「锁定」态：true = 鼠标穿透到游戏（默认）。
/// 穿透状态下窗口收不到任何鼠标事件，所以拖动 / 缩放必须先进「编辑模式」。
static OVERLAY_LOCKED: AtomicBool = AtomicBool::new(true);

/// 全局快捷键：切换悬浮窗显示 / 隐藏（与面板按钮共用同一状态）。
const OVERLAY_SHORTCUT: &str = "Ctrl+Shift+O";

/// 全局快捷键：锁定 / 解锁悬浮窗（解锁后才能拖动和拉边缩放）。
const OVERLAY_EDIT_SHORTCUT: &str = "Ctrl+Shift+L";

fn overlay_visible<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> bool {
    app.get_webview_window("overlay")
        .and_then(|w| w.is_visible().ok())
        .unwrap_or(false)
}

fn place_overlay<R: tauri::Runtime>(win: &tauri::WebviewWindow<R>) {
    let Ok(Some(monitor)) = win.current_monitor() else {
        return;
    };
    let screen = monitor.size();
    let size = win
        .outer_size()
        .unwrap_or(tauri::PhysicalSize::new(380, 150));
    let margin = 20i32;
    let x = screen.width as i32 - size.width as i32 - margin;
    let y = margin;
    let _ = win.set_position(tauri::PhysicalPosition::new(x, y));
}

/// 锁定 / 解锁。解锁时关掉穿透并抢焦点，让窗口能接收拖动与拉边缩放。
fn overlay_set_locked<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    win: &tauri::WebviewWindow<R>,
    locked: bool,
) {
    let _ = win.set_ignore_cursor_events(locked);
    if !locked {
        // 穿透时窗口从不获得焦点，解锁后要主动要一次，否则原生拖动 / 拉边不生效
        let _ = win.set_focus();
    }
    OVERLAY_LOCKED.store(locked, Ordering::Relaxed);
    // 每次都广播：状态万一和前端不同步（比如窗口被外部关掉又显示），
    // 幂等重发一次总比「按了键但工具条不出现」好排查。
    let _ = app.emit("overlay-lock", locked);
}

fn overlay_toggle_locked<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> bool {
    let next = !OVERLAY_LOCKED.load(Ordering::Relaxed);
    if !next && !overlay_visible(app) {
        // 按了编辑快捷键说明就是想调整：窗口没显示就先显示出来，
        // 否则用户完全看不到编辑条，会以为快捷键没生效。
        overlay_set_visible(app, true);
    }
    if let Some(win) = app.get_webview_window("overlay") {
        overlay_set_locked(app, &win, next);
    }
    next
}

/// 显示 / 隐藏悬浮窗，并把可见性广播给前端（按钮据此同步 on/off）。
fn overlay_set_visible<R: tauri::Runtime>(app: &tauri::AppHandle<R>, show: bool) {
    let Some(win) = app.get_webview_window("overlay") else {
        return;
    };
    if show {
        let _ = win.set_always_on_top(true);
        // 每次显示都回到穿透态：平时永远不拦截游戏里的操作
        overlay_set_locked(app, &win, true);
        if !OVERLAY_PLACED.swap(true, Ordering::Relaxed) {
            place_overlay(&win);
        }
        let _ = win.show();
    } else {
        overlay_set_locked(app, &win, true);
        let _ = win.hide();
    }
    let _ = app.emit("overlay-visibility", show);
}

#[tauri::command]
fn show_overlay(app: tauri::AppHandle) {
    overlay_set_visible(&app, true);
}

#[tauri::command]
fn hide_overlay(app: tauri::AppHandle) {
    overlay_set_visible(&app, false);
}

#[tauri::command]
fn toggle_overlay(app: tauri::AppHandle) -> bool {
    let next = !overlay_visible(&app);
    overlay_set_visible(&app, next);
    next
}

/// 前端主动切换锁定态（编辑模式里的「锁定」按钮）。
#[tauri::command]
fn set_overlay_locked(app: tauri::AppHandle, locked: bool) {
    if let Some(win) = app.get_webview_window("overlay") {
        overlay_set_locked(&app, &win, locked);
    }
}

/// 主题声明了自己的尺寸后，由前端把窗口调整到对应大小。
/// 用逻辑像素：设计尺寸是 DPI 无关的，物理像素在 150% 缩放下会被放大 1.5 倍。
#[tauri::command]
fn set_overlay_size(app: tauri::AppHandle, width: f64, height: f64) {
    let Some(win) = app.get_webview_window("overlay") else {
        return;
    };
    let _ = win.set_size(tauri::LogicalSize::new(
        width.max(200.0),
        height.max(80.0),
    ));
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
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
    .invoke_handler(tauri::generate_handler![
        stop_sidecar,
        show_overlay,
        hide_overlay,
        toggle_overlay,
        set_overlay_locked,
        set_overlay_size,
        list_user_themes,
        open_themes_dir,
        delete_user_theme,
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
                    let next = !overlay_visible(app);
                    overlay_set_visible(app, next);
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
                    overlay_toggle_locked(app);
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
                let (_rx, child) = sidecar_command.spawn()?;
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
