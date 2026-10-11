"""credenciais de equipamento e leitura de CFTV — a expansão do NOC, Plano 1 (AGENTE-NOC itens 23, 25 e 26)

Duas tabelas novas, nada do que existe muda:

- ``equipamento_credenciais`` — a credencial de monitoria que o NOC resolve e manda
  no ``definir_equipamentos``. O segredo é gravado **cifrado** (``SecretBox``,
  prefixo ``enc:v1:``); o retrato leva só a impressão.
- ``equipamento_cftv`` — a última leitura de canais, discos e hora de cada gravador,
  guardada para os eventos ``cftv.*`` saírem por diferença.

Os módulos aceitos (CONTRATO §13.8) não precisam de tabela: ficam no KV
``app_config`` (``modulos.aceitos``).

Repetível, como a 0015: cada tabela e índice só é criado se ainda não existe.

Revision ID: 0016_cftv
Revises: 0015_equipamentos
Create Date: 2026-09-30 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0016_cftv"
down_revision: str | None = "0015_equipamentos"
branch_labels: str | None = None
depends_on: str | None = None


def _tem_tabela(nome: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(nome)


def _tem_indice(tabela: str, indice: str) -> bool:
    return any(i["name"] == indice for i in sa.inspect(op.get_bind()).get_indexes(tabela))


def upgrade() -> None:
    if not _tem_tabela("equipamento_credenciais"):
        op.create_table(
            "equipamento_credenciais",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "equipamento_id",
                sa.String(24),
                sa.ForeignKey("equipamentos.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("tipo", sa.String(16), nullable=False),
            sa.Column("usuario", sa.String(128), nullable=False, server_default=""),
            sa.Column("segredo", sa.Text(), nullable=False),
            sa.Column("impressao", sa.String(12), nullable=False),
        )
    if not _tem_indice("equipamento_credenciais", "ix_equipamento_credenciais_equipamento_id"):
        op.create_index(
            "ix_equipamento_credenciais_equipamento_id", "equipamento_credenciais", ["equipamento_id"]
        )

    if not _tem_tabela("equipamento_cftv"):
        op.create_table(
            "equipamento_cftv",
            sa.Column(
                "equipamento_id",
                sa.String(24),
                sa.ForeignKey("equipamentos.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("verificacao_id", sa.String(16), nullable=False),
            sa.Column("leitura", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("lido_em", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    for tabela in ("equipamento_cftv", "equipamento_credenciais"):
        if _tem_tabela(tabela):
            op.drop_table(tabela)
