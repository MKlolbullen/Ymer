//! The phone apps' local relay.
//!
//! The iOS and Android apps bundle Ymer's web app instead of loading it from the
//! server, so they start and edit offline like the installed web app does. The web view
//! opens `http://127.0.0.1:<port>/`, served by this relay:
//!
//! - `/api/…`, `/collab` (WebSocket) and `/ymer-files/…` are forwarded to the Ymer
//!   server the person chose. The web app keeps using same-origin paths and httpOnly
//!   cookies, exactly as it does behind the server's own nginx.
//! - `/__ymer/…` is the relay's own server picker.
//! - Everything else is the bundled web app, with `index.html` for app routes.
//!
//! A fixed port keeps the origin, and with it the web app's IndexedDB copies, the same
//! across launches. Other apps on the device can connect to the port too, so every
//! request must carry the `ymer_app` cookie, which only the app's web view receives
//! (from `/__ymer/start?t=<token>`, the first page the app opens). Without it the relay
//! answers 403 and in particular never hands out cached API responses.

pub mod cache;
pub mod headers;

use std::io;
use std::net::{Ipv4Addr, SocketAddr};
use std::path::PathBuf;
use std::sync::{Arc, RwLock};
use std::time::Duration;

use axum::body::{Body, Bytes};
use axum::extract::ws::{self, WebSocket, WebSocketUpgrade};
use axum::extract::{Query, Request, State};
use axum::http::{header, HeaderMap, HeaderValue, Method, StatusCode, Uri};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Redirect, Response};
use axum::routing::{any, get};
use axum::{Json, Router};
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use tokio::net::TcpListener;
use tokio_tungstenite::tungstenite::{self, client::IntoClientRequest};
use url::Url;

use cache::{ApiCache, Entry};
use headers::APP_COOKIE;

/// Ports tried in order. The first free one is used; keeping it stable keeps the
/// web app's local data.
pub const PORTS: [u16; 10] = [47130, 47131, 47132, 47133, 47134, 47135, 47136, 47137, 47138, 47139];

const PICKER: &str = include_str!("picker.html");
const NETWORK_FIRST_TIMEOUT: Duration = Duration::from_secs(10);
const HEALTH_TIMEOUT: Duration = Duration::from_secs(8);
const CACHE_ENTRIES: usize = 2000;
const AUTH_CHANGES: [&str; 3] = ["/api/auth/login", "/api/auth/logout", "/api/auth/register"];

/// A file of the bundled web app.
pub struct Asset {
    pub bytes: Vec<u8>,
    pub mime_type: String,
}

/// Where the bundled web app comes from (Tauri's embedded assets in the app).
pub trait Assets: Send + Sync + 'static {
    /// `path` has no leading slash, for example `index.html` or `assets/app.js`.
    fn get(&self, path: &str) -> Option<Asset>;
}

pub struct Options {
    /// The app's private data folder; the relay keeps its settings and cache here.
    pub data_dir: PathBuf,
    pub assets: Arc<dyn Assets>,
    /// Shown in the picker and compared with the server's version.
    pub app_version: String,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct Settings {
    server: Option<String>,
}

struct Relay {
    token: String,
    assets: Arc<dyn Assets>,
    app_version: String,
    settings_path: PathBuf,
    server: RwLock<Option<Url>>,
    cache: ApiCache,
    client: reqwest::Client,
}

impl Relay {
    fn server(&self) -> Option<Url> {
        self.server.read().unwrap().clone()
    }

    fn save_server(&self, url: &Url) -> io::Result<()> {
        let settings = Settings { server: Some(url.to_string()) };
        if let Some(dir) = self.settings_path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let tmp = self.settings_path.with_extension("tmp");
        std::fs::write(&tmp, serde_json::to_vec_pretty(&settings)?)?;
        std::fs::rename(tmp, &self.settings_path)?;
        *self.server.write().unwrap() = Some(url.clone());
        Ok(())
    }
}

/// A running relay. Open [`Handle::start_url`] in the app's web view.
pub struct Handle {
    pub addr: SocketAddr,
    token: String,
}

impl Handle {
    pub fn origin(&self) -> String {
        format!("http://{}", self.addr)
    }

