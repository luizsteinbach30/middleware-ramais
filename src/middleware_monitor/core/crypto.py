"""Symmetric encryption for at-rest secrets (USCall token, broker password,
SIP and device passwords).

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


#: Marks a stored value as ciphertext. Columns that gained encryption after
#: data already existed (line SIP passwords, environment web passwords) hold a
#: mix of both for as long as it takes a write to touch each row, and guessing
#: by "does it look like a Fernet token?" is the kind of guess that eventually
#: hands a corrupted ciphertext to a phone as if it were a password.
ENCRYPTED_PREFIX = "enc:v1:"


def is_encrypted(stored: str) -> bool:
    return stored.startswith(ENCRYPTED_PREFIX)


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

    def encrypt_field(self, plaintext: str) -> str:
        """Ciphertext with the marker. Empty stays empty — an absent password
        is not a secret, and marking it would only make the column unreadable."""
        if not plaintext:
            return ""
        return ENCRYPTED_PREFIX + self.encrypt(plaintext)

    def decrypt_field(self, stored: str) -> str:
        """Plaintext from a column that may still hold legacy cleartext.

        Unmarked means legacy and is returned as-is. Marked means it MUST
        decrypt: failing there is a real error (wrong ``APP_SECRET_KEY``, or a
        corrupted row) and raises, instead of quietly returning garbage.
        """
        if not stored:
            return ""
        if not is_encrypted(stored):
            return stored
        return self.decrypt(stored[len(ENCRYPTED_PREFIX) :])


def open_box(secret_key: str) -> SecretBox | None:
    """The box, or ``None`` when the key cannot encrypt anything.

    An install still running the default ``change-me`` has no usable key. That
    must not stop it from reading and writing what it already has — the upgrade
    that encrypts at rest cannot be the upgrade that bricks those installs. The
    caller keeps working in cleartext and says so; see ``segredos_em_claro``.
    """
    try:
        return SecretBox(secret_key)
    except ValueError:
        return None
