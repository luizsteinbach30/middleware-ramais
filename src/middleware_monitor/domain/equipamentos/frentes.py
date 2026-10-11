"""As frentes dos equipamentos e o que o menu lateral mostra (AGENTE-NOC item 18, telas locais).

A mesma divisão do NOC: gravador e câmera são **CFTV**; switch, roteador, firewall,
ponto de acesso, link e nobreak são **Rede**; servidor e hipervisor são
**Servidores**; o resto é **Outros**.

As regras do menu que não se re-derivam lendo o template:

- **Telefonia** aparece com o módulo ``telefonia`` aceito — e desligar esconde
  **só o ramo do menu**: as páginas continuam abrindo pela URL e a coleta segue
  (regra do dono, 30/09; ``modulos.py``).
- **Equipamentos** aparece com ``disponibilidade`` aceito, ou havendo equipamento
  cadastrado, ou havendo alguma frente visível (um adaptador de CFTV ligado num
  agente ainda sem equipamento já é motivo para mostrar onde ele vai aparecer).
- **Cada frente** aparece quando há equipamento dela **ou** algum módulo dela
  aceito (``cftv.*`` para CFTV; ``rede.*`` e ``servidores.*`` quando existirem).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.models import Equipamento
from middleware_monitor.domain.equipamentos import modulos

CFTV = "cftv"
REDE = "rede"
SERVIDORES = "servidores"
OUTROS = "outros"
FRENTES: tuple[str, ...] = (CFTV, REDE, SERVIDORES, OUTROS)
ROTULOS: dict[str, str] = {CFTV: "CFTV", REDE: "Rede", SERVIDORES: "Servidores", OUTROS: "Outros"}

_TIPOS_DA_FRENTE: dict[str, frozenset[str]] = {
    CFTV: frozenset({"gravador", "camera"}),
    REDE: frozenset({"switch", "roteador", "firewall", "ponto_de_acesso", "link", "nobreak"}),
    SERVIDORES: frozenset({"servidor", "hipervisor"}),
}


def frente_do_tipo(tipo: str) -> str:
    for frente, tipos in _TIPOS_DA_FRENTE.items():
        if tipo in tipos:
            return frente
    return OUTROS


def tipos_da_frente(frente: str) -> frozenset[str] | None:
    """Os tipos da frente; ``None`` para ``outros`` (é tudo o que não cai nas outras)."""
    return _TIPOS_DA_FRENTE.get(frente)


def contagem_por_frente(db: DBSession) -> dict[str, int]:
    contagem = dict.fromkeys(FRENTES, 0)
    for tipo, qtd in db.execute(select(Equipamento.tipo, func.count()).group_by(Equipamento.tipo)).all():
        contagem[frente_do_tipo(tipo)] += int(qtd)
    return contagem


def menu(db: DBSession) -> dict[str, Any]:
    """O que o menu lateral mostra. Lido no render de toda página e em ``GET /api/menu``."""
    aceitos = modulos.aceitos(db)
    contagem = contagem_por_frente(db)
    frentes = {f: contagem[f] > 0 or any(m.startswith(f"{f}.") for m in aceitos) for f in FRENTES}
    total = sum(contagem.values())
    return {
        "modulos_aceitos": sorted(aceitos),
        "telefonia": "telefonia" in aceitos,
        "equipamentos": "disponibilidade" in aceitos or total > 0 or any(frentes.values()),
        "frentes": frentes,
        "contagem": {**contagem, "total": total},
    }
