import json
import os
import hashlib
import secrets
from typing import Dict, Tuple, Optional

ACCOUNTS_FILE = "accounts.json"

def load_accounts_data() -> dict:
    if not os.path.exists(ACCOUNTS_FILE):
        return {"users": {}}
    try:
        with open(ACCOUNTS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"users": {}}

def save_accounts_data(data: dict):
    try:
        with open(ACCOUNTS_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass

def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    if not salt:
        # Secure random hex token salting
        salt = secrets.token_hex(16)
    # Append salt to password and compute SHA-256
    hash_val = hashlib.sha256((password + salt).encode("utf-8")).hexdigest()
    return hash_val, salt

def register_user(username: str, password: str) -> Tuple[bool, str]:
    username = username.strip().lower()
    password = password.strip()
    if not username or not password:
        return False, "Username and password cannot be empty!"
        
    accounts = load_accounts_data()
    if username in accounts["users"]:
        return False, "Username already exists!"
        
    hash_val, salt = hash_password(password)
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
    user_data = accounts["users"].get(username)
    if not user_data:
        return False
        
    stored_hash = user_data["hash"]
    stored_salt = user_data["salt"]
    
    computed_hash, _ = hash_password(password, stored_salt)
    return secrets.compare_digest(stored_hash, computed_hash) # timing-attack safe comparison!
