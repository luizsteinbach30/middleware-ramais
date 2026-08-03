"""Configuração da verificação automática de update (v2.7.0).

Até a v2.6.0 a tela tinha canal e toggle decorativos: nada era persistido e o
`auto_update` exposto era um literal `True`. Estes testes travam o contrato
novo — inclusive a regra de produto de que o agendamento **nunca instala**.
"""

from __future__ import annotations

from middleware_monitor.domain.auth.service import bootstrap_admin

ENDPOINT = "/api/system/update-settings"


def _authed(client, db) -> str:
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post("/api/auth/login", json={"username": user.username, "password": plaintext})
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def test_defaults_verificacao_diaria_e_nunca_instala(client, db) -> None:
    _authed(client, db)
    cfg = client.get(ENDPOINT).json()
    assert cfg["auto_check"] is True
    assert cfg["channel"] == "stable"
    assert cfg["check_hour"] == 0 and cfg["check_minute"] == 0
    assert len(cfg["check_days"]) == 7
    # contrato explícito com o operador: o agendamento só avisa
    assert cfg["installs_automatically"] is False


def test_salvar_persiste_e_reflete_no_version(client, db) -> None:
    csrf = _authed(client, db)
    r = client.put(
        ENDPOINT,
        json={
            "auto_check": True,
            "channel": "beta",
            "check_hour": 3,
            "check_minute": 30,
            "check_days": ["mon", "wed", "fri"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    cfg = r.json()
    assert cfg["channel"] == "beta"
    assert cfg["check_hour"] == 3 and cfg["check_minute"] == 30
    assert cfg["check_days"] == ["mon", "wed", "fri"]

    # sobrevive a uma nova leitura (persistido em app_config)
    assert client.get(ENDPOINT).json()["check_days"] == ["mon", "wed", "fri"]
    # e a tela de versão passa a mostrar o canal configurado, não o do .env
    assert client.get("/api/system/version").json()["channel"] == "beta"


def test_desligar_verificacao_reflete_em_version(client, db) -> None:
    csrf = _authed(client, db)
    client.put(ENDPOINT, json={"auto_check": False}, headers={"X-CSRF-Token": csrf})
    assert client.get(ENDPOINT).json()["auto_check"] is False
    # `auto_update` deixou de ser um literal True no código
    assert client.get("/api/system/version").json()["auto_update"] is False


def test_update_parcial_nao_apaga_o_resto(client, db) -> None:
    csrf = _authed(client, db)
    client.put(
        ENDPOINT,
        json={"channel": "beta", "check_hour": 5, "check_days": ["sat"]},
        headers={"X-CSRF-Token": csrf},
    )
    # muda só o minuto
    cfg = client.put(
        ENDPOINT, json={"check_minute": 45}, headers={"X-CSRF-Token": csrf},
    ).json()
    assert cfg["check_minute"] == 45
    assert cfg["check_hour"] == 5
    assert cfg["channel"] == "beta"
    assert cfg["check_days"] == ["sat"]


def test_canal_invalido_devolve_400(client, db) -> None:
    csrf = _authed(client, db)
    r = client.put(ENDPOINT, json={"channel": "nightly"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_hora_fora_do_intervalo_devolve_422(client, db) -> None:
    csrf = _authed(client, db)
    r = client.put(ENDPOINT, json={"check_hour": 99}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 422


def test_dias_invalidos_caem_na_semana_inteira(client, db) -> None:
    """Nenhum dia válido = nunca rodar, indistinguível de desligado — evitamos."""
    csrf = _authed(client, db)
    cfg = client.put(
        ENDPOINT, json={"check_days": ["xxx", "yyy"]}, headers={"X-CSRF-Token": csrf},
    ).json()
    assert len(cfg["check_days"]) == 7


def test_escrita_exige_csrf_e_admin(client, db) -> None:
    _authed(client, db)
    assert client.put(ENDPOINT, json={"auto_check": False}).status_code == 403
