"""Edição central (item 13, etapa I5 do NOC) — ``docs/AGENTE-NOC.md`` e
``noc-workconnect/docs/CONTRATO-DO-AGENTE.md`` §10.

O que se prova aqui, cada garantia com o seu teste:

- a lista do que pode mudar é **daqui**: ``ip``, senha, servidor SIP e chave de segredo
  voltam ``CAMPO_NAO_PERMITIDO`` mesmo que o NOC mande;
- o ``de`` que o NOC viu é conferido: diferente, nada é gravado e volta o valor atual;
- a validação do fabricante roda antes (o ``;`` do TIP 125i);
- backup antes, gravação na planilha e **nunca no aparelho**, releitura no resultado.
"""

from __future__ import annotations

import pytest

from middleware_monitor.core.models import ExtensionEnvironment, ExtensionLine
from middleware_monitor.domain.backup import snapshot
from middleware_monitor.domain.extension_configurator import repository as repo
from middleware_monitor.domain.extension_configurator.service import compute_line_hash
from middleware_monitor.domain.noc import executor
from tests.api.test_noc_tarefas import _ambiente, _AparelhoFalso, _limpo, _processar, _tarefa  # noqa: F401

LINHA = "editar_linha_do_ambiente"
CONFIG = "editar_config_do_ambiente"
ESCRITA = "ESCRITA_REVERSIVEL"


@pytest.fixture
def aparelho(monkeypatch) -> _AparelhoFalso:
    """Qualquer caminho até o telefone quebra o teste: a edição grava só na planilha."""
    falso = _AparelhoFalso()
    monkeypatch.setattr("middleware_monitor.domain.extension_configurator.actions.run_action_on_line", falso)

    async def proibido(*_a, **_k):
        raise AssertionError("a edição central não aplica config no aparelho")

    monkeypatch.setattr("middleware_monitor.domain.extension_configurator.apply.run_apply", proibido)
    return falso


def _linha(db, env) -> ExtensionLine:
    db.expire_all()
    return db.get(ExtensionEnvironment, env.id).lines[0]


def _editar_linha(env, campos, ramal="1001", id_="e1") -> dict:
    return _tarefa(
        id_, tipo=LINHA, raio=ESCRITA, pedido={"ambienteId": env.id, "ramal": ramal, "campos": campos}
    )


# --- A lista daqui ------------------------------------------------------------------------------


def test_os_dois_verbos_sao_escrita_e_exigem_o_pedido_inteiro() -> None:
    assert executor.ACOES[LINHA].raio == ESCRITA
    assert executor.ACOES[CONFIG].raio == ESCRITA
    faltando = executor.conferir(LINHA, ESCRITA, {"ambienteId": "x", "campos": []})
    assert faltando is not None and "precisa de: ramal" in (faltando.erro or "")
    rede = executor.conferir(CONFIG, ESCRITA, {"ambienteId": "x", "campos": [], "gateway": "1"})
    assert rede is not None and "P_CODE_DE_REDE" in (rede.erro or "")
    assert executor.conferir(LINHA, "LEITURA", {"ambienteId": "x", "ramal": "1", "campos": []}) is not None


@pytest.mark.parametrize("campo", ["ip", "senhaSip", "userAuth", "servidorSip", "nome_visivel", "posicao"])
async def test_campo_da_linha_fora_da_lista_e_recusado_sem_gravar(db, aparelho, campo) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    pronta = await _processar(_editar_linha(env, [{"campo": campo, "de": "", "para": "x"}]))
    assert pronta.corpo["naoSuportado"] is True
    assert pronta.corpo["resultado"]["recusa"] == "CAMPO_NAO_PERMITIDO"
    if campo == "ip":
        assert "(é de rede)" in pronta.corpo["erro"]
    assert _linha(db, env).ip == "10.0.0.11"
    assert aparelho.chamadas == []


@pytest.mark.parametrize(
    "chave", ["sip_server", "web_password", "menu_password", "web_user", "sip_transport", "ip"]
)
async def test_chave_da_config_fora_da_lista_e_recusada(db, aparelho, chave) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    antes = env.config_padrao
    pedido = {"ambienteId": env.id, "campos": [{"chave": chave, "de": None, "para": "x"}]}
    pronta = await _processar(_tarefa("c1", tipo=CONFIG, raio=ESCRITA, pedido=pedido))
    assert pronta.corpo["resultado"]["recusa"] == "CAMPO_NAO_PERMITIDO"
    db.expire_all()
    assert db.get(ExtensionEnvironment, env.id).config_padrao == antes


