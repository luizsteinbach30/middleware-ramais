"""Valor que o aparelho não recebe não pode derrubar a tela — vira `invalid` por linha.

Relato do dono (2026-09-08): no cliente, ambientes Intelbras TIP 125i "dão erro
ao salvar a planilha ou editar". Causa: `generate_config` roda dentro de
`compute_statuses` (no salvar E no carregar), e um `;` numa célula levantava
`TIP125iValorInvalido` DEPOIS do commit — 500 `internal_error` no salvar e em
TODO carregamento seguinte do ambiente. Uma célula tirava a planilha inteira do
ar, e a mensagem com a causa ficava só no log do servidor.
"""

from __future__ import annotations

from tests.api.test_extension_configurator import _authed


def _env_tip(client, csrf, nome: str = "Loja 150") -> str:
    r = client.post(
        "/api/extension-configurator/environments",
        json={"nome": nome, "modelo_telefone": "Intelbras TIP 125i"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    return r.json()["id"]


def test_celula_com_ponto_e_virgula_salva_e_marca_a_linha_como_invalida(client, db) -> None:
    csrf = _authed(client, db)
    env_id = _env_tip(client, csrf)
    r = client.put(
        f"/api/extension-configurator/environments/{env_id}/lines",
        json={"linhas": [
            {"ip": "10.150.51.101", "numero_ramal": "15001", "senha_sip": "w0rk;151234"},
            {"ip": "10.150.51.102", "numero_ramal": "15002", "senha_sip": "w0rk151234"},
        ]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    linhas = {ln["numero_ramal"]: ln for ln in r.json()["linhas"]}
    assert linhas["15001"]["status"] == "invalid"
    assert "senha SIP" in linhas["15001"]["erro_config"]
    assert ";" in linhas["15001"]["erro_config"]
    assert linhas["15002"]["status"] == "pending"
    assert linhas["15002"]["erro_config"] is None

    # A tela do ambiente continua carregando — antes era 500 em todo GET.
    r = client.get(f"/api/extension-configurator/environments/{env_id}")
    assert r.status_code == 200, r.text
    assert [ln["status"] for ln in r.json()["linhas"]] == ["invalid", "pending"]


def test_corrigir_a_celula_tira_a_linha_do_invalido(client, db) -> None:
    csrf = _authed(client, db)
    env_id = _env_tip(client, csrf)
    r = client.put(
        f"/api/extension-configurator/environments/{env_id}/lines",
        json={"linhas": [{"ip": "10.150.51.101", "numero_ramal": "15001", "senha_sip": "a;b"}]},
        headers={"X-CSRF-Token": csrf},
    )
    linha = r.json()["linhas"][0]
    assert linha["status"] == "invalid"
    r = client.put(
        f"/api/extension-configurator/environments/{env_id}/lines",
        json={"linhas": [{**linha, "senha_sip": "ab"}]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["linhas"][0]["status"] == "pending"
    assert r.json()["linhas"][0]["erro_config"] is None


def test_numero_abreviado_com_ponto_e_virgula_aponta_a_coluna(client, db) -> None:
    """A seção de teclas fica escondida para o TIP; o erro tem de citar a coluna."""
    csrf = _authed(client, db)
    env_id = _env_tip(client, csrf)
    r = client.put(
        f"/api/extension-configurator/environments/{env_id}/lines",
        json={"linhas": [{"ip": "10.150.51.101", "numero_ramal": "15001", "numero_abreviado": "9;0"}]},
        headers={"X-CSRF-Token": csrf},
    )
    linha = r.json()["linhas"][0]
    assert linha["status"] == "invalid"
    assert "numero_abreviado" in linha["erro_config"]


def test_hotline_ligada_sem_numero_e_recusada_na_config_padrao(client, db) -> None:
    csrf = _authed(client, db)
    env_id = _env_tip(client, csrf)
    r = client.put(
        f"/api/extension-configurator/environments/{env_id}",
        json={"config_padrao": {"hotline_enable": 1, "hotline_number": ""}},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422, r.text
    assert "hotline" in r.json()["detail"].lower()
    # Nada foi gravado: a hotline segue desligada e a planilha continua sã.
    cfg = client.get(f"/api/extension-configurator/environments/{env_id}").json()["config_padrao"]
    assert cfg["hotline_enable"] == 0


def test_hotline_ligada_com_numero_salva(client, db) -> None:
    csrf = _authed(client, db)
    env_id = _env_tip(client, csrf)
    r = client.put(
        f"/api/extension-configurator/environments/{env_id}",
        json={"config_padrao": {"hotline_enable": 1, "hotline_number": "9000", "hotline_time": 3}},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    cfg = client.get(f"/api/extension-configurator/environments/{env_id}").json()["config_padrao"]
    assert cfg["hotline_enable"] == 1
    assert cfg["hotline_number"] == "9000"


def test_config_padrao_com_ponto_e_virgula_no_ntp_e_recusada(client, db) -> None:
    csrf = _authed(client, db)
    env_id = _env_tip(client, csrf)
    r = client.put(
        f"/api/extension-configurator/environments/{env_id}",
        json={"config_padrao": {"ntp_mode": "proprio", "ntp_server": "a.ntp.br;"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422, r.text
    assert "NTP" in r.json()["detail"]


def test_aplicar_com_linha_invalida_registra_erro_sem_tocar_no_aparelho(client, db) -> None:
    from middleware_monitor.core.models import ExtensionApplyRunLine, ExtensionLine

    csrf = _authed(client, db)
    env_id = _env_tip(client, csrf)
    client.put(
        f"/api/extension-configurator/environments/{env_id}/lines",
        json={"linhas": [{"ip": "10.150.51.101", "numero_ramal": "15001", "senha_sip": "a;b"}]},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.post(
        f"/api/extension-configurator/environments/{env_id}/apply",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1

    db.expire_all()
    ln = db.query(ExtensionLine).filter_by(environment_id=env_id).one()
    assert ln.ultimo_status == "erro"
    assert "senha SIP" in (ln.ultimo_erro or "")
    rl = db.query(ExtensionApplyRunLine).filter_by(line_id=ln.id).one()
    assert rl.status_depois == "erro"
    assert "senha SIP" in (rl.erro or "")


def test_modelo_sem_essa_regra_segue_salvando(client, db) -> None:
    """A validação é do adapter, não da API: o HTEK aceita o mesmo payload."""
    csrf = _authed(client, db)
    r = client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Htek", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    env_id = r.json()["id"]
    r = client.put(
        f"/api/extension-configurator/environments/{env_id}/lines",
        json={"linhas": [{"ip": "10.0.0.1", "numero_ramal": "100", "senha_sip": "a;b"}]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["linhas"][0]["status"] == "pending"
