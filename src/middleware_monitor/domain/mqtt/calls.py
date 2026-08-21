"""Reconstrução de chamadas a partir das transições de estado do ramal.

O PBX não publica chamadas — publica o estado de cada ramal. Uma chamada é o que
se deduz de uma sequência de estados, e o que segue foi tirado de **tráfego real**
(broker do cliente, 2026-08-21, 11 mil transições), não do que a documentação do
publicador sugere.

## O que os dados mostram

**Ligação interna** — o chamador entra direto em ``ocupado`` (sem número!), e o
chamado toca com o número do chamador::

    18:02:49  1211  ocupado   numero=—      uniqueid=X
    18:02:49  9959  tocando   numero=1211   uniqueid=X
    18:02:57  9959  ocupado   numero=1211   uniqueid=X     ← atendeu

**Ligação de saída** — aparece como ``discando`` e **não traz uniqueid** (só 8 de
384 têm)::

    18:02:37  21320  discando  numero=800
    18:02:39  21320  ocupado   numero=800
    18:03:06  21320  disponivel                             ← encerrou

**Grupo de captura** — um mesmo ``uniqueid`` toca vários ramais em rodízio (medido:
até 5), e pode nunca ser atendido. Então ``uniqueid`` **não** identifica um par.

## Duas decisões que os dados forçaram

1. **O campo ``duracao`` não serve de chave.** O ADR-0005 dizia que ele era o
   horário de início da chamada, e portanto identificaria o trecho quando não
   houvesse ``uniqueid``. Medido: ele **muda em 98% das chamadas** (1161 de 1183),
   porque marca o início do *estado atual*. Serve para medir toque e conversa —
   não para identificar.

2. **O trecho é delimitado por estado, não por identificador.** Uma perna de
   chamada é uma sequência ininterrupta de ``tocando``/``discando``/``ocupado``
   do mesmo ramal, fechada por ``disponivel``/``indisponivel``. Funciona com e
   sem ``uniqueid``, que é o único jeito de cobrir os dois padrões acima.

Armadilha tratada: ``indisponivel`` costuma vir carregando o ``uniqueid`` **velho**
por minutos depois de a chamada acabar (visto num ramal que oscilava registro).
Por isso o encerramento nunca herda identificador do evento que o fecha.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import (
    ExtensionCall,
    ExtensionDailyStat,
    ExtensionStatusEvent,
)

__all__ = [
    "EM_CHAMADA",
    "Leg",
    "rebuild_calls",
    "rebuild_daily_stats",
    "reconstruir",
    "search_calls",
]

log = get_logger("mqtt.calls")

# Estados que significam "este ramal está numa chamada". Tudo fora daqui fecha o
# trecho.
EM_CHAMADA = frozenset({"tocando", "discando", "ocupado"})

# Trecho aberto há mais do que isto sem nenhum evento é dado como encerrado com
# resultado indeterminado. O coletor pode ter perdido o fim (broker fora do ar,
# serviço parado) — e chamada aberta para sempre viraria número inventado no
# resumo diário.
ABANDONO = timedelta(hours=4)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(slots=True)
class Leg:
    """Uma perna de chamada — o que um ramal viveu, do início ao fim."""

    ramal: str
    started_at: datetime
    direcao: str = "desconhecida"
    numero: str | None = None
    uniqueid: str | None = None
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    last_event_id: int = 0
    # Preenchido quando a perna veio do banco (chamada que ficou aberta na
    # passagem anterior). E o que distingue "continuar esta linha" de "criar
    # uma nova" — sem isso, cada passagem do job recriava a mesma chamada.
    db_id: int | None = None
    _viu_tocando: bool = field(default=False, repr=False)
    _viu_discando: bool = field(default=False, repr=False)

    @property
    def ring_seconds(self) -> int | None:
        """Tempo de toque. Só existe quando houve toque de fato."""
        if not (self._viu_tocando or self._viu_discando):
            return None
        fim = self.answered_at or self.ended_at
        if fim is None:
            return None
        return max(0, int((fim - self.started_at).total_seconds()))

    @property
    def talk_seconds(self) -> int | None:
        if self.answered_at is None or self.ended_at is None:
            return None
        return max(0, int((self.ended_at - self.answered_at).total_seconds()))

    @property
    def outcome(self) -> str:
        if self.ended_at is None:
            return "em_curso"
        if self.answered_at is not None:
            return "atendida"
        # Sem atender: quem tocou perdeu a chamada; quem discou não foi atendido.
        # A distinção importa porque uma é problema de atendimento e a outra não.
        if self._viu_tocando:
            return "perdida"
        if self._viu_discando:
            return "nao_atendida"
        return "indeterminada"


def _direcao_de(status: str, numero: str | None) -> str:
    """Direção deduzida do estado que **abriu** o trecho.

    ``tocando`` é sempre recepção (o número é a origem). ``discando`` é sempre
    saída. ``ocupado`` abrindo o trecho é o chamador de uma ligação interna — o
    PBX o coloca direto em conversa, sem passar por discando, e sem número.
    """
    if status == "tocando":
        return "entrante"
    if status == "discando":
        return "sainte"
    if status == "ocupado":
        return "sainte" if not numero else "desconhecida"
    return "desconhecida"


def reconstruir(
    eventos: list[Any], abertas_iniciais: dict[str, Leg] | None = None,
) -> list[Leg]:
    """Transições (ordenadas por id) → pernas de chamada.

    Função pura: recebe qualquer objeto com os campos de ``ExtensionStatusEvent``
    e devolve as pernas. É onde mora toda a regra, e é o que os testes exercitam
    com sequências capturadas do PBX real.

    ``abertas_iniciais`` continua pernas que ficaram abertas na passagem
    anterior, trazidas do banco. É isso que permite processar **apenas eventos
    novos**: sem semear, uma chamada em curso teria de ser reconstruída relendo
    eventos antigos — e reler evento antigo foi exatamente o que duplicava
    chamada a cada rodada do job.
    """
    abertas: dict[str, Leg] = dict(abertas_iniciais or {})
    prontas: list[Leg] = []

    for ev in eventos:
        ramal = ev.ramal
        atual = abertas.get(ramal)

        if ev.status in EM_CHAMADA:
            if atual is None:
                atual = Leg(
                    ramal=ramal,
                    started_at=ev.received_at,
                    direcao=_direcao_de(ev.status, ev.numero),
                    numero=ev.numero or None,
                    uniqueid=ev.uniqueid,
                )
                abertas[ramal] = atual
            else:
                # O número costuma chegar só depois do primeiro estado; o
                # uniqueid idem. Preencher sem sobrescrever o que já se sabia.
                if not atual.numero and ev.numero:
                    atual.numero = ev.numero
                if not atual.uniqueid and ev.uniqueid:
                    atual.uniqueid = ev.uniqueid
                if atual.direcao == "desconhecida":
                    atual.direcao = _direcao_de(ev.status, ev.numero)
            if ev.status == "tocando":
                atual._viu_tocando = True
            elif ev.status == "discando":
                atual._viu_discando = True
            elif ev.status == "ocupado" and atual.answered_at is None:
                # `duracao` (call_started_at) marca o início do estado atual, e
                # é mais preciso que a hora de recebimento — o publicador demora
                # alguns segundos para contar. Só vale se não for anterior ao
                # início do trecho.
                inicio = ev.call_started_at
                atual.answered_at = (
                    inicio if inicio and inicio >= atual.started_at else ev.received_at
                )
            atual.last_event_id = ev.id
            continue

        # disponivel / indisponivel / desconhecido: fecha o trecho, se houver.
        if atual is not None:
            atual.ended_at = ev.received_at
            atual.last_event_id = ev.id
            # NÃO herdar uniqueid daqui: `indisponivel` carrega o identificador
            # velho por minutos depois do fim (visto em ramal com registro
            # oscilando), e herdá-lo grudaria pernas de chamadas diferentes.
            prontas.append(atual)
            del abertas[ramal]

    prontas.extend(abertas.values())  # ainda em curso
    _parear_pontas(prontas)
    return prontas


def _parear_pontas(pernas: list[Leg]) -> None:
    """Preenche a outra ponta de quem ligou, usando o ``uniqueid``.

    Numa ligação interna o PBX manda o número só para quem **recebe**: o
    chamador aparece em ``ocupado`` sem número nenhum. Na tela isso vira uma
    linha "feita para —", que é justamente a informação que o operador foi
    buscar. Como as duas pernas compartilham o ``uniqueid``, dá para dizer com
    certeza quem era o outro lado.

    Só age quando o grupo tem **exatamente dois ramais**. Num grupo de captura
    (medido: até 5 ramais no mesmo id) não existe "a outra ponta" — escolher uma
    seria inventar.
    """
    grupos: dict[str, list[Leg]] = {}
    for leg in pernas:
        if leg.uniqueid:
            grupos.setdefault(leg.uniqueid, []).append(leg)

    for legs in grupos.values():
        ramais = {leg.ramal for leg in legs}
        if len(ramais) != 2:
            continue
        for leg in legs:
            if leg.numero:
                continue
            outro = ramais - {leg.ramal}
            leg.numero = next(iter(outro))


def _leg_do_banco(linha: ExtensionCall) -> Leg:
    """Recria em memória a perna que ficou aberta, a partir da linha gravada.

    Os dois sinalizadores de "por onde passou" não são colunas — são deduzidos
    do que já se sabe: quem é entrante tocou; quem é sainte e tem tempo de toque
    discou. Sainte sem tempo de toque é o chamador de uma ligação interna, que o
    PBX joga direto em conversa e portanto não passou por nenhum dos dois.
    """
    leg = Leg(
        ramal=linha.ramal,
        started_at=linha.started_at,
        direcao=linha.direcao,
        numero=linha.numero,
        uniqueid=linha.uniqueid,
        answered_at=linha.answered_at,
        last_event_id=linha.last_event_id,
        db_id=linha.id,
    )
    if linha.direcao == "entrante":
        leg._viu_tocando = True
    elif linha.direcao == "sainte" and linha.ring_seconds is not None:
        leg._viu_discando = True
    return leg


def rebuild_calls(db: DBSession, *, limite: int = 20_000) -> dict[str, int]:
    """Processa as transições novas e grava/atualiza as chamadas.

    **Só lê evento novo.** A versão anterior puxava o piso de leitura para trás
    até a chamada em curso mais antiga e reprocessava tudo dali — e as pernas já
    concluídas nesse intervalo, que a comparação não sabia reconhecer, viravam
    linha nova a cada passagem. Com o job de minuto em minuto, uma chamada
    chegou a ser gravada 32 vezes.

    Agora as chamadas que ficaram abertas são **semeadas** na reconstrução, e a
    marca d'água nunca anda para trás. Reprocessar é inofensivo: sem evento
    novo, não há nada a fazer.
    """
    watermark = int(db.scalar(select(func.max(ExtensionCall.last_event_id))) or 0)

    em_curso = list(
        db.scalars(select(ExtensionCall).where(ExtensionCall.outcome == "em_curso")).all()
    )

    eventos = list(
        db.scalars(
            select(ExtensionStatusEvent)
            .where(ExtensionStatusEvent.id > watermark)
            .order_by(ExtensionStatusEvent.id)
            .limit(limite)
        ).all()
    )
    if not eventos:
        _encerrar_abandonadas(db)
        return {"lidos": 0, "criadas": 0, "atualizadas": 0}

    pernas = reconstruir(eventos, {c.ramal: _leg_do_banco(c) for c in em_curso})
    agora = _now()
    criadas = atualizadas = 0

    for leg in pernas:
        valores = {
            "direcao": leg.direcao,
            "numero": leg.numero,
            "uniqueid": leg.uniqueid,
            "answered_at": leg.answered_at,
            "ended_at": leg.ended_at,
            "ring_seconds": leg.ring_seconds,
            "talk_seconds": leg.talk_seconds,
            "outcome": leg.outcome,
            "last_event_id": leg.last_event_id,
            "updated_at": agora,
        }
        if leg.db_id is not None:
            db.execute(
                update(ExtensionCall).where(ExtensionCall.id == leg.db_id).values(**valores)
            )
            atualizadas += 1
        else:
            db.execute(
                insert(ExtensionCall),
                [{
                    "ramal": leg.ramal[:64],
                    "started_at": leg.started_at,
                    "created_at": agora,
                    **valores,
                }],
            )
            criadas += 1

    _encerrar_abandonadas(db)
    log.info("mqtt_calls_rebuilt", lidos=len(eventos), criadas=criadas, atualizadas=atualizadas)
    return {"lidos": len(eventos), "criadas": criadas, "atualizadas": atualizadas}


def _encerrar_abandonadas(db: DBSession) -> None:
    """Perna aberta há muito tempo sem nenhum evento vira indeterminada.

    Sem isto ela contaria como "em curso" para sempre, seria semeada em toda
    passagem do job e envenenaria o resumo diário.
    """
    limite = _now() - ABANDONO
    db.execute(
        update(ExtensionCall)
        .where(
            ExtensionCall.outcome == "em_curso",
            ExtensionCall.started_at < limite,
        )
        .values(outcome="indeterminada", updated_at=_now())
    )


def rebuild_daily_stats(db: DBSession, dia: str) -> int:
    """Recalcula o resumo de um dia (``AAAA-MM-DD``) a partir das chamadas.

    **Uma chamada com ``uniqueid`` conta uma vez por ramal, mesmo que o PBX a
    tenha tocado várias.** Medido em produção: um grupo de captura rodou entre
    quatro ramais e gerou 11 pernas "perdida" para **uma** ligação externa.
    Contar as pernas cruas mostraria 11 chamadas perdidas, e o número que o
    operador usa para cobrar atendimento estaria inflado quase três vezes.
    Perna sem ``uniqueid`` (todas as de saída) conta individualmente, que é o
    melhor que dá para fazer sem identificador.

    Idempotente: recalcula o dia inteiro e substitui.
    """
    inicio = datetime.fromisoformat(f"{dia} 00:00:00")
    fim = inicio + timedelta(days=1)

    chamadas = list(
        db.scalars(
            select(ExtensionCall).where(
                ExtensionCall.started_at >= inicio,
                ExtensionCall.started_at < fim,
                ExtensionCall.outcome != "em_curso",
            )
        ).all()
    )

    vistos: set[tuple[str, str]] = set()
    por_ramal: dict[str, dict[str, int]] = {}
    for ch in chamadas:
        if ch.uniqueid:
            chave = (ch.ramal, ch.uniqueid)
            if chave in vistos:
                continue
            vistos.add(chave)
        linha = por_ramal.setdefault(
            ch.ramal,
            {"chamadas": 0, "atendidas": 0, "perdidas": 0, "entrantes": 0,
             "saintes": 0, "talk_seconds": 0, "ring_seconds": 0},
        )
        linha["chamadas"] += 1
        if ch.outcome == "atendida":
            linha["atendidas"] += 1
        elif ch.outcome == "perdida":
            linha["perdidas"] += 1
        if ch.direcao == "entrante":
            linha["entrantes"] += 1
        elif ch.direcao == "sainte":
            linha["saintes"] += 1
        linha["talk_seconds"] += ch.talk_seconds or 0
        linha["ring_seconds"] += ch.ring_seconds or 0

    agora = _now()
    db.execute(delete(ExtensionDailyStat).where(ExtensionDailyStat.dia == dia))
    if por_ramal:
        db.execute(
            insert(ExtensionDailyStat),
            [
                {"dia": dia, "ramal": ramal, "updated_at": agora, **valores}
                for ramal, valores in sorted(por_ramal.items())
            ],
        )
    log.info("mqtt_daily_stats", dia=dia, ramais=len(por_ramal), pernas=len(chamadas))
    return len(por_ramal)


@dataclass(slots=True)
class CallSearch:
    items: list[ExtensionCall]
    total: int


def search_calls(
    db: DBSession,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    ramal: str | None = None,
    ramal_exato: bool = False,
    numero: str | None = None,
    direcao: str | None = None,
    outcome: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> CallSearch:
    """Busca de chamadas para a tela.

    ``ramal`` e ``numero`` casam por **trecho**, como no ledger de mensagens:
    quem procura uma ligação costuma lembrar de um pedaço do número.
    ``ramal_exato`` desliga isso para o ramal — usado na página de um telefone,
    onde ``9959`` não pode trazer as chamadas do ``19959``.
    """
    stmt = select(ExtensionCall)
    total_stmt = select(func.count()).select_from(ExtensionCall)

    def aplicar(cond: Any) -> None:
        nonlocal stmt, total_stmt
        stmt = stmt.where(cond)
        total_stmt = total_stmt.where(cond)

    if since is not None:
        aplicar(ExtensionCall.started_at >= since)
    if until is not None:
        aplicar(ExtensionCall.started_at <= until)
    if ramal:
        # Trecho e o certo na busca; exato e o certo na pagina de um telefone.
        alvo = ramal.strip()
        aplicar(
            ExtensionCall.ramal == alvo if ramal_exato
            else ExtensionCall.ramal.icontains(alvo)
        )
    if numero:
        aplicar(ExtensionCall.numero.icontains(numero.strip()))
    if direcao:
        aplicar(ExtensionCall.direcao == direcao)
    if outcome:
        aplicar(ExtensionCall.outcome == outcome)

    total = int(db.scalar(total_stmt) or 0)
    itens = list(
        db.scalars(
            stmt.order_by(ExtensionCall.started_at.desc(), ExtensionCall.id.desc())
            .limit(max(1, min(limit, 1000)))
            .offset(max(0, offset))
        ).all()
    )
    return CallSearch(items=itens, total=total)


def daily_stats(db: DBSession, *, dia: str, ramal: str | None = None) -> list[ExtensionDailyStat]:
    stmt = select(ExtensionDailyStat).where(ExtensionDailyStat.dia == dia)
    if ramal:
        stmt = stmt.where(ExtensionDailyStat.ramal == ramal)
    return list(db.scalars(stmt.order_by(ExtensionDailyStat.chamadas.desc())).all())
