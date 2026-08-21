"""Job: reconstrói chamadas a partir das transições e atualiza o resumo diário.

Roda em lote, e não dentro do coletor, por três razões práticas:

* **retomável** — parte do último evento consumido, então uma parada no meio não
  perde nem duplica nada;
* **não atrapalha a ingestão** — o caminho crítico do coletor é gravar o
  comprovante; deduzir chamada é trabalho que pode esperar alguns segundos;
* **reprocessável** — se a regra de reconstrução mudar (e ela já mudou uma vez,
  quando os dados desmentiram a premissa do ``duracao``), dá para recalcular
  sobre as transições que ainda estiverem na retenção.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.domain.mqtt import calls

log = get_logger("jobs.rebuild_calls")


async def run_rebuild_calls() -> None:
    """Processa as transições novas. Frequente e barato (426 ms para 11 mil)."""
    try:
        with session_factory() as db:
            resultado = calls.rebuild_calls(db)
            db.commit()
    except Exception as exc:
        log.error("rebuild_calls_failed", error=type(exc).__name__, message=str(exc))
        return
    if resultado["lidos"]:
        log.info("rebuild_calls_ok", **resultado)


async def run_daily_stats() -> None:
    """Recalcula o resumo de hoje e de ontem.

    Ontem entra junto porque o job roda em UTC e a virada do dia local cai no
    meio: sem recalcular o dia anterior, as chamadas do fim da noite ficariam
    fora do resumo para sempre — e o resumo é o que sobrevive à poda das
    transições.
    """
    hoje = datetime.now().astimezone().date()
    try:
        with session_factory() as db:
            for dia in (hoje - timedelta(days=1), hoje):
                calls.rebuild_daily_stats(db, dia.isoformat())
            db.commit()
    except Exception as exc:
        log.error("daily_stats_failed", error=type(exc).__name__, message=str(exc))
