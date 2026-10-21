"""
core/net/cache.py — an on-disk HTTP cache with real revalidation.

Two levels:

* **memory** — an OrderedDict LRU keyed by URL, so a repaint or a back
  navigation never touches the disk;
* **disk** — `<sha1>.meta` (JSON) + `<sha1>.body` per entry under
  `<data dir>/cache/http`, written atomically and evicted oldest-first.

Freshness follows RFC 9111's useful subset: `Cache-Control: max-age`,
`Expires`, then a `Last-Modified` heuristic (10 % of the age of the document,
capped at a day). Anything else is stored but immediately stale, which means
the next navigation revalidates with `If-None-Match` / `If-Modified-Since`
and a `304 Not Modified` costs one round trip instead of a full download.

`no-store` responses are never written to disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional

__all__ = ["CacheEntry", "HTTPCache"]

MAX_BODY = 8 * 1024 * 1024          # never cache a single body above 8 MiB
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 800
_HEURISTIC_CAP = 24 * 3600


def _parse_int(value: str) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _cache_control(headers: Dict[str, str]) -> Dict[str, str]:
    raw = headers.get("cache-control", "") or ""
    out: Dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition("=")
        out[name.strip().lower()] = value.strip().strip('"')
    return out


def _http_date(value: str) -> Optional[float]:
    if not value:
        return None
    from email.utils import parsedate_to_datetime

    try:
        parsed = parsedate_to_datetime(value)
        return parsed.timestamp()
    except (TypeError, ValueError, IndexError):
        return None


class CacheEntry:
    """One cached response."""

    __slots__ = ("url", "status", "reason", "headers", "raw_headers", "body",
                 "stored_at", "expires_at", "etag", "last_modified", "size")

    def __init__(self, url: str, status: int, reason: str,
                 headers: Dict[str, str], raw_headers: List[List[str]],
                 body: bytes, stored_at: float, expires_at: float) -> None:
        self.url = url
        self.status = status
        self.reason = reason
        self.headers = headers
        self.raw_headers = raw_headers
        self.body = body
        self.stored_at = stored_at
        self.expires_at = expires_at
        self.etag = headers.get("etag", "")
        self.last_modified = headers.get("last-modified", "")
        self.size = len(body)

    # -- freshness -------------------------------------------------------
    def is_fresh(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return now < self.expires_at

    def conditional_headers(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if self.etag:
            out["If-None-Match"] = self.etag
        if self.last_modified:
            out["If-Modified-Since"] = self.last_modified
        return out

    def refresh(self, headers: Dict[str, str], raw_headers: List[List[str]]) -> None:
        """Apply the headers of a 304 to this entry and extend its lifetime."""
        merged = dict(self.headers)
        merged.update(headers)
        self.headers = merged
        if raw_headers:
            self.raw_headers = [list(pair) for pair in raw_headers]
        self.etag = merged.get("etag", self.etag)
        self.last_modified = merged.get("last-modified", self.last_modified)
        self.stored_at = time.time()
        self.expires_at = compute_expiry(merged, self.status, time.time())

    def to_meta(self) -> Dict[str, object]:
        return {
            "url": self.url, "status": self.status, "reason": self.reason,
            "headers": self.headers, "raw_headers": self.raw_headers,
            "stored_at": self.stored_at, "expires_at": self.expires_at,
        }


def compute_expiry(headers: Dict[str, str], status: int, now: float) -> float:
    """When should this response stop being fresh?"""
    cc = _cache_control(headers)
    if "no-cache" in cc or "no-store" in cc:
        return now
    max_age = _parse_int(cc.get("max-age", ""))
    if max_age is not None:
        return now + max(0, min(max_age, 30 * 24 * 3600))
    expires = _http_date(headers.get("expires", ""))
    if expires is not None:
        return min(expires, now + 30 * 24 * 3600)
    modified = _http_date(headers.get("last-modified", ""))
    if modified is not None and modified < now:
        return now + max(60.0, min(_HEURISTIC_CAP, (now - modified) * 0.1))
    # No caching metadata at all: usable, but always revalidated.
    return now


class HTTPCache:
    """LRU memory cache backed by a bounded directory on disk."""

    def __init__(self, directory: Optional[str] = None,
                 max_bytes: int = DEFAULT_MAX_BYTES,
                 max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.directory = directory
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._memory: "OrderedDict[str, CacheEntry]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.revalidated = 0
        self.stores = 0
        if directory:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError:
                self.directory = None
        self._index: Dict[str, float] = {}
        self._scan_disk()

    # -- keys ------------------------------------------------------------
    @staticmethod
    def key_for(url: str) -> str:
        return hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()

    def _paths(self, url: str) -> tuple:
        if not self.directory:
            return None, None
        stem = os.path.join(self.directory, self.key_for(url))
        return stem + ".meta", stem + ".body"

    # -- disk index ------------------------------------------------------
    def _scan_disk(self) -> None:
        if not self.directory:
            return
        try:
            names = os.listdir(self.directory)
        except OSError:
            return
        total = 0
        for name in names:
            if not name.endswith(".meta"):
                continue
            path = os.path.join(self.directory, name)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    meta = json.load(handle)
                size = os.path.getsize(path[:-5] + ".body")
                self._index[str(meta.get("url", ""))] = float(meta.get("stored_at", 0))
                total += size
            except (OSError, ValueError):
                try:
                    os.remove(path)
                except OSError:
                    pass
        if total > self.max_bytes:
            self.evict_to_fit(total)

    def _write_disk(self, entry: CacheEntry) -> None:
        meta_path, body_path = self._paths(entry.url)
        if meta_path is None or body_path is None:
            return
        if entry.size > MAX_BODY:
            return
        try:
            tmp_body = body_path + ".tmp"
            with open(tmp_body, "wb") as handle:
                handle.write(entry.body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_body, body_path)
            tmp_meta = meta_path + ".tmp"
            with open(tmp_meta, "w", encoding="utf-8") as handle:
                json.dump(entry.to_meta(), handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_meta, meta_path)
        except OSError:
            return
        self._index[entry.url] = entry.stored_at
        self.evict_to_fit(self.total_bytes())

    def total_bytes(self) -> int:
        if not self.directory:
            return 0
        total = 0
        for name in list(self._index.keys()):
            _meta, body = self._paths(name)
            if body:
                try:
                    total += os.path.getsize(body)
                except OSError:
                    pass
        return total

    def evict_to_fit(self, current: Optional[int] = None) -> None:
        if not self.directory:
            return
        total = self.total_bytes() if current is None else current
        order = sorted(self._index.items(), key=lambda item: item[1])
        while order and (total > self.max_bytes or len(order) > self.max_entries):
            url, _stamp = order.pop(0)
            meta_path, body_path = self._paths(url)
            for path in (meta_path, body_path):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            self._index.pop(url, None)
            with self._lock:
                self._memory.pop(url, None)
            try:
                total -= os.path.getsize(body_path) if body_path else 0
            except OSError:
                pass

    # -- public API ------------------------------------------------------
    def lookup(self, url: str) -> Optional[CacheEntry]:
        with self._lock:
            entry = self._memory.get(url)
            if entry is not None:
                self._memory.move_to_end(url)
                self.hits += 1
                return entry
        meta_path, body_path = self._paths(url)
        if not meta_path or not os.path.exists(meta_path):
            with self._lock:
                self.misses += 1
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
            with open(body_path, "rb") as handle:
                body = handle.read()
        except (OSError, ValueError):
            with self._lock:
                self.misses += 1
            return None
        entry = CacheEntry(
            url=meta.get("url", url), status=int(meta.get("status", 200)),
            reason=meta.get("reason", ""),
            headers={str(k): str(v) for k, v in (meta.get("headers") or {}).items()},
            raw_headers=[list(pair) for pair in (meta.get("raw_headers") or [])],
            body=body, stored_at=float(meta.get("stored_at", 0)),
            expires_at=float(meta.get("expires_at", 0)),
        )
        with self._lock:
            self._memory[url] = entry
            self.misses += 1
            while len(self._memory) > 300:
                self._memory.popitem(last=False)
        return entry

    def store(self, url: str, status: int, reason: str,
              headers: Dict[str, str], raw_headers: List[List[str]],
              body: bytes) -> None:
        cc = _cache_control(headers)
        if "no-store" in cc or status != 200:
            return
        if not body or len(body) > MAX_BODY:
            return
        now = time.time()
        entry = CacheEntry(url, status, reason, dict(headers),
                           [list(pair) for pair in raw_headers], body, now,
                           compute_expiry(headers, status, now))
        with self._lock:
            self._memory[url] = entry
            self._memory.move_to_end(url)
            while len(self._memory) > 300:
                self._memory.popitem(last=False)
            self.stores += 1
        self._write_disk(entry)

    def note_revalidated(self, entry: CacheEntry, headers: Dict[str, str],
                         raw_headers: List[List[str]]) -> None:
        entry.refresh(headers, raw_headers)
        with self._lock:
            self._memory[entry.url] = entry
            self._memory.move_to_end(entry.url)
            self.revalidated += 1
        self._write_disk(entry)

    def clear(self) -> None:
        with self._lock:
            self._memory.clear()
        if not self.directory:
            return
        try:
            for name in os.listdir(self.directory):
                if name.endswith((".meta", ".body", ".tmp")):
                    try:
                        os.remove(os.path.join(self.directory, name))
                    except OSError:
                        pass
        except OSError:
            pass
        self._index.clear()

    def stats(self) -> Dict[str, object]:
        return {
            "entries": len(self._index),
            "memory": len(self._memory),
            "bytes": self.total_bytes() if self.directory else 0,
            "hits": self.hits,
            "misses": self.misses,
            "revalidated": self.revalidated,
            "stored": self.stores,
        }
