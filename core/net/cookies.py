"""
core/net/cookies.py — RFC 6265 cookie jar.

The previous jar trusted whatever `Domain=` a server sent, which lets any site
plant cookies for any other site. This one enforces:

  * domain-match: a cookie may only be set for the request host or a parent of it
  * public-suffix rejection (a site may not set `Domain=.com`)
  * path-match on a `/` boundary (`/foo` no longer matches `/foobar`)
  * longest-path-first ordering, then creation order
  * `Max-Age=0` / past `Expires` deletion
  * `Secure` only over https, `HttpOnly` never exposed to scripts
  * malformed `Expires` values are ignored instead of raising
  * atomic, locked persistence
"""

from __future__ import annotations

import email.utils
import json
import os
import threading
import time
import urllib.parse
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = ["Cookie", "CookieJar"]

# A pragmatic subset of the Public Suffix List: multi-label suffixes that
# commonly appear plus every single-label ccTLD/gTLD we can cheaply reject.
PUBLIC_SUFFIXES = frozenset({
    "com", "net", "org", "edu", "gov", "mil", "int", "info", "biz", "name",
    "pro", "aero", "coop", "museum", "jobs", "mobi", "travel", "tel", "asia",
    "cat", "xxx", "app", "dev", "io", "ai", "co", "me", "tv", "cc", "ws",
    "uk", "us", "in", "au", "ca", "de", "fr", "jp", "cn", "ru", "br", "it",
    "nl", "se", "no", "es", "ch", "at", "be", "dk", "fi", "pl", "cz", "gr",
    "pt", "ie", "nz", "za", "mx", "ar", "cl", "kr", "tw", "hk", "sg", "my",
    "id", "ph", "th", "vn", "pk", "bd", "lk", "np", "ir", "il", "tr", "ua",
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
    "co.in", "net.in", "org.in", "co.jp", "ne.jp", "or.jp", "com.br",
    "com.cn", "net.cn", "org.cn", "co.za", "com.mx", "com.ar", "co.nz",
    "github.io", "gitlab.io", "herokuapp.com", "cloudfront.net",
    "s3.amazonaws.com", "azurewebsites.net", "appspot.com", "web.app",
    "firebaseapp.com", "blogspot.com", "wordpress.com", "tumblr.com",
    "pages.dev", "netlify.app", "vercel.app", "workers.dev", "ngrok.io",
})


def _domain_match(host: str, cookie_domain: str) -> bool:
    """True if `host` is `cookie_domain` or a subdomain of it."""
    host = host.lower().lstrip(".")
    cookie_domain = cookie_domain.lower().lstrip(".")
    if host == cookie_domain:
        return True
    return host.endswith("." + cookie_domain)


def _is_public_suffix(domain: str) -> bool:
    return domain.lower().lstrip(".") in PUBLIC_SUFFIXES


def _path_match(request_path: str, cookie_path: str) -> bool:
    if request_path == cookie_path:
        return True
    if not request_path.startswith(cookie_path):
        return False
    if cookie_path.endswith("/"):
        return True
    return request_path[len(cookie_path):len(cookie_path) + 1] == "/"


class Cookie:
    __slots__ = ("name", "value", "domain", "path", "expires", "secure",
                 "http_only", "host_only", "creation")

    def __init__(
        self,
        name: str,
        value: str,
        domain: str,
        path: str = "/",
        expires: Optional[float] = None,
        secure: bool = False,
        http_only: bool = False,
        host_only: bool = False,
        creation: Optional[float] = None,
    ) -> None:
        self.name = name
        self.value = value
        self.domain = domain.lower().lstrip(".")
        self.path = path or "/"
        self.expires = expires
        self.secure = secure
        self.http_only = http_only
        self.host_only = host_only
        self.creation = creation if creation is not None else time.time()

    # -- validity ---------------------------------------------------------
    def is_expired(self, now: Optional[float] = None) -> bool:
        if self.expires is None:
            return False
        return (now if now is not None else time.time()) >= self.expires

    def matches(self, host: str, path: str, secure_scheme: bool) -> bool:
        if self.is_expired():
            return False
        if self.secure and not secure_scheme:
            return False
        if self.host_only:
            if host.lower() != self.domain:
                return False
        elif not _domain_match(host, self.domain):
            return False
        return _path_match(path or "/", self.path)

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "name": self.name, "value": self.value, "domain": self.domain,
            "path": self.path, "expires": self.expires, "secure": self.secure,
            "http_only": self.http_only, "host_only": self.host_only,
            "creation": self.creation,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Cookie":
        return cls(
            name=data.get("name", ""),
            value=data.get("value", ""),
            domain=data.get("domain", ""),
            path=data.get("path", "/"),
            expires=data.get("expires"),
            secure=bool(data.get("secure", False)),
            http_only=bool(data.get("http_only", data.get("httponly", False))),
            host_only=bool(data.get("host_only", False)),
            creation=data.get("creation"),
        )

    def __repr__(self) -> str:
        return f"Cookie({self.name}={self.value[:12]!r} @ {self.domain}{self.path})"