    /// The first page to open: it hands the web view its access cookie.
    pub fn start_url(&self) -> String {
        format!("{}/__ymer/start?t={}", self.origin(), self.token)
    }
}

fn random_token() -> String {
    let mut bytes = [0u8; 24];
    getrandom::fill(&mut bytes).expect("system random number generator");
    hex::encode(bytes)
}

/// Turn what someone typed into a server origin, e.g. `ymer.lan:8080` into
/// `http://ymer.lan:8080/`.
pub fn normalize_server(raw: &str) -> Result<Url, String> {
    let text = raw.trim();
    if text.is_empty() {
        return Err("Enter the server address.".into());
    }
    let text = if text.contains("://") { text.to_string() } else { format!("http://{text}") };
    let url = Url::parse(&text).map_err(|_| "That is not a valid address.".to_string())?;
    if url.scheme() != "http" && url.scheme() != "https" {
        return Err("Use an http:// or https:// address.".into());
    }
    if url.host_str().is_none_or(str::is_empty) {
        return Err("That is not a valid address.".into());
    }
    Url::parse(&format!("{}/", url.origin().ascii_serialization())).map_err(|e| e.to_string())
}

/// Bind the first free port of [`PORTS`] on the loopback interface.
pub async fn bind(ports: &[u16]) -> io::Result<TcpListener> {
    let mut last = io::Error::new(io::ErrorKind::AddrNotAvailable, "no port to try");
    for &port in ports {
        match TcpListener::bind((Ipv4Addr::LOCALHOST, port)).await {
            Ok(listener) => return Ok(listener),
            Err(e) => last = e,
        }
    }
    Err(last)
}

/// Start the relay on `listener` (from [`bind`]) and keep serving in the background.
pub fn start(listener: TcpListener, options: Options) -> io::Result<Handle> {
    // reqwest brings its own ring provider; the WebSocket client uses the default one.
    let _ = rustls::crypto::ring::default_provider().install_default();

    let addr = listener.local_addr()?;
    let settings_path = options.data_dir.join("relay.json");
    let settings: Settings = std::fs::read(&settings_path)
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default();
    let server = settings.server.as_deref().and_then(|s| normalize_server(s).ok());
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(5))
        .build()
        .map_err(io::Error::other)?;
    let relay = Arc::new(Relay {
        token: random_token(),
        assets: options.assets,
        app_version: options.app_version,
        settings_path,
        server: RwLock::new(server),
        cache: ApiCache::new(options.data_dir.join("api-cache"), CACHE_ENTRIES),
        client,
    });
    let handle = Handle { addr, token: relay.token.clone() };
    let app = router(relay);
    tokio::spawn(serve_forever(listener, addr, app));
    Ok(handle)
}

/// Accepts connections and, if the socket stops working (iOS can take a suspended
/// app's sockets away), binds the same address again.
struct Rebinding {
    inner: TcpListener,
    addr: SocketAddr,
}

impl axum::serve::Listener for Rebinding {
    type Io = tokio::net::TcpStream;
    type Addr = SocketAddr;

    async fn accept(&mut self) -> (Self::Io, Self::Addr) {
        loop {
            match self.inner.accept().await {
                Ok(conn) => return conn,
                Err(_) => {
                    tokio::time::sleep(Duration::from_millis(250)).await;
                    if let Ok(listener) = TcpListener::bind(self.addr).await {
                        self.inner = listener;
                    }
                }
            }
        }
    }

    fn local_addr(&self) -> io::Result<Self::Addr> {
        Ok(self.addr)
    }
}

async fn serve_forever(listener: TcpListener, addr: SocketAddr, app: Router) {
    let _ = axum::serve(Rebinding { inner: listener, addr }, app).await;
}

fn router(relay: Arc<Relay>) -> Router {
    Router::new()
        .route("/__ymer", get(|| async { Redirect::to("/__ymer/") }))
        .route("/__ymer/", get(picker))
        .route("/__ymer/server", get(server_info).post(choose_server))
        .route("/collab", any(collab))
        .route("/collab/{*rest}", any(collab))
        .route("/api/{*rest}", any(forward))
        .route("/ymer-files/{*rest}", any(forward))
        .fallback(bundled)
        .layer(middleware::from_fn_with_state(relay.clone(), require_app_cookie))
        .route("/__ymer/start", get(start_page))
        .with_state(relay)
}

