"""extension configurator tables

Revision ID: 0002_extension_configurator
Revises: 0001_initial
Create Date: 2026-05-21 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002_extension_configurator"
down_revision: str | None = "0001_initial"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "extension_environments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("nome", sa.String(128), nullable=False),
        sa.Column("modelo_telefone", sa.String(64), nullable=False),
        sa.Column("config_padrao", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "extension_lines",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "environment_id",
            sa.String(64),
            sa.ForeignKey("extension_environments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("numero_ramal", sa.String(32), nullable=False, server_default=""),
        sa.Column("user_auth", sa.String(64), nullable=False, server_default=""),
        sa.Column("senha_sip", sa.String(128), nullable=False, server_default=""),
        sa.Column("servidor_sip", sa.String(128), nullable=False, server_default=""),
        sa.Column("numero_abreviado", sa.String(32), nullable=False, server_default=""),
        sa.Column("nome_visivel", sa.String(64), nullable=False, server_default=""),
        sa.Column("ultimo_hash_aplicado", sa.String(64), nullable=True),
        sa.Column("ultimo_status", sa.String(16), nullable=True),
        sa.Column("ultima_aplicacao", sa.DateTime(), nullable=True),
        sa.Column("ultimo_erro", sa.Text(), nullable=True),
        sa.Column("ultimo_modelo", sa.String(64), nullable=True),
        sa.Column("ultimo_mac", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_extension_lines_environment_id", "extension_lines", ["environment_id"],
    )

    op.create_table(
        "extension_apply_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "environment_id",
            sa.String(64),
            sa.ForeignKey("extension_environments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ok", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("falha", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("forcado", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("operador", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_extension_apply_runs_environment_id",
        "extension_apply_runs", ["environment_id"],
    )
    op.create_index(
        "ix_extension_apply_runs_started_at",
        "extension_apply_runs", ["started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extension_apply_runs_started_at", table_name="extension_apply_runs",
    )
    op.drop_index(
        "ix_extension_apply_runs_environment_id", table_name="extension_apply_runs",
    )
    op.drop_table("extension_apply_runs")
    op.drop_index("ix_extension_lines_environment_id", table_name="extension_lines")
    op.drop_table("extension_lines")
    op.drop_table("extension_environments")
