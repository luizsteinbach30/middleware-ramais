"""Prova de cobertura: quanto de um período o coletor esteve realmente ouvindo.

É o que separa "não houve publicação" de "não estávamos ouvindo". A regra é
conservadora de propósito: na dúvida, o período **não** conta como coberto.
Um processo que morreu sem encerrar limpo deixa um ``startup`` sem o
``stopped`` correspondente — o intervalo anterior a ele vira lacuna, porque
ninguém pode afirmar o que aconteceu ali.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

__all__ = ["UP_STATES", "Coverage", "CoverageGapItem", "compute_coverage"]

UP_STATES = frozenset({"connected", "subscribed"})
DOWN_STATES = frozenset({"disconnected", "error", "stopped"})


@dataclass(slots=True)
class CoverageGapItem:
    started_at: datetime
    ended_at: datetime | None
    seconds: int
    detail: str


@dataclass(slots=True)
class Coverage:
    since: datetime
    until: datetime
    covered_seconds: int = 0
    total_seconds: int = 0
    gaps: list[CoverageGapItem] = field(default_factory=list)
    unknown: bool = False

    @property
    def coverage_pct(self) -> float:
        if self.total_seconds <= 0:
            return 0.0
        return round(100.0 * self.covered_seconds / self.total_seconds, 2)


def compute_coverage(
    events: list[tuple[datetime, str, str]],
    since: datetime,
    until: datetime,
    *,
    state_before: str | None = None,
) -> Coverage:
    """``events`` = [(timestamp, state, detail)] dentro de [since, until].

    ``state_before`` é o estado vigente logo antes de ``since`` (o último
    evento anterior à janela); ``None`` significa que não há histórico — aí o
    período inteiro é desconhecido, não coberto.
    """
    total = max(0, int((until - since).total_seconds()))
    cov = Coverage(since=since, until=until, total_seconds=total)
    if total == 0:
        return cov

    if state_before is None and not events:
        cov.unknown = True
        cov.gaps.append(
            CoverageGapItem(
                started_at=since, ended_at=until, seconds=total,
                detail="sem histórico do coletor neste período",
            )
        )
        return cov

    up = state_before in UP_STATES if state_before is not None else False
    if state_before is None:
        cov.unknown = True

    cursor = since
    gap_inicio: datetime | None = None if up else since
    gap_motivo = "" if up else "coletor fora do ar"

    for ts, state, detail in sorted(events, key=lambda e: e[0]):
        momento = min(max(ts, since), until)
        if up:
            cov.covered_seconds += int((momento - cursor).total_seconds())
        cursor = momento

        if state == "startup":
            # Subiu sem ter encerrado antes: o que veio antes não é comprovável.
            if up:
                cov.gaps.append(
                    CoverageGapItem(
                        started_at=momento, ended_at=momento, seconds=0,
                        detail="coletor reiniciado sem encerramento limpo — "
                        "período anterior não comprovado",
                    )
                )
            up = False
            if gap_inicio is None:
                gap_inicio = momento
                gap_motivo = "coletor iniciando"
            continue

        novo = state in UP_STATES
        if novo and not up:
            if gap_inicio is not None:
                duracao = int((momento - gap_inicio).total_seconds())
                # Reconexão instantânea não é lacuna: nada foi perdido e a lista
                # de lacunas precisa mostrar só o que o operador deve olhar.
                if duracao > 0:
                    cov.gaps.append(
                        CoverageGapItem(
                            started_at=gap_inicio, ended_at=momento,
                            seconds=duracao,
                            detail=gap_motivo or "coletor fora do ar",
                        )
                    )
                gap_inicio = None
                gap_motivo = ""
        elif state in DOWN_STATES and up:
            gap_inicio = momento
            gap_motivo = detail or "conexão perdida"
        up = novo or (up and state not in DOWN_STATES)

    if up:
        cov.covered_seconds += int((until - cursor).total_seconds())
    elif gap_inicio is not None:
        cov.gaps.append(
            CoverageGapItem(
                started_at=gap_inicio, ended_at=until,
                seconds=int((until - gap_inicio).total_seconds()),
                detail=gap_motivo or "coletor fora do ar",
            )
        )

    cov.covered_seconds = max(0, min(cov.covered_seconds, total))
    return cov
