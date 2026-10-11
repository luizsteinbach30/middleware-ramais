"""As abas DVR e Câmeras do ambiente: a planilha local que vira a lista de equipamentos.

O NOC define a lista inteira (``definir_equipamentos``); a tela do ambiente edita
**só os gravadores e as câmeras daquele ambiente**, e o resto da lista passa
intacto. O caminho é o mesmo do NOC — ``lista.validar_para`` e ``lista.gravar`` —
para não haver duas regras do que é um equipamento válido.

As regras que não se re-derivam lendo o código:

- **Nada aqui fala com o DVR nem com a câmera.** A planilha grava o cadastro; quem
  mede é o monitor de equipamentos, pelas verificações.
- **A linha administra três verificações, e só elas**: ``icmp`` (sempre), ``cftv``
  (o adaptador e a porta HTTP do gravador) e ``tcp`` de ref ``rtsp`` (a porta RTSP;
  vazia = sem). Verificação que o NOC pôs a mais no equipamento fica como está.
- **A senha nunca volta.** A linha diz só se há uma guardada. Senha em branco
  mantém a guardada; trocar o usuário sem senha nova reaproveita a guardada.
- **Ref local é ``mw_<hex>``.** Linha nova pode chegar com o ref que a tela gerou
  (é por ele que a câmera aponta para um DVR que nasce no mesmo salvamento).
- **Aba ausente no pedido não mexe** nos equipamentos daquele tipo — é a aba do
  serviço desligado, escondida na tela.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import selectinload

from middleware_monitor.core.models import Equipamento, ExtensionEnvironment
from middleware_monitor.domain.equipamentos import lista, modulos

ABAS: dict[str, str] = {"dvr": "gravador", "camera": "camera"}
COLUNAS_DVR = ("nome", "ip", "portaHttp", "portaRtsp", "adaptador", "modelo", "usuario", "senha", "canais")
COLUNAS_CAMERA = ("nome", "ip", "dvr", "canal", "fabricante", "modelo")
PORTA_RTSP_PADRAO = 554
REF_RTSP = "rtsp"
_REF_LOCAL = re.compile(r"^mw_[0-9a-f]{6,32}$")
_ROTULO_DA_ABA = {"dvr": "DVR", "camera": "Câmeras"}


class LinhaInvalida(ValueError):
    """Uma linha da planilha que não vira equipamento. ``aba`` e ``linha`` (1..N)
    dizem onde; ``None`` quando o problema é do pedido inteiro."""

    def __init__(self, motivo: str, *, aba: str | None = None, linha: int | None = None) -> None:
        onde = f"{_ROTULO_DA_ABA.get(aba or '', aba)}, linha {linha}: " if aba and linha else ""
        super().__init__(onde + motivo)
        self.motivo = motivo
        self.aba = aba
        self.linha = linha

    def como_dict(self) -> dict[str, Any]:
        return {"mensagem": str(self), "aba": self.aba, "linha": self.linha}


def novo_ref() -> str:
    return f"mw_{secrets.token_hex(6)}"


def adaptadores_aceitos(db: DBSession) -> list[str]:
    """Os adaptadores de CFTV que este agente aceita (``cftv.<adaptador>``), na ordem do catálogo."""
    aceitos = modulos.aceitos(db)
    return [a for a in lista.ADAPTADORES_DE_CFTV if f"cftv.{a}" in aceitos]


# --- Leitura -----------------------------------------------------------------------------


def _do_ambiente(db: DBSession, env_id: str) -> list[Equipamento]:
    return list(
        db.scalars(
            select(Equipamento)
            .where(Equipamento.ambiente_id == env_id, Equipamento.tipo.in_(tuple(ABAS.values())))
            .options(selectinload(Equipamento.verificacoes), selectinload(Equipamento.credenciais))
            .order_by(Equipamento.criado_em, Equipamento.nome, Equipamento.id)
        ).all()
    )


def _verificacao(eq: Equipamento, tipo: str, ref: str | None = None) -> Any:
    for vf in eq.verificacoes:
        if vf.tipo == tipo and (ref is None or vf.ref == ref):
            return vf
    return None


def linhas(db: DBSession, env_id: str) -> dict[str, list[dict[str, Any]]]:
    """As duas abas, na ordem em que os equipamentos nasceram (e por nome, entre os do
    mesmo salvamento). Sem segredo."""
    leituras = {}
    for eq_id, leitura in lista._leituras_de_cftv(db).items():
        canais = leitura.get("canais")
        leituras[eq_id] = len(canais) if isinstance(canais, list) else None
    eqs = _do_ambiente(db, env_id)
    refs = {eq.id: eq.ref for eq in eqs}
    dvr: list[dict[str, Any]] = []
    camera: list[dict[str, Any]] = []
    for eq in eqs:
        base = {"id": eq.id, "ref": eq.ref, "nome": eq.nome, "ip": eq.host, "modelo": eq.modelo or ""}
        estado = {"estado": eq.estado, "estadoDesde": lista._hora(eq.estado_desde)}
        if eq.tipo == "gravador":
            cftv = _verificacao(eq, "cftv")
            rtsp = _verificacao(eq, "tcp", REF_RTSP)
            parametros = lista.parametros_de(cftv) if cftv is not None else {}
            http = next((c for c in eq.credenciais if c.tipo == "HTTP"), None)
            dvr.append(
                {
                    **base,
                    "portaHttp": parametros.get("porta"),
                    "portaRtsp": lista.parametros_de(rtsp).get("porta") if rtsp is not None else None,
                    "adaptador": parametros.get("adaptador") or "",
                    "usuario": http.usuario if http is not None else "",
                    "senha": "",
                    "senhaGuardada": http is not None,
                    "canais": eq.canais,
                    "canaisLidos": leituras.get(eq.id),
                    "verificacaoCftv": cftv.estado if cftv is not None else None,
                    **estado,
                }
            )
        else:
            camera.append(
                {
                    **base,
                    "dvr": refs.get(eq.pai_id or "", ""),
                    "canal": eq.canal,
                    "fabricante": eq.fabricante or "",
                    **estado,
                }
            )
    return {"dvr": dvr, "camera": camera}


def contagens(db: DBSession) -> dict[str, dict[str, int]]:
    """``{ambiente_id: {"dvr": n, "camera": n}}`` — para a lista de ambientes."""
    saida: dict[str, dict[str, int]] = {}
    for amb, tipo in db.execute(
        select(Equipamento.ambiente_id, Equipamento.tipo).where(
            Equipamento.ambiente_id.is_not(None), Equipamento.tipo.in_(tuple(ABAS.values()))
        )
    ).all():
        if amb is None:
            continue
        aba = "dvr" if tipo == "gravador" else "camera"
        conta = saida.setdefault(amb, {"dvr": 0, "camera": 0})
        conta[aba] += 1
    return saida


# --- Escrita -----------------------------------------------------------------------------


def _texto(
    linha: dict[str, Any], campo: str, limite: int, *, aba: str, n: int, obrigatorio: bool = False
) -> str | None:
    valor = linha.get(campo)
    valor = "" if valor is None else valor
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        valor = str(valor)
    if not isinstance(valor, str):
        raise LinhaInvalida(f"{campo} é texto.", aba=aba, linha=n)
    valor = valor.strip()
    if not valor:
        if obrigatorio:
            raise LinhaInvalida(f"{campo} é obrigatório.", aba=aba, linha=n)
        return None
    if len(valor) > limite:
        raise LinhaInvalida(f"{campo}: até {limite} caracteres.", aba=aba, linha=n)
    return valor


def _numero(linha: dict[str, Any], campo: str, minimo: int, maximo: int, *, aba: str, n: int) -> int | None:
    valor = linha.get(campo)
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    if isinstance(valor, str) and valor.strip().isdigit():
        valor = int(valor.strip())
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    if not isinstance(valor, int) or isinstance(valor, bool) or not minimo <= valor <= maximo:
        raise LinhaInvalida(f"{campo} é um número de {minimo} a {maximo}.", aba=aba, linha=n)
    return valor


def _para_de_vista(item: dict[str, Any], ref_por_id: dict[str, str]) -> dict[str, Any]:
    """Um item da vista de volta na forma do ``para`` — sem os campos opcionais e sem
    credenciais: o que não vai no pedido fica como está."""
    return {
        "id": item["id"],
        "ref": item["ref"],
        "tipo": item["tipo"],
        "nome": item["nome"],
        "host": item["host"],
        "mac": item["mac"],
        "fabricante": item["fabricante"],
        "modelo": item["modelo"],
        "paiRef": ref_por_id.get(item["paiId"]) if item["paiId"] else None,
        "tentativasParaCair": item["tentativasParaCair"],
        "verificacoes": [dict(v) for v in item["verificacoes"]],
    }


def _ref_livre(desejado: str, ocupados: set[str]) -> str:
    ref, i = desejado, 2
    while ref in ocupados:
        ref, i = f"{desejado}-{i}", i + 1
    return ref


def _trocar_verificacao(
    verificacoes: list[dict[str, Any]], tipo: str, ref: str | None, nova: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Troca a primeira verificação ``tipo`` (de ``ref``, se dado) por ``nova`` — mantendo
    o ``id`` e o intervalo —, acrescenta se não havia, e tira se ``nova`` é ``None``."""
    alvo = next(
        (i for i, v in enumerate(verificacoes) if v["tipo"] == tipo and (ref is None or v["ref"] == ref)),
        None,
    )
    if nova is None:
        return [v for i, v in enumerate(verificacoes) if i != alvo]
    if alvo is not None:
        antes = verificacoes[alvo]
        verificacoes[alvo] = {**antes, "parametros": nova["parametros"]}
        return verificacoes
    ocupados = {v["ref"] for v in verificacoes}
    return [
        *verificacoes,
        {
            "id": None,
            "ref": _ref_livre(nova["ref"], ocupados),
            "tipo": tipo,
            "parametros": nova["parametros"],
            "intervaloS": lista.INTERVALO_PADRAO_S,
        },
    ]


