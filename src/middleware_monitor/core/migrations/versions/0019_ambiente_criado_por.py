"""quem criou o ambiente — relatório de criação de ambientes do NOC

``extension_environments`` ganha duas colunas, as duas nulas:

- ``criado_por`` — quem criou: o usuário logado no middleware (tela local), o
  operador do NOC (tarefa do NOC ou tela aberta pelo túnel do NOC).
- ``criado_via`` — por onde: ``local`` · ``tunel`` · ``noc`` · ``dispositivos``.

Ambiente que já existe fica com as duas em NULL, que quer dizer "desconhecido".
Nada de backfill: inventar um autor seria pior que não ter.

Repetível, como a 0015/0016/0017.

Revision ID: 0019_ambiente_criado_por
Revises: 0018_indice_message_id
Create Date: 2026-10-23 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0019_ambiente_criado_por"
down_revision: str | None = "0018_indice_message_id"
branch_labels: str | None = None
depends_on: str | None = None

_COLUNAS = (("criado_por", "VARCHAR(254)"), ("criado_via", "VARCHAR(16)"))


def _colunas(tabela: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(tabela)}


def upgrade() -> None:
    existentes = _colunas("extension_environments")
    for coluna, tipo in _COLUNAS:
        if coluna not in existentes:
            op.execute(f"ALTER TABLE extension_environments ADD COLUMN {coluna} {tipo}")


def downgrade() -> None:
    existentes = _colunas("extension_environments")
    for coluna, _tipo in reversed(_COLUNAS):
        if coluna in existentes:
            op.execute(f"ALTER TABLE extension_environments DROP COLUMN {coluna}")
