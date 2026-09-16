"""O `VACUUM` do boot depois da migration que recifrou segredos (v2.14.0).

Cifrar a coluna não apaga o que já estava gravado: o SQLite marca a página
antiga como livre e o texto continua legível com um `grep` no arquivo. A
migration 0013 não consegue compactar sozinha — `VACUUM` não roda dentro da
transação em que o alembic a executa —, então ela deixa a marca
`db.compactar_pendente` e quem compacta é o próximo boot.

**O que estes testes cobrem:** que o boot executa o `VACUUM` sem estourar e
limpa a marca, e que um boot sem marca não reescreve o banco. A falha que
motivou isto foi um `COMMIT` escrito à mão que levantava
``cannot commit - no transaction is active``: o `except` do `app.py` engolia a
exceção — de propósito, para um banco que não compacta não derrubar o serviço —
e a marca ficava de pé para sempre, em silêncio.

**O que eles NÃO cobrem:** que o texto antigo sumiu de fato dos bytes do
arquivo. Com o banco em WAL e a sessão do teste segurando a conexão, medir isso
aqui mede o momento do checkpoint, não o meu código. Essa parte foi provada ao
vivo, sobre um banco real levado da 0012 à `head`: o resíduo estava lá logo
depois da migration e sumiu depois do primeiro boot.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session as DBSession

CHAVE = "db.compactar_pendente"


def _marcar(db: DBSession) -> None:
    db.execute(
        text(
            "INSERT INTO app_config (key, value, is_secret, updated_at, updated_by) "
            "VALUES (:k, '1', 0, :agora, NULL) "
            "ON CONFLICT(key) DO UPDATE SET value = '1'"
        ),
        {"k": CHAVE, "agora": datetime.now(UTC).replace(tzinfo=None)},
    )
    db.commit()


def _pendente(db: DBSession) -> str | None:
    return db.execute(
        text("SELECT value FROM app_config WHERE key = :k"), {"k": CHAVE}
    ).scalar_one_or_none()


def test_boot_compacta_e_apaga_a_marca(db: DBSession) -> None:
    from middleware_monitor.app import create_app

    _marcar(db)
    assert _pendente(db) == "1"

    with TestClient(create_app()):
        pass

    assert _pendente(db) is None, (
        "a marca continua de pé: o VACUUM do boot não rodou, e o `except` do "
        "app.py engoliu o motivo"
    )


def test_boot_sem_marca_nao_compacta(db: DBSession) -> None:
    """Compactar a cada boot seria reescrever o banco inteiro toda vez — com o
    ledger do MQTT dentro, isso é caro e não tem motivo."""
    from middleware_monitor.app import create_app

    assert _pendente(db) is None

    with TestClient(create_app()):
        pass

    assert _pendente(db) is None
