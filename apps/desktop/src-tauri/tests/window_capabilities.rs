use std::fs;
use std::path::PathBuf;


#[test]
fn frameless_titlebar_has_all_required_window_permissions() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let capability = fs::read_to_string(manifest.join("capabilities/default.json"))
        .expect("capability file should be readable");
    let json: serde_json::Value = serde_json::from_str(&capability)
        .expect("capability file should contain valid json");
    let permissions = json["permissions"]
        .as_array()
        .expect("permissions should be an array");
    let names: Vec<&str> = permissions.iter().filter_map(|item| item.as_str()).collect();

    for required in [
        "core:window:allow-minimize",
        "core:window:allow-toggle-maximize",
        "core:window:allow-close",
        "core:window:allow-start-dragging",
    ] {
        assert!(names.contains(&required), "missing permission: {required}");
    }
}


#[test]
fn native_exit_path_stops_the_worker_even_when_webview_unmount_is_skipped() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let source = fs::read_to_string(manifest.join("src/lib.rs"))
        .expect("native app entrypoint should be readable");

    assert!(
        source.contains("RunEvent::ExitRequested"),
        "window exit must trigger native worker cleanup"
    );
    assert!(
        source.contains("stop_for_exit"),
        "native exit cleanup must use the bounded worker shutdown path"
    );
}
