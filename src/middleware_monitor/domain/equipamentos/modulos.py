"""Módulos aceitos: o que o agente sabe e o que ele pode usar (CONTRATO §13.8, AGENTE-NOC item 25).

**Saber** é da versão (``CATALOGO``); **aceitar** é configuração deste agente, e
fica no KV ``app_config`` (chave ``modulos.aceitos``). Muda por dois caminhos —
a tela local (``aceitoPor = "local:<usuário>"``) e o verbo ``definir_modulos``
do NOC (``aceitoPor = "noc:<email>"``) — e **vale a última mudança**.

As regras que não se re-derivam lendo o código:

- **Padrão num agente novo:** ``telefonia`` e ``disponibilidade`` aceitos, os
  outros não. Ligar um adaptador de CFTV é escolha de alguém, e fica registrado
  quem e quando.
- **``telefonia`` é só um sinal para as telas** (decisão de 30/09): aceito por
  padrão em todo agente, novo e existente. Desligar **não para nada** do que a
  telefonia faz hoje — coleta do USCall, MQTT, configurador, jobs e as partes do
  lote continuam iguais (regra do dono: nada muda no que a telefonia faz hoje).
  O desligado só esconde a frente do menu, aqui e no NOC, e fica no manifesto.
  Nenhum código de telefonia consulta este módulo.
- **``aceitoEm``/``aceitoPor`` só mudam quando o ``aceito`` muda.** Regravar o
  mesmo valor não é mudança: o manifesto não troca de hash à toa.
- **Módulo desligado não roda.** As verificações dele ficam guardadas e
  ``pausada`` (sem amostra e sem evento); religado, voltam de ``desconhecido``
  (``lista.aplicar_modulos``).
- **A chave não viaja no pacote portável do backup** (``LOCAL_ONLY_KEYS``): segue
  a lista de equipamentos, que é deste agente e também não viaja.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.models import AppConfig

KEY_MODULOS = "modulos.aceitos"


@dataclass(frozen=True)
class Modulo:
    nome: str
    versao: int
    # ``False`` = adaptador escrito só pela documentação do fabricante, antes da
    # prova em equipamento real (ADR 0037, decisão 3). A tela diz isso.
    homologado: bool
    rotulo: str
    descricao: str


# Lista fechada do que esta versão traz. O NOC só oferece na tela o que está aqui.
CATALOGO: tuple[Modulo, ...] = (
    # Só sinal para o menu: desligar NÃO para coleta, MQTT, configurador nem lote.
    Modulo(
        "telefonia",
        1,
        True,
        "Telefonia",
        "USCall, coleta, aparelhos, MQTT e configurador. Desligar só esconde do menu; não para a coleta.",
    ),
    Modulo("disponibilidade", 1, True, "Disponibilidade", "Ping, porta TCP e página HTTP dos equipamentos."),
    Modulo(
        "cftv.intelbras",
        1,
        False,
        "CFTV Intelbras / Dahua",
        "Canais, discos e hora de gravadores Intelbras e Dahua (CGI, só leitura).",
    ),
    Modulo(
        "cftv.hikvision",
        1,
        False,
        "CFTV Hikvision",
        "Canais, discos e hora de gravadores Hikvision (ISAPI, só leitura).",
    ),
    Modulo(
        "cftv.unifi",
        1,
        False,
        "CFTV UniFi Protect",
        "Câmeras, gravação e discos do console UniFi Protect (usuário só de leitura).",
    ),
)
POR_NOME: dict[str, Modulo] = {m.nome: m for m in CATALOGO}
PADRAO_ACEITOS: frozenset[str] = frozenset({"telefonia", "disponibilidade"})


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def modulo_da_verificacao(tipo: str, parametros: dict[str, Any]) -> str:
    """O módulo de que a verificação depende."""
    if tipo == "cftv":
        return f"cftv.{parametros.get('adaptador')}"
    return "disponibilidade"


def _guardado(db: DBSession) -> dict[str, dict[str, Any]]:
    linha = db.get(AppConfig, KEY_MODULOS)
    if linha is None or not linha.value:
        return {}
    try:
        valor = json.loads(linha.value)
    except ValueError:
        return {}
    return {k: v for k, v in valor.items() if isinstance(v, dict)} if isinstance(valor, dict) else {}


def listar(db: DBSession) -> list[dict[str, Any]]:
    """Todos os módulos desta versão, na forma do manifesto."""
    guardado = _guardado(db)
    saida = []
    for m in CATALOGO:
        g = guardado.get(m.nome) or {}
        aceito = g.get("aceito")
        saida.append(
            {
                "nome": m.nome,
                "versao": m.versao,
                "homologado": m.homologado,
                "aceito": aceito if isinstance(aceito, bool) else m.nome in PADRAO_ACEITOS,
                "aceitoEm": g.get("aceitoEm") if isinstance(g.get("aceitoEm"), str) else None,
                "aceitoPor": g.get("aceitoPor") if isinstance(g.get("aceitoPor"), str) else None,
            }
        )
    return saida


def aceitos(db: DBSession) -> set[str]:
    return {m["nome"] for m in listar(db) if m["aceito"]}


def definir(
    db: DBSession, mudancas: dict[str, bool], *, por: str, user_id: int | None = None
) -> list[dict[str, Any]]:
    """Grava ``aceito`` dos módulos dados e pausa/retoma as verificações. Não faz
    commit — quem chama decide. Módulo fora do ``CATALOGO`` levanta ``KeyError``."""
    from middleware_monitor.domain.equipamentos import lista

    for nome in mudancas:
        if nome not in POR_NOME:
            raise KeyError(nome)
    agora = _agora()
    atual = {m["nome"]: m for m in listar(db)}
    guardado = _guardado(db)
    for nome, aceito in mudancas.items():
        if atual[nome]["aceito"] == aceito:
            continue  # regravar o mesmo valor não é mudança
        guardado[nome] = {"aceito": aceito, "aceitoEm": _iso(agora), "aceitoPor": por[:128]}
    linha = db.get(AppConfig, KEY_MODULOS)
    valor = json.dumps(guardado, sort_keys=True, ensure_ascii=False)
    if linha is None:
        db.add(AppConfig(key=KEY_MODULOS, value=valor, is_secret=False, updated_at=agora, updated_by=user_id))
    elif linha.value != valor:
        linha.value, linha.updated_at, linha.updated_by = valor, agora, user_id
    db.flush()
    lista.aplicar_modulos(db, aceitos(db), agora)
    return listar(db)
