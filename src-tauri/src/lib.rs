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

#[cfg(unix)]
use std::os::unix::process::CommandExt;

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
    data_dir: Mutex<Option<PathBuf>>,
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
            candidates.push(exe_dir.join("resources").join("backend"));
            candidates.push(exe_dir.join("_up_").join("backend"));
            candidates.push(exe_dir.join("resources").join("_up_").join("backend"));
            if let Some(p1) = exe_dir.parent() {
                candidates.push(p1.join("backend"));
                candidates.push(p1.join("Resources").join("backend"));
                candidates.push(p1.join("Resources").join("_up_").join("backend"));
                if let Some(p2) = p1.parent() {
                    candidates.push(p2.join("backend"));
                    if let Some(p3) = p2.parent() {
                        candidates.push(p3.join("backend"));
                    }
                }
            }
        }
    }

    let valid: Vec<PathBuf> = candidates
        .into_iter()
        .filter(|p| p.join("app").join("main.py").exists())
        .collect();

    valid
        .iter()
        .find(|p| p.join(".venv").exists())
        .cloned()
        .or_else(|| valid.into_iter().next())
}

fn resolve_python_executable(backend_dir: &PathBuf) -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        let venv_python = backend_dir.join(".venv").join("Scripts").join("python.exe");
        if venv_python.exists() {
            return venv_python;
        }
        return PathBuf::from("python");
    }

    #[cfg(not(target_os = "windows"))]
    {
        let venv_py3 = backend_dir.join(".venv").join("bin").join("python3");
        if venv_py3.exists() {
            return venv_py3;
        }
        let venv_py = backend_dir.join(".venv").join("bin").join("python");
        if venv_py.exists() {
            return venv_py;
        }
        PathBuf::from("python3")
    }
}

fn open_backend_log_stdio(base_data_dir: &PathBuf) -> (Stdio, Stdio) {
    let logs_dir = base_data_dir.join("logs");
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

fn spawn_backend_process(port: u16, custom_data_dir: Option<&PathBuf>) -> Result<Child, String> {
    let backend_dir =
        resolve_backend_dir().ok_or_else(|| "Could not locate backend/app/main.py".to_string())?;
    let python_exe = resolve_python_executable(&backend_dir);
    let effective_data_dir = custom_data_dir
        .cloned()
        .unwrap_or_else(|| backend_dir.join("data"));
    let _ = fs::create_dir_all(&effective_data_dir);
    let (stdout_cfg, stderr_cfg) = open_backend_log_stdio(&effective_data_dir);

    let mut cmd = Command::new(&python_exe);
    cmd.current_dir(&backend_dir)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8");

    if let Some(dir) = custom_data_dir {
        cmd.env("NAREZKI_DATA_DIR", dir);
    }

    cmd.args([
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

    #[cfg(unix)]
    cmd.process_group(0);

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
            #[cfg(unix)]
            {
                let pgid = format!("-{}", child.id());
                let _ = Command::new("kill")
                    .args(["-TERM", &pgid])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
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
    let custom_data_dir = state.data_dir.lock().unwrap().clone();

    match spawn_backend_process(port, custom_data_dir.as_ref()) {
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
        let norm = target.canonicalize().unwrap_or_else(|_| target.clone());
        let clean = norm
            .to_string_lossy()
            .trim_start_matches(r"\\?\")
            .replace('/', "\\");
        if target.is_file() {
            cmd.arg(format!("/select,{}", clean));
        } else {
            cmd.arg(&clean);
        }
        cmd.spawn()
            .map_err(|e| format!("Failed to open Explorer: {}", e))?;
        return Ok(());
    }

    #[cfg(target_os = "macos")]
    {
        let mut cmd = Command::new("open");
        if target.is_file() {
            cmd.arg("-R").arg(&target);
        } else {
            cmd.arg(&target);
        }
        cmd.spawn()
            .map_err(|e| format!("Failed to open Finder: {}", e))?;
        return Ok(());
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let open_target = if target.is_file() {
            target.parent().unwrap_or(&target).to_path_buf()
        } else {
            target
        };
        Command::new("xdg-open")
            .arg(&open_target)
            .spawn()
            .map_err(|e| format!("Failed to open file manager: {}", e))?;
        return Ok(());
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port = find_free_port();

    let backend_state = BackendState {
        child: Mutex::new(None),
        port: Mutex::new(port),
        last_error: Mutex::new(None),
        data_dir: Mutex::new(None),
    };

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(backend_state)
        .invoke_handler(tauri::generate_handler![
            get_backend_info,
            restart_backend,
            open_in_explorer
        ])
        .setup(move |app| {
            #[cfg(not(debug_assertions))]
            let custom_data_dir: Option<PathBuf> = app.path().app_data_dir().ok();
            #[cfg(debug_assertions)]
            let custom_data_dir: Option<PathBuf> = None;

            if let Some(state) = app.try_state::<BackendState>() {
                *state.data_dir.lock().unwrap() = custom_data_dir.clone();
                match spawn_backend_process(port, custom_data_dir.as_ref()) {
                    Ok(child) => {
                        *state.child.lock().unwrap() = Some(child);
                        *state.last_error.lock().unwrap() = None;
                    }
                    Err(err) => {
                        *state.last_error.lock().unwrap() = Some(err);
                    }
                }
            }

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
