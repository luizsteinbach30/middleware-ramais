"""serviços do ambiente e equipamento por ambiente — NOC 1.4.0 (AGENTE-NOC §13.11)

O ambiente deixa de ser só "um modelo de telefone": ganha a lista de serviços
ligados (``telefone``, ``dvr``, ``camera``) e o equipamento passa a dizer de qual
ambiente é.

- ``extension_environments.servicos`` — JSON (Text). Os ambientes que já existem
  nascem com ``["telefone"]``: nada muda para quem só configura ramal.
- ``equipamentos.ambiente_id`` — o ambiente do equipamento, ou nulo.
- ``equipamentos.canais`` (gravador: quantos canais tem) e ``equipamentos.canal``
  (câmera: em que canal do gravador-pai ela entra). Cadastro, não leitura: a
  leitura de CFTV continua dizendo os canais que o gravador respondeu.

Sem chave estrangeira em ``ambiente_id``, de propósito (como o ``pai_id``): no
SQLite uma FK só entra recriando a tabela, e recriar ``equipamentos`` com
``foreign_keys=ON`` apaga em cascata verificações e credenciais. Apagar o
ambiente zera o campo na aplicação (``repository.delete_environment``) e o
``definir_equipamentos`` recusa ambiente que não existe.

Repetível, como a 0015/0016.

Revision ID: 0017_ambiente_servicos
Revises: 0016_cftv
Create Date: 2026-10-09 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0017_ambiente_servicos"
down_revision: str | None = "0016_cftv"
branch_labels: str | None = None
depends_on: str | None = None


def _colunas(tabela: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(tabela)}


def _tem_indice(tabela: str, indice: str) -> bool:
    return any(i["name"] == indice for i in sa.inspect(op.get_bind()).get_indexes(tabela))


def upgrade() -> None:
    if "servicos" not in _colunas("extension_environments"):
        op.execute(
            "ALTER TABLE extension_environments ADD COLUMN servicos TEXT NOT NULL DEFAULT '[\"telefone\"]'"
        )
    colunas = _colunas("equipamentos")
    if "ambiente_id" not in colunas:
        op.execute("ALTER TABLE equipamentos ADD COLUMN ambiente_id VARCHAR(64)")
    for coluna in ("canais", "canal"):
        if coluna not in colunas:
            op.execute(f"ALTER TABLE equipamentos ADD COLUMN {coluna} INTEGER")
    if not _tem_indice("equipamentos", "ix_equipamentos_ambiente_id"):
        op.create_index("ix_equipamentos_ambiente_id", "equipamentos", ["ambiente_id"])


def downgrade() -> None:
    if _tem_indice("equipamentos", "ix_equipamentos_ambiente_id"):
        op.drop_index("ix_equipamentos_ambiente_id", table_name="equipamentos")
    # DROP COLUMN direto (SQLite >= 3.35): nada de recriar a tabela, pelo
    # mesmo motivo de não haver FK.
    colunas = _colunas("equipamentos")
    for coluna in ("canal", "canais", "ambiente_id"):
        if coluna in colunas:
            op.execute(f"ALTER TABLE equipamentos DROP COLUMN {coluna}")
    if "servicos" in _colunas("extension_environments"):
        op.execute("ALTER TABLE extension_environments DROP COLUMN servicos")
