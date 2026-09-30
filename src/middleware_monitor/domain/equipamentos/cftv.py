"""A verificação ``cftv``: ler o gravador, julgar a leitura e contar o que mudou (CONTRATO §13.9).

- ``ler``       — escolhe o adaptador, abre a credencial e lê. **Só leitura.**
- ``julgar``    — a leitura vira o ``cftv`` do retrato e o ``ok``/``ultimoErro`` da verificação.
- ``eventos``   — a diferença entre a leitura anterior e a nova, em eventos ``cftv.*``.

As regras que não se re-derivam lendo o código:

- **``ok`` quando leu e não achou problema.** Problema é canal sem vídeo, canal que
  não grava, disco com defeito ou cheio, gravador sem nenhum disco e hora a mais de
  ``LIMITE_DE_DESVIO_S`` da desta máquina. A queda para ``falha`` segue a máquina do
  §13.4 (``tentativasParaCair``), e o equipamento fica ``degradado`` se ainda pinga.
- **Evento é transição, não repetição**, e sai na hora da leitura (não espera as N
  falhas): é o fato do canal ou do disco, não o estado da verificação. A primeira
  leitura só gera evento de problema — "canal voltou" sem ter ido não é notícia.
- **Leitura que falhou não apaga a anterior** nem gera evento de canal/disco: sem
  leitura nova, não se sabe o que mudou.
- **Senha recusada segura a próxima tentativa por ``ESPERA_APOS_SENHA_RECUSADA``**
  com a mesma credencial: gravador bloqueia o usuário depois de algumas senhas
  erradas, e a monitoria não pode trancar o dono fora da própria loja. Credencial
  nova (outra impressão) tenta na hora.
- **O "só quando muda" ignora** ``lidoEm``, ``horaDoGravador`` e ``desvioS`` (entra só
  se o desvio passou do limite) e mede o livre do disco em faixas de 1% do total —
  senão um disco gravando mudaria o retrato a cada leitura.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from middleware_monitor.integrations.cftv import registry
from middleware_monitor.integrations.cftv.base import (
    DISCO_AUSENTE,
    DISCO_CHEIO,
    DISCO_DEFEITO,
    SEM_VIDEO,
    VIDEO_OK,
    Alvo,
    ErroDeLeitura,
    LeituraCftv,
    SenhaRecusada,
)

LIMITE_DE_DESVIO_S = 120
ESPERA_APOS_SENHA_RECUSADA_S = 30 * 60

CANAL_SEM_VIDEO = "cftv.canal_sem_video"
CANAL_VOLTOU = "cftv.canal_voltou"
DISCO_COM_DEFEITO = "cftv.disco_com_defeito"
DISCO_NORMALIZOU = "cftv.disco_normalizou"
PAROU_DE_GRAVAR = "cftv.parou_de_gravar"
VOLTOU_A_GRAVAR = "cftv.voltou_a_gravar"
HORA_ERRADA = "cftv.hora_errada"
HORA_CERTA = "cftv.hora_certa"


@dataclass(frozen=True)
class Credencial:
    """A credencial aberta, só em memória. O segredo não aparece em ``repr``."""

    usuario: str
    segredo: str
    impressao: str

    def __repr__(self) -> str:
        return f"Credencial(usuario={self.usuario!r}, impressao={self.impressao!r})"


@dataclass(frozen=True)
class Lido:
    ok: bool
    erro: str | None
    # O ``cftv`` do retrato; ``None`` quando a leitura não aconteceu.
    retrato: dict[str, Any] | None


# (verificação, impressão) → até quando não tentar de novo (time.monotonic()).
_SEGURAR: dict[tuple[str, str], float] = {}


def esquecer_recusas() -> None:
    _SEGURAR.clear()


def _hora(valor: datetime | None) -> str | None:
    return valor.replace(tzinfo=None).isoformat(timespec="seconds") if valor else None


def julgar(leitura: LeituraCftv, lido_em: datetime) -> Lido:
    desvio = (
        round((leitura.hora_do_gravador - lido_em).total_seconds())
        if leitura.hora_do_gravador is not None
        else None
    )
    retrato = {
        "lidoEm": _hora(lido_em),
        "canais": [
            {"numero": c.numero, "nome": c.nome, "video": c.video, "gravando": c.gravando}
            for c in leitura.canais
        ],
        "discos": [
            {"numero": d.numero, "estado": d.estado, "totalGb": d.total_gb, "livreGb": d.livre_gb}
            for d in leitura.discos
        ],
        "horaDoGravador": _hora(leitura.hora_do_gravador),
        "desvioS": desvio,
        "diasGuardados": leitura.dias_guardados,
    }
    problemas: list[str] = []
    for c in leitura.canais:
        if c.video == SEM_VIDEO:
            problemas.append(f"canal {c.numero} sem vídeo")
        elif c.gravando is False:
            problemas.append(f"canal {c.numero} não está gravando")
    for d in leitura.discos:
        if d.estado == DISCO_DEFEITO:
            problemas.append(f"disco {d.numero} com defeito")
        elif d.estado == DISCO_CHEIO:
            problemas.append(f"disco {d.numero} cheio")
    if not [d for d in leitura.discos if d.estado != DISCO_AUSENTE]:
        problemas.append("nenhum disco no gravador")
    if desvio is not None and abs(desvio) > LIMITE_DE_DESVIO_S:
        problemas.append(f"hora do gravador {abs(desvio)} s {'adiantada' if desvio > 0 else 'atrasada'}")
    return Lido(ok=not problemas, erro="; ".join(problemas)[:500] or None, retrato=retrato)


async def ler(  # noqa: PLR0911 - uma saída por forma de falha
    verificacao: str,
    host: str,
    parametros: dict[str, Any],
    credencial: Credencial | None,
    lido_em: datetime,
    *,
    respeitar_espera: bool = True,
) -> Lido:
    """Lê o gravador. Nunca levanta: falha vira ``Lido(ok=False, erro=…)``."""
    adaptador_id = str(parametros.get("adaptador"))
    try:
        adaptador = registry.get_adapter(adaptador_id)
    except KeyError:
        return Lido(False, f"adaptador de CFTV desconhecido: {adaptador_id!r}", None)
    if credencial is None:
        return Lido(False, f"sem credencial ({adaptador.tipo_de_credencial})", None)
    chave = (verificacao, credencial.impressao)
    if respeitar_espera and _SEGURAR.get(chave, 0.0) > time.monotonic():
        return Lido(False, "senha recusada (nova tentativa segurada para não bloquear o usuário)", None)
    alvo = Alvo(
        host=host,
        esquema=str(parametros.get("esquema") or "http"),
        porta=int(parametros.get("porta") or 80),
        usuario=credencial.usuario,
        senha=credencial.segredo,
    )
    try:
        leitura = await adaptador.ler(alvo)
    except SenhaRecusada as exc:
        _SEGURAR[chave] = time.monotonic() + ESPERA_APOS_SENHA_RECUSADA_S
        return Lido(False, str(exc) or "senha recusada", None)
    except ErroDeLeitura as exc:
        return Lido(False, str(exc)[:500] or type(exc).__name__, None)
    except Exception as exc:  # adaptador quebrado é falha da leitura, nunca job morto
        return Lido(False, f"{type(exc).__name__}: {exc}"[:500], None)
    _SEGURAR.pop(chave, None)
    return julgar(leitura, lido_em)


def _hora_errada(retrato: dict[str, Any]) -> bool | None:
    desvio = retrato.get("desvioS")
    return None if desvio is None else abs(desvio) > LIMITE_DE_DESVIO_S


def estavel(retrato: dict[str, Any] | None) -> dict[str, Any] | None:
    """O ``cftv`` sem o que muda a cada leitura — é o que entra no hash do retrato."""
    if retrato is None:
        return None
    discos = []
    for d in retrato.get("discos") or []:
        total, livre = d.get("totalGb"), d.get("livreGb")
        faixa = int(livre * 100 // total) if total and livre is not None else None
        discos.append({**{k: v for k, v in d.items() if k != "livreGb"}, "livrePct": faixa})
    return {
        **{k: v for k, v in retrato.items() if k not in ("lidoEm", "horaDoGravador", "desvioS", "discos")},
        "discos": discos,
        "horaErrada": _hora_errada(retrato),
    }


def eventos(anterior: dict[str, Any] | None, novo: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """``(tipo, de, para, detalhe)`` de cada transição entre duas leituras."""
    saida: list[tuple[str, str, str, str]] = []
    primeira = anterior is None
    antes = anterior or {}

    canais_antes = {c["numero"]: c for c in antes.get("canais") or []}
    for c in novo.get("canais") or []:
        velho = canais_antes.get(c["numero"], {})
        rotulo = f"canal {c['numero']}" + (f" ({c['nome']})" if c.get("nome") else "")
        v_de, v_para = velho.get("video", "desconhecido"), c["video"]
        if v_para == SEM_VIDEO and v_de != SEM_VIDEO:
            saida.append((CANAL_SEM_VIDEO, v_de, v_para, f"{rotulo} sem vídeo"))
        elif v_de == SEM_VIDEO and v_para == VIDEO_OK and not primeira:
            saida.append((CANAL_VOLTOU, v_de, v_para, f"{rotulo} voltou"))
        g_de, g_para = velho.get("gravando"), c.get("gravando")
        if g_para is False and g_de is not False:
            saida.append((PAROU_DE_GRAVAR, _gravacao(g_de), "parado", f"{rotulo} parou de gravar"))
        elif g_de is False and g_para is True and not primeira:
            saida.append((VOLTOU_A_GRAVAR, "parado", "gravando", f"{rotulo} voltou a gravar"))

    discos_antes = {d["numero"]: d for d in antes.get("discos") or []}
    ruins = (DISCO_DEFEITO, DISCO_CHEIO)
    for d in novo.get("discos") or []:
        d_de = discos_antes.get(d["numero"], {}).get("estado", "desconhecido")
        d_para = d["estado"]
        if d_para in ruins and d_de != d_para:
            texto = "com defeito" if d_para == DISCO_DEFEITO else "cheio"
            saida.append((DISCO_COM_DEFEITO, d_de, d_para, f"disco {d['numero']} {texto}"))
        elif d_de in ruins and d_para not in ruins and not primeira:
            saida.append((DISCO_NORMALIZOU, d_de, d_para, f"disco {d['numero']} {d_para}"))

    h_de, h_para = _hora_errada(antes) if not primeira else None, _hora_errada(novo)
    if h_para is True and h_de is not True:
        saida.append((HORA_ERRADA, "certa" if h_de is False else "desconhecida", "errada", _desvio(novo)))
    elif h_de is True and h_para is False:
        saida.append((HORA_CERTA, "errada", "certa", _desvio(novo)))
    return saida


def _gravacao(valor: bool | None) -> str:
    return "gravando" if valor is True else "parado" if valor is False else "desconhecido"


def _desvio(retrato: dict[str, Any]) -> str:
    desvio = retrato.get("desvioS") or 0
    return f"hora do gravador {abs(desvio)} s {'adiantada' if desvio > 0 else 'atrasada'} ({desvio:+d} s)"
