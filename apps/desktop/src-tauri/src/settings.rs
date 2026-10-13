use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tauri::Manager;

const DEFAULT_BASE_URL: &str = "https://dashscope.aliyuncs.com/compatible-mode/v1";
const DEFAULT_API_KEY: &str = "YOUR_API_KEY";
const DEFAULT_MODEL: &str = "qwen3.7-plus";

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
pub struct ModelSettings {
    #[serde(rename = "DASHSCOPE_BASE_URL")]
    pub base_url: String,
    #[serde(rename = "DASHSCOPE_API_KEY")]
    pub api_key: String,
    #[serde(rename = "DASHSCOPE_MODEL")]
    pub model: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ModelSettingsState {
    pub current: ModelSettings,
    pub defaults: ModelSettings,
}

#[derive(Debug, thiserror::Error)]
pub enum SettingsError {
    #[error("接口 URL 必须以 http:// 或 https:// 开头")]
    InvalidBaseUrl,
    #[error("API Key 不能为空")]
    MissingApiKey,
    #[error("模型名称不能为空")]
    MissingModel,
    #[error("模型配置文件格式不正确: {0}")]
    InvalidJson(#[from] serde_json::Error),
    #[error("模型配置保存失败: {0}")]
    Io(#[from] std::io::Error),
}

impl ModelSettings {
    pub fn company_default() -> Self {
        Self {
            base_url: DEFAULT_BASE_URL.to_string(),
            api_key: DEFAULT_API_KEY.to_string(),
            model: DEFAULT_MODEL.to_string(),
        }
    }

    fn normalized(self) -> Result<Self, SettingsError> {
        let base_url = self.base_url.trim().trim_end_matches('/').to_string();
        let api_key = self.api_key.trim().to_string();
        let model = self.model.trim().to_string();
        if !(base_url.starts_with("https://") || base_url.starts_with("http://")) {
            return Err(SettingsError::InvalidBaseUrl);
        }
        if api_key.is_empty() {
            return Err(SettingsError::MissingApiKey);
        }
        if model.is_empty() {
            return Err(SettingsError::MissingModel);
        }
        Ok(Self {
            base_url,
            api_key,
            model,
        })
    }
}

pub fn load_or_create_settings(path: &Path) -> Result<ModelSettingsState, SettingsError> {
    let defaults = ModelSettings::company_default();
    let current = if path.is_file() {
        serde_json::from_str::<ModelSettings>(&fs::read_to_string(path)?)?.normalized()?
    } else {
        save_settings(path, defaults.clone())?
    };
    Ok(ModelSettingsState { current, defaults })
}

pub fn save_settings(
    path: &Path,
    settings: ModelSettings,
) -> Result<ModelSettings, SettingsError> {
    let settings = settings.normalized()?;
    let parent = path.parent().ok_or_else(|| {
        SettingsError::Io(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "settings path has no parent",
        ))
    })?;
    fs::create_dir_all(parent)?;
    let temporary = path.with_extension("writing.json");
    let backup = path.with_extension("previous.json");
    fs::write(&temporary, serde_json::to_vec_pretty(&settings)?)?;

    if path.exists() {
        if backup.exists() {
            fs::remove_file(&backup)?;
        }
        fs::rename(path, &backup)?;
        if let Err(error) = fs::rename(&temporary, path) {
            let _ = fs::rename(&backup, path);
            let _ = fs::remove_file(&temporary);
            return Err(SettingsError::Io(error));
        }
        fs::remove_file(backup)?;
    } else {
        fs::rename(temporary, path)?;
    }
    Ok(settings)
}

fn settings_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    app.path()
        .local_data_dir()
        .map(|path| path.join("TrademarkQueryDesktop").join("settings.json"))
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn load_model_settings(app: tauri::AppHandle) -> Result<ModelSettingsState, String> {
    load_or_create_settings(&settings_path(&app)?).map_err(|error| error.to_string())
}

#[tauri::command]
pub fn save_model_settings(
    app: tauri::AppHandle,
    settings: ModelSettings,
) -> Result<ModelSettings, String> {
    save_settings(&settings_path(&app)?, settings).map_err(|error| error.to_string())
}
