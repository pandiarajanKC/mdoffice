"""Symmetric encryption for OAuth tokens at rest (master spec section 51 —
never expose OAuth refresh tokens; storing them in plaintext would violate
that the moment anyone reads the database directly).
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


@lru_cache(maxsize=1)
def _fernet(key: str) -> Fernet:
    return Fernet(key.encode("utf-8"))


def _get_fernet() -> Fernet:
    key = current_app.config["TOKEN_ENCRYPTION_KEY"]
    return _fernet(key)


def encrypt_token(raw_value: str) -> str:
    return _get_fernet().encrypt(raw_value.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted_value: str) -> str:
    try:
        return _get_fernet().decrypt(encrypted_value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Stored token could not be decrypted — TOKEN_ENCRYPTION_KEY may have changed.") from exc
