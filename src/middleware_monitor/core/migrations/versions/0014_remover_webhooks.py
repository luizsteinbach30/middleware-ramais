"""remove o modulo de webhooks

v2.14.0, a pedido do dono: o middleware nao empurra mais webhook para lugar
nenhum. O que eles mandavam (`extensions` e `devices`) vai para o NOC pela
telemetria desde a v2.13.0 — `domain/noc/telemetria.py` leva isso e mais o que
o webhook nunca levou (amostras de ping, eventos, aplicacoes, perfis).

Sai a tabela `webhook_events`, saem as chaves de configuracao dos destinos, e
**a chave do intervalo fica, com o nome certo**: `webhook_interval_minutes`
sempre governou a cadencia da coleta do USCall e do ping da frota, nao o envio
do webhook. Apaga-la pararia o coletor; por isso ela e renomeada para
`coleta_interval_minutes` em vez de removida.

O `downgrade` recria a tabela vazia e devolve o nome antigo da chave. Ele NAO
tenta ressuscitar os eventos apagados nem os destinos configurados: log de
entrega de uma integracao que nao existe mais nao tem para onde voltar, e um
destino meio restaurado voltaria a disparar para um endereco que o operador
talvez ja tenha desligado do outro lado.

Revision ID: 0014_remover_webhooks
Revises: 0013_segredos_cifrados
Create Date: 2026-09-16 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0014_remover_webhooks"
down_revision: str | None = "0013_segredos_cifrados"
branch_labels: str | None = None
depends_on: str | None = None

# Os destinos e os ajustes que so o sender lia.
_CHAVES = (
    "webhooks.extensions.enabled", "webhooks.extensions.url",
    "webhooks.extensions.token", "webhooks.extensions.last_status",
    "webhooks.devices.enabled", "webhooks.devices.url",
    "webhooks.devices.token", "webhooks.devices.last_status",
    "webhooks.results.enabled", "webhooks.results.url",
    "webhooks.results.token", "webhooks.results.last_status",
    "webhook_log_retention_days",
    "webhook_timeout_seconds",
    # Herdadas de instalacoes anteriores a v2.2: o intervalo era por tipo.
    "extensions_interval_seconds", "devices_interval_seconds",
    "results_interval_seconds",
)


def upgrade() -> None:
    conn = op.get_bind()

    # O nome primeiro: se a tabela caisse antes e algo falhasse aqui, o
    # coletor ficaria sem cadencia ate alguem reparar na mao.
    conn.execute(
        sa.text(
            "UPDATE app_config SET key = 'coleta_interval_minutes' "
            "WHERE key = 'webhook_interval_minutes' "
            "  AND NOT EXISTS (SELECT 1 FROM app_config WHERE key = 'coleta_interval_minutes')"
        )
    )
    conn.execute(sa.text("DELETE FROM app_config WHERE key = 'webhook_interval_minutes'"))

    conn.execute(
        sa.text("DELETE FROM app_config WHERE key IN :chaves").bindparams(
            sa.bindparam("chaves", expanding=True)
        ),
        {"chaves": list(_CHAVES)},
    )

    op.drop_table("webhook_events")

    # O log de entrega podia guardar corpo de payload e token de destino. A
    # tabela sumiu; o texto continua nas paginas livres ate o arquivo ser
    # reescrito, e o VACUUM nao roda dentro da transacao do alembic (0013).
    conn.execute(
        sa.text(
            "INSERT INTO app_config (key, value, is_secret, updated_at, updated_by) "
            "VALUES ('db.compactar_pendente', '1', 0, CURRENT_TIMESTAMP, NULL) "
            "ON CONFLICT(key) DO UPDATE SET value = '1'"
        )
    )


def downgrade() -> None:
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False, index=True),
        sa.Column("event_type", sa.String(32), nullable=False, index=True),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("total_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_replay", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("replay_of", sa.Integer(), nullable=True),
    )
    op.get_bind().execute(
        sa.text(
            "UPDATE app_config SET key = 'webhook_interval_minutes' "
            "WHERE key = 'coleta_interval_minutes'"
        )
    )
