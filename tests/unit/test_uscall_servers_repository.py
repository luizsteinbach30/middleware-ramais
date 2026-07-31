"""Unit tests do repository de servidores USCall (multi-servidor v2.7.0)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.models import Device
from middleware_monitor.domain.uscall import repository as repo


def test_create_server_cifra_o_token(db: DBSession) -> None:
    srv = repo.create_server(
        db, nome="Principal", host="pbx.cliente.com", token_plain="tok-secreto",
    )
    db.commit()
    assert srv.id is not None
    assert srv.token != ""
    assert "tok-secreto" not in srv.token          # nunca plaintext no banco
    assert repo.load_server_token(srv) == "tok-secreto"


def test_create_server_sem_token(db: DBSession) -> None:
    srv = repo.create_server(db, nome="Lab", host="10.0.0.1", token_plain="")
    db.commit()
    assert srv.token == ""
    assert repo.load_server_token(srv) is None


def test_list_servers_filtra_enabled(db: DBSession) -> None:
    repo.create_server(db, nome="A", host="a.local", token_plain="t1")
    repo.create_server(db, nome="B", host="b.local", token_plain="t2", enabled=False)
    db.commit()
    assert [s.nome for s in repo.list_servers(db)] == ["A", "B"]
    assert [s.nome for s in repo.list_servers(db, enabled_only=True)] == ["A"]


def test_update_server_parcial_mantem_token(db: DBSession) -> None:
    srv = repo.create_server(db, nome="A", host="a.local", token_plain="original")
    db.commit()
    old_cipher = srv.token
    # nome/host mudam, token_plain=None mantém o token cifrado intacto
    repo.update_server(db, srv, nome="A2", host="a2.local", token_plain=None)
    db.commit()
    assert srv.nome == "A2"
    assert srv.host == "a2.local"
    assert srv.token == old_cipher
    assert repo.load_server_token(srv) == "original"
    # token novo re-cifra
    repo.update_server(db, srv, token_plain="novo")
    db.commit()
    assert srv.token != old_cipher
    assert repo.load_server_token(srv) == "novo"


def test_delete_server_desvincula_devices(db: DBSession) -> None:
    srv = repo.create_server(db, nome="A", host="a.local", token_plain="t")
    now = datetime.now(UTC).replace(tzinfo=None)
    dev = Device(
        name="3660", ip="10.0.0.20", uscall_server_id=srv.id,
        created_at=now, updated_at=now,
    )
    db.add(dev)
    db.commit()
    assert repo.delete_server(db, srv.id) is True
    db.commit()
    db.refresh(dev)
    assert dev.uscall_server_id is None            # device preservado, sem origem
    assert repo.get_server(db, srv.id) is None
    assert repo.delete_server(db, 9999) is False
