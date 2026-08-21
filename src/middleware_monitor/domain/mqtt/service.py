"""Coletor: liga o cliente MQTT ao banco sem deixar a rede esperar pelo disco.

Desenho: o paho entrega em thread própria e só encosta em uma fila em memória;
um worker no loop do app drena essa fila e grava em lote. A ingestão nunca
bloqueia na rede e a gravação nunca acontece uma linha por vez.

Quando a fila enche (banco lento, disco travado), a mensagem mais antiga é
descartada e o descarte é contado **e mostrado na tela** — um comprovante que
some sem aviso é pior do que comprovante nenhum.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.domain.mqtt import realtime
from middleware_monitor.domain.mqtt import repository as repo
from middleware_monitor.domain.mqtt.parser import (
    ExtensionStatus,
    parse_extension_payload,
    ramal_from_topic,
)
from middleware_monitor.integrations.mqtt_client import MqttAuth, MqttConnection, MqttEndpoint

__all__ = ["MqttIngestor", "get_ingestor"]

log = get_logger("mqtt.ingest")

QUEUE_MAX = 10_000
FLUSH_SECONDS = 1.0
BATCH_MAX = 500
BATCHES_PER_CYCLE = 10


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class _RateWindow:
    """Contador de mensagens no último minuto (60 baldes de 1 s)."""

    __slots__ = ("_buckets", "_last_sec")

    def __init__(self) -> None:
        self._buckets = [0] * 60
        self._last_sec = 0

    def add(self, now: float) -> None:
        sec = int(now)
        self._roll(sec)
        self._buckets[sec % 60] += 1

    def per_minute(self, now: float) -> int:
        self._roll(int(now))
        return sum(self._buckets)

    def _roll(self, sec: int) -> None:
        if self._last_sec == 0:
            self._last_sec = sec
            return
        if sec <= self._last_sec:
            return
        for i in range(1, min(sec - self._last_sec, 60) + 1):
            self._buckets[(self._last_sec + i) % 60] = 0
        self._last_sec = sec


@dataclass(slots=True)
class _BrokerRuntime:
    broker_id: int
    nome: str
    endpoint_label: str
    client_id: str
    topics: list[str]
    connection: MqttConnection
    state: str = "disconnected"
    detail: str = ""
    connected_since: datetime | None = None
    received: int = 0


class MqttIngestor:
    """Ciclo de vida das assinaturas + gravação em lote no ledger."""

    def __init__(self, db_factory: Callable[[], DBSession] = session_factory) -> None:
        self._db_factory = db_factory
        self._runtimes: dict[int, _BrokerRuntime] = {}
        # Cada item é (linha do ledger, status já extraídos do payload). O
        # parse acontece uma vez só, na thread do paho, e viaja junto — refazê-lo
        # na gravação seria decodificar o mesmo JSON duas vezes por mensagem.
        self._buffer: deque[tuple[dict[str, Any], list[ExtensionStatus]]] = deque()
        self._conn_buffer: deque[dict[str, Any]] = deque()
        self._rate = _RateWindow()
        self._task: asyncio.Task[None] | None = None
        # Loop onde o coletor vive: guardado no start para que endpoints
        # sincronos (que rodam em thread do FastAPI) consigam pedir reconexao.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = False
        self._started_at: datetime | None = None
        # contadores
        self.received = 0
        self.persisted = 0
        self.dropped = 0
        self.persist_failures = 0
        self.last_message_at: datetime | None = None
        self._lag_sum = 0.0
        self._lag_count = 0
        # Mensagens cuja hora do PBX esta a mais de 1h do relogio do servidor:
        # ficam fora da media (senao um relogio torto envenena a metrica), mas
        # sao contadas — media vazia sem explicacao esconde o problema.
        self.clock_outliers = 0
        # Estado ao vivo por ramal (fase 3): alimenta o painel e impede que
        # estado repetido — reentrega da sessão durável, ou publicador que varre
        # periodicamente — vire transição nova.
        self.state = realtime.RealtimeState()
        self.transitions = 0
        self.devices_touched = 0

    # ── ciclo de vida ────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._loop = asyncio.get_running_loop()
        self._started_at = _now()
        # Marca o boot na prova de cobertura: um "startup" logo depois de um
        # "connected" (sem "stopped" entre eles) denuncia que o processo morreu
        # sem encerrar — e o periodo anterior deixa de ser comprovavel.
        self._conn_buffer.append(_conn_row("startup", "coletor iniciado"))
        # Reidrata o estado dos ramais antes do primeiro lote: sem isso, a volta
        # do serviço gravaria uma transição falsa para cada ramal do broker.
        try:
            with self._db_factory() as db:
                self.state.prime(db)
        except Exception as exc:  # banco indisponível não impede coletar
            log.warning("mqtt_realtime_prime_failed", error=type(exc).__name__, message=str(exc))
        self._task = asyncio.create_task(self._writer_loop())
        await self._connect_all()

    async def stop(self) -> None:
        self._stopping = True
        self._conn_buffer.append(_conn_row("stopped", "coletor encerrado"))
        for rt in list(self._runtimes.values()):
            rt.connection.stop()
        self._runtimes.clear()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._flush()  # não perde o que já chegou

    def request_reload(self) -> None:
        """Pede reconexao de qualquer thread (a tela salva em endpoint sincrono).

        Sem coletor rodando — testes, ou app subindo sem broker — nao ha o que
        reconectar, e a chamada e um no-op silencioso.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.reload(), loop)
        except RuntimeError as exc:  # loop encerrando
            log.warning("mqtt_reload_skipped", message=str(exc))

    async def reload(self) -> None:
        """Aplica a configuração salva na tela sem reiniciar o serviço."""
        for rt in list(self._runtimes.values()):
            rt.connection.stop()
        self._runtimes.clear()
        await self._connect_all()

    async def _connect_all(self) -> None:
        with self._db_factory() as db:
            brokers = repo.list_brokers(db, enabled_only=True)
            configs = [
                (
                    b.id, b.nome, b.host, b.port, b.transport, b.tls, b.ws_path,
                    b.username, repo.load_broker_password(b), b.tls_verify,
                    b.tls_fingerprint, repo.broker_topics(b), b.qos, b.clean_session,
                    b.client_id, b.max_payload_kb,
                )
                for b in brokers
            ]
        for cfg in configs:
            self._start_broker(*cfg)
        log.info("mqtt_ingest_started", brokers=len(configs))

    def _start_broker(
        self, broker_id: int, nome: str, host: str, port: int, transport: str, tls: bool,
        ws_path: str | None, username: str, password: str, tls_verify: bool,
        tls_fingerprint: str | None, topics: list[str], qos: int, clean_session: bool,
        client_id: str, max_payload_kb: int,
    ) -> None:
        if not topics:
            log.warning("mqtt_broker_sem_topico", broker_id=broker_id, nome=nome)
            return
        endpoint = MqttEndpoint(host=host, port=port, transport=transport, tls=tls, ws_path=ws_path)
        conn = MqttConnection(
            endpoint=endpoint,
            client_id=client_id or repo.default_client_id(nome),
            topics=topics,
            auth=MqttAuth(username=username, password=password),
            qos=qos,
            clean_session=clean_session,
            tls_verify=tls_verify,
            tls_fingerprint=tls_fingerprint,
            on_message=self._make_on_message(broker_id, max_payload_kb),
            on_state=self._make_on_state(broker_id, endpoint.label, client_id),
        )
        self._runtimes[broker_id] = _BrokerRuntime(
            broker_id=broker_id, nome=nome, endpoint_label=endpoint.label,
            client_id=client_id, topics=topics, connection=conn,
        )
        conn.start()

    # ── callbacks (thread do paho) ───────────────────────────────────────────

    def _make_on_message(
        self, broker_id: int, max_payload_kb: int,
    ) -> Callable[[str, bytes, int, bool], None]:
        def handler(topic: str, payload: bytes, qos: int, retained: bool) -> None:
            now = _now()
            row, statuses = _build_row(
                broker_id, topic, payload, qos, retained, max_payload_kb, now,
            )
            self.received += 1
            self.last_message_at = now
            self._rate.add(time.time())
            rt = self._runtimes.get(broker_id)
            if rt is not None:
                rt.received += 1
            if row["event_at"] is not None:
                lag = (now - row["event_at"]).total_seconds()
                if -3600 < lag < 3600:
                    self._lag_sum += lag
                    self._lag_count += 1
                else:
                    self.clock_outliers += 1
            if len(self._buffer) >= QUEUE_MAX:
                self._buffer.popleft()
                self.dropped += 1
                if self.dropped % 100 == 1:
                    log.error("mqtt_queue_overflow", dropped=self.dropped, queue=len(self._buffer))
            self._buffer.append((row, statuses))

        return handler

    def _make_on_state(
        self, broker_id: int, endpoint_label: str, client_id: str,
    ) -> Callable[[str, str], None]:
        def handler(state: str, detail: str) -> None:
            rt = self._runtimes.get(broker_id)
            if rt is not None:
                rt.state = state
                rt.detail = detail
                if state == "connected":
                    rt.connected_since = _now()
                elif state == "disconnected":
                    rt.connected_since = None
            if state == "connecting":
                return  # ruído: só transições reais entram na prova de cobertura
            self._conn_buffer.append(
                {
                    "broker_id": broker_id,
                    "timestamp": _now(),
                    "state": state,
                    "detail": detail,
                    "client_id": client_id,
                    "endpoint": endpoint_label,
                }
            )
            nivel = log.warning if state in ("disconnected", "error") else log.info
            nivel("mqtt_state", broker_id=broker_id, state=state, detail=detail)

        return handler

    # ── gravação ─────────────────────────────────────────────────────────────

    async def _writer_loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(FLUSH_SECONDS)
                await self._flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - o loop não pode morrer
                log.error("mqtt_writer_loop_failed", error=type(exc).__name__, message=str(exc))

    async def _flush(self) -> None:
        for _ in range(BATCHES_PER_CYCLE):
            if not self._buffer and not self._conn_buffer:
                return
            msgs = [self._buffer.popleft() for _ in range(min(BATCH_MAX, len(self._buffer)))]
            conns = [self._conn_buffer.popleft() for _ in range(len(self._conn_buffer))]
            ok = await asyncio.to_thread(self._persist, msgs, conns)
            if not ok:
                # Devolve para a frente da fila: comprovante não se joga fora
                # porque o banco piscou. Se a fila encher, o descarte é contado.
                self._buffer.extendleft(reversed(msgs))
                self._conn_buffer.extendleft(reversed(conns))
                return

    def _persist(
        self,
        msgs: list[tuple[dict[str, Any], list[ExtensionStatus]]],
        conns: list[dict[str, Any]],
    ) -> bool:
        """Uma transação por lote: ledger, transições e estado dos ramais.

        Tudo junto de propósito — se a transição fosse gravada fora da mesma
        transação do ledger, um comprovante poderia existir sem o estado que ele
        originou (ou o contrário) depois de uma queda no meio do caminho.
        """
        transicoes: list[realtime.Sample] = []
        try:
            with self._db_factory() as db:
                self.state.prime(db)  # no-op depois da primeira vez
                ids = repo.insert_messages(db, [row for row, _ in msgs])
                amostras: list[realtime.Sample] = []
                for (row, statuses), msg_id in zip(msgs, ids, strict=True):
                    if statuses:
                        amostras.extend(
                            realtime.samples_from(statuses, row["received_at"], msg_id)
                        )
                transicoes, toques = self.state.classify(amostras)
                realtime.insert_transitions(db, transicoes)
                tocados = realtime.touch_devices(db, toques)
                repo.insert_connection_events(db, conns)
                db.commit()
        except Exception as exc:
            # O cache já anotou as transições como conhecidas; sem desfazer,
            # elas nunca mais seriam gravadas e o ramal congelaria no painel.
            self.state.rollback(transicoes)
            self.persist_failures += 1
            log.error(
                "mqtt_persist_failed",
                error=type(exc).__name__, message=str(exc), batch=len(msgs),
            )
            return False
        self.persisted += len(msgs)
        self.transitions += len(transicoes)
        self.devices_touched += tocados
        return True

    # ── estado para a tela ───────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        agora = time.time()
        brokers = [
            {
                "broker_id": rt.broker_id,
                "nome": rt.nome,
                "endpoint": rt.endpoint_label,
                "client_id": rt.client_id,
                "topics": rt.topics,
                "state": rt.state,
                "detail": rt.detail,
                "connected_since": rt.connected_since,
                "received": rt.received,
            }
            for rt in self._runtimes.values()
        ]
        return {
            "running": self._task is not None,
            "started_at": self._started_at,
            "brokers": brokers,
            "received": self.received,
            "persisted": self.persisted,
            "dropped": self.dropped,
            "persist_failures": self.persist_failures,
            "queue_depth": len(self._buffer),
            "per_minute": self._rate.per_minute(agora),
            "last_message_at": self.last_message_at,
            "avg_lag_seconds": round(self._lag_sum / self._lag_count, 2) if self._lag_count else None,
            "clock_outliers": self.clock_outliers,
            "transitions": self.transitions,
            "devices_touched": self.devices_touched,
            "tracked_ramais": len(self.state),
        }

    def live(self) -> dict[str, Any]:
        """Estado de cada ramal agora, direto da memória.

        Não consulta o banco de propósito: a tela recarrega a cada 2 a 3 s e uma
        varredura de tabela nesse ritmo custaria mais que a própria ingestão.
        """
        agora = _now()
        return {
            "generated_at": agora,
            "extensions": self.state.snapshot(agora),
            "counts": self.state.counts(),
        }