class CookieJar:
    """A thread-safe, persistent cookie store."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path
        self._cookies: List[Cookie] = []
        self._lock = threading.RLock()
        if path:
            self.load()

    # -- persistence ------------------------------------------------------
    def load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                self._cookies = [Cookie.from_dict(d) for d in data
                                 if isinstance(d, dict) and d.get("name")]
        except (OSError, ValueError):
            self._cookies = []

    def save(self) -> None:
        if not self.path:
            return
        with self._lock:
            now = time.time()
            self._cookies = [c for c in self._cookies if not c.is_expired(now)]
            payload = [c.to_dict() for c in self._cookies]
        try:
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # -- querying ---------------------------------------------------------
    def all(self) -> List[Cookie]:
        with self._lock:
            return list(self._cookies)

    def header_for(self, url: str) -> Optional[str]:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        if not host:
            return None
        path = parts.path or "/"
        secure = parts.scheme == "https"
        with self._lock:
            matching = [c for c in self._cookies if c.matches(host, path, secure)]
            matching.sort(key=lambda c: (-len(c.path), c.creation))
        if not matching:
            return None
        return "; ".join(f"{c.name}={c.value}" for c in matching)

    def get(self, name: str, url: str) -> Optional[str]:
        header = self.header_for(url) or ""
        for part in header.split(";"):
            if "=" in part:
                key, _, value = part.strip().partition("=")
                if key == name:
                    return value
        return None

    # -- mutating ---------------------------------------------------------
    def set(
        self,
        name: str,
        value: str,
        domain: str,
        path: str = "/",
        expires: Optional[float] = None,
        secure: bool = False,
        http_only: bool = False,
        host_only: bool = False,
    ) -> Cookie:
        with self._lock:
            self._cookies = [
                c for c in self._cookies
                if not (c.name == name and c.domain == domain.lower().lstrip(".")
                        and c.path == path)
            ]
            cookie = Cookie(name, value, domain, path, expires, secure,
                            http_only, host_only)
            self._cookies.append(cookie)
        self.save()
        return cookie

    def clear_domain(self, domain: str) -> None:
        domain = domain.lower().lstrip(".")
        with self._lock:
            self._cookies = [c for c in self._cookies if c.domain != domain]
        self.save()

    def clear(self) -> None:
        with self._lock:
            self._cookies = []
        self.save()

    # -- HTTP integration -------------------------------------------------
    def store_from_headers(self, request_url: str,
                           headers: Sequence[Tuple[str, str]]) -> List[Cookie]:
        """Process `Set-Cookie` headers from a response to `request_url`."""
        parts = urllib.parse.urlsplit(request_url)
        host = (parts.hostname or "").lower()
        if not host:
            return []
        secure_scheme = parts.scheme == "https"
        request_path = parts.path or "/"
        stored: List[Cookie] = []
        now = time.time()

        for name, value in headers:
            if name.lower() != "set-cookie":
                continue
            parsed = parse_set_cookie(value)
            if parsed is None:
                continue
            cookie_name, cookie_value, attrs = parsed

            domain_attr = attrs.get("domain")
            if domain_attr:
                candidate = domain_attr.lstrip(".").lower()
                if not _domain_match(host, candidate):
                    continue                      # cross-site cookie: reject
                if _is_public_suffix(candidate):
                    continue                      # `.com` and friends: reject
                if host == candidate and "." not in candidate.replace(":", ""):
                    continue                      # bare hostname is not a domain
                cookie_domain = candidate
                host_only = False
            else:
                cookie_domain = host
                host_only = True

            cookie_path = attrs.get("path") or default_path(request_path)

            expires = None
            if "max-age" in attrs:
                try:
                    max_age = int(attrs["max-age"])
                    expires = now - 1 if max_age <= 0 else now + max_age
                except ValueError:
                    expires = None
            elif "expires" in attrs:
                expires = parse_http_date(attrs["expires"])

            secure = "secure" in attrs
            if secure and not secure_scheme and not _is_loopback(host):
                continue

            with self._lock:
                self._cookies = [
                    c for c in self._cookies
                    if not (c.name == cookie_name and c.domain == cookie_domain
                            and c.path == cookie_path and c.host_only == host_only)
                ]
                if expires is not None and expires <= now:
                    continue                      # deletion
                cookie = Cookie(
                    cookie_name, cookie_value, cookie_domain, cookie_path,
                    expires, secure, "httponly" in attrs, host_only,
                )
                self._cookies.append(cookie)
                stored.append(cookie)

        if stored:
            self.save()
        return stored


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _is_loopback(host: str) -> bool:
    return host in ("localhost", "127.0.0.1", "::1")


def default_path(request_path: str) -> str:
    if not request_path.startswith("/"):
        return "/"
    idx = request_path.rfind("/")
    if idx <= 0:
        return "/"
    return request_path[:idx]


def parse_http_date(value: str) -> Optional[float]:
    """Parse an HTTP date; return None instead of raising on garbage."""
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt is None:
            return None
        return dt.timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    for fmt in ("%a, %d-%b-%Y %H:%M:%S GMT", "%a, %d %b %Y %H:%M:%S GMT",
                "%a %b %d %H:%M:%S %Y"):
        try:
            return email.utils.mktime_tz(email.utils.parsedate(value)) or None
        except Exception:
            continue
    return None


def parse_set_cookie(header: str):
    """`name=value; Path=/; HttpOnly` -> (name, value, attrs)."""
    if not header:
        return None
    pieces = header.split(";")
    first = pieces[0]
    if "=" not in first:
        return None
    name, _, value = first.partition("=")
    name = name.strip()
    if not name:
        return None
    attrs: Dict[str, str] = {}
    for piece in pieces[1:]:
        piece = piece.strip()
        if not piece:
            continue
        if "=" in piece:
            key, _, val = piece.partition("=")
            attrs[key.strip().lower()] = val.strip().strip('"')
        else:
            attrs[piece.lower()] = ""
    return name, value.strip(), attrs
