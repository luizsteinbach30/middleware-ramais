"""action_state: tracking in-memory dos runs de device actions em massa."""

from __future__ import annotations

from middleware_monitor.domain.extension_configurator import action_state
from middleware_monitor.domain.extension_configurator.action_state import (
    ActionRowState,
    ActionRunState,
)


def _run(run_id: str, started_at: float) -> ActionRunState:
    rs = ActionRunState(run_id=run_id, env_id="env1", action="normalize")
    rs.started_at = started_at
    return rs


def test_summary_conta_por_stage() -> None:
    rs = _run("r1", 100.0)
    rs.rows = [
        ActionRowState(line_id="a", ip="10.0.0.1", numero_ramal="3001", stage="done"),
        ActionRowState(line_id="b", ip="10.0.0.2", numero_ramal="3002", stage="done"),
        ActionRowState(line_id="c", ip="10.0.0.3", numero_ramal="3003", stage="error"),
        ActionRowState(line_id="d", ip="10.0.0.4", numero_ramal="3004"),
    ]
    assert rs.summary() == {"pending": 1, "running": 0, "done": 2, "error": 1}


def test_register_get_e_prune_mantem_mais_recentes() -> None:
    action_state._RUNS.clear()
    for i in range(25):
        action_state.register(_run(f"r{i}", float(i)))
    assert action_state.get("r24") is not None

    action_state.prune(keep=20)
    # os 5 mais antigos caem; os 20 mais recentes ficam
    assert action_state.get("r0") is None
    assert action_state.get("r4") is None
    assert action_state.get("r5") is not None
    assert action_state.get("r24") is not None
    action_state._RUNS.clear()
