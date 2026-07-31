"""Endpoints do Configurador de Ramais: auth, CSRF, CRUD basico."""

from __future__ import annotations

from middleware_monitor.domain.auth.service import bootstrap_admin


def _authed(client, db) -> str:
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post(
        "/api/auth/login",
        json={"username": user.username, "password": plaintext},
    )
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def test_list_environments_requer_auth(client) -> None:
    r = client.get("/api/extension-configurator/environments")
    assert r.status_code == 401


def test_create_environment_requer_csrf(client, db) -> None:
    _authed(client, db)
    r = client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Unidade 03", "modelo_telefone": "HTEK UC902G"},
    )
    assert r.status_code == 403


def test_create_and_list_environment_fluxo_completo(client, db) -> None:
    csrf = _authed(client, db)
    # cria
    r = client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Unidade 03", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["id"] == "unidade-03"
    assert body["telefones"] == 0
    # lista
    r = client.get("/api/extension-configurator/environments")
    assert r.status_code == 200
    envs = r.json()["environments"]
    assert any(e["id"] == "unidade-03" for e in envs)


def test_environment_detail_traz_config_padrao_e_status(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "Intelbras V5501"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.get("/api/extension-configurator/environments/lab")
    assert r.status_code == 200
    body = r.json()
    assert body["modelo_telefone"] == "Intelbras V5501"
    assert body["config_padrao"]["keylock_enable"] == 2
    assert body["config_padrao"]["keylock_timeout"] == 30
    assert body["linhas"] == []


def test_save_lines_e_calcula_status(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.put(
        "/api/extension-configurator/environments/lab/lines",
        json={"linhas": [
            {"ip": "192.168.0.10", "numero_ramal": "3660", "senha_sip": "s1"},
            {"ip": "", "numero_ramal": "3661"},
        ]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert len(body["linhas"]) == 2
    # Linha com IP: status calculado (pending pois nunca foi aplicada)
    com_ip = next(ln for ln in body["linhas"] if ln["ip"])
    assert com_ip["status"] == "pending"
    assert len(com_ip["hash_atual"]) == 64


def test_searchable_contem_apenas_o_nome_do_ambiente(client, db) -> None:
    """Bug v2.6.0: `searchable` concatenava ramal/IP/MAC/user auth das linhas,
    então buscar um ramal trazia ambientes cujo NOME não batia."""
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Unidade 07", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    client.put(
        "/api/extension-configurator/environments/unidade-07/lines",
        json={"linhas": [
            {"ip": "192.168.9.77", "numero_ramal": "3660", "user_auth": "authuser77"},
        ]},
        headers={"X-CSRF-Token": csrf},
    )
    envs = client.get("/api/extension-configurator/environments").json()["environments"]
    env = next(e for e in envs if e["id"] == "unidade-07")
    assert env["searchable"] == "unidade 07"
    # dados internos da planilha NÃO entram na busca livre
    assert "3660" not in env["searchable"]
    assert "192.168.9.77" not in env["searchable"]
    assert "authuser77" not in env["searchable"]
    # nem o modelo — tem filtro dedicado
    assert "htek" not in env["searchable"]


def test_update_environment_atualiza_config_padrao(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.put(
        "/api/extension-configurator/environments/lab",
        json={"config_padrao": {"sip_server": "pbx.novo.com"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    detail = client.get("/api/extension-configurator/environments/lab").json()
    assert detail["config_padrao"]["sip_server"] == "pbx.novo.com"
    # defaults preservados
    assert detail["config_padrao"]["keylock_enable"] == 2


def test_duplicate_environment_requer_csrf(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.post("/api/extension-configurator/environments/lab/duplicate")
    assert r.status_code == 403


def test_duplicate_environment_copia_config_sem_linhas(client, db) -> None:
    csrf = _authed(client, db)
    # ambiente origem com config customizada + 2 ramais
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Matriz", "modelo_telefone": "Intelbras V5501"},
        headers={"X-CSRF-Token": csrf},
    )
    client.put(
        "/api/extension-configurator/environments/matriz",
        json={"config_padrao": {"sip_server": "pbx.matriz.com", "menu_password": "9090"}},
        headers={"X-CSRF-Token": csrf},
    )
    client.put(
        "/api/extension-configurator/environments/matriz/lines",
        json={"linhas": [
            {"ip": "192.168.0.10", "numero_ramal": "3660", "senha_sip": "s1"},
            {"ip": "192.168.0.11", "numero_ramal": "3661", "senha_sip": "s2"},
        ]},
        headers={"X-CSRF-Token": csrf},
    )

    # duplica sem nome → "Cópia de Matriz"
    r = client.post(
        "/api/extension-configurator/environments/matriz/duplicate",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["nome"] == "Cópia de Matriz"
    assert body["id"] == "copia-de-matriz"
    assert body["telefones"] == 0  # ramais NÃO são copiados

    detail = client.get(
        f"/api/extension-configurator/environments/{body['id']}",
    ).json()
    assert detail["modelo_telefone"] == "Intelbras V5501"
    assert detail["config_padrao"]["sip_server"] == "pbx.matriz.com"
    assert detail["config_padrao"]["menu_password"] == "9090"
    assert detail["config_padrao"]["keylock_enable"] == 2  # defaults preservados
    assert detail["linhas"] == []

    # origem permanece intacta (ainda com os 2 ramais)
    src = client.get("/api/extension-configurator/environments/matriz").json()
    assert len(src["linhas"]) == 2


def test_duplicate_environment_nome_customizado(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Matriz", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.post(
        "/api/extension-configurator/environments/matriz/duplicate",
        json={"nome": "Filial Centro"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["nome"] == "Filial Centro"
    assert r.json()["id"] == "filial-centro"


def test_duplicate_environment_inexistente(client, db) -> None:
    csrf = _authed(client, db)
    r = client.post(
        "/api/extension-configurator/environments/nao-existe/duplicate",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404


def test_delete_environment(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.delete(
        "/api/extension-configurator/environments/lab",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert client.get(
        "/api/extension-configurator/environments/lab",
    ).status_code == 404


def test_phone_models_exposto(client, db) -> None:
    _authed(client, db)
    r = client.get("/api/extension-configurator/phone-models")
    assert r.status_code == 200
    models = r.json()["models"]
    assert "HTEK UC902G" in models
    assert any("Intelbras" in m for m in models)


def test_web_pages_redirecionam_para_login_sem_auth(client) -> None:
    """Smoke das 4 paginas HTML novas — sem login devem ir para /login."""
    paths = [
        "/extension-configurator/environments",
        "/extension-configurator/runs",
        "/extension-configurator/environments/lab",
        "/extension-configurator/environments/lab/config",
    ]
    for p in paths:
        r = client.get(p, follow_redirects=False)
        assert r.status_code == 302, f"{p} -> {r.status_code}"
        assert r.headers["location"] == "/login"


def test_web_pages_renderizam_com_auth(client, db) -> None:
    csrf = _authed(client, db)
    # cria ambiente para a pagina de detail
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    for path in (
        "/extension-configurator/environments",
        "/extension-configurator/runs",
        "/extension-configurator/environments/lab",
        "/extension-configurator/environments/lab/config",
    ):
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        assert "html" in r.headers.get("content-type", "")


def test_ping_batch_requer_auth(client) -> None:
    r = client.post(
        "/api/extension-configurator/ping",
        json={"ips": ["192.168.0.10"]},
    )
    assert r.status_code == 401


def test_ping_batch_requer_csrf(client, db) -> None:
    _authed(client, db)
    r = client.post(
        "/api/extension-configurator/ping",
        json={"ips": ["192.168.0.10"]},
    )
    assert r.status_code == 403


class _FakePingProbe:
    async def ping(self, ip: str, timeout_ms: int) -> int | None:
        return 4 if ip == "192.168.0.10" else None


class _FakeArpProbe:
    async def lookup(self, ip: str) -> str | None:
        return "aa:bb:cc:dd:ee:ff" if ip == "192.168.0.10" else None


def _patch_probes(monkeypatch) -> None:
    # make_*_probe() apenas instancia o probe → a própria classe serve
    monkeypatch.setattr(
        "middleware_monitor.api.extension_configurator.make_ping_probe",
        _FakePingProbe,
    )
    monkeypatch.setattr(
        "middleware_monitor.api.extension_configurator.make_arp_probe",
        _FakeArpProbe,
    )


def test_ping_batch_dedupe_valida_e_reporta_status(client, db, monkeypatch) -> None:
    csrf = _authed(client, db)
    _patch_probes(monkeypatch)
    r = client.post(
        "/api/extension-configurator/ping",
        # duplicado + IP valido offline + invalido + vazio
        json={"ips": ["192.168.0.10", "192.168.0.10", "10.0.0.5", "nao-eh-ip", ""]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    # invalido/vazio descartados; dedupe aplicado
    assert set(body.keys()) == {"192.168.0.10", "10.0.0.5"}
    # host online resolve MAC via ARP; offline nao tenta ARP
    assert body["192.168.0.10"] == {"online": True, "latency_ms": 4, "mac": "aa:bb:cc:dd:ee:ff"}
    assert body["10.0.0.5"] == {"online": False, "latency_ms": None, "mac": None}


def test_ping_batch_persiste_mac_no_ambiente(client, db, monkeypatch) -> None:
    csrf = _authed(client, db)
    _patch_probes(monkeypatch)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    client.put(
        "/api/extension-configurator/environments/lab/lines",
        json={"linhas": [{"ip": "192.168.0.10", "numero_ramal": "3660"}]},
        headers={"X-CSRF-Token": csrf},
    )
    # sem MAC ainda
    detail = client.get("/api/extension-configurator/environments/lab").json()
    assert detail["linhas"][0]["ultimo_mac"] in (None, "")

    r = client.post(
        "/api/extension-configurator/ping",
        json={"ips": ["192.168.0.10"], "environment_id": "lab"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["192.168.0.10"]["mac"] == "aa:bb:cc:dd:ee:ff"
    # persistiu na linha do ambiente
    detail = client.get("/api/extension-configurator/environments/lab").json()
    assert detail["linhas"][0]["ultimo_mac"] == "aa:bb:cc:dd:ee:ff"


def test_preview_line_mostra_campos_e_mascara_senha(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.put(
        "/api/extension-configurator/environments/lab/lines",
        json={"linhas": [
            {"ip": "192.168.0.10", "numero_ramal": "3660", "senha_sip": "SenhaSecreta123"},
        ]},
        headers={"X-CSRF-Token": csrf},
    )
    line_id = r.json()["linhas"][0]["id"]

    pv = client.get(f"/api/extension-configurator/environments/lab/lines/{line_id}/preview")
    assert pv.status_code == 200, pv.json()
    body = pv.json()
    assert body["status"] == "pending"
    assert body["vai_mudar"] is True
    assert len(body["campos"]) > 0
    # a senha real NUNCA aparece no preview (nem no XML cru, nem nos campos)
    assert "SenhaSecreta123" not in body["xml"]
    valores = [c["valor"] for c in body["campos"]]
    assert all("SenhaSecreta123" != v for v in valores)
    # o placeholder mascarado aparece (campos são URL-decodificados p/ HTEK)
    assert "********" in valores


def test_preview_line_404_em_linha_de_outro_ambiente(client, db) -> None:
    csrf = _authed(client, db)
    for nome in ("Lab", "Outro"):
        client.post(
            "/api/extension-configurator/environments",
            json={"nome": nome, "modelo_telefone": "HTEK UC902G"},
            headers={"X-CSRF-Token": csrf},
        )
    r = client.put(
        "/api/extension-configurator/environments/lab/lines",
        json={"linhas": [{"ip": "192.168.0.10", "numero_ramal": "3660"}]},
        headers={"X-CSRF-Token": csrf},
    )
    line_id = r.json()["linhas"][0]["id"]
    # a linha existe, mas não pertence ao ambiente 'outro'
    pv = client.get(f"/api/extension-configurator/environments/outro/lines/{line_id}/preview")
    assert pv.status_code == 404


def test_export_requer_csrf(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.post(
        "/api/extension-configurator/environments/lab/export",
        json={"passphrase": "segredo"},
    )
    assert r.status_code == 403


def test_export_import_roundtrip_preserva_senha(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Matriz", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    client.put(
        "/api/extension-configurator/environments/matriz/lines",
        json={"linhas": [
            {"ip": "192.168.0.10", "numero_ramal": "3660", "senha_sip": "ZapZap#42"},
        ]},
        headers={"X-CSRF-Token": csrf},
    )
    # exporta cifrado
    exp = client.post(
        "/api/extension-configurator/environments/matriz/export",
        json={"passphrase": "minha-frase"},
        headers={"X-CSRF-Token": csrf},
    )
    assert exp.status_code == 200, exp.text
    blob = exp.text
    # o arquivo cifrado NÃO contém a senha em claro
    assert "ZapZap#42" not in blob

    # passphrase errada → 400
    bad = client.post(
        "/api/extension-configurator/environments/import",
        json={"passphrase": "errada", "blob": blob},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 400

    # import correto → novo ambiente (slug novo, não sobrescreve)
    imp = client.post(
        "/api/extension-configurator/environments/import",
        json={"passphrase": "minha-frase", "blob": blob, "nome": "Matriz"},
        headers={"X-CSRF-Token": csrf},
    )
    assert imp.status_code == 200, imp.json()
    new_id = imp.json()["id"]
    assert new_id != "matriz"  # colisão de nome gera slug novo
    assert imp.json()["linhas"] == 1

    # a senha chegou correta no destino (device receberia o valor certo)
    detail = client.get(f"/api/extension-configurator/environments/{new_id}").json()
    assert detail["linhas"][0]["senha_sip"] == "ZapZap#42"
    assert detail["modelo_telefone"] == "HTEK UC902G"


def test_apply_environment_sem_linhas_devolve_total_zero(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.post(
        "/api/extension-configurator/environments/lab/apply",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["total"] == 0
    assert len(body["run_id"]) == 12
