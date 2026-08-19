"""Repository do broker MQTT, do ledger de mensagens e da prova de cobertura.

Segue o padrão do repo: funções módulo-level recebendo ``db`` como primeiro
argumento, ``flush`` aqui e ``commit`` em quem chama. A senha do broker é
cifrada com a ``SecretBox`` da aplicação e só sai em texto claro para o
coletor — nunca para a API.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.crypto import SecretBox
from middleware_monitor.core.models import MqttBroker, MqttConnectionEvent, MqttMessage
from middleware_monitor.settings import get_settings

__all__ = [
    "broker_topics",
    "count_messages",
    "create_broker",
    "default_client_id",
    "delete_broker",
    "get_broker",
    "insert_connection_events",
    "insert_messages",
    "last_connection_event_before",
    "last_message_at",
    "list_brokers",
    "list_connection_events",
    "load_broker_password",
    "payload_bytes_total",
    "purge_connection_events",
    "purge_messages_by_age",
    "purge_messages_by_size",
    "search_messages",
    "set_pinned",
]


def _box() -> SecretBox:
    return SecretBox(get_settings().secret_key)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _rowcount(db: DBSession, stmt: Any) -> int:
    return cast("CursorResult[Any]", db.execute(stmt)).rowcount


# ── brokers ──────────────────────────────────────────────────────────────────


def default_client_id(prefix: str = "") -> str:
    """Identificador estável e único desta instalação no broker.

    Fica gravado na linha do broker: é ele que sustenta a sessão durável (o
    broker guarda as mensagens enquanto o serviço está parado). Trocar de
    ``client_id`` a cada boot jogaria fora essa garantia.
    """
    base = "".join(c for c in prefix.strip().lower() if c.isalnum() or c in "-_")[:24]
    sufixo = secrets.token_hex(3)
    return f"mwmonitor-{base}-{sufixo}" if base else f"mwmonitor-{sufixo}"


def list_brokers(db: DBSession, *, enabled_only: bool = False) -> list[MqttBroker]:
    stmt = select(MqttBroker).order_by(MqttBroker.id)
    if enabled_only:
        stmt = stmt.where(MqttBroker.enabled.is_(True))
    return list(db.scalars(stmt).all())


def get_broker(db: DBSession, broker_id: int) -> MqttBroker | None:
    return db.get(MqttBroker, broker_id)


def create_broker(
    db: DBSession,
    *,
    nome: str,
    address_input: str,
    host: str,
    port: int,
    transport: str = "tcp",
    tls: bool = False,
    ws_path: str | None = None,
    username: str = "",
    password_plain: str = "",
    tls_verify: bool = True,
    tls_fingerprint: str | None = None,
    topics: Sequence[str] = (),
    qos: int = 1,
    clean_session: bool = False,
    client_id: str = "",
    max_payload_kb: int = 0,
    enabled: bool = True,
) -> MqttBroker:
    now = _now()
    broker = MqttBroker(
        nome=nome.strip(),
        address_input=address_input.strip(),
        host=host.strip(),
        port=port,
        transport=transport,
        tls=tls,
        ws_path=ws_path,
        username=username.strip(),
        password=_box().encrypt(password_plain) if password_plain else "",
        tls_verify=tls_verify,
        tls_fingerprint=tls_fingerprint or None,
        topics=json.dumps(list(topics)),
        qos=qos,
        clean_session=clean_session,
        client_id=client_id or default_client_id(nome),
        max_payload_kb=max_payload_kb,
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )
    db.add(broker)
    db.flush()
    return broker


def update_broker(
    db: DBSession,
    broker: MqttBroker,
    *,
    nome: str | None = None,
    address_input: str | None = None,
    host: str | None = None,
    port: int | None = None,
    transport: str | None = None,
    tls: bool | None = None,
    ws_path: str | None = None,
    username: str | None = None,
    password_plain: str | None = None,
    tls_verify: bool | None = None,
    tls_fingerprint: str | None = None,
    topics: Sequence[str] | None = None,
    qos: int | None = None,
    max_payload_kb: int | None = None,
    enabled: bool | None = None,
) -> MqttBroker:
    """Atualização parcial. ``password_plain=None`` mantém a senha atual;
    string não-vazia re-cifra; ``""`` limpa (mesma semântica do token USCall)."""
    if nome is not None:
        broker.nome = nome.strip()
    if address_input is not None:
        broker.address_input = address_input.strip()
    if host is not None:
        broker.host = host.strip()
    if port is not None:
        broker.port = port
    if transport is not None:
        broker.transport = transport
    if tls is not None:
        broker.tls = tls
    if ws_path is not None:
        broker.ws_path = ws_path or None
    if username is not None:
        broker.username = username.strip()
    if password_plain is not None:
        broker.password = _box().encrypt(password_plain) if password_plain else ""
    if tls_verify is not None:
        broker.tls_verify = tls_verify
    if tls_fingerprint is not None:
        broker.tls_fingerprint = tls_fingerprint or None
    if topics is not None:
        broker.topics = json.dumps(list(topics))
    if qos is not None:
        broker.qos = qos
    if max_payload_kb is not None:
        broker.max_payload_kb = max_payload_kb
    if enabled is not None:
        broker.enabled = enabled
    broker.updated_at = _now()
    db.flush()
    return broker


def delete_broker(db: DBSession, broker_id: int) -> bool:
    broker = db.get(MqttBroker, broker_id)
    if broker is None:
        return False
    db.delete(broker)
    db.flush()
    return True


def load_broker_password(broker: MqttBroker) -> str:
    """Plaintext da senha (uso interno do coletor)."""
    if not broker.password:
        return ""
    return _box().decrypt(broker.password) or ""


def broker_topics(broker: MqttBroker) -> list[str]:
    try:
        data = json.loads(broker.topics or "[]")
    except (json.JSONDecodeError, ValueError):
        return []
    return [str(t) for t in data if str(t).strip()] if isinstance(data, list) else []


# ── ledger ───────────────────────────────────────────────────────────────────


def insert_messages(db: DBSession, rows: list[dict[str, Any]]) -> int:
    """Gravação em lote (executemany). Chamado pelo coletor a cada janela."""
    if not rows:
        return 0
    db.execute(insert(MqttMessage), rows)
    return len(rows)


def insert_connection_events(db: DBSession, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    db.execute(insert(MqttConnectionEvent), rows)
    return len(rows)


def search_messages(
    db: DBSession,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    topic_filter: str | None = None,
    ramal: str | None = None,
    contains: str | None = None,
    pinned_only: bool = False,
    after_id: int | None = None,
    limit: int = 100,
    newest_first: bool = True,
) -> tuple[list[MqttMessage], int]:
    """Busca no ledger. Devolve (página, total que casou com o filtro).

    ``topic_filter`` aceita curingas MQTT; a parte fixa antes do primeiro
    curinga vira ``LIKE`` no banco (usa o índice) e o casamento exato é
    conferido em Python — evita varredura total quando o filtro é específico.
    """
    from middleware_monitor.domain.mqtt.address import match_topic_any

    stmt = select(MqttMessage)
    count_stmt = select(func.count()).select_from(MqttMessage)

    def apply(where: Any) -> None:
        nonlocal stmt, count_stmt
        stmt = stmt.where(where)
        count_stmt = count_stmt.where(where)

    if since is not None:
        apply(MqttMessage.received_at >= since)
    if until is not None:
        apply(MqttMessage.received_at <= until)
    if ramal:
        apply(MqttMessage.ramal == ramal.strip())
    if pinned_only:
        apply(MqttMessage.pinned.is_(True))
    if contains:
        apply(MqttMessage.payload.ilike(f"%{contains.strip()}%"))
    if after_id is not None:
        apply(MqttMessage.id > after_id)

    exact_filter: str | None = None
    if topic_filter and topic_filter.strip() not in ("", "#"):
        exact_filter = topic_filter.strip()
        prefixo = _literal_prefix(exact_filter)
        if prefixo:
            apply(MqttMessage.topic.like(f"{prefixo}%"))

    total = int(db.scalar(count_stmt) or 0)
    stmt = stmt.order_by(
        MqttMessage.received_at.desc() if newest_first else MqttMessage.received_at.asc(),
        MqttMessage.id.desc() if newest_first else MqttMessage.id.asc(),
    )
    if exact_filter is None:
        return list(db.scalars(stmt.limit(limit)).all()), total

    # Com curinga o corte fino é em Python; lê em blocos até completar a página.
    itens: list[MqttMessage] = []
    casaram = 0
    for row in db.scalars(stmt.execution_options(yield_per=500)):
        if not match_topic_any(exact_filter, row.topic):
            continue
        casaram += 1
        if len(itens) < limit:
            itens.append(row)
    return itens, casaram


def _literal_prefix(topic_filter: str) -> str:
    """Parte do filtro antes do primeiro curinga — serve de ``LIKE`` no índice."""
    partes: list[str] = []
    for nivel in topic_filter.split("/"):
        if nivel in ("+", "#"):
            break
        partes.append(nivel)
    return "/".join(partes)


def set_pinned(db: DBSession, message_id: int, pinned: bool) -> bool:
    msg = db.get(MqttMessage, message_id)
    if msg is None:
        return False
    msg.pinned = pinned
    db.flush()
    return True


def count_messages(db: DBSession) -> int:
    return int(db.scalar(select(func.count()).select_from(MqttMessage)) or 0)


def payload_bytes_total(db: DBSession) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(MqttMessage.payload_bytes), 0))) or 0)


def last_message_at(db: DBSession) -> datetime | None:
    return db.scalar(select(func.max(MqttMessage.received_at)))


def last_connection_event_before(db: DBSession, ts: datetime) -> MqttConnectionEvent | None:
    """Estado vigente logo antes da janela consultada — base da cobertura."""
    return db.scalars(
        select(MqttConnectionEvent)
        .where(MqttConnectionEvent.timestamp < ts)
        .order_by(MqttConnectionEvent.timestamp.desc(), MqttConnectionEvent.id.desc())
        .limit(1)
    ).first()


def list_connection_events(
    db: DBSession, *, since: datetime | None = None, until: datetime | None = None,
    limit: int = 500,
) -> list[MqttConnectionEvent]:
    stmt = select(MqttConnectionEvent)
    if since is not None:
        stmt = stmt.where(MqttConnectionEvent.timestamp >= since)
    if until is not None:
        stmt = stmt.where(MqttConnectionEvent.timestamp <= until)
    stmt = stmt.order_by(MqttConnectionEvent.timestamp.asc()).limit(limit)
    return list(db.scalars(stmt).all())


# ── retenção ─────────────────────────────────────────────────────────────────


def purge_messages_by_age(db: DBSession, cutoff: datetime) -> int:
    """Apaga mensagens anteriores ao corte. **Fixadas nunca são apagadas** —
    é o que garante que um comprovante já usado como evidência sobreviva."""
    return _rowcount(
        db,
        delete(MqttMessage).where(
            MqttMessage.received_at < cutoff, MqttMessage.pinned.is_(False),
        ),
    )


def purge_messages_by_size(db: DBSession, max_bytes: int, *, chunk: int = 5000) -> int:
    """Apaga as mais antigas até o volume de payload caber no limite.

    Remove exatamente o excedente: soma da mais antiga para a mais nova e para
    assim que couber. Fixadas ficam de fora — evidência não some por falta de
    espaço (avisar sobre o espaço é papel da tela, não da poda).
    """
    if max_bytes <= 0:
        return 0
    total = payload_bytes_total(db)
    if total <= max_bytes:
        return 0

    removidas = 0
    while total > max_bytes:
        # Sem cursor de paginação de propósito: as linhas do laço anterior já
        # foram apagadas, então a próxima consulta devolve as seguintes.
        lote = list(
            db.execute(
                select(MqttMessage.id, MqttMessage.payload_bytes)
                .where(MqttMessage.pinned.is_(False))
                .order_by(MqttMessage.received_at.asc(), MqttMessage.id.asc())
                .limit(chunk)
            ).all()
        )
        if not lote:
            break
        ids: list[int] = []
        for msg_id, tamanho in lote:
            ids.append(int(msg_id))
            total -= int(tamanho or 0)
            if total <= max_bytes:
                break
        removidas += _rowcount(db, delete(MqttMessage).where(MqttMessage.id.in_(ids)))
    return removidas


def purge_connection_events(db: DBSession, cutoff: datetime) -> int:
    return _rowcount(db, delete(MqttConnectionEvent).where(MqttConnectionEvent.timestamp < cutoff))
