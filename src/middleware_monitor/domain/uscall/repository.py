"""Repository dos servidores USCall (multi-servidor desde a v2.7.0).

O token de cada servidor é cifrado com a ``SecretBox`` da aplicação (mesma
cifra dos segredos da ``app_config``). ``load_server_token`` devolve o
plaintext e é de uso interno de jobs/integrações — nunca exponha na API/UI.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.crypto import SecretBox
from middleware_monitor.core.models import Device, UscallServer
from middleware_monitor.settings import get_settings

__all__ = [
    "create_server",
    "delete_server",
    "get_server",
    "list_servers",
    "load_server_token",
    "update_server",
]


def _box() -> SecretBox:
    return SecretBox(get_settings().secret_key)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def list_servers(db: DBSession, *, enabled_only: bool = False) -> list[UscallServer]:
    stmt = select(UscallServer).order_by(UscallServer.id)
    if enabled_only:
        stmt = stmt.where(UscallServer.enabled.is_(True))
    return list(db.scalars(stmt).all())


def get_server(db: DBSession, server_id: int) -> UscallServer | None:
    return db.get(UscallServer, server_id)


def create_server(
    db: DBSession,
    *,
    nome: str,
    host: str,
    token_plain: str,
    verify_ssl: bool = True,
    enabled: bool = True,
) -> UscallServer:
    now = _now()
    srv = UscallServer(
        nome=nome.strip(),
        host=host.strip(),
        token=_box().encrypt(token_plain) if token_plain else "",
        verify_ssl=verify_ssl,
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )
    db.add(srv)
    db.flush()
    return srv


def update_server(
    db: DBSession,
    srv: UscallServer,
    *,
    nome: str | None = None,
    host: str | None = None,
    token_plain: str | None = None,
    verify_ssl: bool | None = None,
    enabled: bool | None = None,
) -> UscallServer:
    """Atualização parcial. ``token_plain=None`` mantém o token atual;
    string não-vazia re-cifra; ``""`` limpa."""
    if nome is not None:
        srv.nome = nome.strip()
    if host is not None:
        srv.host = host.strip()
    if token_plain is not None:
        srv.token = _box().encrypt(token_plain) if token_plain else ""
    if verify_ssl is not None:
        srv.verify_ssl = verify_ssl
    if enabled is not None:
        srv.enabled = enabled
    srv.updated_at = _now()
    db.flush()
    return srv


def delete_server(db: DBSession, server_id: int) -> bool:
    srv = db.get(UscallServer, server_id)
    if srv is None:
        return False
    # Desvincula explicitamente (além do ON DELETE SET NULL do FK) para não
    # depender do pragma foreign_keys da conexão.
    db.execute(
        update(Device)
        .where(Device.uscall_server_id == server_id)
        .values(uscall_server_id=None)
    )
    db.delete(srv)
    db.flush()
    return True


def load_server_token(srv: UscallServer) -> str | None:
    """Plaintext do token do servidor (ou ``None`` se não configurado)."""
    if not srv.token:
        return None
    return _box().decrypt(srv.token)
