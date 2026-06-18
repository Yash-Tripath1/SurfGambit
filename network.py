import socket
import ssl
import urllib.parse
import gzip
from typing import Dict, Tuple, Optional

class NetworkResponse:
    def __init__(self, status_code: int, headers: Dict[str, str], body: bytes, url: str):
        self.status_code = status_code
        self.headers = headers
        self.body = body
        self.url = url
        self.text = self._decode_body()

    def _decode_body(self) -> str:
        # Detect encoding from headers
        content_type = self.headers.get("content-type", "").lower()
        encoding = "utf-8"
        if "charset=" in content_type:
            parts = content_type.split("charset=")
            if len(parts) > 1:
                encoding = parts[1].split(";")[0].strip()
        
        try:
            return self.body.decode(encoding)
        except Exception:
            try:
                return self.body.decode("utf-8", errors="replace")
            except Exception:
                return self.body.decode("latin-1", errors="replace")

def request(url: str, max_redirects: int = 5, headers_override: Optional[Dict[str, str]] = None) -> NetworkResponse:
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme: {parsed_url.scheme}")

    current_url = url
    for redirect_count in range(max_redirects):
        parsed = urllib.parse.urlparse(current_url)
        scheme = parsed.scheme
        host = parsed.hostname
        port = parsed.port
        path = parsed.path
        if parsed.query:
            path += "?" + parsed.query
        if not path:
            path = "/"

        if not host:
            raise ValueError(f"Invalid host in URL: {current_url}")

        if port is None:
            port = 443 if scheme == "https" else 80

        # Establish socket connection
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10.0)

        try:
            sock.connect((host, port))
            if scheme == "https":
                # For maximum reliability across Windows dev environments, we bypass
                # local CA certificate store validation. This ensures HTTPS sites
                # and images load correctly even if the OS certificate store is outdated.
                ctx = ssl._create_unverified_context()
                sock = ctx.wrap_socket(sock, server_hostname=host)
        except Exception as e:
            sock.close()
            raise RuntimeError(f"Connection failed to {host}:{port}. Error: {e}")

        # Construct raw HTTP/1.1 request
        headers = {
            "Host": f"{host}:{port}" if parsed.port else host,
            "User-Agent": "SurfGambit/1.1 (Python Custom Web Browser; Agent-Vibe)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip",
            "Connection": "close",
        }
        if headers_override:
            headers.update(headers_override)

        req_lines = [f"GET {path} HTTP/1.1"]
        for k, v in headers.items():
            req_lines.append(f"{k}: {v}")
        req_text = "\r\n".join(req_lines) + "\r\n\r\n"
        
        try:
            sock.sendall(req_text.encode("utf-8"))
        except Exception as e:
            sock.close()
            raise RuntimeError(f"Failed to send data: {e}")

        # Receive Response
        response_bytes = bytearray()
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_bytes.extend(chunk)
        except socket.timeout:
            pass
        except Exception as e:
            sock.close()
            raise RuntimeError(f"Error reading socket: {e}")
        finally:
            sock.close()

        # Parse Headers and Body
        header_end = response_bytes.find(b"\r\n\r\n")
        if header_end == -1:
            raise RuntimeError("Invalid HTTP response: Headers not delimited by \\r\\n\\r\\n")

        headers_raw = response_bytes[:header_end]
        body_raw = response_bytes[header_end + 4:]

        header_lines = headers_raw.decode("latin-1").split("\r\n")
        status_line = header_lines[0]
        status_parts = status_line.split(" ", 2)
        if len(status_parts) < 2:
            raise RuntimeError(f"Invalid HTTP status line: {status_line}")
        
        try:
            status_code = int(status_parts[1])
        except ValueError:
            raise RuntimeError(f"Non-integer HTTP status code: {status_parts[1]}")

        response_headers = {}
        for line in header_lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                response_headers[k.strip().lower()] = v.strip()

        # Handle Chunked Encoding
        if response_headers.get("transfer-encoding", "").lower() == "chunked":
            body_raw = _decode_chunked(body_raw)

        # Handle Content-Encoding (gzip)
        if response_headers.get("content-encoding", "").lower() == "gzip":
            try:
                body_raw = gzip.decompress(body_raw)
            except Exception as e:
                # Fallback if decompression fails
                pass

        # Handle Redirects
        if status_code in (301, 302, 303, 307, 308):
            redirect_url = response_headers.get("location")
            if redirect_url:
                # Support relative redirect URLs
                current_url = urllib.parse.urljoin(current_url, redirect_url)
                continue

        # No redirect or redirect failed, return response
        return NetworkResponse(status_code, response_headers, bytes(body_raw), current_url)

    raise RuntimeError("Too many redirects")

def _decode_chunked(body: bytearray) -> bytearray:
    decoded = bytearray()
    idx = 0
    while idx < len(body):
        # Read chunk size line until \r\n
        c_end = body.find(b"\r\n", idx)
        if c_end == -1:
            break
        chunk_size_str = body[idx:c_end].split(b";")[0].strip() # ignore chunk extensions
        if not chunk_size_str:
            break
        try:
            chunk_size = int(chunk_size_str, 16)
        except ValueError:
            break
        
        idx = c_end + 2
        if chunk_size == 0:
            break
        
        # Read chunk data
        if idx + chunk_size <= len(body):
            decoded.extend(body[idx : idx + chunk_size])
            idx += chunk_size + 2 # skip chunk data + \r\n
        else:
            decoded.extend(body[idx:])
            break
            
    return decoded

if __name__ == "__main__":
    # Test request
    print("Testing network request to http://example.com...")
    resp = request("http://example.com")
    print("Status:", resp.status_code)
    print("Headers:", resp.headers)
    print("Body preview:", resp.text[:200])
