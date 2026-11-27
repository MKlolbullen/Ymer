//! Ymer client apps.
//!
//! - Desktop (Windows, macOS, Linux): one window that opens the Ymer server the user
//!   picks. The bundled page (desktop/src/index.html) asks for the server address and
//!   remembers it; later launches go straight to the server. Server > Change Server
//!   goes back to that page. The server's pages get no access to Tauri commands.
//! - Phones (iOS, Android): the app bundles Ymer's web app and runs a local relay
//!   (the `ymer-relay` crate) that serves it and forwards /api, /collab and
//!   /ymer-files to the chosen server, so the app starts and edits offline.

#[cfg(desktop)]
mod desktop {
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

    pub fn run() {
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
}

#[cfg(mobile)]
mod mobile {
    use std::sync::Arc;

    use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};

    /// The web app bundled into the app (frontendDist in tauri.android/ios.conf.json).
    struct BundledAssets(AppHandle);

    impl ymer_relay::Assets for BundledAssets {
        fn get(&self, path: &str) -> Option<ymer_relay::Asset> {
            let asset = self.0.asset_resolver().get(path.to_string())?;
            Some(ymer_relay::Asset { bytes: asset.bytes, mime_type: asset.mime_type })
        }
    }

    fn setup(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
        let options = ymer_relay::Options {
            data_dir: app.path().app_data_dir()?,
            assets: Arc::new(BundledAssets(app.handle().clone())),
            app_version: app.package_info().version.to_string(),
        };
        let relay = tauri::async_runtime::block_on(async move {
            let listener = ymer_relay::bind(&ymer_relay::PORTS).await?;
            ymer_relay::start(listener, options)
        })?;
        WebviewWindowBuilder::new(app, "main", WebviewUrl::External(relay.start_url().parse()?)).build()?;
        Ok(())
    }

    pub fn run() {
        tauri::Builder::default()
            .setup(setup)
            .run(tauri::generate_context!())
            .expect("error while running Ymer");
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(desktop)]
    desktop::run();
    #[cfg(mobile)]
    mobile::run();
}
