"""Do payload cru ao estado do ramal agora — a fase 3 do coletor.

O ledger guarda tudo como chegou; aqui a mensagem vira informação operacional:

* **transições** em ``extension_status_events`` — só entra o que mudou de
  verdade; o comprovante continua inteiro no ledger, apontado por ``message_id``.
  Medido contra o broker do cliente (2026-08-21, 243 ramais): esse publicador já
  fala só na mudança, então ~99% das mensagens viram transição e o filtro quase
  não corta nada **aqui**. Ele continua valendo por dois motivos que não dependem
  do publicador: a sessão durável entrega a fila acumulada quando o serviço volta
  (e reentregar não pode virar linha nova), e um publicador que varra
  periodicamente — que é o comportamento comum — repetiria o mesmo estado sem parar;
* **estado ao vivo** no ``Device`` (``telephony_status`` e, quando o registro no
  PBX muda, ``logical_status``), para o dashboard e o ping enxergarem o ramal
  em segundos em vez de esperar o ciclo de coleta REST de 60 s.

O estado corrente de cada ramal vive em memória (``RealtimeState``) e é
reidratado do banco no boot: é o que sustenta o painel ao vivo sem varrer
tabela a cada 2 s.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import Device, ExtensionStatusEvent
from middleware_monitor.domain.mqtt.parser import (
    ExtensionStatus,
    logical_from_status,
    normalize_status,
)

__all__ = [
    "RealtimeState",
    "Sample",
    "insert_transitions",
    "live_events",
    "purge_status_events",
    "ramal_timeline",
    "samples_from",
    "touch_devices",
]

log = get_logger("mqtt.realtime")

# Piso entre duas escritas no ``Device`` para o mesmo ramal quando o estado não
# mudou. Só tem efeito com publicador que reenvia o estado periodicamente; com um
# que fala apenas na mudança (o caso do broker do cliente), o caminho quase nunca
# dispara — e não faz falta, porque a coleta REST continua mantendo
# ``last_seen_at`` em dia.
TOUCH_INTERVAL = timedelta(seconds=60)

# Teto do cache em memória: instalações reais têm ~200 ramais; o limite existe
# só para um publicador defeituoso não crescer o processo indefinidamente.
MAX_TRACKED = 5_000


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(slots=True)
class Sample:
    """Um status já normalizado, pronto para virar transição."""

    ramal: str
    status: str  # canônico (ver parser.STATUS_ORDER)
    status_raw: str
    numero: str
    uniqueid: str | None
    event_at: datetime | None
    received_at: datetime
    call_started_at: datetime | None
    message_id: int | None = None

    @property
    def key(self) -> tuple[str, str, str | None, datetime | None]:
        """O que caracteriza "o mesmo estado".

        ``call_started_at`` (o campo ``duracao``) entra na chave porque duas
        chamadas seguidas para o mesmo número, sem passar por Disponivel entre
        elas, teriam status e número idênticos — e são duas chamadas.
        """
        return (self.status, self.numero, self.uniqueid, self.call_started_at)


def samples_from(
    statuses: list[ExtensionStatus], received_at: datetime, message_id: int | None,
) -> list[Sample]:
    return [
        Sample(
            ramal=s.ramal,
            status=normalize_status(s.status),
            status_raw=s.status,
            numero=s.numero,
            uniqueid=s.uniqueid,
            event_at=s.event_at,
            received_at=received_at,
            call_started_at=s.call_started_at,
            message_id=message_id,
        )
        for s in statuses
    ]


@dataclass(slots=True)
class _Tracked:
    key: tuple[str, str, str | None, datetime | None]
    status: str
    numero: str
    uniqueid: str | None
    since: datetime  # quando este estado começou
    last_seen: datetime  # última mensagem sobre o ramal, mudando ou não
    touched_at: datetime  # última escrita no Device


class RealtimeState:
    """Último estado conhecido de cada ramal + quem já foi gravado.

    Mora no coletor (instância única, ``workers=1``). O banco é a fonte da
    verdade histórica; este cache existe para (a) não gravar repetição e
    (b) responder o painel ao vivo sem consultar o banco a cada 2 s.
    """

    def __init__(self) -> None:
        self._by_ramal: dict[str, _Tracked] = {}
        self._primed = False
        # A gravação roda em ``asyncio.to_thread`` e a tela lê pelo threadpool
        # do FastAPI: sem o lock, um ``snapshot`` concorrente com um lote levanta
        # "dictionary changed size during iteration".
        self._lock = threading.Lock()

    # ── reidratação ──────────────────────────────────────────────────────────

    def prime(self, db: DBSession) -> None:
        """Recarrega o último estado de cada ramal do banco.

        Sem isso, o primeiro lote depois de um restart gravaria uma transição
        falsa para todo ramal — "mudou para Disponivel" quando nada mudou.
        """
        if self._primed:
            return
        ultimos = select(func.max(ExtensionStatusEvent.id)).group_by(
            ExtensionStatusEvent.ramal
        )
        linhas = db.scalars(
            select(ExtensionStatusEvent).where(ExtensionStatusEvent.id.in_(ultimos))
        ).all()
        with self._lock:
            for ev in linhas:
                self._by_ramal[ev.ramal] = _Tracked(
                    key=(ev.status, ev.numero or "", ev.uniqueid, ev.call_started_at),
                    status=ev.status,
                    numero=ev.numero or "",
                    uniqueid=ev.uniqueid,
                    since=ev.received_at,
                    last_seen=ev.received_at,
                    touched_at=ev.received_at,
                )
        self._primed = True
        log.info("mqtt_realtime_primed", ramais=len(self._by_ramal))

    # ── uso pelo coletor ─────────────────────────────────────────────────────

    def classify(self, samples: list[Sample]) -> tuple[list[Sample], list[Sample]]:
        """Separa o lote em (transições a gravar, ramais a tocar no Device).

        Um ramal pode aparecer nas duas listas, em nenhuma (repetição recente),
        ou só na segunda (mesmo estado, mas já passou o ``TOUCH_INTERVAL`` desde
        a última escrita no Device).
        """
        transicoes: list[Sample] = []
        toques: dict[str, Sample] = {}
        with self._lock:
            for s in samples:
                if not s.ramal:
                    continue
                atual = self._by_ramal.get(s.ramal)
                if atual is None or atual.key != s.key:
                    if atual is None and len(self._by_ramal) >= MAX_TRACKED:
                        # Publicador anômalo: para de acompanhar novos ramais em
                        # memória, mas segue gravando o ledger (que é a prova).
                        continue
                    transicoes.append(s)
                    toques[s.ramal] = s
                    self._by_ramal[s.ramal] = _Tracked(
                        key=s.key,
                        status=s.status,
                        numero=s.numero,
                        uniqueid=s.uniqueid,
                        since=s.received_at,
                        last_seen=s.received_at,
                        touched_at=s.received_at,
                    )
                    continue
                atual.last_seen = s.received_at
                if s.received_at - atual.touched_at >= TOUCH_INTERVAL:
                    atual.touched_at = s.received_at
                    toques[s.ramal] = s
        return transicoes, list(toques.values())

    def rollback(self, transicoes: list[Sample]) -> None:
        """Desfaz o cache quando a gravação do lote falhou.

        Sem isto, uma transição que não chegou ao banco ficaria marcada como
        já conhecida e nunca mais seria gravada — o ramal congelaria no painel.
        """
        with self._lock:
            for s in transicoes:
                self._by_ramal.pop(s.ramal, None)
        if transicoes:
            self._primed = False  # relê do banco no próximo lote

    # ── leitura para a tela ──────────────────────────────────────────────────

    def snapshot(self, now: datetime | None = None) -> list[dict[str, Any]]:
        agora = now or _now()
        with self._lock:
            itens = list(self._by_ramal.items())
        out = [
            {
                "ramal": ramal,
                "status": t.status,
                "numero": t.numero or None,
                "uniqueid": t.uniqueid,
                "since": t.since,
                "seconds_in_status": max(0, int((agora - t.since).total_seconds())),
                "last_seen_at": t.last_seen,
                "seconds_since_message": max(0, int((agora - t.last_seen).total_seconds())),
            }
            for ramal, t in itens
        ]
        out.sort(key=lambda r: str(r["ramal"]).zfill(12))
        return out

    def counts(self) -> dict[str, int]:
        with self._lock:
            estados = [t.status for t in self._by_ramal.values()]
        out: dict[str, int] = {}
        for est in estados:
            out[est] = out.get(est, 0) + 1
        return out

    def __len__(self) -> int:
        return len(self._by_ramal)


# ── gravação ─────────────────────────────────────────────────────────────────


def insert_transitions(db: DBSession, samples: list[Sample]) -> int:
    if not samples:
        return 0
    db.execute(
        insert(ExtensionStatusEvent),
        [
            {
                "ramal": s.ramal[:64],
                "status": s.status,
                "status_raw": s.status_raw[:64],
                "numero": s.numero[:64] or None,
                "uniqueid": s.uniqueid[:64] if s.uniqueid else None,
                "event_at": s.event_at,
                "received_at": s.received_at,
                "call_started_at": s.call_started_at,
                "message_id": s.message_id,
            }
            for s in samples
        ],
    )
    return len(samples)


def touch_devices(db: DBSession, samples: list[Sample]) -> int:
    """Leva o estado do ramal para o ``Device`` — o que o dashboard já lê.

    Não cria device: o payload MQTT não tem IP nem MAC, e device sem endereço
    quebraria o ping e o Configurador. Quem cria continua sendo a coleta REST
    (``upsert_from_uscall``); o MQTT só adianta o estado de quem já existe.
    """
    if not samples:
        return 0
    nomes = {s.ramal for s in samples}
    existentes = {
        nome: dev_id
        for dev_id, nome in db.execute(
            select(Device.id, Device.name).where(Device.name.in_(nomes))
        ).all()
    }
    tocados = 0
    for s in samples:
        dev_id = existentes.get(s.ramal)
        if dev_id is None:
            continue
        valores: dict[str, Any] = {
            "telephony_status": s.status,
            "telephony_status_at": s.received_at,
            "telephony_numero": s.numero or None,
            "updated_at": s.received_at,
        }
        logico = logical_from_status(s.status)
        if logico is not None:
            valores["logical_status"] = logico
            valores["status_source"] = "mqtt"
            if logico == "available":
                valores["last_seen_at"] = s.received_at
        db.execute(update(Device).where(Device.id == dev_id).values(**valores))
        tocados += 1
    return tocados


def purge_status_events(db: DBSession, cutoff: datetime) -> int:
    stmt = delete(ExtensionStatusEvent).where(ExtensionStatusEvent.received_at < cutoff)
    return cast("CursorResult[Any]", db.execute(stmt)).rowcount


# ── consultas ────────────────────────────────────────────────────────────────


def live_events(db: DBSession, *, limit: int = 50) -> list[ExtensionStatusEvent]:
    """Últimas transições, de todos os ramais — a "fita" do painel ao vivo."""
    return list(
        db.scalars(
            select(ExtensionStatusEvent)
            .order_by(ExtensionStatusEvent.id.desc())
            .limit(max(1, min(limit, 500)))
        ).all()
    )


def ramal_timeline(
    db: DBSession, ramal: str, *, since: datetime | None = None, limit: int = 200,
) -> list[ExtensionStatusEvent]:
    stmt = select(ExtensionStatusEvent).where(ExtensionStatusEvent.ramal == ramal)
    if since is not None:
        stmt = stmt.where(ExtensionStatusEvent.received_at >= since)
    stmt = stmt.order_by(ExtensionStatusEvent.id.desc()).limit(max(1, min(limit, 1000)))
    return list(db.scalars(stmt).all())
