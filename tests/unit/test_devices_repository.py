from middleware_monitor.domain.devices.repository import (
    delete_devices,
    list_devices,
    record_ping,
    status_counts,
    upsert_from_uscall,
)
from middleware_monitor.domain.extension_configurator import repository as ec_repo


def test_upsert_creates_new_when_available(db) -> None:
    upsert_from_uscall(db, [
        {"ramal": "3660", "status": "disponivel", "ip": "10.0.0.1"},
    ])
    db.commit()
    rows, total = list_devices(db)
    assert total == 1
    assert rows[0].ip == "10.0.0.1"
    assert rows[0].logical_status == "available"


def test_upsert_updates_existing_ip(db) -> None:
    upsert_from_uscall(db, [{"ramal": "3660", "status": "disponivel", "ip": "10.0.0.1"}])
    db.commit()
    upsert_from_uscall(db, [{"ramal": "3660", "status": "disponivel", "ip": "10.0.0.99"}])
    db.commit()
    rows, _ = list_devices(db)
    assert rows[0].ip == "10.0.0.99"


def test_record_ping_updates_device(db) -> None:
    upsert_from_uscall(db, [{"ramal": "3660", "status": "disponivel", "ip": "10.0.0.1"}])
    db.commit()
    rows, _ = list_devices(db)
    record_ping(db, rows[0], online=True, latency_ms=4)
    db.commit()
    counts = status_counts(db)
    assert counts["network_online"] == 1
    assert counts["avg_latency_ms"] == 4


def _seed_many(db) -> None:
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.10"},
        {"ramal": "3050", "status": "disponivel", "ip": "10.0.0.200"},
        {"ramal": "3100", "status": "disponivel", "ip": "10.0.1.5"},
        {"ramal": "recepcao", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    db.commit()


def test_filter_ip_range(db) -> None:
    _seed_many(db)
    # 10.0.0.10 .. 10.0.0.200 inclui 3001, 3050 e recepcao(10.0.0.50); exclui 10.0.1.5
    rows, total = list_devices(db, ip_from="10.0.0.10", ip_to="10.0.0.200")
    names = {r.name for r in rows}
    assert total == 3
    assert names == {"3001", "3050", "recepcao"}


def test_filter_ip_range_aberto_em_um_lado(db) -> None:
    _seed_many(db)
    rows, total = list_devices(db, ip_from="10.0.1.0")
    assert total == 1
    assert rows[0].name == "3100"


def test_filter_ramal_range(db) -> None:
    _seed_many(db)
    # 3001..3050 inclui 3001 e 3050; 'recepcao' não é numérico → fora
    rows, total = list_devices(db, ramal_from=3001, ramal_to=3050)
    assert total == 2
    assert {r.name for r in rows} == {"3001", "3050"}


def test_filter_por_ambiente_e_sem_vinculo(db) -> None:
    _seed_many(db)
    env = ec_repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    ec_repo.save_lines(db, env, [
        ec_repo.new_line(ip="10.0.0.10", numero_ramal="3001"),  # vincula ao 3001
    ])
    db.commit()

    rows_env, total_env = list_devices(db, environment=env.id)
    assert total_env == 1 and rows_env[0].name == "3001"

    rows_none, total_none = list_devices(db, environment="none")
    assert "3001" not in {r.name for r in rows_none}
    assert total_none == 3


def test_delete_devices_desvincula_linha_e_apaga_pings(db) -> None:
    upsert_from_uscall(db, [{"ramal": "3001", "status": "disponivel", "ip": "10.0.0.10"}])
    env = ec_repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    ec_repo.save_lines(db, env, [ec_repo.new_line(ip="10.0.0.10", numero_ramal="3001")])
    db.commit()
    rows, _ = list_devices(db)
    dev = rows[0]
    record_ping(db, dev, online=True, latency_ms=5)
    db.commit()
    line = ec_repo.list_lines(db, env.id)[0]
    assert line.device_id == dev.id

    deleted = delete_devices(db, [dev.id])
    db.commit()
    assert deleted == 1

    _rows_after, total_after = list_devices(db)
    assert total_after == 0
    # linha e ambiente preservados, só desvinculados
    line_after = ec_repo.list_lines(db, env.id)[0]
    assert line_after.device_id is None


def test_delete_devices_lista_vazia(db) -> None:
    assert delete_devices(db, []) == 0
