"""coluna registro_sip no snapshot da linha (verificação SIP pós-aplicação)

Revision ID: 0005_run_line_registro_sip
Revises: 0004_apply_run_lines
Create Date: 2026-05-27 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005_run_line_registro_sip"
down_revision: str | None = "0004_apply_run_lines"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # nullable + sem server_default → aditivo, não afeta dados/fluxos existentes
    with op.batch_alter_table("extension_apply_run_lines") as batch:
        batch.add_column(sa.Column("registro_sip", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("extension_apply_run_lines") as batch:
        batch.drop_column("registro_sip")
