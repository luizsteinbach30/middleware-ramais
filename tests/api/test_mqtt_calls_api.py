"""API de chamadas e o resumo diário.

O resumo não é "contar as linhas de /calls": ele deduplica por `uniqueid`, e é
essa diferença que estes testes fixam — foi ela que impediu um grupo de captura
de inflar o número de chamadas perdidas em quase três vezes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from middleware_monitor.core.models import ExtensionCall
from middleware_monitor.domain.auth.service import bootstrap_admin
from middleware_monitor.domain.mqtt import calls as calls_domain

BASE = datetime(2026, 8, 21, 14, 0, 0)
DIA = "2026-08-21"


def _authed(client, db) -> None:
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post("/api/auth/login", json={"username": user.username, "password": plaintext})
    assert r.status_code == 200, r.json()


def _call(db, **over) -> ExtensionCall:
    dados = {
        "ramal": "9959", "direcao": "entrante", "numero": "1211", "uniqueid": None,
        "started_at": BASE, "answered_at": BASE + timedelta(seconds=5),
        "ended_at": BASE + timedelta(seconds=45), "ring_seconds": 5,
        "talk_seconds": 40, "outcome": "atendida", "last_event_id": 1,
        "created_at": BASE, "updated_at": BASE,
    }
    dados.update(over)
    c = ExtensionCall(**dados)
    db.add(c)
    db.flush()
    return c


def test_lista_exige_sessao(client) -> None:
    assert client.get("/api/mqtt/calls").status_code == 401


def test_lista_traz_as_chamadas_do_periodo(client, db) -> None:
    _authed(client, db)
    _call(db)
    _call(db, ramal="1211", direcao="sainte", numero=None, ring_seconds=None)
    db.commit()

    body = client.get("/api/mqtt/calls?last=24h").json()
    assert body["total"] == 2
    assert {i["ramal"] for i in body["items"]} == {"9959", "1211"}
    recebida = next(i for i in body["items"] if i["ramal"] == "9959")
    assert recebida["direcao"] == "entrante"
    assert recebida["talk_seconds"] == 40


def test_filtros_por_trecho_direcao_e_resultado(client, db) -> None:
    _authed(client, db)
    _call(db, ramal="9950", numero="11966715065", outcome="perdida",
          answered_at=None, talk_seconds=None)
    _call(db, ramal="9951", direcao="sainte", numero="800")
    db.commit()

    # Ramal por trecho, como no ledger de mensagens.
    assert client.get("/api/mqtt/calls?last=24h&ramal=995").json()["total"] == 2
    assert client.get("/api/mqtt/calls?last=24h&ramal=9950").json()["total"] == 1
    # Número da outra ponta também por trecho.
    assert client.get("/api/mqtt/calls?last=24h&numero=1196").json()["total"] == 1
    assert client.get("/api/mqtt/calls?last=24h&direcao=sainte").json()["total"] == 1
    assert client.get("/api/mqtt/calls?last=24h&outcome=perdida").json()["total"] == 1


def test_resumo_diario_deduplica_grupo_de_captura(client, db) -> None:
    """A regra que impede o número de perdidas de inflar.

    Medido em produção: uma ligação externa rodou um grupo de captura e gerou 11
    pernas "perdida" para **uma** chamada. Contar as pernas cruas mostraria 11
    perdidas; o resumo tem de mostrar uma por ramal.
    """
    _authed(client, db)
    # O mesmo uniqueid tocou o mesmo ramal três vezes (rodízio do grupo).
    for i in range(3):
        _call(db, ramal="3660", uniqueid="Z", outcome="perdida",
              answered_at=None, talk_seconds=None,
              started_at=BASE + timedelta(seconds=i * 30))
    # E uma chamada de saída, sem uniqueid — essa conta sozinha.
    _call(db, ramal="3660", direcao="sainte", numero="800", uniqueid=None)
    db.commit()

    assert client.get("/api/mqtt/calls?last=24h").json()["total"] == 4  # pernas cruas

    calls_domain.rebuild_daily_stats(db, DIA)
    db.commit()

    (resumo,) = client.get(f"/api/mqtt/calls/daily?dia={DIA}").json()
    assert resumo["ramal"] == "3660"
    assert resumo["chamadas"] == 2, "3 pernas do mesmo uniqueid contam uma vez"
    assert resumo["perdidas"] == 1
    assert resumo["saintes"] == 1
    assert resumo["atendidas"] == 1


def test_resumo_ignora_chamada_em_curso(client, db) -> None:
    # Chamada que ainda não acabou não tem duração; entrar no resumo distorceria
    # a média e mudaria o número a cada recálculo.
    _authed(client, db)
    _call(db, outcome="em_curso", ended_at=None, talk_seconds=None)
    db.commit()
    calls_domain.rebuild_daily_stats(db, DIA)
    db.commit()
    assert client.get(f"/api/mqtt/calls/daily?dia={DIA}").json() == []


def test_dia_invalido_e_recusado(client, db) -> None:
    _authed(client, db)
    assert client.get("/api/mqtt/calls/daily?dia=ontem").status_code == 422


def test_reconstrucao_e_idempotente(db) -> None:
    """Rodar duas vezes não pode duplicar chamada.

    O job roda de minuto em minuto e as pernas ainda abertas voltam para o
    processamento a cada passagem — sem idempotência, cada rodada criaria uma
    linha nova para a mesma chamada em curso.
    """
    from middleware_monitor.core.models import ExtensionStatusEvent

    eventos = [
        ("9959", "tocando", "1211", 0),
        ("9959", "ocupado", "1211", 8),
        ("9959", "disponivel", None, 40),
    ]
    for ramal, status, numero, seg in eventos:
        db.add(ExtensionStatusEvent(
            ramal=ramal, status=status, status_raw=status.title(), numero=numero,
            uniqueid="X" if numero else None,
            received_at=BASE + timedelta(seconds=seg), event_at=None, call_started_at=None,
        ))
    db.commit()

    primeiro = calls_domain.rebuild_calls(db)
    db.commit()
    segundo = calls_domain.rebuild_calls(db)
    db.commit()

    assert primeiro["criadas"] == 1
    assert segundo["criadas"] == 0, "reprocessar não pode duplicar"
    assert db.query(ExtensionCall).count() == 1
