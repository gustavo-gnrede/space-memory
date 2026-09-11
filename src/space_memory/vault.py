"""Encrypted secret vault primitives.

Secrets (passwords, tokens) are encrypted with AES-256-GCM. The ciphertext is
bound to its (space, key) identity via associated data so a blob cannot be
re-labelled or moved across spaces without failing to decrypt.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def key_from_hex(value: str) -> bytes:
    try:
        raw = bytes.fromhex(value.strip())
    except ValueError as exc:
        raise ValueError("vault key must be a hex string") from exc
    if len(raw) != 32:
        raise ValueError("vault key must be 32 bytes (64 hex chars)")
    return raw


def _aad(space_id: str, key_name: str) -> bytes:
    return f"space:{space_id}:key:{key_name}".encode()


def encrypt(key: bytes, space_id: str, key_name: str, plaintext: str) -> str:
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), _aad(space_id, key_name))
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt(key: bytes, space_id: str, key_name: str, blob: str) -> str:
    raw = base64.b64decode(blob)
    nonce, ciphertext = raw[:12], raw[12:]
    plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(space_id, key_name))
    return plaintext.decode("utf-8")
