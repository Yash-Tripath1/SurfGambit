import json
import os
import time
import urllib.parse
from typing import Dict, List, Tuple, Optional

COOKIES_FILE = "cookies.json"

class Cookie:
    def __init__(self, name: str, value: str, domain: str, path: str = "/", expires: Optional[float] = None, secure: bool = False, httponly: bool = False):
        self.name = name
        self.value = value
        self.domain = domain.lower()
        self.path = path
        self.expires = expires  # Epoch timestamp
        self.secure = secure
        self.httponly = httponly

    def is_expired(self) -> bool:
        if self.expires is not None:
            return time.time() > self.expires
        return False

    def matches_url(self, url: str) -> bool:
        if self.is_expired():
            return False
            
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        if not host:
            return False
        host = host.lower()
        
        # Domain Matching (supports subdomains, e.g. .github.com matches github.com and api.github.com)
        cookie_domain = self.domain
        if cookie_domain.startswith("."):
            if not (host.endswith(cookie_domain) or host == cookie_domain[1:]):
                return False
        else:
            if host != cookie_domain:
                return False
                
        # Path Matching (cookie path must be a prefix of URL path)
        req_path = parsed.path or "/"
        if not req_path.startswith(self.path):
            return False
            
        # Secure Matching
        if self.secure and parsed.scheme != "https":
            return False
            
        return True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "expires": self.expires,
            "secure": self.secure,
            "httponly": self.httponly
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'Cookie':
        return cls(
            name=d["name"],
            value=d["value"],
            domain=d["domain"],
            path=d.get("path", "/"),
            expires=d.get("expires"),
            secure=d.get("secure", False),
            httponly=d.get("httponly", False)
        )


class CookieJar:
    def __init__(self, filepath: str = COOKIES_FILE):
        self.filepath = filepath
        self.cookies: List[Cookie] = []
        self.load()

    def load(self):
        if not os.path.exists(self.filepath):
            self.cookies = []
            return
        try:
            with open(self.filepath, "r") as f:
                data = json.load(f)
                self.cookies = [Cookie.from_dict(d) for d in data]
        except Exception:
            self.cookies = []

    def save(self):
        # Clean expired cookies before saving
        self.cookies = [c for c in self.cookies if not c.is_expired()]
        try:
            with open(self.filepath, "w") as f:
                json.dump([c.to_dict() for c in self.cookies], f, indent=4)
        except Exception:
            pass

    def extract_cookies(self, response_url: str, header_tuples: List[Tuple[str, str]]):
        parsed_url = urllib.parse.urlparse(response_url)
        default_domain = parsed_url.hostname or ""
        
        has_new = False
        for name, value in header_tuples:
            if name.lower() == "set-cookie":
                cookie = self._parse_set_cookie(value, default_domain)
                if cookie:
                    # Remove any existing cookie with the same name, domain, and path
                    self.cookies = [c for c in self.cookies if not (c.name == cookie.name and c.domain == cookie.domain and c.path == cookie.path)]
                    self.cookies.append(cookie)
                    has_new = True
                    
        if has_new:
            self.save()

    def get_cookie_header(self, request_url: str) -> Optional[str]:
        # Filter and retrieve active cookies
        matching = [c for c in self.cookies if c.matches_url(request_url)]
        if not matching:
            return None
            
        # Format string: name1=value1; name2=value2
        return "; ".join(f"{c.name}={c.value}" for c in matching)

    def _parse_set_cookie(self, set_cookie_val: str, default_domain: str) -> Optional[Cookie]:
        parts = [p.strip() for p in set_cookie_val.split(";")]
        if not parts or "=" not in parts[0]:
            return None
            
        # First part is name=value
        name_val = parts[0].split("=", 1)
        name = name_val[0].strip()
        value = name_val[1].strip()
        
        # Default attributes
        domain = default_domain
        path = "/"
        expires_timestamp = None
        secure = False
        httponly = False
        
        for part in parts[1:]:
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip().lower()
                v = v.strip()
                if k == "domain":
                    domain = v
                elif k == "path":
                    path = v
                elif k == "max-age":
                    try:
                        expires_timestamp = time.time() + float(v)
                    except ValueError:
                        pass
                elif k == "expires":
                    # Simple parse for HTTP Date (e.g., Wed, 09 Jun 2021 10:18:14 GMT)
                    # We can use standard library to convert HTTP date to timestamp
                    import email.utils
                    t_tuple = email.utils.parsedate_to_datetime(v)
                    if t_tuple:
                        expires_timestamp = t_tuple.timestamp()
            else:
                k = part.strip().lower()
                if k == "secure":
                    secure = True
                elif k == "httponly":
                    httponly = True
                    
        return Cookie(name=name, value=value, domain=domain, path=path, expires=expires_timestamp, secure=secure, httponly=httponly)


# Global Shared Cookie Jar
jar = CookieJar()

if __name__ == "__main__":
    print("Testing custom cookie parser...")
    test_headers = [
        ("Set-Cookie", "session_id=hacker_vibes_999; Domain=.github.com; Path=/; Secure; HttpOnly"),
        ("Set-Cookie", "user_lang=en-US; Max-Age=3600")
    ]
    jar.extract_cookies("https://github.com/login", test_headers)
    
    print("Cookies inside jar:")
    for c in jar.cookies:
        print(f" - {c.name}={c.value} (Domain: {c.domain}, Path: {c.path})")
        
    print("\nRetrieving cookies for https://api.github.com/user:")
    hdr = jar.get_cookie_header("https://api.github.com/user")
    print("Cookie Header:", hdr)
