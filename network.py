import socket
import ssl
from urllib.parse import urlparse


def parse_url(url):
    """Parse URL into scheme, host, port, path."""
    parsed = urlparse(url)
    scheme = parsed.scheme
    host = parsed.netloc
    port = 443 if scheme == "https" else 80
    path = parsed.path if parsed.path else "/"
    return scheme, host, port, path


def decode_chunked(body):
    """Decode chunked transfer encoding."""
    decoded = b""
    body = body.encode() if isinstance(body, str) else body
    while True:
        if b"\r\n" not in body:
            break
        chunk_header = body[:body.find(b"\r\n") + 2]
        body = body[len(chunk_header):]
        try:
            chunk_size = int(chunk_header.split(b"\r\n")[0], 16)
        except:
            break
        if chunk_size == 0:
            break
        if len(body) < chunk_size + 2:
            break
        chunk_data = body[:chunk_size]
        body = body[chunk_size + 2:]
        decoded += chunk_data
    return decoded.decode("utf-8", errors="ignore")


def split_headers_body(response):
    """Split headers/body and decode chunked if needed."""
    header_end = response.find("\r\n\r\n")
    headers = response[:header_end]
    body = response[header_end + 4:]
    if "Transfer-Encoding: chunked" in headers:
        body = decode_chunked(body)
    return headers, body


def fetch_url(url, max_redirects=5):
    """Fetch URL with redirect support."""
    if max_redirects <= 0:
        raise Exception("Too many redirects")
    scheme, host, port, path = parse_url(url)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    if scheme == "https":
        context = ssl.create_default_context()
        sock = context.wrap_socket(sock, server_hostname=host)
    sock.connect((host, port))
    request = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
    sock.sendall(request.encode())
    response = b""
    while True:
        data = sock.recv(4096)
        if not data:
            break
        response += data
    sock.close()
    response = response.decode("utf-8", errors="ignore")
    headers, body = split_headers_body(response)
    if " 301 " in headers or " 302 " in headers:
        location_header = [h for h in headers.split("\r\n") if "Location:" in h]
        if location_header:
            location = location_header[0].split(": ", 1)[1].strip()
            if location.startswith("/"):
                location = f"{scheme}://{host}{location}"
            return fetch_url(location, max_redirects - 1)
    return headers, body


if __name__ == "__main__":
    url = "https://example.com"
    headers, body = fetch_url(url)
    print("=== HEADERS ===")
    print(headers)
    print("\n=== BODY ===")
    print(body[:500])