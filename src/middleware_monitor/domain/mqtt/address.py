"""Interpretação do endereço do broker e casamento de tópicos MQTT.

Funções puras, sem I/O — a sonda de rede vive em ``discovery.py``. O objetivo é
que o operador digite o que tiver em mãos (``emqx.exemplo.com``,
``emqx.exemplo.com:8883``, ``ssl://...``, ``https://.../mqtt`` ou até a URL do
painel do EMQX) e o sistema resolva o resto.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "DEFAULT_PORTS",
    "Candidate",
    "ParsedAddress",
    "TopicBranch",
    "build_candidates",
    "match_topic",
    "match_topic_any",
    "normalize_topic_filter",
    "topic_tree",
    "validate_topic_filter",
]

# Portas padrão do MQTT (as mesmas do EMQX de fábrica).
DEFAULT_PORTS: dict[tuple[str, bool], int] = {
    ("tcp", False): 1883,
    ("tcp", True): 8883,
    ("websockets", False): 8083,
    ("websockets", True): 8084,
}

# Portas em que o EMQX serve o painel web, não MQTT. Digitar a URL do painel é
# o erro mais comum de quem copia o endereço do navegador.
DASHBOARD_PORTS: dict[int, bool] = {18083: False, 18084: True}

_SCHEMES: dict[str, tuple[str, bool]] = {
    # esquema: (transporte, tls)
    "mqtt": ("tcp", False),
    "tcp": ("tcp", False),
    "mqtts": ("tcp", True),
    "mqtt+ssl": ("tcp", True),
    "ssl": ("tcp", True),
    "tls": ("tcp", True),
    "ws": ("websockets", False),
    "http": ("websockets", False),
    "wss": ("websockets", True),
    "https": ("websockets", True),
}

DEFAULT_WS_PATH = "/mqtt"


@dataclass(slots=True)
class ParsedAddress:
    """O que dá para afirmar sobre o endereço só olhando o texto digitado.

    ``None`` em ``transport``/``tls``/``port`` significa "não foi informado" —
    quem resolve é a sonda, não um palpite.
    """

    host: str
    port: int | None = None
    transport: str | None = None
    tls: bool | None = None
    ws_path: str | None = None
    scheme: str | None = None
    dashboard_port: bool = False


@dataclass(slots=True)
class Candidate:
    """Endpoint a ser testado pela sonda."""

    host: str
    port: int
    transport: str  # tcp | websockets
    tls: bool
    ws_path: str | None = None
    reason: str = ""  # por que este candidato entrou na lista (vai para a tela)

    @property
    def label(self) -> str:
        proto = "wss" if (self.transport == "websockets" and self.tls) else (
            "ws" if self.transport == "websockets" else ("ssl" if self.tls else "tcp")
        )
        path = self.ws_path or "" if self.transport == "websockets" else ""
        return f"{proto}://{self.host}:{self.port}{path}"


def parse_broker_address(raw: str) -> ParsedAddress:
    """Quebra o endereço digitado em host/porta/transporte, sem adivinhar.

    Aceita, entre outros: ``host``, ``host:8883``, ``ssl://host:8883``,
    ``mqtts://host``, ``ws://host:8083/mqtt``, ``https://host/mqtt`` e
    ``[::1]:1883``. Levanta ``ValueError`` quando não sobra host.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("endereco_vazio")

    scheme: str | None = None
    if "://" in text:
        scheme_raw, _, text = text.partition("://")
        scheme = scheme_raw.strip().lower()
        if scheme not in _SCHEMES:
            raise ValueError(f"esquema_desconhecido:{scheme}")

    # Caminho (só faz sentido em websocket) e query/fragmento são separados aqui.
    path: str | None = None
    for sep in ("?", "#"):
        text = text.split(sep, 1)[0]
    if "/" in text:
        text, _, rest = text.partition("/")
        rest = rest.strip()
        path = "/" + rest if rest else "/"

    # Credenciais embutidas (user:pass@host) são ignoradas de propósito: senha
    # entra pelo campo próprio, que é cifrado.
    if "@" in text:
        text = text.rsplit("@", 1)[1]

    host, port = _split_host_port(text)
    if not host:
        raise ValueError("host_ausente")

    transport: str | None = None
    tls: bool | None = None
    if scheme is not None:
        transport, tls = _SCHEMES[scheme]

    parsed = ParsedAddress(
        host=host,
        port=port,
        transport=transport,
        tls=tls,
        ws_path=path,
        scheme=scheme,
        dashboard_port=port in DASHBOARD_PORTS if port else False,
    )
    return parsed


def _split_host_port(text: str) -> tuple[str, int | None]:
    text = text.strip()
    if text.startswith("["):  # IPv6 literal: [::1]:1883
        end = text.find("]")
        if end == -1:
            raise ValueError("ipv6_invalido")
        host = text[1:end]
        rest = text[end + 1 :]
        if rest.startswith(":"):
            return host, _port(rest[1:])
        return host, None
    if text.count(":") > 1:  # IPv6 sem colchetes e sem porta
        return text, None
    if ":" in text:
        host, _, port_raw = text.partition(":")
        return host.strip(), _port(port_raw)
    return text, None


def _port(raw: str) -> int:
    raw = raw.strip()
    if not raw.isdigit():
        raise ValueError(f"porta_invalida:{raw}")
    value = int(raw)
    if not 1 <= value <= 65535:
        raise ValueError(f"porta_fora_de_faixa:{value}")
    return value


