//! Header rewriting between the web view (http://127.0.0.1:<port>) and the server.
//!
//! To the server, requests look like they come from its own pages: Host, Origin and
//! Referer name the server. To the web view, responses look like they come from the
//! relay: cookies lose Domain and Secure (the relay's origin is plain http on the
//! device's loopback), redirects to the server become relative, and HSTS is dropped
//! so 127.0.0.1 is never pinned to https.

use axum::http::{header, HeaderMap, HeaderName, HeaderValue};
use url::Url;

/// The cookie that proves a request comes from the app's own web view.
pub const APP_COOKIE: &str = "ymer_app";

const HOP_BY_HOP: [&str; 9] = [
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
];

fn hop_by_hop(name: &HeaderName) -> bool {
    HOP_BY_HOP.contains(&name.as_str())
}

/// `https://ymer.example.com:8443` without a trailing slash.
pub fn origin(server: &Url) -> String {
    server.origin().ascii_serialization()
}

/// Read one cookie from a request.
pub fn cookie(headers: &HeaderMap, name: &str) -> Option<String> {
    headers
        .get_all(header::COOKIE)
        .iter()
        .filter_map(|v| v.to_str().ok())
        .flat_map(|v| v.split(';'))
        .filter_map(|pair| pair.trim().split_once('='))
        .find(|(k, _)| *k == name)
        .map(|(_, v)| v.to_string())
}

/// Headers to send upstream for a request the web view made to the relay.
pub fn to_upstream(incoming: &HeaderMap, server: &Url) -> HeaderMap {
    let server_origin = origin(server);
    let mut out = HeaderMap::new();
    for (name, value) in incoming {
        if hop_by_hop(name) || name == header::HOST || name.as_str().starts_with("sec-websocket") {
            continue;
        }
        if name == header::COOKIE {
            let kept: Vec<&str> = value
                .to_str()
                .unwrap_or("")
                .split(';')
                .map(str::trim)
                .filter(|pair| !pair.is_empty() && pair.split('=').next() != Some(APP_COOKIE))
                .collect();
            if !kept.is_empty() {
                if let Ok(v) = HeaderValue::from_str(&kept.join("; ")) {
                    out.append(header::COOKIE, v);
                }
            }
            continue;
        }
        if name == header::ORIGIN {
            if let Ok(v) = HeaderValue::from_str(&server_origin) {
                out.insert(header::ORIGIN, v);
            }
            continue;
        }
        if name == header::REFERER {
            let path = value
                .to_str()
                .ok()
                .and_then(|r| Url::parse(r).ok())
                .map(|u| u[url::Position::BeforePath..].to_string())
                .unwrap_or_else(|| "/".into());
            if let Ok(v) = HeaderValue::from_str(&format!("{server_origin}{path}")) {
                out.insert(header::REFERER, v);
            }
            continue;
        }
        out.append(name.clone(), value.clone());
    }
    out
}

/// Make a server cookie work on the relay's origin.
pub fn rewrite_set_cookie(value: &str) -> String {
    let mut parts = value.split(';').map(str::trim);
    let mut out = vec![parts.next().unwrap_or("").to_string()];
    for attr in parts {
        let lower = attr.to_ascii_lowercase();
        if lower.starts_with("domain=") || lower == "secure" || lower == "partitioned" {
            continue;
        }
        if lower == "samesite=none" {
            // SameSite=None requires Secure; everything here is same-origin anyway.
            out.push("SameSite=Lax".into());
            continue;
        }
        out.push(attr.to_string());
    }
    out.join("; ")
}

