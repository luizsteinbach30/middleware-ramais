"""Edição central (item 13, etapa I5 do NOC; ADR 0012) — ``docs/AGENTE-NOC.md`` e
``noc-workconnect/docs/CONTRATO-DO-AGENTE.md`` §10.

O que se prova aqui, cada garantia com o seu teste:

- a planilha inteira se edita pelo NOC — IP, ramal, usuário, senha SIP, servidor, nº abreviado e
  nome —, com linha nova, remoção e ordem nova, e a linha que veio de outra mantém o id;
- a planilha que o NOC viu (``de``) é conferida inteira: diferente, nada é gravado e volta a atual,
  **sem a senha**; o resultado também não leva senha;
- a validação roda antes (IP que é IP, o ``;`` do TIP 125i), e campo fora dos sete é recusado;
- backup antes, gravação na planilha e **nunca no aparelho**;
- na config padrão, a lista branca continua daqui e credencial não se edita;
- reaplicar acha a linha pela posição e não exige aparelho vinculado.
"""

from __future__ import annotations

import json

import pytest

from middleware_monitor.core.models import ExtensionEnvironment
from middleware_monitor.domain.backup import snapshot
from middleware_monitor.domain.extension_configurator import repository as repo
from middleware_monitor.domain.extension_configurator.service import compute_line_hash
from middleware_monitor.domain.noc import executor
from tests.api.test_noc_tarefas import _ambiente, _AparelhoFalso, _limpo, _processar, _tarefa  # noqa: F401

PLANILHA = "editar_planilha_do_ambiente"
CONFIG = "editar_config_do_ambiente"
ESCRITA = "ESCRITA_REVERSIVEL"
CAMPOS = ("ramal", "ip", "userAuth", "senhaSip", "servidorSip", "numeroAbreviado", "nomeVisivel")


@pytest.fixture
def aparelho(monkeypatch) -> _AparelhoFalso:
    """Qualquer caminho até o telefone quebra o teste: a edição grava só na planilha."""
    falso = _AparelhoFalso()
    monkeypatch.setattr("middleware_monitor.domain.extension_configurator.actions.run_action_on_line", falso)

    async def proibido(*_a, **_k):
        raise AssertionError("a edição central não aplica config no aparelho")

    monkeypatch.setattr("middleware_monitor.domain.extension_configurator.apply.run_apply", proibido)
    return falso


def _vista(db, env) -> list[dict]:
    """A planilha como o retrato a mostra ao NOC — é o ``de`` que a tela mandaria."""
    db.expire_all()
    return executor._planilha_atual(db.get(ExtensionEnvironment, env.id))


def _para(de: list[dict]) -> list[dict]:
    return [{"origem": ln["posicao"], **{c: ln[c] for c in CAMPOS}} for ln in de]


def _editar(env, de, para, id_="p1") -> dict:
    return _tarefa(id_, tipo=PLANILHA, raio=ESCRITA, pedido={"ambienteId": env.id, "de": de, "para": para})


def _linhas(db, env):
    db.expire_all()
    return sorted(db.get(ExtensionEnvironment, env.id).lines, key=lambda ln: ln.posicao)


# --- A forma ------------------------------------------------------------------------------------


def test_os_verbos_sao_escrita_e_a_linha_por_campo_saiu() -> None:
    assert executor.ACOES[PLANILHA].raio == ESCRITA
    assert executor.ACOES[CONFIG].raio == ESCRITA
    assert "editar_linha_do_ambiente" not in executor.ACOES
    faltando = executor.conferir(PLANILHA, ESCRITA, {"ambienteId": "x", "de": []})
    assert faltando is not None and "precisa de: para" in (faltando.erro or "")
    rede = executor.conferir(CONFIG, ESCRITA, {"ambienteId": "x", "campos": [], "gateway": "1"})
    assert rede is not None and "P_CODE_DE_REDE" in (rede.erro or "")


@pytest.mark.parametrize("extra", ["deviceId", "status", "posicao", "vlan"])
async def test_campo_fora_dos_sete_e_recusado_sem_gravar(db, aparelho, extra) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    de = _vista(db, env)
    para = [{**_para(de)[0], extra: "x"}]
    pronta = await _processar(_editar(env, de, para))
    assert pronta.corpo["naoSuportado"] is True
    assert pronta.corpo["resultado"]["recusa"] == "CAMPO_NAO_PERMITIDO"
    assert _linhas(db, env)[0].ip == "10.0.0.11"
    assert aparelho.chamadas == []


async def test_duas_linhas_da_mesma_origem_e_recusa(db, aparelho) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    de = _vista(db, env)
    pronta = await _processar(_editar(env, de, _para(de) + _para(de)))
    assert pronta.corpo["resultado"]["recusa"] == "ORIGEM_REPETIDA"


# --- A planilha ---------------------------------------------------------------------------------


