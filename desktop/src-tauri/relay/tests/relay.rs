//! The relay against a stand-in Ymer server on the loopback interface.

use std::collections::HashMap;
use std::sync::Arc;

use axum::extract::ws::{Message, WebSocketUpgrade};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::routing::{any, get, post};
use axum::{Json, Router};
use futures_util::{SinkExt, StreamExt};
use tokio::net::TcpListener;
use tokio_tungstenite::tungstenite::{self, client::IntoClientRequest};
use ymer_relay::{Asset, Assets, Handle, Options};

struct MemoryAssets(HashMap<&'static str, (&'static str, &'static str)>);

impl Assets for MemoryAssets {
    fn get(&self, path: &str) -> Option<Asset> {
        self.0.get(path).map(|(body, mime)| Asset { bytes: body.as_bytes().to_vec(), mime_type: mime.to_string() })
    }
}

fn assets() -> Arc<dyn Assets> {
    Arc::new(MemoryAssets(HashMap::from([
        ("index.html", ("<html>ymer app</html>", "text/html")),
        ("assets/app.js", ("console.log(1)", "text/javascript")),
    ])))
}

/// What the stand-in server saw in a request, echoed back as JSON.
fn echo(headers: &HeaderMap, body: String) -> serde_json::Value {
    let h = |name: header::HeaderName| headers.get(name).and_then(|v| v.to_str().ok()).map(str::to_string);
    serde_json::json!({
        "host": h(header::HOST),
        "origin": h(header::ORIGIN),
        "cookie": h(header::COOKIE),
        "body": body,
    })
}

/// A stand-in Ymer server. Every response closes its connection so stopping the
/// server really makes it unreachable.
async fn upstream() -> (String, tokio::task::JoinHandle<()>) {
    let app = Router::new()
        .route("/api/health", get(|| async { Json(serde_json::json!({"status": "ok", "service": "ymer-api", "version": "9.9.9"})) }))
        .route(
            "/api/workspaces",
            get(|headers: HeaderMap| async move {
                let n = headers.get("x-n").and_then(|v| v.to_str().ok()).unwrap_or("1").to_string();
                ([(header::SET_COOKIE, "ymer_access=tok; Secure; HttpOnly; Path=/api; Domain=example.com")], Json(serde_json::json!({ "n": n, "seen": echo(&headers, String::new()) })))
            }),
        )
        .route("/api/echo", post(|headers: HeaderMap, body: String| async move { Json(echo(&headers, body)) }))
        .route("/api/auth/login", post(|| async { StatusCode::OK }))
        .route(
            "/collab",
            any(|ws: WebSocketUpgrade, headers: HeaderMap| async move {
                let cookie = headers.get(header::COOKIE).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
                ws.on_upgrade(move |mut socket| async move {
                    let _ = socket.send(Message::Text(format!("cookie:{cookie}").into())).await;
                    while let Some(Ok(msg)) = socket.recv().await {
                        match msg {
                            Message::Text(t) => { let _ = socket.send(Message::Text(format!("echo:{t}").into())).await; }
                            Message::Binary(b) => { let _ = socket.send(Message::Binary(b)).await; }
                            Message::Close(_) => break,
                            _ => {}
                        }
                    }
                })
            }),
        )
        .layer(axum::middleware::map_response(|mut r: axum::response::Response| async move {
            if r.status() != StatusCode::SWITCHING_PROTOCOLS {
                r.headers_mut().insert(header::CONNECTION, "close".parse().unwrap());
            }
            r
        }));
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let base = format!("http://{addr}");
    // The redirect points at the server's own origin, as Flask's would.
    let app = app.route(
        "/api/redirect-abs",
        get(move || {
            let to = format!("{base}/api/elsewhere");
            async move { (StatusCode::FOUND, [(header::LOCATION, to)]).into_response() }
        }),
    );
    let task = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (format!("http://{addr}"), task)
}

struct App {
    relay: Handle,
    client: reqwest::Client,
    cookie: String,
    _dir: tempfile::TempDir,
}

impl App {
    async fn start() -> Self {
        let dir = tempfile::tempdir().unwrap();
        Self::start_in(dir).await
    }

    async fn start_in(dir: tempfile::TempDir) -> Self {
        let listener = ymer_relay::bind(&[0]).await.unwrap();
        let relay = ymer_relay::start(
            listener,
            Options { data_dir: dir.path().to_path_buf(), assets: assets(), app_version: "1.2.3".into() },
        )
        .unwrap();
        let client = reqwest::Client::builder().redirect(reqwest::redirect::Policy::none()).build().unwrap();
        let start = client.get(relay.start_url()).send().await.unwrap();
        assert_eq!(start.status(), StatusCode::SEE_OTHER);
        let set = start.headers()[header::SET_COOKIE].to_str().unwrap().to_string();
        assert!(set.contains("HttpOnly") && set.contains("SameSite=Strict"), "{set}");
        let cookie = set.split(';').next().unwrap().to_string();
        Self { relay, client, cookie, _dir: dir }
    }

    fn url(&self, path: &str) -> String {
        format!("{}{path}", self.relay.origin())
    }

    fn get(&self, path: &str) -> reqwest::RequestBuilder {
        self.client.get(self.url(path)).header(header::COOKIE, &self.cookie)
    }

    async fn choose(&self, server: &str) -> reqwest::Response {
        self.client
            .post(self.url("/__ymer/server"))
            .header(header::COOKIE, &self.cookie)
            .json(&serde_json::json!({ "url": server }))
            .send()
            .await
            .unwrap()
    }
}

#[tokio::test]
async fn only_the_app_web_view_gets_in() {
    let app = App::start().await;
    let bare = app.client.get(app.url("/")).send().await.unwrap();
    assert_eq!(bare.status(), StatusCode::FORBIDDEN);
    let wrong = app.client.get(app.url("/__ymer/start?t=nope")).send().await.unwrap();
    assert_eq!(wrong.status(), StatusCode::FORBIDDEN);
    let forged = app.client.get(app.url("/api/workspaces")).header(header::COOKIE, "ymer_app=guess").send().await.unwrap();
    assert_eq!(forged.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn asks_for_a_server_first() {
    let app = App::start().await;
    let home = app.get("/").send().await.unwrap();
    assert_eq!(home.status(), StatusCode::SEE_OTHER);
    assert_eq!(home.headers()[header::LOCATION], "/__ymer/");
    let picker = app.get("/__ymer/").send().await.unwrap().text().await.unwrap();
    assert!(picker.contains("Connect to Ymer"));
    let api = app.get("/api/workspaces").send().await.unwrap();
    assert_eq!(api.status(), StatusCode::SERVICE_UNAVAILABLE);
    let info: serde_json::Value = app.get("/__ymer/server").send().await.unwrap().json().await.unwrap();
    assert_eq!(info["server"], serde_json::Value::Null);
    assert_eq!(info["app_version"], "1.2.3");
}

#[tokio::test]
async fn checks_the_server_before_saving_it() {
    let (server, _task) = upstream().await;
    let app = App::start().await;

    let bad = app.choose("").await;
    assert_eq!(bad.status(), StatusCode::BAD_REQUEST);
    let nobody = app.choose("http://127.0.0.1:9").await;
    assert_eq!(nobody.status(), StatusCode::BAD_GATEWAY);

    let ok = app.choose(&server.replace("http://", "")).await;
    assert_eq!(ok.status(), StatusCode::OK);
    let info: serde_json::Value = ok.json().await.unwrap();
    assert_eq!(info["server"], format!("{server}/"));
    assert_eq!(info["server_version"], "9.9.9");
    assert_eq!(info["changed"], true);
    assert_eq!(info["reset_pending"], false, "nothing to clean up on the first choice");

    let again: serde_json::Value = app.choose(&server).await.json().await.unwrap();
    assert_eq!(again["changed"], false);
}

#[tokio::test]
async fn serves_the_bundled_web_app() {
    let (server, _task) = upstream().await;
    let app = App::start().await;
    app.choose(&server).await;

    for path in ["/", "/index.html", "/w/123/p/456"] {
        let page = app.get(path).send().await.unwrap();
        assert_eq!(page.status(), StatusCode::OK, "{path}");
        assert_eq!(page.headers()[header::CACHE_CONTROL], "no-cache");
        assert!(page.headers()[header::CONTENT_TYPE].to_str().unwrap().starts_with("text/html"));
        assert_eq!(page.text().await.unwrap(), "<html>ymer app</html>");
    }
    let js = app.get("/assets/app.js").send().await.unwrap();
    assert_eq!(js.headers()[header::CONTENT_TYPE], "text/javascript");
    assert!(js.headers()[header::CACHE_CONTROL].to_str().unwrap().contains("immutable"));
    assert_eq!(app.get("/assets/missing.js").send().await.unwrap().status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn forwards_api_calls_as_if_from_the_server() {
    let (server, _task) = upstream().await;
    let app = App::start().await;
    app.choose(&server).await;
    let host = server.trim_start_matches("http://");

    let read = app
        .get("/api/workspaces")
        .header(header::COOKIE, format!("{}; ymer_access=abc", app.cookie))
        .header(header::ORIGIN, app.relay.origin())
        .send()
        .await
        .unwrap();
    assert_eq!(read.status(), StatusCode::OK);
    assert_eq!(read.headers()[header::SET_COOKIE], "ymer_access=tok; HttpOnly; Path=/api");
    let body: serde_json::Value = read.json().await.unwrap();
    assert_eq!(body["seen"]["host"], host);
    assert_eq!(body["seen"]["origin"], server);
    assert_eq!(body["seen"]["cookie"], "ymer_access=abc", "the app cookie stays on the device");

    let write: serde_json::Value = app
        .client
        .post(app.url("/api/echo"))
        .header(header::COOKIE, &app.cookie)
        .body("x".repeat(100_000))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(write["body"].as_str().unwrap().len(), 100_000);

    let redirect = app.get("/api/redirect-abs").send().await.unwrap();
    assert_eq!(redirect.status(), StatusCode::FOUND);
    assert_eq!(redirect.headers()[header::LOCATION], "/api/elsewhere");
}

#[tokio::test]
async fn answers_reads_from_its_cache_when_the_server_is_gone() {
    let (server, task) = upstream().await;
    let dir = tempfile::tempdir().unwrap();
    let data = dir.path().to_path_buf();
    let app = App::start_in(dir).await;
    app.choose(&server).await;

    let first: serde_json::Value = app.get("/api/workspaces").header("x-n", "7").send().await.unwrap().json().await.unwrap();
    assert_eq!(first["n"], "7");

    task.abort();
    let _ = task.await;

    let cached = app.get("/api/workspaces").send().await.unwrap();
    assert_eq!(cached.status(), StatusCode::OK);
    assert_eq!(cached.headers()["x-ymer-offline"], "1");
    let cached: serde_json::Value = cached.json().await.unwrap();
    assert_eq!(cached["n"], "7");

    let unseen = app.get("/api/workspaces?other=1").send().await.unwrap();
    assert_eq!(unseen.status(), StatusCode::SERVICE_UNAVAILABLE);
    let write = app.client.post(app.url("/api/echo")).header(header::COOKIE, &app.cookie).send().await.unwrap();
    assert_eq!(write.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert!(data.join("relay.json").exists());
}

#[tokio::test]
async fn signing_in_drops_cached_reads() {
    let (server, task) = upstream().await;
    let app = App::start().await;
    app.choose(&server).await;
    app.get("/api/workspaces").send().await.unwrap();
    let login = app.client.post(app.url("/api/auth/login")).header(header::COOKIE, &app.cookie).send().await.unwrap();
    assert_eq!(login.status(), StatusCode::OK);
    task.abort();
    let _ = task.await;
    assert_eq!(app.get("/api/workspaces").send().await.unwrap().status(), StatusCode::SERVICE_UNAVAILABLE);
}

#[tokio::test]
async fn remembers_the_server_across_launches() {
    let (server, _task) = upstream().await;
    let keep = tempfile::tempdir().unwrap();
    let keep_path = keep.path().to_path_buf();
    let first = App::start_in(keep).await;
    first.choose(&server).await;
    std::mem::forget(first); // keep the folder for the second launch

    let listener = ymer_relay::bind(&[0]).await.unwrap();
    let relay = ymer_relay::start(listener, Options { data_dir: keep_path, assets: assets(), app_version: "1.2.3".into() }).unwrap();
    let client = reqwest::Client::builder().redirect(reqwest::redirect::Policy::none()).build().unwrap();
    let start = client.get(relay.start_url()).send().await.unwrap();
    assert_eq!(start.headers()[header::LOCATION], "/", "goes straight to the app");
}

#[tokio::test]
async fn forwards_the_collab_websocket() {
    let (server, _task) = upstream().await;
    let app = App::start().await;
    app.choose(&server).await;

    let url = app.url("/collab").replace("http://", "ws://");
    let mut request = url.into_client_request().unwrap();
    request.headers_mut().insert(header::COOKIE, format!("{}; ymer_access=abc", app.cookie).parse().unwrap());
    let (mut socket, _) = tokio_tungstenite::connect_async(request).await.unwrap();

    let hello = socket.next().await.unwrap().unwrap();
    assert_eq!(hello, tungstenite::Message::text("cookie:ymer_access=abc"));
    socket.send(tungstenite::Message::text("hi")).await.unwrap();
    assert_eq!(socket.next().await.unwrap().unwrap(), tungstenite::Message::text("echo:hi"));
    socket.send(tungstenite::Message::binary(vec![1u8, 2, 3])).await.unwrap();
    assert_eq!(socket.next().await.unwrap().unwrap(), tungstenite::Message::binary(vec![1u8, 2, 3]));
    socket.close(None).await.unwrap();

    let mut no_cookie = app.url("/collab").replace("http://", "ws://").into_client_request().unwrap();
    no_cookie.headers_mut().remove(header::COOKIE);
    assert!(tokio_tungstenite::connect_async(no_cookie).await.is_err());
}

#[tokio::test]
async fn a_change_of_server_is_finished_before_anything_reaches_the_new_one() {
    let (first, _a) = upstream().await;
    let (second, _b) = upstream().await;
    let keep = tempfile::tempdir().unwrap();
    let keep_path = keep.path().to_path_buf();
    let app = App::start_in(keep).await;
    app.choose(&first).await;
    app.get("/api/workspaces").send().await.unwrap();

    let switched: serde_json::Value = app.choose(&second).await.json().await.unwrap();
    assert_eq!(switched["changed"], true);
    assert_eq!(switched["reset_pending"], true);

    // The app closes here, before the web view cleaned up. Next launch:
    std::mem::forget(app);
    let listener = ymer_relay::bind(&[0]).await.unwrap();
    let relay = ymer_relay::start(listener, Options { data_dir: keep_path, assets: assets(), app_version: "1.2.3".into() }).unwrap();
    let client = reqwest::Client::builder().redirect(reqwest::redirect::Policy::none()).build().unwrap();
    let start = client.get(relay.start_url()).send().await.unwrap();
    assert_eq!(start.headers()[header::LOCATION], "/__ymer/", "the picker finishes the switch");
    let cookie = start.headers()[header::SET_COOKIE].to_str().unwrap().split(';').next().unwrap().to_string();
    let get = |path: &str| client.get(format!("{}{path}", relay.origin())).header(header::COOKIE, &cookie);

    let info: serde_json::Value = get("/__ymer/server").send().await.unwrap().json().await.unwrap();
    assert_eq!(info["reset_pending"], true);
    assert_eq!(get("/").send().await.unwrap().headers()[header::LOCATION], "/__ymer/");
    let api = get("/api/workspaces").header(header::COOKIE, format!("{cookie}; ymer_access=old")).send().await.unwrap();
    assert_eq!(api.status(), StatusCode::SERVICE_UNAVAILABLE, "old cookies never reach the new server");

    let done = client.post(format!("{}/__ymer/reset-done", relay.origin())).header(header::COOKIE, &cookie).send().await.unwrap();
    assert_eq!(done.status(), StatusCode::NO_CONTENT);
    let expired: Vec<_> = done.headers().get_all(header::SET_COOKIE).iter().map(|v| v.to_str().unwrap().to_string()).collect();
    assert!(expired.iter().any(|c| c.starts_with("ymer_access=;")) && expired.iter().any(|c| c.starts_with("ymer_refresh=;")));
    let body: serde_json::Value = get("/api/workspaces").send().await.unwrap().json().await.unwrap();
    assert_eq!(body["seen"]["host"], second.trim_start_matches("http://"));
}
