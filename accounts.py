import json
import os
import hashlib
import secrets
import base64
from typing import Dict, Tuple, Optional

ACCOUNTS_FILE = "accounts.json"
KEY_FILE = "master.key"

def load_accounts_data() -> dict:
    if not os.path.exists(ACCOUNTS_FILE):
        return {"users": {}, "credentials": {}}
    try:
        with open(ACCOUNTS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"users": {}, "credentials": {}}

def save_accounts_data(data: dict):
    try:
        with open(ACCOUNTS_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass

def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(16)
    hash_val = hashlib.sha256((password + salt).encode("utf-8")).hexdigest()
    return hash_val, salt

def register_user(username: str, password: str) -> Tuple[bool, str]:
    username = username.strip().lower()
    password = password.strip()
    if not username or not password:
        return False, "Username and password cannot be empty!"
        
    accounts = load_accounts_data()
    if username in accounts.get("users", {}):
        return False, "Username already exists!"
        
    hash_val, salt = hash_password(password)
    if "users" not in accounts:
        accounts["users"] = {}
    accounts["users"][username] = {
        "salt": salt,
        "hash": hash_val
    }
    save_accounts_data(accounts)
    return True, "Account registered successfully!"

def verify_user(username: str, password: str) -> bool:
    username = username.strip().lower()
    password = password.strip()
    if not username or not password:
        return False
        
    accounts = load_accounts_data()
    user_data = accounts.get("users", {}).get(username)
    if not user_data:
        return False
        
    stored_hash = user_data["hash"]
    stored_salt = user_data["salt"]
    
    computed_hash, _ = hash_password(password, stored_salt)
    return secrets.compare_digest(stored_hash, computed_hash)


# ============================================================
# Pure Standard-Library Secure Credentials Encryption (No Pip!)
# ============================================================
def _get_master_key() -> bytes:
    if not os.path.exists(KEY_FILE):
        key = secrets.token_bytes(32)
        with open(KEY_FILE, "wb") as f:
            f.write(key)
        return key
    try:
        with open(KEY_FILE, "rb") as f:
            return f.read()
    except Exception:
        return b"surfgambit_fallback_key_32_bytes_long"

def encrypt_data(data: str) -> str:
    key = _get_master_key()
    data_bytes = data.encode("utf-8")
    encrypted = bytearray()
    for i, b in enumerate(data_bytes):
        encrypted.append(b ^ key[i % len(key)])
    return base64.b64encode(encrypted).decode("utf-8")

def decrypt_data(enc_str: str) -> str:
    key = _get_master_key()
    try:
        data_bytes = base64.b64decode(enc_str.encode("utf-8"))
        decrypted = bytearray()
        for i, b in enumerate(data_bytes):
            decrypted.append(b ^ key[i % len(key)])
        return decrypted.decode("utf-8")
    except Exception:
        return ""


# ============================================================
# Domain Credentials Cache Managers
# ============================================================
def save_domain_credentials(domain: str, username: str, password: str):
    domain = domain.strip().lower()
    accounts = load_accounts_data()
    if "credentials" not in accounts:
        accounts["credentials"] = {}
        
    enc_pass = encrypt_data(password)
    accounts["credentials"][domain] = {
        "username": username,
        "password": enc_pass
    }
    save_accounts_data(accounts)

def get_domain_credentials(domain: str) -> Optional[Tuple[str, str]]:
    domain = domain.strip().lower()
    accounts = load_accounts_data()
    creds = accounts.get("credentials", {}).get(domain)
    if not creds:
        return None
    dec_pass = decrypt_data(creds["password"])
    return creds["username"], dec_pass

def clear_domain_credentials(domain: str):
    domain = domain.strip().lower()
    accounts = load_accounts_data()
    if "credentials" in accounts and domain in accounts["credentials"]:
        del accounts["credentials"][domain]
        save_accounts_data(accounts)
