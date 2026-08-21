"""tabelas extension_calls e extension_daily_stats

v2.9.0 (fase 4 do coletor MQTT) — chamadas reconstruídas a partir das transições
de estado do ramal, e o resumo diário que sobrevive à poda das transições.

Uma linha de `extension_calls` é **uma perna** da chamada, não uma conversa: numa
ligação interna o PBX publica os dois ramais e cada um vira uma linha, amarrados
pelo `uniqueid` quando ele vem.

Revision ID: 0011_extension_calls
Revises: 0010_extension_status_events
Create Date: 2026-08-21 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0011_extension_calls"
down_revision: str | None = "0010_extension_status_events"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "extension_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ramal", sa.String(64), nullable=False),
        sa.Column("direcao", sa.String(16), nullable=False, server_default="desconhecida"),
        sa.Column("numero", sa.String(64), nullable=True),
        sa.Column("uniqueid", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("ring_seconds", sa.Integer(), nullable=True),
        sa.Column("talk_seconds", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="em_curso"),
        sa.Column("last_event_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_extension_calls_ramal", "extension_calls", ["ramal"])
    op.create_index("ix_extension_calls_uniqueid", "extension_calls", ["uniqueid"])
    op.create_index("ix_extension_calls_started_at", "extension_calls", ["started_at"])
    op.create_index("ix_extension_calls_ramal_started", "extension_calls", ["ramal", "started_at"])
    op.create_index("ix_extension_calls_outcome", "extension_calls", ["outcome"])

    op.create_table(
        "extension_daily_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dia", sa.String(10), nullable=False),
        sa.Column("ramal", sa.String(64), nullable=False),
        sa.Column("chamadas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("atendidas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("perdidas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entrantes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("saintes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("talk_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ring_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_extension_daily_stats_dia", "extension_daily_stats", ["dia"])
    op.create_index("ix_extension_daily_stats_ramal", "extension_daily_stats", ["ramal"])
    op.create_index(
        "ix_extension_daily_stats_dia_ramal", "extension_daily_stats", ["dia", "ramal"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_extension_daily_stats_dia_ramal", table_name="extension_daily_stats")
    op.drop_index("ix_extension_daily_stats_ramal", table_name="extension_daily_stats")
    op.drop_index("ix_extension_daily_stats_dia", table_name="extension_daily_stats")
    op.drop_table("extension_daily_stats")

    op.drop_index("ix_extension_calls_outcome", table_name="extension_calls")
    op.drop_index("ix_extension_calls_ramal_started", table_name="extension_calls")
    op.drop_index("ix_extension_calls_started_at", table_name="extension_calls")
    op.drop_index("ix_extension_calls_uniqueid", table_name="extension_calls")
    op.drop_index("ix_extension_calls_ramal", table_name="extension_calls")
    op.drop_table("extension_calls")