fn message(status: StatusCode, text: &str) -> Response {
    (status, Json(serde_json::json!({ "message": text }))).into_response()
}

async fn require_app_cookie(State(relay): State<Arc<Relay>>, request: Request, next: Next) -> Response {
    if headers::cookie(request.headers(), APP_COOKIE).as_deref() != Some(relay.token.as_str()) {
        return message(StatusCode::FORBIDDEN, "Open Ymer from the app.");
    }
    next.run(request).await
}

#[derive(Deserialize)]
struct StartQuery {
    t: String,
}

async fn start_page(State(relay): State<Arc<Relay>>, Query(q): Query<StartQuery>) -> Response {
    if q.t != relay.token {
        return message(StatusCode::FORBIDDEN, "Open Ymer from the app.");
    }
    let to = if relay.server().is_some() { "/" } else { "/__ymer/" };
    let cookie = format!("{APP_COOKIE}={}; Path=/; HttpOnly; SameSite=Strict", relay.token);
    ([(header::SET_COOKIE, cookie)], Redirect::to(to)).into_response()
}

async fn picker() -> Response {
    ([(header::CONTENT_TYPE, "text/html; charset=utf-8"), (header::CACHE_CONTROL, "no-cache")], PICKER)
        .into_response()
}

#[derive(Serialize)]
struct ServerInfo {
    server: Option<String>,
    app_version: String,
    server_version: Option<String>,
    changed: bool,
}

async fn server_info(State(relay): State<Arc<Relay>>) -> Json<ServerInfo> {
    Json(ServerInfo {
        server: relay.server().map(|u| u.to_string()),
        app_version: relay.app_version.clone(),
        server_version: None,
        changed: false,
    })
}

#[derive(Deserialize)]
struct ChooseServer {
    url: String,
}

#[derive(Deserialize)]
struct Health {
    service: Option<String>,
    version: Option<String>,
}

async fn choose_server(State(relay): State<Arc<Relay>>, Json(body): Json<ChooseServer>) -> Response {
    let url = match normalize_server(&body.url) {
        Ok(url) => url,
        Err(e) => return message(StatusCode::BAD_REQUEST, &e),
    };
    let health = relay
        .client
        .get(url.join("api/health").expect("valid path"))
        .timeout(HEALTH_TIMEOUT)
        .send()
        .await;
    let origin = headers::origin(&url);
    let health = match health {
        Err(_) => {
            return message(
                StatusCode::BAD_GATEWAY,
                &format!("Couldn't reach {origin}. Check the address and that this device can reach it."),
            )
        }
        Ok(response) => response.json::<Health>().await.ok(),
    };
    let Some(Health { service: Some(service), version }) = health else {
        return message(StatusCode::BAD_GATEWAY, &format!("{origin} answered, but it isn't a Ymer server."));
    };
    if service != "ymer-api" {
        return message(StatusCode::BAD_GATEWAY, &format!("{origin} answered, but it isn't a Ymer server."));
    }
    let changed = relay.server().as_ref() != Some(&url);
    if let Err(e) = relay.save_server(&url) {
        return message(StatusCode::INTERNAL_SERVER_ERROR, &format!("Couldn't save the server: {e}"));
    }
    let mut response = Json(ServerInfo {
        server: Some(url.to_string()),
        app_version: relay.app_version.clone(),
        server_version: version,
        changed,
    })
    .into_response();
    if changed {
        let _ = relay.cache.clear();
        // The old server's session means nothing to the new one.
        for cookie in ["ymer_access=; Max-Age=0; Path=/api", "ymer_refresh=; Max-Age=0; Path=/api/auth"] {
            response.headers_mut().append(header::SET_COOKIE, HeaderValue::from_static(cookie));
        }
    }
    response
}

fn path_and_query(uri: &Uri) -> String {
    uri.path_and_query().map(|p| p.as_str().to_string()).unwrap_or_else(|| "/".into())
}

fn cached_response(entry: Entry) -> Response {
    let mut response = Response::new(Body::from(entry.body));
    let h = response.headers_mut();
    for (name, value) in [(header::CONTENT_TYPE, entry.content_type), (header::CONTENT_ENCODING, entry.content_encoding)] {
        if let Some(v) = value.and_then(|v| HeaderValue::from_str(&v).ok()) {
            h.insert(name, v);
        }
    }
    h.insert("x-ymer-offline", HeaderValue::from_static("1"));
    response
}