def _credenciais_do_dvr(
    eq: Equipamento | None, usuario: str, senha: str, *, n: int
) -> list[dict[str, str]] | None:
    """``None`` = manter as guardadas. A lista inteira, quando muda: as outras
    credenciais do equipamento (do NOC) seguem decifradas para não se perderem."""
    guardada = next((c for c in eq.credenciais if c.tipo == "HTTP"), None) if eq is not None else None
    if not senha:
        if guardada is None:
            if usuario:
                raise LinhaInvalida("informe a senha do DVR.", aba="dvr", linha=n)
            return None
        if usuario in ("", guardada.usuario):
            return None  # as duas em branco também mantêm: a senha nunca vem preenchida
    try:
        outras = []
        if eq is not None:
            for c in eq.credenciais:
                if c.tipo == "HTTP":
                    continue
                aberta = lista.credencial_de(eq, c.tipo)
                if aberta is not None:
                    outras.append({"tipo": c.tipo, "usuario": aberta[0], "segredo": aberta[1]})
        if not senha:
            assert eq is not None
            aberta = lista.credencial_de(eq, "HTTP")
            senha = aberta[1] if aberta is not None else ""
    except ValueError as exc:
        raise LinhaInvalida(
            "a senha guardada não abre com a chave desta instalação; digite a senha de novo.",
            aba="dvr",
            linha=n,
        ) from exc
    return [*outras, {"tipo": "HTTP", "usuario": usuario, "segredo": senha}]


