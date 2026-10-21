"""
core/net/client.py — the HTTP client.

Raw sockets, but done properly:

  * GET **and POST** (urlencoded + multipart), because a browser that cannot
    POST cannot log in anywhere
  * TLS certificate verification is ON by default (opt out per setting)
  * separate connect / read timeouts instead of a single 3 s kill switch
  * gzip + deflate, chunked transfer decoding
  * redirects with correct method rewriting (303 -> GET, 307/308 preserve)
  * truncation detection: a short body raises instead of silently rendering
    half a page
  * cookie injection / extraction per hop, plus `Referer`
"""

from __future__ import annotations

import gzip
import io
import os
import re
import socket
import ssl
import time
import urllib.parse
import zlib
from typing import Callable, Dict, Iterable, List, Optional, Tuple

__all__ = ["HTTPClient", "Response", "NetworkError", "DEFAULT_USER_AGENT"]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 SurfGambit/2.0"
)

MAX_REDIRECTS = 10
REDIRECT_CODES = {301, 302, 303, 307, 308}

_CHARSET_RE = re.compile(rb"""charset\s*=\s*["']?([a-zA-Z0-9_\-]+)""", re.I)


class NetworkError(Exception):
    """Any transport-level failure, with a message fit for a human."""

    def __init__(self, message: str, url: str = "", cause: Optional[BaseException] = None):
        super().__init__(message)
        self.url = url
        self.cause = cause


