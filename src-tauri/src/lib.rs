use serde::Serialize;
use std::fs::{self, OpenOptions};
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, RunEvent, State,
};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

#[derive(Debug, Clone, Serialize)]
pub struct BackendInfo {
    pub url: String,
    pub ws_url: String,
    pub port: u16,
    pub running: bool,
    pub error: Option<String>,
}

struct BackendState {
    child: Mutex<Option<Child>>,
    port: Mutex<u16>,
    last_error: Mutex<Option<String>>,
}

fn find_free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|listener| listener.local_addr())
        .map(|addr| addr.port())
        .unwrap_or(8765)
}

fn resolve_backend_dir() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();

    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd.join("backend"));
        if let Some(parent) = cwd.parent() {
            candidates.push(parent.join("backend"));
        }
    }

    if let Ok(exe) = std::env::current_exe() {
        if let Some(exe_dir) = exe.parent() {
            candidates.push(exe_dir.join("backend"));
            if let Some(p1) = exe_dir.parent() {
                candidates.push(p1.join("backend"));
                if let Some(p2) = p1.parent() {
                    candidates.push(p2.join("backend"));
                    if let Some(p3) = p2.parent() {
                        candidates.push(p3.join("backend"));
                    }
                }
            }
        }
    }

    candidates
        .into_iter()
        .find(|p| p.join("app").join("main.py").exists())
}

fn resolve_python_executable(backend_dir: &PathBuf) -> PathBuf {
    #[cfg(target_os = "windows")]
    let venv_python = backend_dir.join(".venv").join("Scripts").join("python.exe");

    #[cfg(not(target_os = "windows"))]
    let venv_python = backend_dir.join(".venv").join("bin").join("python");

    if venv_python.exists() {
        venv_python
    } else {
        PathBuf::from("python")
    }
}

fn open_backend_log_stdio(backend_dir: &PathBuf) -> (Stdio, Stdio) {
    let logs_dir = backend_dir.join("data").join("logs");
    if fs::create_dir_all(&logs_dir).is_ok() {
        let log_path = logs_dir.join("backend.log");
        if let Ok(out_file) = OpenOptions::new().create(true).append(true).open(&log_path) {
            if let Ok(err_file) = out_file.try_clone() {
                return (Stdio::from(out_file), Stdio::from(err_file));
            }
        }
    }
    (Stdio::null(), Stdio::null())
}

fn spawn_backend_process(port: u16) -> Result<Child, String> {
    let backend_dir =
        resolve_backend_dir().ok_or_else(|| "Could not locate backend/app/main.py".to_string())?;
    let python_exe = resolve_python_executable(&backend_dir);
    let (stdout_cfg, stderr_cfg) = open_backend_log_stdio(&backend_dir);

    let mut cmd = Command::new(&python_exe);
    cmd.current_dir(&backend_dir)
        .args([
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            &port.to_string(),
        ])
        .stdout(stdout_cfg)
        .stderr(stderr_cfg);

    #[cfg(target_os = "windows")]
    cmd.creation_flags(CREATE_NO_WINDOW);

    cmd.spawn().map_err(|e| {
        format!(
            "Failed to spawn Python backend ({:?} in {:?}): {}",
            python_exe, backend_dir, e
        )
    })
}

fn stop_backend_process(state: &BackendState) {
    if let Ok(mut guard) = state.child.lock() {
        if let Some(mut child) = guard.take() {
            #[cfg(target_os = "windows")]
            {
                let pid = child.id().to_string();
                let mut kill_cmd = Command::new("taskkill");
                kill_cmd
                    .args(["/PID", &pid, "/T", "/F"])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .creation_flags(CREATE_NO_WINDOW);
                let _ = kill_cmd.status();
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn build_backend_info(state: &BackendState) -> BackendInfo {
    let port = *state.port.lock().unwrap();
    let mut running = false;
    if let Ok(mut guard) = state.child.lock() {
        if let Some(child) = guard.as_mut() {
            running = matches!(child.try_wait(), Ok(None));
        }
    }
    let error = state.last_error.lock().unwrap().clone();
    BackendInfo {
        url: format!("http://127.0.0.1:{}", port),
        ws_url: format!("ws://127.0.0.1:{}/ws", port),
        port,
        running,
        error,
    }
}

fn restart_backend_internal(state: &BackendState) -> BackendInfo {
    stop_backend_process(state);
    let port = find_free_port();
    *state.port.lock().unwrap() = port;

    match spawn_backend_process(port) {
        Ok(child) => {
            *state.child.lock().unwrap() = Some(child);
            *state.last_error.lock().unwrap() = None;
        }
        Err(err) => {
            *state.last_error.lock().unwrap() = Some(err);
        }
    }

    build_backend_info(state)
}

#[tauri::command]
fn get_backend_info(state: State<'_, BackendState>) -> BackendInfo {
    build_backend_info(&state)
}

#[tauri::command]
fn restart_backend(state: State<'_, BackendState>) -> BackendInfo {
    restart_backend_internal(&state)
}

#[tauri::command]
fn open_in_explorer(path: String) -> Result<(), String> {
    let target = PathBuf::from(&path);
    if !target.exists() {
        return Err(format!("Path does not exist: {}", path));
    }

    #[cfg(target_os = "windows")]
    {
        let mut cmd = Command::new("explorer");
        if target.is_file() {
            cmd.arg(format!("/select,{}", target.to_string_lossy()));
        } else {
            cmd.arg(&target);
        }
        cmd.spawn()
            .map_err(|e| format!("Failed to open Explorer: {}", e))?;
        return Ok(());
    }

    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg(&target)
            .spawn()
            .map_err(|e| format!("Failed to open Finder: {}", e))?;
        return Ok(());
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        Command::new("xdg-open")
            .arg(&target)
            .spawn()
            .map_err(|e| format!("Failed to open file manager: {}", e))?;
        return Ok(());
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port = find_free_port();
    let (initial_child, initial_error) = match spawn_backend_process(port) {
        Ok(child) => (Some(child), None),
        Err(err) => (None, Some(err)),
    };

    let backend_state = BackendState {
        child: Mutex::new(initial_child),
        port: Mutex::new(port),
        last_error: Mutex::new(initial_error),
    };

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(backend_state)
        .invoke_handler(tauri::generate_handler![
            get_backend_info,
            restart_backend,
            open_in_explorer
        ])
        .setup(|app| {
            let show_i = MenuItem::with_id(app, "show", "Открыть Narezki", true, None::<&str>)?;
            let restart_i = MenuItem::with_id(
                app,
                "restart_backend",
                "Перезапустить бэкенд",
                true,
                None::<&str>,
            )?;
            let quit_i = MenuItem::with_id(app, "quit", "Выход", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_i, &restart_i, &quit_i])?;

            let mut tray_builder = TrayIconBuilder::new()
                .menu(&menu)
                .tooltip("Narezki — AI Shorts & Reels Studio")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "restart_backend" => {
                        if let Some(state) = app.try_state::<BackendState>() {
                            let info = restart_backend_internal(&state);
                            let _ = app.emit("backend-restarted", info);
                        }
                    }
                    "quit" => {
                        if let Some(state) = app.try_state::<BackendState>() {
                            stop_backend_process(&state);
                        }
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                });

            if let Some(icon) = app.default_window_icon() {
                tray_builder = tray_builder.icon(icon.clone());
            }

            let _tray = tray_builder.build(app)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            if let Some(state) = app_handle.try_state::<BackendState>() {
                stop_backend_process(&state);
            }
        }
    });
}
