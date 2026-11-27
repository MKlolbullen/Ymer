//! Ymer desktop client: one window that opens the Ymer server the user picks.
//!
//! The bundled page (desktop/src/index.html) asks for the server address and
//! remembers it; later launches go straight to the server. Server > Change Server
//! goes back to that page. The server's pages get no access to Tauri commands.

// Hide the console window on Windows release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::menu::{Menu, MenuItem, Submenu};
use tauri::{Manager, Url};

/// Where Tauri serves the bundled page on each platform.
fn picker_url() -> Url {
    let base = if cfg!(windows) {
        "http://tauri.localhost/"
    } else {
        "tauri://localhost/"
    };
    Url::parse(&format!("{base}index.html?change=1")).expect("valid URL")
}

fn main() {
    tauri::Builder::default()
        .menu(|app| {
            let menu = Menu::default(app)?;
            let change = MenuItem::with_id(app, "change-server", "Change Server…", true, None::<&str>)?;
            let reload = MenuItem::with_id(app, "reload", "Reload", true, Some("CmdOrCtrl+R"))?;
            let server = Submenu::with_items(app, "Server", true, &[&change, &reload])?;
            menu.append(&server)?;
            Ok(menu)
        })
        .on_menu_event(|app, event| {
            let Some(window) = app.get_webview_window("main") else { return };
            match event.id().as_ref() {
                "change-server" => {
                    let _ = window.navigate(picker_url());
                }
                "reload" => {
                    let _ = window.eval("location.reload()");
                }
                _ => {}
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Ymer");
}