def build_candidates(parsed: ParsedAddress) -> list[Candidate]:
    """Endpoints a sondar, do mais provável para o menos.

    Regras: porta digitada pelo operador sempre é testada (nos quatro
    transportes, porque só a rede diz o que ela é); porta ausente vira a
    varredura das quatro portas padrão, preferindo as cifradas. Quando a porta
    digitada é a do painel web, as portas padrão entram como alternativa — é o
    que permite responder "essa é a porta do painel; o MQTT está na 8883".
    """
    host = parsed.host
    ws_path = parsed.ws_path or DEFAULT_WS_PATH
    out: list[Candidate] = []
    seen: set[tuple[int, str, bool, str | None]] = set()

    def add(port: int, transport: str, tls: bool, reason: str) -> None:
        path = ws_path if transport == "websockets" else None
        key = (port, transport, tls, path)
        if key in seen:
            return
        seen.add(key)
        out.append(
            Candidate(host=host, port=port, transport=transport, tls=tls, ws_path=path, reason=reason)
        )

    if parsed.port is not None:
        combos: list[tuple[str, bool]]
        if parsed.transport is not None and parsed.tls is not None:
            # Esquema explícito: tenta primeiro exatamente o que foi pedido.
            combos = [(parsed.transport, parsed.tls)]
            combos += [c for c in _ALL_COMBOS if c != (parsed.transport, parsed.tls)]
        else:
            combos = list(_ALL_COMBOS)
        for transport, tls in combos:
            add(parsed.port, transport, tls, "porta informada")

    if parsed.port is None or parsed.dashboard_port:
        reason = "porta padrão" if parsed.port is None else "porta padrão (a informada é do painel web)"
        order: list[tuple[str, bool]] = list(_PREFERRED_ORDER)
        if parsed.transport is not None and parsed.tls is not None:
            # Esquema informado sem porta: a porta padrão daquele esquema vem primeiro.
            preferido = (parsed.transport, parsed.tls)
            order = [preferido] + [c for c in _PREFERRED_ORDER if c != preferido]
        for transport, tls in order:
            add(DEFAULT_PORTS[(transport, tls)], transport, tls, reason)

    return out


# Cifrado primeiro: se os dois funcionarem, o cifrado é o que fica gravado.
_PREFERRED_ORDER: tuple[tuple[str, bool], ...] = (
    ("tcp", True),
    ("tcp", False),
    ("websockets", True),
    ("websockets", False),
)
_ALL_COMBOS: tuple[tuple[str, bool], ...] = _PREFERRED_ORDER


# ── Filtros de tópico ────────────────────────────────────────────────────────


def normalize_topic_filter(raw: str) -> str:
    return (raw or "").strip().strip("\n").rstrip("/") or "#"


def validate_topic_filter(raw: str) -> str | None:  # noqa: PLR0911 - uma saída por regra do MQTT
    """Devolve o motivo de o filtro ser inválido, ou ``None`` se estiver ok.

    Regras do MQTT: ``#`` só no último nível e sozinho nele; ``+`` sozinho no
    nível; nada de nível vazio no meio.
    """
    text = (raw or "").strip()
    if not text:
        return "filtro_vazio"
    if len(text) > 512:
        return "filtro_muito_longo"
    levels = text.split("/")
    for i, level in enumerate(levels):
        if level == "":
            if len(levels) == 1:
                return "filtro_vazio"
            continue  # "/a" e "a//b" são legais no MQTT (nível vazio)
        if "#" in level:
            if level != "#":
                return "curinga_multinivel_isolado"
            if i != len(levels) - 1:
                return "curinga_multinivel_no_fim"
        if "+" in level and level != "+":
            return "curinga_um_nivel_isolado"
    return None


def match_topic(filter_: str, topic: str) -> bool:
    """Casamento MQTT: um filtro que começa com curinga não casa com ``$SYS``."""
    f = filter_.split("/")
    t = topic.split("/")
    if t and t[0].startswith("$") and f and f[0] in ("+", "#"):
        return False
    return _match_levels(f, t)


def match_topic_any(filter_: str, topic: str) -> bool:
    """Igual ao ``match_topic``, mas sem a regra do ``$`` — usado na busca sobre
    o que já está gravado, onde o operador quer achar exatamente o que pediu."""
    return _match_levels(filter_.split("/"), topic.split("/"))


def _match_levels(f: list[str], t: list[str]) -> bool:
    for i, level in enumerate(f):
        if level == "#":
            return i == len(f) - 1
        if i >= len(t):
            return False
        if level == "+":
            continue
        if level != t[i]:
            return False
    return len(f) == len(t)


@dataclass(slots=True)
class TopicBranch:
    """Ramo sugerido para assinatura, com o que foi observado nele."""

    filter: str
    messages: int = 0
    topics: int = 0
    samples: list[str] = field(default_factory=list)


def topic_tree(counts: dict[str, int], *, max_depth: int = 3) -> list[TopicBranch]:
    """Agrupa tópicos observados em ramos sugeríveis para assinatura.

    Cada ramo vira um filtro pronto (``v1/data/extenStatus/+``) com a contagem
    de mensagens e de tópicos distintos — é o que a tela mostra para o operador
    marcar o que quer gravar.
    """
    grupos: dict[str, TopicBranch] = {}
    for topic, count in counts.items():
        niveis = topic.split("/")
        pai = niveis[:-1]
        if not pai:
            filtro = topic
        elif len(pai) > max_depth:
            # Arvore muito funda: agrupa no prefixo e cobre o resto com "#".
            filtro = "/".join(pai[:max_depth]) + "/#"
        else:
            filtro = "/".join(pai) + "/+"
        ramo = grupos.setdefault(filtro, TopicBranch(filter=filtro))
        ramo.messages += count
        ramo.topics += 1
        if len(ramo.samples) < 3:
            ramo.samples.append(topic)
    return sorted(grupos.values(), key=lambda r: (-r.messages, r.filter))
