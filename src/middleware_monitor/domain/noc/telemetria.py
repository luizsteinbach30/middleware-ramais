"""A telemetria para o NOC — Fase 1 (``docs/AGENTE-NOC.md``, itens 5, 6 e 7).

Tudo o que os webhooks ``extensions`` e ``devices`` mandavam ate a v2.13.0
(o modulo saiu na v2.14.0), e mais: amostras
de ping, transições de telefonia do MQTT local, relatórios de aplicação e o
perfil que cada ambiente diz que a linha deve ter.

Etapa I3 do NOC (itens 11 e 12): o retrato dos ambientes com ``id`` e a config
padrão por lista branca, e o estado do coletor MQTT — ver ``retrato.py``.

Expansão do NOC, Plano 0 (item 20, CONTRATO §13.3): ``equipamentos`` (retrato que
**só viaja quando muda** — parte ausente é "não mudou", lista vazia é "nenhum"),
``amostrasDeVerificacao`` e ``eventosDeEquipamento`` (por cursor).

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
    AmostraDeVerificacao,
    Collection,
    Device,
    DevicePing,
    EventoDeEquipamento,
    ExtensionApplyRun,
    ExtensionLine,
    ExtensionStatusEvent,
    MqttConnectionEvent,
    UscallServer,
)
from middleware_monitor.domain.equipamentos import lista as equipamentos
from middleware_monitor.domain.noc import estado, retrato

# Continua 1: tudo o que a I3 acrescentou é campo novo num JSON que o NOC lê
# chave a chave — um NOC que ainda não conhece ``ambientes`` só não o projeta.
VERSAO_DO_CONTRATO = 1
LIMITE_AMOSTRAS = 5000
LIMITE_EVENTOS = 2000
LIMITE_APLICACOES = 50
LIMITE_AMOSTRAS_DE_VERIFICACAO = 5000
LIMITE_EVENTOS_DE_EQUIPAMENTO = 2000
# Retrato de equipamentos que não mudou vai de novo depois disto, de segurança
# (e leva a última leitura de cada verificação, que o hash ignora).
RETRATO_DE_SEGURANCA = timedelta(minutes=15)
# Ao enrolar, a série começa uma hora para trás — não com todo o histórico
# guardado (30 dias de ping de uma loja grande são milhões de linhas).
JANELA_INICIAL_AMOSTRAS = timedelta(hours=1)
JANELA_INICIAL_APLICACOES = timedelta(days=7)
JANELA_INICIAL_CONEXOES = timedelta(hours=24)

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
    if atual.get("conexoes") is None:
        atual["conexoes"] = _max_id_antes(
            db, MqttConnectionEvent.id, MqttConnectionEvent.timestamp, agora - JANELA_INICIAL_CONEXOES
        )
    if atual.get("amostrasEquipamento") is None:
        atual["amostrasEquipamento"] = _max_id_antes(
            db, AmostraDeVerificacao.id, AmostraDeVerificacao.em, agora - JANELA_INICIAL_AMOSTRAS
        )
    if atual.get("eventosEquipamento") is None:
        atual["eventosEquipamento"] = _max_id_antes(
            db, EventoDeEquipamento.id, EventoDeEquipamento.em, agora - JANELA_INICIAL_CONEXOES
        )
    return {k: int(v or 0) for k, v in atual.items()}


def montar_lote(
    db: DBSession, cur: dict[str, int], *, coletor_ao_vivo: dict[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, int], bool]:
    """Um lote e os cursores que ele leva. ``mais`` diz se alguma fonte bateu no limite.

    ``coletor_ao_vivo`` é o ``status()`` do coletor MQTT em memória; sem ele, o
    estado de cada broker sai do último evento gravado.
    """
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
                # O nome é para a tela; o id é a identidade — renomear lá não separa o histórico.
                "ambienteId": r.environment_id,
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
            "ambienteId": ln.environment_id,
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

    conexoes, novos["conexoes"], mais_conexoes = retrato.conexoes_mqtt(db, cur["conexoes"])
    mais = mais or mais_conexoes

    amostras_eq, novos["amostrasEquipamento"], mais_amostras_eq = _amostras_de_verificacao(
        db, cur.get("amostrasEquipamento", 0)
    )
    eventos_eq, novos["eventosEquipamento"], mais_eventos_eq = _eventos_de_equipamento(
        db, cur.get("eventosEquipamento", 0)
    )
    mais = mais or mais_amostras_eq or mais_eventos_eq

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
        # Etapa I3 do NOC — retratos completos a cada lote, menos ``conexoesMqtt``, que anda por cursor.
        "ambientes": retrato.ambientes(db),
        "coletor": retrato.coletor(db, coletor_ao_vivo),
        "conexoesMqtt": conexoes,
        "mensagensPorHora": retrato.mensagens_por_hora(db),
        # Expansão do NOC, Plano 0 — por cursor.
        "amostrasDeVerificacao": amostras_eq,
        "eventosDeEquipamento": eventos_eq,
    }
    retrato_eq = equipamentos.retrato(db)
    if equipamentos_devem_ir(db, retrato_eq):
        lote["equipamentos"] = retrato_eq
    return lote, novos, mais


def equipamentos_devem_ir(db: DBSession, retrato_eq: list[dict[str, Any]]) -> bool:
    """O retrato dos equipamentos só viaja quando muda (ADR 0026). Vai também no
    primeiro lote depois de enrolar (o hash é apagado), no primeiro depois de o NOC
    voltar, e de segurança a cada ``RETRATO_DE_SEGURANCA``."""
    entregue, em = estado.retrato_de_equipamentos_entregue(db)
    if entregue is None or em is None or entregue != equipamentos.sha256_do_retrato(retrato_eq):
        return True
    if estado.carregar(db).telemetria_detalhe:
        return True
    return _agora() - em >= RETRATO_DE_SEGURANCA


def _amostras_de_verificacao(db: DBSession, cursor: int) -> tuple[list[dict[str, Any]], int, bool]:
    linhas = db.scalars(
        select(AmostraDeVerificacao)
        .where(AmostraDeVerificacao.id > cursor)
        .order_by(AmostraDeVerificacao.id)
        .limit(LIMITE_AMOSTRAS_DE_VERIFICACAO)
    ).all()
    itens = [
        {
            "id": a.id,
            "verificacao": a.verificacao_id,
            "em": _hora(a.em),
            "ok": bool(a.ok),
            "valor": a.valor if a.ok else None,
        }
        for a in linhas
    ]
    novo = int(linhas[-1].id) if linhas else cursor
    return itens, novo, len(linhas) == LIMITE_AMOSTRAS_DE_VERIFICACAO


def _eventos_de_equipamento(db: DBSession, cursor: int) -> tuple[list[dict[str, Any]], int, bool]:
    linhas = db.scalars(
        select(EventoDeEquipamento)
        .where(EventoDeEquipamento.id > cursor)
        .order_by(EventoDeEquipamento.id)
        .limit(LIMITE_EVENTOS_DE_EQUIPAMENTO)
    ).all()
    itens = [
        {
            "id": e.id,
            "equipamento": e.equipamento_id,
            "verificacao": e.verificacao_id,
            "tipo": e.tipo,
            "de": e.de,
            "para": e.para,
            # A hora crua do agente: é a identidade do evento no NOC (§13.5).
            "em": _hora(e.em),
            "detalhe": e.detalhe,
        }
        for e in linhas
    ]
    novo = int(linhas[-1].id) if linhas else cursor
    return itens, novo, len(linhas) == LIMITE_EVENTOS_DE_EQUIPAMENTO
