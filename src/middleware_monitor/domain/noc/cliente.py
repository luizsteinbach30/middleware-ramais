"""O HTTP de saída para o canal do agente (``/agente/v1``).

Três escolhas:

- **Um verbo por função, e o erro com nome.** O NOC responde
  ``{ mensagem, codigo }``; o código vira ``ErroDoNoc.codigo`` para o job decidir
  (``AGENTE_REVOGADO`` para, ``CREDENCIAL_INVALIDA`` continua), e a mensagem vai
  inteira para a tela.
- **TLS com a validação padrão do httpx (certifi).** Nada de ``verify=False`` e
  nada do pinning do MQTT, que confere o certificado *depois* de a credencial ter
  ido no CONNECT. O NOC tem certificado público.
- **Timeout curto (20 s)**, menor que o intervalo do heartbeat: o scheduler roda
  com ``max_instances=1``, e um pedido pendurado pularia os seguintes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from middleware_monitor.version import __version__

TIMEOUT_S = 20.0


class ErroDoNoc(Exception):
    """Falha falando com o NOC. ``codigo`` vem do NOC ou é local (``SEM_CONEXAO``)."""

    def __init__(self, codigo: str, mensagem: str, status: int | None = None) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem
        self.status = status


@dataclass(frozen=True)
class Enrolado:
    agente_id: str
    credencial: str
    intervalo_s: int


def normalizar_url(url: str) -> str:
    """``noc.workconnect.com.br/`` → ``https://noc.workconnect.com.br``.

    Sem esquema vira **https**. ``http://`` só é aceito como veio (homologação
    no lab), e a tela avisa: o segredo do agente viaja nesse canal.
    """
    limpa = (url or "").strip().rstrip("/")
    if not limpa:
        raise ValueError("Informe o endereço do NOC.")
    if "://" not in limpa:
        limpa = f"https://{limpa}"
    esquema = limpa.split("://", 1)[0].lower()
    if esquema not in {"http", "https"}:
        raise ValueError("O endereço do NOC precisa começar com https://")
    return limpa


def _cabecalhos(credencial: str | None = None) -> dict[str, str]:
    h = {"User-Agent": f"MiddlewareMonitor/{__version__}", "Accept": "application/json"}
    if credencial:
        h["Authorization"] = f"Bearer {credencial}"
    return h


async def _pedir(
    url: str, caminho: str, corpo: dict[str, Any], credencial: str | None = None
) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as http:
            resposta = await http.post(f"{url}{caminho}", json=corpo, headers=_cabecalhos(credencial))
    except httpx.TimeoutException as exc:
        raise ErroDoNoc("SEM_CONEXAO", f"O NOC não respondeu em {int(TIMEOUT_S)} s.") from exc
    except httpx.HTTPError as exc:
        raise ErroDoNoc("SEM_CONEXAO", f"Não foi possível falar com o NOC: {type(exc).__name__}.") from exc

    if resposta.status_code >= 400:
        codigo, mensagem = "ERRO_HTTP", f"O NOC respondeu {resposta.status_code}."
        try:
            dado = resposta.json()
            codigo = str(dado.get("codigo") or codigo)
            mensagem = str(dado.get("mensagem") or mensagem)
        except ValueError:
            # Resposta que não é JSON (proxy, página de erro): o status diz mais
            # do que um "erro inesperado".
            pass
        raise ErroDoNoc(codigo, mensagem, resposta.status_code)
    return resposta


def _json(resposta: httpx.Response) -> dict[str, Any]:
    """Um 2xx precisa trazer o corpo esperado — resposta vazia não é sucesso."""
    try:
        dado = resposta.json()
    except ValueError as exc:
        raise ErroDoNoc(
            "RESPOSTA_INVALIDA", "O NOC respondeu sem JSON — o endereço aponta mesmo para o NOC?"
        ) from exc
    if not isinstance(dado, dict):
        raise ErroDoNoc("RESPOSTA_INVALIDA", "O NOC respondeu num formato inesperado.")
    return dado


async def enrolar(url: str, *, codigo: str, maquina: str, sistema: str) -> Enrolado:
    resposta = await _pedir(
        url,
        "/agente/v1/enrolar",
        {
            "codigo": codigo,
            "maquina": maquina,
            "versao": __version__,
            "sistema": sistema,
        },
    )
    dado = _json(resposta)
    agente_id, credencial = dado.get("agenteId"), dado.get("credencial")
    if (
        not isinstance(agente_id, str)
        or not isinstance(credencial, str)
        or not credencial.startswith(f"{agente_id}.")
    ):
        raise ErroDoNoc("RESPOSTA_INVALIDA", "O NOC aceitou o código mas não devolveu uma credencial válida.")
    intervalo = dado.get("intervaloHeartbeatS")
    return Enrolado(agente_id, credencial, intervalo if isinstance(intervalo, int) else 60)


async def heartbeat(url: str, credencial: str, *, relogio_iso: str, manifesto_sha256: str) -> dict[str, Any]:
    resposta = await _pedir(
        url,
        "/agente/v1/heartbeat",
        {
            "versao": __version__,
            "relogio": relogio_iso,
            "manifestoSha256": manifesto_sha256,
        },
        credencial,
    )
    dado = _json(resposta)
    if not isinstance(dado.get("intervaloHeartbeatS"), int):
        raise ErroDoNoc("RESPOSTA_INVALIDA", "Resposta do heartbeat sem o intervalo.")
    return dado


async def enviar_manifesto(url: str, credencial: str, manifesto: dict[str, Any]) -> None:
    await _pedir(url, "/agente/v1/manifesto", manifesto, credencial)
