"""Repository do Configurador de Ramais.

CRUD direto em SQLAlchemy 2.0 Session sincrona. `config_padrao` é JSON em
Text — o repository serializa na escrita e devolve dict mesclado com defaults
na leitura, pra que callers sempre vejam o conjunto completo de chaves.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.models import (
    Device,
    ExtensionApplyRun,
    ExtensionEnvironment,
    ExtensionLine,
    LineReapplyEvent,
)

from .defaults import default_config_padrao

__all__ = [
    "auto_link_lines_by_ip",
    "create_environment",
    "create_reapply_event",
    "create_run",
    "delete_environment",
    "finish_reapply_event",
    "finish_run",
    "generate_slug",
    "get_environment",
    "get_line",
    "last_reapply_event_for_line",
    "link_line_to_device",
    "list_environments",
    "list_lines",
    "list_reapply_events",
    "list_runs",
    "merged_config_padrao",
    "new_line",
    "save_lines",
    "unlink_line_from_device",
    "update_environment",
    "update_line_status",
]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


_ACCENT_MAP: list[tuple[str, str]] = [
    ("áàâãä", "a"), ("éèêë", "e"), ("íìîï", "i"),
    ("óòôõö", "o"), ("úùûü", "u"), ("ç", "c"),
]


def generate_slug(nome: str) -> str:
    """ASCII-safe slug; usado como `id` (PK) do ambiente."""
    s = nome.lower().strip()
    for chars, repl in _ACCENT_MAP:
        s = re.sub(f"[{chars}]", repl, s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or f"ambiente-{uuid.uuid4().hex[:6]}"


def merged_config_padrao(env: ExtensionEnvironment) -> dict[str, Any]:
    """Lê `config_padrao` (JSON) e mescla com defaults — defaults nunca apagam
    chaves salvas, mas garantem que chaves novas apareçam pra UI."""
    base = default_config_padrao()
    try:
        saved = json.loads(env.config_padrao or "{}")
    except json.JSONDecodeError:
        saved = {}
    if isinstance(saved, dict):
        base.update(saved)
    return base


def create_environment(
    db: DBSession, *, nome: str, modelo_telefone: str,
) -> ExtensionEnvironment:
    base_id = generate_slug(nome)
    env_id = base_id
    i = 2
    while db.get(ExtensionEnvironment, env_id) is not None:
        env_id = f"{base_id}-{i}"
        i += 1
    now = _now()
    env = ExtensionEnvironment(
        id=env_id,
        nome=nome,
        modelo_telefone=modelo_telefone,
        config_padrao=json.dumps(default_config_padrao(), ensure_ascii=False),
        created_at=now,
        updated_at=now,
    )
    db.add(env)
    db.flush()
    return env


def list_environments(db: DBSession) -> list[ExtensionEnvironment]:
    return list(
        db.scalars(select(ExtensionEnvironment).order_by(ExtensionEnvironment.nome)).all()
    )


def get_environment(db: DBSession, env_id: str) -> ExtensionEnvironment | None:
    return db.get(ExtensionEnvironment, env_id)


def update_environment(
    db: DBSession, env: ExtensionEnvironment, *,
    nome: str | None = None,
    config_padrao: dict[str, Any] | None = None,
) -> ExtensionEnvironment:
    if nome is not None:
        env.nome = nome
    if config_padrao is not None:
        merged = merged_config_padrao(env)
        merged.update(config_padrao)
        env.config_padrao = json.dumps(merged, ensure_ascii=False)
    env.updated_at = _now()
    db.flush()
    return env


def delete_environment(db: DBSession, env_id: str) -> bool:
    env = db.get(ExtensionEnvironment, env_id)
    if env is None:
        return False
    db.delete(env)
    db.flush()
    return True


def new_line(
    *,
    ip: str = "",
    numero_ramal: str = "",
    user_auth: str = "",
    senha_sip: str = "",
    servidor_sip: str = "",
    numero_abreviado: str = "",
    nome_visivel: str = "",
) -> dict[str, Any]:
    """Helper que devolve dict pronto para `save_lines`. Não persiste."""
    return {
        "id": uuid.uuid4().hex,
        "ip": ip,
        "numero_ramal": numero_ramal,
        "user_auth": user_auth or numero_ramal,
        "senha_sip": senha_sip,
        "servidor_sip": servidor_sip,
        "numero_abreviado": numero_abreviado,
        "nome_visivel": nome_visivel,
    }


def list_lines(db: DBSession, env_id: str) -> list[ExtensionLine]:
    return list(
        db.scalars(
            select(ExtensionLine)
            .where(ExtensionLine.environment_id == env_id)
            .order_by(ExtensionLine.created_at, ExtensionLine.id)
        ).all()
    )


def get_device_info_for_lines(
    db: DBSession, lines: list[ExtensionLine],
) -> dict[str, dict[str, Any]]:
    """Para cada linha vinculada a um Device, retorna {line_id: {device_id,
    device_name, device_ip, network_status}}.

    Linhas sem vínculo não aparecem no dict.
    """
    ids = [ln.device_id for ln in lines if ln.device_id is not None]
    if not ids:
        return {}
    devs = list(db.scalars(select(Device).where(Device.id.in_(ids))).all())
    by_dev = {d.id: d for d in devs}
    out: dict[str, dict[str, Any]] = {}
    for ln in lines:
        if ln.device_id is None:
            continue
        d = by_dev.get(ln.device_id)
        if d is None:
            continue
        out[ln.id] = {
            "device_id": d.id,
            "device_name": d.name,
            "device_ip": d.ip,
            "network_status": d.network_status,
        }
    return out


def save_lines(
    db: DBSession, env: ExtensionEnvironment, rows: list[dict[str, Any]],
) -> list[ExtensionLine]:
    """Substitui todas as linhas do ambiente pelas vindas da planilha.

    Estratégia upsert-por-id: linhas vindas com `id` existente são atualizadas
    preservando histórico (ultimo_*); linhas com id novo são criadas; linhas
    ausentes na entrada são removidas (cascade).

    Se o IP de uma linha mudar e o novo IP já não bater com o device atualmente
    vinculado, o vínculo é zerado (preserva consistência). Após o upsert, roda
    `auto_link_lines_by_ip` pra revincular linhas órfãs que agora batem com
    algum Device.
    """
    now = _now()
    incoming_ids = {str(r.get("id") or "") for r in rows if r.get("id")}
    existing = {ln.id: ln for ln in list_lines(db, env.id)}
    # remove as que sumiram
    for old_id, ln in existing.items():
        if old_id not in incoming_ids:
            db.delete(ln)
    # upsert
    out: list[ExtensionLine] = []
    for r in rows:
        rid = str(r.get("id") or "") or uuid.uuid4().hex
        new_ip = str(r.get("ip", "") or "")
        fields = {
            "ip": new_ip,
            "numero_ramal": str(r.get("numero_ramal", "") or ""),
            "user_auth": str(r.get("user_auth", "") or r.get("numero_ramal", "") or ""),
            "senha_sip": str(r.get("senha_sip", "") or ""),
            "servidor_sip": str(r.get("servidor_sip", "") or ""),
            "numero_abreviado": str(r.get("numero_abreviado", "") or ""),
            "nome_visivel": str(r.get("nome_visivel", "") or ""),
        }
        ln_existing = existing.get(rid)
        if ln_existing is None:
            ln_new = ExtensionLine(
                id=rid, environment_id=env.id, created_at=now, updated_at=now, **fields,
            )
            db.add(ln_new)
            out.append(ln_new)
        else:
            ip_changed = ln_existing.ip != new_ip
            for k, v in fields.items():
                setattr(ln_existing, k, v)
            ln_existing.updated_at = now
            # Se IP mudou e linha estava vinculada a device com outro IP, zera
            # vínculo. O auto_link_lines_by_ip abaixo revincula se aplicável.
            if ip_changed and ln_existing.device_id is not None:
                dev = db.get(Device, ln_existing.device_id)
                if dev is None or dev.ip != new_ip:
                    ln_existing.device_id = None
            out.append(ln_existing)
    env.updated_at = now
    db.flush()
    auto_link_lines_by_ip(db)
    return out


def update_line_status(
    db: DBSession,
    line: ExtensionLine,
    *,
    status: str,
    erro: str | None = None,
    hash_aplicado: str | None = None,
    modelo: str | None = None,
    mac: str | None = None,
) -> None:
    now = _now()
    line.ultimo_status = status
    line.ultima_aplicacao = now
    line.ultimo_erro = erro
    if hash_aplicado is not None:
        line.ultimo_hash_aplicado = hash_aplicado
    if modelo is not None:
        line.ultimo_modelo = modelo
    if mac is not None:
        line.ultimo_mac = mac
    line.updated_at = now
    db.flush()


def create_run(
    db: DBSession, env_id: str, *, total: int, forcado: bool, operador: str | None,
) -> ExtensionApplyRun:
    run = ExtensionApplyRun(
        environment_id=env_id,
        started_at=_now(),
        total=total,
        ok=0,
        falha=0,
        forcado=forcado,
        operador=operador,
    )
    db.add(run)
    db.flush()
    return run


def finish_run(db: DBSession, run: ExtensionApplyRun, *, ok: int, falha: int) -> None:
    run.finished_at = _now()
    run.ok = ok
    run.falha = falha
    db.flush()


def list_runs(
    db: DBSession, *, env_id: str | None = None, limit: int = 100,
) -> list[ExtensionApplyRun]:
    stmt = select(ExtensionApplyRun).order_by(ExtensionApplyRun.started_at.desc())
    if env_id:
        stmt = stmt.where(ExtensionApplyRun.environment_id == env_id)
    return list(db.scalars(stmt.limit(limit)).all())


# ---------------------------------------------------------------------------
# Vinculação Device <-> ExtensionLine
# ---------------------------------------------------------------------------


def get_line(db: DBSession, line_id: str) -> ExtensionLine | None:
    return db.get(ExtensionLine, line_id)


def link_line_to_device(
    db: DBSession, line: ExtensionLine, device: Device,
) -> ExtensionLine:
    line.device_id = device.id
    line.updated_at = _now()
    db.flush()
    return line


def unlink_line_from_device(db: DBSession, line: ExtensionLine) -> ExtensionLine:
    line.device_id = None
    line.updated_at = _now()
    db.flush()
    return line


def auto_link_lines_by_ip(db: DBSession) -> int:
    """Vincula ExtensionLines sem device_id ao Device cujo IP bate.

    Idempotente. Retorna quantidade de linhas vinculadas nesta passada.
    """
    linked = 0
    rows = list(
        db.scalars(
            select(ExtensionLine).where(
                ExtensionLine.device_id.is_(None),
                ExtensionLine.ip.is_not(None),
                ExtensionLine.ip != "",
            )
        ).all()
    )
    for ln in rows:
        dev = db.scalar(
            select(Device).where(Device.ip == ln.ip).limit(1)
        )
        if dev is not None:
            ln.device_id = dev.id
            ln.updated_at = _now()
            linked += 1
    if linked:
        db.flush()
    return linked


# ---------------------------------------------------------------------------
# Histórico de reapply automático
# ---------------------------------------------------------------------------


def create_reapply_event(
    db: DBSession,
    *,
    line_id: str,
    device_id: int | None,
    reason: str,
    status: str = "running",
    error: str | None = None,
    run_id: int | None = None,
) -> LineReapplyEvent:
    ev = LineReapplyEvent(
        line_id=line_id,
        device_id=device_id,
        started_at=_now(),
        status=status,
        reason=reason,
        error=error,
        run_id=run_id,
    )
    db.add(ev)
    db.flush()
    return ev


def finish_reapply_event(
    db: DBSession,
    event: LineReapplyEvent,
    *,
    status: str,
    error: str | None = None,
    run_id: int | None = None,
) -> None:
    event.finished_at = _now()
    event.status = status
    if error is not None:
        event.error = error
    if run_id is not None:
        event.run_id = run_id
    db.flush()


def list_reapply_events(
    db: DBSession,
    *,
    line_id: str | None = None,
    device_id: int | None = None,
    limit: int = 50,
) -> list[LineReapplyEvent]:
    stmt = select(LineReapplyEvent).order_by(
        LineReapplyEvent.started_at.desc(), LineReapplyEvent.id.desc(),
    )
    if line_id is not None:
        stmt = stmt.where(LineReapplyEvent.line_id == line_id)
    if device_id is not None:
        stmt = stmt.where(LineReapplyEvent.device_id == device_id)
    return list(db.scalars(stmt.limit(limit)).all())


def last_reapply_event_for_line(
    db: DBSession, line_id: str,
) -> LineReapplyEvent | None:
    return db.scalar(
        select(LineReapplyEvent)
        .where(LineReapplyEvent.line_id == line_id)
        .order_by(
            LineReapplyEvent.started_at.desc(), LineReapplyEvent.id.desc(),
        )
        .limit(1),
    )
