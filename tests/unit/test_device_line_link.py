"""Unit tests da vinculação Device <-> ExtensionLine e reapply events."""

from __future__ import annotations

from sqlalchemy.orm import Session as DBSession

from middleware_monitor.domain.devices.repository import upsert_from_uscall
from middleware_monitor.domain.extension_configurator import (
    repository as repo,
)


def _make_env_with_line(
    db: DBSession, *, modelo: str = "HTEK UC902G", ip: str = "192.168.0.41",
    ramal: str = "9001",
) -> tuple[str, str]:
    env = repo.create_environment(db, nome="Lab", modelo_telefone=modelo)
    lines = repo.save_lines(db, env, [
        repo.new_line(ip=ip, numero_ramal=ramal),
    ])
    db.commit()
    return env.id, lines[0].id


def test_link_and_unlink_line_to_device(db: DBSession) -> None:
    # cria linha PRIMEIRO (sem device existente, save_lines não vai linkar)
    _, line_id = _make_env_with_line(db)
    line = repo.get_line(db, line_id)
    assert line is not None and line.device_id is None

    upsert_from_uscall(db, [
        {"ramal": "9001", "status": "disponivel", "ip": "192.168.0.41"},
    ])
    db.commit()
    # upsert_from_uscall faz auto-link → linha agora deve estar vinculada
    line = repo.get_line(db, line_id)
    assert line is not None and line.device_id is not None

    from sqlalchemy import select

    from middleware_monitor.core.models import Device

    dev = db.scalar(select(Device).where(Device.ip == "192.168.0.41"))
    assert dev is not None
    assert line.device_id == dev.id

    repo.unlink_line_from_device(db, line)
    db.commit()
    assert line.device_id is None

    repo.link_line_to_device(db, line, dev)
    db.commit()
    assert line.device_id == dev.id


def test_auto_link_lines_by_ip_vincula_quando_ip_bate(db: DBSession) -> None:
    # Linhas criadas ANTES dos devices: save_lines não acha device pra linkar.
    _, line_a = _make_env_with_line(db, ip="192.168.0.41", ramal="9001")
    env_b = repo.create_environment(db, nome="Outro", modelo_telefone="Intelbras V5501")
    lines_b = repo.save_lines(db, env_b, [
        repo.new_line(ip="192.168.0.42", numero_ramal="9002"),
    ])
    db.commit()
    line_b = lines_b[0].id

    # Cria os devices via inserção direta (sem passar pelo upsert que faz auto-link)
    from middleware_monitor.core.models import Device
    from datetime import UTC, datetime
    now = datetime.now(UTC).replace(tzinfo=None)
    db.add(Device(name="9001", ip="192.168.0.41", logical_status="available",
                  network_status="unknown", last_seen_at=now,
                  created_at=now, updated_at=now))
    db.add(Device(name="9002", ip="192.168.0.42", logical_status="available",
                  network_status="unknown", last_seen_at=now,
                  created_at=now, updated_at=now))
    db.commit()

    linked = repo.auto_link_lines_by_ip(db)
    db.commit()
    assert linked == 2

    a = repo.get_line(db, line_a)
    b = repo.get_line(db, line_b)
    assert a is not None and a.device_id is not None
    assert b is not None and b.device_id is not None

    # idempotência
    linked_again = repo.auto_link_lines_by_ip(db)
    assert linked_again == 0


def test_auto_link_lines_by_ip_ignora_linhas_sem_ip(db: DBSession) -> None:
    upsert_from_uscall(db, [
        {"ramal": "9001", "status": "disponivel", "ip": "192.168.0.41"},
    ])
    db.commit()
    env = repo.create_environment(db, nome="Sem IP", modelo_telefone="HTEK UC902G")
    lines = repo.save_lines(db, env, [
        repo.new_line(ip="", numero_ramal="9001"),
    ])
    db.commit()
    linked = repo.auto_link_lines_by_ip(db)
    assert linked == 0
    assert repo.get_line(db, lines[0].id).device_id is None  # type: ignore[union-attr]


