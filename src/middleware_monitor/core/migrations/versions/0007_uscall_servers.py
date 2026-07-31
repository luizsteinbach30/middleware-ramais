"""tabela uscall_servers (multi-servidor USCall) + devices.uscall_server_id

Suporte a mais de um servidor USCall por instalação. A config KV antiga
(uscall_host / uscall_token / uscall_verify_ssl em app_config) vira a primeira
linha da tabela ("Principal"), copiando o ciphertext do token VERBATIM — a
mesma APP_SECRET_KEY decripta (SecretBox), então nenhum re-encrypt é preciso.
As linhas KV legadas permanecem no banco (rollback barato) mas deixam de ser
lidas pelo app.

Revision ID: 0007_uscall_servers
Revises: 0006_extension_line_posicao
Create Date: 2026-07-31 00:00:00
"""
from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0007_uscall_servers"
down_revision: str | None = "0006_extension_line_posicao"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "uscall_servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nome", sa.String(64), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        # ciphertext SecretBox (mesma cifra da app_config)
        sa.Column("token", sa.Text(), nullable=False, server_default=""),
        sa.Column("verify_ssl", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("uscall_server_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_devices_uscall_server_id",
            "uscall_servers",
            ["uscall_server_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_devices_uscall_server_id", ["uscall_server_id"])

    # Data migration: config KV atual vira o servidor "Principal".
    conn = op.get_bind()
    rows = {
        k: v
        for k, v in conn.execute(
            sa.text(
                "SELECT key, value FROM app_config "
                "WHERE key IN ('uscall_host', 'uscall_token', 'uscall_verify_ssl')"
            )
        )
    }
    host = (rows.get("uscall_host") or "").strip()
    if host:
        now = datetime.now(UTC).replace(tzinfo=None)
        conn.execute(
            sa.text(
                "INSERT INTO uscall_servers "
                "(nome, host, token, verify_ssl, enabled, created_at, updated_at) "
                "VALUES (:nome, :host, :token, :verify, 1, :now, :now)"
            ),
            {
                "nome": "Principal",
                "host": host,
                "token": rows.get("uscall_token") or "",
                "verify": 1 if (rows.get("uscall_verify_ssl") or "1") in ("1", "true", "True") else 0,
                "now": now,
            },
        )
        # Devices existentes vieram todos do servidor único antigo.
        server_id = conn.execute(sa.text("SELECT id FROM uscall_servers LIMIT 1")).scalar()
        conn.execute(
            sa.text("UPDATE devices SET uscall_server_id = :sid"),
            {"sid": server_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_index("ix_devices_uscall_server_id")
        batch.drop_constraint("fk_devices_uscall_server_id", type_="foreignkey")
        batch.drop_column("uscall_server_id")
    op.drop_table("uscall_servers")
