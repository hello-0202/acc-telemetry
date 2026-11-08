// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock, RwLock};

use acc_telemetry_lib::setup_tray;
use tauri::{Emitter, Manager, RunEvent};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandChild;

struct Sidecar(Mutex<Option<CommandChild>>);

// ───────────────────────── 存储路径（单一真源） ─────────────────────────
//
// 三类持久化位置统一由这里决定，Rust / Python sidecar / 前端都从这里取值：
//   configDir  → config.json、window-state.json
//   dataDir    → telemetry.db（+ WAL / SHM）
//   themesDir  → 用户主题根目录
//
// 全新安装的默认布局：配置文件放 <安装目录>\，数据库放 <安装目录>\data\，
// 主题放 <安装目录>\data\themes\。安装目录不可写（比如装进 Program Files）时
// 自动回退到 %LOCALAPPDATA%\acc-telemetry 这套旧布局，保证软件能起来。
//
// 「路径配置」本身不能放进可改的目录（改完就找不到自己了），所以固定写在一个引导文件：
// %LOCALAPPDATA%\acc-telemetry\paths.json（开发模式 paths-dev.json，互不干扰）。
// 改路径只写 pending；重启时（sidecar 还没启动、数据库没被占用）才复制数据并生效。

#[derive(serde::Serialize, serde::Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct PathConfig {
    config_dir: PathBuf,
    data_dir: PathBuf,
    themes_dir: PathBuf,
}

#[derive(serde::Serialize, serde::Deserialize, Clone, Debug, Default)]
#[serde(rename_all = "camelCase")]
struct PathsFile {
    /// 当前进程实际生效的路径
    effective: Option<PathConfig>,
    /// 设置页改过、等重启才生效的路径。
    /// 有了 pending 就一定会「迁移」：旧位置的文件搬走后不再保留，
    /// 失败则整体回滚（见 migrate_paths_data），不存在「只切不搬」的中间态。
    pending: Option<PathConfig>,
    /// 给设置页看的提示（回退 / 旧数据继承 / 复制结果）
    note: Option<String>,
}

struct PathsState {
    effective: PathConfig,
    default_layout: PathConfig,
    file: PathsFile,
}

/// 全局静态而不是 app.manage：`theme://` 协议回调是同步闭包、拿不到 AppHandle 的 state，
/// 用 OnceLock + RwLock 才能在协议里同步读到当前生效的路径。
static PATHS: OnceLock<RwLock<PathsState>> = OnceLock::new();

fn legacy_base() -> PathBuf {
    std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir)
        .join("acc-telemetry")
}

fn install_dir() -> Option<PathBuf> {
    std::env::current_exe().ok()?.parent().map(|p| p.to_path_buf())
}

fn fallback_layout() -> PathConfig {
    let base = legacy_base();
    PathConfig {
        config_dir: base.clone(),
        data_dir: base.clone(),
        themes_dir: base.join("themes"),
    }
}

fn install_layout() -> PathConfig {
    let root = install_dir().unwrap_or_else(legacy_base);
    let data = root.join("data");
    PathConfig {
        config_dir: root,
        data_dir: data.clone(),
        themes_dir: data.join("themes"),
    }
}

