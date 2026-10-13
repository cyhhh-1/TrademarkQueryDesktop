use serde_json::Value;
use tempfile::tempdir;
use trademark_query_desktop_lib::settings::{
    load_or_create_settings, save_settings, ModelSettings,
};

#[test]
fn first_load_creates_a_ready_to_use_company_configuration() {
    let directory = tempdir().unwrap();
    let path = directory.path().join("settings.json");

    let state = load_or_create_settings(&path).unwrap();

    assert!(path.is_file());
    assert_eq!(
        state.current.base_url,
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    );
    assert_eq!(state.current.api_key, "YOUR_API_KEY");

    assert_eq!(state.current.model, "qwen3.7-plus");
    assert_eq!(state.current, state.defaults);

    let stored: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert_eq!(stored["DASHSCOPE_MODEL"], "qwen3.7-plus");
    assert!(stored["DASHSCOPE_API_KEY"].as_str().is_some_and(|key| !key.is_empty()));
}

#[test]
fn saved_configuration_is_trimmed_and_returned_on_the_next_load() {
    let directory = tempdir().unwrap();
    let path = directory.path().join("settings.json");
    let settings = ModelSettings {
        base_url: " https://gateway.example.com/v1/ ".to_string(),
        api_key: " replacement-key ".to_string(),
        model: " qwen-vl-max ".to_string(),
    };

    let saved = save_settings(&path, settings).unwrap();
    let loaded = load_or_create_settings(&path).unwrap();

    assert_eq!(saved.base_url, "https://gateway.example.com/v1");
    assert_eq!(saved.api_key, "replacement-key");
    assert_eq!(saved.model, "qwen-vl-max");
    assert_eq!(loaded.current, saved);
    assert_eq!(
        loaded.defaults.base_url,
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    );
}

#[test]
fn invalid_or_incomplete_configuration_is_rejected_without_overwriting_the_file() {
    let directory = tempdir().unwrap();
    let path = directory.path().join("settings.json");
    let original = load_or_create_settings(&path).unwrap().current;

    for invalid in [
        ModelSettings {
            base_url: "ftp://gateway.example.com".to_string(),
            api_key: "replacement-key".to_string(),
            model: "qwen-vl-max".to_string(),
        },
        ModelSettings {
            base_url: "https://gateway.example.com/v1".to_string(),
            api_key: "   ".to_string(),
            model: "qwen-vl-max".to_string(),
        },
        ModelSettings {
            base_url: "https://gateway.example.com/v1".to_string(),
            api_key: "replacement-key".to_string(),
            model: "".to_string(),
        },
    ] {
        assert!(save_settings(&path, invalid).is_err());
    }

    assert_eq!(load_or_create_settings(&path).unwrap().current, original);
}
