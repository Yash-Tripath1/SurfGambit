"""
core/store.py — settings, bookmarks, history and the download list.

All four live in the per-user data directory (see core/storage.py) and are
written atomically. Every load is defensive: a corrupt or hand-edited file
degrades to defaults instead of breaking startup, and the bad file is moved
aside rather than overwritten.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.storage import atomic_write, data_path, load_json, save_json

HISTORY_LIMIT = 3000
BOOKMARK_LIMIT = 1000
DOWNLOAD_LIMIT = 500
VISIT_MERGE_SECONDS = 60

DEFAULT_SETTINGS: Dict[str, Any] = {
    "homepage": "surfgambit://welcome",
    "search_engine": "Bing",
    "user_agent_name": "Chrome",
    "verify_tls": "on",
    "javascript": "off",
    "mascot_active": "on",
    "save_history": "on",
    "images": "on",
    "adblock": "off",
    "zoom": 1.0,
    "profile": "",
}

VALID_CHOICES = {
    "search_engine": ["Bing", "Google", "DuckDuckGo", "Startpage", "Wikipedia"],
    "user_agent_name": ["Chrome", "Firefox", "Safari", "SurfGambit"],
    "verify_tls": ["on", "off"],
    "javascript": ["on", "off"],
    "mascot_active": ["on", "off"],
    "save_history": ["on", "off"],
    "images": ["on", "off"],
    "adblock": ["on", "off"],
}


def _now() -> float:
    return time.time()


def _as_text(value: Any, limit: int = 2048) -> str:
    if value is None:
        return ""
    text = str(value)
    return text[:limit]


class Store:
    """One place for every piece of user state that outlives a tab."""

    def __init__(self, base: Optional[str] = None) -> None:
        self.base = base or data_path("")
        os.makedirs(self.base, exist_ok=True)
        self._settings_path = os.path.join(self.base, "settings.json")
        self._bookmarks_path = os.path.join(self.base, "bookmarks.json")
        self._history_path = os.path.join(self.base, "history.json")
        self._downloads_path = os.path.join(self.base, "downloads.json")
        self._settings: Optional[Dict[str, Any]] = None
        self._bookmarks: Optional[List[Dict[str, str]]] = None
        self._history: Optional[List[Dict[str, Any]]] = None
        self._downloads: Optional[List[Dict[str, Any]]] = None

    # -- settings -------------------------------------------------------
    def load_settings(self) -> Dict[str, Any]:
        if self._settings is None:
            raw = load_json(self._settings_path, {})
            if not isinstance(raw, dict):
                raw = {}
            merged = dict(DEFAULT_SETTINGS)
            for key, value in raw.items():
                if key not in DEFAULT_SETTINGS:
                    continue
                if key in VALID_CHOICES and value not in VALID_CHOICES[key]:
                    continue
                merged[key] = value
            try:
                merged["zoom"] = max(0.4, min(3.0, float(merged.get("zoom", 1.0))))
            except (TypeError, ValueError):
                merged["zoom"] = 1.0
            self._settings = merged
        return dict(self._settings)

    def save_settings(self, settings: Dict[str, Any]) -> bool:
        merged = dict(DEFAULT_SETTINGS)
        for key, value in (settings or {}).items():
            if key not in DEFAULT_SETTINGS:
                continue
            if key in VALID_CHOICES and value not in VALID_CHOICES[key]:
                continue
            merged[key] = value
        self._settings = merged
        return save_json(self._settings_path, merged)

    # -- bookmarks ------------------------------------------------------
    def load_bookmarks(self) -> List[Dict[str, str]]:
        if self._bookmarks is None:
            raw = load_json(self._bookmarks_path, [])
            out: List[Dict[str, str]] = []
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    url = _as_text(item.get("url"))
                    if not url:
                        continue
                    out.append({
                        "url": url,
                        "title": _as_text(item.get("title")) or url,
                        "added": _as_text(item.get("added")) or str(int(_now())),
                    })
            self._bookmarks = out[:BOOKMARK_LIMIT]
        return list(self._bookmarks)

    def save_bookmarks(self, bookmarks: Iterable[Dict[str, str]]) -> bool:
        cleaned: List[Dict[str, str]] = []
        seen = set()
        for item in bookmarks or []:
            url = _as_text((item or {}).get("url"))
            if not url or url in seen:
                continue
            seen.add(url)
            cleaned.append({
                "url": url,
                "title": _as_text(item.get("title")) or url,
                "added": _as_text(item.get("added")) or str(int(_now())),
            })
        self._bookmarks = cleaned[:BOOKMARK_LIMIT]
        return save_json(self._bookmarks_path, self._bookmarks)

    def is_bookmarked(self, url: str) -> bool:
        return any(b.get("url") == url for b in self.load_bookmarks())

    def toggle_bookmark(self, url: str, title: str) -> Tuple[bool, str]:
        """Add or remove a bookmark. Returns (now_bookmarked, message)."""
        bookmarks = self.load_bookmarks()
        for item in bookmarks:
            if item.get("url") == url:
                bookmarks.remove(item)
                self.save_bookmarks(bookmarks)
                return False, "Bookmark removed"
        bookmarks.insert(0, {"url": url, "title": title or url,
                             "added": str(int(_now()))})
        self.save_bookmarks(bookmarks)
        return True, "Bookmark added"

    def remove_bookmark(self, url: str) -> bool:
        bookmarks = self.load_bookmarks()
        remaining = [b for b in bookmarks if b.get("url") != url]
        if len(remaining) == len(bookmarks):
            return False
        self.save_bookmarks(remaining)
        return True

    # -- history --------------------------------------------------------
    def load_history(self) -> List[Dict[str, Any]]:
        if self._history is None:
            raw = load_json(self._history_path, [])
            out: List[Dict[str, Any]] = []
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    url = _as_text(item.get("url"))
                    if not url or not url.startswith(("http://", "https://")):
                        continue
                    try:
                        visited = float(item.get("visited", 0))
                    except (TypeError, ValueError):
                        visited = 0.0
                    out.append({
                        "url": url,
                        "title": _as_text(item.get("title")) or url,
                        "visited": visited,
                        "count": int(item.get("count", 1) or 1),
                    })
            out.sort(key=lambda e: e["visited"], reverse=True)
            self._history = out[:HISTORY_LIMIT]
        return list(self._history)

    def _flush_history(self) -> bool:
        return save_json(self._history_path, self._history or [])

    def record_visit(self, url: str, title: str) -> None:
        if not url.startswith(("http://", "https://")):
            return
        history = self.load_history()
        now = _now()
        for entry in history:
            if entry["url"] == url:
                if now - float(entry["visited"]) < VISIT_MERGE_SECONDS:
                    entry["visited"] = now
                    entry["title"] = title or entry["title"]
                    self._flush_history()
                    return
                history.remove(entry)
                history.insert(0, {
                    "url": url,
                    "title": title or entry["title"],
                    "visited": now,
                    "count": int(entry.get("count", 1)) + 1,
                })
                self._history = history[:HISTORY_LIMIT]
                self._flush_history()
                return
        history.insert(0, {"url": url, "title": title or url,
                           "visited": now, "count": 1})
        self._history = history[:HISTORY_LIMIT]
        self._flush_history()

    def clear_history(self) -> None:
        self._history = []
        self._flush_history()

    def history_suggestions(self, prefix: str, limit: int = 6) -> List[Dict[str, Any]]:
        prefix = prefix.strip().lower()
        if not prefix:
            return []
        scored: List[Tuple[int, Dict[str, Any]]] = []
        for entry in self.load_history():
            url = entry["url"].lower()
            title = entry["title"].lower()
            if url.startswith(prefix) or title.startswith(prefix):
                score = 3
            elif prefix in url or prefix in title:
                score = 1
            else:
                continue
            scored.append((score * 1000 + int(entry.get("count", 1)), entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:limit]]

    # -- downloads ------------------------------------------------------
    def load_downloads(self) -> List[Dict[str, Any]]:
        if self._downloads is None:
            raw = load_json(self._downloads_path, [])
            out: List[Dict[str, Any]] = []
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    path = _as_text(item.get("path"))
                    if not path:
                        continue
                    out.append({
                        "url": _as_text(item.get("url")),
                        "path": path,
                        "name": _as_text(item.get("name")) or os.path.basename(path),
                        "size": int(item.get("size", 0) or 0),
                        "status": _as_text(item.get("status")) or "unknown",
                        "started": _as_text(item.get("started")),
                    })
            self._downloads = out[:DOWNLOAD_LIMIT]
        return list(self._downloads)

    def save_downloads(self, downloads: Iterable[Dict[str, Any]]) -> bool:
        self._downloads = list(downloads)[:DOWNLOAD_LIMIT]
        return save_json(self._downloads_path, self._downloads)

    def add_download(self, entry: Dict[str, Any]) -> None:
        downloads = self.load_downloads()
        downloads.insert(0, {
            "url": _as_text(entry.get("url")),
            "path": _as_text(entry.get("path")),
            "name": _as_text(entry.get("name")) or os.path.basename(_as_text(entry.get("path"))),
            "size": int(entry.get("size", 0) or 0),
            "status": _as_text(entry.get("status")) or "in progress",
            "started": _as_text(entry.get("started")) or str(int(_now())),
        })
        self.save_downloads(downloads)

    def update_download(self, path: str, **fields: Any) -> None:
        downloads = self.load_downloads()
        for entry in downloads:
            if entry.get("path") == path:
                for key, value in fields.items():
                    if key in entry:
                        entry[key] = value
                break
        self.save_downloads(downloads)

    def clear_downloads(self) -> None:
        self.save_downloads([])
