"""Symmetric encryption for at-rest secrets (USCall token, webhook tokens).

The encryption key is derived from ``APP_SECRET_KEY`` via HKDF so that rotating
the secret only affects what was encrypted with the previous derivation. Each
sensitive value is stored as a base64-encoded ``Fernet`` token.
"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _derive_key(secret_key: str, purpose: bytes) -> bytes:
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=purpose,
    ).derive(secret_key.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


class SecretBox:
    """Encrypt / decrypt small strings (tokens, passwords) at rest."""

    def __init__(self, secret_key: str) -> None:
        if not secret_key or len(secret_key) < 16:
            raise ValueError("secret_key must be at least 16 characters")
        self._fernet = Fernet(_derive_key(secret_key, b"app_config_secret_v1"))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("invalid_secret") from exc
