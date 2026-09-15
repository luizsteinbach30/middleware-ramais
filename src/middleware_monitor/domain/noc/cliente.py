"""O HTTP de saída para o canal do agente (``/agente/v1``).

Três escolhas:

- **Um verbo por função, e o erro com nome.** O NOC responde
  ``{ mensagem, codigo }``; o código vira ``ErroDoNoc.codigo`` para o job decidir
  (``AGENTE_REVOGADO`` para, ``CREDENCIAL_INVALIDA`` continua), e a mensagem vai
  inteira para a tela.
- **TLS sempre conferido** (certifi, mais a autoridade de laboratório quando
  existe). Nada de ``verify=False``. No canal, o mesmo contexto apresenta o
  certificado do agente — mTLS, ADR 0006 do NOC.
- **Timeout curto (20 s)**, menor que o intervalo do heartbeat: o scheduler roda
  com ``max_instances=1``, e um pedido pendurado pularia os seguintes.
"""

from __future__ import annotations

import gzip
import json
import ssl
from dataclasses import dataclass
from typing import Any

import httpx

from middleware_monitor.domain.noc import certificado as cert
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
    certificado_pem: str
    url_canal: str | None


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
    url: str,
    caminho: str,
    corpo: dict[str, Any],
    credencial: str | None = None,
    *,
    tls: ssl.SSLContext | None = None,
) -> httpx.Response:
    try:
        contexto = tls or cert.contexto_do_servidor()
        async with httpx.AsyncClient(timeout=TIMEOUT_S, verify=contexto) as http:
            resposta = await http.post(f"{url}{caminho}", json=corpo, headers=_cabecalhos(credencial))
    except httpx.TimeoutException as exc:
        raise ErroDoNoc("SEM_CONEXAO", f"O NOC não respondeu em {int(TIMEOUT_S)} s.") from exc
    except httpx.ConnectError as exc:
        # TLS recusado (certificado do NOC não confere) chega aqui como ConnectError:
        # a mensagem da exceção diz qual dos dois.
        raise ErroDoNoc("SEM_CONEXAO", f"Não foi possível falar com o NOC: {exc}") from exc
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


async def enrolar(url: str, *, codigo: str, maquina: str, sistema: str, csr: str) -> Enrolado:
    resposta = await _pedir(
        url,
        "/agente/v1/enrolar",
        {
            "codigo": codigo,
            "maquina": maquina,
            "versao": __version__,
            "sistema": sistema,
            "csr": csr,
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
    certificado_pem = dado.get("certificado")
    if not isinstance(certificado_pem, str) or "BEGIN CERTIFICATE" not in certificado_pem:
        raise ErroDoNoc(
            "RESPOSTA_INVALIDA", "O NOC aceitou o código mas não devolveu o certificado do agente."
        )
    intervalo = dado.get("intervaloHeartbeatS")
    canal = dado.get("urlDoCanal")
    return Enrolado(
        agente_id,
        credencial,
        intervalo if isinstance(intervalo, int) else 60,
        certificado_pem,
        normalizar_url(canal) if isinstance(canal, str) and canal else None,
    )


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
        tls=cert.contexto_do_agente(),
    )
    dado = _json(resposta)
    if not isinstance(dado.get("intervaloHeartbeatS"), int):
        raise ErroDoNoc("RESPOSTA_INVALIDA", "Resposta do heartbeat sem o intervalo.")
    return dado


async def enviar_manifesto(url: str, credencial: str, manifesto: dict[str, Any]) -> None:
    await _pedir(url, "/agente/v1/manifesto", manifesto, credencial, tls=cert.contexto_do_agente())


async def renovar_certificado(url: str, credencial: str, csr: str) -> str:
    """Pede o certificado novo com o que ainda vale. Devolve o PEM."""
    resposta = await _pedir(
        url, "/agente/v1/certificado", {"csr": csr}, credencial, tls=cert.contexto_do_agente()
    )
    dado = _json(resposta)
    pem = dado.get("certificado")
    if not isinstance(pem, str) or "BEGIN CERTIFICATE" not in pem:
        raise ErroDoNoc("RESPOSTA_INVALIDA", "A renovação não devolveu certificado.")
    return pem


async def enviar_telemetria(url: str, credencial: str, lote: dict[str, Any]) -> bool:
    """Envia um lote em gzip com ``Idempotency-Key`` = id do lote. Devolve se o NOC
    disse que era duplicado (o lote já tinha chegado — o cursor pode avançar)."""
    corpo = gzip.compress(json.dumps(lote, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    cabecalhos = {
        **_cabecalhos(credencial),
        "Content-Type": "application/json",
        "Content-Encoding": "gzip",
        "Idempotency-Key": str(lote["lote"]),
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S * 3, verify=cert.contexto_do_agente()) as http:
            resposta = await http.post(f"{url}/agente/v1/telemetria", content=corpo, headers=cabecalhos)
    except httpx.TimeoutException as exc:
        raise ErroDoNoc("SEM_CONEXAO", "O NOC não respondeu a tempo ao lote de telemetria.") from exc
    except httpx.HTTPError as exc:
        raise ErroDoNoc("SEM_CONEXAO", f"Não foi possível entregar a telemetria: {exc}") from exc
    if resposta.status_code != 202:
        codigo, mensagem = "ERRO_HTTP", f"O NOC respondeu {resposta.status_code} ao lote."
        try:
            dado = resposta.json()
            codigo = str(dado.get("codigo") or codigo)
            mensagem = str(dado.get("mensagem") or mensagem)
        except ValueError:
            pass
        raise ErroDoNoc(codigo, mensagem, resposta.status_code)
    # 202 sem o corpo esperado não é entrega confirmada.
    dado = _json(resposta)
    if dado.get("recebido") is not True:
        raise ErroDoNoc("RESPOSTA_INVALIDA", "O NOC respondeu 202 sem confirmar o recebimento do lote.")
    return dado.get("duplicado") is True
