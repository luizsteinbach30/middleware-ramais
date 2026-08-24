"""Ocupação do ledger: o que tem cache, o que não tem, e por quê.

O desenho aqui é assimétrico de propósito (ver `domain/mqtt/storage.py`): a
contagem é exata a cada chamada porque custa 0 ms num índice de cobertura e é o
número que o operador acompanha enquanto configura o broker; a soma de bytes é
varredura da tabela inteira e só recalcula por TTL. Estes testes prendem essa
assimetria — trocá-la por "tudo com cache" faria a tela de configuração parecer
travada, e por "tudo ao vivo" traria de volta a varredura a cada 5 s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from middleware_monitor.domain.mqtt import storage

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as DBSession


@pytest.fixture(autouse=True)
def _limpa_cache():
    storage.invalidate()
    yield
    storage.invalidate()


def test_soma_de_bytes_nao_e_refeita_dentro_do_ttl(
    db: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas = {"soma": 0, "contagem": 0}

    def _soma(_db):
        chamadas["soma"] += 1
        return 4096

    def _contagem(_db):
        chamadas["contagem"] += 1
        return 7

    monkeypatch.setattr(storage.repo, "payload_bytes_total", _soma)
    monkeypatch.setattr(storage.repo, "count_messages", _contagem)

    primeiro = storage.tamanho(db)
    segundo = storage.tamanho(db)

    assert primeiro.payload_bytes == segundo.payload_bytes == 4096
    assert chamadas["soma"] == 1, "a varredura da tabela não pode repetir no TTL"
    # A contagem, ao contrário, é refeita: é barata e é a que o operador observa.
    assert chamadas["contagem"] == 2


def test_contagem_acompanha_o_banco_mesmo_com_bytes_em_cache(
    db: DBSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    quantidade = {"n": 1}
    monkeypatch.setattr(storage.repo, "payload_bytes_total", lambda _db: 4096)
    monkeypatch.setattr(storage.repo, "count_messages", lambda _db: quantidade["n"])

    assert storage.tamanho(db).mensagens == 1
    quantidade["n"] = 42
    assert storage.tamanho(db).mensagens == 42


def test_invalidate_forca_recalculo(db: DBSession, monkeypatch: pytest.MonkeyPatch) -> None:
    valores = iter([4096, 8192])
    monkeypatch.setattr(storage.repo, "payload_bytes_total", lambda _db: next(valores))
    monkeypatch.setattr(storage.repo, "count_messages", lambda _db: 1)

    assert storage.tamanho(db).payload_bytes == 4096
    storage.invalidate()
    assert storage.tamanho(db).payload_bytes == 8192


def test_ttl_vencido_recalcula(db: DBSession, monkeypatch: pytest.MonkeyPatch) -> None:
    valores = iter([4096, 8192])
    monkeypatch.setattr(storage.repo, "payload_bytes_total", lambda _db: next(valores))
    monkeypatch.setattr(storage.repo, "count_messages", lambda _db: 1)

    relogio = {"t": 1000.0}
    monkeypatch.setattr(storage.time, "monotonic", lambda: relogio["t"])

    assert storage.tamanho(db).payload_bytes == 4096
    relogio["t"] += storage.TTL_SECONDS + 1
    assert storage.tamanho(db).payload_bytes == 8192
