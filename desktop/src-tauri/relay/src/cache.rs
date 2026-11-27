//! The relay's copy of recent API reads, used when the server can't be reached.
//!
//! It mirrors the web app's service worker cache (frontend/vite.config.ts): GET
//! requests under /api except sign-in, health, AI and downloads, network first. The
//! phone apps need their own copy because iOS web views run no service worker for an
//! address chosen at run time. Entries live in the app's private data folder and are
//! dropped when someone signs in or out, or when the server changes.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// Whether a GET to this path may be answered from the cache when offline.
pub fn cacheable(path: &str) -> bool {
    let Some(rest) = path.strip_prefix("/api/") else { return false };
    let first = rest.split('/').next().unwrap_or("");
    if matches!(first, "auth" | "health" | "ai") {
        return false;
    }
    !(path.ends_with("/download") || path.ends_with("/content"))
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Entry {
    pub content_type: Option<String>,
    pub content_encoding: Option<String>,
    #[serde(skip)]
    pub body: Vec<u8>,
}

pub struct ApiCache {
    dir: PathBuf,
    max_entries: usize,
    writes: AtomicUsize,
}

impl ApiCache {
    pub fn new(dir: PathBuf, max_entries: usize) -> Self {
        Self { dir, max_entries, writes: AtomicUsize::new(0) }
    }

    fn paths(&self, key: &str) -> (PathBuf, PathBuf) {
        let name = hex::encode(Sha256::digest(key.as_bytes()));
        (self.dir.join(format!("{name}.json")), self.dir.join(format!("{name}.body")))
    }

    /// `key` is the request's path and query.
    pub fn get(&self, key: &str) -> Option<Entry> {
        let (meta, body) = self.paths(key);
        let mut entry: Entry = serde_json::from_slice(&fs::read(meta).ok()?).ok()?;
        entry.body = fs::read(body).ok()?;
        Some(entry)
    }

    pub fn put(&self, key: &str, entry: &Entry) -> io::Result<()> {
        fs::create_dir_all(&self.dir)?;
        let (meta, body) = self.paths(key);
        write_atomic(&body, &entry.body)?;
        write_atomic(&meta, &serde_json::to_vec(entry)?)?;
        if self.writes.fetch_add(1, Ordering::Relaxed) % 50 == 49 {
            self.prune()?;
        }
        Ok(())
    }

    pub fn clear(&self) -> io::Result<()> {
        match fs::remove_dir_all(&self.dir) {
            Err(e) if e.kind() != io::ErrorKind::NotFound => Err(e),
            _ => Ok(()),
        }
    }

    /// Keep the newest `max_entries` responses.
    pub fn prune(&self) -> io::Result<()> {
        let mut metas: Vec<_> = fs::read_dir(&self.dir)?
            .filter_map(Result::ok)
            .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
            .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path())))
            .collect();
        if metas.len() <= self.max_entries {
            return Ok(());
        }
        metas.sort();
        let excess = metas.len() - self.max_entries;
        for (_, meta) in metas.into_iter().take(excess) {
            let _ = fs::remove_file(meta.with_extension("body"));
            let _ = fs::remove_file(meta);
        }
        Ok(())
    }
}

fn write_atomic(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let tmp = path.with_extension("tmp");
    fs::write(&tmp, bytes)?;
    fs::rename(tmp, path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_the_service_worker_rules() {
        assert!(cacheable("/api/workspaces"));
        assert!(cacheable("/api/pages/abc"));
        assert!(!cacheable("/api/auth/me"));
        assert!(!cacheable("/api/health"));
        assert!(!cacheable("/api/ai/ask"));
        assert!(!cacheable("/api/files/1/download"));
        assert!(!cacheable("/api/files/1/content"));
        assert!(!cacheable("/collab"));
        assert!(!cacheable("/index.html"));
        assert!(cacheable("/api/authors"), "only the auth segment itself is excluded");
    }

    #[test]
    fn stores_reads_clears_and_prunes() {
        let dir = tempfile::tempdir().unwrap();
        let cache = ApiCache::new(dir.path().join("c"), 2);
        let entry = |b: &str| Entry {
            content_type: Some("application/json".into()),
            content_encoding: None,
            body: b.as_bytes().to_vec(),
        };
        assert_eq!(cache.get("/api/a"), None);
        cache.put("/api/a", &entry("1")).unwrap();
        assert_eq!(cache.get("/api/a"), Some(entry("1")));
        cache.put("/api/a", &entry("2")).unwrap();
        assert_eq!(cache.get("/api/a").unwrap().body, b"2");

        for key in ["/api/b", "/api/c"] {
            std::thread::sleep(std::time::Duration::from_millis(20));
            cache.put(key, &entry(key)).unwrap();
        }
        cache.prune().unwrap();
        assert_eq!(cache.get("/api/a"), None, "oldest entry pruned");
        assert!(cache.get("/api/c").is_some());

        cache.clear().unwrap();
        assert_eq!(cache.get("/api/c"), None);
        cache.clear().unwrap();
    }
}
