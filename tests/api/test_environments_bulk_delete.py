"""Exclusão de ambientes usada pela ação em massa da lista.

A UI apaga os selecionados em série pelo DELETE por ambiente (não há endpoint
de bulk); estes testes cobrem o contrato que ela depende.
"""

from __future__ import annotations

from middleware_monitor.domain.auth.service import bootstrap_admin


def _authed(client, db) -> str:
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post("/api/auth/login", json={"username": user.username, "password": plaintext})
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def _criar(client, csrf, nome: str) -> str:
    r = client.post(
        "/api/extension-configurator/environments",
        json={"nome": nome, "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code in (200, 201), r.json()
    return r.json()["id"]


def test_apagar_varios_ambientes_em_serie(client, db) -> None:
    csrf = _authed(client, db)
    ids = [_criar(client, csrf, f"Loja {i}") for i in range(3)]

    # a linha de um deles some junto (cascade) — a UI avisa disso no modal
    client.put(
        f"/api/extension-configurator/environments/{ids[0]}/lines",
        json={"linhas": [{"ip": "10.0.0.1", "numero_ramal": "3001"}]},
        headers={"X-CSRF-Token": csrf},
    )

    for env_id in ids:
        r = client.delete(
            f"/api/extension-configurator/environments/{env_id}",
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, r.json()

    restantes = client.get("/api/extension-configurator/environments").json()["environments"]
    assert [e["id"] for e in restantes if e["id"] in ids] == []


def test_apagar_ambiente_inexistente_devolve_404(client, db) -> None:
    csrf = _authed(client, db)
    r = client.delete(
        "/api/extension-configurator/environments/nao-existe",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404


def test_apagar_ambiente_exige_csrf(client, db) -> None:
    csrf = _authed(client, db)
    env_id = _criar(client, csrf, "Sem CSRF")
    r = client.delete(f"/api/extension-configurator/environments/{env_id}")
    assert r.status_code == 403