# --- A linha ------------------------------------------------------------------------------------


async def test_editar_linha_grava_na_planilha_rele_e_nao_toca_o_aparelho(db, aparelho) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    campos = [
        {"campo": "nomeVisivel", "de": "", "para": "Caixa 1"},
        {"campo": "numeroAbreviado", "de": "", "para": "11"},
    ]
    pronta = await _processar(_editar_linha(env, campos))
    assert pronta.corpo["ok"] is True, pronta.corpo
    r = pronta.corpo["resultado"]
    assert r["campos"] == [
        {"campo": "nomeVisivel", "gravado": "Caixa 1"},
        {"campo": "numeroAbreviado", "gravado": "11"},
    ]
    assert r["backup"] and r["ambienteId"] == env.id and r["ramal"] == "1001"
    linha = _linha(db, env)
    assert (linha.nome_visivel, linha.numero_abreviado, linha.ip) == ("Caixa 1", "11", "10.0.0.11")
    assert aparelho.chamadas == []


async def test_linha_ja_aplicada_fica_desatualizada(db, aparelho) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    linha = _linha(db, env)
    linha.ultimo_status, linha.ultimo_hash_aplicado = "ok", compute_line_hash(linha.environment, linha)
    db.commit()
    pronta = await _processar(_editar_linha(env, [{"campo": "nomeVisivel", "de": "", "para": "Balcão"}]))
    assert pronta.corpo["resultado"]["status"] == "outdated"


async def test_de_diferente_do_atual_recusa_com_o_valor_de_hoje_e_nao_faz_backup(
    db, aparelho, monkeypatch
) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    linha = _linha(db, env)
    linha.nome_visivel = "Mudado na loja"
    db.commit()
    backups: list[str] = []
    monkeypatch.setattr(snapshot, "create_snapshot", lambda **k: backups.append("x"))

    pronta = await _processar(_editar_linha(env, [{"campo": "nomeVisivel", "de": "", "para": "Do NOC"}]))
    assert pronta.corpo["naoSuportado"] is True
    assert pronta.corpo["resultado"] == {
        "recusa": "DE_DIVERGENTE",
        "atuais": [{"campo": "nomeVisivel", "atual": "Mudado na loja"}],
    }
    assert _linha(db, env).nome_visivel == "Mudado na loja"
    assert backups == []


async def test_valor_que_o_fabricante_nao_aceita_e_recusado_antes_do_backup(db, aparelho) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"), modelo="Intelbras TIP 125i")
    pronta = await _processar(_editar_linha(env, [{"campo": "nomeVisivel", "de": "", "para": "Caixa;1"}]))
    assert pronta.corpo["resultado"]["recusa"] == "VALOR_INVALIDO"
    assert "';'" in pronta.corpo["erro"]
    assert _linha(db, env).nome_visivel == ""


async def test_ambiente_e_ramal_que_nao_existem(db, aparelho) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    sem_ambiente = await _processar(
        _tarefa(
            "x1",
            tipo=LINHA,
            raio=ESCRITA,
            pedido={
                "ambienteId": "nao-existe",
                "ramal": "1001",
                "campos": [{"campo": "nomeVisivel", "de": "", "para": "a"}],
            },
        )
    )
    assert sem_ambiente.corpo["resultado"]["recusa"] == "AMBIENTE_NAO_ENCONTRADO"
    sem_ramal = await _processar(
        _editar_linha(env, [{"campo": "nomeVisivel", "de": "", "para": "a"}], ramal="9999", id_="x2")
    )
    assert sem_ramal.corpo["resultado"]["recusa"] == "RAMAL_NAO_ENCONTRADO"


async def test_escrita_sem_backup_nao_grava(db, aparelho, monkeypatch) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))

    def quebra(**_):
        raise snapshot.SnapshotError("disco cheio")

    monkeypatch.setattr(snapshot, "create_snapshot", quebra)
    pronta = await _processar(_editar_linha(env, [{"campo": "nomeVisivel", "de": "", "para": "a"}]))
    assert pronta.corpo["ok"] is False and "Backup obrigatório" in pronta.corpo["erro"]
    assert _linha(db, env).nome_visivel == ""


