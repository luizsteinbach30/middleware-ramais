"""Telas locais de equipamentos e o menu em árvore (``docs/AGENTE-NOC.md`` item 18).

O que se prova aqui:

- ``/api/menu``: Telefonia segue o módulo ``telefonia``; Equipamentos aparece com
  ``disponibilidade``, com equipamento cadastrado ou com frente visível; cada frente
  aparece com equipamento dela ou módulo dela aceito — a divisão é a do NOC;
- ``/api/equipamentos`` (lista, filtro de frente) e ``/api/equipamentos/<id>``
  (verificações com ``pausada`` e o módulo, leitura do gravador, eventos) — e
  **nenhum segredo nem impressão** na resposta;
- "Verificar agora": só admin, com CSRF, a mesma leitura avulsa do verbo
  ``verificar_equipamento`` (estado, amostras e eventos não mudam);
- as páginas renderizam com o menu certo, e Telefonia desligada só some do menu;
- a seção do manifesto em ``/system/noc`` não diz mais que não há ações.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from middleware_monitor.core.models import (
    AmostraDeVerificacao,
    CredencialDeEquipamento,
    Equipamento,
    EventoDeEquipamento,
    LeituraDeCftv,
    VerificacaoDeEquipamento,
)
from middleware_monitor.domain.auth.service import bootstrap_admin
from middleware_monitor.domain.equipamentos import frentes, lista, modulos, sondas
from middleware_monitor.domain.noc import executor
from middleware_monitor.integrations.network.base import ResultadoDaSonda

SEGREDO = "senha-do-gravador-NAO-PODE-SAIR"


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _login(client, db, *, admin: bool = True) -> str:
    nome = "admin" if admin else "leitor"
    user, senha = bootstrap_admin(db, username=nome, password="SenhaForte123")
    user.must_change_password = False
    if not admin:
        user.role = "viewer"
    db.commit()
    r = client.post("/api/auth/login", json={"username": nome, "password": senha})
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def _equipamento(
    db, eid: str, tipo: str, nome: str, *, verificacoes=(), estado: str = "no_ar"
) -> Equipamento:
    agora = _agora()
    eq = Equipamento(
        id=eid,
        ref=f"noc-{eid}",
        tipo=tipo,
        nome=nome,
        host="10.22.8.200",
        tentativas_para_cair=3,
        estado=estado,
        estado_desde=agora - timedelta(hours=1),
        criado_em=agora,
        atualizado_em=agora,
    )
    for i, (vid, vtipo, parametros, vestado) in enumerate(verificacoes):
        eq.verificacoes.append(
            VerificacaoDeEquipamento(
                id=vid,
                ref=f"noc-{vid}",
                posicao=i,
                tipo=vtipo,
                parametros=json.dumps(parametros),
                intervalo_s=60,
                estado=vestado,
                estado_desde=agora,
                falhas_seguidas=0,
                ultimo_valor=12 if vestado == "ok" else None,
                ultima_em=agora if vestado != "pausada" else None,
                ultimo_erro="sem resposta" if vestado == "falha" else None,
            )
        )
    db.add(eq)
    db.flush()
    return eq


@pytest.fixture
def cadastro(db):
    """Um gravador com ping, http e cftv (intelbras, pausada: módulo desligado por
    padrão), credencial cifrada, leitura de CFTV e um evento; um switch; um servidor."""
    grav = _equipamento(
        db,
        "eq_gravador0000001",
        "gravador",
        "NVR Frente de caixa",
        verificacoes=[
            ("vf_ping00000001", "icmp", {}, "ok"),
            ("vf_http00000001", "http", {"esquema": "http", "porta": 80, "caminho": "/"}, "falha"),
            (
                "vf_cftv00000001",
                "cftv",
                {"adaptador": "intelbras", "esquema": "http", "porta": 80},
                "pausada",
            ),
        ],
        estado="degradado",
    )
    box = lista._box()
    assert box is not None
    grav.credenciais.append(
        CredencialDeEquipamento(
            tipo="HTTP",
            usuario="monitor",
            segredo=box.encrypt_field(SEGREDO),
            impressao=lista.impressao(SEGREDO),
        )
    )
    db.add(
        LeituraDeCftv(
            equipamento_id=grav.id,
            verificacao_id="vf_cftv00000001",
            leitura=json.dumps(
                {
                    "lidoEm": "2026-09-30T12:00:00",
                    "canais": [{"numero": 1, "nome": "CAM 1", "video": "ok", "gravando": True}],
                    "discos": [{"numero": 1, "estado": "ok", "totalGb": 1000.0, "livreGb": 250.0}],
                    "horaDoGravador": "2026-09-30T12:00:03",
                    "desvioS": 3,
                    "diasGuardados": 21,
                }
            ),
            lido_em=_agora(),
        )
    )
    db.add(
        EventoDeEquipamento(
            equipamento_id=grav.id,
            verificacao_id="vf_http00000001",
            tipo="equipamento.degradou",
            de="no_ar",
            para="degradado",
            em=_agora(),
            detalhe="http: sem sucesso em 3 tentativas seguidas — sem resposta",
        )
    )
    _equipamento(
        db,
        "eq_switch00000001",
        "switch",
        "Switch do rack",
        verificacoes=[("vf_ping00000002", "icmp", {}, "ok")],
    )
    _equipamento(db, "eq_servid00000001", "hipervisor", "Hyper-V", verificacoes=[])
    db.commit()
    return grav


# --- A divisão em frentes e o menu ---------------------------------------------------------------


def test_todo_tipo_cai_numa_frente_e_a_divisao_e_a_do_noc() -> None:
    esperado = {
        "gravador": "cftv",
        "camera": "cftv",
        "switch": "rede",
        "roteador": "rede",
        "firewall": "rede",
        "ponto_de_acesso": "rede",
        "link": "rede",
        "nobreak": "rede",
        "servidor": "servidores",
        "hipervisor": "servidores",
        "controle_de_acesso": "outros",
        "impressora": "outros",
        "outro": "outros",
    }
    assert set(esperado) == set(lista.TIPOS)
    assert {t: frentes.frente_do_tipo(t) for t in lista.TIPOS} == esperado


def test_menu_padrao_mostra_telefonia_e_equipamentos_sem_frente(db) -> None:
    m = frentes.menu(db)
    assert m["telefonia"] is True
    assert m["equipamentos"] is True  # disponibilidade é aceito por padrão
    assert m["frentes"] == {"cftv": False, "rede": False, "servidores": False, "outros": False}
    assert m["contagem"]["total"] == 0


def test_frente_aparece_com_modulo_dela_ou_com_equipamento_dela(db) -> None:
    modulos.definir(db, {"cftv.hikvision": True}, por="local:admin")
    _equipamento(db, "eq_impress0000001", "impressora", "Impressora")
    db.commit()
    m = frentes.menu(db)
    assert m["frentes"] == {"cftv": True, "rede": False, "servidores": False, "outros": True}
    assert m["contagem"]["outros"] == 1 and m["contagem"]["total"] == 1


def test_equipamentos_some_so_sem_disponibilidade_sem_equipamento_e_sem_frente(db) -> None:
    modulos.definir(db, {"disponibilidade": False, "telefonia": False}, por="local:admin")
    db.commit()
    m = frentes.menu(db)
    assert (m["telefonia"], m["equipamentos"]) == (False, False)
    # Um equipamento cadastrado basta para o ramo voltar, mesmo sem disponibilidade.
    _equipamento(db, "eq_switch00000009", "switch", "Switch")
    db.commit()
    m = frentes.menu(db)
    assert m["equipamentos"] is True and m["frentes"]["rede"] is True


def test_api_do_menu_pede_login(client, db) -> None:
    assert client.get("/api/menu").status_code == 401
    _login(client, db, admin=False)
    r = client.get("/api/menu")
    assert r.status_code == 200
    assert r.json()["modulos_aceitos"] == ["disponibilidade", "telefonia"]


# --- Lista e detalhe -------------------------------------------------------------------------


def test_lista_com_filtro_de_frente(client, db, cadastro) -> None:
    _login(client, db, admin=False)
    r = client.get("/api/equipamentos")
    assert r.status_code == 200
    corpo = r.json()
    assert [e["nome"] for e in corpo["itens"]] == ["Hyper-V", "NVR Frente de caixa", "Switch do rack"]
    assert corpo["contagem"] == {"cftv": 1, "rede": 1, "servidores": 1, "outros": 0, "total": 3}
    nvr = corpo["itens"][1]
    assert nvr["estado"] == "degradado" and nvr["frente"] == "cftv"
    assert [v["estado"] for v in nvr["verificacoes"]] == ["ok", "falha", "pausada"]
    assert nvr["ultimaLeitura"].endswith("Z") and nvr["estadoDesde"].endswith("Z")
    assert corpo["noc"]["enrolado"] is False

    so_cftv = client.get("/api/equipamentos?frente=cftv").json()["itens"]
    assert [e["id"] for e in so_cftv] == [cadastro.id]
    assert client.get("/api/equipamentos?frente=rede").json()["itens"][0]["tipo"] == "switch"
    assert client.get("/api/equipamentos?frente=telefonia").status_code == 400


def test_lista_do_cftv_diz_o_gravador_e_o_canal_da_camera(client, db, cadastro) -> None:
    """A tela do CFTV agrupa as câmeras sob o gravador, na ordem do canal (2.17.1:
    "a tela para listar as câmeras tá muito esquisita" — vinha tudo em ordem
    alfabética, gravador e câmera misturados)."""
    cam = _equipamento(db, "eq_camera00000001", "camera", "Caixa 01")
    cam.pai_id, cam.canal = cadastro.id, 7
    db.commit()
    _login(client, db, admin=False)
    itens = {e["id"]: e for e in client.get("/api/equipamentos?frente=cftv").json()["itens"]}
    assert (itens[cam.id]["paiId"], itens[cam.id]["canal"]) == (cadastro.id, 7)
    assert (itens[cadastro.id]["paiId"], itens[cadastro.id]["canal"]) == (None, None)


def test_lista_e_detalhe_pedem_login(client, db, cadastro) -> None:
    assert client.get("/api/equipamentos").status_code == 401
    assert client.get(f"/api/equipamentos/{cadastro.id}").status_code == 401


def test_detalhe_traz_verificacoes_leitura_credencial_sem_segredo_e_eventos(client, db, cadastro) -> None:
    _login(client, db, admin=False)
    r = client.get(f"/api/equipamentos/{cadastro.id}")
    assert r.status_code == 200
    e = r.json()
    assert e["frente"] == "cftv" and e["pode_verificar"] is False
    por_tipo = {v["tipo"]: v for v in e["verificacoes"]}
    assert por_tipo["icmp"]["pausada"] is False and por_tipo["icmp"]["ultimoValor"] == 12
    assert por_tipo["http"]["ultimoErro"] == "sem resposta"
    cftv = por_tipo["cftv"]
    assert (cftv["pausada"], cftv["modulo"], cftv["moduloAceito"]) == (True, "cftv.intelbras", False)
    assert cftv["moduloRotulo"] == "CFTV Intelbras / Dahua"
    assert e["cftv"]["discos"][0]["livreGb"] == 250.0 and e["cftv"]["diasGuardados"] == 21
    assert e["credenciais"] == [{"tipo": "HTTP", "usuario": "monitor", "definida": True}]
    assert [ev["tipo"] for ev in e["eventos"]] == ["equipamento.degradou"]
    # Nem o segredo, nem a cifra dele, nem a impressão.
    assert SEGREDO not in r.text
    assert lista.impressao(SEGREDO) not in r.text
    assert "enc:v1" not in r.text


def test_detalhe_de_equipamento_que_nao_existe_e_404(client, db) -> None:
    _login(client, db)
    assert client.get("/api/equipamentos/eq_naoexiste000001").status_code == 404
    assert client.get("/api/equipamentos/" + "x" * 40).status_code == 404


# --- Verificar agora -------------------------------------------------------------------------


@pytest.fixture
def sonda(monkeypatch) -> list[tuple[str, str]]:
    chamadas: list[tuple[str, str]] = []

    async def falsa(tipo, host, parametros, **_k):
        chamadas.append((tipo, host))
        return ResultadoDaSonda(
            ok=tipo == "icmp",
            valor=7 if tipo == "icmp" else None,
            erro=None if tipo == "icmp" else "HTTP 500",
        )

    monkeypatch.setattr(sondas, "executar", falsa)
    return chamadas


def test_verificar_agora_e_a_leitura_avulsa_do_verbo(client, db, cadastro, sonda) -> None:
    csrf = _login(client, db)
    antes = {v.id: (v.estado, v.ultima_em) for v in cadastro.verificacoes}
    r = client.post(f"/api/equipamentos/{cadastro.id}/verificar", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    corpo = r.json()
    por_tipo = {v["tipo"]: v for v in corpo["verificacoes"]}
    assert por_tipo["icmp"]["ok"] is True and por_tipo["icmp"]["valor"] == 7
    assert por_tipo["http"]["erro"] == "HTTP 500"
    # A pausada não roda: nem chega à sonda.
    assert por_tipo["cftv"]["erro"].startswith("pausada: módulo cftv.intelbras")
    assert sorted(sonda) == [("http", "10.22.8.200"), ("icmp", "10.22.8.200")]
    assert SEGREDO not in r.text
    # Só leitura: estado, amostra e evento ficam como estavam.
    db.expire_all()
    depois = {v.id: (v.estado, v.ultima_em) for v in db.get(Equipamento, cadastro.id).verificacoes}
    assert depois == antes
    assert db.query(AmostraDeVerificacao).count() == 0
    assert db.query(EventoDeEquipamento).count() == 1
    # A mesma função do verbo do NOC.
    assert executor.ACOES["verificar_equipamento"].raio == executor.LEITURA


def test_verificar_agora_so_admin_e_com_csrf(client, db, cadastro, sonda) -> None:
    csrf = _login(client, db, admin=False)
    r = client.post(f"/api/equipamentos/{cadastro.id}/verificar", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403
    assert sonda == []


def test_verificar_agora_sem_csrf_e_recusado(client, db, cadastro, sonda) -> None:
    _login(client, db)
    assert client.post(f"/api/equipamentos/{cadastro.id}/verificar").status_code == 403
    assert sonda == []


def test_verificar_agora_equipamento_desconhecido_e_404(client, db, sonda) -> None:
    csrf = _login(client, db)
    r = client.post("/api/equipamentos/eq_naoexiste000001/verificar", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 404


# --- Páginas e menu renderizado --------------------------------------------------------------


def test_paginas_novas_renderizam_com_o_menu_em_arvore(client, db, cadastro) -> None:
    _login(client, db)
    r = client.get("/equipamentos?frente=cftv")
    assert r.status_code == 200
    assert 'id="mm-menu"' in r.text and 'data-ramo="telefonia"' in r.text
    # O item da frente atual vem marcado.
    assert re.search(r'href="/equipamentos\?frente=cftv" data-frente="cftv"\s+aria-current="page"', r.text)

    r = client.get(f"/equipamentos/{cadastro.id}")
    assert r.status_code == 200
    assert f'data-equipamento="{cadastro.id}"' in r.text
    assert re.search(r'data-frente="cftv"\s+aria-current="page"', r.text)

    r = client.get("/system/modulos")
    assert r.status_code == 200 and 'id="mod-lista"' in r.text


def test_menu_esconde_frente_sem_equipamento_nem_modulo(client, db) -> None:
    _login(client, db)
    r = client.get("/")
    assert 'data-frente="rede" hidden' in r.text
    assert 'data-frente="cftv" hidden' in r.text


def test_telefonia_desligada_so_some_do_menu(client, db) -> None:
    _login(client, db)
    assert 'data-visivel="telefonia" hidden' not in client.get("/").text
    modulos.definir(db, {"telefonia": False}, por="local:admin")
    db.commit()
    r = client.get("/")
    assert 'data-visivel="telefonia" hidden' in r.text
    # As páginas da telefonia continuam abrindo pela URL.
    for url in ("/devices", "/collections", "/mqtt-painel", "/extension-configurator/environments"):
        assert client.get(url).status_code == 200, url
    assert client.get("/api/menu").json()["telefonia"] is False


def test_ramo_da_pagina_atual_vem_aberto(client, db) -> None:
    _login(client, db)
    r = client.get("/mqtt-chamadas")
    assert 'class="mm-ramo mm-sub aberto" data-ramo="mqtt"' in r.text
    assert 'class="mm-ramo aberto" data-ramo="telefonia"' in r.text
    # Ambientes passa a acender no menu (antes nunca acendia na própria lista).
    r = client.get("/extension-configurator/environments")
    assert 'class="mm-ramo mm-sub aberto" data-ramo="configurador"' in r.text


# --- /system/noc: o manifesto não mente sobre as ações ---------------------------------------


def test_manifesto_na_tela_traz_as_acoes_com_raio(client, db) -> None:
    _login(client, db)
    r = client.get("/api/noc/manifesto")
    corpo = r.json()
    assert corpo["acoes"] == sorted(executor.ACOES)
    assert corpo["raio_das_acoes"]["verificar_equipamento"] == executor.LEITURA
    assert corpo["raio_das_acoes"]["definir_equipamentos"] == executor.ESCRITA
    # O campo é só da tela: o hash do manifesto não o inclui.
    from middleware_monitor.domain.noc import manifesto

    assert "raio_das_acoes" not in manifesto.montar(db)
    pagina = client.get("/system/noc").text
    assert "ainda não executa nenhuma ação" not in pagina
    assert 'id="noc-acoes"' in pagina
