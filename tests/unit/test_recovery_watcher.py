"""Testes do watcher `_trigger_recovery_reapplies` (Fase B)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.domain.devices.repository import upsert_from_uscall
from middleware_monitor.domain.extension_configurator import (
    repository as ec_repo,
)
from middleware_monitor.jobs import monitor_devices as md_job


def _seed_linked_line(
    db: DBSession,
    *,
    ip: str = "10.0.0.50",
    ramal: str = "3001",
    pbx_status: str = "disponivel",
) -> tuple[int, str]:
    env = ec_repo.create_environment(
        db, nome="Filial", modelo_telefone="HTEK UC902G",
    )
    lines = ec_repo.save_lines(db, env, [
        ec_repo.new_line(ip=ip, numero_ramal=ramal),
    ])
    # Cria device com status disponivel (upsert só cria devices available)
    upsert_from_uscall(db, [
        {"ramal": ramal, "status": "disponivel", "ip": ip},
    ])
    db.commit()
    ln = ec_repo.get_line(db, lines[0].id)
    assert ln is not None and ln.device_id is not None
    # Se o cenário precisa de PBX unavailable, força após criação.
    if pbx_status != "disponivel":
        from sqlalchemy import select

        from middleware_monitor.core.models import Device

        dev = db.scalar(select(Device).where(Device.id == ln.device_id))
        assert dev is not None
        dev.logical_status = "unavailable"
        db.commit()
    return ln.device_id, ln.id


def _set_network_status(db: DBSession, device_id: int, status: str) -> None:
    from sqlalchemy import select

    from middleware_monitor.core.models import Device

    dev = db.scalar(select(Device).where(Device.id == device_id))
    assert dev is not None
    dev.network_status = status
    db.commit()


def test_candidates_inclui_online_unavailable_sem_recovery(db: DBSession) -> None:
    """Telefone que respondeu ICMP o tempo todo (nunca offline) mas está
    'unavailable' no PBX deve virar candidato mesmo sem nenhum recovered_id —
    é o cenário onde a config mudou sem o aparelho cair."""
    device_id, _ = _seed_linked_line(db, pbx_status="indisponivel")
    _set_network_status(db, device_id, "online")

    ids = md_job._reapply_candidate_ids(db, [])
    assert device_id in ids


def test_candidates_ignora_online_available(db: DBSession) -> None:
    """Device online + available no PBX (registrado e provisionado) não é
    candidato — nada a reaplicar."""
    device_id, _ = _seed_linked_line(db)  # available
    _set_network_status(db, device_id, "online")

    ids = md_job._reapply_candidate_ids(db, [])
    assert device_id not in ids


def test_candidates_ignora_offline_unavailable(db: DBSession) -> None:
    """Device offline (não responde ICMP) não é candidato: não há como aplicar
    config num aparelho inalcançável. Só entra se voltar (via recovered_ids)."""
    device_id, _ = _seed_linked_line(db, pbx_status="indisponivel")
    _set_network_status(db, device_id, "offline")

    assert device_id not in md_job._reapply_candidate_ids(db, [])
    # mas se acabou de voltar (recovered), entra pela união
    assert device_id in md_job._reapply_candidate_ids(db, [device_id])


@pytest.mark.asyncio
async def test_trigger_recovery_dispara_apply_single_line(
    db: DBSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # device 'unavailable' no PBX simula o cenário onde devemos reaplicar
    device_id, line_id = _seed_linked_line(db, pbx_status="indisponivel")
    called: list[dict[str, str]] = []

    async def fake_run(line_id_arg: str, *, operador: str, reason: str):  # type: ignore[no-untyped-def]
        called.append({"line_id": line_id_arg, "operador": operador, "reason": reason})
        return ("rid", 1, 1)

    monkeypatch.setattr(
        md_job.ec_apply, "run_apply_single_line", fake_run,
    )

    await md_job._trigger_recovery_reapplies(
        [device_id], debounce_minutes=60,
    )
    assert len(called) == 1
    assert called[0]["line_id"] == line_id
    assert called[0]["operador"] == "auto-watcher"
    assert called[0]["reason"] == "recovery"


@pytest.mark.asyncio
async def test_trigger_recovery_respeita_debounce(
    db: DBSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    device_id, line_id = _seed_linked_line(db, pbx_status="indisponivel")
    # Cria evento recente de recovery (15min atrás)
    ev = ec_repo.create_reapply_event(
        db, line_id=line_id, device_id=device_id, reason="recovery",
    )
    ev.started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=15)
    db.commit()

    called: list[str] = []

    async def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        called.append(args[0])
        return ("rid", 1, 1)

    monkeypatch.setattr(md_job.ec_apply, "run_apply_single_line", fake_run)

    await md_job._trigger_recovery_reapplies(
        [device_id], debounce_minutes=60,  # 60 > 15 ⇒ deve PULAR
    )
    assert called == []

    # Agora com debounce curto (10min) — 15min > 10min ⇒ deve disparar
    await md_job._trigger_recovery_reapplies(
        [device_id], debounce_minutes=10,
    )
    assert called == [line_id]


@pytest.mark.asyncio
async def test_trigger_recovery_desiste_apos_erro_ate_reregistro(
    db: DBSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Após um recovery que falhou (ambas credenciais), o watcher para de
    tentar — até o telefone reregistrar (last_seen_at avança) ou intervenção
    manual."""
    device_id, line_id = _seed_linked_line(db, pbx_status="indisponivel")

    from sqlalchemy import select

    from middleware_monitor.core.models import Device

    dev = db.scalar(select(Device).where(Device.id == device_id))
    assert dev is not None
    t0 = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=30)
    dev.last_seen_at = t0  # última vez que registrou
    db.commit()

    # recovery falhou DEPOIS da última vez online (mesmo episódio de queda)
    ev = ec_repo.create_reapply_event(
        db, line_id=line_id, device_id=device_id, reason="recovery",
    )
    ev.status = "erro"
    ev.started_at = t0 + timedelta(minutes=10)
    db.commit()

    called: list[str] = []

    async def fake_run(line_id_arg: str, *, operador: str, reason: str):  # type: ignore[no-untyped-def]
        called.append(line_id_arg)
        return ("rid", 1, 1)

    monkeypatch.setattr(md_job.ec_apply, "run_apply_single_line", fake_run)

    # debounce curto (1 min) — quem deve segurar é a regra de desistência
    await md_job._trigger_recovery_reapplies([device_id], debounce_minutes=1)
    assert called == []  # desistiu

    # telefone reregistrou (last_seen_at agora é POSTERIOR à falha) → novo episódio
    dev.last_seen_at = t0 + timedelta(minutes=20)
    db.commit()
    await md_job._trigger_recovery_reapplies([device_id], debounce_minutes=1)
    assert called == [line_id]


