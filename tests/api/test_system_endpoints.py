def test_healthz_no_auth(client) -> None:
    r = client.get("/api/system/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readyz_returns_status(client) -> None:
    r = client.get("/api/system/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"ok", "degraded"}
