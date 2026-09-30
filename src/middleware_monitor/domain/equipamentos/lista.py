"""O cadastro de equipamentos: a lista que o NOC define e o retrato que volta.

Três formas da mesma lista, cada uma com o seu uso (CONTRATO-DO-AGENTE §13):

- **o retrato** (``retrato``) — o que viaja no lote, com estado e última leitura;
- **a vista** (``vista``) — o retrato sem nada que muda sozinho; é o ``de`` que o
  NOC devolve em ``definir_equipamentos``;
- **o pedido** (``validar_para``) — o ``para``, com ``paiRef`` no lugar de ``paiId``
  (o pai pode nascer no mesmo pedido que o filho).

As regras que não se re-derivam lendo o código:

- **O ``de`` é conferido como conjunto por ``id``, campo a campo.** Ordem não
  importa; campo ausente e ``null`` são iguais. Qualquer diferença — inclusive
  de quantidade — recusa o pedido inteiro. O NOC nunca sobrescreve o que mudou
  aqui depois do retrato que ele viu.
- **O ``id`` nasce aqui e não muda.** Nunca o IP nem o nome: equipamento troca de
  endereço e é renomeado, e continua sendo o mesmo.
- **Trocar o que a verificação mede zera o estado dela.** Host novo zera o
  equipamento inteiro; ``tipo`` ou ``parametros`` novos zeram a verificação. O
  estado antigo falava de outra coisa.
- **Editar não gera evento.** Evento é fato do equipamento, não da lista.

Plano 1 (CFTV, CONTRATO §13.8 e §13.9):

- **Verificação de módulo não aceito é recusada** quando nasce ou muda de medida;
  a que já existia e continua igual fica guardada e ``pausada``
  (``aplicar_modulos``). Sem isso, desligar um módulo travaria qualquer edição da
  lista até o NOC apagar as verificações dele.
- **``credenciais`` ausente (ou ``null``) no item do ``para`` mantém as guardadas**;
  ``[]`` apaga. É a regra do lote: parte ausente é "não mudou". O segredo é
  gravado cifrado e só a impressão sai daqui.
- **Credencial nova zera as verificações ``cftv`` do equipamento**: o "senha
  recusada" de antes falava da credencial velha.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import selectinload

from middleware_monitor.core.crypto import SecretBox, open_box
from middleware_monitor.core.models import (
    CredencialDeEquipamento,
    Equipamento,
    LeituraDeCftv,
    VerificacaoDeEquipamento,
)
from middleware_monitor.domain.equipamentos import estados
from middleware_monitor.domain.equipamentos.modulos import modulo_da_verificacao
from middleware_monitor.settings import get_settings

TIPOS: tuple[str, ...] = (
    "gravador",
    "camera",
    "switch",
    "roteador",
    "firewall",
    "ponto_de_acesso",
    "link",
    "nobreak",
    "servidor",
    "hipervisor",
    "controle_de_acesso",
    "impressora",
    "outro",
)
TIPOS_DE_VERIFICACAO: tuple[str, ...] = ("icmp", "tcp", "http", "cftv")
ADAPTADORES_DE_CFTV: tuple[str, ...] = ("intelbras", "hikvision", "unifi")
# ADR 0037 do NOC: um de cada por equipamento.
TIPOS_DE_CREDENCIAL: tuple[str, ...] = (
    "HTTP",
    "API",
    "ONVIF",
    "SNMP_V2C",
    "SNMP_V3",
    "SSH",
    "WINRM",
    "REDFISH",
)
MAXIMO_DE_CREDENCIAIS = len(TIPOS_DE_CREDENCIAL)
LIMITE_DO_USUARIO = 128
LIMITE_DO_SEGREDO = 1024

MAXIMO_DE_EQUIPAMENTOS = 5000
MAXIMO_DE_VERIFICACOES = 10
INTERVALO_MINIMO_S = 30
INTERVALO_PADRAO_S = 60
INTERVALO_MAXIMO_S = 86_400
TENTATIVAS_PADRAO = 3
TENTATIVAS_MAXIMAS = 10

CAMPOS_DO_PARA = frozenset(
    {
        "id",
        "ref",
        "tipo",
        "nome",
        "host",
        "mac",
        "fabricante",
        "modelo",
        "paiRef",
        "tentativasParaCair",
        "verificacoes",
        "credenciais",
    }
)
CAMPOS_DA_CREDENCIAL = frozenset({"tipo", "usuario", "segredo"})
CAMPOS_DA_VERIFICACAO = frozenset({"id", "ref", "tipo", "parametros", "intervaloS"})
# O que o ``de`` compara — exatamente a ``vista``.
CAMPOS_DO_DE = (
    "id",
    "ref",
    "tipo",
    "nome",
    "host",
    "mac",
    "fabricante",
    "modelo",
    "paiId",
    "tentativasParaCair",
)
CAMPOS_DA_VERIFICACAO_NO_DE = ("id", "ref", "tipo", "parametros", "intervaloS")
# O que muda a cada leitura e não entra no hash de "só quando muda" do lote.
VOLATEIS_DA_VERIFICACAO = frozenset({"ultimoValor", "ultimaEm", "ultimoErro"})

_HOST = re.compile(r"^[A-Za-z0-9._:%\[\]-]{1,255}$")
_CAMINHO = re.compile(r"^/[\x21-\x7e]{0,511}$")
_LIMITES_DE_TEXTO = {"ref": 64, "nome": 128, "mac": 32, "fabricante": 64, "modelo": 64}


def novo_id_equipamento() -> str:
    return f"eq_{secrets.token_hex(8)}"


def novo_id_verificacao() -> str:
    return f"vf_{secrets.token_hex(6)}"


def _hora(valor: datetime | None) -> str | None:
    """UTC sem fuso, como o resto do lote (CONTRATO §8)."""
    return valor.replace(tzinfo=None).isoformat(timespec="seconds") if valor else None


def parametros_de(vf: VerificacaoDeEquipamento) -> dict[str, Any]:
    try:
        valor = json.loads(vf.parametros or "{}")
    except ValueError:
        return {}
    return valor if isinstance(valor, dict) else {}


def _todos(db: DBSession) -> list[Equipamento]:
    return list(
        db.scalars(
            select(Equipamento)
            .options(selectinload(Equipamento.verificacoes), selectinload(Equipamento.credenciais))
            .order_by(Equipamento.id)
        ).all()
    )


# --- As formas de saída ------------------------------------------------------------------


def _vista_de(eq: Equipamento) -> dict[str, Any]:
    return {
        "id": eq.id,
        "ref": eq.ref,
        "tipo": eq.tipo,
        "nome": eq.nome,
        "host": eq.host,
        "mac": eq.mac,
        "fabricante": eq.fabricante,
        "modelo": eq.modelo,
        "paiId": eq.pai_id,
        "tentativasParaCair": eq.tentativas_para_cair,
        "verificacoes": [
            {
                "id": vf.id,
                "ref": vf.ref,
                "tipo": vf.tipo,
                "parametros": parametros_de(vf),
                "intervaloS": vf.intervalo_s,
            }
            for vf in eq.verificacoes
        ],
    }


def vista(db: DBSession) -> list[dict[str, Any]]:
    """A lista na forma do ``de`` — o que a recusa ``LISTA_MUDOU`` devolve em ``atual``."""
    return [_vista_de(eq) for eq in _todos(db)]


def impressao(segredo: str) -> str:
    """Os 12 primeiros hexadecimais do sha256 do segredo — é por ela que o NOC sabe
    que a credencial chegou, sem o segredo sair daqui (CONTRATO §13.9)."""
    return hashlib.sha256(segredo.encode("utf-8")).hexdigest()[:12]


def _leituras_de_cftv(db: DBSession) -> dict[str, dict[str, Any]]:
    saida = {}
    for linha in db.scalars(select(LeituraDeCftv)).all():
        try:
            valor = json.loads(linha.leitura or "{}")
        except ValueError:
            continue
        if isinstance(valor, dict):
            saida[linha.equipamento_id] = valor
    return saida


def retrato(db: DBSession) -> list[dict[str, Any]]:
    """A parte ``equipamentos`` do lote: a vista, mais estado e última leitura,
    as credenciais **sem o segredo** e a última leitura de CFTV."""
    saida = []
    leituras = _leituras_de_cftv(db)
    for eq in _todos(db):
        item = _vista_de(eq)
        item["estado"] = eq.estado
        item["estadoDesde"] = _hora(eq.estado_desde)
        item["credenciais"] = [
            {"tipo": c.tipo, "usuario": c.usuario, "impressao": c.impressao} for c in eq.credenciais
        ]
        item["cftv"] = leituras.get(eq.id)
        for dados, vf in zip(item["verificacoes"], eq.verificacoes, strict=True):
            dados.update(
                {
                    "estado": vf.estado,
                    "estadoDesde": _hora(vf.estado_desde),
                    "ultimoValor": vf.ultimo_valor,
                    "ultimaEm": _hora(vf.ultima_em),
                    "ultimoErro": vf.ultimo_erro,
                }
            )
        saida.append(item)
    return saida


def sha256_do_retrato(itens: list[dict[str, Any]]) -> str:
    """Hash do retrato sem a última leitura: latência que oscila não é mudança.
    Mudança de estado, de cadastro ou de ``estadoDesde`` é. Do ``cftv`` saem a hora
    e o ``lidoEm`` (``cftv.estavel``)."""
    from middleware_monitor.domain.equipamentos import cftv

    estavel = [
        {
            **eq,
            "cftv": cftv.estavel(eq.get("cftv")),
            "verificacoes": [
                {k: v for k, v in vf.items() if k not in VOLATEIS_DA_VERIFICACAO}
                for vf in eq.get("verificacoes", [])
            ],
        }
        for eq in itens
    ]
    canonico = json.dumps(estavel, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


# --- A conferência do ``de`` -------------------------------------------------------------


class ForaDeForma(ValueError):
    """``de`` ou ``para`` que não tem a forma do contrato. ``indice`` é a posição
    (a partir de 0) no ``para``; ``verificacao``, a posição da verificação."""

    def __init__(self, motivo: str, *, indice: int | None = None, verificacao: int | None = None) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.indice = indice
        self.verificacao = verificacao

    def como_dict(self) -> dict[str, Any]:
        saida: dict[str, Any] = {"motivo": self.motivo}
        if self.indice is not None:
            saida["indice"] = self.indice
        if self.verificacao is not None:
            saida["verificacao"] = self.verificacao
        return saida


def _sem_nulos(valor: Any) -> Any:
    if isinstance(valor, dict):
        return {k: _sem_nulos(v) for k, v in valor.items() if v is not None}
    return valor


def _canonico(item: dict[str, Any]) -> str:
    """Um item da vista como texto comparável: só os campos do ``de``, ausente = null,
    verificações por ``id``. ``True`` não é ``1`` (JSON distingue)."""
    verificacoes = item.get("verificacoes") or []
    if not isinstance(verificacoes, list) or not all(isinstance(v, dict) for v in verificacoes):
        raise ForaDeForma("de: verificacoes é uma lista de objetos.")
    forma = {
        **{c: item.get(c) for c in CAMPOS_DO_DE},
        "verificacoes": sorted(
            (
                {
                    **{c: v.get(c) for c in CAMPOS_DA_VERIFICACAO_NO_DE},
                    "parametros": _sem_nulos(v.get("parametros") or {}),
                }
                for v in verificacoes
            ),
            key=lambda v: str(v["id"]),
        ),
    }
    return json.dumps(forma, sort_keys=True, ensure_ascii=False)


def por_id(lista: list[dict[str, Any]]) -> dict[str, str]:
    saida: dict[str, str] = {}
    for item in lista:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ForaDeForma("de: cada item é um objeto com o id que o agente emitiu.")
        if item["id"] in saida:
            raise ForaDeForma(f"de: o id {item['id']!r} aparece duas vezes.")
        saida[item["id"]] = _canonico(item)
    return saida


def confere(de: list[Any], atual: list[dict[str, Any]]) -> bool:
    """``de`` é a lista atual? Como conjunto por ``id``, campo a campo."""
    return por_id(de) == por_id(atual)


# --- A validação do ``para`` -------------------------------------------------------------


def _texto(item: dict[str, Any], campo: str, *, obrigatorio: bool, i: int) -> str | None:
    valor = item.get(campo)
    if valor is None or valor == "":
        if obrigatorio:
            raise ForaDeForma(f"{campo} é obrigatório.", indice=i)
        return None
    if not isinstance(valor, str) or len(valor) > _LIMITES_DE_TEXTO[campo] or valor != valor.strip():
        raise ForaDeForma(
            f"{campo}: texto de até {_LIMITES_DE_TEXTO[campo]} caracteres, sem espaço nas pontas.", indice=i
        )
    return valor


def _inteiro(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _porta(valor: Any, onde: str) -> int:
    if not _inteiro(valor) or not 1 <= valor <= 65535:
        raise ValueError(f"{onde}: porta é um inteiro de 1 a 65535.")
    return int(valor)


def normalizar_parametros(tipo: str, parametros: Any) -> dict[str, Any]:
    """Os ``parametros`` com os padrões preenchidos. ``ValueError`` com o motivo."""
    if parametros is None:
        parametros = {}
    if not isinstance(parametros, dict):
        raise ValueError("parametros é um objeto.")
    permitidos = {
        "icmp": set(),
        "tcp": {"porta"},
        "http": {"esquema", "porta", "caminho", "statusEsperado", "verificarCertificado"},
        "cftv": {"adaptador", "esquema", "porta"},
    }[tipo]
    extras = sorted(set(parametros) - permitidos)
    if extras:
        raise ValueError(f"parametros de {tipo} não aceita {', '.join(extras)}.")
    if tipo == "icmp":
        return {}
    if tipo == "tcp":
        if "porta" not in parametros:
            raise ValueError("tcp precisa de parametros.porta.")
        return {"porta": _porta(parametros["porta"], "tcp")}
    if tipo == "cftv":
        adaptador = parametros.get("adaptador")
        if adaptador not in ADAPTADORES_DE_CFTV:
            raise ValueError(f"cftv: adaptador é {', '.join(ADAPTADORES_DE_CFTV)}.")
        esquema = parametros.get("esquema") or ("https" if adaptador == "unifi" else "http")
        if esquema not in ("http", "https"):
            raise ValueError("cftv: esquema é http ou https.")
        porta = parametros.get("porta")
        porta = (443 if esquema == "https" else 80) if porta is None else _porta(porta, "cftv")
        return {"adaptador": adaptador, "esquema": esquema, "porta": porta}
    esquema = parametros.get("esquema") or "http"
    if esquema not in ("http", "https"):
        raise ValueError("http: esquema é http ou https.")
    porta = parametros.get("porta")
    porta = (443 if esquema == "https" else 80) if porta is None else _porta(porta, "http")
    caminho = parametros.get("caminho") or "/"
    if not isinstance(caminho, str) or not _CAMINHO.match(caminho):
        raise ValueError("http: caminho começa com / e não tem espaço.")
    status = parametros.get("statusEsperado")
    status = 200 if status is None else status
    if not _inteiro(status) or not 100 <= status <= 599:
        raise ValueError("http: statusEsperado é um inteiro de 100 a 599.")
    verificar = parametros.get("verificarCertificado")
    verificar = False if verificar is None else verificar
    if not isinstance(verificar, bool):
        raise ValueError("http: verificarCertificado é true ou false.")
    return {
        "esquema": esquema,
        "porta": porta,
        "caminho": caminho,
        "statusEsperado": status,
        "verificarCertificado": verificar,
    }


def _verificacoes(
    item: dict[str, Any],
    i: int,
    dele: dict[str, dict[str, Any]],
    de_outros: set[str],
    aceitos: set[str] | None,
) -> list[dict[str, Any]]:
    lista = item.get("verificacoes")
    lista = [] if lista is None else lista
    if not isinstance(lista, list):
        raise ForaDeForma("verificacoes é uma lista.", indice=i)
    if len(lista) > MAXIMO_DE_VERIFICACOES:
        raise ForaDeForma(f"no máximo {MAXIMO_DE_VERIFICACOES} verificações por equipamento.", indice=i)
    saida: list[dict[str, Any]] = []
    refs: set[str] = set()
    ids: set[str] = set()
    for j, v in enumerate(lista):
        if not isinstance(v, dict):
            raise ForaDeForma("cada verificação é um objeto.", indice=i, verificacao=j)
        extras = sorted(set(v) - CAMPOS_DA_VERIFICACAO)
        if extras:
            raise ForaDeForma(f"a verificação não tem o campo {extras[0]!r}.", indice=i, verificacao=j)
        vid = v.get("id")
        if vid is not None:
            if not isinstance(vid, str) or vid not in dele:
                motivo = (
                    f"a verificação {vid!r} é de outro equipamento."
                    if vid in de_outros
                    else f"a verificação {vid!r} não existe neste equipamento (nova vai com id null)."
                )
                raise ForaDeForma(motivo, indice=i, verificacao=j)
            if vid in ids:
                raise ForaDeForma(f"a verificação {vid!r} aparece duas vezes.", indice=i, verificacao=j)
            ids.add(vid)
        ref = v.get("ref")
        if not isinstance(ref, str) or not ref or len(ref) > _LIMITES_DE_TEXTO["ref"]:
            raise ForaDeForma("a verificação precisa de ref (texto de até 64).", indice=i, verificacao=j)
        if ref in refs:
            raise ForaDeForma(f"ref repetido nas verificações: {ref!r}.", indice=i, verificacao=j)
        refs.add(ref)
        tipo = v.get("tipo")
        if tipo not in TIPOS_DE_VERIFICACAO:
            raise ForaDeForma(
                f"tipo de verificação {tipo!r} fora da lista ({', '.join(TIPOS_DE_VERIFICACAO)}).",
                indice=i,
                verificacao=j,
            )
        intervalo = v.get("intervaloS")
        intervalo = INTERVALO_PADRAO_S if intervalo is None else intervalo
        if not _inteiro(intervalo) or not INTERVALO_MINIMO_S <= intervalo <= INTERVALO_MAXIMO_S:
            raise ForaDeForma(
                f"intervaloS é um inteiro de {INTERVALO_MINIMO_S} a {INTERVALO_MAXIMO_S}.",
                indice=i,
                verificacao=j,
            )
        try:
            parametros = normalizar_parametros(tipo, v.get("parametros"))
        except ValueError as exc:
            raise ForaDeForma(str(exc), indice=i, verificacao=j) from exc
        if tipo == "cftv":
            if item.get("tipo") != "gravador":
                raise ForaDeForma("verificação cftv só em equipamento gravador.", indice=i, verificacao=j)
            if any(s["tipo"] == "cftv" for s in saida):
                raise ForaDeForma("uma verificação cftv por equipamento.", indice=i, verificacao=j)
        modulo = modulo_da_verificacao(tipo, parametros)
        antes = dele.get(vid) if vid else None
        mesma_medida = antes is not None and antes["tipo"] == tipo and antes["parametros"] == parametros
        if aceitos is not None and modulo not in aceitos and not mesma_medida:
            raise ForaDeForma(f"módulo {modulo} não aceito neste agente.", indice=i, verificacao=j)
        saida.append({"id": vid, "ref": ref, "tipo": tipo, "parametros": parametros, "intervaloS": intervalo})
    return saida


def _credenciais(item: dict[str, Any], i: int) -> list[dict[str, str]] | None:
    """``None`` = manter as guardadas; lista = trocar por esta."""
    lista = item.get("credenciais")
    if lista is None:
        return None
    if not isinstance(lista, list) or len(lista) > MAXIMO_DE_CREDENCIAIS:
        raise ForaDeForma(f"credenciais é uma lista de até {MAXIMO_DE_CREDENCIAIS} itens.", indice=i)
    saida: list[dict[str, str]] = []
    for c in lista:
        if not isinstance(c, dict) or set(c) - CAMPOS_DA_CREDENCIAL:
            raise ForaDeForma("cada credencial é { tipo, usuario, segredo }.", indice=i)
        tipo = c.get("tipo")
        if tipo not in TIPOS_DE_CREDENCIAL:
            raise ForaDeForma(
                f"tipo de credencial {str(tipo)[:20]!r} fora da lista ({', '.join(TIPOS_DE_CREDENCIAL)}).",
                indice=i,
            )
        if any(s["tipo"] == tipo for s in saida):
            raise ForaDeForma(f"credencial {tipo} repetida.", indice=i)
        usuario = c.get("usuario")
        usuario = "" if usuario is None else usuario
        if not isinstance(usuario, str) or len(usuario) > LIMITE_DO_USUARIO:
            raise ForaDeForma(f"credencial {tipo}: usuario é texto de até {LIMITE_DO_USUARIO}.", indice=i)
        segredo = c.get("segredo")
        # A mensagem nunca repete o segredo: ela volta ao NOC e vai para o log.
        if not isinstance(segredo, str) or not segredo or len(segredo) > LIMITE_DO_SEGREDO:
            raise ForaDeForma(
                f"credencial {tipo}: segredo é texto de 1 a {LIMITE_DO_SEGREDO} caracteres.", indice=i
            )
        saida.append({"tipo": tipo, "usuario": usuario, "segredo": segredo})
    if saida and _box() is None:
        raise ForaDeForma(
            "esta instalação não tem APP_SECRET_KEY utilizável: a credencial não pode ser guardada cifrada.",
            indice=i,
        )
    return saida


def validar_para(
    para: list[Any], atual: list[dict[str, Any]], *, aceitos: set[str] | None = None
) -> list[dict[str, Any]]:
    """O ``para`` normalizado, ou ``ForaDeForma`` com o índice e o motivo.

    ``aceitos`` são os módulos aceitos neste agente; ``None`` não confere (uso interno)."""
    if len(para) > MAXIMO_DE_EQUIPAMENTOS:
        raise ForaDeForma(f"no máximo {MAXIMO_DE_EQUIPAMENTOS} equipamentos por agente.")
    verificacoes_de = {eq["id"]: {v["id"]: v for v in eq["verificacoes"]} for eq in atual}
    todas = set().union(*verificacoes_de.values()) if verificacoes_de else set()
    saida: list[dict[str, Any]] = []
    refs: dict[str, int] = {}
    ids: set[str] = set()
    for i, item in enumerate(para):
        if not isinstance(item, dict):
            raise ForaDeForma("cada item de para é um objeto.", indice=i)
        extras = sorted(set(item) - CAMPOS_DO_PARA)
        if extras:
            dica = " (o pai vai por paiRef)" if extras[0] == "paiId" else ""
            raise ForaDeForma(f"para não tem o campo {extras[0]!r}{dica}.", indice=i)
        eid = item.get("id")
        if eid is not None:
            if not isinstance(eid, str) or eid not in verificacoes_de:
                raise ForaDeForma(f"o equipamento {eid!r} não existe (novo vai com id null).", indice=i)
            if eid in ids:
                raise ForaDeForma(f"o equipamento {eid!r} aparece duas vezes.", indice=i)
            ids.add(eid)
        ref = _texto(item, "ref", obrigatorio=True, i=i)
        assert ref is not None
        if ref in refs:
            raise ForaDeForma(f"ref repetido: {ref!r} (também no índice {refs[ref]}).", indice=i)
        refs[ref] = i
        tipo = item.get("tipo")
        if tipo not in TIPOS:
            raise ForaDeForma(f"tipo {tipo!r} fora da lista ({', '.join(TIPOS)}).", indice=i)
        host = item.get("host")
        if not isinstance(host, str) or not host.strip():
            raise ForaDeForma("host é obrigatório.", indice=i)
        if not _HOST.match(host):
            raise ForaDeForma(f"host {host[:80]!r} não é um IP nem um nome de host.", indice=i)
        tentativas = item.get("tentativasParaCair")
        tentativas = TENTATIVAS_PADRAO if tentativas is None else tentativas
        if not _inteiro(tentativas) or not 1 <= tentativas <= TENTATIVAS_MAXIMAS:
            raise ForaDeForma(f"tentativasParaCair é um inteiro de 1 a {TENTATIVAS_MAXIMAS}.", indice=i)
        pai = item.get("paiRef")
        if pai is not None and (not isinstance(pai, str) or not pai):
            raise ForaDeForma("paiRef é o ref de outro equipamento, ou null.", indice=i)
        saida.append(
            {
                "id": eid,
                "ref": ref,
                "tipo": tipo,
                "nome": _texto(item, "nome", obrigatorio=True, i=i),
                "host": host,
                "mac": _texto(item, "mac", obrigatorio=False, i=i),
                "fabricante": _texto(item, "fabricante", obrigatorio=False, i=i),
                "modelo": _texto(item, "modelo", obrigatorio=False, i=i),
                "paiRef": pai,
                "tentativasParaCair": tentativas,
                "verificacoes": _verificacoes(
                    item, i, verificacoes_de.get(eid, {}) if eid else {}, todas, aceitos
                ),
                "credenciais": _credenciais(item, i),
            }
        )
    # O pai: existe na lista, não é o próprio e não fecha ciclo.
    pai_de = {eq["ref"]: eq["paiRef"] for eq in saida}
    for i, eq in enumerate(saida):
        if eq["paiRef"] is None:
            continue
        if eq["paiRef"] not in refs:
            raise ForaDeForma(
                f"paiRef {eq['paiRef']!r} não é o ref de nenhum equipamento da lista.", indice=i
            )
        vistos = {eq["ref"]}
        atual_ref: str | None = eq["paiRef"]
        while atual_ref is not None:
            if atual_ref in vistos:
                raise ForaDeForma(f"paiRef fecha um ciclo em {atual_ref!r}.", indice=i)
            vistos.add(atual_ref)
            atual_ref = pai_de.get(atual_ref)
    return saida


# --- A gravação --------------------------------------------------------------------------


@dataclass
class Gravacao:
    criados: int = 0
    removidos: int = 0
    ids_por_ref: dict[str, str] = field(default_factory=dict)


def _box() -> SecretBox | None:
    return open_box(get_settings().secret_key)


def _trocar_credenciais(eq: Equipamento, novas: list[dict[str, str]], agora: datetime) -> None:
    box = _box()
    antes = {(c.tipo, c.usuario, c.impressao) for c in eq.credenciais}
    depois = {(c["tipo"], c["usuario"], impressao(c["segredo"])) for c in novas}
    if antes == depois:
        return
    if novas and box is None:  # validar_para já recusou; aqui é a última trava
        raise ValueError("sem APP_SECRET_KEY: credencial não é gravada em claro")
    eq.credenciais = [
        CredencialDeEquipamento(
            tipo=c["tipo"],
            usuario=c["usuario"],
            segredo=box.encrypt_field(c["segredo"]) if box else "",
            impressao=impressao(c["segredo"]),
        )
        for c in novas
    ]
    # O resultado de antes falava da credencial velha ("senha recusada").
    for vf in eq.verificacoes:
        if vf.tipo == "cftv" and vf.estado != estados.PAUSADA:
            _zerar(vf, agora)


def credencial_de(eq: Equipamento, tipo: str) -> tuple[str, str, str] | None:
    """``(usuario, segredo em claro, impressao)`` da credencial daquele tipo, ou ``None``.
    **Único** caminho de leitura do segredo; chave de cifra trocada levanta ``ValueError``."""
    for c in eq.credenciais:
        if c.tipo == tipo:
            box = _box()
            if box is None:
                raise ValueError("sem APP_SECRET_KEY para abrir a credencial")
            return c.usuario, box.decrypt_field(c.segredo), c.impressao
    return None


def aplicar_modulos(db: DBSession, aceitos: set[str], agora: datetime) -> int:
    """Pausa as verificações de módulo não aceito e retoma (de ``desconhecido``) as
    do módulo aceito. Sem amostra e sem evento: é configuração, não fato. Não faz
    commit. Devolve quantos equipamentos mudaram."""
    mudaram = 0
    for eq in _todos(db):
        mexeu = False
        for vf in eq.verificacoes:
            aceito = modulo_da_verificacao(vf.tipo, parametros_de(vf)) in aceitos
            if not aceito and vf.estado != estados.PAUSADA:
                _zerar(vf, agora)
                vf.estado = estados.PAUSADA
                mexeu = True
            elif aceito and vf.estado == estados.PAUSADA:
                _zerar(vf, agora)
                mexeu = True
        if mexeu:
            mudaram += 1
            derivado = estados.estado_do_equipamento(eq.verificacoes)
            if derivado != eq.estado:
                eq.estado, eq.estado_desde = derivado, agora
    return mudaram


def gravar(
    db: DBSession, validado: list[dict[str, Any]], agora: datetime, *, aceitos: set[str] | None = None
) -> Gravacao:
    """Troca a lista inteira pela ``validado``. Não faz commit — quem chama decide.

    ``validado`` já passou por ``validar_para`` contra a lista atual (na mesma
    transação): todo ``id`` citado existe, e toda verificação citada é do equipamento.
    """
    existentes = {eq.id: eq for eq in _todos(db)}
    g = Gravacao()
    for item in validado:
        g.ids_por_ref[item["ref"]] = item["id"] or novo_id_equipamento()

    citados = {item["id"] for item in validado if item["id"]}
    for eid, antigo in existentes.items():
        if eid not in citados:
            db.delete(antigo)
            g.removidos += 1

    for item in validado:
        eid = g.ids_por_ref[item["ref"]]
        eq: Equipamento | None = existentes.get(eid)
        if eq is None:
            eq = Equipamento(id=eid, criado_em=agora, estado=estados.DESCONHECIDO, estado_desde=agora)
            db.add(eq)
            g.criados += 1
        elif eq.host != item["host"]:
            # Endereço novo: o estado antigo falava de outro lugar.
            eq.estado, eq.estado_desde = estados.DESCONHECIDO, agora
            for zerada in eq.verificacoes:
                _zerar(zerada, agora)
        eq.ref, eq.tipo, eq.nome, eq.host = item["ref"], item["tipo"], item["nome"], item["host"]
        eq.mac, eq.fabricante, eq.modelo = item["mac"], item["fabricante"], item["modelo"]
        eq.pai_id = g.ids_por_ref[item["paiRef"]] if item["paiRef"] else None
        eq.tentativas_para_cair = item["tentativasParaCair"]
        eq.atualizado_em = agora

        antigas = {vf.id: vf for vf in eq.verificacoes}
        novas: list[VerificacaoDeEquipamento] = []
        vf: VerificacaoDeEquipamento | None
        for posicao, v in enumerate(item["verificacoes"]):
            parametros = json.dumps(v["parametros"], sort_keys=True, ensure_ascii=False)
            vf = antigas.get(v["id"]) if v["id"] else None
            if vf is None:
                vf = VerificacaoDeEquipamento(
                    id=novo_id_verificacao(),
                    estado=estados.DESCONHECIDO,
                    estado_desde=agora,
                    falhas_seguidas=0,
                )
            elif vf.tipo != v["tipo"] or parametros_de(vf) != v["parametros"]:
                _zerar(vf, agora)
            vf.ref, vf.tipo, vf.parametros, vf.intervalo_s, vf.posicao = (
                v["ref"],
                v["tipo"],
                parametros,
                v["intervaloS"],
                posicao,
            )
            novas.append(vf)
        eq.verificacoes = novas  # a que saiu da lista é apagada (delete-orphan)
        if item.get("credenciais") is not None:
            _trocar_credenciais(eq, item["credenciais"], agora)

        # Verificação removida ou zerada muda o estado derivado — sem evento.
        derivado = estados.estado_do_equipamento(novas)
        if derivado != eq.estado:
            eq.estado, eq.estado_desde = derivado, agora
        # Sem verificação cftv, a leitura de CFTV guardada não fala de mais nada.
        if not any(vf.tipo == "cftv" for vf in novas) and (leitura := db.get(LeituraDeCftv, eid)):
            db.delete(leitura)
    if aceitos is not None:
        db.flush()
        aplicar_modulos(db, aceitos, agora)
    return g


def _zerar(vf: VerificacaoDeEquipamento, agora: datetime) -> None:
    vf.estado, vf.estado_desde = estados.DESCONHECIDO, agora
    vf.falhas_seguidas = 0
    vf.ultimo_valor = vf.ultima_em = vf.ultimo_erro = None
