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
    ExtensionApplyRun,
    ExtensionEnvironment,
    ExtensionLine,
)

from .defaults import default_config_padrao

__all__ = [
    "create_environment",
    "create_run",
    "delete_environment",
    "finish_run",
    "generate_slug",
    "get_environment",
    "list_environments",
    "list_lines",
    "list_runs",
    "merged_config_padrao",
    "new_line",
    "save_lines",
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


def save_lines(
    db: DBSession, env: ExtensionEnvironment, rows: list[dict[str, Any]],
) -> list[ExtensionLine]:
    """Substitui todas as linhas do ambiente pelas vindas da planilha.

    Estratégia upsert-por-id: linhas vindas com `id` existente são atualizadas
    preservando histórico (ultimo_*); linhas com id novo são criadas; linhas
    ausentes na entrada são removidas (cascade).
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
        fields = {
            "ip": str(r.get("ip", "") or ""),
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
            for k, v in fields.items():
                setattr(ln_existing, k, v)
            ln_existing.updated_at = now
            out.append(ln_existing)
    env.updated_at = now
    db.flush()
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