def test_reapply_event_lifecycle(db: DBSession) -> None:
    env_id, line_id = _make_env_with_line(db)
    run = repo.create_run(db, env_id, total=1, forcado=False, operador="auto-watcher")
    db.commit()

    ev = repo.create_reapply_event(
        db, line_id=line_id, device_id=None, reason="recovery",
    )
    db.commit()
    assert ev.status == "running"
    assert ev.finished_at is None

    repo.finish_reapply_event(db, ev, status="ok", run_id=run.id)
    db.commit()
    assert ev.status == "ok"
    assert ev.finished_at is not None
    assert ev.run_id == run.id


def test_list_reapply_events_filtra_por_line(db: DBSession) -> None:
    _, line_a = _make_env_with_line(db, ramal="9001", ip="192.168.0.41")
    env_b = repo.create_environment(db, nome="Outro", modelo_telefone="HTEK UC902G")
    lines_b = repo.save_lines(db, env_b, [repo.new_line(numero_ramal="9002")])
    db.commit()
    line_b = lines_b[0].id

    repo.create_reapply_event(db, line_id=line_a, device_id=None, reason="recovery")
    repo.create_reapply_event(db, line_id=line_a, device_id=None, reason="manual_device_page")
    repo.create_reapply_event(db, line_id=line_b, device_id=None, reason="recovery")
    db.commit()

    a_events = repo.list_reapply_events(db, line_id=line_a)
    assert len(a_events) == 2
    assert all(e.line_id == line_a for e in a_events)

    last = repo.last_reapply_event_for_line(db, line_a)
    assert last is not None and last.reason == "manual_device_page"


def test_upsert_uscall_auto_links_orphan_extension_line(db: DBSession) -> None:
    """ExtensionLine cadastrada com IP X — quando USCall traz device com IP X,
    a linha deve ser vinculada automaticamente."""
    env = repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    lines = repo.save_lines(db, env, [
        repo.new_line(ip="10.0.0.50", numero_ramal="3001"),
    ])
    db.commit()
    line_id = lines[0].id

    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    db.commit()

    ln = repo.get_line(db, line_id)
    assert ln is not None and ln.device_id is not None

    # Re-upsert do mesmo device — não deve duplicar nem quebrar.
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    db.commit()
    ln2 = repo.get_line(db, line_id)
    assert ln2 is not None and ln2.device_id == ln.device_id


def test_upsert_uscall_nao_revincula_se_ja_vinculada(db: DBSession) -> None:
    """Se a linha já tem device_id (vinculação manual), upsert não sobrescreve."""
    env = repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    lines = repo.save_lines(db, env, [
        repo.new_line(ip="10.0.0.50", numero_ramal="3001"),
    ])
    db.commit()
    line_id = lines[0].id

    # Device "errado" vinculado manualmente
    from sqlalchemy import select

    from middleware_monitor.core.models import Device

    upsert_from_uscall(db, [
        {"ramal": "9999", "status": "disponivel", "ip": "10.0.0.99"},
    ])
    db.commit()
    other = db.scalar(select(Device).where(Device.name == "9999"))
    assert other is not None
    ln = repo.get_line(db, line_id)
    assert ln is not None
    repo.link_line_to_device(db, ln, other)
    db.commit()
    assert ln.device_id == other.id

    # Agora o device "certo" chega — vínculo manual deve ser preservado.
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    db.commit()
    ln_after = repo.get_line(db, line_id)
    assert ln_after is not None and ln_after.device_id == other.id


def test_save_lines_faz_auto_link_por_ip(db: DBSession) -> None:
    """Quando a planilha é salva, ExtensionLines com IP igual a Devices
    existentes devem ser vinculadas automaticamente."""
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
        {"ramal": "3002", "status": "disponivel", "ip": "10.0.0.51"},
    ])
    db.commit()
    env = repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    lines = repo.save_lines(db, env, [
        repo.new_line(ip="10.0.0.50", numero_ramal="3001"),
        repo.new_line(ip="10.0.0.51", numero_ramal="3002"),
        repo.new_line(ip="10.0.0.52", numero_ramal="3003"),  # sem device
    ])
    db.commit()
    by_ramal = {ln.numero_ramal: ln for ln in lines}
    assert by_ramal["3001"].device_id is not None
    assert by_ramal["3002"].device_id is not None
    assert by_ramal["3003"].device_id is None


