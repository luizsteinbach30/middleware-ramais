"""Estado in-memory dos runs de device actions em massa (tracking ao vivo).

Espelha ``run_state.py`` (apply): uvicorn roda com **1 worker** (ADR existente),
entao um dict modulo-level nao tem race conditions. Se o middleware reiniciar,
o tracking do run em curso se perde — mas o rastro persistente fica em
``device_action_events`` (1 evento por telefone), entao a auditoria sobrevive.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

ActionRowStage = Literal["pending", "running", "done", "error"]


@dataclass
class ActionRowState:
    line_id: str
    ip: str
    numero_ramal: str
    stage: ActionRowStage = "pending"
    msg: str = ""
    started_at: float | None = None
    finished_at: float | None = None


@dataclass
class ActionRunState:
    run_id: str
    env_id: str
    action: str
    rows: list[ActionRowState] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def summary(self) -> dict[str, int]:
        out = {"pending": 0, "running": 0, "done": 0, "error": 0}
        for r in self.rows:
            out[r.stage] = out.get(r.stage, 0) + 1
        return out


# Singleton in-memory.
_RUNS: dict[str, ActionRunState] = {}


def register(run: ActionRunState) -> None:
    _RUNS[run.run_id] = run


def get(run_id: str) -> ActionRunState | None:
    return _RUNS.get(run_id)


def prune(*, keep: int = 20) -> None:
    """Mantem so os N runs mais recentes (FIFO de inicio)."""
    if len(_RUNS) <= keep:
        return
    runs = sorted(_RUNS.values(), key=lambda r: r.started_at, reverse=True)
    keep_ids = {r.run_id for r in runs[:keep]}
    for rid in list(_RUNS):
        if rid not in keep_ids:
            _RUNS.pop(rid, None)
