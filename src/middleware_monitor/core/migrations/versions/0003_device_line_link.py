"""device <-> extension_line link + reapply events

Revision ID: 0003_device_line_link
Revises: 0002_extension_configurator
Create Date: 2026-05-23 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_device_line_link"
down_revision: str | None = "0002_extension_configurator"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column(
                "network_status_prev",
                sa.String(16),
                nullable=False,
                server_default="unknown",
            ),
        )

    with op.batch_alter_table("extension_lines") as batch:
        batch.add_column(
            sa.Column("device_id", sa.Integer(), nullable=True),
        )
        batch.create_foreign_key(
            "fk_extension_lines_device_id",
            "devices",
            ["device_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(
            "ix_extension_lines_device_id",
            ["device_id"],
        )

    op.create_table(
        "line_reapply_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "line_id",
            sa.String(32),
            sa.ForeignKey("extension_lines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("extension_apply_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_line_reapply_events_line_id", "line_reapply_events", ["line_id"],
    )
    op.create_index(
        "ix_line_reapply_events_device_id", "line_reapply_events", ["device_id"],
    )
    op.create_index(
        "ix_line_reapply_events_started_at", "line_reapply_events", ["started_at"],
    )

    # Backfill por IP: ExtensionLines sem device_id que tenham IP igual a algum
    # Device. Idempotente (NULL antes; UPDATE só preenche os NULL).
    op.execute(
        """
        UPDATE extension_lines
        SET device_id = (
            SELECT d.id FROM devices d
            WHERE d.ip IS NOT NULL
              AND d.ip <> ''
              AND d.ip = extension_lines.ip
            LIMIT 1
        )
        WHERE device_id IS NULL
          AND ip IS NOT NULL
          AND ip <> ''
        """,
    )


def downgrade() -> None:
    op.drop_index("ix_line_reapply_events_started_at", table_name="line_reapply_events")
    op.drop_index("ix_line_reapply_events_device_id", table_name="line_reapply_events")
    op.drop_index("ix_line_reapply_events_line_id", table_name="line_reapply_events")
    op.drop_table("line_reapply_events")

    with op.batch_alter_table("extension_lines") as batch:
        batch.drop_index("ix_extension_lines_device_id")
        batch.drop_constraint("fk_extension_lines_device_id", type_="foreignkey")
        batch.drop_column("device_id")

    with op.batch_alter_table("devices") as batch:
        batch.drop_column("network_status_prev")
