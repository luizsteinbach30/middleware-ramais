"""snapshot por linha em cada execução (relatório de aplicação)

Revision ID: 0004_apply_run_lines
Revises: 0003_device_line_link
Create Date: 2026-05-25 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004_apply_run_lines"
down_revision: str | None = "0003_device_line_link"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "extension_apply_run_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("extension_apply_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("line_id", sa.String(32), nullable=True),
        sa.Column("numero_ramal", sa.String(32), nullable=False, server_default=""),
        sa.Column("ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("nome_visivel", sa.String(64), nullable=False, server_default=""),
        sa.Column("status_antes", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("status_depois", sa.String(16), nullable=False, server_default="running"),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("modelo", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_extension_apply_run_lines_run_id",
        "extension_apply_run_lines",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extension_apply_run_lines_run_id",
        table_name="extension_apply_run_lines",
    )
    op.drop_table("extension_apply_run_lines")
