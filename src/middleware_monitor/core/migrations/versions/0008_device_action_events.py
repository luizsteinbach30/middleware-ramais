"""tabela device_action_events — auditoria de ações remotas nos telefones

Feature 6 (v2.7.0): "Normalizar telefone" (volume máx + DND off) e demais ações
remotas. Auditoria flat, desacoplada do padrão de run de aplicação.

Revision ID: 0008_device_action_events
Revises: 0007_uscall_servers
Create Date: 2026-07-31 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008_device_action_events"
down_revision: str | None = "0007_uscall_servers"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "device_action_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("environment_id", sa.String(64), nullable=True),
        sa.Column("line_id", sa.String(32), nullable=True),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("vendor", sa.String(32), nullable=False, server_default=""),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("operador", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_device_action_events_timestamp", "device_action_events", ["timestamp"],
    )
    op.create_index(
        "ix_device_action_events_environment_id",
        "device_action_events", ["environment_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_device_action_events_environment_id", table_name="device_action_events")
    op.drop_index("ix_device_action_events_timestamp", table_name="device_action_events")
    op.drop_table("device_action_events")