async def test_edita_ip_e_credenciais_cria_remove_e_reordena_mantendo_o_id(db, aparelho) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"), ("1002", "10.0.0.12"), ("1003", "10.0.0.13"))
    ids = [ln.id for ln in _linhas(db, env)]
    de = _vista(db, env)
    para = [
        # a 3 vem para o topo, com IP, usuário e senha novos
        {
            **_para(de)[2],
            "ip": "10.0.0.33",
            "userAuth": "u1003",
            "senhaSip": "nova-senha",
            "nomeVisivel": "Balcão",
        },
        # a 1 fica como está
        _para(de)[0],
        # a 2 some; nasce uma linha nova no fim
        {
            "origem": None,
            "ramal": "1004",
            "ip": "10.0.0.14",
            "userAuth": "",
            "senhaSip": "s4",
            "servidorSip": "",
            "numeroAbreviado": "",
            "nomeVisivel": "Nova",
        },
    ]
    pronta = await _processar(_editar(env, de, para))
    assert pronta.corpo["ok"] is True, pronta.corpo
    r = pronta.corpo["resultado"]
    assert (r["criadas"], r["removidas"], r["alteradas"]) == (1, 1, 1)
    assert [(ln["posicao"], ln["ramal"], ln["ip"]) for ln in r["linhas"]] == [
        (1, "1003", "10.0.0.33"),
        (2, "1001", "10.0.0.11"),
        (3, "1004", "10.0.0.14"),
    ]
    assert "nova-senha" not in json.dumps(pronta.corpo) and "senhaSip" not in json.dumps(r)
    linhas = _linhas(db, env)
    assert [ln.id for ln in linhas[:2]] == [ids[2], ids[0]]  # quem veio de outra manteve o id
    assert ids[1] not in [ln.id for ln in linhas]
    assert (linhas[0].user_auth, repo.senha_sip_de(linhas[0])) == ("u1003", "nova-senha")
    assert aparelho.chamadas == []


async def test_linha_ja_aplicada_fica_desatualizada(db, aparelho) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    linha = _linhas(db, env)[0]
    linha.ultimo_status, linha.ultimo_hash_aplicado = "ok", compute_line_hash(linha.environment, linha)
    db.commit()
    de = _vista(db, env)
    pronta = await _processar(_editar(env, de, [{**_para(de)[0], "nomeVisivel": "Balcão"}]))
    assert pronta.corpo["resultado"]["linhas"][0]["status"] == "outdated"


async def test_planilha_que_mudou_na_loja_recusa_sem_senha_e_sem_backup(db, aparelho, monkeypatch) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    de = _vista(db, env)
    linha = _linhas(db, env)[0]
    linha.nome_visivel = "Mudado na loja"
    repo.save_lines(
        db,
        linha.environment,
        [
            {
                "id": linha.id,
                "ip": linha.ip,
                "numero_ramal": "1001",
                "senha_sip": "segredo-da-loja",
                "nome_visivel": "Mudado na loja",
            }
        ],
    )
    db.commit()
    backups: list[str] = []
    monkeypatch.setattr(snapshot, "create_snapshot", lambda **k: backups.append("x"))

    pronta = await _processar(_editar(env, de, [{**_para(de)[0], "nomeVisivel": "Do NOC"}]))
    assert pronta.corpo["naoSuportado"] is True
    r = pronta.corpo["resultado"]
    assert r["recusa"] == "DE_DIVERGENTE"
    assert r["atual"][0]["nomeVisivel"] == "Mudado na loja"
    assert r["atual"][0]["senhaSip"] == {"definida": True}
    assert "segredo-da-loja" not in json.dumps(pronta.corpo)
    assert _linhas(db, env)[0].nome_visivel == "Mudado na loja"
    assert backups == []


@pytest.mark.parametrize(
    ("mudanca", "modelo", "trecho"),
    [
        ({"ip": "10.0.0.999"}, "HTEK UC902G", "não é um IP"),
        ({"ramal": ""}, "HTEK UC902G", "ramal é obrigatório"),
        ({"nomeVisivel": "Caixa;1"}, "Intelbras TIP 125i", "';'"),
    ],
)
async def test_valor_invalido_e_recusado_antes_do_backup(db, aparelho, mudanca, modelo, trecho) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"), modelo=modelo)
    de = _vista(db, env)
    pronta = await _processar(_editar(env, de, [{**_para(de)[0], **mudanca}]))
    assert pronta.corpo["resultado"]["recusa"] == "VALOR_INVALIDO", pronta.corpo
    assert pronta.corpo["resultado"]["posicao"] == 1
    assert trecho in pronta.corpo["erro"]
    assert _linhas(db, env)[0].ip == "10.0.0.11"


async def test_ambiente_que_nao_existe(db, aparelho) -> None:
    pronta = await _processar(
        _tarefa("x1", tipo=PLANILHA, raio=ESCRITA, pedido={"ambienteId": "nao-existe", "de": [], "para": []})
    )
    assert pronta.corpo["resultado"]["recusa"] == "AMBIENTE_NAO_ENCONTRADO"