def _conn_row(state: str, detail: str) -> dict[str, Any]:
    return {
        "broker_id": None,
        "timestamp": _now(),
        "state": state,
        "detail": detail,
        "client_id": "",
        "endpoint": "",
    }


def _build_row(
    broker_id: int, topic: str, payload: bytes, qos: int, retained: bool,
    max_payload_kb: int, now: datetime,
) -> tuple[dict[str, Any], list[ExtensionStatus]]:
    """Linha do ledger (corpo verbatim) + os status reconhecidos no payload.

    O parse sai daqui junto com a linha para não decodificar o mesmo JSON duas
    vezes: uma para achar o ramal do índice, outra para normalizar o estado.
    """
    tamanho = len(payload)
    try:
        texto = payload.decode("utf-8")
        b64 = False
    except UnicodeDecodeError:
        texto = ""
        b64 = True

    # O reconhecimento roda sobre o corpo **inteiro**: truncar antes cortaria o
    # JSON no meio e o ramal do payload se perderia justamente nas mensagens
    # grandes. O corte só vale para o que vai ao disco.
    statuses: list[ExtensionStatus] = [] if b64 else parse_extension_payload(texto)
    ramal: str | None = statuses[0].ramal if statuses else None
    event_at: datetime | None = statuses[0].event_at if statuses else None
    if ramal is None:
        ramal = ramal_from_topic(topic)

    truncated = False
    if max_payload_kb > 0 and tamanho > max_payload_kb * 1024:
        payload = payload[: max_payload_kb * 1024]
        truncated = True
    if b64:
        texto = base64.b64encode(payload).decode("ascii")
    elif truncated:
        texto = payload.decode("utf-8", errors="ignore")

    row = {
        "broker_id": broker_id,
        "received_at": now,
        "topic": topic[:512],
        "ramal": ramal,
        "payload": texto,
        "payload_bytes": tamanho,
        "qos": qos,
        "retained": retained,
        "b64": b64,
        "truncated": truncated,
        "event_at": event_at,
        "pinned": False,
    }
    return row, statuses


_ingestor: MqttIngestor | None = None


def get_ingestor() -> MqttIngestor:
    """Instância única (o app roda com ``workers=1``, ADR-0001)."""
    global _ingestor
    if _ingestor is None:
        _ingestor = MqttIngestor()
    return _ingestor
