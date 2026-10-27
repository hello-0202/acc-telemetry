// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;

use acc_telemetry_lib::setup_tray;
use tauri::{Manager, RunEvent};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandChild;

struct Sidecar(Mutex<Option<CommandChild>>);

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // 系统托盘（关闭窗口后用于恢复 / 退出）
            setup_tray(app)?;

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
