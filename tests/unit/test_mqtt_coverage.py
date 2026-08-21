"""Prova de cobertura — o que separa "ninguém publicou" de "ninguém ouviu"."""

from __future__ import annotations

from datetime import datetime, timedelta

from middleware_monitor.domain.mqtt.coverage import compute_coverage

INICIO = datetime(2026, 8, 19, 12, 0, 0)
FIM = INICIO + timedelta(hours=1)


def test_conectado_o_periodo_inteiro_e_cobertura_total() -> None:
    cov = compute_coverage([], INICIO, FIM, state_before="connected")
    assert cov.covered_seconds == 3600
    assert cov.coverage_pct == 100.0
    assert cov.gaps == []


def test_queda_no_meio_vira_lacuna_com_duracao() -> None:
    eventos = [
        (INICIO + timedelta(minutes=20), "disconnected", "conexão perdida"),
        (INICIO + timedelta(minutes=25), "connected", "conectado"),
    ]
    cov = compute_coverage(eventos, INICIO, FIM, state_before="connected")
    assert cov.covered_seconds == 3600 - 300
    assert len(cov.gaps) == 1
    assert cov.gaps[0].seconds == 300
    assert "perdida" in cov.gaps[0].detail


def test_sem_historico_o_periodo_e_desconhecido_nao_coberto() -> None:
    cov = compute_coverage([], INICIO, FIM, state_before=None)
    assert cov.unknown is True
    assert cov.covered_seconds == 0
    assert cov.coverage_pct == 0.0
    assert cov.gaps and cov.gaps[0].seconds == 3600


def test_fora_do_ar_o_periodo_todo() -> None:
    cov = compute_coverage([], INICIO, FIM, state_before="disconnected")
    assert cov.covered_seconds == 0
    assert cov.gaps[0].seconds == 3600


def test_reinicio_sem_encerramento_limpo_nao_conta_como_coberto() -> None:
    # O processo morreu (sem "stopped"); no boot seguinte grava "startup".
    # O que houve entre uma coisa e outra ninguém pode afirmar.
    eventos = [
        (INICIO + timedelta(minutes=30), "startup", "coletor iniciado"),
        (INICIO + timedelta(minutes=31), "connected", "conectado"),
    ]
    cov = compute_coverage(eventos, INICIO, FIM, state_before="connected")
    assert cov.covered_seconds == 3600 - 60 - 0 or cov.covered_seconds < 3600
    assert any("não comprovado" in g.detail for g in cov.gaps)


def test_parada_limpa_e_volta_marca_a_janela_parada() -> None:
    eventos = [
        (INICIO + timedelta(minutes=10), "stopped", "coletor encerrado"),
        (INICIO + timedelta(minutes=40), "startup", "coletor iniciado"),
        (INICIO + timedelta(minutes=40, seconds=2), "connected", "conectado"),
    ]
    cov = compute_coverage(eventos, INICIO, FIM, state_before="connected")
    assert 1700 <= cov.covered_seconds <= 1810  # ~10 min + ~20 min
    assert any(g.seconds >= 1800 for g in cov.gaps)


def test_janela_vazia_nao_quebra() -> None:
    cov = compute_coverage([], INICIO, INICIO, state_before="connected")
    assert cov.total_seconds == 0
    assert cov.coverage_pct == 0.0


def test_reconexao_instantanea_nao_vira_lacuna() -> None:
    # Queda e volta no mesmo segundo: nada foi perdido, então não polui a lista.
    momento = INICIO + timedelta(minutes=10)
    eventos = [
        (momento, "disconnected", "conexão perdida"),
        (momento, "connected", "conectado"),
    ]
    cov = compute_coverage(eventos, INICIO, FIM, state_before="connected")
    assert cov.gaps == []
    assert cov.coverage_pct == 100.0
