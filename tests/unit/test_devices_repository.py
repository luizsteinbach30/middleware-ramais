from middleware_monitor.domain.devices.repository import (
    list_devices,
    record_ping,
    status_counts,
    upsert_from_uscall,
)


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