async def test_escrita_sem_backup_nao_grava(db, aparelho, monkeypatch) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))

    def quebra(**_):
        raise snapshot.SnapshotError("disco cheio")

    monkeypatch.setattr(snapshot, "create_snapshot", quebra)
    de = _vista(db, env)
    pronta = await _processar(_editar(env, de, [{**_para(de)[0], "nomeVisivel": "a"}]))
    assert pronta.corpo["ok"] is False and "Backup obrigatório" in pronta.corpo["erro"]
    assert _linhas(db, env)[0].nome_visivel == ""


async def test_edicao_repetida_nao_grava_duas_vezes(db, aparelho) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    de = _vista(db, env)
    tarefa = _editar(env, de, [{**_para(de)[0], "nomeVisivel": "Primeira"}])
    assert (await _processar(tarefa)).corpo["ok"] is True
    linha = _linhas(db, env)[0]
    linha.nome_visivel = "Mudado depois"
    db.commit()
    await _processar(tarefa)
    assert _linhas(db, env)[0].nome_visivel == "Mudado depois"


# --- Reaplicar pela posição ------------------------------------------------------------------------


async def test_reaplicar_pela_posicao_sem_aparelho_vinculado(db, monkeypatch) -> None:
    enviados: list[str] = []

    async def envio_falso(adapter, ip, chain, cfg_bytes):
        enviados.append(ip)

    monkeypatch.setattr(
        "middleware_monitor.domain.extension_configurator.apply._send_config_with_fallback", envio_falso
    )
    env = _ambiente(db, ("1001", "10.0.0.11"), ("1002", "10.0.0.12"))
    assert all(ln.device_id is None for ln in _linhas(db, env))  # nenhuma linha tem aparelho
    pronta = await _processar(
        _tarefa(
            "r1",
            tipo="reaplicar_config_do_ambiente",
            raio=ESCRITA,
            pedido={"ambienteId": env.id, "posicao": 2},
        )
    )
    assert enviados == ["10.0.0.12"]
    assert pronta.corpo["ok"] is True, pronta.corpo
    assert pronta.corpo["resultado"]["ramal"] == "1002"


async def test_reaplicar_posicao_que_nao_existe_ou_forma_misturada(db) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    fora = await _processar(
        _tarefa(
            "r2",
            tipo="reaplicar_config_do_ambiente",
            raio=ESCRITA,
            pedido={"ambienteId": env.id, "posicao": 9},
        )
    )
    assert fora.corpo["resultado"]["recusa"] == "LINHA_NAO_ENCONTRADA"
    misturado = await _processar(
        _tarefa(
            "r3",
            tipo="reaplicar_config_do_ambiente",
            raio=ESCRITA,
            pedido={"ambienteId": env.id, "posicao": 1, "ramal": "1001"},
        )
    )
    assert misturado.corpo["naoSuportado"] is True


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


@pytest.mark.parametrize(
    "chave", ["sip_server", "web_password", "menu_password", "web_user", "sip_transport", "ip"]
)
async def test_chave_da_config_fora_da_lista_e_recusada(db, aparelho, chave) -> None:
    """As credenciais da config padrão continuam só "definida" (ADR 0012 mudou a planilha, não a config)."""
    env = _ambiente(db, ("1001", "10.0.0.11"))
    antes = env.config_padrao
    pedido = {"ambienteId": env.id, "campos": [{"chave": chave, "de": None, "para": "x"}]}
    pronta = await _processar(_tarefa("c9", tipo=CONFIG, raio=ESCRITA, pedido=pedido))
    assert pronta.corpo["resultado"]["recusa"] == "CAMPO_NAO_PERMITIDO"
    db.expire_all()
    assert db.get(ExtensionEnvironment, env.id).config_padrao == antes


async def test_o_numero_da_linha_do_retrato_e_o_que_o_executor_aceita(db, aparelho) -> None:
    """A planilha do middleware grava ``posicao`` a partir de 0; o retrato e o executor falam 1..N."""
    from middleware_monitor.domain.noc import retrato as rt

    env = _ambiente(db, ("1001", "10.0.0.11"), ("1002", "10.0.0.12"))
    assert [ln.posicao for ln in _linhas(db, env)] == [0, 1]
    [amb] = rt.ambientes(db)
    de = [{"posicao": ln["posicao"], **{c: ln[c] for c in CAMPOS}} for ln in amb["linhas"]]
    assert [ln["posicao"] for ln in de] == [1, 2]
    pronta = await _processar(_editar(env, de, [{**_para(de)[1], "nomeVisivel": "Segunda"}, _para(de)[0]]))
    assert pronta.corpo["ok"] is True, pronta.corpo
    assert [ln.numero_ramal for ln in _linhas(db, env)] == ["1002", "1001"]
