"""
core/accounts.py — local profiles for the browser's own pages.

Security posture (deliberately different from v1):

  * Passwords are hashed with **PBKDF2-HMAC-SHA256, 600 000 iterations**, a
    random 16-byte salt per user, and compared in constant time. v1 used one
    round of salted SHA-256, which is ~10 million guesses/second on a GPU.
  * Passwords are never stripped or truncated, never put in a URL, and never
    written to disk in any recoverable form.
  * The old "remember this HTTP password" feature wrote base64 (and previously
    XOR) into the same file. That is **removed**: basic-auth credentials are
    kept in memory for the session only. Honest obfuscation is still not
    encryption, and a browser that stores secrets it cannot protect is worse
    than one that asks again.
  * The file is written atomically with owner-only permissions.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Dict, Optional, Tuple

from .storage import load_json, save_json, data_path

__all__ = ["AccountStore", "USERNAME_MAX", "PASSWORD_MIN"]

USERNAME_MAX = 32
PASSWORD_MIN = 8
PBKDF2_ROUNDS = 600_000
SALT_BYTES = 16


class AccountStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or data_path("accounts.json")
        self._lock = threading.RLock()
        self._data = load_json(self.path, {"version": 2, "users": {}})
        if not isinstance(self._data, dict):
            self._data = {"version": 2, "users": {}}
        self._data.setdefault("users", {})
        # v1 files stored {"salt": ..., "hash": ...} with no iteration count.
        self._data.setdefault("version", 2)
        self._sessions: Dict[str, float] = {}

    # -- persistence ------------------------------------------------------
    def _save(self) -> None:
        save_json(self.path, self._data, mode=0o600)

    # -- validation -------------------------------------------------------
    @staticmethod
    def validate_username(username: str) -> Tuple[bool, str]:
        username = (username or "").strip()
        if not username:
            return False, "Username cannot be empty."
        if len(username) > USERNAME_MAX:
            return False, f"Username must be at most {USERNAME_MAX} characters."
        if not all(c.isalnum() or c in "._-" for c in username):
            return False, "Username may only contain letters, digits, dot, dash or underscore."
        return True, ""

    @staticmethod
    def validate_password(password: str) -> Tuple[bool, str]:
        if not password:
            return False, "Password cannot be empty."
        if len(password) < PASSWORD_MIN:
            return False, f"Password must be at least {PASSWORD_MIN} characters."
        if len(password) > 1024:
            return False, "Password is too long."
        return True, ""

    # -- hashing ----------------------------------------------------------
    @staticmethod
    def _hash(password: str, salt: bytes, rounds: int) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, rounds
        ).hex()

    # -- public API -------------------------------------------------------
    def register(self, username: str, password: str) -> Tuple[bool, str]:
        ok, message = self.validate_username(username)
        if not ok:
            return False, message
        ok, message = self.validate_password(password)
        if not ok:
            return False, message

        username = username.strip()
        key = username.lower()
        with self._lock:
            if key in self._data["users"]:
                return False, "That username is already taken."
            salt = secrets.token_bytes(SALT_BYTES)
            self._data["users"][key] = {
                "username": username,
                "salt": salt.hex(),
                "hash": self._hash(password, salt, PBKDF2_ROUNDS),
                "rounds": PBKDF2_ROUNDS,
                "created": int(time.time()),
            }
            self._save()
        return True, "Account created. You are signed in."

    def verify(self, username: str, password: str) -> bool:
        if not username or not password:
            return False
        key = username.strip().lower()
        with self._lock:
            record = self._data["users"].get(key)
        if not record:
            # Spend comparable time so timing does not reveal existence.
            self._hash(password, b"\x00" * SALT_BYTES, PBKDF2_ROUNDS)
            return False
        try:
            salt = bytes.fromhex(record["salt"])
            rounds = int(record.get("rounds", 1))
        except (KeyError, ValueError):
            return False
        computed = self._hash(password, salt, rounds)
        return hmac.compare_digest(computed, record.get("hash", ""))

    def login(self, username: str, password: str) -> Tuple[bool, str]:
        if self.verify(username, password):
            token = secrets.token_urlsafe(32)
            with self._lock:
                self._sessions[token] = time.time()
            return True, token
        return False, "Incorrect username or password."

    def display_name(self, username: str) -> Optional[str]:
        with self._lock:
            record = self._data["users"].get((username or "").strip().lower())
        return record.get("username") if record else None

    def user_count(self) -> int:
        with self._lock:
            return len(self._data["users"])


# ---------------------------------------------------------------------------
# Module-level compatibility API
# ---------------------------------------------------------------------------
# v1 exposed bare functions operating on a single accounts.json. They are kept
# (and implemented on top of AccountStore) because outside code and the audit
# harness import them. Stored HTTP credentials are **read** for migration but
# never written: see `save_domain_credentials`.

_default_store: Optional[AccountStore] = None
_session_credentials: Dict[str, Tuple[str, str]] = {}
_credential_lock = threading.RLock()


def _store() -> AccountStore:
    global _default_store
    if _default_store is None:
        _default_store = AccountStore()
    return _default_store


def load_accounts_data() -> Dict[str, object]:
    """Return the on-disk accounts document (creating a sane default)."""
    store = _store()
    return store._data


def save_accounts_data(data: Dict[str, object]) -> None:
    """Persist an accounts document atomically with owner-only permissions."""
    store = _store()
    if not isinstance(data, dict):
        raise TypeError("accounts data must be a dict")
    data.setdefault("version", 2)
    data.setdefault("users", {})
    store._data = data
    store._save()


def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    """PBKDF2-HMAC-SHA256 hash. Returns (hex digest, hex salt)."""
    raw_salt = bytes.fromhex(salt) if salt else secrets.token_bytes(SALT_BYTES)
    return AccountStore._hash(password, raw_salt, PBKDF2_ROUNDS), raw_salt.hex()


def register_user(username: str, password: str) -> Tuple[bool, str]:
    return _store().register(username, password)


def verify_user(username: str, password: str) -> bool:
    return _store().verify(username, password)


def save_domain_credentials(domain: str, username: str, password: str) -> None:
    """
    Remember an HTTP basic-auth pair **for this session only**.

    v1 wrote these to accounts.json, first XOR-"encrypted", then base64. Both
    are readable by anyone who can read the file, so this version keeps them
    in memory and asks again next launch.
    """
    domain = (domain or "").strip().lower()
    if not domain:
        return
    with _credential_lock:
        _session_credentials[domain] = (username or "", password or "")


def get_domain_credentials(domain: str) -> Optional[Tuple[str, str]]:
    """
    Look up credentials for a host.

    Accepts the two shapes found in the wild:

      * v2: {"username": ..., "password": ...}
      * v1: "Basic <base64>" (bare string) - parsed for migration, never written
    """
    domain = (domain or "").strip().lower()
    if not domain:
        return None
    with _credential_lock:
        cached = _session_credentials.get(domain)
    if cached:
        return cached

    data = _store()._data
    credentials = data.get("credentials")
    if not isinstance(credentials, dict):
        return None
    record = credentials.get(domain)
    if isinstance(record, dict):
        username = record.get("username", "")
        password = record.get("password", "")
        return (str(username), str(password)) if username or password else None
    if isinstance(record, str):
        text = record.strip()
        if text.lower().startswith("basic "):
            text = text[6:].strip()
        try:
            import base64

            decoded = base64.b64decode(text + "=" * (-len(text) % 4), validate=False)
            username, _, password = decoded.decode("utf-8").partition(":")
            return (username, password)
        except (ValueError, UnicodeDecodeError):
            return None
    return None


def clear_domain_credentials(domain: str) -> None:
    domain = (domain or "").strip().lower()
    with _credential_lock:
        _session_credentials.pop(domain, None)
    data = _store()._data
    credentials = data.get("credentials")
    if isinstance(credentials, dict):
        credentials.pop(domain, None)
        _store()._save()
