"""Os serviços de um ambiente: telefone, DVR e câmera (NOC 1.4.0, CONTRATO §13.11).

O ambiente era "um modelo de telefone com ramais". Agora ele diz quais serviços
estão ligados, e cada serviço só aparece (na tela, no retrato, no manifesto)
quando ligado:

- **telefone** — o configurador de ramais de sempre. Ambiente sem telefone tem
  ``modelo_telefone = ""`` (a coluna continua NOT NULL: mudar isso no SQLite é
  recriar a tabela). Desligar com ramal cadastrado é recusado — apagar ramal é
  ato próprio, não efeito colateral de um clique.
- **dvr** / **camera** — os gravadores e as câmeras do ambiente
  (``equipamentos.ambiente_id``). Desligar não apaga nada: esconde a aba e pausa
  as verificações (``lista.aplicar_modulos``). Religar retoma.

Ambiente sem nenhum serviço é recusado: não há o que mostrar nele.
"""

from __future__ import annotations

import json
from typing import Any

SERVICOS: tuple[str, ...] = ("telefone", "dvr", "camera")
PADRAO: tuple[str, ...] = ("telefone",)


class ServicosInvalidos(ValueError):
    pass


def normalizar(valor: Any) -> list[str]:
    """A lista na ordem de ``SERVICOS``, sem repetição. ``ServicosInvalidos`` com o motivo."""
    if not isinstance(valor, list) or not all(isinstance(s, str) for s in valor):
        raise ServicosInvalidos("servicos é uma lista de textos.")
    fora = sorted(set(valor) - set(SERVICOS))
    if fora:
        raise ServicosInvalidos(f"serviço {fora[0]!r} fora da lista ({', '.join(SERVICOS)}).")
    if not valor:
        raise ServicosInvalidos("o ambiente precisa de pelo menos um serviço.")
    return [s for s in SERVICOS if s in valor]


def servicos_de(env: Any) -> list[str]:
    """Os serviços guardados. Valor ilegível vale o padrão (o ambiente de antes da 0017)."""
    try:
        valor = json.loads(getattr(env, "servicos", None) or "null")
        return normalizar(valor)
    except (ValueError, ServicosInvalidos):
        return list(PADRAO)


def tem_telefone(env: Any) -> bool:
    return "telefone" in servicos_de(env)


def serializar(servicos: list[str]) -> str:
    return json.dumps(normalizar(servicos))