/// Forward an HTTP request to the server.
async fn forward(State(relay): State<Arc<Relay>>, request: Request) -> Response {
    let Some(server) = relay.server() else {
        return message(StatusCode::SERVICE_UNAVAILABLE, "Choose a Ymer server first.");
    };
    let (parts, body) = request.into_parts();
    let key = path_and_query(&parts.uri);
    let target = match server.join(&key) {
        Ok(url) => url,
        Err(_) => return message(StatusCode::BAD_REQUEST, "Bad path."),
    };
    let cacheable = parts.method == Method::GET && cache::cacheable(parts.uri.path());
    let mut upstream = relay
        .client
        .request(parts.method.clone(), target)
        .headers(headers::to_upstream(&parts.headers, &server));
    if parts.method != Method::GET && parts.method != Method::HEAD {
        upstream = upstream.body(reqwest::Body::wrap_stream(body.into_data_stream()));
    }
    if cacheable {
        upstream = upstream.timeout(NETWORK_FIRST_TIMEOUT);
    }

    let reply = match upstream.send().await {
        Ok(reply) => reply,
        Err(_) => {
            if cacheable {
                if let Some(entry) = relay.cache.get(&key) {
                    return cached_response(entry);
                }
            }
            return message(StatusCode::SERVICE_UNAVAILABLE, "Can't reach the Ymer server.");
        }
    };

    let status = reply.status();
    if status.is_success() && AUTH_CHANGES.contains(&parts.uri.path()) {
        let _ = relay.cache.clear();
    }
    let out_headers = headers::from_upstream(reply.headers(), &server);

    if cacheable && status == StatusCode::OK {
        let content_type = header_string(reply.headers(), header::CONTENT_TYPE);
        let content_encoding = header_string(reply.headers(), header::CONTENT_ENCODING);
        let body = match reply.bytes().await {
            Ok(body) => body,
            Err(_) => {
                return match relay.cache.get(&key) {
                    Some(entry) => cached_response(entry),
                    None => message(StatusCode::SERVICE_UNAVAILABLE, "Can't reach the Ymer server."),
                }
            }
        };
        let _ = relay.cache.put(&key, &Entry { content_type, content_encoding, body: body.to_vec() });
        return build(status, out_headers, Body::from(body));
    }
    build(status, out_headers, Body::from_stream(reply.bytes_stream()))
}

fn header_string(h: &HeaderMap, name: header::HeaderName) -> Option<String> {
    h.get(name).and_then(|v| v.to_str().ok()).map(str::to_string)
}

fn build(status: StatusCode, headers: HeaderMap, body: Body) -> Response {
    let mut response = Response::new(body);
    *response.status_mut() = status;
    *response.headers_mut() = headers;
    response
}

/// Forward the collaboration WebSocket.
async fn collab(State(relay): State<Arc<Relay>>, ws: WebSocketUpgrade, uri: Uri, incoming: HeaderMap) -> Response {
    let Some(server) = relay.server() else {
        return message(StatusCode::SERVICE_UNAVAILABLE, "Choose a Ymer server first.");
    };
    let mut target = match server.join(&path_and_query(&uri)) {
        Ok(url) => url,
        Err(_) => return message(StatusCode::BAD_REQUEST, "Bad path."),
    };
    let scheme = if server.scheme() == "https" { "wss" } else { "ws" };
    let _ = target.set_scheme(scheme);
    let Ok(mut request) = target.as_str().into_client_request() else {
        return message(StatusCode::BAD_REQUEST, "Bad path.");
    };
    for (name, value) in headers::to_upstream(&incoming, &server).iter() {
        if name == header::CONTENT_LENGTH || name == header::ACCEPT_ENCODING {
            continue;
        }
        request.headers_mut().append(name.clone(), value.clone());
    }
    let upstream = match tokio::time::timeout(Duration::from_secs(10), tokio_tungstenite::connect_async(request)).await {
        Ok(Ok((stream, _))) => stream,
        _ => return message(StatusCode::BAD_GATEWAY, "Can't reach the Ymer server."),
    };
    ws.on_upgrade(move |socket| pump(socket, upstream))
}

