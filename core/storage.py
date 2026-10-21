"""
core/storage.py — where user data lives, and how it is written.

Previously every JSON file was opened by bare filename, i.e. relative to the
current working directory. Launch the packaged .exe from `Program Files` and
every write failed silently, losing bookmarks.

Now:
  * data lives in the proper per-user directory
    (Windows `%APPDATA%\\SurfGambit`, macOS `~/Library/Application Support/...`,
    elsewhere `$XDG_DATA_HOME/surfgambit`)
  * `SURFGAMBIT_DATA_DIR` overrides it (portable mode, tests)
  * every write is atomic (`tmp` + `os.replace`), so a crash mid-write cannot
    leave a truncated JSON file
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Optional

__all__ = ["data_dir", "data_path", "load_json", "save_json", "atomic_write"]


def data_dir() -> str:
    override = os.environ.get("SURFGAMBIT_DATA_DIR")
    if override:
        path = override
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "SurfGambit")
    elif sys.platform == "darwin":
        path = os.path.expanduser("~/Library/Application Support/SurfGambit")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        path = os.path.join(base, "surfgambit")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = tempfile.mkdtemp(prefix="surfgambit-")
    return path


def data_path(name: str) -> str:
    return os.path.join(data_dir(), name)


def atomic_write(path: str, text: str, mode: Optional[int] = None) -> None:
    """Write `text` to `path` atomically."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".sg-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        # Corrupt file: keep a copy so the user can recover it, then reset.
        try:
            os.replace(path, path + ".corrupt")
        except OSError:
            pass
        return default


def save_json(path: str, data: Any, mode: Optional[int] = None) -> bool:
    try:
        atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False), mode=mode)
        return True
    except Exception:
        return False
