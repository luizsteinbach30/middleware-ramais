"""Reconhecimento do payload de status de ramal.

O reconhecimento é **pelo formato do corpo, não pelo nome do tópico**: o
publicador pode mudar o tópico de cliente para cliente, e o ledger continua
gravando tudo de qualquer jeito. O que estas funções fazem é enriquecer a
mensagem com os campos que a busca usa (``ramal``, ``event_at``).

Formato reconhecido (USCall/Asterisk, o mesmo contrato do ``/api/extenstatus``)::

    {"retorno": {"0119": {"status": "Ocupado", "ramal": "0119",
                          "data": "2026-08-19 13:51:01.256211",
                          "duracao": "2026-08-19 13:50:58",
                          "numero": "800", "uniqueid": "1787158106.5138"}}}

``duracao`` **não é uma duração**: é o horário de início da chamada em curso —
é assim que o publicador manda, e é o que permite reconstruir a chamada mesmo
quando o ``uniqueid`` não vem (nem todo ramal o publica).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "STATUS_ORDER",
    "ExtensionStatus",
    "logical_from_status",
    "normalize_status",
    "parse_extension_payload",
    "parse_pbx_datetime",
    "ramal_from_topic",
]

_STATUS_KEYS = ("status", "ramal")

# Status canônicos, na ordem em que o painel os agrupa (do mais "livre" ao mais
# "preso"). O publicador manda com acento e caixa variável; o canônico é sem
# acento e minúsculo, para não depender de como o PBX escreveu.
STATUS_ORDER = ("disponivel", "tocando", "discando", "ocupado", "indisponivel")

_ACENTOS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
                         "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC")

# Sinônimos vistos no campo. Mantido explícito (e não por "startswith") porque
# um status novo do publicador tem de aparecer como desconhecido na tela, não
# ser silenciosamente confundido com um conhecido.
_SINONIMOS = {
    "disponivel": "disponivel",
    "livre": "disponivel",
    "indisponivel": "indisponivel",
    "offline": "indisponivel",
    "tocando": "tocando",
    "ringing": "tocando",
    "chamando": "tocando",
    "ocupado": "ocupado",
    "em chamada": "ocupado",
    "falando": "ocupado",
    "discando": "discando",
    "dialing": "discando",
}


def normalize_status(raw: str | None) -> str:
    """``'Indisponível'`` → ``'indisponivel'``. Não reconhecido → ``'desconhecido'``."""
    texto = (raw or "").translate(_ACENTOS).strip().lower()
    if not texto:
        return "desconhecido"
    return _SINONIMOS.get(texto, "desconhecido")


def logical_from_status(status: str) -> str | None:
    """Status canônico → ``logical_status`` do Device, ou ``None`` se não dá para dizer.

    **Tocando, Ocupado e Discando querem dizer que o ramal ESTÁ registrado** no
    PBX — só quem está registrado toca ou conversa. Marcá-los ``unavailable``
    faria ``jobs/monitor_devices.py`` reaplicar configuração no telefone no meio
    da ligação, porque lá o critério de "config perdida" é responder ping e
    estar ``unavailable``. Só ``Indisponivel`` significa registro caído.

    ``desconhecido`` devolve ``None``: status que o publicador inventou não
    derruba nem levanta o estado do ramal — fica como estava.
    """
    if status == "indisponivel":
        return "unavailable"
    if status in STATUS_ORDER:
        return "available"
    return None


@dataclass(slots=True)
class ExtensionStatus:
    ramal: str
    status: str
    numero: str
    uniqueid: str | None
    event_at: datetime | None  # campo "data" — quando o PBX viu o evento (UTC)
    call_started_at: datetime | None  # campo "duracao" — início da chamada (UTC)


def parse_extension_payload(payload: str) -> list[ExtensionStatus]:
    """Extrai os status contidos no payload. Lista vazia = não reconhecido.

    Nunca levanta: payload inválido é só payload não reconhecido — a mensagem
    crua já está gravada, que é o que importa para a prova.
    """
    text = (payload or "").strip()
    if not text.startswith("{"):
        return []
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    retorno = data.get("retorno")
    if not isinstance(retorno, dict):
        return []

    out: list[ExtensionStatus] = []
    for chave, valor in retorno.items():
        if not isinstance(valor, dict):
            continue
        if not all(k in valor for k in _STATUS_KEYS):
            continue
        # A chave do dicionário e o campo "ramal" sempre bateram na captura de
        # referência; se divergirem, o campo manda (é o dado, não o índice).
        ramal = str(valor.get("ramal") or chave or "").strip()
        if not ramal:
            continue
        out.append(
            ExtensionStatus(
                ramal=ramal,
                status=str(valor.get("status") or "").strip(),
                numero=str(valor.get("numero") or "").strip(),
                uniqueid=(str(valor.get("uniqueid")).strip() or None)
                if valor.get("uniqueid") not in (None, "")
                else None,
                event_at=parse_pbx_datetime(valor.get("data")),
                call_started_at=parse_pbx_datetime(valor.get("duracao")),
            )
        )
    return out


def parse_pbx_datetime(raw: Any) -> datetime | None:
    """``'2026-08-19 13:51:01.256211'`` → datetime naive em UTC.

    O PBX manda hora local sem fuso; assume-se o fuso do servidor onde o
    middleware roda (na prática, a mesma máquina/rede do PBX). Um desvio grande
    aparece no painel como atraso médio entre o evento e o recebimento.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            local = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return local.astimezone().astimezone(UTC).replace(tzinfo=None)
    return None


def ramal_from_topic(topic: str) -> str | None:
    """Último nível do tópico, quando parece um ramal (só dígitos).

    Reserva para quando o payload não é reconhecido — ``v1/data/extenStatus/0119``
    ainda permite filtrar por ramal na tela.
    """
    if not topic:
        return None
    last = topic.rsplit("/", 1)[-1].strip()
    if last and last.isdigit() and len(last) <= 32:
        return last
    return None
