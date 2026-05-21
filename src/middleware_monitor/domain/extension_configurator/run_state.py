"""Estado in-memory dos runs de aplicacao (tracking de progresso ao vivo).

Uvicorn roda com **1 worker** (ADR existente), entao podemos manter um dict
modulo-level sem race conditions. Quando o middleware reinicia, runs em curso
perdem o tracking — mas o estado persistente (DB) preserva os ultimos_*
de cada linha, entao a UI sempre sabe o que aconteceu.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

RowStage = Literal["pending", "ping", "send", "done", "error"]


@dataclass
class RowState:
    line_id: str
    ip: str
    numero_ramal: str
    stage: RowStage = "pending"
    msg: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    hash_esperado: str = ""


@dataclass
class RunState:
    run_id: str
    env_id: str
    db_run_id: int  # PK em extension_apply_runs
    rows: list[RowState] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancelled: bool = False

    def summary(self) -> dict[str, int]:
        out = {"pending": 0, "ping": 0, "send": 0, "done": 0, "error": 0}
        for r in self.rows:
            out[r.stage] = out.get(r.stage, 0) + 1
        return out


# Singleton in-memory.
_RUNS: dict[str, RunState] = {}


def register(run: RunState) -> None:
    _RUNS[run.run_id] = run


def get(run_id: str) -> RunState | None:
    return _RUNS.get(run_id)


def cancel(run_id: str) -> bool:
    run = _RUNS.get(run_id)
    if run is None or run.finished_at is not None:
        return False
    run.cancelled = True
    return True


def prune(*, keep: int = 20) -> None:
    """Mantem so os N runs mais recentes (FIFO de inicio)."""
    if len(_RUNS) <= keep:
        return
    runs = sorted(_RUNS.values(), key=lambda r: r.started_at, reverse=True)
    keep_ids = {r.run_id for r in runs[:keep]}
    for rid in list(_RUNS):
        if rid not in keep_ids:
            _RUNS.pop(rid, None)
