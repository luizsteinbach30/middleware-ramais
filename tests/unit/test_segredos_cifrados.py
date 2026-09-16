"""A cifra em repouso dos segredos do Configurador de Ramais (v2.14.0).

Até aqui a senha SIP de cada ramal e as quatro senhas web do ambiente ficavam em
texto claro no SQLite — e o `AGENTE-NOC.md` afirmava o contrário. Estes testes
são a prova de que não ficam mais, e de que nada do que já funcionava quebrou:
o parque não vira `outdated`, a tela não apaga senha sem querer, e a instalação
sem `APP_SECRET_KEY` continua de pé.

O que se mede aqui é o **conteúdo da coluna**, não o que o repository devolve.
Perguntar ao repository se ele cifrou é perguntar ao réu se ele é culpado.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.crypto import ENCRYPTED_PREFIX, open_box
from middleware_monitor.domain.extension_configurator import repository as repo
from middleware_monitor.domain.extension_configurator.defaults import CHAVES_SECRETAS
from middleware_monitor.domain.extension_configurator.service import (
    build_row,
    compute_line_hash,
)
from middleware_monitor.settings import get_settings


def _ambiente_com_linha(db: DBSession, senha: str = "S3nh4-SIP!") -> tuple:
    env = repo.create_environment(db, nome="Loja 01", modelo_telefone="HTEK UC902G")
    repo.save_lines(db, env, [
        {"ip": "10.0.0.10", "numero_ramal": "1001", "senha_sip": senha},
    ])
    db.commit()
    (linha,) = repo.list_lines(db, env.id)
    return env, linha


def test_a_senha_sip_nao_fica_em_claro_na_coluna(db: DBSession) -> None:
    env, linha = _ambiente_com_linha(db, "S3nh4-SIP!")

    # o que está gravado é ciphertext marcado...
    bruto = db.execute(
        text("SELECT senha_sip FROM extension_lines WHERE id = :id"), {"id": linha.id}
    ).scalar_one()
    assert bruto.startswith(ENCRYPTED_PREFIX)
    assert "S3nh4-SIP!" not in bruto

    # ...e quem lê pelo caminho certo recebe a senha de verdade
    assert repo.senha_sip_de(linha) == "S3nh4-SIP!"
    assert build_row(linha, repo.merged_config_padrao(env))["senha_sip"] == "S3nh4-SIP!"


def test_as_senhas_web_do_ambiente_nao_ficam_em_claro(db: DBSession) -> None:
    env = repo.create_environment(db, nome="Loja 02", modelo_telefone="Intelbras V5501")
    repo.update_environment(db, env, config_padrao={
        "web_password": "admin-da-loja",
        "nova_web_password": "trocada-2026",
        "menu_password": "4321",
        "keylock_password": "9876",
    })
    db.commit()

    bruto = db.execute(
        text("SELECT config_padrao FROM extension_environments WHERE id = :id"),
        {"id": env.id},
    ).scalar_one()
    for segredo in ("admin-da-loja", "trocada-2026", "4321", "9876"):
        assert segredo not in bruto
    gravado = json.loads(bruto)
    for chave in CHAVES_SECRETAS:
        assert gravado[chave].startswith(ENCRYPTED_PREFIX)

    # e a leitura devolve tudo em claro, que é o que os vendors recebem
    cfg = repo.merged_config_padrao(env)
    assert cfg["web_password"] == "admin-da-loja"
    assert cfg["keylock_password"] == "9876"
    # campo que não é segredo continua legível na coluna, como sempre foi
    assert cfg["web_user"] == "admin"


def test_clonar_ambiente_nao_vaza_a_senha_do_original(db: DBSession) -> None:
    env = repo.create_environment(db, nome="Loja 03", modelo_telefone="Yealink T31G")
    repo.update_environment(db, env, config_padrao={"web_password": "unica-no-mundo"})
    db.commit()

    copia = repo.clone_environment(db, env, nome="Loja 03 copia")
    db.commit()

    assert "unica-no-mundo" not in copia.config_padrao
    assert repo.merged_config_padrao(copia)["web_password"] == "unica-no-mundo"


def test_o_hash_da_linha_nao_muda_por_causa_da_cifra(db: DBSession) -> None:
    """O parque não pode virar `outdated` por uma mudança de armazenamento.

    O hash é do XML que o aparelho receberia — e o aparelho recebe a senha em
    claro. Se ele fosse calculado sobre o ciphertext, mudaria a cada gravação
    (Fernet carrega timestamp e IV) e toda linha já aplicada apareceria como
    pendente no dia da atualização.
    """
    env, linha = _ambiente_com_linha(db, "MesmaSenha#1")
    primeiro = compute_line_hash(env, linha)
    ciphertext_antes = linha.senha_sip

    # regrava a MESMA senha: ciphertext novo, hash igual
    repo.save_lines(db, env, [
        {"id": linha.id, "ip": "10.0.0.10", "numero_ramal": "1001",
         "senha_sip": "MesmaSenha#1"},
    ])
    db.commit()
    (regravada,) = repo.list_lines(db, env.id)

    assert regravada.senha_sip != ciphertext_antes  # ciphertext diferente
    assert compute_line_hash(env, regravada) == primeiro  # mesmo XML


def test_sem_chave_utilizavel_grava_em_claro_e_diz_que_gravou(
    db: DBSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A atualização que protege o segredo não pode ser a que derruba o cliente.

    Instalação com o `change-me` default não tem como cifrar. Ela continua
    funcionando exatamente como antes — e `segredos_em_claro()` existe para que
    a tela possa dizer isso, em vez de a instalação achar que está protegida.
    """
    assert repo.segredos_em_claro() is False  # a chave dos testes serve

    monkeypatch.setenv("APP_SECRET_KEY", "change-me")
    get_settings.cache_clear()
    try:
        assert repo.segredos_em_claro() is True
        _env, linha = _ambiente_com_linha(db, "sem-cifra-aqui")
        assert linha.senha_sip == "sem-cifra-aqui"
        assert repo.senha_sip_de(linha) == "sem-cifra-aqui"
    finally:
        get_settings.cache_clear()


def test_valor_legado_em_claro_continua_legivel(db: DBSession) -> None:
    """Coluna com texto claro de antes da migration não vira lixo.

    O prefixo é o que separa os dois mundos: sem ele, decifrar seria adivinhar,
    e o palpite errado mandaria um texto qualquer para o telefone.
    """
    _env, linha = _ambiente_com_linha(db, "sera-substituida")
    db.execute(
        text("UPDATE extension_lines SET senha_sip = :v WHERE id = :id"),
        {"v": "senha-legada-em-claro", "id": linha.id},
    )
    db.commit()
    db.refresh(linha)

    assert repo.senha_sip_de(linha) == "senha-legada-em-claro"


def test_ciphertext_de_outra_chave_estoura_em_vez_de_virar_senha(
    db: DBSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marcado como cifrado **tem** de decifrar. Falhou, é erro, não é senha.

    É a diferença entre o aparelho recusar uma senha errada — visível — e o
    sistema aplicar silenciosamente um valor que ninguém escolheu.
    """
    _env, linha = _ambiente_com_linha(db, "original")
    outra = open_box("outra-chave-bem-comprida-1234")
    assert outra is not None
    db.execute(
        text("UPDATE extension_lines SET senha_sip = :v WHERE id = :id"),
        {"v": outra.encrypt_field("de-outra-instalacao"), "id": linha.id},
    )
    db.commit()
    db.refresh(linha)

    with pytest.raises(ValueError, match="invalid_secret"):
        repo.senha_sip_de(linha)