/// 目录能不能写：真建一次目录 + 写一个探针文件（Program Files 上会失败）。
fn writable_dir(dir: &Path) -> bool {
    if std::fs::create_dir_all(dir).is_err() {
        return false;
    }
    let probe = dir.join(format!(".acc-write-probe-{}", std::process::id()));
    match std::fs::write(&probe, b"ok") {
        Ok(()) => {
            let _ = std::fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

fn layout_writable(p: &PathConfig) -> bool {
    writable_dir(&p.config_dir) && writable_dir(&p.data_dir) && writable_dir(&p.themes_dir)
}

/// 「恢复默认」的目标：安装目录布局；不可写才退回旧布局。不做旧数据探测，
/// 否则老用户点「恢复默认」会原地不动、搬不到安装目录。
fn reset_layout() -> PathConfig {
    // 开发模式默认就是 LOCALAPPDATA（见 default_layout），「恢复默认」必须指向同一处，
    // 否则会把数据搬进 target/debug —— 下次 cargo build 就可能被清掉。
    if cfg!(debug_assertions) {
        return fallback_layout();
    }
    let p = install_layout();
    if layout_writable(&p) {
        p
    } else {
        fallback_layout()
    }
}

fn legacy_has_data() -> bool {
    let base = legacy_base();
    ["telemetry.db", "config.json", "window-state.json"]
        .iter()
        .any(|n| base.join(n).is_file())
        || base.join("themes").is_dir()
}

/// 首次启动（引导文件里还没有 effective）该用哪套布局。
fn default_layout() -> (PathConfig, Option<String>) {
    if cfg!(debug_assertions) {
        return (
            fallback_layout(),
            Some("开发模式：数据仍放在 %LOCALAPPDATA%\\acc-telemetry，避免写进构建目录。".into()),
        );
    }
    let install = install_layout();
    if !layout_writable(&install) {
        return (
            fallback_layout(),
            Some(
                "安装目录不可写（常见于装到 C:\\Program Files），已自动回退到用户数据目录；可在下面改到别处。"
                    .into(),
            ),
        );
    }
    // 老用户升级：旧目录里已经有圈速 / 配置 / 主题，默认沿用，绝不静默换位置。
    if legacy_has_data() {
        return (
            fallback_layout(),
            Some("检测到旧版数据，仍沿用原来的位置；点「恢复默认」可把默认位置改成安装目录。".into()),
        );
    }
    (install, None)
}

fn bootstrap_file() -> PathBuf {
    let dir = std::env::var_os("ACC_TELEMETRY_BOOTSTRAP_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(legacy_base);
    let name = if cfg!(debug_assertions) {
        "paths-dev.json"
    } else {
        "paths.json"
    };
    dir.join(name)
}

fn load_paths_file() -> PathsFile {
    std::fs::read_to_string(bootstrap_file())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_paths_file(file: &PathsFile) -> Result<(), String> {
    let path = bootstrap_file();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("创建引导目录失败: {e}"))?;
    }
    let text = serde_json::to_string_pretty(file).map_err(|e| e.to_string())?;
    std::fs::write(&path, text).map_err(|e| format!("写入路径配置失败: {e}"))
}

// ───────────────────── 存储位置迁移：先全拷完、再删旧、失败整体回滚 ─────────────────────

/// 迁移结果：真正搬走（旧位置已删）的文件数 / 目标已有同名文件而放弃搬的文件数 /
/// 复制成功但旧文件删不掉的文件数。
struct MigrateReport {
    moved: u32,
    kept: u32,
    leftover: u32,
}

/// 建目录，并把「这次新建的每一层」记下来，供回滚时反向删除（深层优先）。
fn ensure_dir_tracked(dir: &Path, created_dirs: &mut Vec<PathBuf>) -> Result<(), String> {
    if dir.is_dir() {
        return Ok(());
    }
    let mut missing: Vec<PathBuf> = Vec::new();
    let mut cur = Some(dir);
    while let Some(p) = cur {
        if p.is_dir() {
            break;
        }
        missing.push(p.to_path_buf());
        cur = p.parent();
    }
    std::fs::create_dir_all(dir).map_err(|e| format!("创建 {} 失败: {e}", dir.display()))?;
    // missing 是「深 → 浅」，正好符合回滚时的删除顺序
    created_dirs.extend(missing);
    Ok(())
}

/// 展开主题目录里的所有文件。跳过导入临时目录和回收站 —— 它们不是用户数据。
fn collect_tree_files(
    src: &Path,
    dst: &Path,
    depth: u8,
    out: &mut Vec<(PathBuf, PathBuf)>,
) -> Result<(), String> {
    if !src.is_dir() || depth > 16 {
        return Ok(());
    }
    let entries =
        std::fs::read_dir(src).map_err(|e| format!("读取 {} 失败: {e}", src.display()))?;
    for entry in entries.flatten() {
        let name = entry.file_name();
        let label = name.to_string_lossy();
        if label == ".trash" || label.starts_with(".import-") {
            continue;
        }
        let from = entry.path();
        let to = dst.join(&name);
        if from.is_dir() {
            collect_tree_files(&from, &to, depth + 1, out)?;
        } else if from.is_file() {
            out.push((from, to));
        }
    }
    Ok(())
}

fn collect_migration_pairs(
    from: &PathConfig,
    to: &PathConfig,
    out: &mut Vec<(PathBuf, PathBuf)>,
) -> Result<(), String> {
    for name in ["config.json", "window-state.json"] {
        let src = from.config_dir.join(name);
        if src.is_file() {
            out.push((src, to.config_dir.join(name)));
        }
    }
    for name in ["telemetry.db", "telemetry.db-wal", "telemetry.db-shm"] {
        let src = from.data_dir.join(name);
        if src.is_file() {
            out.push((src, to.data_dir.join(name)));
        }
    }
    collect_tree_files(&from.themes_dir, &to.themes_dir, 0, out)
}

/// 递归清理空目录（只删空的，非空 remove_dir 会失败，天然安全）。
fn prune_empty_dirs(dir: &Path, depth: u8) {
    if !dir.is_dir() || depth > 16 {
        return;
    }
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let p = entry.path();
        if p.is_dir() {
            prune_empty_dirs(&p, depth + 1);
        }
    }
    let _ = std::fs::remove_dir(dir);
}

/// 旧位置能不能顺手删目录：配置目录可能就是安装目录本身，删了等于把程序卸了。
fn removable_old_dir(dir: &Path, from: &PathConfig) -> bool {
    dir.is_dir()
        && dir != from.config_dir
        && install_dir().map(|i| i != dir).unwrap_or(true)
}

fn rollback_migration(created_files: &[PathBuf], created_dirs: &[PathBuf]) {
    // 只删「本次新建」的文件，绝不碰目标目录里原有内容
    for f in created_files.iter().rev() {
        let _ = std::fs::remove_file(f);
    }
    // 目录按层级由深到浅删，父目录才可能空出来（非递归 remove_dir，非空自然失败）
    let mut dirs: Vec<&PathBuf> = created_dirs.iter().collect();
    dirs.sort_by_key(|d| std::cmp::Reverse(d.components().count()));
    dirs.dedup();
    for d in dirs {
        let _ = std::fs::remove_dir(d);
    }
}

/// 真正的迁移流程，按「全部复制并校验 → 才删旧文件」的顺序走，
/// 因此任何一步失败都能靠 rollback_migration 回到动手前的状态，旧位置数据一个不少。
fn do_migrate(
    from: &PathConfig,
    to: &PathConfig,
    created_files: &mut Vec<PathBuf>,
    created_dirs: &mut Vec<PathBuf>,
) -> Result<MigrateReport, String> {
    for dir in [&to.config_dir, &to.data_dir, &to.themes_dir] {
        ensure_dir_tracked(dir, created_dirs)?;
    }

    let mut pairs: Vec<(PathBuf, PathBuf)> = Vec::new();
    collect_migration_pairs(from, to, &mut pairs)?;

    // 第一阶段：只复制，不动旧文件
    let mut kept = 0u32;
    let mut planned: Vec<(PathBuf, PathBuf)> = Vec::new();
    for (src, dst) in pairs {
        if src == dst {
            // 这一项没动（用户只改了另外一个目录），不复制也不统计，更绝不能删
            continue;
        }
        if dst.exists() {
            // 目标已有同名文件：不覆盖；旧文件也不删（数据一定还在，事后在提示里说明）
            kept += 1;
            continue;
        }
        if let Some(parent) = dst.parent() {
            ensure_dir_tracked(parent, created_dirs)?;
        }
        std::fs::copy(&src, &dst).map_err(|e| format!("复制 {} 失败: {e}", src.display()))?;
        created_files.push(dst.clone());

        // 校验大小，避免留下半个数据库却把旧文件删了
        let src_len = std::fs::metadata(&src)
            .map_err(|e| format!("读取 {} 信息失败: {e}", src.display()))?
            .len();
        let dst_len = std::fs::metadata(&dst)
            .map_err(|e| format!("读取 {} 信息失败: {e}", dst.display()))?
            .len();
        if src_len != dst_len {
            return Err(format!(
                "{} 复制后大小不一致（{src_len} → {dst_len} 字节）",
                src.display()
            ));
        }
        planned.push((src, dst));
    }

    // 第二阶段：全部复制成功，才删旧文件
    let mut moved = 0u32;
    let mut leftover = 0u32;
    for (src, _) in &planned {
        match std::fs::remove_file(src) {
            Ok(()) => moved += 1,
            Err(_) => leftover += 1,
        }
    }
    // 旧位置留下的空目录顺手清掉（含主题下的空子目录）；只删空目录，绝不递归删数据，
    // 配置目录 / 安装目录也一律不碰。
    if removable_old_dir(&from.themes_dir, from) {
        prune_empty_dirs(&from.themes_dir, 0);
    }
    if removable_old_dir(&from.data_dir, from) {
        let _ = std::fs::remove_dir(&from.data_dir); // 非递归：还有别的东西就自然失败
    }
    Ok(MigrateReport {
        moved,
        kept,
        leftover,
    })
}

/// 在 sidecar 启动之前调用：数据库此刻没被占用，WAL / SHM 一起搬走才不丢最近圈次。
fn migrate_paths_data(from: &PathConfig, to: &PathConfig) -> Result<MigrateReport, String> {
    let mut created_files: Vec<PathBuf> = Vec::new();
    let mut created_dirs: Vec<PathBuf> = Vec::new();
    match do_migrate(from, to, &mut created_files, &mut created_dirs) {
        Ok(report) => Ok(report),
        Err(e) => {
            rollback_migration(&created_files, &created_dirs);
            Err(e)
        }
    }
}

fn migrate_note(r: &MigrateReport) -> String {
    let mut s = if r.moved > 0 {
        format!("已迁移存储位置：搬走 {} 个文件。", r.moved)
    } else {
        "已切换存储位置。".to_string()
    };
    if r.kept > 0 {
        s.push_str(&format!(
            "新位置已有 {} 个同名文件，保留了新位置的版本，对应的旧文件未删除。",
            r.kept
        ));
    } else if r.leftover == 0 {
        s.push_str("旧位置的文件已清理。");
    }
    if r.leftover > 0 {
        s.push_str(&format!(
            "有 {} 个旧文件没能删除（可能被占用），不影响使用，可手动清理。",
            r.leftover
        ));
    }
    s
}

fn init_paths() {
    let mut file = load_paths_file();
    let is_first = file.effective.is_none();
    let (first_default, first_note) = default_layout();
    let mut effective = file.effective.take().unwrap_or(first_default);
    if is_first {
        file.note = first_note;
    } else if !layout_writable(&effective) {
        // 比如上次把数据放到了移动盘、这次没插：不能让软件起不来。
        effective = fallback_layout();
        file.note = Some("原存储位置不可写（盘符可能已拔出），本次已临时回退到用户数据目录。".into());
    }

    if let Some(pending) = file.pending.take() {
        if pending != effective {
            if !layout_writable(&pending) {
                file.note = Some("新存储位置不可写，已保留原位置；请在设置里换一个目录。".into());
            } else {
                match migrate_paths_data(&effective, &pending) {
                    Ok(report) => {
                        effective = pending;
                        file.note = Some(migrate_note(&report));
                    }
                    Err(e) => {
                        // 回滚完成：旧位置原封不动，新位置里这次拷进去的文件已清掉
                        file.note = Some(format!(
                            "迁移失败，已回滚到原位置（旧数据完好，新位置已清理）：{e}"
                        ));
                    }
                }
            }
        }
    }

    file.effective = Some(effective.clone());
    file.pending = None;
    if let Err(e) = save_paths_file(&file) {
        eprintln!("[paths] {e}");
    }
    let default_layout = reset_layout();
    let _ = PATHS.set(RwLock::new(PathsState {
        effective,
        default_layout,
        file,
    }));
}

fn paths() -> PathConfig {
    PATHS
        .get()
        .map(|s| s.read().unwrap().effective.clone())
        .unwrap_or_else(fallback_layout)
}

fn config_root() -> PathBuf {
    paths().config_dir
}

fn themes_root() -> PathBuf {
    paths().themes_dir
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct PathsInfo {
    effective: PathConfig,
    pending: Option<PathConfig>,
    default_layout: PathConfig,
    config_file: String,
    db_file: String,
    window_state_file: String,
    install_dir: String,
    bootstrap_file: String,
    note: Option<String>,
}

fn build_paths_info(
    effective: &PathConfig,
    pending: Option<PathConfig>,
    default_layout: &PathConfig,
    note: Option<String>,
) -> PathsInfo {
    PathsInfo {
        effective: effective.clone(),
        pending,
        default_layout: default_layout.clone(),
        config_file: effective.config_dir.join("config.json").to_string_lossy().into_owned(),
        db_file: effective.data_dir.join("telemetry.db").to_string_lossy().into_owned(),
        window_state_file: effective
            .config_dir
            .join("window-state.json")
            .to_string_lossy()
            .into_owned(),
        install_dir: install_dir()
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_default(),
        bootstrap_file: bootstrap_file().to_string_lossy().into_owned(),
        note,
    }
}

fn paths_info() -> PathsInfo {
    if let Some(state) = PATHS.get() {
        let st = state.read().unwrap();
        return build_paths_info(
            &st.effective,
            st.file.pending.clone(),
            &st.default_layout,
            st.file.note.clone(),
        );
    }
    let fallback = fallback_layout();
    build_paths_info(&fallback, None, &fallback, None)
}

fn validate_layout(p: &PathConfig) -> Result<(), String> {
    for (label, dir) in [
        ("配置文件目录", &p.config_dir),
        ("数据目录", &p.data_dir),
        ("主题目录", &p.themes_dir),
    ] {
        if !dir.is_absolute() {
            return Err(format!("{label} 必须是绝对路径：{}", dir.display()));
        }
        if dir.is_file() {
            return Err(format!("{label} 指向的是文件而不是文件夹：{}", dir.display()));
        }
        if !writable_dir(dir) {
            return Err(format!("{label} 不可写：{}", dir.display()));
        }
    }
    Ok(())
}

/// 只写 pending，不动当前生效路径：运行中的 sidecar 还持有旧数据库。
/// 重启时 init_paths() 会把旧位置的数据整体迁移过去（失败自动回滚），
/// 前端只提示「重启后生效」。
fn set_pending_layout(layout: PathConfig) -> Result<PathsInfo, String> {
    validate_layout(&layout)?;
    let Some(state) = PATHS.get() else {
        return Err("路径模块还没初始化".into());
    };
    let mut st = state.write().unwrap();
    // 选回当前生效的路径 = 撤销 pending，别留一条「pending 等于 effective」的空记录
    st.file.pending = if layout == st.effective {
        None
    } else {
        Some(layout)
    };
    st.file.note = None;
    save_paths_file(&st.file)?;
    Ok(build_paths_info(
        &st.effective,
        st.file.pending.clone(),
        &st.default_layout,
        None,
    ))
}

#[tauri::command]
fn get_data_paths() -> PathsInfo {
    paths_info()
}

#[tauri::command]
fn set_data_paths(
    window: tauri::WebviewWindow,
    layout: PathConfig,
) -> Result<PathsInfo, String> {
    if window.label() != "main" {
        return Err("not allowed from this window".into());
    }
    set_pending_layout(layout)
}

#[tauri::command]
fn reset_data_paths(window: tauri::WebviewWindow) -> Result<PathsInfo, String> {
    if window.label() != "main" {
        return Err("not allowed from this window".into());
    }
    set_pending_layout(reset_layout())
}

/// 重启应用，让 pending 的存储位置在 sidecar 启动前完成迁移。
/// 必须先杀掉 sidecar：AppHandle::restart() 在主线程分支里直接 exit，
/// 不会走 RunEvent::Exit，我们那条兜底 kill 就轮不到，端口会被孤儿进程占着。
#[tauri::command]
fn restart_app(app: tauri::AppHandle, window: tauri::WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("not allowed from this window".into());
    }
    if let Some(state) = app.try_state::<Sidecar>() {
        if let Some(child) = state.0.lock().unwrap().take() {
            let _ = child.kill();
        }
    }
    // 给 Windows 一点时间真正回收进程句柄：新实例一起来就要迁移数据库，
    // 旧 sidecar 还握着 telemetry.db 会让复制读到半截文件。
    std::thread::sleep(std::time::Duration::from_millis(200));
    app.restart()
}

#[tauri::command]
fn open_data_path(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    kind: String,
) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    if window.label() != "main" {
        return Err("not allowed from this window".into());
    }
    let p = paths();
    let dir = match kind.as_str() {
        // 设置页只有一个「存储位置」入口，root = 三个目录的根（= config_dir）
        "root" | "config" => p.config_dir,
        "data" => p.data_dir,
        "themes" => p.themes_dir,
        "install" => install_dir().ok_or("找不到安装目录")?,
        _ => return Err("未知的位置类型".into()),
    };
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建目录失败: {e}"))?;
    app.opener()
        .open_path(dir.to_string_lossy().to_string(), None::<String>)
        .map_err(|e| format!("打开目录失败: {e}"))
}

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
// 存到当前「配置文件目录」下的 window-state.json（默认 = 安装目录，可在设置里改）。
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

fn load_window_state() -> WindowStateFile {
    match std::fs::read_to_string(config_root().join("window-state.json")) {
        Ok(s) => serde_json::from_str(&s).unwrap_or_default(),
        Err(_) => WindowStateFile::default(),
    }
}

fn persist_window_state(st: &WindowStateFile) {
    let dir = config_root();
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

/// 用户主题根目录来自顶部的 `themes_root()`（`PathConfig::themes_dir`）。
/// 不再单独拼 `%LOCALAPPDATA%`：设置页改完路径后，协议 / 列表 / 导入 / 监听必须读同一份值。

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
    let root = themes_root();
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
    let root = themes_root();
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
    let root = themes_root();
    std::fs::create_dir_all(&root).map_err(|e| format!("创建目录失败: {e}"))?;
    app.opener()
        .open_path(root.to_string_lossy().to_string(), None::<String>)
        .map_err(|e| format!("打开目录失败: {e}"))
}

/// 删除用户主题：优先走系统回收站（可恢复），不做永久删除。
/// 只允许删 `themes/<合法 id>` 这一层，canonicalize 之后再校验一次前缀。
///
/// 为什么要 async + spawn_blocking：`trash` 在 Windows 上走 `IFileOperation`（Shell COM），
/// 在 Tauri 的同步命令线程（阻塞事件循环的 STA）里实测会返回
/// `Unknown { description: "Some operations were aborted" }` —— 回收站明明可用、
/// 文件也没有被占用，换到独立线程调用就正常。删完再由前端刷新列表。
///
/// 回收站仍失败时兜底：同盘 rename 到 `themes/.trash/<id>-<时间戳>`。
/// 同一卷上的重命名是原子的，不会失败在权限/Shell 上；数据也还在，能手动捡回来。
/// `.trash` 因为含 `.` 不满足 `valid_theme_id`，不会被 `list_user_themes` 当成主题列出来。
#[tauri::command]
async fn delete_user_theme(id: String) -> Result<(), String> {
    if !valid_theme_id(&id) {
        return Err("主题 id 不合法".into());
    }
    let root = themes_root();
    let root = root.canonicalize().map_err(|e| format!("主题目录不可用: {e}"))?;
    let dir = root
        .join(&id)
        .canonicalize()
        .map_err(|_| "主题不存在".to_string())?;
    if dir == root || !dir.starts_with(&root) {
        return Err("拒绝删除：路径越界".into());
    }

    let target = dir.clone();
    let trashed = tauri::async_runtime::spawn_blocking(move || trash::delete(&target))
        .await
        .map_err(|e| format!("删除线程异常: {e}"))?;
    if trashed.is_ok() {
        return Ok(());
    }

    eprintln!(
        "[theme] 回收站删除失败，改用应用回收目录: {:?}",
        trashed.as_ref().err()
    );
    let trash_root = root.join(".trash");
    std::fs::create_dir_all(&trash_root).map_err(|e| format!("创建回收目录失败: {e}"))?;
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let dest = trash_root.join(format!("{id}-{stamp}"));
    std::fs::rename(&dir, &dest).map_err(|e| {
        format!(
            "移入回收站失败: {} / 移到应用回收目录也失败: {e}",
            trashed.err().map(|e| e.to_string()).unwrap_or_default()
        )
    })
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

    let root = themes_root();
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

    let root = themes_root();
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
        get_data_paths,
        set_data_paths,
        reset_data_paths,
        restart_app,
        open_data_path,
    ])
        .setup(|app| {
            // 必须在任何路径读取之前：解析默认布局 / 旧数据迁移 / 应用上次改的路径，
            // 而且此时 sidecar 还没启动，数据库没被占用，复制 WAL 才安全。
            init_paths();

            // 系统托盘（关闭窗口后用于恢复 / 退出）
            setup_tray(app)?;

            // 用户主题目录提前建好，否则用户根本找不到该往哪儿放主题
            let dir = themes_root();
            if let Err(e) = std::fs::create_dir_all(&dir) {
                eprintln!("[theme] 无法创建用户主题目录 {}: {e}", dir.display());
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
                let effective = paths();
                let mut sidecar_command = app
                    .shell()
                    .sidecar("telemetry_core")?
                    .set_process_group(true);
                // Python 侧 DB_PATH / CONFIG_PATH 在模块导入时求值，只能用环境变量透传。
                // 开发者自己显式设了同名变量时不覆盖（造测试数据用的隔离库要能生效）。
                if std::env::var_os("ACC_TELEMETRY_DB").is_none() {
                    sidecar_command = sidecar_command.env(
                        "ACC_TELEMETRY_DB",
                        effective
                            .data_dir
                            .join("telemetry.db")
                            .to_string_lossy()
                            .into_owned(),
                    );
                }
                if std::env::var_os("ACC_TELEMETRY_CONFIG").is_none() {
                    sidecar_command = sidecar_command.env(
                        "ACC_TELEMETRY_CONFIG",
                        effective
                            .config_dir
                            .join("config.json")
                            .to_string_lossy()
                            .into_owned(),
                    );
                }
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

#[cfg(test)]
mod path_tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "acc-paths-test-{}-{name}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    fn layout(root: &Path, prefix: &str) -> PathConfig {
        let base = root.join(prefix);
        PathConfig {
            config_dir: base.join("cfg"),
            data_dir: base.join("data"),
            themes_dir: base.join("data").join("themes"),
        }
    }

    /// 迁移 = 搬到新位置 + 删掉旧文件；配置目录本身不能删（可能就是安装目录）。
    #[test]
    fn migrate_moves_files_and_clears_old_location() {
        let root = tmp("move");
        let from = layout(&root, "old");
        let to = layout(&root, "new");
        for d in [&from.config_dir, &from.data_dir, &from.themes_dir.join("my-hud")] {
            std::fs::create_dir_all(d).unwrap();
        }
        std::fs::write(from.config_dir.join("config.json"), "CFG").unwrap();
        std::fs::write(from.config_dir.join("window-state.json"), "WIN").unwrap();
        std::fs::write(from.data_dir.join("telemetry.db"), "DB").unwrap();
        std::fs::write(from.data_dir.join("telemetry.db-wal"), "WAL").unwrap();
        std::fs::write(from.themes_dir.join("my-hud").join("theme.json"), "THEME").unwrap();

        let r = migrate_paths_data(&from, &to).unwrap();
        assert_eq!(r.moved, 5, "config / window-state / db / wal / theme.json");
        assert_eq!(r.kept, 0);
        assert_eq!(r.leftover, 0);

        // 新位置齐了
        assert_eq!(std::fs::read_to_string(to.config_dir.join("config.json")).unwrap(), "CFG");
        assert_eq!(std::fs::read_to_string(to.data_dir.join("telemetry.db")).unwrap(), "DB");
        assert_eq!(std::fs::read_to_string(to.data_dir.join("telemetry.db-wal")).unwrap(), "WAL");
        assert_eq!(
            std::fs::read_to_string(to.themes_dir.join("my-hud").join("theme.json")).unwrap(),
            "THEME"
        );
        // 旧位置的文件不能留（这才是「迁移」而不是「复制」）
        assert!(!from.config_dir.join("config.json").exists());
        assert!(!from.data_dir.join("telemetry.db").exists());
        assert!(!from.themes_dir.join("my-hud").join("theme.json").exists());
        // 空的 data / themes 子目录顺手清掉，但配置目录必须留着
        assert!(!from.data_dir.exists(), "搬空的 data 目录应被清掉");
        assert!(from.config_dir.is_dir(), "配置目录可能就是安装目录，绝不能删");

        let _ = std::fs::remove_dir_all(root);
    }

    /// 目标已有同名文件：不覆盖新位置、也不删旧文件 —— 两边数据都还在，不构成丢数据。
    #[test]
    fn migrate_keeps_conflicting_target_file_and_leaves_source() {
        let root = tmp("conflict");
        let from = layout(&root, "old");
        let to = layout(&root, "new");
        for d in [&from.config_dir, &from.data_dir, &to.config_dir, &to.data_dir] {
            std::fs::create_dir_all(d).unwrap();
        }
        std::fs::write(from.config_dir.join("config.json"), "OLD_CONFIG").unwrap();
        std::fs::write(from.data_dir.join("telemetry.db"), "OLD_DB").unwrap();
        std::fs::write(to.config_dir.join("config.json"), "NEW_CONFIG").unwrap();
        // 主题回收站不是用户数据，迁移时不该跟着走
        std::fs::create_dir_all(from.themes_dir.join(".trash").join("junk")).unwrap();
        std::fs::write(from.themes_dir.join(".trash").join("junk").join("x.txt"), "JUNK").unwrap();

        let r = migrate_paths_data(&from, &to).unwrap();
        assert_eq!(r.moved, 1, "只有数据库搬走");
        assert_eq!(r.kept, 1, "config.json 目标已存在，跳过");
        assert!(!to.themes_dir.join(".trash").exists(), "回收目录不搬");
        assert_eq!(
            std::fs::read_to_string(to.config_dir.join("config.json")).unwrap(),
            "NEW_CONFIG"
        );
        assert!(
            from.config_dir.join("config.json").is_file(),
            "跳过的文件必须留在原处，不能删"
        );
        assert!(!from.data_dir.join("telemetry.db").exists());

        let _ = std::fs::remove_dir_all(root);
    }

    /// 中途失败必须回滚：旧数据处理完好，新位置里已拷过去的文件要清干净。
    #[test]
    fn migrate_rolls_back_when_copy_fails() {
        let root = tmp("rollback");
        let from = layout(&root, "old");
        let to = layout(&root, "new");
        for d in [&from.config_dir, &from.data_dir, &from.themes_dir.join("my-hud")] {
            std::fs::create_dir_all(d).unwrap();
        }
        std::fs::write(from.config_dir.join("config.json"), "CFG").unwrap();
        std::fs::write(from.data_dir.join("telemetry.db"), "DB").unwrap();
        std::fs::write(from.themes_dir.join("my-hud").join("theme.json"), "THEME").unwrap();

        // 目标主题目录里先放一个同名「文件」，让建目录失败，卡在拷贝中途
        std::fs::create_dir_all(&to.themes_dir).unwrap();
        std::fs::write(to.themes_dir.join("my-hud"), "blocker").unwrap();

        let err = migrate_paths_data(&from, &to).err().expect("必须报错");
        assert!(!err.is_empty());
        // 旧位置一个文件都不能少
        assert!(from.config_dir.join("config.json").is_file());
        assert!(from.data_dir.join("telemetry.db").is_file());
        assert!(from.themes_dir.join("my-hud").join("theme.json").is_file());
        // 新位置里这次拷过去的必须清掉，不能留半截
        assert!(!to.config_dir.join("config.json").exists());
        assert!(!to.data_dir.join("telemetry.db").exists());
        assert_eq!(
            std::fs::read_to_string(to.themes_dir.join("my-hud")).unwrap(),
            "blocker",
            "回滚不能碰目标原本就有的东西"
        );

        let _ = std::fs::remove_dir_all(root);
    }

    /// 只改其中一个目录（比如只换主题目录）时，另外两个目录里的文件必须原地不动、也不算「跳过」。
    #[test]
    fn migrate_only_touched_role_leaves_other_dirs_alone() {
        let root = tmp("partial");
        let from = layout(&root, "old");
        std::fs::create_dir_all(&from.config_dir).unwrap();
        std::fs::create_dir_all(from.themes_dir.join("my-hud")).unwrap();
        std::fs::write(from.config_dir.join("config.json"), "CFG").unwrap();
        std::fs::write(from.data_dir.join("telemetry.db"), "DB").unwrap();
        std::fs::write(from.themes_dir.join("my-hud").join("theme.json"), "THEME").unwrap();

        let to = PathConfig {
            config_dir: from.config_dir.clone(),
            data_dir: from.data_dir.clone(),
            themes_dir: root.join("new-themes"),
        };
        let r = migrate_paths_data(&from, &to).unwrap();
        assert_eq!(r.moved, 1, "只搬主题文件");
        assert_eq!(r.kept, 0, "没动的目录不该被算成「目标已存在」");
        assert!(from.config_dir.join("config.json").is_file());
        assert!(from.data_dir.join("telemetry.db").is_file());
        assert!(to.themes_dir.join("my-hud").join("theme.json").is_file());
        assert!(!from.themes_dir.exists(), "搬空的旧主题目录应被清掉");

        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn validate_layout_rejects_relative_paths_and_files() {
        let root = tmp("validate");
        let mut p = layout(&root, "ok");
        p.config_dir = PathBuf::from("relative/dir");
        assert!(validate_layout(&p).unwrap_err().contains("绝对路径"));

        let file = root.join("not-a-dir.txt");
        std::fs::write(&file, "x").unwrap();
        let mut p = layout(&root, "ok");
        p.data_dir = file;
        assert!(validate_layout(&p).unwrap_err().contains("文件而不是文件夹"));
        let _ = std::fs::remove_dir_all(root);
    }

    /// 前端按 camelCase 传 layout，引导文件也按 camelCase 落盘；字段名漂移会静默丢配置。
    #[test]
    fn path_config_serializes_to_camel_case() {
        let root = tmp("serde");
        let value = serde_json::to_value(layout(&root, "ok")).unwrap();
        for key in ["configDir", "dataDir", "themesDir"] {
            assert!(value.get(key).is_some(), "缺少字段 {key}");
        }
        let file = PathsFile {
            effective: None,
            pending: Some(layout(&root, "next")),
            note: None,
        };
        let value = serde_json::to_value(&file).unwrap();
        assert!(value.get("pending").unwrap().get("configDir").is_some());
        assert!(value.get("copyOnApply").is_none(), "旧字段已移除，不能再落盘");
        let _ = std::fs::remove_dir_all(root);
    }

    /// 开发模式不能把数据塞进 target/debug（cargo build 会清掉），默认与恢复默认必须一致。
    #[test]
    fn dev_default_and_reset_are_the_same_layout() {
        if cfg!(debug_assertions) {
            assert_eq!(default_layout().0, fallback_layout());
            assert_eq!(reset_layout(), fallback_layout());
        }
    }
}
