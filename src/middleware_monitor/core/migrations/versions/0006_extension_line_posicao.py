"""coluna posicao em extension_lines — ordem canônica = ordem da planilha

As linhas eram ordenadas por (created_at, id); num save em lote o created_at
empata e o id é uuid aleatório, então a ordem exibida "embaralhava" em relação
à planilha digitada/importada. A coluna posicao grava a posição da linha no
payload salvo e passa a ser o primeiro critério de ordenação.

Revision ID: 0006_extension_line_posicao
Revises: 0005_run_line_registro_sip
Create Date: 2026-07-31 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006_extension_line_posicao"
down_revision: str | None = "0005_run_line_registro_sip"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("extension_lines") as batch:
        batch.add_column(
            sa.Column("posicao", sa.Integer(), nullable=False, server_default="0")
        )
    # Backfill: numera as linhas existentes por ambiente seguindo a ordenação
    # antiga (created_at, id), preservando a ordem que o usuário via até aqui.
    # ROW_NUMBER exige SQLite >= 3.25 (Python 3.11 embute 3.37+).
    op.execute(
        """
        UPDATE extension_lines SET posicao = (
          SELECT rn FROM (
            SELECT id, ROW_NUMBER() OVER (
              PARTITION BY environment_id ORDER BY created_at, id
            ) - 1 AS rn
            FROM extension_lines
          ) t
          WHERE t.id = extension_lines.id
        )
        """
    )
    op.create_index(
        "ix_extension_lines_env_posicao",
        "extension_lines",
        ["environment_id", "posicao"],
    )


def downgrade() -> None:
    op.drop_index("ix_extension_lines_env_posicao", table_name="extension_lines")
    with op.batch_alter_table("extension_lines") as batch:
        batch.drop_column("posicao")
