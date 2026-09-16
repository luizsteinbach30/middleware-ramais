"""Tests for the config repository — leitura, escrita parcial e o que ele NÃO faz.

Nota v2.14.0: este arquivo tinha três testes de webhook, e dois deles eram a
**única** cobertura do round-trip de segredo do KV de configuração. Com o módulo
de webhooks removido, esse caminho deixou de existir: `load_secret` ficou sem
chamador e foi removido junto, e `update_config` não grava mais nenhum campo
cifrado. Não há o que testar no lugar — o que existe agora é a garantia de que
segredo **não** passa por aqui, e é ela que o último teste fixa.

Segredo com cifra continua existindo no produto, com dono próprio em cada caso:
`domain/uscall/repository.py` (token do PBX), `domain/mqtt/repository.py` (senha
do broker), `domain/noc/estado.py` (credencial do NOC), `domain/backup/settings.py`
(passphrase) e `domain/extension_configurator/repository.py` (senha SIP e senhas
web do ambiente, v2.14.0).
"""

from __future__ import annotations

from middleware_monitor.domain.config import repository as repo
from middleware_monitor.domain.config.repository import load_config, update_config
from middleware_monitor.domain.config.schemas import AppConfigUpdate


def test_uscall_kv_nao_recebe_mais_escrita(db) -> None:
    """Campos extra no payload são ignorados (pydantic extra=ignore) e o KV
    legado do USCall permanece intocado."""
    update_config(
        db,
        AppConfigUpdate.model_validate({"uscall_host": "x.test", "uscall_token": "t"}),
        user_id=None,
    )
    cfg = load_config(db)
    assert cfg.uscall_host == ""
    assert cfg.uscall_token is None


def test_intervalo_da_coleta_vai_e_volta(db) -> None:
    """O botão que governa a cadência da coleta — o único que sobrou do bloco
    que se chamava "webhooks" na tela."""
    update_config(db, AppConfigUpdate(coleta_interval_minutes=15), user_id=None)
    assert load_config(db).coleta_interval_minutes == 15


def test_le_o_nome_antigo_da_chave_do_intervalo(db) -> None:
    """Banco restaurado de um backup anterior à v2.14.0 traz
    `webhook_interval_minutes`, que a migration 0014 renomeia. A leitura aceita
    os dois nomes — restaurar um backup velho não pode zerar a cadência da
    coleta e fazer o middleware varrer o PBX a cada minuto."""
    # `_set` direto: é exatamente o que a migration 0014 deixa no banco.
    repo._set(
        db, "webhook_interval_minutes", "42", is_secret=False, user_id=None,
    )
    db.commit()
    assert load_config(db).coleta_interval_minutes == 42


def test_nenhum_segredo_se_escreve_pelo_config(db) -> None:
    """A garantia que substitui os testes de webhook removidos.

    Enquanto `update_config` sabia cifrar, bastava alguém acrescentar um campo à
    `AppConfigUpdate` para um segredo novo entrar por um caminho que ninguém
    testa. Agora nenhuma linha escrita por aqui nasce marcada como secreta.
    """
    update_config(
        db,
        AppConfigUpdate(client_code="loja-01", coleta_interval_minutes=30),
        user_id=None,
    )
    db.commit()
    marcadas = [r.key for r in repo._all_rows(db).values() if r.is_secret]
    assert marcadas == []
    assert not hasattr(repo, "load_secret")
