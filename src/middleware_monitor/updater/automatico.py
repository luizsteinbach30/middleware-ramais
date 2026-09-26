"""A atualização pedida pelo NOC — ``docs/AGENTE-NOC.md`` item 16, ADR 0008.

O NOC diz qual versão quer (``frota.versao_desejada``), em que janela, e se
alguém apertou "Atualizar agora". Este módulo decide **se e quando** instalar,
e conta ao NOC em que pé está. Quem instala é ``updater/instalar.py``, o mesmo
caminho do botão local.

As regras que não se re-derivam lendo o código:

- **Só sobe.** Voltar de versão pode exigir desfazer migration do banco, e
  nenhum instalador faz isso. Desejada abaixo da instalada é ``ACIMA_DA_DESEJADA``.
- **Versão exata.** Instala-se a desejada, não "a mais nova do canal": o NOC é
  quem decide o ritmo da frota.
- **Só ocioso.** Nada começa com aplicação aberta, escrita remota em andamento,
  restauração pendente ou túnel de acesso web aberto — trocar a versão no meio
  derrubaria o que a pessoa está fazendo.
- **Frota espalhada.** Cada agente espera, dentro da janela, um atraso fixo tirado
  do próprio id (até 60 min). Sem isso a frota inteira baixa a release no mesmo
  minuto das 02:00.
- **Três tentativas por versão**, uma por hora no máximo. Depois, ``FALHOU`` até
  alguém pedir "Atualizar agora" (que zera a conta) ou o NOC mudar a versão.
- **O estado sobrevive ao reinício.** ``INSTALANDO`` gravado antes de trocar; no
  boot seguinte, versão nova rodando é ``EM_DIA``, a antiga depois de 20 min é
  ``FALHOU`` — a volta automática do instalador já aconteceu.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from typing import Any

from packaging.version import InvalidVersion, Version

from middleware_monitor.core.logging import get_logger

log = get_logger("updater.automatico")

EM_DIA = "EM_DIA"
ACIMA = "ACIMA_DA_DESEJADA"
AGUARDANDO = "AGUARDANDO_JANELA"
OCUPADO = "OCUPADO"
SEM_RELEASE = "SEM_RELEASE"
INSTALANDO = "INSTALANDO"
FALHOU = "FALHOU"
DESLIGADA = "DESLIGADA"

MAXIMO_DE_TENTATIVAS = 3
INTERVALO_ENTRE_TENTATIVAS = timedelta(hours=1)
ESPALHAMENTO_MAXIMO_MIN = 60
# Mais que isso em INSTALANDO sem a versão nova rodando: a troca não ficou.
PRAZO_DA_INSTALACAO = timedelta(minutes=20)
# Run de aplicação "aberto" há mais que isso é resto de uma queda, não trabalho.
RUN_ABANDONADO = timedelta(hours=6)

_HORA = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_PREFIXO = "noc.atualizacao."
_CHAVES = ("estado", "alvo", "detalhe", "em", "tentativas", "ultima_tentativa", "pedido_tratado")


@dataclass(frozen=True)
class Pedido:
    """O que o NOC mandou em ``atualizacao`` no heartbeat (CONTRATO §12.1)."""

    versao_desejada: str | None
    janela_inicio: time
    janela_fim: time
    agora_pedido_em: str | None

    @classmethod
    def do_heartbeat(cls, bruto: Any) -> Pedido | None:
        if not isinstance(bruto, dict):
            return None
        bruta = bruto.get("janela")
        janela: dict[str, Any] = bruta if isinstance(bruta, dict) else {}
        inicio = _hora(janela.get("inicio"), time(2, 0))
        fim = _hora(janela.get("fim"), time(5, 0))
        versao = bruto.get("versaoDesejada")
        pedido = bruto.get("atualizarAgoraPedidoEm")
        return cls(
            versao if isinstance(versao, str) and versao else None,
            inicio,
            fim,
            pedido if isinstance(pedido, str) and pedido else None,
        )


@dataclass(frozen=True)
class Estado:
    """O que fica gravado entre heartbeats (e entre reinícios)."""

    estado: str = EM_DIA
    alvo: str = ""
    detalhe: str = ""
    em: datetime | None = None
    tentativas: int = 0
    ultima_tentativa: datetime | None = None
    pedido_tratado: str = ""

    def para_o_noc(self) -> dict[str, Any]:
        corpo: dict[str, Any] = {"estado": self.estado, "tentativas": min(self.tentativas, 99)}
        if self.alvo:
            corpo["versaoAlvo"] = self.alvo[:40]
        if self.detalhe:
            corpo["detalhe"] = self.detalhe[:300]
        if self.em:
            corpo["em"] = self.em.replace(tzinfo=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        return corpo


@dataclass(frozen=True)
class Decisao:
    estado: Estado
    instalar: bool = False


def _hora(valor: Any, padrao: time) -> time:
    m = _HORA.match(valor) if isinstance(valor, str) else None
    return time(int(m.group(1)), int(m.group(2))) if m else padrao


def _versao(texto: str | None) -> Version | None:
    try:
        return Version(texto) if texto else None
    except InvalidVersion:
        return None


def espalhamento_min(agente_id: str, duracao_da_janela_min: int) -> int:
    """Minutos que este agente espera depois da janela abrir. Fixo por agente."""
    teto = max(1, min(ESPALHAMENTO_MAXIMO_MIN, duracao_da_janela_min))
    return int(hashlib.sha256(agente_id.encode("utf-8")).hexdigest(), 16) % teto


def minutos_na_janela(agora_local: datetime, inicio: time, fim: time) -> tuple[int, int] | None:
    """``(minutos desde que abriu, duração)``, ou ``None`` fora dela. Cruza a meia-noite."""
    ini = inicio.hour * 60 + inicio.minute
    fin = fim.hour * 60 + fim.minute
    agora = agora_local.hour * 60 + agora_local.minute
    duracao = (fin - ini) % (24 * 60) or 24 * 60
    passados = (agora - ini) % (24 * 60)
    return (passados, duracao) if passados < duracao else None


def decidir(  # noqa: PLR0911 - uma saída por regra, na ordem em que valem
    atual: Estado,
    pedido: Pedido,
    *,
    versao_instalada: str,
    agora_utc: datetime,
    agora_local: datetime,
    agente_id: str,
    ocupado: str | None,
    ligada: bool,
) -> Decisao:
    """A regra inteira, sem banco nem rede — é o que os testes exercitam."""
    instalada = _versao(versao_instalada)
    desejada = _versao(pedido.versao_desejada)
    alvo = str(desejada) if desejada else ""

    def com(estado: str, detalhe: str = "", **extra: Any) -> Estado:
        return replace(atual, estado=estado, alvo=alvo, detalhe=detalhe, em=agora_utc, **extra)

    if instalada is None or desejada is None:
        return Decisao(com(EM_DIA, "o NOC não definiu versão desejada" if desejada is None else ""))
    if instalada == desejada:
        return Decisao(com(EM_DIA, tentativas=0))
    if instalada > desejada:
        return Decisao(com(ACIMA, f"instalada {instalada}; só sobe — voltar de versão é manual"))
    if not ligada:
        return Decisao(com(DESLIGADA, "desligada na tela de atualizações deste middleware"))

    # Uma troca em curso: espera o prazo antes de chamar de falha.
    if atual.estado == INSTALANDO and atual.alvo == alvo and atual.ultima_tentativa:
        if agora_utc - atual.ultima_tentativa < PRAZO_DA_INSTALACAO:
            return Decisao(replace(atual, em=agora_utc))
        return Decisao(com(FALHOU, "a versão nova não ficou instalada; a anterior continua rodando"))

    tentativas = atual.tentativas if atual.alvo == alvo else 0
    agora_pedido = bool(pedido.agora_pedido_em) and pedido.agora_pedido_em != atual.pedido_tratado
    if agora_pedido:
        tentativas = 0

    # Release ainda não publicada: consultar o GitHub a cada heartbeat seria 60 chamadas por
    # hora por agente. Uma por hora basta — ou na hora, se alguém pedir.
    if (
        not agora_pedido
        and atual.estado == SEM_RELEASE
        and atual.alvo == alvo
        and atual.em
        and agora_utc - atual.em < INTERVALO_ENTRE_TENTATIVAS
    ):
        return Decisao(atual)

    if tentativas >= MAXIMO_DE_TENTATIVAS:
        detalhe = atual.detalhe if atual.estado == FALHOU and atual.alvo == alvo else ""
        return Decisao(com(FALHOU, detalhe or f"{tentativas} tentativas sem sucesso", tentativas=tentativas))
    if (
        not agora_pedido
        and tentativas
        and atual.ultima_tentativa
        and agora_utc - atual.ultima_tentativa < INTERVALO_ENTRE_TENTATIVAS
    ):
        return Decisao(
            com(FALHOU if atual.estado == FALHOU else AGUARDANDO, atual.detalhe, tentativas=tentativas)
        )

    if not agora_pedido:
        janela = minutos_na_janela(agora_local, pedido.janela_inicio, pedido.janela_fim)
        abre = pedido.janela_inicio.strftime("%H:%M")
        if janela is None:
            return Decisao(com(AGUARDANDO, f"abre às {abre}", tentativas=tentativas))
        passados, duracao = janela
        espera = espalhamento_min(agente_id, duracao)
        if passados < espera:
            return Decisao(
                com(
                    AGUARDANDO,
                    f"na janela; a vez deste agente é {espera} min depois de {abre}",
                    tentativas=tentativas,
                )
            )

    if ocupado:
        return Decisao(com(OCUPADO, ocupado, tentativas=tentativas))

    return Decisao(
        com(
            INSTALANDO,
            "pedido pelo NOC agora" if agora_pedido else "na janela do NOC",
            tentativas=tentativas + 1,
            ultima_tentativa=agora_utc,
            pedido_tratado=pedido.agora_pedido_em if agora_pedido else atual.pedido_tratado,
        ),
        instalar=True,
    )


# --- Estado gravado ----------------------------------------------------------------------


def carregar(db: Any) -> Estado:
    from sqlalchemy import select

    from middleware_monitor.core.models import AppConfig

    linhas = {
        r.key.removeprefix(_PREFIXO): r.value
        for r in db.scalars(select(AppConfig).where(AppConfig.key.startswith(_PREFIXO)))
    }

    def data(k: str) -> datetime | None:
        try:
            return datetime.fromisoformat(linhas[k]) if linhas.get(k) else None
        except ValueError:
            return None

    try:
        tentativas = int(linhas.get("tentativas") or 0)
    except ValueError:
        tentativas = 0
    return Estado(
        estado=linhas.get("estado") or EM_DIA,
        alvo=linhas.get("alvo") or "",
        detalhe=linhas.get("detalhe") or "",
        em=data("em"),
        tentativas=tentativas,
        ultima_tentativa=data("ultima_tentativa"),
        pedido_tratado=linhas.get("pedido_tratado") or "",
    )


def gravar(db: Any, e: Estado) -> None:
    """Não faz commit — quem chama decide. ``noc.*`` não viaja no pacote portável."""
    from sqlalchemy import select

    from middleware_monitor.core.models import AppConfig

    valores = {
        "estado": e.estado,
        "alvo": e.alvo,
        "detalhe": e.detalhe[:300],
        "em": e.em.isoformat() if e.em else "",
        "tentativas": str(e.tentativas),
        "ultima_tentativa": e.ultima_tentativa.isoformat() if e.ultima_tentativa else "",
        "pedido_tratado": e.pedido_tratado,
    }
    existentes = {r.key: r for r in db.scalars(select(AppConfig).where(AppConfig.key.startswith(_PREFIXO)))}
    agora = datetime.now(UTC).replace(tzinfo=None)
    for k in _CHAVES:
        chave = f"{_PREFIXO}{k}"
        linha = existentes.get(chave)
        if linha is None:
            db.add(AppConfig(key=chave, value=valores[k], is_secret=False, updated_at=agora))
        else:
            linha.value = valores[k]
            linha.updated_at = agora


# --- O que ocupa o middleware --------------------------------------------------------------


def ocupacao(db: Any, agora_utc: datetime) -> str | None:
    """O motivo de não trocar a versão agora, ou ``None`` se está ocioso."""
    from sqlalchemy import func, select

    from middleware_monitor.core.models import ExtensionApplyRun, NocTarefa

    aberto = db.scalar(
        select(func.count())
        .select_from(ExtensionApplyRun)
        .where(
            ExtensionApplyRun.finished_at.is_(None), ExtensionApplyRun.started_at > agora_utc - RUN_ABANDONADO
        )
    )
    if aberto:
        return "há aplicação de configuração em andamento"
    escrita = db.scalar(
        select(func.count())
        .select_from(NocTarefa)
        .where(
            NocTarefa.iniciada_em.is_not(None), NocTarefa.concluida_em.is_(None), NocTarefa.raio != "LEITURA"
        )
    )
    if escrita:
        return "há escrita remota do NOC em andamento"
    from middleware_monitor.domain.noc import tunel

    if tunel.abertas():
        return "há acesso web aberto pelo túnel do NOC"
    if _restauracao_pendente():
        return "há restauração de backup esperando o próximo boot"
    return None


def _restauracao_pendente() -> bool:
    from middleware_monitor.domain.backup import snapshot

    try:
        return (snapshot.backups_dir() / snapshot.PENDING_DB).exists()
    except Exception:
        return False


# --- O ciclo, chamado a cada heartbeat -----------------------------------------------------


def _resultado_do_windows(e: Estado) -> Estado:
    """O ajudante do Windows deixa o desfecho da troca num arquivo (standalone.py)."""
    import sys

    if not getattr(sys, "frozen", False):
        return e
    from middleware_monitor.desktop import get_data_dir
    from middleware_monitor.updater.standalone import ler_resultado

    lido = ler_resultado(get_data_dir())
    if lido is None:
        return e
    desfecho, versao, motivo = lido
    if desfecho == "ok":
        return e  # a versão rodando já diz; decidir() marca EM_DIA
    log.warning("atualizacao_voltou", desfecho=desfecho, versao=versao, motivo=motivo)
    # "voltou" = o executável novo subiu e não respondeu: tentar de novo na mesma versão
    # troca o cliente de executável mais duas vezes sem mudar nada. Só o botão do NOC (ou
    # uma versão desejada nova) tenta outra vez.
    tentativas = MAXIMO_DE_TENTATIVAS if desfecho == "voltou" else e.tentativas
    return replace(e, estado=FALHOU, alvo=versao or e.alvo, detalhe=motivo or desfecho, tentativas=tentativas)


async def ciclo(resposta: Any, *, agente_id: str) -> dict[str, Any] | None:
    """Um passo, depois de cada heartbeat. Devolve o estado para o próximo heartbeat
    (``None`` quando o NOC não anunciou ``atualizacao`` — NOC antigo recusaria o campo)."""
    from middleware_monitor.core.db import session_factory
    from middleware_monitor.domain.config.update_settings import load_update_settings
    from middleware_monitor.version import __version__

    pedido = Pedido.do_heartbeat(resposta)
    if pedido is None:
        return None
    agora_utc = datetime.now(UTC).replace(tzinfo=None)
    with session_factory() as db:
        atual = _resultado_do_windows(carregar(db))
        decisao = decidir(
            atual,
            pedido,
            versao_instalada=__version__,
            agora_utc=agora_utc,
            agora_local=datetime.now().astimezone(),
            agente_id=agente_id,
            ocupado=ocupacao(db, agora_utc),
            ligada=load_update_settings(db).auto_noc,
        )
        estado = decisao.estado
        gravar(db, estado)
        db.commit()
    if estado.estado != atual.estado or estado.alvo != atual.alvo:
        log.info(
            "atualizacao_estado",
            de=atual.estado,
            para=estado.estado,
            alvo=estado.alvo,
            detalhe=estado.detalhe,
        )
    if decisao.instalar:
        estado = await _instalar(estado)
    return estado.para_o_noc()


async def _instalar(estado: Estado) -> Estado:
    import sys

    from middleware_monitor.core.db import session_factory
    from middleware_monitor.settings import get_settings
    from middleware_monitor.updater.client import GithubReleasesClient, UpdateMode
    from middleware_monitor.updater.instalar import FalhaNaInstalacao, instalar

    settings = get_settings()
    cliente = GithubReleasesClient(settings.update_repo, token=settings.effective_update_token)
    modo: UpdateMode = "standalone" if getattr(sys, "frozen", False) else "legacy"
    falha: str | None = None
    novo = estado
    try:
        release = await cliente.release_for_version(Version(estado.alvo), mode=modo)
        if release is None:
            # Não conta como tentativa: publicar a release resolve sozinho.
            novo = replace(
                estado,
                estado=SEM_RELEASE,
                detalhe=f"a versão {estado.alvo} não está publicada para este sistema",
                tentativas=max(0, estado.tentativas - 1),
            )
        else:
            log.info("atualizacao_iniciada", alvo=estado.alvo, detalhe=estado.detalhe)
            instalar(release, encerrar_em_s=3.0)
            return estado
    except FalhaNaInstalacao as exc:
        falha = str(exc)
    except Exception as exc:  # GitHub fora, disco cheio: vira FALHOU com o motivo
        falha = f"{type(exc).__name__}: {exc}"
    if falha:
        log.error("atualizacao_falhou", alvo=estado.alvo, erro=falha)
        novo = replace(estado, estado=FALHOU, detalhe=falha[:300])
    with session_factory() as db:
        gravar(db, novo)
        db.commit()
    return novo
