"""Verificação de registro SIP pós-aplicação (mapeamento de status + payload mínimo)."""

from __future__ import annotations

from typing import Any

from middleware_monitor.domain.extension_configurator import verify


class _FakeUscall:
    """Cliente USCall falso; conta quantas vezes a lista completa foi pedida."""

    def __init__(self, by_ramal: dict[str, dict[str, Any]]) -> None:
        self._by = by_ramal
        self.full_fetches = 0
        self.single_fetches = 0

    async def fetch_extensions(self) -> list[dict[str, Any]]:
        self.full_fetches += 1
        return list(self._by.values())

    async def fetch_extension(self, ramal: str) -> dict[str, Any] | None:
        self.single_fetches += 1
        return self._by.get(str(ramal))


async def test_batch_mapeia_status_e_faz_uma_consulta(monkeypatch) -> None:
    fake = _FakeUscall({
        "3660": {"ramal": "3660", "status": "disponivel"},
        "3661": {"ramal": "3661", "status": "indisponivel"},
        "3662": {"ramal": "3662", "status": "ocupado"},  # ocupado = registrado
    })
    monkeypatch.setattr(verify, "_build_client", lambda _f: fake)
    res = await verify.verify_registration_batch(
        None, ["3660", "3661", "3662", "9999"], attempts=1, delay_s=0,
    )
    assert res == {
        "3660": "registered",
        "3661": "unregistered",
        "3662": "registered",
        "9999": "unregistered",
    }
    # uma única consulta da lista cobriu todos os ramais (não 1 por ramal)
    assert fake.full_fetches == 1


async def test_batch_skip_quando_uscall_nao_configurado(monkeypatch) -> None:
    monkeypatch.setattr(verify, "_build_client", lambda _f: None)
    assert await verify.verify_registration_batch(None, ["3660"], attempts=1) is None


async def test_one_consulta_so_o_ramal(monkeypatch) -> None:
    fake = _FakeUscall({"3660": {"ramal": "3660", "status": "tocando"}})
    monkeypatch.setattr(verify, "_build_client", lambda _f: fake)
    assert await verify.verify_registration_one(None, "3660", attempts=1, delay_s=0) == "registered"
    assert await verify.verify_registration_one(None, "9999", attempts=1, delay_s=0) == "unregistered"
    # consultou por ramal (payload mínimo), nunca a lista completa
    assert fake.single_fetches == 2
    assert fake.full_fetches == 0


async def test_one_skip_sem_uscall(monkeypatch) -> None:
    monkeypatch.setattr(verify, "_build_client", lambda _f: None)
    assert await verify.verify_registration_one(None, "3660", attempts=1) == "skipped"
