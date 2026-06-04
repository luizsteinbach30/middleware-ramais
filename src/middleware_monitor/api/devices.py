"""Devices endpoints (list, detail, history, force ping)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import (
    get_current_user,
    get_session,
    require_admin,
    require_csrf,
)
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import (
    ExtensionEnvironment,
    ExtensionLine,
    User,
)
from middleware_monitor.core.tasks import spawn
from middleware_monitor.core.time import iso_utc
from middleware_monitor.domain.devices.repository import (
    delete_devices,
    get_device,
    history_aggregate,
    list_devices,
    recent_pings,
    record_ping,
    status_counts,
)
from middleware_monitor.domain.devices.schemas import (
    DeviceOut,
    DevicePingOut,
    DevicesPage,
    HistoryPoint,
    HistoryWindow,
)
from middleware_monitor.domain.extension_configurator import apply as ec_apply
from middleware_monitor.domain.extension_configurator import repository as ec_repo
from middleware_monitor.integrations.network import make_ping_probe
from middleware_monitor.jobs.monitor_devices import run_monitor_devices

router = APIRouter(prefix="/api/devices", tags=["devices"])
log = get_logger("api.devices")

_FORCE_LAST_AT: dict[str, float] = {}
_FORCE_RATE_S = 60.0


def _device_out(d, link: dict | None) -> DeviceOut:
    out = DeviceOut.model_validate(d, from_attributes=True)
    if link is not None:
        out.extension_line_id = link.get("line_id")
        out.extension_line_ramal = link.get("ramal")
        out.extension_line_nome_visivel = link.get("nome_visivel")
        out.extension_environment_id = link.get("env_id")
        out.extension_environment_nome = link.get("env_nome")
    return out


def _link_map_for_devices(db: DBSession, device_ids: list[int]) -> dict[int, dict]:
    """Retorna {device_id: {line_id, ramal, nome_visivel, env_id, env_nome}}.

    Uma query batch por ExtensionLine e outra por ExtensionEnvironment — evita
    N+1 quando lista paginada tem muitos devices.
    """
    if not device_ids:
        return {}
    lines = list(
        db.scalars(
            select(ExtensionLine).where(ExtensionLine.device_id.in_(device_ids))
        ).all()
    )
    if not lines:
        return {}
    env_ids = {ln.environment_id for ln in lines}
    envs = {
        e.id: e
        for e in db.scalars(
            select(ExtensionEnvironment).where(
                ExtensionEnvironment.id.in_(env_ids),
            )
        ).all()
    }
    out: dict[int, dict] = {}
    for ln in lines:
        env = envs.get(ln.environment_id)
        if ln.device_id is None or env is None:
            continue
        out[ln.device_id] = {
            "line_id": ln.id,
            "ramal": ln.numero_ramal,
            "nome_visivel": ln.nome_visivel,
            "env_id": env.id,
            "env_nome": env.nome,
        }
    return out


@router.get("", response_model=DevicesPage)
def list_(
    search: str | None = None,
    network: str | None = None,
    logical: str | None = None,
    environment: str | None = None,
    ip_from: str | None = None,
    ip_to: str | None = None,
    ramal_from: int | None = Query(default=None, ge=0),
    ramal_to: int | None = Query(default=None, ge=0),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> DevicesPage:
    rows, total = list_devices(
        db,
        search=search,
        network=network,
        logical=logical,
        environment=environment,
        ip_from=ip_from,
        ip_to=ip_to,
        ramal_from=ramal_from,
        ramal_to=ramal_to,
        page=page,
        size=size,
    )
    links = _link_map_for_devices(db, [r.id for r in rows])
    return DevicesPage(
        items=[_device_out(r, links.get(r.id)) for r in rows],
        total=total,
        page=page,
        size=size,
    )


class SummaryOut(BaseModel):
    total: int
    network_online: int
    network_offline: int
    logical_available: int
    logical_unavailable: int
    avg_latency_ms: int
    max_latency_ms: int


@router.get("/summary", response_model=SummaryOut)
def summary(
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> SummaryOut:
    return SummaryOut(**status_counts(db))


@router.get("/{device_id}", response_model=DeviceOut)
def detail(
    device_id: int,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> DeviceOut:
    d = get_device(db, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="not_found")
    links = _link_map_for_devices(db, [device_id])
    return _device_out(d, links.get(device_id))


@router.get("/{device_id}/pings", response_model=list[DevicePingOut])
def pings(
    device_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> list[DevicePingOut]:
    rows = recent_pings(db, device_id, limit=limit)
    return [
        DevicePingOut(timestamp=p.timestamp, online=p.online, latency_ms=p.latency_ms)
        for p in rows
    ]


@router.get("/{device_id}/history", response_model=HistoryWindow)
def history(
    device_id: int,
    window: str = Query(default="24h"),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> HistoryWindow:
    granularity, points = history_aggregate(db, device_id, window=window)
    return HistoryWindow(
        # history_aggregate só produz "1m"/"5m"/"1h"; o cast restringe o str ao Literal
        granularity=cast('Literal["1m", "5m", "1h"]', granularity),
        points=[
            HistoryPoint(timestamp=t, online_ratio=ratio, latency_ms_avg=avg, latency_ms_max=mx)
            for t, ratio, avg, mx in points
        ],
    )


@router.post(
    "/{device_id}/refresh",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def refresh_one(device_id: int, db: DBSession = Depends(get_session)) -> dict[str, object]:
    d = get_device(db, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="not_found")
    if not d.ip:
        raise HTTPException(status_code=400, detail="device_without_ip")
    probe = make_ping_probe()
    started = time.perf_counter()
    latency = await probe.ping(d.ip, timeout_ms=1500)
    online = latency is not None
    record_ping(db, d, online=online, latency_ms=latency)
    db.commit()
    return {
        "online": online,
        "latency_ms": latency,
        "took_ms": int((time.perf_counter() - started) * 1000),
        "timestamp": iso_utc(datetime.now(UTC)),
    }


@router.post(
    "/force-monitor",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def force_monitor(user: User = Depends(require_admin)) -> dict[str, str]:
    last = _FORCE_LAST_AT.get(str(user.id), 0.0)
    if time.monotonic() - last < _FORCE_RATE_S:
        raise HTTPException(status_code=429, detail="rate_limited")
    _FORCE_LAST_AT[str(user.id)] = time.monotonic()
    spawn(run_monitor_devices())
    return {"status": "scheduled"}


# ---------------------------------------------------------------------------
# Vinculação Device <-> ExtensionLine (Fase C)
# ---------------------------------------------------------------------------


class LinkedLineOut(BaseModel):
    line_id: str
    environment_id: str
    environment_nome: str
    modelo_telefone: str
    ip: str
    numero_ramal: str
    nome_visivel: str
    ultimo_status: str | None
    ultima_aplicacao: datetime | None
    ultimo_hash_aplicado: str | None
    ultimo_erro: str | None
    ultimo_modelo: str | None
    ultimo_mac: str | None


class AvailableLineOut(BaseModel):
    line_id: str
    environment_id: str
    environment_nome: str
    modelo_telefone: str
    ip: str
    numero_ramal: str
    nome_visivel: str


class LinkRequest(BaseModel):
    line_id: str


class ReapplyEventOut(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    reason: str
    error: str | None
    run_id: int | None


def _serialize_linked_line(
    line: ExtensionLine, env: ExtensionEnvironment,
) -> LinkedLineOut:
    return LinkedLineOut(
        line_id=line.id,
        environment_id=env.id,
        environment_nome=env.nome,
        modelo_telefone=env.modelo_telefone,
        ip=line.ip,
        numero_ramal=line.numero_ramal,
        nome_visivel=line.nome_visivel,
        ultimo_status=line.ultimo_status,
        ultima_aplicacao=line.ultima_aplicacao,
        ultimo_hash_aplicado=line.ultimo_hash_aplicado,
        ultimo_erro=line.ultimo_erro,
        ultimo_modelo=line.ultimo_modelo,
        ultimo_mac=line.ultimo_mac,
    )


@router.get("/{device_id}/extension-line", response_model=LinkedLineOut | None)
def get_linked_line(
    device_id: int,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> LinkedLineOut | None:
    d = get_device(db, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="not_found")
    line = db.scalar(
        select(ExtensionLine).where(ExtensionLine.device_id == device_id).limit(1),
    )
    if line is None:
        return None
    env = ec_repo.get_environment(db, line.environment_id)
    if env is None:
        return None
    return _serialize_linked_line(line, env)


@router.get("/{device_id}/available-lines", response_model=list[AvailableLineOut])
def list_available_lines(
    device_id: int,
    environment_id: str | None = Query(default=None),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> list[AvailableLineOut]:
    """Linhas que podem ser vinculadas a este device.

    Inclui: todas as linhas sem device_id. Se `environment_id` for passado,
    filtra apenas por esse ambiente. Útil pro modal de vinculação manual no
    fluxo "escolher ambiente primeiro".
    """
    d = get_device(db, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="not_found")

    stmt = (
        select(ExtensionLine)
        .where(ExtensionLine.device_id.is_(None))
        .order_by(ExtensionLine.ip, ExtensionLine.numero_ramal)
    )
    if environment_id:
        stmt = stmt.where(ExtensionLine.environment_id == environment_id)
    rows = list(db.scalars(stmt).all())
    out: list[AvailableLineOut] = []
    for ln in rows:
        env = ec_repo.get_environment(db, ln.environment_id)
        if env is None:
            continue
        out.append(AvailableLineOut(
            line_id=ln.id,
            environment_id=env.id,
            environment_nome=env.nome,
            modelo_telefone=env.modelo_telefone,
            ip=ln.ip,
            numero_ramal=ln.numero_ramal,
            nome_visivel=ln.nome_visivel,
        ))
    return out


class LinkEnvironmentOut(BaseModel):
    environment_id: str
    environment_nome: str
    modelo_telefone: str
    orphan_lines: int
    has_match: bool  # alguma linha do ambiente com IP igual ao do device


@router.get(
    "/{device_id}/link-environments",
    response_model=list[LinkEnvironmentOut],
)
def list_link_environments(
    device_id: int,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> list[LinkEnvironmentOut]:
    """Lista ambientes com linhas órfãs (sem device_id).

    Usado pelo modal de vinculação manual: usuário escolhe o ambiente; o
    sistema sugere automaticamente a linha (por IP). `has_match` indica se há
    match forte de IP — usado pra ordenar ambientes "óbvios" no topo.
    """
    d = get_device(db, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="not_found")

    envs = ec_repo.list_environments(db)
    out: list[LinkEnvironmentOut] = []
    for env in envs:
        orphans = list(
            db.scalars(
                select(ExtensionLine).where(
                    ExtensionLine.environment_id == env.id,
                    ExtensionLine.device_id.is_(None),
                )
            ).all()
        )
        if not orphans:
            continue
        has_match = bool(
            d.ip and any(ln.ip == d.ip for ln in orphans),
        )
        out.append(LinkEnvironmentOut(
            environment_id=env.id,
            environment_nome=env.nome,
            modelo_telefone=env.modelo_telefone,
            orphan_lines=len(orphans),
            has_match=has_match,
        ))
    # ambientes com match primeiro; depois alfabético
    out.sort(key=lambda e: (not e.has_match, e.environment_nome.lower()))
    return out


class LinkSuggestionOut(BaseModel):
    auto_resolved: bool
    line: AvailableLineOut | None
    candidates: list[AvailableLineOut]
    reason: str  # 'ip_match' | 'ramal_match' | 'single_orphan' | 'ambiguous' | 'none'


@router.get(
    "/{device_id}/link-suggestion",
    response_model=LinkSuggestionOut,
)
def link_suggestion(
    device_id: int,
    environment_id: str = Query(...),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> LinkSuggestionOut:
    """Sugere uma linha pra vincular ao device num ambiente específico.

    Heurística (em ordem):
      1. linha do ambiente com IP igual ao do device → auto_resolved
      2. linha do ambiente com ramal igual ao nome do device → auto_resolved
      3. ambiente com 1 única linha órfã → auto_resolved (single_orphan)
      4. múltiplas linhas órfãs → lista candidates, sem auto_resolved
    """
    d = get_device(db, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="not_found")
    env = ec_repo.get_environment(db, environment_id)
    if env is None:
        raise HTTPException(status_code=404, detail="environment_not_found")

    orphans = list(
        db.scalars(
            select(ExtensionLine).where(
                ExtensionLine.environment_id == environment_id,
                ExtensionLine.device_id.is_(None),
            )
        ).all()
    )

    def _to_out(ln: ExtensionLine) -> AvailableLineOut:
        return AvailableLineOut(
            line_id=ln.id,
            environment_id=env.id,
            environment_nome=env.nome,
            modelo_telefone=env.modelo_telefone,
            ip=ln.ip,
            numero_ramal=ln.numero_ramal,
            nome_visivel=ln.nome_visivel,
        )

    candidates_out = [_to_out(ln) for ln in orphans]

    # 1. IP match
    if d.ip:
        by_ip = [ln for ln in orphans if ln.ip == d.ip]
        if len(by_ip) == 1:
            return LinkSuggestionOut(
                auto_resolved=True, line=_to_out(by_ip[0]),
                candidates=candidates_out, reason="ip_match",
            )
    # 2. ramal match
    by_ramal = [ln for ln in orphans if ln.numero_ramal == d.name]
    if len(by_ramal) == 1:
        return LinkSuggestionOut(
            auto_resolved=True, line=_to_out(by_ramal[0]),
            candidates=candidates_out, reason="ramal_match",
        )
    # 3. única linha órfã
    if len(orphans) == 1:
        return LinkSuggestionOut(
            auto_resolved=True, line=_to_out(orphans[0]),
            candidates=candidates_out, reason="single_orphan",
        )
    # 4. ambíguo / vazio
    return LinkSuggestionOut(
        auto_resolved=False, line=None,
        candidates=candidates_out,
        reason="ambiguous" if orphans else "none",
    )


@router.post(
    "/{device_id}/link",
    response_model=LinkedLineOut,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def link_device(
    device_id: int,
    payload: LinkRequest,
    db: DBSession = Depends(get_session),
) -> LinkedLineOut:
    d = get_device(db, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="not_found")
    line = ec_repo.get_line(db, payload.line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="line_not_found")
    if line.device_id is not None and line.device_id != device_id:
        raise HTTPException(status_code=409, detail="line_already_linked_to_other_device")
    ec_repo.link_line_to_device(db, line, d)
    db.commit()
    env = ec_repo.get_environment(db, line.environment_id)
    if env is None:
        raise HTTPException(status_code=500, detail="environment_missing")
    return _serialize_linked_line(line, env)


@router.delete(
    "/{device_id}/link",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def unlink_device(
    device_id: int,
    db: DBSession = Depends(get_session),
) -> dict[str, str]:
    d = get_device(db, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="not_found")
    line = db.scalar(
        select(ExtensionLine).where(ExtensionLine.device_id == device_id).limit(1),
    )
    if line is None:
        return {"status": "ok", "detail": "no_link"}
    ec_repo.unlink_line_from_device(db, line)
    db.commit()
    return {"status": "ok"}


@router.post(
    "/{device_id}/apply-config",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def apply_config_to_device(
    device_id: int,
    user: User = Depends(require_admin),
    db: DBSession = Depends(get_session),
) -> dict[str, object]:
    d = get_device(db, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="not_found")
    line = db.scalar(
        select(ExtensionLine).where(ExtensionLine.device_id == device_id).limit(1),
    )
    if line is None:
        raise HTTPException(status_code=400, detail="device_not_linked")
    if not line.ip:
        raise HTTPException(status_code=400, detail="line_without_ip")
    line_id = line.id
    try:
        run_id, db_run_id, event_id = await ec_apply.run_apply_single_line(
            line_id, operador=user.username, reason="manual_device_page",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "scheduled",
        "run_id": run_id,
        "db_run_id": db_run_id,
        "event_id": event_id,
        "line_id": line_id,
    }


@router.get(
    "/{device_id}/reapply-events",
    response_model=list[ReapplyEventOut],
)
def list_device_reapply_events(
    device_id: int,
    limit: int = Query(default=20, ge=1, le=200),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> list[ReapplyEventOut]:
    d = get_device(db, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="not_found")
    events = ec_repo.list_reapply_events(db, device_id=device_id, limit=limit)
    return [
        ReapplyEventOut(
            id=e.id,
            started_at=e.started_at,
            finished_at=e.finished_at,
            status=e.status,
            reason=e.reason,
            error=e.error,
            run_id=e.run_id,
        )
        for e in events
    ]


# ---------------------------------------------------------------------------
# Auto-link em massa (Fase D)
# ---------------------------------------------------------------------------


@router.post(
    "/auto-link",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def auto_link_now(
    _user: User = Depends(require_admin),
    db: DBSession = Depends(get_session),
) -> dict[str, int]:
    """Roda `auto_link_lines_by_ip` agora — vincula todas as ExtensionLines
    órfãs cujo IP bate com algum Device. Idempotente."""
    linked = ec_repo.auto_link_lines_by_ip(db)
    db.commit()
    return {"linked": linked}


# ---------------------------------------------------------------------------
# Ações em massa sobre devices selecionados
# ---------------------------------------------------------------------------


class BulkDeleteIn(BaseModel):
    device_ids: list[int]


class AddToEnvironmentIn(BaseModel):
    environment_id: str
    device_ids: list[int]


class CreateEnvFromDevicesIn(BaseModel):
    nome: str
    modelo_telefone: str = "HTEK UC902G"
    device_ids: list[int]


class BulkAddResult(BaseModel):
    environment_id: str
    environment_nome: str
    added: int
    skipped: int


@router.post(
    "/bulk/delete",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def bulk_delete(
    payload: BulkDeleteIn,
    db: DBSession = Depends(get_session),
) -> dict[str, int]:
    """Apaga os devices selecionados. Linhas vinculadas são preservadas (apenas
    desvinculadas); pings são removidos."""
    deleted = delete_devices(db, payload.device_ids)
    db.commit()
    log.info("devices_bulk_deleted", count=deleted)
    return {"deleted": deleted}


@router.post(
    "/bulk/add-to-environment",
    response_model=BulkAddResult,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def bulk_add_to_environment(
    payload: AddToEnvironmentIn,
    db: DBSession = Depends(get_session),
) -> BulkAddResult:
    """Adiciona os devices selecionados como linhas de um ambiente existente."""
    env = ec_repo.get_environment(db, payload.environment_id)
    if env is None:
        raise HTTPException(status_code=404, detail="environment_not_found")
    res = ec_repo.add_devices_as_lines(db, env, payload.device_ids)
    db.commit()
    log.info(
        "devices_added_to_environment",
        env_id=env.id, added=res["added"], skipped=res["skipped"],
    )
    return BulkAddResult(
        environment_id=env.id, environment_nome=env.nome,
        added=res["added"], skipped=res["skipped"],
    )


@router.post(
    "/bulk/create-environment",
    response_model=BulkAddResult,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def bulk_create_environment(
    payload: CreateEnvFromDevicesIn,
    db: DBSession = Depends(get_session),
) -> BulkAddResult:
    """Cria um ambiente novo (nome + modelo) e já adiciona os devices
    selecionados como linhas."""
    nome = payload.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="nome_obrigatorio")
    modelo = payload.modelo_telefone.strip() or "HTEK UC902G"
    env = ec_repo.create_environment(db, nome=nome, modelo_telefone=modelo)
    res = ec_repo.add_devices_as_lines(db, env, payload.device_ids)
    db.commit()
    log.info(
        "environment_created_from_devices",
        env_id=env.id, modelo=modelo, added=res["added"], skipped=res["skipped"],
    )
    return BulkAddResult(
        environment_id=env.id, environment_nome=env.nome,
        added=res["added"], skipped=res["skipped"],
    )
