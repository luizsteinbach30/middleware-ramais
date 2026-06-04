"""Cifra portável por passphrase para export/import de ambientes.

Diferente do ``SecretBox`` (que deriva do ``APP_SECRET_KEY`` local, atrelado à
instalação), aqui a chave vem de uma **passphrase** escolhida pelo usuário, via
PBKDF2HMAC-SHA256 + salt aleatório → ``Fernet``. Isso torna o arquivo exportado
**portátil**: pode ser importado noutra instalação desde que se conheça a
passphrase. O envelope carrega o salt e o nº de iterações usados.
"""

from __future__ import annotations

import base64
import json
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_FORMAT = "mwr-env-export"
_VERSION = 1
_ITERATIONS = 200_000
_SALT_BYTES = 16


class ExportDecryptError(Exception):
    """Falha ao decifrar/validar um arquivo de export (passphrase ou formato)."""


def _derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def encrypt_export(data: bytes, passphrase: str) -> bytes:
    """Cifra ``data`` com a ``passphrase`` e devolve o envelope JSON em bytes."""
    if not passphrase:
        raise ValueError("passphrase obrigatória")
    salt = os.urandom(_SALT_BYTES)
    token = Fernet(_derive_key(passphrase, salt, _ITERATIONS)).encrypt(data)
    envelope = {
        "format": _FORMAT,
        "v": _VERSION,
        "kdf": {
            "salt": base64.b64encode(salt).decode("ascii"),
            "iterations": _ITERATIONS,
        },
        "ct": base64.b64encode(token).decode("ascii"),
    }
    return json.dumps(envelope).encode("utf-8")


def decrypt_export(blob: bytes, passphrase: str) -> bytes:
    """Decifra o envelope gerado por :func:`encrypt_export`.

    Levanta :class:`ExportDecryptError` se o arquivo for inválido ou a passphrase
    estiver errada.
    """
    try:
        envelope = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportDecryptError("arquivo inválido (não é um export válido)") from exc
    if not isinstance(envelope, dict) or envelope.get("format") != _FORMAT:
        raise ExportDecryptError("formato de arquivo desconhecido")
    try:
        salt = base64.b64decode(envelope["kdf"]["salt"])
        iterations = int(envelope["kdf"]["iterations"])
        token = base64.b64decode(envelope["ct"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExportDecryptError("envelope corrompido") from exc
    try:
        return Fernet(_derive_key(passphrase, salt, iterations)).decrypt(token)
    except InvalidToken as exc:
        raise ExportDecryptError("passphrase incorreta ou arquivo corrompido") from exc
