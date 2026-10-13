use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::Value;
use tauri::Emitter;
#[cfg(not(debug_assertions))]
use tauri::Manager;

#[derive(Debug, Default)]
pub struct LineDecoder {
    buffer: Vec<u8>,
}

#[derive(Debug, thiserror::Error)]
pub enum DecodeError {
    #[error("Worker 输出不是有效 JSON: {0}")]
    Json(#[from] serde_json::Error),
}

impl LineDecoder {
    pub fn push(&mut self, bytes: &[u8]) -> Result<Vec<Value>, DecodeError> {
        self.buffer.extend_from_slice(bytes);
        let mut events = Vec::new();
        while let Some(newline) = self.buffer.iter().position(|byte| *byte == b'\n') {
            let mut line: Vec<u8> = self.buffer.drain(..=newline).collect();
            line.pop();
            if line.last() == Some(&b'\r') {
                line.pop();
            }
            if line.iter().all(u8::is_ascii_whitespace) {
                continue;
            }
            events.push(serde_json::from_slice(&line)?);
        }
        Ok(events)
    }
}

#[derive(Default)]
pub struct WorkerManager {
    process: Mutex<Option<WorkerProcess>>,
}

struct WorkerProcess {
    child: Child,
    stdin: ChildStdin,
}

fn stop_process(process: &mut WorkerProcess, timeout: Duration) -> Result<(), String> {
    let _ = process
        .stdin
        .write_all(b"{\"type\":\"shutdown\",\"protocol\":1}\n");
    let _ = process.stdin.flush();
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if process
            .child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_some()
        {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    process.child.kill().map_err(|error| error.to_string())?;
    process
        .child
        .wait()
        .map(|_| ())
        .map_err(|error| error.to_string())
}

impl WorkerManager {
    pub fn start(&self, app: &tauri::AppHandle) -> Result<(), String> {
        let mut slot = self.process.lock().map_err(|_| "Worker 状态锁已损坏")?;
        if let Some(process) = slot.as_mut() {
            if process
                .child
                .try_wait()
                .map_err(|error| error.to_string())?
                .is_none()
            {
                return Ok(());
            }
        }

        let (program, args, working_dir) = worker_command(app)?;
        let mut command = Command::new(program);
        command
            .args(args)
            .current_dir(working_dir)
            .env("PYTHONUTF8", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }
        let mut child = command
            .spawn()
            .map_err(|error| format!("无法启动 Worker: {error}"))?;
        let stdin = child.stdin.take().ok_or("无法连接 Worker 输入")?;
        let mut stdout = child.stdout.take().ok_or("无法连接 Worker 输出")?;
        let app_handle = app.clone();
        thread::spawn(move || {
            let mut decoder = LineDecoder::default();
            let mut buffer = [0_u8; 8192];
            loop {
                match stdout.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(count) => match decoder.push(&buffer[..count]) {
                        Ok(events) => {
                            for event in events {
                                let _ = app_handle.emit("worker-event", event);
                            }
                        }
                        Err(error) => {
                            let _ = app_handle.emit(
                                "worker-event",
                                serde_json::json!({
                                    "type": "error",
                                    "code": "WORKER_PROTOCOL_ERROR",
                                    "message": error.to_string(),
                                }),
                            );
                            break;
                        }
                    },
                    Err(error) => {
                        let _ = app_handle.emit(
                            "worker-event",
                            serde_json::json!({
                                "type": "error",
                                "code": "WORKER_READ_ERROR",
                                "message": format!("读取 Worker 输出失败: {error}"),
                            }),
                        );
                        break;
                    }
                }
            }
        });
        *slot = Some(WorkerProcess { child, stdin });
        Ok(())
    }

    pub fn send(&self, command: &Value) -> Result<(), String> {
        let mut slot = self.process.lock().map_err(|_| "Worker 状态锁已损坏")?;
        let process = slot.as_mut().ok_or("Worker 尚未启动")?;
        if process
            .child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_some()
        {
            return Err("Worker 已退出，请重新打开应用".to_string());
        }
        let mut line = serde_json::to_vec(command).map_err(|error| error.to_string())?;
        line.push(b'\n');
        process
            .stdin
            .write_all(&line)
            .map_err(|error| error.to_string())?;
        process.stdin.flush().map_err(|error| error.to_string())
    }

    pub fn stop(&self) -> Result<(), String> {
        self.stop_with_timeout(Duration::from_secs(35))
    }

    pub fn stop_for_exit(&self) -> Result<(), String> {
        self.stop_with_timeout(Duration::from_secs(25))
    }

    fn stop_with_timeout(&self, timeout: Duration) -> Result<(), String> {
        let mut slot = self.process.lock().map_err(|_| "Worker 状态锁已损坏")?;
        let Some(mut process) = slot.take() else {
            return Ok(());
        };
        stop_process(&mut process, timeout)
    }
}

impl Drop for WorkerManager {
    fn drop(&mut self) {
        if let Ok(slot) = self.process.get_mut()
            && let Some(mut process) = slot.take()
        {
            let _ = stop_process(&mut process, Duration::from_secs(35));
        }
    }
}

#[tauri::command]
pub fn worker_start(
    app: tauri::AppHandle,
    manager: tauri::State<'_, WorkerManager>,
) -> Result<(), String> {
    manager.start(&app)
}

#[tauri::command]
pub fn worker_send(command: Value, manager: tauri::State<'_, WorkerManager>) -> Result<(), String> {
    manager.send(&command)
}

#[tauri::command]
pub fn worker_stop(manager: tauri::State<'_, WorkerManager>) -> Result<(), String> {
    manager.stop()
}

fn worker_command(_app: &tauri::AppHandle) -> Result<(PathBuf, Vec<String>, PathBuf), String> {
    #[cfg(debug_assertions)]
    {
        let project = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../..")
            .canonicalize()
            .map_err(|error| error.to_string())?;
        let worker = project.join("worker");
        let python = worker.join(".venv").join("Scripts").join("python.exe");
        if !python.is_file() {
            return Err(format!("开发 Worker Python 不存在: {}", python.display()));
        }
        return Ok((python, vec!["-m".into(), "trademark_worker".into()], worker));
    }
    #[cfg(not(debug_assertions))]
    {
        let resource = _app
            .path()
            .resource_dir()
            .map_err(|error| error.to_string())?;
        let executable = resource.join("worker").join("trademark-worker.exe");
        if !executable.is_file() {
            return Err(format!("安装包缺少 Worker: {}", executable.display()));
        }
        let working = executable.parent().ok_or("Worker 路径无效")?.to_path_buf();
        Ok((executable, Vec::new(), working))
    }
}
