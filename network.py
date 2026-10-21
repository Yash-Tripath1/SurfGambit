"""
network.py — compatibility surface for the legacy transport.

The transport is now `core.net.client.HTTPClient`: verified TLS by default,
GET and POST, gzip/deflate, chunked transfer decoding, redirects with
method rewriting, cookie and Referer handling per hop.

`network.socket` remains importable (and patchable) because code and tests
reach for it; the underlying client uses the same `socket` module, so socket
fakes still intercept requests.
"""

from __future__ import annotations

import json
import socket  # noqa: F401  (re-exported: callers patch network.socket.socket)
from typing import Dict, Optional

from core.net.client import HTTPClient, NetworkError, Response
from core.net.cookies import CookieJar
from core.search import USER_AGENTS
from core.storage import data_path

__all__ = ["NetworkResponse", "NetworkError", "request", "request_post", "socket",
           "HTTPClient"]


class NetworkResponse:
    """legacy-shaped view over a `core.net.client.Response`."""

    def __init__(self, response: Response) -> None:
        self._response = response
        self.status_code = response.status
        self.headers = response.headers
        self.body = response.body
        self.url = response.url
        self.raw_headers = list(response.headers.items())
        self.text = response.text

    @property
    def ok(self) -> bool:
        return self._response.ok

    def header(self, name: str, default: str = "") -> str:
        return self._response.header(name, default)


def _settings() -> Dict[str, object]:
    """legacy read settings.json from the working directory; keep that, then XDG."""
    for path in ("settings.json", data_path("settings.json")):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            continue
    return {}


def _user_agent(settings: Dict[str, object]) -> str:
    explicit = settings.get("user_agent")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    name = str(settings.get("user_agent_name", "Chrome"))
    return USER_AGENTS.get(name, USER_AGENTS["Chrome"])


def _client(settings: Dict[str, object]) -> HTTPClient:
    return HTTPClient(
        cookie_jar=CookieJar(data_path("cookies.json")),
        user_agent=_user_agent(settings),
        verify_tls=str(settings.get("verify_tls", "on")).lower() not in ("off", "false", "0"),
    )


def request(url: str, max_redirects: int = 5,
            headers_override: Optional[Dict[str, str]] = None) -> NetworkResponse:
    settings = _settings()
    client = _client(settings)
    client.max_redirects = max_redirects
    response = client.get(url, headers=headers_override)
    jar = client.cookies
    if jar is not None and getattr(jar, "path", None):
        jar.save()
    return NetworkResponse(response)


def request_post(url: str, data=None,
                 headers_override: Optional[Dict[str, str]] = None) -> NetworkResponse:
    settings = _settings()
    client = _client(settings)
    response = client.post(url, data=data, headers=headers_override)
    jar = client.cookies
    if jar is not None and getattr(jar, "path", None):
        jar.save()
    return NetworkResponse(response)
