"""A telemetria para o NOC — Fase 1 (``docs/AGENTE-NOC.md``, itens 5, 6 e 7).

Tudo o que os webhooks ``extensions`` e ``devices`` mandavam, e mais: amostras
de ping, transições de telefonia do MQTT local, relatórios de aplicação e o
perfil que cada ambiente diz que a linha deve ter.

**Por cursor, não por "o que mudou desde o último envio".** Cada fonte tem um
cursor (o último id entregue) que só avança quando o NOC responde 202. NOC fora
do ar não perde nada — o cursor espera — e reenviar não duplica, porque o lote
leva ``Idempotency-Key`` e o NOC também deduplica por id de origem. É a regra da
casa: falha de dependência é espera, não estado final.

**O que nunca vai:** ``senha_sip``, ``user_auth``, senha web ou qualquer chave
com nome de segredo. As linhas do ambiente são lidas campo a campo (lista de
permissão); a linha crua do USCall passa por um filtro de nomes, e o NOC filtra
de novo na entrada.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import selectinload

from middleware_monitor.core.models import (
    Collection,
    Device,
    DevicePing,
    ExtensionApplyRun,
    ExtensionLine,
    ExtensionStatusEvent,
    UscallServer,
)
from middleware_monitor.domain.noc import estado

VERSAO_DO_CONTRATO = 1
LIMITE_AMOSTRAS = 5000
LIMITE_EVENTOS = 2000
LIMITE_APLICACOES = 50
# Ao enrolar, a série começa uma hora para trás — não com todo o histórico
# guardado (30 dias de ping de uma loja grande são milhões de linhas).
JANELA_INICIAL_AMOSTRAS = timedelta(hours=1)
JANELA_INICIAL_APLICACOES = timedelta(days=7)

_NOME_DE_SEGREDO = re.compile(r"senha|passw|secret|segredo|token|credencial|pwd|auth", re.IGNORECASE)


def _hora(valor: datetime | None) -> str | None:
    """UTC sem fuso, como o banco guarda — o NOC lê como UTC e corrige o relógio."""
    return valor.replace(tzinfo=None).isoformat(timespec="seconds") if valor else None


def sem_segredos(valor: Any) -> Any:
    if isinstance(valor, list):
        return [sem_segredos(v) for v in valor]
    if isinstance(valor, dict):
        return {k: sem_segredos(v) for k, v in valor.items() if not _NOME_DE_SEGREDO.search(str(k))}
    return valor


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _max_id_antes(db: DBSession, coluna_id: Any, coluna_tempo: Any, limite: datetime) -> int:
    return int(db.scalar(select(func.max(coluna_id)).where(coluna_tempo < limite)) or 0)


def cursores(db: DBSession) -> dict[str, int]:
    """Os cursores guardados — ou, na primeira vez, o ponto de partida."""
    atual = estado.carregar_cursores(db)
    agora = _agora()
    if atual.get("amostras") is None:
        atual["amostras"] = _max_id_antes(
            db, DevicePing.id, DevicePing.timestamp, agora - JANELA_INICIAL_AMOSTRAS
        )
    if atual.get("eventos") is None:
        atual["eventos"] = _max_id_antes(
            db, ExtensionStatusEvent.id, ExtensionStatusEvent.received_at, agora - JANELA_INICIAL_AMOSTRAS
        )
    if atual.get("aplicacoes") is None:
        atual["aplicacoes"] = _max_id_antes(
            db, ExtensionApplyRun.id, ExtensionApplyRun.started_at, agora - JANELA_INICIAL_APLICACOES
        )
    if atual.get("coleta") is None:
        atual["coleta"] = 0
    return {k: int(v or 0) for k, v in atual.items()}


def montar_lote(db: DBSession, cur: dict[str, int]) -> tuple[dict[str, Any], dict[str, int], bool]:
    """Um lote e os cursores que ele leva. ``mais`` diz se alguma fonte bateu no limite."""
    servidores = {s.id: s.nome for s in db.scalars(select(UscallServer)).all()}
    novos = dict(cur)
    mais = False

    dispositivos = [
        {
            "ramal": d.name,
            "ip": d.ip,
            "mac": d.mac,
            "modelo": d.model,
            "estadoLogico": d.logical_status,
            "estadoRede": d.network_status,
            "estadoTelefonia": d.telephony_status,
            "telefoniaEm": _hora(d.telephony_status_at),
            "latenciaMs": d.latency_ms,
            "ultimoPingEm": _hora(d.last_ping_at),
            "uscall": servidores.get(d.uscall_server_id) if d.uscall_server_id is not None else None,
        }
        for d in db.scalars(select(Device).order_by(Device.name)).all()
    ]

    linhas_ping = db.execute(
        select(DevicePing.id, Device.name, DevicePing.timestamp, DevicePing.online, DevicePing.latency_ms)
        .join(Device, Device.id == DevicePing.device_id)
        .where(DevicePing.id > cur["amostras"])
        .order_by(DevicePing.id)
        .limit(LIMITE_AMOSTRAS)
    ).all()
    amostras = [
        {"ramal": nome, "em": _hora(ts), "online": bool(online), "latenciaMs": lat if online else None}
        for _id, nome, ts, online, lat in linhas_ping
    ]
    if linhas_ping:
        novos["amostras"] = int(linhas_ping[-1][0])
        mais = mais or len(linhas_ping) == LIMITE_AMOSTRAS

    linhas_eventos = db.scalars(
        select(ExtensionStatusEvent)
        .where(ExtensionStatusEvent.id > cur["eventos"])
        .order_by(ExtensionStatusEvent.id)
        .limit(LIMITE_EVENTOS)
    ).all()
    eventos = [
        {
            "id": e.id,
            "ramal": e.ramal,
            "status": e.status,
            "statusBruto": e.status_raw,
            "em": _hora(e.event_at or e.received_at),
            "recebidoEm": _hora(e.received_at),
        }
        for e in linhas_eventos
    ]
    if linhas_eventos:
        novos["eventos"] = int(linhas_eventos[-1].id)
        mais = mais or len(linhas_eventos) == LIMITE_EVENTOS

    runs = db.scalars(
        select(ExtensionApplyRun)
        .where(ExtensionApplyRun.id > cur["aplicacoes"])
        .options(selectinload(ExtensionApplyRun.run_lines), selectinload(ExtensionApplyRun.environment))
        .order_by(ExtensionApplyRun.id)
        .limit(LIMITE_APLICACOES)
    ).all()
    aplicacoes: list[dict[str, Any]] = []
    for r in runs:
        # Relatório ainda rodando segura o cursor: avançar por cima dele o
        # perderia para sempre.
        if r.finished_at is None:
            break
        aplicacoes.append(
            {
                "id": r.id,
                "ambiente": r.environment.nome if r.environment else "?",
                "inicio": _hora(r.started_at),
                "fim": _hora(r.finished_at),
                "total": r.total,
                "ok": r.ok,
                "falha": r.falha,
                "forcado": r.forcado,
                "operador": r.operador,
                "linhas": [
                    {
                        "ramal": ln.numero_ramal,
                        "ip": ln.ip,
                        "antes": ln.status_antes,
                        "depois": ln.status_depois,
                        "erro": ln.erro,
                        "modelo": ln.modelo,
                        "registroSip": ln.registro_sip,
                    }
                    for ln in r.run_lines
                ],
            }
        )
        novos["aplicacoes"] = r.id
    mais = mais or len(aplicacoes) == LIMITE_APLICACOES

    # Lista de permissão, campo a campo: a linha do ambiente tem senha SIP.
    perfis = [
        {
            "ramal": ln.numero_ramal,
            "ambiente": ln.environment.nome,
            "modelo": ln.environment.modelo_telefone,
            "ip": ln.ip,
            "status": ln.ultimo_status,
            "aplicadoEm": _hora(ln.ultima_aplicacao),
            "hash": ln.ultimo_hash_aplicado,
            "erro": ln.ultimo_erro,
            "modeloDetectado": ln.ultimo_modelo,
            "macDetectado": ln.ultimo_mac,
        }
        for ln in db.scalars(select(ExtensionLine).options(selectinload(ExtensionLine.environment))).all()
        if ln.numero_ramal
    ]

    coleta = db.scalar(
        select(Collection)
        .where(Collection.type == "extensions", Collection.id > cur["coleta"])
        .order_by(Collection.id.desc())
        .limit(1)
    )
    ramais_uscall = None
    if coleta is not None:
        try:
            ramais_uscall = sem_segredos(json.loads(coleta.payload))
        except ValueError:
            ramais_uscall = None
        novos["coleta"] = coleta.id

    lote = {
        "versaoDoContrato": VERSAO_DO_CONTRATO,
        "lote": uuid.uuid4().hex,
        "geradoEm": _hora(_agora()),
        "dispositivos": dispositivos,
        "amostras": amostras,
        "eventos": eventos,
        "aplicacoes": aplicacoes,
        "perfis": perfis,
        "ramaisUscall": ramais_uscall,
    }
    return lote, novos, mais
