"""A máquina de estados da disponibilidade (CONTRATO-DO-AGENTE §13.4).

As regras que não se re-derivam lendo o código:

- **A queda só é declarada depois de falhas seguidas** (``tentativasParaCair``,
  padrão 3), e a **volta na primeira resposta**. Uma falha isolada não muda o
  estado da verificação — e, portanto, não gera evento.
- **Verificação nova começa ``desconhecido``** e só vira ``falha`` pelas mesmas N
  falhas seguidas; a primeira resposta a põe em ``ok``.
- **O estado do equipamento é derivado das verificações.** As principais são as
  ``icmp``; sem nenhuma ``icmp``, todas. ``fora`` = todas as principais em falha;
  ``degradado`` = alguma principal responde e outra verificação falha; ``no_ar``
  = alguma principal responde e nenhuma falha. O resto (nada decidido ainda) é
  ``desconhecido``.
- **Só transição gera evento**, e só as quatro do contrato. ``desconhecido →
  no_ar`` não é notícia (é o primeiro ciclo), e voltar a ``desconhecido`` só
  acontece por edição da lista, que não é fato do equipamento.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from middleware_monitor.core.models import VerificacaoDeEquipamento

NO_AR = "no_ar"
DEGRADADO = "degradado"
FORA = "fora"
DESCONHECIDO = "desconhecido"

OK = "ok"
FALHA = "falha"

CAIU = "equipamento.caiu"
VOLTOU = "equipamento.voltou"
DEGRADOU = "equipamento.degradou"
NORMALIZOU = "equipamento.normalizou"


def registrar(
    vf: VerificacaoDeEquipamento,
    *,
    ok: bool,
    valor: int | None,
    erro: str | None,
    em: datetime,
    tentativas_para_cair: int,
) -> bool:
    """Aplica um resultado à verificação. Devolve ``True`` se o estado dela mudou."""
    vf.ultima_em = em
    vf.ultimo_valor = valor if ok else None
    vf.ultimo_erro = None if ok else (erro or "sem resposta")[:500]
    if ok:
        vf.falhas_seguidas = 0
        if vf.estado != OK:
            vf.estado, vf.estado_desde = OK, em
            return True
        return False
    vf.falhas_seguidas = (vf.falhas_seguidas or 0) + 1
    if vf.estado != FALHA and vf.falhas_seguidas >= max(1, tentativas_para_cair):
        vf.estado, vf.estado_desde = FALHA, em
        return True
    return False


def estado_do_equipamento(verificacoes: Iterable[VerificacaoDeEquipamento]) -> str:
    todas = list(verificacoes)
    if not todas:
        return DESCONHECIDO
    principais = [v for v in todas if v.tipo == "icmp"] or todas
    if any(v.estado == OK for v in principais):
        return DEGRADADO if any(v.estado == FALHA for v in todas) else NO_AR
    if all(v.estado == FALHA for v in principais):
        return FORA
    return DESCONHECIDO


def tipo_do_evento(de: str, para: str) -> str | None:
    if para in {de, DESCONHECIDO}:
        return None
    if para == FORA:
        return CAIU
    if de == FORA:
        return VOLTOU
    if para == DEGRADADO:
        return DEGRADOU
    if de == DEGRADADO and para == NO_AR:
        return NORMALIZOU
    return None


def causa(tipo_evento: str, mudaram: list[VerificacaoDeEquipamento]) -> VerificacaoDeEquipamento | None:
    """A verificação que provocou a transição: a que caiu (queda e degradação) ou a
    que voltou (volta e normalização)."""
    alvo = FALHA if tipo_evento in (CAIU, DEGRADOU) else OK
    candidatas = [v for v in mudaram if v.estado == alvo]
    # Queda e volta são do ping; degradação e normalização, da outra verificação.
    principal_primeiro = tipo_evento in (CAIU, VOLTOU)
    candidatas = sorted(candidatas, key=lambda v: (v.tipo == "icmp") != principal_primeiro)
    return candidatas[0] if candidatas else (mudaram[0] if mudaram else None)


def detalhe(tipo_evento: str, vf: VerificacaoDeEquipamento | None, tentativas: int) -> str | None:
    if vf is None:
        return None
    if tipo_evento in (CAIU, DEGRADOU):
        return (
            f"{vf.tipo}: sem sucesso em {tentativas} tentativas seguidas — {vf.ultimo_erro or 'sem resposta'}"
        )
    if vf.ultimo_valor is None:
        return f"{vf.tipo}: respondeu"
    return f"{vf.tipo}: respondeu em {vf.ultimo_valor} ms"
