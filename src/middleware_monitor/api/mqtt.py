"""API do coletor MQTT: configuração assistida, estado e ledger de mensagens.

O ledger é a razão de ser desta integração: o serviço que publica no broker não
registra os próprios envios, e daqui sai o comprovante de que a mensagem chegou,
no tópico certo, na hora certa — com a prova de cobertura do período junto.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import (
    get_current_user,
    get_session,
    require_admin,
    require_csrf,
)
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import MqttBroker, MqttMessage, User
from middleware_monitor.core.time import as_local_str, iso_utc
from middleware_monitor.domain.config.repository import load_config
from middleware_monitor.domain.mqtt import repository as repo
from middleware_monitor.domain.mqtt.address import (
    normalize_topic_filter,
    validate_topic_filter,
)
from middleware_monitor.domain.mqtt.coverage import Coverage, compute_coverage
from middleware_monitor.domain.mqtt.discovery import (
    DEFAULT_SNIFF_FILTERS,
    ProbeResult,
    discover,
    sniff_topics,
)
from middleware_monitor.domain.mqtt.schemas import (
    BrokerIn,
    BrokerOut,
    BrokerStatus,
    CoverageGap,
    CoverageOut,
    DiscoverRequest,
    DiscoverResponse,
    MessageDetail,
    MessageOut,
    MessagesPage,
    PinRequest,
    ProbeOut,
    SniffBranch,
    SniffRequest,
    SniffResponse,
    StatusOut,
)
from middleware_monitor.domain.mqtt.service import get_ingestor
from middleware_monitor.integrations.mqtt_client import MqttAuth, MqttEndpoint
from middleware_monitor.version import __version__

router = APIRouter(prefix="/api/mqtt", tags=["mqtt"])
log = get_logger("api.mqtt")

_SPAN_RE = re.compile(r"^(\d+)([mhd])$")
PREVIEW_CHARS = 240


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ── configuração do broker ───────────────────────────────────────────────────


def _broker_out(b: MqttBroker) -> BrokerOut:
    return BrokerOut(
        id=b.id,
        nome=b.nome,
        address_input=b.address_input,
        host=b.host,
        port=b.port,
        transport=b.transport,
        tls=b.tls,
        ws_path=b.ws_path,
        username=b.username,
        password="set" if b.password else None,
        tls_verify=b.tls_verify,
        tls_fingerprint=b.tls_fingerprint,
        topics=repo.broker_topics(b),
        qos=b.qos,
        clean_session=b.clean_session,
        client_id=b.client_id,
        max_payload_kb=b.max_payload_kb,
        enabled=b.enabled,
    )


def _validated_topics(topics: list[str]) -> list[str]:
    limpos: list[str] = []
    for raw in topics:
        filtro = normalize_topic_filter(raw)
        erro = validate_topic_filter(filtro)
        if erro:
            raise HTTPException(status_code=422, detail=f"topic_invalid:{erro}")
        if filtro not in limpos:
            limpos.append(filtro)
    return limpos


def _apply_config_async() -> None:
    """Reconecta o coletor com a configuração recém-salva, sem reiniciar o app.

    Estes endpoints são síncronos (mexem no banco) e por isso rodam em uma
    thread do FastAPI: o agendamento tem de ir para o loop do coletor, não
    para o desta thread — que não tem loop nenhum.
    """
    get_ingestor().request_reload()


@router.get("/brokers", response_model=list[BrokerOut], dependencies=[Depends(require_admin)])
def list_brokers(db: DBSession = Depends(get_session)) -> list[BrokerOut]:
    return [_broker_out(b) for b in repo.list_brokers(db)]


@router.post(
    "/brokers",
    response_model=BrokerOut,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def create_broker(payload: BrokerIn, db: DBSession = Depends(get_session)) -> BrokerOut:
    topics = _validated_topics(payload.topics)
    if not topics:
        raise HTTPException(status_code=422, detail="topics_required")
    senha = payload.password if payload.password not in (None, "set") else ""
    broker = repo.create_broker(
        db,
        nome=payload.nome,
        address_input=payload.address_input,
        host=payload.host,
        port=payload.port,
        transport=payload.transport,
        tls=payload.tls,
        ws_path=payload.ws_path,
        username=payload.username,
        password_plain=senha or "",
        tls_verify=payload.tls_verify,
        tls_fingerprint=payload.tls_fingerprint,
        topics=topics,
        qos=payload.qos,
        max_payload_kb=payload.max_payload_kb,
        enabled=payload.enabled,
    )
    db.commit()
    log.info("mqtt_broker_created", broker_id=broker.id, nome=broker.nome, endpoint=broker.host)
    _apply_config_async()
    return _broker_out(broker)


@router.put(
    "/brokers/{broker_id}",
    response_model=BrokerOut,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def update_broker(
    broker_id: int, payload: BrokerIn, db: DBSession = Depends(get_session),
) -> BrokerOut:
    broker = repo.get_broker(db, broker_id)
    if broker is None:
        raise HTTPException(status_code=404, detail="broker_not_found")
    topics = _validated_topics(payload.topics)
    if not topics:
        raise HTTPException(status_code=422, detail="topics_required")
    # "set" (ou ausente) preserva a senha gravada; string nova re-cifra.
    senha = None if payload.password in (None, "set") else payload.password
    repo.update_broker(
        db,
        broker,
        nome=payload.nome,
        address_input=payload.address_input,
        host=payload.host,
        port=payload.port,
        transport=payload.transport,
        tls=payload.tls,
        ws_path=payload.ws_path,
        username=payload.username,
        password_plain=senha,
        tls_verify=payload.tls_verify,
        tls_fingerprint=payload.tls_fingerprint or "",
        topics=topics,
        qos=payload.qos,
        max_payload_kb=payload.max_payload_kb,
        enabled=payload.enabled,
    )
    db.commit()
    log.info("mqtt_broker_updated", broker_id=broker.id, nome=broker.nome)
    _apply_config_async()
    return _broker_out(broker)


@router.delete(
    "/brokers/{broker_id}",
    status_code=204,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def delete_broker(broker_id: int, db: DBSession = Depends(get_session)) -> Response:
    if not repo.delete_broker(db, broker_id):
        raise HTTPException(status_code=404, detail="broker_not_found")
    db.commit()
    log.info("mqtt_broker_deleted", broker_id=broker_id)
    _apply_config_async()
    return Response(status_code=204)


# ── descoberta ───────────────────────────────────────────────────────────────


@router.post(
    "/discover",
    response_model=DiscoverResponse,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def discover_endpoint(
    payload: DiscoverRequest, db: DBSession = Depends(get_session),
) -> DiscoverResponse:
    """Descobre porta, transporte e TLS testando a rede — nada é deduzido do texto."""
    import asyncio

    senha = payload.password or ""
    if not senha and payload.broker_id is not None:
        broker = repo.get_broker(db, payload.broker_id)
        if broker is not None:
            senha = repo.load_broker_password(broker)

    report = await asyncio.to_thread(
        discover, payload.address, username=payload.username, password=senha,
    )
    log.info(
        "mqtt_discover",
        address=payload.address,
        success=report.success,
        chosen=report.chosen.label if report.chosen else None,
        error=report.error or None,
    )
    return DiscoverResponse(
        success=report.success,
        address_input=report.address_input,
        host=report.host,
        resolved=report.resolved,
        dns_error=report.dns_error,
        results=[_probe_out(r) for r in report.results],
        chosen=_probe_out(report.chosen) if report.chosen else None,
        needs_credentials=report.needs_credentials,
        needs_cert_trust=report.needs_cert_trust,
        notes=report.notes,
        error=report.error,
    )


def _probe_out(r: ProbeResult) -> ProbeOut:
    cert = r.cert
    return ProbeOut(
        label=r.label,
        host=r.host,
        port=r.port,
        transport=r.transport,
        tls=r.tls,
        ws_path=r.ws_path,
        reason=r.reason,
        tcp_ok=r.tcp_ok,
        latency_ms=r.latency_ms,
        tls_ok=r.tls_ok,
        mqtt_ok=r.mqtt_ok,
        connack=r.connack,
        detail=r.detail,
        auth_required=r.auth_required,
        http_server=r.http_server,
        server_hint=r.server_hint,
        cert_subject=cert.subject if cert else "",
        cert_issuer=cert.issuer if cert else "",
        cert_not_after=cert.not_after if cert else "",
        cert_fingerprint=cert.fingerprint if cert else "",
        cert_trusted_by_ca=cert.trusted_by_ca if cert else False,
        cert_error=cert.error if cert else "",
    )


@router.post(
    "/sniff",
    response_model=SniffResponse,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def sniff(payload: SniffRequest, db: DBSession = Depends(get_session)) -> SniffResponse:
    """Escuta o broker por alguns segundos e devolve os ramos de tópico existentes.

    É o que permite escolher o tópico em vez de digitar um palpite. Usa sessão
    descartável — não encosta na assinatura durável do coletor.
    """
    import asyncio

    from middleware_monitor.domain.mqtt.address import topic_tree

    host: str | None = payload.host
    port: int | None = payload.port
    transport: str = payload.transport
    tls: bool = payload.tls
    ws_path: str | None = payload.ws_path
    username, senha = payload.username, payload.password or ""
    tls_verify, fingerprint = payload.tls_verify, payload.tls_fingerprint

    if payload.broker_id is not None:
        broker = repo.get_broker(db, payload.broker_id)
        if broker is None:
            raise HTTPException(status_code=404, detail="broker_not_found")
        host, port = broker.host, broker.port
        transport, tls, ws_path = broker.transport, broker.tls, broker.ws_path
        username = broker.username
        senha = senha or repo.load_broker_password(broker)
        tls_verify, fingerprint = broker.tls_verify, broker.tls_fingerprint

    if not host or not port:
        raise HTTPException(status_code=422, detail="endpoint_required")

    if payload.filter.strip():
        filtros = [normalize_topic_filter(payload.filter)]
        if erro := validate_topic_filter(filtros[0]):
            raise HTTPException(status_code=422, detail=f"topic_invalid:{erro}")
    else:
        filtros = list(DEFAULT_SNIFF_FILTERS)

    result = await asyncio.to_thread(
        sniff_topics,
        MqttEndpoint(host=host, port=port, transport=transport, tls=tls, ws_path=ws_path),
        MqttAuth(username=username, password=senha),
        seconds=payload.seconds,
        filters=filtros,
        tls_verify=tls_verify,
        tls_fingerprint=fingerprint,
    )
    branches = [
        SniffBranch(
            filter=ramo.filter,
            messages=ramo.messages,
            topics=ramo.topics,
            samples=ramo.samples,
            # "reconhecido" = o payload tem o formato de status de ramal; é o
            # ramo que a tela já deixa marcado para o operador.
            recognized=any(s in result.recognized for s in ramo.samples),
            sample_payload=result.samples.get(ramo.samples[0], "")[:400] if ramo.samples else "",
        )
        for ramo in topic_tree(result.counts)
    ]
    log.info(
        "mqtt_sniff",
        host=host, port=port, seconds=payload.seconds, filter_used=result.filter_used or None,
        denied=result.denied or None, messages=result.messages, topics=len(result.counts),
        error=result.error or None,
    )
    return SniffResponse(
        success=not result.error,
        seconds=result.seconds,
        messages=result.messages,
        topics=len(result.counts),
        branches=branches,
        filter_used=result.filter_used,
        denied=result.denied,
        error=result.error,
    )


# ── estado ───────────────────────────────────────────────────────────────────


@router.get("/status", response_model=StatusOut)
def status(
    _user: User = Depends(get_current_user), db: DBSession = Depends(get_session),
) -> StatusOut:
    snap = get_ingestor().status()
    cfg = load_config(db)
    brokers = repo.list_brokers(db)
    return StatusOut(
        running=bool(snap["running"]),
        configured=bool(brokers),
        started_at=snap["started_at"],
        brokers=[BrokerStatus(**b) for b in snap["brokers"]],
        received=int(snap["received"]),
        persisted=int(snap["persisted"]),
        dropped=int(snap["dropped"]),
        persist_failures=int(snap["persist_failures"]),
        queue_depth=int(snap["queue_depth"]),
        per_minute=int(snap["per_minute"]),
        last_message_at=snap["last_message_at"] or repo.last_message_at(db),
        avg_lag_seconds=snap["avg_lag_seconds"],
        clock_outliers=int(snap["clock_outliers"]),
        stored_messages=repo.count_messages(db),
        stored_payload_bytes=repo.payload_bytes_total(db),
        retention_days=cfg.mqtt_message_retention_days,
        retention_max_mb=cfg.mqtt_message_max_mb,
    )


# ── ledger ───────────────────────────────────────────────────────────────────


def _parse_span(value: str) -> timedelta:
    m = _SPAN_RE.match(value.strip().lower())
    if not m:
        raise HTTPException(status_code=422, detail="invalid_span")
    n, unidade = int(m.group(1)), m.group(2)
    if unidade == "m":
        return timedelta(minutes=n)
    if unidade == "h":
        return timedelta(hours=n)
    return timedelta(days=n)


def _window(
    last: str | None, since: datetime | None, until: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    """Janela consultada. ``last`` (15m, 24h, 7d) tem precedência sobre datas."""
    if last:
        fim = _now()
        return fim - _parse_span(last), fim
    return _naive_utc(since), _naive_utc(until)


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _message_out(msg: MqttMessage) -> MessageOut:
    return MessageOut(
        id=msg.id,
        received_at=msg.received_at,
        topic=msg.topic,
        ramal=msg.ramal,
        qos=msg.qos,
        retained=msg.retained,
        b64=msg.b64,
        truncated=msg.truncated,
        pinned=msg.pinned,
        payload_bytes=msg.payload_bytes,
        event_at=msg.event_at,
        preview=" ".join((msg.payload or "").split())[:PREVIEW_CHARS],
    )


@router.get("/messages", response_model=MessagesPage)
def list_messages(
    last: str | None = Query(default=None, description="janela recente: 15m, 1h, 24h, 7d"),
    since: datetime | None = None,
    until: datetime | None = None,
    topic: str | None = Query(default=None, max_length=512),
    ramal: str | None = Query(default=None, max_length=64),
    contains: str | None = Query(default=None, max_length=200),
    pinned: bool = False,
    after_id: int | None = Query(default=None, ge=0, description="modo ao vivo: o que chegou depois"),
    before_id: int | None = Query(default=None, ge=1, description="paginação: mais antigas que"),
    limit: int = Query(default=100, ge=1, le=1000),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> MessagesPage:
    ini, fim = _window(last, since, until)
    filtro = normalize_topic_filter(topic) if topic else None
    if filtro and (erro := validate_topic_filter(filtro)):
        raise HTTPException(status_code=422, detail=f"topic_invalid:{erro}")
    resultado = repo.search_messages(
        db,
        since=ini,
        until=fim,
        topic_filter=filtro,
        ramal=ramal,
        contains=contains,
        pinned_only=pinned,
        after_id=after_id,
        before_id=before_id,
        limit=limit,
        # No modo ao vivo a página sai em ordem cronológica: são as novas.
        newest_first=after_id is None,
    )
    return MessagesPage(
        items=[_message_out(m) for m in resultado.items],
        total=resultado.total,
        limit=limit,
        truncated=resultado.total > len(resultado.items),
        exact_total=resultado.exact_total,
    )


@router.get("/messages/{message_id}", response_model=MessageDetail)
def get_message(
    message_id: int,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> MessageDetail:
    msg = db.get(MqttMessage, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="message_not_found")
    base = _message_out(msg)
    broker = repo.get_broker(db, msg.broker_id) if msg.broker_id else None
    return MessageDetail(
        **base.model_dump(),
        payload=msg.payload,
        pretty=_pretty(msg.payload),
        broker=broker.nome if broker else "",
    )


def _pretty(payload: str) -> str | None:
    try:
        return json.dumps(json.loads(payload), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        return None


@router.post(
    "/messages/{message_id}/pin",
    dependencies=[Depends(require_csrf)],
)
def pin_message(
    message_id: int,
    payload: PinRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, bool]:
    """Fixa a mensagem como evidência — a retenção nunca apaga o que está fixado."""
    if not repo.set_pinned(db, message_id, payload.pinned):
        raise HTTPException(status_code=404, detail="message_not_found")
    db.commit()
    log.info(
        "mqtt_message_pinned",
        message_id=message_id, pinned=payload.pinned, operador=user.username,
    )
    return {"pinned": payload.pinned}


@router.get("/messages/{message_id}/comprovante")
def download_comprovante(
    message_id: int,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> Response:
    """Comprovante em texto: a mensagem como chegou + a cobertura no minuto dela.

    Serve para anexar em chamado/e-mail sem precisar explicar a tela.
    """
    msg = db.get(MqttMessage, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="message_not_found")
    broker = repo.get_broker(db, msg.broker_id) if msg.broker_id else None
    janela_ini = msg.received_at - timedelta(minutes=1)
    janela_fim = msg.received_at + timedelta(minutes=1)
    cobertura = _coverage(db, janela_ini, janela_fim)

    linhas = [
        "COMPROVANTE DE RECEBIMENTO DE MENSAGEM MQTT",
        "=" * 52,
        f"Registro nº ............ {msg.id}",
        f"Recebido em (local) .... {as_local_str(msg.received_at)}",
        f"Recebido em (UTC) ...... {iso_utc(msg.received_at)}",
        f"Tópico ................. {msg.topic}",
        f"Ramal .................. {msg.ramal or '-'}",
        f"Hora do evento no PBX .. {as_local_str(msg.event_at) or '-'}",
        f"QoS .................... {msg.qos}",
        f"Retained ............... {'sim' if msg.retained else 'não'}",
        f"Tamanho do payload ..... {msg.payload_bytes} bytes"
        + (" (truncado na gravação)" if msg.truncated else ""),
        f"Broker ................. {broker.nome if broker else '-'}"
        + (f" ({broker.host}:{broker.port})" if broker else ""),
        f"Fixado como evidência .. {'sim' if msg.pinned else 'não'}",
        "",
        f"Cobertura do coletor no minuto do registro: {cobertura.coverage_pct:.1f}%",
        "",
        "PAYLOAD (como recebido" + (", em base64" if msg.b64 else "") + "):",
        "-" * 52,
        msg.payload,
        "-" * 52,
        f"Emitido por Middleware USCall Monitor {__version__} em "
        f"{as_local_str(_now())} (hora local do servidor).",
    ]
    corpo = "\r\n".join(linhas)
    nome = f"comprovante-mqtt-{msg.id}.txt"
    return Response(
        content=corpo,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


# ── cobertura ────────────────────────────────────────────────────────────────


def _coverage(db: DBSession, since: datetime, until: datetime) -> Coverage:
    anterior = repo.last_connection_event_before(db, since)
    eventos = [
        (e.timestamp, e.state, e.detail or "")
        for e in repo.list_connection_events(db, since=since, until=until, limit=2000)
    ]
    return compute_coverage(
        eventos, since, until, state_before=anterior.state if anterior else None,
    )


@router.get("/coverage", response_model=CoverageOut)
def coverage(
    last: str | None = Query(default=None),
    since: datetime | None = None,
    until: datetime | None = None,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> CoverageOut:
    """Quanto do período o coletor esteve ouvindo — a prova negativa.

    Sem isso, "não há mensagem no período" não diz se ninguém publicou ou se
    ninguém estava escutando.
    """
    ini, fim = _window(last or "24h", since, until)
    assert ini is not None and fim is not None
    cov = _coverage(db, ini, fim)
    return CoverageOut(
        since=ini,
        until=fim,
        covered_seconds=cov.covered_seconds,
        total_seconds=cov.total_seconds,
        coverage_pct=cov.coverage_pct,
        gaps=[
            CoverageGap(
                started_at=g.started_at, ended_at=g.ended_at,
                seconds=g.seconds, detail=g.detail,
            )
            for g in cov.gaps
        ],
        unknown=cov.unknown,
    )
