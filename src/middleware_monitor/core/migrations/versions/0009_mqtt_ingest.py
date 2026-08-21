"""tabelas mqtt_brokers, mqtt_messages e mqtt_connection_events

v2.8.0 — ingestão de mensagens do broker EMQX. O serviço que publica o status
dos ramais não registra os próprios envios; estas tabelas guardam a mensagem
como ela chegou (o comprovante), a configuração do broker descoberta na tela e
o histórico de conexão do coletor (a prova de cobertura: sem ele, a ausência de
uma mensagem no período não prova nada).

Revision ID: 0009_mqtt_ingest
Revises: 0008_device_action_events
Create Date: 2026-08-19 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009_mqtt_ingest"
down_revision: str | None = "0008_device_action_events"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "mqtt_brokers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nome", sa.String(64), nullable=False),
        sa.Column("address_input", sa.String(255), nullable=False, server_default=""),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("transport", sa.String(16), nullable=False, server_default="tcp"),
        sa.Column("tls", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("ws_path", sa.String(128), nullable=True),
        sa.Column("username", sa.String(128), nullable=False, server_default=""),
        sa.Column("password", sa.Text(), nullable=False, server_default=""),
        sa.Column("tls_verify", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("tls_fingerprint", sa.String(95), nullable=True),
        sa.Column("topics", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("qos", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("clean_session", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("client_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("max_payload_kb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "mqtt_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "broker_id", sa.Integer(),
            sa.ForeignKey("mqtt_brokers.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("topic", sa.String(512), nullable=False),
        sa.Column("ramal", sa.String(64), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("qos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retained", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("b64", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("event_at", sa.DateTime(), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_mqtt_messages_broker_id", "mqtt_messages", ["broker_id"])
    op.create_index("ix_mqtt_messages_received_at", "mqtt_messages", ["received_at"])
    op.create_index("ix_mqtt_messages_topic_ts", "mqtt_messages", ["topic", "received_at"])
    op.create_index("ix_mqtt_messages_ramal_ts", "mqtt_messages", ["ramal", "received_at"])
    op.create_index("ix_mqtt_messages_pinned", "mqtt_messages", ["pinned"])

    op.create_table(
        "mqtt_connection_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("broker_id", sa.Integer(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("client_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("endpoint", sa.String(255), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_mqtt_connection_events_broker_id", "mqtt_connection_events", ["broker_id"],
    )
    op.create_index(
        "ix_mqtt_connection_events_timestamp", "mqtt_connection_events", ["timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_mqtt_connection_events_timestamp", table_name="mqtt_connection_events")
    op.drop_index("ix_mqtt_connection_events_broker_id", table_name="mqtt_connection_events")
    op.drop_table("mqtt_connection_events")

    op.drop_index("ix_mqtt_messages_pinned", table_name="mqtt_messages")
    op.drop_index("ix_mqtt_messages_ramal_ts", table_name="mqtt_messages")
    op.drop_index("ix_mqtt_messages_topic_ts", table_name="mqtt_messages")
    op.drop_index("ix_mqtt_messages_received_at", table_name="mqtt_messages")
    op.drop_index("ix_mqtt_messages_broker_id", table_name="mqtt_messages")
    op.drop_table("mqtt_messages")

    op.drop_table("mqtt_brokers")