def test_save_lines_zera_vinculo_quando_ip_muda(db: DBSession) -> None:
    """Se o usuário trocar o IP de uma linha vinculada e o novo IP não bater
    com o device anterior, o vínculo deve ser zerado (auto-link revincula se
    achar match)."""
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    db.commit()
    env = repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    lines = repo.save_lines(db, env, [
        repo.new_line(ip="10.0.0.50", numero_ramal="3001"),
    ])
    db.commit()
    assert lines[0].device_id is not None
    line_id = lines[0].id

    # usuário muda o IP da linha pra outro
    repo.save_lines(db, env, [
        {"id": line_id, "ip": "10.0.0.99", "numero_ramal": "3001"},
    ])
    db.commit()
    ln = repo.get_line(db, line_id)
    assert ln is not None and ln.device_id is None


def test_get_device_info_for_lines_retorna_so_vinculadas(db: DBSession) -> None:
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    db.commit()
    env = repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    lines = repo.save_lines(db, env, [
        repo.new_line(ip="10.0.0.50", numero_ramal="3001"),
        repo.new_line(ip="10.0.0.99", numero_ramal="3002"),
    ])
    db.commit()
    info = repo.get_device_info_for_lines(db, lines)
    assert len(info) == 1
    vinc = next(ln for ln in lines if ln.device_id is not None)
    assert info[vinc.id]["device_name"] == "3001"


def test_uscall_propaga_ip_para_linha_vinculada(db: DBSession) -> None:
    """Quando USCall traz o mesmo ramal com IP diferente, o IP da linha
    vinculada também deve atualizar."""
    env = repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    lines = repo.save_lines(db, env, [
        repo.new_line(ip="192.168.0.61", numero_ramal="3678", nome_visivel="Recepção"),
    ])
    db.commit()
    upsert_from_uscall(db, [
        {"ramal": "3678", "status": "disponivel", "ip": "192.168.0.61"},
    ])
    db.commit()
    line_id = lines[0].id
    ln = repo.get_line(db, line_id)
    assert ln is not None and ln.device_id is not None  # vinculou no upsert

    # USCall agora traz o mesmo ramal num IP diferente (DHCP / troca de rede)
    upsert_from_uscall(db, [
        {"ramal": "3678", "status": "disponivel", "ip": "192.168.0.99"},
    ])
    db.commit()

    ln2 = repo.get_line(db, line_id)
    assert ln2 is not None
    assert ln2.ip == "192.168.0.99"

    from sqlalchemy import select

    from middleware_monitor.core.models import Device

    dev = db.scalar(select(Device).where(Device.name == "3678"))
    assert dev is not None and dev.ip == "192.168.0.99"


def test_uscall_nao_mexe_em_linha_de_outro_device(db: DBSession) -> None:
    """IP do device A muda — linhas vinculadas só ao device A são afetadas,
    linhas de outros devices ficam intactas."""
    env = repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    lines = repo.save_lines(db, env, [
        repo.new_line(ip="10.0.0.10", numero_ramal="3001"),
        repo.new_line(ip="10.0.0.11", numero_ramal="3002"),
    ])
    db.commit()
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.10"},
        {"ramal": "3002", "status": "disponivel", "ip": "10.0.0.11"},
    ])
    db.commit()
    l1_id, l2_id = lines[0].id, lines[1].id

    # Só o 3001 muda de IP
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.99"},
        {"ramal": "3002", "status": "disponivel", "ip": "10.0.0.11"},
    ])
    db.commit()
    assert repo.get_line(db, l1_id).ip == "10.0.0.99"  # type: ignore[union-attr]
    assert repo.get_line(db, l2_id).ip == "10.0.0.11"  # type: ignore[union-attr]


def test_app_config_persists_auto_reapply_flags(db: DBSession) -> None:
    from middleware_monitor.domain.config.repository import (
        load_config,
        update_config,
    )
    from middleware_monitor.domain.config.schemas import AppConfigUpdate

    cfg = load_config(db)
    assert cfg.auto_reapply_on_recovery is False
    assert cfg.auto_reapply_debounce_minutes == 60

    update_config(
        db,
        AppConfigUpdate(
            auto_reapply_on_recovery=True, auto_reapply_debounce_minutes=15,
        ),
        user_id=None,
    )
    cfg2 = load_config(db)
    assert cfg2.auto_reapply_on_recovery is True
    assert cfg2.auto_reapply_debounce_minutes == 15