@dataclass
class _Origem:
    aba: str
    linha: int


def montar_para(
    db: DBSession, env: ExtensionEnvironment, pedido: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[_Origem | None]]:
    """A lista inteira desejada (forma do ``para``) e, por índice, de que linha da
    planilha veio cada item (``None`` = equipamento fora da planilha)."""
    atual = lista.vista(db)
    ref_por_id = {item["id"]: item["ref"] for item in atual}
    eq_por_id = {eq.id: eq for eq in _do_ambiente(db, env.id)}
    abas = {aba: pedido.get(aba) for aba in ABAS if pedido.get(aba) is not None}
    for aba, linhas_da_aba in abas.items():
        if not isinstance(linhas_da_aba, list):
            raise LinhaInvalida(f"{aba} é uma lista de linhas.")
    tipos_trocados = {ABAS[aba] for aba in abas}

    para: list[dict[str, Any]] = []
    origens: list[_Origem | None] = []
    for item in atual:
        if item["ambiente"] == env.id and item["tipo"] in tipos_trocados:
            continue
        para.append(_para_de_vista(item, ref_por_id))
        origens.append(None)

    refs_usados = {p["ref"] for p in para}
    vista_por_id = {item["id"]: item for item in atual}
    aceitos = set(adaptadores_aceitos(db))

    def ref_da_linha(linha: dict[str, Any], eq: Equipamento | None, aba: str, n: int) -> str:
        if eq is not None:
            ref = eq.ref
        else:
            pedido_ref = linha.get("ref")
            ref = pedido_ref if isinstance(pedido_ref, str) and _REF_LOCAL.match(pedido_ref) else novo_ref()
        if ref in refs_usados:
            raise LinhaInvalida(f"o ref {ref!r} já é de outro equipamento.", aba=aba, linha=n)
        refs_usados.add(ref)
        return ref

    def equipamento_da_linha(linha: dict[str, Any], tipo: str, aba: str, n: int) -> Equipamento | None:
        eid = linha.get("id")
        if eid in (None, ""):
            return None
        eq = eq_por_id.get(eid) if isinstance(eid, str) else None
        if eq is None or eq.tipo != tipo:
            raise LinhaInvalida(
                "a linha aponta para um equipamento que não é deste ambiente.", aba=aba, linha=n
            )
        return eq

    # DVR primeiro: a câmera aponta para o ref dele.
    refs_de_dvr: set[str] = {
        p["ref"]
        for p in para
        if p["tipo"] == "gravador" and vista_por_id.get(p["id"] or "", {}).get("ambiente") == env.id
    }
    vistos: set[str] = set()
    for n, linha in enumerate(abas.get("dvr") or [], start=1):
        if not isinstance(linha, dict):
            raise LinhaInvalida("cada linha é um objeto.", aba="dvr", linha=n)
        eq = equipamento_da_linha(linha, "gravador", "dvr", n)
        if eq is not None and eq.id in vistos:
            raise LinhaInvalida("o mesmo DVR aparece duas vezes.", aba="dvr", linha=n)
        if eq is not None:
            vistos.add(eq.id)
        nome = _texto(linha, "nome", 128, aba="dvr", n=n, obrigatorio=True)
        ip = _texto(linha, "ip", 255, aba="dvr", n=n, obrigatorio=True)
        porta_http = _numero(linha, "portaHttp", 1, 65535, aba="dvr", n=n)
        porta_rtsp = _numero(linha, "portaRtsp", 1, 65535, aba="dvr", n=n)
        canais = _numero(linha, "canais", 1, lista.MAXIMO_DE_CANAIS, aba="dvr", n=n)
        adaptador = _texto(linha, "adaptador", 32, aba="dvr", n=n, obrigatorio=True) or ""
        antes = vista_por_id.get(eq.id) if eq is not None else None
        cftv_antes = next((v for v in (antes or {}).get("verificacoes", []) if v["tipo"] == "cftv"), None)
        mesmo_adaptador = cftv_antes is not None and cftv_antes["parametros"].get("adaptador") == adaptador
        if adaptador not in lista.ADAPTADORES_DE_CFTV:
            raise LinhaInvalida(f"adaptador é {', '.join(lista.ADAPTADORES_DE_CFTV)}.", aba="dvr", linha=n)
        if adaptador not in aceitos and not mesmo_adaptador:
            raise LinhaInvalida(
                f"o módulo cftv.{adaptador} não está aceito neste agente (Sistema → Módulos).",
                aba="dvr",
                linha=n,
            )
        usuario = _texto(linha, "usuario", lista.LIMITE_DO_USUARIO, aba="dvr", n=n) or ""
        senha = linha.get("senha") or ""
        if not isinstance(senha, str) or len(senha) > lista.LIMITE_DO_SEGREDO:
            raise LinhaInvalida(f"senha: até {lista.LIMITE_DO_SEGREDO} caracteres.", aba="dvr", linha=n)
        ref = ref_da_linha(linha, eq, "dvr", n)
        refs_de_dvr.add(ref)

        verificacoes = [dict(v) for v in (antes or {}).get("verificacoes", [])]
        if not any(v["tipo"] == "icmp" for v in verificacoes):
            verificacoes = _trocar_verificacao(verificacoes, "icmp", None, {"ref": "icmp", "parametros": {}})
        esquema = cftv_antes["parametros"].get("esquema") if mesmo_adaptador and cftv_antes else None
        parametros_cftv: dict[str, Any] = {"adaptador": adaptador}
        if esquema:
            parametros_cftv["esquema"] = esquema
        if porta_http is not None:
            parametros_cftv["porta"] = porta_http
        try:
            parametros_cftv = lista.normalizar_parametros("cftv", parametros_cftv)
        except ValueError as exc:
            raise LinhaInvalida(str(exc), aba="dvr", linha=n) from exc
        verificacoes = _trocar_verificacao(
            verificacoes, "cftv", None, {"ref": "cftv", "parametros": parametros_cftv}
        )
        verificacoes = _trocar_verificacao(
            verificacoes,
            "tcp",
            REF_RTSP,
            None if porta_rtsp is None else {"ref": REF_RTSP, "parametros": {"porta": porta_rtsp}},
        )
        do_dvr: dict[str, Any] = {
            "id": eq.id if eq is not None else None,
            "ref": ref,
            "tipo": "gravador",
            "nome": nome,
            "host": ip,
            "mac": antes["mac"] if antes else None,
            "fabricante": antes["fabricante"] if antes else None,
            "modelo": _texto(linha, "modelo", 64, aba="dvr", n=n),
            "paiRef": ref_por_id.get(antes["paiId"]) if antes and antes["paiId"] else None,
            "tentativasParaCair": antes["tentativasParaCair"] if antes else lista.TENTATIVAS_PADRAO,
            "verificacoes": verificacoes,
            "ambiente": env.id,
            "canais": canais,
            "canal": None,
        }
        credenciais = _credenciais_do_dvr(eq, usuario, senha, n=n)
        if credenciais is not None:
            do_dvr["credenciais"] = credenciais
        para.append(do_dvr)
        origens.append(_Origem("dvr", n))

    vistos = set()
    for n, linha in enumerate(abas.get("camera") or [], start=1):
        if not isinstance(linha, dict):
            raise LinhaInvalida("cada linha é um objeto.", aba="camera", linha=n)
        eq = equipamento_da_linha(linha, "camera", "camera", n)
        if eq is not None and eq.id in vistos:
            raise LinhaInvalida("a mesma câmera aparece duas vezes.", aba="camera", linha=n)
        if eq is not None:
            vistos.add(eq.id)
        antes = vista_por_id.get(eq.id) if eq is not None else None
        dvr = _texto(linha, "dvr", 64, aba="camera", n=n)
        if dvr is not None and dvr not in refs_de_dvr:
            raise LinhaInvalida("o DVR escolhido não é deste ambiente.", aba="camera", linha=n)
        verificacoes = [dict(v) for v in (antes or {}).get("verificacoes", [])]
        if not any(v["tipo"] == "icmp" for v in verificacoes):
            verificacoes = _trocar_verificacao(verificacoes, "icmp", None, {"ref": "icmp", "parametros": {}})
        para.append(
            {
                "id": eq.id if eq is not None else None,
                "ref": ref_da_linha(linha, eq, "camera", n),
                "tipo": "camera",
                "nome": _texto(linha, "nome", 128, aba="camera", n=n, obrigatorio=True),
                "host": _texto(linha, "ip", 255, aba="camera", n=n, obrigatorio=True),
                "mac": antes["mac"] if antes else None,
                "fabricante": _texto(linha, "fabricante", 64, aba="camera", n=n),
                "modelo": _texto(linha, "modelo", 64, aba="camera", n=n),
                "paiRef": dvr,
                "tentativasParaCair": antes["tentativasParaCair"] if antes else lista.TENTATIVAS_PADRAO,
                "verificacoes": verificacoes,
                "ambiente": env.id,
                "canais": None,
                "canal": _numero(linha, "canal", 1, lista.MAXIMO_DE_CANAIS, aba="camera", n=n),
            }
        )
        origens.append(_Origem("camera", n))
    return para, origens


def gravar(
    db: DBSession, env: ExtensionEnvironment, pedido: dict[str, Any], agora: datetime
) -> lista.Gravacao:
    """Valida e grava. Não faz commit. ``LinhaInvalida`` com a aba e a linha."""
    para, origens = montar_para(db, env, pedido)
    atual = lista.vista(db)
    try:
        validado = lista.validar_para(
            para, atual, aceitos=modulos.aceitos(db), ambientes=lista.ids_de_ambientes(db)
        )
    except lista.ForaDeForma as exc:
        origem = origens[exc.indice] if exc.indice is not None and exc.indice < len(origens) else None
        if origem is None:
            raise LinhaInvalida(f"a lista de equipamentos recusou o pedido: {exc.motivo}") from exc
        raise LinhaInvalida(exc.motivo, aba=origem.aba, linha=origem.linha) from exc
    return lista.gravar(db, validado, agora, aceitos=modulos.aceitos(db))