@pytest.mark.asyncio
async def test_trigger_recovery_pula_se_uscall_available(
    db: DBSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Se device.logical_status == 'available' (PBX vê o ramal), NÃO reaplica.
    Só age quando o PBX continua reportando unavailable apesar do ICMP voltar."""
    device_id, line_id = _seed_linked_line(db)
    # _seed_linked_line cria device com status='disponivel' (=available no PBX)
    from sqlalchemy import select

    from middleware_monitor.core.models import Device

    dev = db.scalar(select(Device).where(Device.id == device_id))
    assert dev is not None
    assert dev.logical_status == "available"

    called: list[str] = []

    async def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        called.append(args[0])
        return ("rid", 1, 1)

    monkeypatch.setattr(md_job.ec_apply, "run_apply_single_line", fake_run)
    await md_job._trigger_recovery_reapplies(
        [device_id], debounce_minutes=60,
    )
    # Available no PBX → não toma ação
    assert called == []

    # Agora simulamos: USCall reportou unavailable (PBX não vê o ramal)
    dev.logical_status = "unavailable"
    db.commit()
    await md_job._trigger_recovery_reapplies(
        [device_id], debounce_minutes=60,
    )
    assert called == [line_id]


@pytest.mark.asyncio
async def test_trigger_recovery_pula_linhas_sem_ip(
    db: DBSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linha sem IP (mesmo vinculada) não deve disparar."""
    env = ec_repo.create_environment(
        db, nome="Filial", modelo_telefone="HTEK UC902G",
    )
    lines = ec_repo.save_lines(db, env, [
        ec_repo.new_line(ip="", numero_ramal="3001"),
    ])
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    db.commit()
    # Vincula manual (sem IP na linha, mas device existe) e força unavailable
    # no PBX pra atender a regra do watcher (só age quando PBX não vê o ramal).
    from sqlalchemy import select

    from middleware_monitor.core.models import Device

    dev = db.scalar(select(Device).where(Device.name == "3001"))
    assert dev is not None
    dev.logical_status = "unavailable"
    ln = ec_repo.get_line(db, lines[0].id)
    assert ln is not None
    ec_repo.link_line_to_device(db, ln, dev)
    db.commit()

    called: list[str] = []

    async def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        called.append(args[0])
        return ("rid", 1, 1)

    monkeypatch.setattr(md_job.ec_apply, "run_apply_single_line", fake_run)
    await md_job._trigger_recovery_reapplies(
        [dev.id], debounce_minutes=60,
    )
    assert called == []
