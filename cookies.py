"""
cookies.py — compatibility surface for the v1 cookie API.

The RFC 6265 implementation lives in `core.net.cookies`: domain-match,
public-suffix rejection, path-boundary matching, Max-Age=0 deletion, secure
and http-only flags, atomic persistence. This module keeps the old names
(`CookieJar(filepath=...)`, `extract_cookies`, `get_cookie_header`) working.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from core.net.cookies import Cookie, CookieJar as _CoreCookieJar

__all__ = ["Cookie", "CookieJar"]


class CookieJar(_CoreCookieJar):
    def __init__(self, filepath: Optional[str] = None) -> None:
        super().__init__(filepath)

    # -- v1 method names --------------------------------------------------
    def extract_cookies(self, response_url: str,
                        header_tuples: List[Tuple[str, str]]) -> None:
        self.store_from_headers(response_url, header_tuples)

    def get_cookie_header(self, request_url: str) -> Optional[str]:
        return self.header_for(request_url)
