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
    ExtensionApplyRunLine,
    ExtensionEnvironment,
    ExtensionLine,
    LineReapplyEvent,
)

from .defaults import default_config_padrao

__all__ = [
    "add_devices_as_lines",
    "auto_link_lines_by_ip",
    "clone_environment",
    "create_environment",
    "create_reapply_event",
    "create_run",
    "create_run_line",
    "delete_environment",
    "finish_reapply_event",
    "finish_run",
    "finish_run_line",
    "generate_slug",
    "get_environment",
    "get_line",
    "last_reapply_event_for_line",
    "link_line_to_device",
    "list_environments",
    "list_lines",
    "list_reapply_events",
    "list_run_lines",
    "list_runs",
    "merged_config_padrao",
    "new_line",
    "save_lines",
    "unlink_line_from_device",
    "update_environment",
    "update_line_macs",
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


def clone_environment(
    db: DBSession, src: ExtensionEnvironment, *, nome: str | None = None,
) -> ExtensionEnvironment:
    """Cria um NOVO ambiente copiando o modelo e TODA a config padrão de `src`.

    Não copia ramais (linhas) nem histórico de execuções — só o "molde" do
    ambiente. `merged_config_padrao` garante que o conjunto integral de chaves
    (servidor SIP, credenciais web/SIP, function keys, softkeys, idioma, etc.)
    seja levado. Nome padrão "Cópia de {nome}"; colisão de slug é resolvida por
    `create_environment`.
    """
    new_nome = (nome or "").strip() or f"Cópia de {src.nome}"
    new_env = create_environment(db, nome=new_nome, modelo_telefone=src.modelo_telefone)
    new_env.config_padrao = json.dumps(merged_config_padrao(src), ensure_ascii=False)
    new_env.updated_at = _now()
    db.flush()
    return new_env


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
    # posicao = ordem da planilha (canônica); created_at/id só como desempate
    # para dados antigos com posicao repetida.
    return list(
        db.scalars(
            select(ExtensionLine)
            .where(ExtensionLine.environment_id == env_id)
            .order_by(
                ExtensionLine.posicao,
                ExtensionLine.created_at,
                ExtensionLine.id,
            )
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
    for i, r in enumerate(rows):
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
            # ordem canônica = posição no payload (ordem da planilha na tela)
            "posicao": i,
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


def update_line_macs(db: DBSession, env_id: str, mac_by_ip: dict[str, str]) -> int:
    """Persiste o MAC coletado (via ARP) nas linhas do ambiente, casado por IP.

    Chamado pelo monitor de ping ao vivo — precisa ser idempotente e barato:
    só grava quando o MAC é novo ou mudou (nada a fazer nas rodadas seguintes).
    Retorna quantas linhas foram efetivamente atualizadas.
    """
    if not mac_by_ip:
        return 0
    now = _now()
    changed = 0
    for line in list_lines(db, env_id):
        mac = mac_by_ip.get(line.ip or "")
        if mac and line.ultimo_mac != mac:
            line.ultimo_mac = mac
            line.updated_at = now
            changed += 1
    if changed:
        db.flush()
    return changed


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


def create_run_line(
    db: DBSession,
    *,
    run_id: int,
    line_id: str | None,
    numero_ramal: str,
    ip: str,
    nome_visivel: str,
    status_antes: str,
) -> ExtensionApplyRunLine:
    """Snapshot de uma linha impactada por um run (status_depois='running')."""
    rl = ExtensionApplyRunLine(
        run_id=run_id,
        line_id=line_id,
        numero_ramal=numero_ramal,
        ip=ip,
        nome_visivel=nome_visivel,
        status_antes=status_antes,
        status_depois="running",
        created_at=_now(),
    )
    db.add(rl)
    db.flush()
    return rl


def finish_run_line(
    db: DBSession,
    run_line: ExtensionApplyRunLine,
    *,
    status_depois: str,
    erro: str | None = None,
    modelo: str | None = None,
    registro_sip: str | None = None,
) -> None:
    run_line.status_depois = status_depois
    if erro is not None:
        run_line.erro = erro
    if modelo is not None:
        run_line.modelo = modelo
    if registro_sip is not None:
        run_line.registro_sip = registro_sip
    db.flush()


def list_run_lines(db: DBSession, run_id: int) -> list[ExtensionApplyRunLine]:
    return list(
        db.scalars(
            select(ExtensionApplyRunLine)
            .where(ExtensionApplyRunLine.run_id == run_id)
            .order_by(ExtensionApplyRunLine.numero_ramal, ExtensionApplyRunLine.id)
        ).all()
    )


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


def add_devices_as_lines(
    db: DBSession, env: ExtensionEnvironment, device_ids: list[int],
) -> dict[str, Any]:
    """Cria uma ExtensionLine por device no ambiente, vinculada ao device.

    Preenche apenas os campos conhecidos do device: ``ip`` e ``numero_ramal``
    (= ``device.name``, que é o ramal no USCall); ``user_auth`` herda o ramal
    (convenção de ``new_line``). Os demais (senha SIP, servidor, etc.) ficam
    vazios e herdam os defaults do ambiente na geração da config.

    Diferente de ``save_lines``, **não** apaga as linhas existentes — só faz
    append. Pula (sem erro) devices já vinculados a alguma linha e devices cujo
    IP já existe como linha neste ambiente. Retorna
    ``{"added", "skipped", "skipped_detail"}``.
    """
    if not device_ids:
        return {"added": 0, "skipped": 0, "skipped_detail": []}
    devices = list(db.scalars(select(Device).where(Device.id.in_(device_ids))).all())
    by_id = {d.id: d for d in devices}
    existing_lines = list_lines(db, env.id)
    existing_ips = {ln.ip for ln in existing_lines if ln.ip}
    # append: continua a numeração de posicao a partir do fim da planilha
    next_pos = max((ln.posicao for ln in existing_lines), default=-1) + 1
    already_linked = set(
        db.scalars(
            select(ExtensionLine.device_id).where(
                ExtensionLine.device_id.in_(device_ids),
                ExtensionLine.device_id.is_not(None),
            )
        ).all()
    )
    now = _now()
    added = 0
    skipped: list[dict[str, Any]] = []
    for dev_id in device_ids:
        dev = by_id.get(dev_id)
        if dev is None:
            skipped.append({"device_id": dev_id, "reason": "not_found"})
            continue
        if dev_id in already_linked:
            skipped.append({"device_id": dev_id, "reason": "already_linked"})
            continue
        if dev.ip and dev.ip in existing_ips:
            skipped.append({"device_id": dev_id, "reason": "ip_duplicate"})
            continue
        db.add(ExtensionLine(
            id=uuid.uuid4().hex,
            environment_id=env.id,
            ip=dev.ip or "",
            numero_ramal=dev.name,
            user_auth=dev.name,
            device_id=dev.id,
            posicao=next_pos,
            created_at=now,
            updated_at=now,
        ))
        next_pos += 1
        added += 1
        already_linked.add(dev_id)
        if dev.ip:
            existing_ips.add(dev.ip)
    env.updated_at = now
    db.flush()
    return {"added": added, "skipped": len(skipped), "skipped_detail": skipped}


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
