"""O que o agente do NOC guarda — KV ``app_config`` com prefixo ``noc.``.

Mesmo padrão do auto-update e do backup: chaves prefixadas, dataclass congelada
na leitura, escrita explícita.

Três decisões que não se re-derivam lendo o código:

- **A credencial fica no banco, cifrada com a ``SecretBox``** — nunca em
  ``/etc`` nem no ``.env``. No Linux o serviço roda com ``ProtectSystem=strict``
  e só escreve em ``/var/lib/middleware-monitor``; no desktop o ``.env`` é lido
  relativo ao cwd, que o ``.exe`` não tem previsível.
- **Nenhuma chave ``noc.*`` viaja no pacote portável do backup**
  (``LOCAL_ONLY_KEYS``). O pacote serve para levar a configuração para OUTRA
  máquina; levar junto a identidade faria duas máquinas se apresentarem ao NOC
  como o mesmo agente, e nenhum log denunciaria qual é qual.
- **Perder a ``APP_SECRET_KEY`` não derruba nada**: a credencial vira
  ilegível, o estado diz ``credencial_ilegivel`` e o caminho é enrolar de novo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.crypto import SecretBox
from middleware_monitor.core.models import AppConfig
from middleware_monitor.core.time import iso_utc
from middleware_monitor.settings import get_settings

_PREFIX = "noc."
KEY_URL = f"{_PREFIX}url"
KEY_AGENTE_ID = f"{_PREFIX}agente_id"
KEY_CREDENCIAL = f"{_PREFIX}credencial"
KEY_ENROLADO_EM = f"{_PREFIX}enrolado_em"
KEY_INTERVALO = f"{_PREFIX}intervalo_heartbeat_s"
KEY_VERSAO_DESEJADA = f"{_PREFIX}versao_desejada"
KEY_SITUACAO = f"{_PREFIX}situacao"
KEY_DETALHE = f"{_PREFIX}detalhe"
KEY_ULTIMO_CONTATO = f"{_PREFIX}ultimo_contato_em"
KEY_ULTIMA_TENTATIVA = f"{_PREFIX}ultima_tentativa_em"
KEY_OFFSET = f"{_PREFIX}relogio_offset_s"
KEY_MANIFESTO_SHA = f"{_PREFIX}manifesto_sha256"
KEY_MANIFESTO_EM = f"{_PREFIX}manifesto_enviado_em"
# O canal mTLS que o NOC indica no enrolamento (e confirma a cada heartbeat).
KEY_URL_CANAL = f"{_PREFIX}url_canal"
KEY_CERTIFICADO_EXPIRA = f"{_PREFIX}certificado_expira_em"
# Telemetria: um cursor por fonte (último id entregue com 202) e o último envio.
KEY_CURSOR_AMOSTRAS = f"{_PREFIX}cursor_amostras"
KEY_CURSOR_EVENTOS = f"{_PREFIX}cursor_eventos"
KEY_CURSOR_APLICACOES = f"{_PREFIX}cursor_aplicacoes"
KEY_CURSOR_COLETA = f"{_PREFIX}cursor_coleta"
KEY_TELEMETRIA_EM = f"{_PREFIX}telemetria_enviada_em"
KEY_TELEMETRIA_DETALHE = f"{_PREFIX}telemetria_detalhe"
CURSORES = {
    "amostras": KEY_CURSOR_AMOSTRAS,
    "eventos": KEY_CURSOR_EVENTOS,
    "aplicacoes": KEY_CURSOR_APLICACOES,
    "coleta": KEY_CURSOR_COLETA,
}

TODAS_AS_CHAVES: frozenset[str] = frozenset(
    {
        KEY_URL,
        KEY_AGENTE_ID,
        KEY_CREDENCIAL,
        KEY_ENROLADO_EM,
        KEY_INTERVALO,
        KEY_VERSAO_DESEJADA,
        KEY_SITUACAO,
        KEY_DETALHE,
        KEY_ULTIMO_CONTATO,
        KEY_ULTIMA_TENTATIVA,
        KEY_OFFSET,
        KEY_MANIFESTO_SHA,
        KEY_MANIFESTO_EM,
        KEY_URL_CANAL,
        KEY_CERTIFICADO_EXPIRA,
        KEY_CURSOR_AMOSTRAS,
        KEY_CURSOR_EVENTOS,
        KEY_CURSOR_APLICACOES,
        KEY_CURSOR_COLETA,
        KEY_TELEMETRIA_EM,
        KEY_TELEMETRIA_DETALHE,
    }
)

# O endereço definitivo do NOC (formulário A2, 2026-09-15). Vive no código, e
# não no `.env`: o agente precisa dele antes de existir qualquer configuração, e
# é a tela de enrolamento que o troca — para homologação no lab, por exemplo.
URL_PADRAO = "https://noc.workconnect.com.br"

INTERVALO_PADRAO_S = 60

# Situações possíveis. `revogado` é a única que PARA o heartbeat: revogar é ato
# deliberado no NOC, e insistir não muda nada. Credencial recusada continua
# tentando — se o NOC tiver restaurado um backup, parar a frota inteira seria
# uma visita técnica por site.
NAO_ENROLADO = "nao_enrolado"
CONECTADO = "conectado"
SEM_CONEXAO = "sem_conexao"
CREDENCIAL_RECUSADA = "credencial_recusada"
CREDENCIAL_ILEGIVEL = "credencial_ilegivel"
REVOGADO = "revogado"
AGUARDANDO = "aguardando_primeiro_contato"


@dataclass(frozen=True)
class EstadoNoc:
    url: str
    agente_id: str | None
    tem_credencial: bool
    enrolado_em: datetime | None
    intervalo_s: int
    versao_desejada: str | None
    situacao: str
    detalhe: str | None
    ultimo_contato_em: datetime | None
    ultima_tentativa_em: datetime | None
    relogio_offset_s: int | None
    manifesto_sha256: str | None
    manifesto_enviado_em: datetime | None
    url_canal: str | None
    certificado_expira_em: datetime | None
    telemetria_enviada_em: datetime | None
    telemetria_detalhe: str | None

    @property
    def endereco_do_canal(self) -> str:
        """Onde o heartbeat e a telemetria vão: o canal mTLS, ou o próprio NOC
        quando ele não indicou canal separado."""
        return self.url_canal or self.url

    @property
    def enrolado(self) -> bool:
        return bool(self.agente_id and self.tem_credencial)

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "enrolado": self.enrolado,
            "agente_id": self.agente_id,
            "enrolado_em": iso_utc(self.enrolado_em),
            "intervalo_heartbeat_s": self.intervalo_s,
            "versao_desejada": self.versao_desejada,
            "situacao": self.situacao,
            "detalhe": self.detalhe,
            "ultimo_contato_em": iso_utc(self.ultimo_contato_em),
            "ultima_tentativa_em": iso_utc(self.ultima_tentativa_em),
            "relogio_offset_s": self.relogio_offset_s,
            "manifesto_enviado_em": iso_utc(self.manifesto_enviado_em),
            "url_canal": self.url_canal,
            "certificado_expira_em": iso_utc(self.certificado_expira_em),
            "telemetria_enviada_em": iso_utc(self.telemetria_enviada_em),
            "telemetria_detalhe": self.telemetria_detalhe,
        }


def _box() -> SecretBox:
    return SecretBox(get_settings().secret_key)


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _linhas(db: DBSession) -> dict[str, AppConfig]:
    return {r.key: r for r in db.scalars(select(AppConfig).where(AppConfig.key.startswith(_PREFIX))).all()}


def _data(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _int(raw: str | None) -> int | None:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


def carregar(db: DBSession) -> EstadoNoc:
    r = {k: v.value for k, v in _linhas(db).items()}
    tem_credencial = bool(r.get(KEY_CREDENCIAL))
    agente_id = r.get(KEY_AGENTE_ID) or None
    situacao = r.get(KEY_SITUACAO) or (AGUARDANDO if agente_id and tem_credencial else NAO_ENROLADO)
    intervalo = _int(r.get(KEY_INTERVALO))
    return EstadoNoc(
        url=r.get(KEY_URL) or URL_PADRAO,
        agente_id=agente_id,
        tem_credencial=tem_credencial,
        enrolado_em=_data(r.get(KEY_ENROLADO_EM)),
        intervalo_s=intervalo if intervalo and 15 <= intervalo <= 3600 else INTERVALO_PADRAO_S,
        versao_desejada=r.get(KEY_VERSAO_DESEJADA) or None,
        situacao=situacao,
        detalhe=r.get(KEY_DETALHE) or None,
        ultimo_contato_em=_data(r.get(KEY_ULTIMO_CONTATO)),
        ultima_tentativa_em=_data(r.get(KEY_ULTIMA_TENTATIVA)),
        relogio_offset_s=_int(r.get(KEY_OFFSET)),
        manifesto_sha256=r.get(KEY_MANIFESTO_SHA) or None,
        manifesto_enviado_em=_data(r.get(KEY_MANIFESTO_EM)),
        url_canal=r.get(KEY_URL_CANAL) or None,
        certificado_expira_em=_data(r.get(KEY_CERTIFICADO_EXPIRA)),
        telemetria_enviada_em=_data(r.get(KEY_TELEMETRIA_EM)),
        telemetria_detalhe=r.get(KEY_TELEMETRIA_DETALHE) or None,
    )


def carregar_cursores(db: DBSession) -> dict[str, int | None]:
    """Os cursores guardados; ``None`` onde ainda não existe (primeiro envio)."""
    r = {k: v.value for k, v in _linhas(db).items()}
    return {nome: _int(r.get(chave)) for nome, chave in CURSORES.items()}


def gravar(db: DBSession, valores: dict[str, str | None], *, user_id: int | None = None) -> None:
    """Grava as chaves dadas; ``None`` apaga. Não faz commit — quem chama decide."""
    existentes = _linhas(db)
    agora = _agora()
    for chave, valor in valores.items():
        assert chave in TODAS_AS_CHAVES, chave
        linha = existentes.get(chave)
        if valor is None:
            if linha is not None:
                db.delete(linha)
            continue
        segredo = chave == KEY_CREDENCIAL
        if linha is None:
            db.add(AppConfig(key=chave, value=valor, is_secret=segredo, updated_at=agora, updated_by=user_id))
        else:
            linha.value = valor
            linha.is_secret = segredo
            linha.updated_at = agora
            linha.updated_by = user_id


def guardar_credencial(
    db: DBSession, *, url: str, agente_id: str, credencial: str, intervalo_s: int, user_id: int | None
) -> None:
    gravar(
        db,
        {
            KEY_URL: url,
            KEY_AGENTE_ID: agente_id,
            KEY_CREDENCIAL: _box().encrypt(credencial),
            KEY_ENROLADO_EM: _agora().isoformat(),
            KEY_INTERVALO: str(intervalo_s),
            KEY_SITUACAO: AGUARDANDO,
            KEY_DETALHE: None,
            KEY_ULTIMO_CONTATO: None,
            KEY_MANIFESTO_SHA: None,
            KEY_MANIFESTO_EM: None,
            KEY_VERSAO_DESEJADA: None,
            KEY_OFFSET: None,
        },
        user_id=user_id,
    )


def ler_credencial(db: DBSession) -> str | None:
    """A credencial em claro, ou ``None``. Chave de cifra trocada levanta ``ValueError``."""
    linha = _linhas(db).get(KEY_CREDENCIAL)
    if linha is None or not linha.value:
        return None
    return _box().decrypt(linha.value)


def esquecer(db: DBSession, *, user_id: int | None) -> None:
    """Apaga a identidade local. **Não revoga no NOC** — isso é ato do NOC."""
    # O endereço fica: desenrolar para enrolar de novo no mesmo NOC é o caso comum.
    gravar(db, {k: None for k in TODAS_AS_CHAVES if k != KEY_URL}, user_id=user_id)
