"""cifra em repouso da senha SIP e das senhas web do ambiente

v2.14.0. Até aqui `extension_lines.senha_sip` e as quatro senhas dentro de
`extension_environments.config_padrao` (`web_password`, `nova_web_password`,
`menu_password`, `keylock_password`) ficavam em **texto claro** no SQLite,
enquanto `docs/AGENTE-NOC.md` afirmava que credencial de aparelho ficava
cifrada. Passam a usar a mesma `SecretBox` do token do USCall e da senha do
broker, com o prefixo `enc:v1:` marcando o que já é ciphertext.

`senha_sip` vira `Text`: a senha cifrada não cabe em `String(128)`.

**Esta migration não falha quando não há chave.** Instalação com o default
`change-me` não consegue cifrar, e a atualização que protege o segredo não pode
ser a que impede o cliente de atualizar: nesse caso ela só alarga a coluna e
deixa os valores como estão — o `repository` continua gravando em claro e a tela
de sistema passa a dizer isso em voz alta.

Revision ID: 0013_segredos_cifrados
Revises: 0012_noc_tarefas
Create Date: 2026-09-16 00:00:00
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from middleware_monitor.core.crypto import is_encrypted, open_box
from middleware_monitor.domain.extension_configurator.defaults import CHAVES_SECRETAS
from middleware_monitor.settings import get_settings

revision: str = "0013_segredos_cifrados"
down_revision: str | None = "0012_noc_tarefas"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("extension_lines") as batch:
        batch.alter_column(
            "senha_sip",
            existing_type=sa.String(128),
            type_=sa.Text(),
            existing_nullable=False,
        )

    box = open_box(get_settings().secret_key)
    if box is None:
        # Sem chave utilizável não há o que cifrar. A coluna já está larga; o
        # dia em que a chave existir, a primeira gravação de cada linha cifra.
        return

    conn = op.get_bind()
    cifrou = False

    for line_id, senha in conn.execute(
        sa.text("SELECT id, senha_sip FROM extension_lines")
    ).all():
        if not senha or is_encrypted(senha):
            continue
        conn.execute(
            sa.text("UPDATE extension_lines SET senha_sip = :v WHERE id = :id"),
            {"v": box.encrypt_field(senha), "id": line_id},
        )
        cifrou = True

    for env_id, bruto in conn.execute(
        sa.text("SELECT id, config_padrao FROM extension_environments")
    ).all():
        try:
            cfg = json.loads(bruto or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(cfg, dict):
            continue
        mudou = False
        for chave in CHAVES_SECRETAS:
            valor = cfg.get(chave)
            if isinstance(valor, str) and valor and not is_encrypted(valor):
                cfg[chave] = box.encrypt_field(valor)
                mudou = True
        if mudou:
            conn.execute(
                sa.text(
                    "UPDATE extension_environments SET config_padrao = :v WHERE id = :id"
                ),
                {"v": json.dumps(cfg, ensure_ascii=False), "id": env_id},
            )
            cifrou = True

    if cifrou:
        # **Cifrar a coluna não apaga o texto que já estava no arquivo.** O
        # SQLite marca a página antiga como livre e segue em frente: a senha
        # continua legível com um `grep` no `.db` até o arquivo ser reescrito.
        # Um `VACUUM` faria isso, e não cabe aqui — o alembic roda a migration
        # dentro de uma transação, e o SQLite recusa `VACUUM` dentro de uma. A
        # marca fica, e quem compacta é o próximo boot (`app.lifespan`).
        conn.execute(
            sa.text(
                # `updated_by` é FK para users.id — quem escreve aqui é a
                # migration, e ela não é um usuário.
                "INSERT INTO app_config (key, value, is_secret, updated_at, updated_by) "
                "VALUES ('db.compactar_pendente', '1', 0, :agora, NULL) "
                "ON CONFLICT(key) DO UPDATE SET value = '1', updated_at = :agora"
            ),
            {"agora": datetime.now(UTC).replace(tzinfo=None)},
        )


def downgrade() -> None:
    # Decifra de volta para que a versão anterior, que não conhece o prefixo,
    # continue lendo as senhas. Sem chave não há como — e aí o downgrade deixa
    # os valores cifrados, que é o estado seguro: senha ilegível quebra o apply
    # de forma visível; senha corrompida em claro iria para o aparelho.
    box = open_box(get_settings().secret_key)
    if box is not None:
        conn = op.get_bind()
        for line_id, senha in conn.execute(
            sa.text("SELECT id, senha_sip FROM extension_lines")
        ).all():
            if senha and is_encrypted(senha):
                conn.execute(
                    sa.text("UPDATE extension_lines SET senha_sip = :v WHERE id = :id"),
                    {"v": box.decrypt_field(senha), "id": line_id},
                )
        for env_id, bruto in conn.execute(
            sa.text("SELECT id, config_padrao FROM extension_environments")
        ).all():
            try:
                cfg = json.loads(bruto or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(cfg, dict):
                continue
            mudou = False
            for chave in CHAVES_SECRETAS:
                valor = cfg.get(chave)
                if isinstance(valor, str) and is_encrypted(valor):
                    cfg[chave] = box.decrypt_field(valor)
                    mudou = True
            if mudou:
                conn.execute(
                    sa.text(
                        "UPDATE extension_environments SET config_padrao = :v "
                        "WHERE id = :id"
                    ),
                    {"v": json.dumps(cfg, ensure_ascii=False), "id": env_id},
                )

    with op.batch_alter_table("extension_lines") as batch:
        batch.alter_column(
            "senha_sip",
            existing_type=sa.Text(),
            type_=sa.String(128),
            existing_nullable=False,
        )