/// Headers to return to the web view for a server response.
pub fn from_upstream(upstream: &HeaderMap, server: &Url) -> HeaderMap {
    let server_origin = origin(server);
    let mut out = HeaderMap::new();
    for (name, value) in upstream {
        if hop_by_hop(name) || name == header::STRICT_TRANSPORT_SECURITY || name == "alt-svc" {
            continue;
        }
        if name == header::SET_COOKIE {
            if let Some(v) = value.to_str().ok().map(rewrite_set_cookie) {
                if let Ok(v) = HeaderValue::from_str(&v) {
                    out.append(header::SET_COOKIE, v);
                }
            }
            continue;
        }
        if name == header::LOCATION {
            let loc = value.to_str().unwrap_or("");
            let local = match loc.strip_prefix(&server_origin) {
                Some("") => "/".to_string(),
                Some(rest) if rest.starts_with('/') => rest.to_string(),
                _ => loc.to_string(),
            };
            if let Ok(v) = HeaderValue::from_str(&local) {
                out.insert(header::LOCATION, v);
            }
            continue;
        }
        out.append(name.clone(), value.clone());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn server() -> Url {
        Url::parse("https://ymer.example.com/").unwrap()
    }

    #[test]
    fn requests_look_like_they_come_from_the_server() {
        let mut h = HeaderMap::new();
        h.insert(header::HOST, "127.0.0.1:47130".parse().unwrap());
        h.insert(header::ORIGIN, "http://127.0.0.1:47130".parse().unwrap());
        h.insert(header::REFERER, "http://127.0.0.1:47130/w/1?x=2".parse().unwrap());
        h.insert(header::COOKIE, "ymer_app=secret; ymer_access=abc".parse().unwrap());
        h.insert(header::CONNECTION, "keep-alive".parse().unwrap());
        h.insert("x-csrf-token", "t".parse().unwrap());
        let out = to_upstream(&h, &server());
        assert!(out.get(header::HOST).is_none());
        assert!(out.get(header::CONNECTION).is_none());
        assert_eq!(out[header::ORIGIN], "https://ymer.example.com");
        assert_eq!(out[header::REFERER], "https://ymer.example.com/w/1?x=2");
        assert_eq!(out[header::COOKIE], "ymer_access=abc");
        assert_eq!(out["x-csrf-token"], "t");

        let mut only_app = HeaderMap::new();
        only_app.insert(header::COOKIE, "ymer_app=secret".parse().unwrap());
        assert!(to_upstream(&only_app, &server()).get(header::COOKIE).is_none());
    }

    #[test]
    fn reads_cookies() {
        let mut h = HeaderMap::new();
        h.insert(header::COOKIE, "a=1; ymer_app=xyz".parse().unwrap());
        assert_eq!(cookie(&h, APP_COOKIE).as_deref(), Some("xyz"));
        assert_eq!(cookie(&h, "b"), None);
    }

    #[test]
    fn cookies_work_on_the_relay_origin() {
        assert_eq!(
            rewrite_set_cookie("ymer_access=abc; Domain=example.com; Secure; HttpOnly; Path=/api; SameSite=Lax"),
            "ymer_access=abc; HttpOnly; Path=/api; SameSite=Lax"
        );
        assert_eq!(rewrite_set_cookie("a=b; secure; SameSite=None"), "a=b; SameSite=Lax");
    }

    #[test]
    fn responses_point_at_the_relay() {
        let mut h = HeaderMap::new();
        h.insert(header::LOCATION, "https://ymer.example.com/api/x".parse().unwrap());
        h.insert(header::STRICT_TRANSPORT_SECURITY, "max-age=1".parse().unwrap());
        h.insert(header::TRANSFER_ENCODING, "chunked".parse().unwrap());
        h.insert(header::CONTENT_TYPE, "application/json".parse().unwrap());
        h.append(header::SET_COOKIE, "a=1; Secure".parse().unwrap());
        h.append(header::SET_COOKIE, "b=2; Domain=x".parse().unwrap());
        let out = from_upstream(&h, &server());
        assert_eq!(out[header::LOCATION], "/api/x");
        assert!(out.get(header::STRICT_TRANSPORT_SECURITY).is_none());
        assert!(out.get(header::TRANSFER_ENCODING).is_none());
        assert_eq!(out[header::CONTENT_TYPE], "application/json");
        let cookies: Vec<_> = out.get_all(header::SET_COOKIE).iter().collect();
        assert_eq!(cookies, ["a=1", "b=2"]);

        let mut other = HeaderMap::new();
        other.insert(header::LOCATION, "https://elsewhere.example/".parse().unwrap());
        assert_eq!(from_upstream(&other, &server())[header::LOCATION], "https://elsewhere.example/");
    }
}
