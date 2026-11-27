//! Run the phone apps' relay on this computer, to try the bundled web app in a
//! desktop browser:
//!
//!     node desktop/scripts/build-web.mjs
//!     cargo run -p ymer-relay --example serve -- desktop/mobile-web /tmp/ymer-relay
//!
//! then open the printed address.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use ymer_relay::{Asset, Assets, Options};

struct Folder(PathBuf);

impl Assets for Folder {
    fn get(&self, path: &str) -> Option<Asset> {
        if path.split('/').any(|part| part == "..") {
            return None;
        }
        let file = self.0.join(path);
        let bytes = std::fs::read(&file).ok()?;
        Some(Asset { bytes, mime_type: mime(&file).into() })
    }
}

fn mime(path: &Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).unwrap_or("") {
        "html" => "text/html",
        "js" | "mjs" => "text/javascript",
        "css" => "text/css",
        "json" | "webmanifest" => "application/json",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "woff2" => "font/woff2",
        "wasm" => "application/wasm",
        _ => "application/octet-stream",
    }
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let mut args = std::env::args().skip(1);
    let web = PathBuf::from(args.next().unwrap_or_else(|| "desktop/mobile-web".into()));
    let data_dir = PathBuf::from(args.next().unwrap_or_else(|| std::env::temp_dir().join("ymer-relay").display().to_string()));
    let listener = ymer_relay::bind(&ymer_relay::PORTS).await?;
    let relay = ymer_relay::start(
        listener,
        Options { data_dir, assets: Arc::new(Folder(web)), app_version: env!("CARGO_PKG_VERSION").into() },
    )?;
    println!("Open {}", relay.start_url());
    std::future::pending::<()>().await;
    Ok(())
}
