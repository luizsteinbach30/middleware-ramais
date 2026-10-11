"""índice em ``extension_status_events.message_id`` — a poda diária travava o processo

``extension_status_events.message_id`` aponta para ``mqtt_messages.id`` com ``ON DELETE SET NULL``, e o
middleware liga ``foreign_keys=ON``. Sem índice nessa coluna, cada mensagem MQTT apagada pela poda
(``run_retention``, 00:30) obrigava o SQLite a varrer a tabela de eventos de status inteira. Medido numa cópia
do banco de homologação (09/10): 124.826 mensagens a podar contra 117.938 eventos — sem o índice, mais de
5 minutos e não terminou (na máquina, o processo ficou mais de 30 minutos parado, interface e jobs junto);
com o índice, a poda inteira em 2,0 s.

Só cria o índice; nenhum dado muda. Repetível (``IF NOT EXISTS``).

Revision ID: 0018_indice_message_id
Revises: 0017_ambiente_servicos
Create Date: 2026-10-09 22:20:00
"""

from __future__ import annotations

from alembic import op

revision: str = "0018_indice_message_id"
down_revision: str | None = "0017_ambiente_servicos"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_extension_status_events_message_id "
        "ON extension_status_events (message_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_extension_status_events_message_id")
