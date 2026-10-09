"""tabelas de equipamentos — a expansão do NOC, Plano 0 (AGENTE-NOC itens 18 a 21)

Câmera, switch e servidor não têm ramal: ganham cadastro próprio, com as
verificações de disponibilidade de cada um (icmp, tcp, http), as amostras e os
eventos de transição. **Só tabelas novas**: nada do que existe muda.

Repetível: cada tabela e índice só é criado se ainda não existe. A linha da
expansão é paralela à ``main``, e um banco que já passou por aqui (ou foi
restaurado de um snapshot que tinha as tabelas) não pode quebrar o boot.

Revision ID: 0015_equipamentos
Revises: 0014_remover_webhooks
Create Date: 2026-09-30 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0015_equipamentos"
down_revision: str | None = "0014_remover_webhooks"
branch_labels: str | None = None
depends_on: str | None = None


def _tem_tabela(nome: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(nome)


def _tem_indice(tabela: str, indice: str) -> bool:
    return any(i["name"] == indice for i in sa.inspect(op.get_bind()).get_indexes(tabela))


def _indice(nome: str, tabela: str, colunas: list[str]) -> None:
    if not _tem_indice(tabela, nome):
        op.create_index(nome, tabela, colunas)


def upgrade() -> None:
    if not _tem_tabela("equipamentos"):
        op.create_table(
            "equipamentos",
            sa.Column("id", sa.String(24), primary_key=True),
            sa.Column("ref", sa.String(64), nullable=False),
            sa.Column("tipo", sa.String(32), nullable=False),
            sa.Column("nome", sa.String(128), nullable=False),
            sa.Column("host", sa.String(255), nullable=False),
            sa.Column("mac", sa.String(32), nullable=True),
            sa.Column("fabricante", sa.String(64), nullable=True),
            sa.Column("modelo", sa.String(64), nullable=True),
            sa.Column("pai_id", sa.String(24), nullable=True),
            sa.Column("tentativas_para_cair", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("estado", sa.String(16), nullable=False, server_default="desconhecido"),
            sa.Column("estado_desde", sa.DateTime(), nullable=True),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("atualizado_em", sa.DateTime(), nullable=False),
        )
    _indice("ix_equipamentos_ref", "equipamentos", ["ref"])

    if not _tem_tabela("equipamento_verificacoes"):
        op.create_table(
            "equipamento_verificacoes",
            sa.Column("id", sa.String(16), primary_key=True),
            sa.Column(
                "equipamento_id",
                sa.String(24),
                sa.ForeignKey("equipamentos.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("ref", sa.String(64), nullable=False),
            sa.Column("posicao", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tipo", sa.String(8), nullable=False),
            sa.Column("parametros", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("intervalo_s", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("estado", sa.String(16), nullable=False, server_default="desconhecido"),
            sa.Column("estado_desde", sa.DateTime(), nullable=True),
            sa.Column("falhas_seguidas", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("ultimo_valor", sa.Integer(), nullable=True),
            sa.Column("ultima_em", sa.DateTime(), nullable=True),
            sa.Column("ultimo_erro", sa.Text(), nullable=True),
        )
    _indice("ix_equipamento_verificacoes_equipamento_id", "equipamento_verificacoes", ["equipamento_id"])

    if not _tem_tabela("equipamento_amostras"):
        op.create_table(
            "equipamento_amostras",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("verificacao_id", sa.String(16), nullable=False),
            sa.Column("em", sa.DateTime(), nullable=False),
            sa.Column("ok", sa.Boolean(), nullable=False),
            sa.Column("valor", sa.Integer(), nullable=True),
        )
    _indice("ix_equipamento_amostras_em", "equipamento_amostras", ["em"])

    if not _tem_tabela("equipamento_eventos"):
        op.create_table(
            "equipamento_eventos",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("equipamento_id", sa.String(24), nullable=False),
            sa.Column("verificacao_id", sa.String(16), nullable=True),
            sa.Column("tipo", sa.String(32), nullable=False),
            sa.Column("de", sa.String(16), nullable=False),
            sa.Column("para", sa.String(16), nullable=False),
            sa.Column("em", sa.DateTime(), nullable=False),
            sa.Column("detalhe", sa.Text(), nullable=True),
        )
    _indice("ix_equipamento_eventos_em", "equipamento_eventos", ["em"])


def downgrade() -> None:
    for tabela in ("equipamento_eventos", "equipamento_amostras", "equipamento_verificacoes", "equipamentos"):
        if _tem_tabela(tabela):
            op.drop_table(tabela)
