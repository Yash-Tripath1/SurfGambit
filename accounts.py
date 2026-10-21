"""
accounts.py — compatibility surface for the legacy account helpers.

Implementation: `core.accounts` (PBKDF2-HMAC-SHA256, 600k iterations, 16-byte
salt, constant-time comparison, atomic 0600 writes). HTTP basic-auth
credentials are session-only; see `save_domain_credentials`.
"""

from __future__ import annotations

from core.accounts import (AccountStore, clear_domain_credentials,
                           get_domain_credentials, hash_password,
                           load_accounts_data, register_user,
                           save_accounts_data, save_domain_credentials,
                           verify_user)

__all__ = [
    "AccountStore", "load_accounts_data", "save_accounts_data",
    "hash_password", "register_user", "verify_user",
    "save_domain_credentials", "get_domain_credentials",
    "clear_domain_credentials",
]
