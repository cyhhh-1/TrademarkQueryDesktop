use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use tauri::Manager;
use tauri_plugin_dialog::DialogExt;

#[derive(Debug, thiserror::Error)]
pub enum FileCommandError {
    #[error("不能覆盖原始输入文件，请选择其他文件名")]
    OriginalInput,
    #[error("结果文件不存在")]
    SourceMissing,
    #[error("只能保存为 .xlsx 文件")]
    WrongExtension,
    #[error("文件路径无效")]
    InvalidPath,
    #[error("保存结果失败: {0}")]
    Io(#[from] std::io::Error),
}

pub fn validate_save_target(input: &Path, target: &Path) -> Result<(), FileCommandError> {
    if normalized_path(input)? == normalized_path(target)? {
        return Err(FileCommandError::OriginalInput);
    }
    if target
        .extension()
        .and_then(|value| value.to_str())
        .is_none_or(|value| !value.eq_ignore_ascii_case("xlsx"))
    {
        return Err(FileCommandError::WrongExtension);
    }
    Ok(())
}

#[tauri::command]
pub async fn pick_xlsx(app: tauri::AppHandle) -> Result<Option<String>, String> {
    let picked = app
        .dialog()
        .file()
        .add_filter("Excel 工作簿", &["xlsx"])
        .blocking_pick_file();
    picked
        .map(|path| path.into_path().map(|value| value.display().to_string()))
        .transpose()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn save_output_as(
    app: tauri::AppHandle,
    source: String,
    input: Option<String>,
) -> Result<Option<String>, String> {
    let source = PathBuf::from(source);
    if !source.is_file() {
        return Err(FileCommandError::SourceMissing.to_string());
    }
    let suggested = source
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("商标查询结果.xlsx");
    let selected = app
        .dialog()
        .file()
        .add_filter("Excel 工作簿", &["xlsx"])
        .set_file_name(suggested)
        .blocking_save_file();
    let Some(selected) = selected else {
        return Ok(None);
    };
    let target = selected.into_path().map_err(|error| error.to_string())?;
    if let Some(input) = input {
        validate_save_target(Path::new(&input), &target).map_err(|error| error.to_string())?;
    }
    if normalized_path(&source).map_err(|error| error.to_string())?
        == normalized_path(&target).map_err(|error| error.to_string())?
    {
        return Err("结果已位于所选位置，无需重复保存".to_string());
    }
    atomic_copy(&source, &target).map_err(|error| error.to_string())?;
    Ok(Some(target.display().to_string()))
}

#[tauri::command]
pub fn open_task_folder(path: String) -> Result<(), String> {
    let path = PathBuf::from(path);
    let mut command = Command::new("explorer.exe");
    if path.is_file() {
        command.arg(format!("/select,{}", path.display()));
    } else {
        command.arg(path);
    }
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn clear_login_session(app: tauri::AppHandle) -> Result<(), String> {
    let local = app
        .path()
        .local_data_dir()
        .map_err(|error| error.to_string())?;
    let expected = local.join("TrademarkQueryDesktop").join("edge-profile");
    if !expected.exists() {
        return Ok(());
    }
    let resolved = expected.canonicalize().map_err(|error| error.to_string())?;
    let resolved_parent = resolved
        .parent()
        .ok_or_else(|| "登录目录路径无效".to_string())?;
    let expected_parent = local
        .join("TrademarkQueryDesktop")
        .canonicalize()
        .map_err(|error| error.to_string())?;
    if resolved_parent != expected_parent
        || resolved.file_name().and_then(|v| v.to_str()) != Some("edge-profile")
    {
        return Err("拒绝清除未经验证的目录".to_string());
    }
    if edge_debug_port_is_alive(&resolved) {
        return Err("请先关闭商标查询专用 Edge，再清除登录信息".to_string());
    }
    fs::remove_dir_all(&resolved).map_err(|error| error.to_string())
}

fn normalized_path(path: &Path) -> Result<String, FileCommandError> {
    let absolute = if path.exists() {
        path.canonicalize()?
    } else if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    Ok(absolute
        .to_string_lossy()
        .replace('/', "\\")
        .trim_end_matches('\\')
        .to_lowercase())
}

fn atomic_copy(source: &Path, target: &Path) -> Result<(), FileCommandError> {
    let parent = target.parent().ok_or(FileCommandError::InvalidPath)?;
    fs::create_dir_all(parent)?;
    let name = target
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or(FileCommandError::InvalidPath)?;
    let temporary = parent.join(format!(".{name}.writing"));
    let backup = parent.join(format!(".{name}.previous"));
    fs::copy(source, &temporary)?;
    if target.exists() {
        if backup.exists() {
            fs::remove_file(&backup)?;
        }
        fs::rename(target, &backup)?;
        if let Err(error) = fs::rename(&temporary, target) {
            let _ = fs::rename(&backup, target);
            let _ = fs::remove_file(&temporary);
            return Err(FileCommandError::Io(error));
        }
        fs::remove_file(backup)?;
    } else {
        fs::rename(temporary, target)?;
    }
    Ok(())
}

fn edge_debug_port_is_alive(profile: &Path) -> bool {
    let Ok(contents) = fs::read_to_string(profile.join("DevToolsActivePort")) else {
        return false;
    };
    let Some(port) = contents
        .lines()
        .next()
        .and_then(|line| line.parse::<u16>().ok())
    else {
        return false;
    };
    std::net::TcpStream::connect_timeout(
        &std::net::SocketAddr::from(([127, 0, 0, 1], port)),
        std::time::Duration::from_millis(250),
    )
    .is_ok()
}
