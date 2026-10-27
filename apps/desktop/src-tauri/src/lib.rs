pub mod files;
pub mod settings;
pub mod worker;

use tauri::Manager;

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(worker::WorkerManager::default())
        .invoke_handler(tauri::generate_handler![
            worker::worker_start,
            worker::worker_send,
            worker::worker_stop,
            files::pick_xlsx,
            files::save_template,
            files::save_output_as,
            files::open_task_folder,
            files::clear_login_session,
            settings::load_model_settings,
            settings::save_model_settings,
        ])
        .build(tauri::generate_context!())
        .expect("failed to build trademark query desktop application");

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::ExitRequested { .. }) {
            let manager = app_handle.state::<worker::WorkerManager>();
            let _ = manager.stop_for_exit();
        }
    });
}
