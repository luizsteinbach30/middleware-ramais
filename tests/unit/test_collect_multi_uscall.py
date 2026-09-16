"""Coleta multi-servidor USCall: merge_payloads, falha parcial e origem no upsert."""

from __future__ import annotations

import json

import httpx
import respx

from middleware_monitor.domain.devices.repository import upsert_from_uscall
from middleware_monitor.domain.uscall import repository as uscall_repo
from middleware_monitor.jobs.collect_extensions import merge_payloads

# ------------------------------------------------------------ merge_payloads


def test_merge_uniao_com_campo_aditivo() -> None:
    merged, by_ramal = merge_payloads([
        (1, "Matriz", [{"ramal": "3660", "ip": "10.0.0.1", "status": "disponivel"}]),
        (2, "Filial", [{"ramal": "4100", "ip": "10.1.0.1", "status": "disponivel"}]),
    ])
    assert [m["ramal"] for m in merged] == ["3660", "4100"]
    assert merged[0]["uscall_server"] == "Matriz"
    assert merged[1]["uscall_server"] == "Filial"
    # shape flat original preservado (só a chave aditiva entrou)
    assert set(merged[0]) == {"ramal", "ip", "status", "uscall_server"}
    assert by_ramal == {"3660": 1, "4100": 2}


def test_merge_ramal_duplicado_primeiro_vence() -> None:
    merged, by_ramal = merge_payloads([
        (1, "Matriz", [{"ramal": "3660", "status": "disponivel"}]),
        (2, "Filial", [{"ramal": "3660", "status": "indisponivel"}]),
    ])
    assert len(merged) == 1
    assert merged[0]["uscall_server"] == "Matriz"
    assert merged[0]["status"] == "disponivel"
    assert by_ramal == {"3660": 1}


def test_merge_nao_muta_payload_original() -> None:
    rows = [{"ramal": "3660", "status": "disponivel"}]
    merge_payloads([(1, "Matriz", rows)])
    assert "uscall_server" not in rows[0]


# ------------------------------------------------- coletor com falha parcial


@respx.mock
async def test_collect_um_servidor_fora_nao_derruba_os_demais(db, monkeypatch) -> None:
    uscall_repo.create_server(db, nome="Matriz", host="pbx-a.test", token_plain="ta")
    uscall_repo.create_server(db, nome="Filial", host="pbx-b.test", token_plain="tb")
    db.commit()

    respx.get("https://pbx-a.test/api/extenstatus").mock(
        return_value=httpx.Response(
            200, json=[{"ramal": "3660", "ip": "10.0.0.1", "status": "disponivel"}],
        )
    )
    respx.get("https://pbx-b.test/api/extenstatus").mock(side_effect=httpx.ConnectError)

    from middleware_monitor.jobs import collect_extensions as job

    await job.run_collect_extensions()

    # O payload parcial (só a Matriz) foi persistido, com a origem marcada.
    # Até a v2.14.0 quem media isto era o webhook despachado; o módulo saiu, e o
    # snapshot é o observável melhor — é o que sobrevive ao ciclo.
    from middleware_monitor.core.models import Collection

    snaps = db.query(Collection).filter_by(type="extensions").all()
    assert len(snaps) == 1
    payload = json.loads(snaps[0].payload)
    assert [p["ramal"] for p in payload] == ["3660"]
    assert payload[0]["uscall_server"] == "Matriz"

    from middleware_monitor.core.models import Device

    dev = db.query(Device).filter_by(name="3660").one()
    srv = uscall_repo.list_servers(db)[0]
    assert dev.uscall_server_id == srv.id


@respx.mock
async def test_collect_todos_fora_nao_grava_snapshot(db) -> None:
    """Falha total não vira snapshot vazio: um retrato de zero ramais seria lido
    como "o PBX não tem ramal", e não como "não deu para perguntar"."""
    uscall_repo.create_server(db, nome="Matriz", host="pbx-a.test", token_plain="ta")
    db.commit()
    respx.get("https://pbx-a.test/api/extenstatus").mock(side_effect=httpx.ConnectError)

    from middleware_monitor.jobs import collect_extensions as job

    await job.run_collect_extensions()

    from middleware_monitor.core.models import Collection

    assert db.query(Collection).count() == 0


async def test_collect_sem_servidores_skips(db) -> None:
    from middleware_monitor.jobs import collect_extensions as job

    # nenhum servidor cadastrado → não explode, só loga collect_skipped
    await job.run_collect_extensions()


# ------------------------------------------------------- upsert com origem


def test_upsert_grava_e_atualiza_origem(db) -> None:
    payload = [{"ramal": "3660", "ip": "10.0.0.1", "status": "disponivel"}]
    upsert_from_uscall(db, payload, server_id_by_ramal={})
    db.commit()

    from middleware_monitor.core.models import Device

    dev = db.query(Device).filter_by(name="3660").one()
    assert dev.uscall_server_id is None

    srv = uscall_repo.create_server(db, nome="Matriz", host="a.test", token_plain="t")
    db.commit()
    upsert_from_uscall(db, payload, server_id_by_ramal={"3660": srv.id})
    db.commit()
    db.refresh(dev)
    assert dev.uscall_server_id == srv.id
