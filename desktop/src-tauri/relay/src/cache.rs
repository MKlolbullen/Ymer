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
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::Mutex;

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
    /// Bumped by every [`ApiCache::clear`]. A response fetched before a clear (an
    /// earlier server or account) carries an older generation and is not stored.
    generation: AtomicU64,
    /// Serializes reads, writes, clears and pruning.
    lock: Mutex<()>,
}

impl ApiCache {
    pub fn new(dir: PathBuf, max_entries: usize) -> Self {
        Self { dir, max_entries, writes: AtomicUsize::new(0), generation: AtomicU64::new(0), lock: Mutex::new(()) }
    }

    /// Take this before sending a request whose response may be stored with [`ApiCache::put`].
    pub fn generation(&self) -> u64 {
        self.generation.load(Ordering::SeqCst)
    }

    /// One file per response: a 4-byte little-endian metadata length, the metadata
    /// as JSON, then the body. Written with one rename, so a body never pairs with
    /// another response's metadata.
    fn path(&self, key: &str) -> PathBuf {
        let name = hex::encode(Sha256::digest(key.as_bytes()));
        self.dir.join(format!("{name}.entry"))
    }

    /// `key` is the request's path and query.
    pub fn get(&self, key: &str) -> Option<Entry> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        let bytes = fs::read(self.path(key)).ok()?;
        let len = u32::from_le_bytes(bytes.get(..4)?.try_into().ok()?) as usize;
        let meta = bytes.get(4..4 + len)?;
        let mut entry: Entry = serde_json::from_slice(meta).ok()?;
        entry.body = bytes[4 + len..].to_vec();
        Some(entry)
    }

    /// Store a response fetched at `generation`; skipped if the cache was cleared since.
    pub fn put(&self, key: &str, entry: &Entry, generation: u64) -> io::Result<()> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        if generation != self.generation() {
            return Ok(());
        }
        fs::create_dir_all(&self.dir)?;
        let meta = serde_json::to_vec(entry)?;
        let len = u32::try_from(meta.len()).map_err(|_| io::Error::other("metadata too large"))?;
        let mut bytes = Vec::with_capacity(4 + meta.len() + entry.body.len());
        bytes.extend_from_slice(&len.to_le_bytes());
        bytes.extend_from_slice(&meta);
        bytes.extend_from_slice(&entry.body);
        write_atomic(&self.path(key), &bytes)?;
        if self.writes.fetch_add(1, Ordering::Relaxed) % 50 == 49 {
            self.prune()?;
        }
        Ok(())
    }

    pub fn clear(&self) -> io::Result<()> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        self.generation.fetch_add(1, Ordering::SeqCst);
        match fs::remove_dir_all(&self.dir) {
            Err(e) if e.kind() != io::ErrorKind::NotFound => Err(e),
            _ => Ok(()),
        }
    }

    /// Keep the newest `max_entries` responses. Called by `put`, which holds the lock.
    fn prune(&self) -> io::Result<()> {
        let mut metas: Vec<_> = fs::read_dir(&self.dir)?
            .filter_map(Result::ok)
            .filter(|e| e.path().extension().is_some_and(|x| x == "entry"))
            .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path())))
            .collect();
        if metas.len() <= self.max_entries {
            return Ok(());
        }
        metas.sort();
        let excess = metas.len() - self.max_entries;
        for (_, path) in metas.into_iter().take(excess) {
            let _ = fs::remove_file(path);
        }
        Ok(())
    }
}

fn write_atomic(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let mut tmp = path.as_os_str().to_owned();
    tmp.push(".tmp");
    let tmp = PathBuf::from(tmp);
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
        let g = cache.generation();
        cache.put("/api/a", &entry("1"), g).unwrap();
        assert_eq!(cache.get("/api/a"), Some(entry("1")));
        cache.put("/api/a", &entry("2"), g).unwrap();
        assert_eq!(cache.get("/api/a").unwrap().body, b"2");

        for key in ["/api/b", "/api/c"] {
            std::thread::sleep(std::time::Duration::from_millis(20));
            cache.put(key, &entry(key), g).unwrap();
        }
        let _guard = cache.lock.lock().unwrap();
        cache.prune().unwrap();
        drop(_guard);
        assert_eq!(cache.get("/api/a"), None, "oldest entry pruned");
        assert!(cache.get("/api/c").is_some());

        // A damaged file reads as a miss, not as a wrong response.
        fs::write(cache.path("/api/c"), [9, 0, 0, 0, b'{']).unwrap();
        assert_eq!(cache.get("/api/c"), None);

        cache.clear().unwrap();
        assert_eq!(cache.get("/api/c"), None);
        cache.clear().unwrap();

        // A response fetched before a clear (old server or account) is not stored.
        cache.put("/api/late", &entry("old"), g).unwrap();
        assert_eq!(cache.get("/api/late"), None);
        cache.put("/api/late", &entry("new"), cache.generation()).unwrap();
        assert_eq!(cache.get("/api/late").unwrap().body, b"new");
    }
}