async def test_edicao_repetida_nao_grava_duas_vezes(db, aparelho) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    tarefa = _editar_linha(env, [{"campo": "nomeVisivel", "de": "", "para": "Primeira"}])
    assert (await _processar(tarefa)).corpo["ok"] is True
    linha = _linha(db, env)
    linha.nome_visivel = "Mudado depois"
    db.commit()
    # Reentrega da mesma tarefa: devolve o gravado e não sobrescreve a mudança local.
    segunda = await _processar(tarefa)
    assert segunda.corpo["resultado"]["campos"] == [{"campo": "nomeVisivel", "gravado": "Primeira"}]
    assert _linha(db, env).nome_visivel == "Mudado depois"


# --- A config padrão -----------------------------------------------------------------------------


def _editar_config(env, campos, id_="c1") -> dict:
    return _tarefa(id_, tipo=CONFIG, raio=ESCRITA, pedido={"ambienteId": env.id, "campos": campos})


async def test_editar_config_grava_tudo_e_conta_as_linhas_desatualizadas(db, aparelho) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"), ("1002", "10.0.0.12"))
    for linha in db.get(ExtensionEnvironment, env.id).lines:
        linha.ultimo_status, linha.ultimo_hash_aplicado = "ok", compute_line_hash(linha.environment, linha)
    db.commit()
    atual = repo.merged_config_padrao(env)["register_expiration"]
    pronta = await _processar(
        _editar_config(
            env,
            [
                {"chave": "register_expiration", "de": atual, "para": atual + 60},
                {"chave": "validar_conectividade", "de": False, "para": True},
            ],
        )
    )
    assert pronta.corpo["ok"] is True, pronta.corpo
    r = pronta.corpo["resultado"]
    assert r["campos"] == [
        {"chave": "register_expiration", "gravado": atual + 60},
        {"chave": "validar_conectividade", "gravado": True},
    ]
    assert r["linhasDesatualizadas"] == 2 and r["backup"]
    db.expire_all()
    assert (
        repo.merged_config_padrao(db.get(ExtensionEnvironment, env.id))["register_expiration"] == atual + 60
    )
    assert aparelho.chamadas == []


async def test_config_um_de_divergente_recusa_o_pedido_inteiro(db, aparelho) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    atual = repo.merged_config_padrao(env)["register_expiration"]
    pronta = await _processar(
        _editar_config(
            env,
            [
                {"chave": "register_expiration", "de": atual, "para": atual + 1},
                {"chave": "validar_conectividade", "de": True, "para": True},
            ],
        )
    )
    assert pronta.corpo["resultado"]["recusa"] == "DE_DIVERGENTE"
    assert {"chave": "validar_conectividade", "atual": False} in pronta.corpo["resultado"]["atuais"]
    db.expire_all()
    assert repo.merged_config_padrao(db.get(ExtensionEnvironment, env.id))["register_expiration"] == atual


async def test_config_com_tipo_trocado_ou_valor_que_o_fabricante_recusa(db, aparelho) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"), modelo="Intelbras TIP 125i")
    cfg = repo.merged_config_padrao(env)
    tipo = await _processar(
        _editar_config(
            env, [{"chave": "register_expiration", "de": cfg["register_expiration"], "para": "120"}]
        )
    )
    assert tipo.corpo["resultado"]["recusa"] == "VALOR_INVALIDO"
    # `True` não é `1`: interruptor continua interruptor.
    assert (
        await _processar(
            _editar_config(env, [{"chave": "validar_conectividade", "de": 0, "para": True}], id_="c2")
        )
    ).corpo["resultado"]["recusa"] == "DE_DIVERGENTE"
    # Hotline ligada sem número: o TIP não recebe, e a config não muda.
    hotline = await _processar(
        _editar_config(env, [{"chave": "hotline_enable", "de": 0, "para": 1}], id_="c3")
    )
    assert hotline.corpo["resultado"]["recusa"] == "VALOR_INVALIDO", hotline.corpo
    assert "hotline" in hotline.corpo["erro"]
    db.expire_all()
    assert repo.merged_config_padrao(db.get(ExtensionEnvironment, env.id))["hotline_enable"] == 0