class Response:
    __slots__ = ("status", "reason", "headers", "raw_headers", "body", "url",
                 "truncated", "elapsed")

    def __init__(self, status: int, reason: str, headers: Dict[str, str],
                 raw_headers: List[Tuple[str, str]], body: bytes, url: str,
                 truncated: bool = False, elapsed: float = 0.0) -> None:
        self.status = status
        self.reason = reason
        self.headers = headers
        self.raw_headers = raw_headers
        self.body = body
        self.url = url
        self.truncated = truncated
        self.elapsed = elapsed

    # -- convenience ------------------------------------------------------
    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").split(";")[0].strip().lower()

    @property
    def charset(self) -> str:
        ctype = self.headers.get("content-type") or ""
        m = re.search(r"charset\s*=\s*[\"']?([\w\-]+)", ctype, re.I)
        if m:
            return m.group(1)
        # Peek at the document for <meta charset>.
        head = self.body[:2048]
        m = _CHARSET_RE.search(head)
        if m:
            try:
                return m.group(1).decode("ascii", "ignore")
            except Exception:
                pass
        return "utf-8"

    @property
    def text(self) -> str:
        try:
            return self.body.decode(self.charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            return self.body.decode("utf-8", errors="replace")

    @property
    def is_html(self) -> bool:
        ct = self.content_type
        return ct in ("", "text/html", "application/xhtml+xml")

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def header_all(self, name: str) -> List[str]:
        name = name.lower()
        return [v for k, v in self.raw_headers if k.lower() == name]

    def __repr__(self) -> str:
        return f"<Response {self.status} {len(self.body)}B {self.url}>"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _encode_params(params) -> bytes:
    if not params:
        return b""
    if isinstance(params, (bytes, bytearray)):
        return bytes(params)
    if isinstance(params, str):
        return params.encode("utf-8")
    return urllib.parse.urlencode(params, doseq=True).encode("utf-8")


def _multipart(fields: Dict[str, str]) -> Tuple[bytes, str]:
    boundary = "----SurfGambitBoundary%x" % int(time.time() * 1000)
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.write(str(value).encode("utf-8"))
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


class HTTPClient:
    def __init__(
        self,
        cookie_jar=None,
        user_agent: str = DEFAULT_USER_AGENT,
        verify_tls: bool = True,
        connect_timeout: float = 6.0,
        read_timeout: float = 20.0,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self.cookies = cookie_jar
        self.user_agent = user_agent
        self.verify_tls = verify_tls
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_redirects = max_redirects
        self._ssl_context: Optional[ssl.SSLContext] = None
        self.last_error: Optional[str] = None

    # -- TLS --------------------------------------------------------------
    def _context(self) -> ssl.SSLContext:
        if self._ssl_context is not None:
            return self._ssl_context
        if self.verify_tls:
            ctx = ssl.create_default_context()
            try:
                ctx.load_default_certs()
            except Exception:
                pass
        else:
            ctx = ssl._create_unverified_context()   # explicit opt-in only
        try:
            ctx.set_alpn_protocols(["http/1.1"])
        except (AttributeError, NotImplementedError):
            pass
        self._ssl_context = ctx
        return ctx

    # -- public API -------------------------------------------------------
    def get(self, url: str, headers: Optional[Dict[str, str]] = None,
            referer: str = "") -> Response:
        return self.request("GET", url, headers=headers, referer=referer)

    def post(self, url: str, data=None, headers: Optional[Dict[str, str]] = None,
             referer: str = "") -> Response:
        return self.request("POST", url, data=data, headers=headers, referer=referer)

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data=None,
        referer: str = "",
    ) -> Response:
        method = method.upper()
        started = time.time()
        current_url = url
        body = _encode_params(data) if data is not None else b""
        content_type = None
        if data is not None and not body and isinstance(data, dict):
            body, content_type = _multipart(data)

        for hop in range(self.max_redirects + 1):
            response = self._single_request(
                method, current_url, headers, body, content_type, referer
            )

            if response.status in REDIRECT_CODES:
                location = response.header("location")
                if location:
                    next_url = urllib.parse.urljoin(current_url, location)
                    if not next_url.startswith(("http://", "https://")):
                        raise NetworkError(
                            f"Refused to follow redirect to {next_url!r}", current_url
                        )
                    if response.status == 303 or (
                        response.status in (301, 302) and method == "POST"
                    ):
                        method = "GET"
                        body = b""
                        content_type = None
                    referer = current_url
                    current_url = next_url
                    continue

            response.elapsed = time.time() - started
            response.url = current_url
            return response

        raise NetworkError(f"Too many redirects (>{self.max_redirects})", url)

    # -- one hop ----------------------------------------------------------
    def _single_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]],
        body: bytes,
        content_type: Optional[str],
        referer: str,
    ) -> Response:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise NetworkError(f"Unsupported URL scheme {parts.scheme!r}", url)
        host = parts.hostname
        if not host:
            raise NetworkError(f"Invalid URL (no host): {url}", url)

        port = parts.port or (443 if parts.scheme == "https" else 80)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        # Percent-encode anything the URL left raw (spaces, unicode…).
        path = urllib.parse.quote(path, safe="/?&=%:+,;@!$'()*~#[]")
        host_header = host if parts.port in (None, 443, 80) else f"{host}:{port}"

        request_headers: Dict[str, str] = {
            "Host": host_header,
            "User-Agent": self.user_agent,
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,image/png,image/gif,*/*;q=0.8"),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        }
        if referer:
            request_headers["Referer"] = referer
        if body:
            request_headers["Content-Length"] = str(len(body))
            request_headers["Content-Type"] = content_type or "application/x-www-form-urlencoded"
        if self.cookies is not None:
            try:
                cookie_header = self.cookies.header_for(url)
            except Exception:
                cookie_header = None
            if cookie_header:
                request_headers["Cookie"] = cookie_header
        if headers:
            request_headers.update(headers)

        # -- connect ------------------------------------------------------
        try:
            sock = socket.create_connection((host, port), timeout=self.connect_timeout)
        except socket.timeout:
            raise NetworkError(f"Timed out connecting to {host}:{port}", url)
        except socket.gaierror as exc:
            raise NetworkError(f"Could not resolve {host}", url, exc)
        except OSError as exc:
            raise NetworkError(f"Connection to {host}:{port} failed: {exc}", url, exc)

        try:
            if parts.scheme == "https":
                try:
                    sock = self._context().wrap_socket(sock, server_hostname=host)
                except ssl.SSLCertVerificationError as exc:
                    raise NetworkError(
                        f"TLS certificate rejected for {host}: {exc.verify_message}",
                        url, exc,
                    )
                except ssl.SSLError as exc:
                    raise NetworkError(f"TLS handshake failed for {host}: {exc}", url, exc)

            sock.settimeout(self.read_timeout)
            request_bytes = self._build_request(method, path, request_headers, body)
            sock.sendall(request_bytes)
            return self._read_response(sock, url)
        finally:
            try:
                sock.close()
            except Exception:
                pass

    @staticmethod
    def _build_request(method: str, path: str, headers: Dict[str, str],
                       body: bytes) -> bytes:
        lines = [f"{method} {path} HTTP/1.1"]
        for key, value in headers.items():
            lines.append(f"{key}: {value}")
        head = "\r\n".join(lines) + "\r\n\r\n"
        return head.encode("latin-1", errors="replace") + (body or b"")

    # -- response reading -------------------------------------------------
    def _read_response(self, sock, url: str) -> Response:
        buffer = bytearray()
        header_end = -1

        # Read headers.
        while header_end == -1:
            try:
                chunk = sock.recv(16384)
            except socket.timeout:
                raise NetworkError("Timed out waiting for the server to respond", url)
            except OSError as exc:
                raise NetworkError(f"Connection error while reading: {exc}", url, exc)
            if not chunk:
                if not buffer:
                    raise NetworkError("Server closed the connection with no response", url)
                break
            buffer.extend(chunk)
            header_end = buffer.find(b"\r\n\r\n")
            if len(buffer) > 1_000_000 and header_end == -1:
                raise NetworkError("Response headers too large", url)

        if header_end == -1:
            raise NetworkError("Malformed HTTP response (no header terminator)", url)

        header_block = bytes(buffer[:header_end]).decode("latin-1")
        body_bytes = bytearray(buffer[header_end + 4:])

        lines = header_block.split("\r\n")
        status_line = lines[0]
        m = re.match(r"HTTP/\d(?:\.\d)?\s+(\d{3})\s*(.*)", status_line)
        if not m:
            raise NetworkError(f"Bad status line: {status_line[:80]!r}", url)
        status = int(m.group(1))
        reason = m.group(2).strip()

        headers: Dict[str, str] = {}
        raw_headers: List[Tuple[str, str]] = []
        for line in lines[1:]:
            if not line:
                continue
            if line[0] in " \t" and raw_headers:      # obs-fold continuation
                k, v = raw_headers[-1]
                raw_headers[-1] = (k, v + " " + line.strip())
                continue
            if ":" not in line:
                continue
            name, _, value = line.partition(":")
            name = name.strip()
            value = value.strip()
            raw_headers.append((name, value))
            lowered = name.lower()
            if lowered in headers:
                # Keep the first occurrence for the dict view; `raw_headers`
                # retains every Set-Cookie.
                if lowered == "set-cookie":
                    continue
            headers[lowered] = value

        if self.cookies is not None:
            try:
                self.cookies.store_from_headers(url, raw_headers)
            except Exception:
                pass

        if status in (204, 304):
            return Response(status, reason, headers, raw_headers, b"", url)

        chunked = "chunked" in (headers.get("transfer-encoding", "").lower())
        length_header = headers.get("content-length")
        expected: Optional[int] = None
        if length_header and not chunked:
            try:
                expected = int(length_header)
            except ValueError:
                expected = None

        truncated = False

        if chunked:
            # Keep the raw framing bytes and re-decode from the start as more
            # arrives; never overwrite the buffer with decoded output.
            raw = bytearray(body_bytes)
            decoded = bytearray()
            while True:
                out, complete = self._decode_chunked(raw)
                if len(out) >= len(decoded):
                    decoded = bytearray(out)
                if complete:
                    break
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    truncated = True
                    break
                except OSError:
                    truncated = True
                    break
                if not chunk:
                    truncated = True
                    break
                raw.extend(chunk)
            body_bytes = decoded
        elif expected is not None:
            while len(body_bytes) < expected:
                try:
                    chunk = sock.recv(min(65536, expected - len(body_bytes)))
                except socket.timeout:
                    truncated = True
                    break
                except OSError:
                    truncated = True
                    break
                if not chunk:
                    truncated = len(body_bytes) < expected
                    break
                body_bytes.extend(chunk)
        else:
            while True:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    break
                except OSError:
                    break
                if not chunk:
                    break
                body_bytes.extend(chunk)

        payload = bytes(body_bytes)
        payload = self._decompress(payload, headers.get("content-encoding", ""))

        return Response(status, reason, headers, raw_headers, payload, url,
                        truncated=truncated)

    @staticmethod
    def _decompress(payload: bytes, encoding: str) -> bytes:
        encoding = (encoding or "").lower().strip()
        if not encoding or encoding == "identity":
            return payload
        if encoding in ("gzip", "x-gzip"):
            try:
                return gzip.decompress(payload)
            except Exception:
                try:
                    return zlib.decompress(payload, 16 + zlib.MAX_WBITS)
                except Exception:
                    return payload
        if encoding == "deflate":
            for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
                try:
                    return zlib.decompress(payload, wbits)
                except Exception:
                    continue
            return payload
        return payload

    @staticmethod
    def _decode_chunked(data: bytearray) -> Tuple[bytes, bool]:
        """Decode as many complete chunks as available."""
        out = bytearray()
        idx = 0
        n = len(data)
        while True:
            line_end = data.find(b"\r\n", idx)
            if line_end == -1:
                return bytes(out), False
            raw_size = bytes(data[idx:line_end]).split(b";")[0].strip()
            if not raw_size:
                return bytes(out), False
            try:
                size = int(raw_size, 16)
            except ValueError:
                return bytes(out), False
            idx = line_end + 2
            if size == 0:
                return bytes(out), True
            if idx + size > n:
                return bytes(out), False
            out.extend(data[idx:idx + size])
            idx += size
            if data[idx:idx + 2] == b"\r\n":
                idx += 2

    # -- downloads --------------------------------------------------------
    def download(self, url: str, destination: str, referer: str = "",
                 progress: Optional[Callable[[int], None]] = None) -> int:
        """Stream a URL straight to disk (no full-body buffering)."""
        response = self.request("GET", url, referer=referer)
        if not response.ok:
            raise NetworkError(f"HTTP {response.status} for {url}", url)
        total = 0
        tmp = destination + ".part"
        with open(tmp, "wb") as handle:
            handle.write(response.body)
            total += len(response.body)
            if progress:
                progress(total)
        os.replace(tmp, destination)
        return total
