"""De onde sai a hora que vai para o telefone.

Três níveis, do mais específico para o mais geral: o ambiente pode mandar (filial
em outro fuso), senão vale a configuração da instalação, senão o fuso detectado
do servidor. O objetivo do desenho é que **o caso normal não exija digitar nada**:
o instalador sobe o serviço, e todo telefone de todo ambiente recebe o fuso da
máquina que os administra.

Por que existem as chaves ``timezone_mode``/``ntp_mode`` em vez de "campo vazio =
herda": ambiente antigo **já tem** ``timezone`` gravado no blob, porque
``create_environment`` serializa ``default_config_padrao()`` inteiro. Sem um modo
explícito, uma filial em Manaus ficaria presa em São Paulo para sempre, sem que
ninguém entendesse por quê.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Importado como modulo, e nao pelos nomes: assim um teste que fixa o fuso do
# servidor (monkeypatch em core.timezone) alcanca esta camada tambem.
from middleware_monitor.core import timezone as core_tz
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.timezone import FALLBACK_TZ

__all__ = [
    "FALLBACK_NTP",
    "HERDAR",
    "PROPRIO",
    "TimeSettings",
    "global_settings",
    "invalidate_cache",
    "resolve",
]

log = get_logger("extension_configurator.time")

# O NTP público do Observatório Nacional. É o default histórico do produto e o
# que os aparelhos de fábrica já trazem — trocar marcaria toda a planilha como
# desatualizada sem ganho nenhum.
FALLBACK_NTP = "a.ntp.br"

# Valor dos campos de modo. "herdar" é o default e o caminho normal.
HERDAR = "herdar"
PROPRIO = "proprio"


@dataclass(frozen=True, slots=True)
class TimeSettings:
    """A hora resolvida, pronta para o adapter traduzir para o seu dialeto."""

    timezone: str  # nome IANA
    offset_minutes: int
    ntp_server: str
    origem_tz: str  # ambiente | global | servidor | fallback
    origem_ntp: str  # ambiente | global | fallback

    @property
    def herdado(self) -> bool:
        return self.origem_tz in ("servidor", "fallback")


def _texto(fonte: dict[str, Any], chave: str) -> str:
    valor = fonte.get(chave)
    return str(valor).strip() if valor not in (None, "") else ""


def _resolver_fuso(
    cfg_ambiente: dict[str, Any], cfg_global: dict[str, Any],
) -> tuple[str, int, str]:
    if _texto(cfg_ambiente, "timezone_mode") == PROPRIO:
        nome = _texto(cfg_ambiente, "timezone")
        if core_tz.is_valid_iana(nome):
            return nome, core_tz.offset_minutes_for(nome) or 0, "ambiente"

    if _texto(cfg_global, "phone_timezone_mode") == PROPRIO:
        nome = _texto(cfg_global, "phone_timezone")
        if core_tz.is_valid_iana(nome):
            return nome, core_tz.offset_minutes_for(nome) or 0, "global"

    detectado = core_tz.detect_server_timezone()
    if detectado.name:
        return detectado.name, detectado.offset_minutes, "servidor"

    # Sem nome IANA: o offset do SO ainda vale, e é ele que o Yealink e o
    # Intelbras V usam. O nome de fallback é só para quem precisa de tabela.
    return FALLBACK_TZ, detectado.offset_minutes, "fallback"


# A configuração global vem do KV `app_config`, que só muda quando alguém salva
# a tela. Ler uma vez por processo e invalidar na escrita evita que
# `compute_line_hash` — chamado uma vez por ramal, 200 vezes numa planilha
# grande — abra uma sessão de banco por linha.
_global_cache: dict[str, Any] | None = None


def global_settings() -> dict[str, Any]:
    """Ajustes de hora da instalação, em cache até alguém salvar a tela.

    Falha silenciosa por desenho: quem chama é a geração de config, e banco
    indisponível não pode impedir de renderizar — sem a configuração global a
    precedência simplesmente cai para o fuso detectado do servidor.
    """
    global _global_cache
    if _global_cache is not None:
        return _global_cache

    dados: dict[str, Any] = {}
    try:
        from middleware_monitor.core.db import session_factory
        from middleware_monitor.domain.config.repository import load_config

        with session_factory() as db:
            cfg = load_config(db)
        dados = {
            "phone_timezone_mode": cfg.phone_timezone_mode,
            "phone_timezone": cfg.phone_timezone,
            "phone_ntp_server": cfg.phone_ntp_server,
        }
    except Exception as exc:
        log.warning(
            "time_settings_global_indisponivel",
            error=type(exc).__name__, message=str(exc),
        )
    _global_cache = dados
    return dados


def resolve(
    cfg_ambiente: dict[str, Any] | None = None,
    cfg_global: dict[str, Any] | None = None,
) -> TimeSettings:
    """Aplica a precedência e devolve o que o telefone deve receber.

    Determinística dentro de uma execução: a detecção do servidor é cacheada e o
    resto vem dos dois dicionários. Isso importa porque ``compute_line_hash``
    chama isto indiretamente uma vez por ramal — 200 chamadas numa planilha
    grande — e o hash não pode oscilar entre elas.
    """
    ambiente = cfg_ambiente or {}
    # None = "vá buscar"; um dicionário explícito (inclusive vazio) é respeitado
    # como está, que é o que os testes usam para fixar cada nível da precedência.
    geral = global_settings() if cfg_global is None else cfg_global

    nome, offset, origem_tz = _resolver_fuso(ambiente, geral)

    if _texto(ambiente, "ntp_mode") == PROPRIO and (ntp := _texto(ambiente, "ntp_server")):
        origem_ntp = "ambiente"
    elif ntp := _texto(geral, "phone_ntp_server"):
        origem_ntp = "global"
    else:
        ntp, origem_ntp = FALLBACK_NTP, "fallback"

    return TimeSettings(
        timezone=nome,
        offset_minutes=offset,
        ntp_server=ntp,
        origem_tz=origem_tz,
        origem_ntp=origem_ntp,
    )


def invalidate_cache() -> None:
    """Chamado quando a configuração global muda, para o efeito ser imediato."""
    global _global_cache
    _global_cache = None
    core_tz.forget_detection()
