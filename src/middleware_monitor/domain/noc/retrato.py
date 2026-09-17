"""O retrato do configurador e do coletor MQTT para o NOC — ``docs/AGENTE-NOC.md``, itens 11 e 12.

Etapa I3 do NOC: o cliente é a casa, a loja de cada agente vem dos ambientes dele,
e o NOC espelha o configurador de ramais. Para isso o ambiente precisa viajar
**como entidade**, com o ``id`` (o slug), e não só como o nome repetido em cada linha.

**A config padrão sai por lista branca, nunca por lista negra.** A senha SIP e as
senhas web são cifradas em repouso, mas ``merged_config_padrao`` as devolve
decifradas: um retrato "tudo menos as senhas" mandaria ao NOC a próxima chave de
segredo que alguém acrescentar. Toda chave de ``default_config_padrao`` está em
exatamente uma das três listas abaixo, e o teste quebra quando aparece uma que não
está em nenhuma.

**Por que a config vai como lista de ``{chave, valor}``, e não como objeto:** o NOC
tira de todo lote as chaves com nome de segredo, em qualquer profundidade
(``dominio/telemetria/telemetria.ts``). Um ``{"web_password": {"definida": true}}``
sumiria na entrada, e com ele a informação de que a senha existe. Com o nome da
chave como *valor*, o filtro de lá continua valendo para o que importa e a
informação chega.

**O coletor MQTT diz se estava ouvindo.** Sem isso, "nenhuma mensagem nesta hora"
no NOC não separa "ninguém publicou" de "o coletor estava fora do ar" — a mesma
lição da prova de cobertura deste repositório (``domain/mqtt/coverage.py``). Por
isso hora sem conexão **não vai como zero**: não vai.

O estado atual do coletor vai no lote, e não no heartbeat, embora o item 12 do
``AGENTE-NOC.md`` o pusesse no heartbeat. O heartbeat do NOC é um DTO fechado
(``forbidNonWhitelisted``): um campo novo ali derrubaria o agente em todo NOC que
ainda não o conhece. O lote é JSON livre e já é retrato a cada minuto.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import selectinload

from middleware_monitor.core.models import (
    ExtensionEnvironment,
    ExtensionLine,
    MqttBroker,
    MqttConnectionEvent,
    MqttMessage,
)
from middleware_monitor.domain.extension_configurator import time_settings
from middleware_monitor.domain.extension_configurator.defaults import CHAVES_SECRETAS
from middleware_monitor.domain.extension_configurator.repository import merged_config_padrao
from middleware_monitor.domain.extension_configurator.service import compute_statuses, status_resumo
from middleware_monitor.domain.extension_configurator.softkeys import softkey_catalog_for
from middleware_monitor.domain.mqtt.coverage import UP_STATES, compute_coverage

__all__ = [
    "CONFIG_COM_VALOR",
    "CONFIG_NAO_SAI",
    "CONFIG_SO_DEFINIDA",
    "LIMITE_CONEXOES",
    "ambientes",
    "coletor",
    "conexoes_mqtt",
    "config_para_o_noc",
    "mensagens_por_hora",
    "secoes_do_modelo",
]

# --- A config padrão: as três listas ---------------------------------------------------

# Vão com valor. São também as únicas que a edição central (item 13) poderá mudar.
CONFIG_COM_VALOR: tuple[str, ...] = (
    "register_expiration",
    "sip_account",
    "timezone_mode",
    "timezone",
    "ntp_mode",
    "ntp_server",
    "validar_conectividade",
    "verificar_registro_sip",
    "keylock_enable",
    "keylock_timeout",
    "hotline_enable",
    "hotline_number",
    "hotline_time",
    "function_keys",
)

# Vão só como ``{"definida": true|false}``. O usuário web entra junto com as senhas
# porque é metade da mesma credencial.
CONFIG_SO_DEFINIDA: tuple[str, ...] = (*CHAVES_SECRETAS, "web_user", "nova_web_user")

# Não saem. ``sip_server`` e ``sip_transport`` dizem para onde o aparelho registra —
# para o aparelho é rede, e rede nunca viaja para ser editada de fora. Os idiomas
# ficam fora até o TELAS §14 pedir: chave nova no espelho é decisão, não sobra.
CONFIG_NAO_SAI: tuple[str, ...] = ("sip_server", "sip_transport", "web_language", "lcd_language")

# Seções de todo modelo; as outras dependem do catálogo do fabricante — o NOC não
# oferece o que o aparelho não tem (TELAS §14).
SECOES_BASE: tuple[str, ...] = ("sip", "hora", "credenciais", "validacao")

# --- Coletor MQTT ----------------------------------------------------------------------

LIMITE_CONEXOES = 2000
JANELA_MENSAGENS = timedelta(hours=24)


def _hora(valor: datetime | None) -> str | None:
    """UTC sem fuso, como o resto do lote."""
    return valor.replace(tzinfo=None).isoformat(timespec="seconds") if valor else None


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def secoes_do_modelo(modelo: str) -> list[str]:
    """As seções que a tela de config padrão daqui mostra para o modelo.

    Mesma decisão de ``extension_configurator_config.js``: avançadas só Intelbras;
    teclas e hotline pelo catálogo do fabricante.
    """
    catalogo = softkey_catalog_for(modelo)
    secoes = list(SECOES_BASE)
    if str(modelo).lower().startswith("intelbras"):
        secoes.append("avancadas")
    if catalogo.get("hotline"):
        secoes.append("hotline")
    if catalogo.get("softkeys", True):
        secoes.append("teclas")
    return secoes


def config_para_o_noc(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """A config padrão por lista branca: ``[{chave, valor}]`` e ``[{chave, definida}]``.

    Recebe a config **decifrada** (é o que ``merged_config_padrao`` devolve) e só
    deixa passar valor das chaves de ``CONFIG_COM_VALOR``.
    """
    campos: list[dict[str, Any]] = [{"chave": k, "valor": cfg[k]} for k in CONFIG_COM_VALOR if k in cfg]
    campos.extend({"chave": k, "definida": bool(cfg.get(k))} for k in CONFIG_SO_DEFINIDA)
    return campos


def _ultima_aplicacao(env: ExtensionEnvironment) -> dict[str, Any] | None:
    terminadas = [r for r in env.runs if r.finished_at is not None]
    if not terminadas:
        return None
    r = max(terminadas, key=lambda run: (run.started_at, run.id))
    return {
        "id": r.id,
        "inicio": _hora(r.started_at),
        "fim": _hora(r.finished_at),
        "ok": r.ok,
        "total": r.total,
    }


def ambientes(db: DBSession) -> list[dict[str, Any]]:
    """Retrato completo dos ambientes: o que sumiu daqui foi apagado aqui.

    As linhas vão campo a campo (lista de permissão): **sem** ``senha_sip``,
    ``user_auth`` e ``servidor_sip``.
    """
    envs = db.scalars(
        select(ExtensionEnvironment)
        .options(
            selectinload(ExtensionEnvironment.lines).selectinload(ExtensionLine.device),
            selectinload(ExtensionEnvironment.runs),
        )
        .order_by(ExtensionEnvironment.nome, ExtensionEnvironment.id)
    ).all()
    retrato: list[dict[str, Any]] = []
    for env in envs:
        linhas = sorted(env.lines, key=lambda ln: (ln.posicao, ln.numero_ramal, ln.id))
        # Status fino por linha (inclui ``outdated`` e ``invalid``), o mesmo da planilha daqui.
        status_por_id = {s["id"]: s["status"] for s in compute_statuses(env, linhas)}
        contagem: dict[str, int] = {}
        for s in status_por_id.values():
            contagem[s] = contagem.get(s, 0) + 1
        cfg = merged_config_padrao(env)
        hora = time_settings.resolve(cfg)
        retrato.append(
            {
                "id": env.id,
                "nome": env.nome,
                "modelo": env.modelo_telefone,
                "ramais": len(linhas),
                "vinculados": sum(1 for ln in linhas if ln.device_id is not None),
                "situacao": status_resumo(linhas)["agregado"],
                "contagemPorStatus": contagem,
                "ultimaAplicacao": _ultima_aplicacao(env),
                "atualizadoEm": _hora(env.updated_at),
                "configPadrao": config_para_o_noc(cfg),
                # O que o telefone recebe de fato, e de onde veio — é o "herdado" da tela do NOC.
                "hora": {
                    "timezone": hora.timezone,
                    "ntpServer": hora.ntp_server,
                    "origemFuso": hora.origem_tz,
                    "origemNtp": hora.origem_ntp,
                },
                "secoes": secoes_do_modelo(env.modelo_telefone),
                "linhas": [
                    {
                        "posicao": ln.posicao,
                        "ramal": ln.numero_ramal,
                        "nomeVisivel": ln.nome_visivel,
                        "numeroAbreviado": ln.numero_abreviado,
                        "ip": ln.ip,
                        "deviceId": ln.device_id,
                        # O NOC conhece o aparelho pelo ramal do retrato de dispositivos.
                        "dispositivo": ln.device.name if ln.device is not None else None,
                        "status": status_por_id.get(ln.id, "pending"),
                        "ultimoModelo": ln.ultimo_modelo,
                        "ultimoMac": ln.ultimo_mac,
                        "ultimaAplicacao": _hora(ln.ultima_aplicacao),
                        "ultimoErro": ln.ultimo_erro,
                    }
                    for ln in linhas
                ],
            }
        )
    return retrato


# --- Coletor MQTT ----------------------------------------------------------------------


def _estado_do_coletor(state: str | None) -> str:
    return "conectado" if state in UP_STATES else "desconectado"


def _ultimo_evento(
    db: DBSession, broker_id: int, antes: datetime | None = None
) -> MqttConnectionEvent | None:
    """O último evento que vale para o broker: os dele e os do processo inteiro
    (``startup``/``stopped`` não têm broker)."""
    stmt = select(MqttConnectionEvent).where(
        or_(MqttConnectionEvent.broker_id == broker_id, MqttConnectionEvent.broker_id.is_(None))
    )
    if antes is not None:
        stmt = stmt.where(MqttConnectionEvent.timestamp < antes)
    return db.scalar(
        stmt.order_by(MqttConnectionEvent.timestamp.desc(), MqttConnectionEvent.id.desc()).limit(1)
    )


def coletor(db: DBSession, ao_vivo: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """O estado de cada broker agora.

    ``ao_vivo`` é o ``status()`` do coletor em memória (o processo que ouve);
    sem ele — teste, ou coletor ainda não iniciado — o estado vem do último evento
    gravado. Nenhum broker ligado vira uma entrada ``sem_broker``: lista vazia
    seria indistinguível de "este agente não informa o coletor".
    """
    agora = _agora()
    brokers = db.scalars(select(MqttBroker).where(MqttBroker.enabled.is_(True)).order_by(MqttBroker.id)).all()
    if not brokers:
        return [
            {
                "brokerId": None,
                "broker": None,
                "endereco": None,
                "estado": "sem_broker",
                "desde": None,
                # Texto é da tela do NOC; o dado é o estado.
                "detalhe": None,
                "mensagens24h": 0,
                "ultimaMensagemEm": _hora(db.scalar(select(func.max(MqttMessage.received_at)))),
            }
        ]

    por_broker = {b["broker_id"]: b for b in (ao_vivo or {}).get("brokers", [])}
    desde_24h = agora - JANELA_MENSAGENS
    contagens: dict[int | None, int] = {
        broker_id: int(qtd)
        for broker_id, qtd in db.execute(
            select(MqttMessage.broker_id, func.count(MqttMessage.id))
            .where(MqttMessage.received_at >= desde_24h)
            .group_by(MqttMessage.broker_id)
        ).all()
    }
    ultimas: dict[int | None, datetime | None] = {
        broker_id: ultima
        for broker_id, ultima in db.execute(
            select(MqttMessage.broker_id, func.max(MqttMessage.received_at)).group_by(MqttMessage.broker_id)
        ).all()
    }

    saida: list[dict[str, Any]] = []
    for b in brokers:
        vivo = por_broker.get(b.id)
        evento = _ultimo_evento(db, b.id)
        if vivo is not None:
            estado = _estado_do_coletor(vivo.get("state"))
            detalhe = vivo.get("detail") or None
            desde = vivo.get("connected_since") if estado == "conectado" else None
        else:
            estado = _estado_do_coletor(evento.state if evento else None)
            # Só o que o ledger gravou; "sem evento" é ``desde`` nulo, não uma frase inventada aqui.
            detalhe = evento.detail if evento else None
            desde = None
        if desde is None and evento is not None:
            desde = evento.timestamp
        saida.append(
            {
                "brokerId": b.id,
                "broker": b.nome,
                # Só host e porta: usuário e senha do broker nunca entram no endereço.
                "endereco": f"{b.host}:{b.port}",
                "estado": estado,
                "desde": _hora(desde),
                "detalhe": detalhe,
                "mensagens24h": int(contagens.get(b.id, 0)),
                "ultimaMensagemEm": _hora(ultimas.get(b.id)),
            }
        )
    return saida


def conexoes_mqtt(db: DBSession, cursor: int) -> tuple[list[dict[str, Any]], int, bool]:
    """O histórico de conexão novo desde o cursor: ``(eventos, novo_cursor, mais)``.

    O ponto de partida do primeiro envio (24 h para trás, não o histórico inteiro)
    é decidido com os outros cursores, em ``telemetria.cursores``.
    """
    linhas = db.scalars(
        select(MqttConnectionEvent)
        .where(MqttConnectionEvent.id > cursor)
        .order_by(MqttConnectionEvent.id)
        .limit(LIMITE_CONEXOES)
    ).all()
    eventos = [
        {
            "id": e.id,
            "brokerId": e.broker_id,
            "em": _hora(e.timestamp),
            # startup · connected · subscribed · disconnected · error · stopped — como o ledger grava.
            "estado": e.state,
            "detalhe": e.detail,
        }
        for e in linhas
    ]
    novo = int(linhas[-1].id) if linhas else cursor
    return eventos, novo, len(linhas) == LIMITE_CONEXOES


def _inicio_da_hora(momento: datetime) -> datetime:
    return momento.replace(minute=0, second=0, microsecond=0)


def mensagens_por_hora(db: DBSession, agora: datetime | None = None) -> list[dict[str, Any]]:
    """Mensagens por broker e por hora nas últimas 24 h, **só das horas em que o
    coletor ouviu**. Hora sem nenhum segundo de cobertura não entra — nem como zero.

    ``coberturaPct`` diz quanto da hora foi ouvido: 12 mensagens em 100% não é o
    mesmo dado que 12 mensagens em 10%.
    """
    agora = agora or _agora()
    fim_da_hora_atual = _inicio_da_hora(agora) + timedelta(hours=1)
    inicio = fim_da_hora_atual - JANELA_MENSAGENS
    brokers = db.scalars(select(MqttBroker).order_by(MqttBroker.id)).all()
    if not brokers:
        return []

    hora_sql = func.strftime("%Y-%m-%d %H:00:00", MqttMessage.received_at)
    contagens: dict[tuple[int | None, str], int] = {
        (broker_id, hora): int(qtd)
        for broker_id, hora, qtd in db.execute(
            select(MqttMessage.broker_id, hora_sql, func.count(MqttMessage.id))
            .where(MqttMessage.received_at >= inicio)
            .group_by(MqttMessage.broker_id, hora_sql)
        ).all()
    }

    saida: list[dict[str, Any]] = []
    for b in brokers:
        anterior = _ultimo_evento(db, b.id, antes=inicio)
        eventos = db.scalars(
            select(MqttConnectionEvent)
            .where(
                or_(MqttConnectionEvent.broker_id == b.id, MqttConnectionEvent.broker_id.is_(None)),
                MqttConnectionEvent.timestamp >= inicio,
                MqttConnectionEvent.timestamp <= agora,
            )
            .order_by(MqttConnectionEvent.timestamp, MqttConnectionEvent.id)
        ).all()
        estado_antes = anterior.state if anterior else None
        hora = inicio
        while hora < fim_da_hora_atual:
            fim = min(hora + timedelta(hours=1), agora)
            if fim <= hora:
                break
            dentro = [(e.timestamp, e.state, e.detail or "") for e in eventos if hora <= e.timestamp < fim]
            cobertura = compute_coverage(dentro, hora, fim, state_before=estado_antes)
            if dentro:
                estado_antes = dentro[-1][1]
            if cobertura.covered_seconds > 0:
                saida.append(
                    {
                        "brokerId": b.id,
                        "broker": b.nome,
                        "hora": _hora(hora),
                        "mensagens": contagens.get((b.id, hora.strftime("%Y-%m-%d %H:00:00")), 0),
                        "coberturaPct": cobertura.coverage_pct,
                    }
                )
            hora += timedelta(hours=1)
    return saida
