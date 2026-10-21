"""
core/downloads.py: download queue with safe filenames.

Rules this file enforces (each one was a bug in legacy):

* never overwrite a file that already exists; pick "name (2).ext" instead
* never let the server choose the path: only the final component is used,
  stripped of separators, control characters and Windows device names
* never start a download the user did not ask for
* report progress and failures through callbacks, and record every outcome
  in the download list
"""

from __future__ import annotations

import os
import re
import threading
import time
import urllib.parse
from typing import Callable, Dict, List, Optional

from core.net.client import Cancelled, NetworkError

__all__ = ["sanitise_filename", "unique_path", "Download", "DownloadManager"]

_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')
_DEVICE_NAMES = {
    "con", "prn", "aux", "nul",
    *{f"com{i}" for i in range(1, 10)},
    *{f"lpt{i}" for i in range(1, 10)},
}
MAX_FILENAME = 150


def sanitise_filename(name: str, fallback: str = "download") -> str:
    name = urllib.parse.unquote(name or "").strip()
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE.sub("_", name).strip(" .")
    if not name or name in (".", ".."):
        return fallback
    stem, dot, ext = name.rpartition(".")
    if dot and stem.lower() in _DEVICE_NAMES:
        name = stem + "_." + ext
    elif not dot and name.lower() in _DEVICE_NAMES:
        name = name + "_"
    if len(name) > MAX_FILENAME:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 8:
            keep = MAX_FILENAME - len(ext) - 1
            name = stem[:keep] + "." + ext
        else:
            name = name[:MAX_FILENAME]
    return name or fallback


def unique_path(directory: str, filename: str) -> str:
    os.makedirs(directory, exist_ok=True)
    candidate = os.path.join(directory, filename)
    if not os.path.exists(candidate):
        return candidate
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    counter = 2
    while True:
        suffix = f" ({counter})"
        name = f"{stem}{suffix}" + (f".{ext}" if ext else "")
        candidate = os.path.join(directory, name)
        if not os.path.exists(candidate):
            return candidate
        counter += 1
        if counter > 9999:
            raise RuntimeError("Could not find a free filename")


def filename_from_headers(headers: Dict[str, str], url: str) -> str:
    disposition = ""
    for key, value in headers.items():
        if key.lower() == "content-disposition":
            disposition = value
            break
    match = re.search(r"filename\*\s*=\s*([^;]+)", disposition, re.I)
    if match:
        raw = match.group(1).strip().strip('"')
        if "''" in raw:
            raw = raw.split("''", 1)[1]
        return sanitise_filename(raw)
    match = re.search(r'filename\s*=\s*"([^"]*)"', disposition, re.I) or \
        re.search(r"filename\s*=\s*([^;]+)", disposition, re.I)
    if match:
        return sanitise_filename(match.group(1))
    path = urllib.parse.urlsplit(url).path
    return sanitise_filename(os.path.basename(path) or "download")


class Download:
    __slots__ = ("url", "path", "filename", "bytes_done", "size", "status",
                 "error", "cancel_event")

    def __init__(self, url: str, path: str) -> None:
        self.url = url
        self.path = path
        self.filename = os.path.basename(path)
        self.bytes_done = 0
        self.size = 0
        self.status = "queued"
        self.error = ""
        self.cancel_event = threading.Event()

    @property
    def progress(self) -> float:
        """0..1, or -1 when the server did not send a Content-Length."""
        if self.size <= 0:
            return -1.0
        return max(0.0, min(1.0, self.bytes_done / self.size))

    def cancel(self) -> None:
        self.cancel_event.set()
        self.status = "cancelling"

    def __repr__(self) -> str:
        return f"Download({self.filename!r}, {self.status})"


class DownloadManager:
    def __init__(self, store, client, on_update: Optional[Callable[[Download], None]] = None,
                 max_parallel: int = 3) -> None:
        self.store = store
        self.client = client
        self.on_update = on_update
        self._slots = threading.Semaphore(max(1, max_parallel))
        self._active: Dict[str, Download] = {}
        self._lock = threading.Lock()

    def _notify(self, download: Download) -> None:
        if self.on_update is not None:
            try:
                self.on_update(download)
            except Exception:
                pass

    def is_active(self, path: str) -> bool:
        with self._lock:
            return path in self._active

    def active(self) -> List[Download]:
        with self._lock:
            return list(self._active.values())

    def cancel(self, download: Download) -> None:
        download.cancel()

    def start(self, url: str, directory: str, referer: str = "",
              filename: Optional[str] = None,
              exact_path: Optional[str] = None) -> Download:
        """Queue a download.

        `exact_path` is used when the user picked a destination in a save
        dialog: the file is written to "<path>.part" and only moved into
        place once the transfer completes, so a failure can never truncate
        an existing file.
        """
        if exact_path:
            path = exact_path
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        else:
            name = sanitise_filename(filename) if filename else filename_from_headers({}, url)
            path = unique_path(directory, name)
        download = Download(url, path)
        with self._lock:
            self._active[path] = download
        self.store.add_download({
            "url": url, "path": path, "name": download.filename,
            "size": 0, "status": "in progress", "started": str(int(time.time())),
        })
        self._notify(download)
        thread = threading.Thread(target=self._run, args=(download, referer), daemon=True)
        thread.start()
        return download

    def _run(self, download: Download, referer: str) -> None:
        with self._slots:
            download.status = "downloading"
            self._notify(download)
            try:
                size = self.client.download(
                    download.url, download.path, referer=referer,
                    progress=lambda done, total, d=download: self._progress(d, done, total),
                    cancel=download.cancel_event,
                )
                download.size = size
                download.bytes_done = size
                download.status = "complete"
            except Cancelled:
                download.status = "cancelled"
                download.error = ""
                try:
                    partial = download.path + ".part"
                    if os.path.exists(partial):
                        os.remove(partial)
                except OSError:
                    pass
            except (NetworkError, OSError, RuntimeError) as exc:
                download.status = "failed"
                download.error = str(exc)
                try:
                    partial = download.path + ".part"
                    if os.path.exists(partial):
                        os.remove(partial)
                except OSError:
                    pass
            finally:
                with self._lock:
                    self._active.pop(download.path, None)
                self.store.update_download(
                    download.path,
                    size=download.size,
                    status=download.status if not download.error
                    else f"failed: {download.error[:120]}",
                )
                self._notify(download)

    def _progress(self, download: Download, done: int, total: int = 0) -> None:
        download.bytes_done = done
        if total:
            download.size = total
        self._notify(download)
