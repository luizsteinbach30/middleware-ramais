"""Unit tests do service.py (hashes, statuses, picker)."""

from __future__ import annotations

from sqlalchemy.orm import Session as DBSession

from middleware_monitor.domain.extension_configurator import (
    repository as repo,
)
from middleware_monitor.domain.extension_configurator import (
    service,
)


def _env_with_lines(db: DBSession, modelo: str) -> tuple:
    """Cria env + 3 linhas. Devolve `(env, by_ramal)` para evitar depender
    da ordem do `list_lines` (que pode reordenar quando created_at coincide)."""
    env = repo.create_environment(db, nome="Lab", modelo_telefone=modelo)
    repo.update_environment(db, env, config_padrao={"sip_server": "pbx.test"})
    repo.save_lines(db, env, [
        repo.new_line(ip="192.168.0.10", numero_ramal="3660", senha_sip="s1"),
        repo.new_line(ip="192.168.0.11", numero_ramal="3661", senha_sip="s2"),
        repo.new_line(ip="", numero_ramal="3662", senha_sip="s3"),  # sem IP
    ])
    db.commit()
    by_ramal = {ln.numero_ramal: ln for ln in repo.list_lines(db, env.id)}
    return env, by_ramal


def test_adapter_for_intelbras_e_htek() -> None:
    a = service.adapter_for("Intelbras V5501")
    assert a.vendor_id == "intelbras"
    b = service.adapter_for("HTEK UC902G")
    assert b.vendor_id == "htek"
    # default cai em HTEK
    c = service.adapter_for("desconhecido")
    assert c.vendor_id == "htek"


def test_compute_line_hash_e_determ_para_mesmo_input(db: DBSession) -> None:
    env, by_ramal = _env_with_lines(db, "HTEK UC902G")
    h1 = service.compute_line_hash(env, by_ramal["3660"])
    h2 = service.compute_line_hash(env, by_ramal["3660"])
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex
    # linhas diferentes geram hashes diferentes
    assert h1 != service.compute_line_hash(env, by_ramal["3661"])


def test_compute_statuses_pending_para_linha_sem_ip(db: DBSession) -> None:
    env, by_ramal = _env_with_lines(db, "HTEK UC902G")
    lines = list(by_ramal.values())
    statuses = {s["id"]: s for s in service.compute_statuses(env, lines)}
    sem_ip = by_ramal["3662"]
    com_ip = by_ramal["3660"]
    assert statuses[com_ip.id]["status"] == "pending"  # nunca aplicado
    assert statuses[sem_ip.id]["status"] == "pending"
    assert statuses[sem_ip.id]["hash_atual"] == ""


def test_line_status_applied_e_outdated(db: DBSession) -> None:
    env, by_ramal = _env_with_lines(db, "HTEK UC902G")
    ln = by_ramal["3660"]
    h_now = service.compute_line_hash(env, ln)
    repo.update_line_status(db, ln, status="ok", hash_aplicado=h_now)
    db.commit()
    assert service.line_status(ln, h_now) == "applied"
    assert service.line_status(ln, "0" * 64) == "outdated"


def test_line_status_error_quando_ultima_erro(db: DBSession) -> None:
    _env, by_ramal = _env_with_lines(db, "HTEK UC902G")
    repo.update_line_status(db, by_ramal["3660"], status="erro", erro="ping falhou")
    db.commit()
    assert service.line_status(by_ramal["3660"], "abc") == "error"


def test_pick_lines_to_apply_pula_sem_ip_e_applied(db: DBSession) -> None:
    env, by_ramal = _env_with_lines(db, "HTEK UC902G")
    com_ip_aplicada = by_ramal["3660"]
    com_ip_pendente = by_ramal["3661"]
    sem_ip = by_ramal["3662"]
    h = service.compute_line_hash(env, com_ip_aplicada)
    repo.update_line_status(db, com_ip_aplicada, status="ok", hash_aplicado=h)
    db.commit()
    picked = service.pick_lines_to_apply(
        env, list(by_ramal.values()), force=False, selected_ids=None,
    )
    ids = [ln.id for ln, _ in picked]
    assert com_ip_aplicada.id not in ids
    assert com_ip_pendente.id in ids
    assert sem_ip.id not in ids


def test_pick_lines_to_apply_force_pega_tudo_com_ip(db: DBSession) -> None:
    env, by_ramal = _env_with_lines(db, "HTEK UC902G")
    com_ip_aplicada = by_ramal["3660"]
    com_ip_pendente = by_ramal["3661"]
    sem_ip = by_ramal["3662"]
    h = service.compute_line_hash(env, com_ip_aplicada)
    repo.update_line_status(db, com_ip_aplicada, status="ok", hash_aplicado=h)
    db.commit()
    picked = service.pick_lines_to_apply(
        env, list(by_ramal.values()), force=True, selected_ids=None,
    )
    ids = [ln.id for ln, _ in picked]
    assert com_ip_aplicada.id in ids
    assert com_ip_pendente.id in ids
    assert sem_ip.id not in ids


def test_pick_lines_to_apply_selected_ids_ignora_filtro_applied(db: DBSession) -> None:
    env, by_ramal = _env_with_lines(db, "HTEK UC902G")
    ln = by_ramal["3660"]
    h = service.compute_line_hash(env, ln)
    repo.update_line_status(db, ln, status="ok", hash_aplicado=h)
    db.commit()
    picked = service.pick_lines_to_apply(
        env, list(by_ramal.values()), force=False, selected_ids=[ln.id],
    )
    assert [x.id for x, _ in picked] == [ln.id]


def test_pick_lines_to_apply_selected_filtra_sem_ip(db: DBSession) -> None:
    env, by_ramal = _env_with_lines(db, "HTEK UC902G")
    sem_ip = by_ramal["3662"]
    picked = service.pick_lines_to_apply(
        env, list(by_ramal.values()), force=False, selected_ids=[sem_ip.id],
    )
    assert picked == []
