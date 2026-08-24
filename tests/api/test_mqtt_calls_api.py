"""API de chamadas e o resumo diário.

O resumo não é "contar as linhas de /calls": ele deduplica por `uniqueid`, e é
essa diferença que estes testes fixam — foi ela que impediu um grupo de captura
de inflar o número de chamadas perdidas em quase três vezes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from middleware_monitor.core.models import ExtensionCall
from middleware_monitor.domain.auth.service import bootstrap_admin
from middleware_monitor.domain.mqtt import calls as calls_domain

# Ancorado no agora, não numa data fixa: os endpoints filtram por janela
# (`last=24h`), então uma constante do dia em que o teste foi escrito sai da
# janela sozinha com o passar do tempo e a suíte quebra sem nada ter mudado.
BASE = datetime.now(UTC).replace(tzinfo=None, microsecond=0) - timedelta(hours=2)
DIA = BASE.date().isoformat()


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


def test_chamada_em_curso_antiga_nao_duplica_as_ja_concluidas(db) -> None:
    """Regressao da duplicacao vista em producao (95 mil linhas para 3,7 mil).

    O cenario que quebrava: uma chamada fica **aberta** (telefone fora do gancho,
    ramal com registro oscilando). Toda passagem do job puxava o piso de leitura
    para tras ate ela e reprocessava tudo dali — e as pernas ja concluidas nesse
    intervalo viravam linha nova a cada rodada. Com o job de minuto em minuto,
    a mesma chamada foi gravada 32 vezes.

    Aqui o job roda tres vezes com uma chamada aberta no comeco da janela: as
    concluidas depois dela nao podem se multiplicar.
    """
    from middleware_monitor.core.models import ExtensionStatusEvent

    # Ancorado no agora: com BASE fixa, o teste passa a medir o relogio —
    # depois de ABANDONO a chamada aberta e encerrada antes da verificacao.
    agora = datetime.now(UTC).replace(tzinfo=None)

    def evento(ramal, status, numero, seg, uid=None):
        db.add(ExtensionStatusEvent(
            ramal=ramal, status=status, status_raw=status.title(), numero=numero,
            uniqueid=uid, received_at=agora + timedelta(seconds=seg),
            event_at=None, call_started_at=None,
        ))

    # Uma chamada que NUNCA fecha, aberta antes de todas as outras.
    evento("0318", "ocupado", None, 0)
    # E duas chamadas completas depois dela.
    evento("9959", "tocando", "1211", 10, "A")
    evento("9959", "ocupado", "1211", 14, "A")
    evento("9959", "disponivel", None, 40)
    evento("9950", "tocando", "1212", 50, "B")
    evento("9950", "disponivel", None, 58)
    db.commit()

    for _ in range(3):
        calls_domain.rebuild_calls(db)
        db.commit()

    concluidas = db.query(ExtensionCall).filter(ExtensionCall.outcome != "em_curso").all()
    assert len(concluidas) == 2, (
        f"as concluidas se multiplicaram: {[(c.ramal, c.started_at) for c in concluidas]}"
    )
    assert db.query(ExtensionCall).filter(ExtensionCall.outcome == "em_curso").count() == 1


def test_chamada_aberta_continua_na_mesma_linha(db) -> None:
    """A perna aberta tem de ser CONTINUADA, nao recriada.

    E o outro lado da mesma moeda: se o job so lesse eventos novos sem semear as
    pernas abertas, a chamada em curso perderia o inicio e viraria uma linha
    nova quando finalmente fechasse.
    """
    from middleware_monitor.core.models import ExtensionStatusEvent

    agora = datetime.now(UTC).replace(tzinfo=None)

    def evento(status, numero, seg):
        db.add(ExtensionStatusEvent(
            ramal="9959", status=status, status_raw=status.title(), numero=numero,
            uniqueid="A", received_at=agora + timedelta(seconds=seg),
            event_at=None, call_started_at=None,
        ))

    evento("tocando", "1211", 0)
    db.commit()
    calls_domain.rebuild_calls(db)
    db.commit()

    (aberta,) = db.query(ExtensionCall).all()
    assert aberta.outcome == "em_curso"
    id_original = aberta.id

    # A chamada e atendida e encerrada numa passagem posterior do job.
    evento("ocupado", "1211", 6)
    evento("disponivel", None, 30)
    db.commit()
    calls_domain.rebuild_calls(db)
    db.commit()

    (fechada,) = db.query(ExtensionCall).all()
    assert fechada.id == id_original, "tem de continuar a mesma linha"
    assert fechada.outcome == "atendida"
    assert fechada.started_at == agora, "o inicio nao pode se perder"
    assert fechada.talk_seconds == 24


def test_ramal_exato_nao_traz_chamada_de_outro_ramal(client, db) -> None:
    """A página de um telefone não pode mostrar chamada de outro.

    O filtro de ramal casa por trecho, que é o certo na tela de busca — mas ali
    `9959` traria também o `19959`. Numa página de device isso é erro, não
    conveniência: o operador leria como sendo daquele aparelho.
    """
    _authed(client, db)
    _call(db, ramal="9959")
    _call(db, ramal="19959")
    db.commit()

    assert client.get("/api/mqtt/calls?last=24h&ramal=9959").json()["total"] == 2
    exato = client.get("/api/mqtt/calls?last=24h&ramal=9959&ramal_exato=true").json()
    assert exato["total"] == 1
    assert exato["items"][0]["ramal"] == "9959"
