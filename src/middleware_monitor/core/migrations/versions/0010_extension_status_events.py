"""tabela extension_status_events + estado de telefonia no device

v2.8.0 (fase 3) — normalização do que chega pelo MQTT. A mensagem crua continua
no ledger (o comprovante); aqui ficam só as **transições** de estado do ramal,
que é o que o painel ao vivo e a reconstrução de chamadas consomem.

As colunas novas em ``devices`` separam duas perguntas que estavam misturadas:
``logical_status`` responde "o ramal está registrado no PBX?" (e é ela que
decide reaplicação de configuração), enquanto ``telephony_status`` responde
"o que o ramal está fazendo agora?". Um ramal em conversa está registrado.

Revision ID: 0010_extension_status_events
Revises: 0009_mqtt_ingest
Create Date: 2026-08-21 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010_extension_status_events"
down_revision: str | None = "0009_mqtt_ingest"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "extension_status_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ramal", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_raw", sa.String(64), nullable=False, server_default=""),
        sa.Column("numero", sa.String(64), nullable=True),
        sa.Column("uniqueid", sa.String(64), nullable=True),
        sa.Column("event_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("call_started_at", sa.DateTime(), nullable=True),
        sa.Column(
            "message_id", sa.Integer(),
            sa.ForeignKey("mqtt_messages.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.create_index("ix_extension_status_events_ramal", "extension_status_events", ["ramal"])
    op.create_index(
        "ix_extension_status_events_received_at", "extension_status_events", ["received_at"],
    )
    op.create_index(
        "ix_extension_status_events_ramal_ts", "extension_status_events",
        ["ramal", "received_at"],
    )

    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column(
                "telephony_status", sa.String(16), nullable=False, server_default="unknown",
            )
        )
        batch.add_column(sa.Column("telephony_status_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("telephony_numero", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column(
                "status_source", sa.String(16), nullable=False, server_default="unknown",
            )
        )

    # Instalações existentes só tiveram o estado lógico escrito pela coleta REST.
    op.execute(
        "UPDATE devices SET status_source = 'uscall' WHERE logical_status <> 'unknown'"
    )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("status_source")
        batch.drop_column("telephony_numero")
        batch.drop_column("telephony_status_at")
        batch.drop_column("telephony_status")

    op.drop_index("ix_extension_status_events_ramal_ts", table_name="extension_status_events")
    op.drop_index(
        "ix_extension_status_events_received_at", table_name="extension_status_events",
    )
    op.drop_index("ix_extension_status_events_ramal", table_name="extension_status_events")
    op.drop_table("extension_status_events")