async fn pump<S>(socket: WebSocket, upstream: tokio_tungstenite::WebSocketStream<S>)
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    let (mut to_view, mut from_view) = socket.split();
    let (mut to_server, mut from_server) = upstream.split();
    let up = async {
        while let Some(Ok(msg)) = from_view.next().await {
            let close = matches!(msg, ws::Message::Close(_));
            if to_server.send(to_tungstenite(msg)).await.is_err() || close {
                break;
            }
        }
        let _ = to_server.close().await;
    };
    let down = async {
        while let Some(Ok(msg)) = from_server.next().await {
            let Some(msg) = from_tungstenite(msg) else { continue };
            let close = matches!(msg, ws::Message::Close(_));
            if to_view.send(msg).await.is_err() || close {
                break;
            }
        }
        let _ = to_view.close().await;
    };
    tokio::select! {
        _ = up => {},
        _ = down => {},
    }
}

fn to_tungstenite(msg: ws::Message) -> tungstenite::Message {
    use tungstenite::protocol::{frame::coding::CloseCode, CloseFrame};
    match msg {
        ws::Message::Text(t) => tungstenite::Message::text(t.as_str()),
        ws::Message::Binary(b) => tungstenite::Message::Binary(b),
        ws::Message::Ping(b) => tungstenite::Message::Ping(b),
        ws::Message::Pong(b) => tungstenite::Message::Pong(b),
        ws::Message::Close(frame) => tungstenite::Message::Close(frame.map(|f| CloseFrame {
            code: CloseCode::from(f.code),
            reason: f.reason.as_str().into(),
        })),
    }
}

fn from_tungstenite(msg: tungstenite::Message) -> Option<ws::Message> {
    Some(match msg {
        tungstenite::Message::Text(t) => ws::Message::Text(t.as_str().into()),
        tungstenite::Message::Binary(b) => ws::Message::Binary(b),
        tungstenite::Message::Ping(b) => ws::Message::Ping(b),
        tungstenite::Message::Pong(b) => ws::Message::Pong(b),
        tungstenite::Message::Close(frame) => ws::Message::Close(frame.map(|f| ws::CloseFrame {
            code: f.code.into(),
            reason: f.reason.as_str().into(),
        })),
        tungstenite::Message::Frame(_) => return None,
    })
}

/// Serve the bundled web app; app routes get index.html.
async fn bundled(State(relay): State<Arc<Relay>>, method: Method, uri: Uri) -> Response {
    if method != Method::GET && method != Method::HEAD {
        return StatusCode::METHOD_NOT_ALLOWED.into_response();
    }
    let path = uri.path().trim_start_matches('/');
    let last = path.rsplit('/').next().unwrap_or("");
    let is_page = path.is_empty() || path == "index.html" || !last.contains('.');
    if is_page && relay.server().is_none() {
        return Redirect::to("/__ymer/").into_response();
    }
    let asset = if is_page { relay.assets.get("index.html") } else { relay.assets.get(path) };
    let Some(asset) = asset else {
        return (StatusCode::NOT_FOUND, "Not found").into_response();
    };
    let cache_control = if path.starts_with("assets/") { "public, max-age=31536000, immutable" } else { "no-cache" };
    let mime = if is_page { "text/html; charset=utf-8".to_string() } else { asset.mime_type };
    let body = if method == Method::HEAD { Body::empty() } else { Body::from(Bytes::from(asset.bytes)) };
    let mut response = Response::new(body);
    let h = response.headers_mut();
    if let Ok(v) = HeaderValue::from_str(&mime) {
        h.insert(header::CONTENT_TYPE, v);
    }
    h.insert(header::CACHE_CONTROL, HeaderValue::from_static(cache_control));
    response
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_server_addresses() {
        assert_eq!(normalize_server("ymer.lan:8080").unwrap().as_str(), "http://ymer.lan:8080/");
        assert_eq!(
            normalize_server(" https://ymer.example.com/w/123?x=1 ").unwrap().as_str(),
            "https://ymer.example.com/"
        );
        assert!(normalize_server("").is_err());
        assert!(normalize_server("ftp://x").is_err());
        assert!(normalize_server("http://").is_err());
    }
}
