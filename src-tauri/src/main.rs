// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use std::sync::Mutex;
use tauri::Manager;

/// Holds the backend port for frontend queries.
struct BackendPort(Mutex<u16>);

/// Tauri command: frontend calls this to discover the backend API port.
#[tauri::command]
fn get_backend_port(state: tauri::State<BackendPort>) -> u16 {
    *state.0.lock().unwrap()
}

/// Find an available TCP port on localhost.
fn find_available_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .expect("无法绑定端口")
        .local_addr()
        .unwrap()
        .port()
}

fn main() {
    // In release mode, pick a dynamic port for the sidecar backend.
    // In dev mode, use the default 8000 (backend started separately).
    let port: u16 = if cfg!(debug_assertions) {
        8000
    } else {
        find_available_port()
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(BackendPort(Mutex::new(port)))
        .invoke_handler(tauri::generate_handler![get_backend_port])
        .setup(move |app| {
            // ── Spawn sidecar in release builds ──
            #[cfg(not(debug_assertions))]
            {
                use tauri_plugin_shell::ShellExt;

                let sidecar = app
                    .shell()
                    .sidecar("binaries/ai-reader-backend")
                    .expect("无法创建 sidecar 命令")
                    .args(["--port", &port.to_string()]);

                let (_rx, _child) = sidecar.spawn().expect("无法启动后端 sidecar");
            }

            // ── Auto-update check in release builds ──
            #[cfg(not(debug_assertions))]
            {
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(e) = check_for_updates(handle).await {
                        eprintln!("更新检查失败: {e}");
                    }
                });
            }

            // Suppress unused variable warning in debug mode
            #[cfg(debug_assertions)]
            let _ = app;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("启动 Tauri 应用失败");
}

/// Check for updates and prompt the user if available.
#[cfg(not(debug_assertions))]
async fn check_for_updates(app: tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    use tauri_plugin_updater::UpdaterExt;

    let updater = app.updater()?;

    if let Some(update) = updater.check().await? {
        // Update is available — the frontend can handle the UI prompt
        // via the updater JS API, or we can auto-download here.
        // For now, log the available version; frontend handles UX.
        eprintln!(
            "发现新版本: {} (当前: {})",
            update.version, update.current_version
        );
    }

    Ok(())
}
