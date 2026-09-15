"""tabela noc_tarefas — o registro local das tarefas recebidas do NOC

v2.13.0 (agente do NOC, Fases 2 e 3). Uma linha por tarefa que o NOC entregou:
o que foi pedido, quando começou, o resultado e se o NOC já confirmou o
recebimento dele.

É a **idempotência do lado do agente**: tarefa repetida devolve o resultado
gravado e não executa de novo. ``send_config`` reinicia o aparelho — uma
reentrega por falha de rede, sem este registro, derrubaria o telefone duas vezes.

Revision ID: 0012_noc_tarefas
Revises: 0011_extension_calls
Create Date: 2026-09-15 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012_noc_tarefas"
down_revision: str | None = "0011_extension_calls"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "noc_tarefas",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tipo", sa.String(64), nullable=False),
        sa.Column("raio", sa.String(32), nullable=False),
        sa.Column("idempotencia", sa.String(64), nullable=False),
        sa.Column("pedido", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("pedida_por", sa.String(128), nullable=True),
        sa.Column("recebida_em", sa.DateTime(), nullable=False),
        sa.Column("iniciada_em", sa.DateTime(), nullable=True),
        sa.Column("concluida_em", sa.DateTime(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=True),
        sa.Column("nao_suportado", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resultado", sa.Text(), nullable=True),
        sa.Column("bruto", sa.Text(), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("entregue_em", sa.DateTime(), nullable=True),
        sa.Column("tentativas_de_entrega", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_noc_tarefas_entregue", "noc_tarefas", ["concluida_em", "entregue_em"])


def downgrade() -> None:
    op.drop_index("ix_noc_tarefas_entregue", table_name="noc_tarefas")
    op.drop_table("noc_tarefas")
