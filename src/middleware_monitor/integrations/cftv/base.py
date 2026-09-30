"""O contrato dos adaptadores de CFTV (AGENTE-NOC item 23, CONTRATO §13.9).

No molde do ``VendorAdapter`` do configurador de ramais, com uma diferença que é
a regra do dono: **só leitura**. Não existe ``send_config`` nem ``execute_action``
aqui — o adaptador só sabe ``ler``, e ``ler`` só faz ``GET`` (o UniFi faz também
o ``POST`` de login, que é autenticação, não configuração).

As regras que não se re-derivam lendo o código:

- **Resposta vazia não é sucesso, e 200 não prova nada.** Cada adaptador confere
  a forma do que leu (a chave que tem de estar lá, a raiz do XML, o objeto do
  JSON) e levanta ``RespostaInvalida`` quando não tem.
- **Senha recusada é erro próprio** (``SenhaRecusada``): o job segura novas
  tentativas com a mesma credencial, porque gravador bloqueia o usuário depois
  de algumas senhas erradas seguidas, e o dono ficaria sem acesso à própria
  loja por causa da monitoria.
- **Sem proxy do sistema e sem conferir certificado**: o gravador está na rede da
  loja, quase sempre com certificado autoassinado, como a sonda HTTP (item 19).
- Adaptador escrito só pela documentação sai ``homologado = False`` até a prova
  em equipamento real (ADR 0037, decisão 3).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

TIMEOUT_S = 8.0

VIDEO_OK = "ok"
SEM_VIDEO = "sem_video"
VIDEO_DESCONHECIDO = "desconhecido"

DISCO_OK = "ok"
DISCO_DEFEITO = "defeito"
DISCO_CHEIO = "cheio"
DISCO_AUSENTE = "ausente"


class ErroDeLeitura(RuntimeError):
    """A leitura não aconteceu; a mensagem vai para ``ultimoErro`` (tela do NOC)."""


class SenhaRecusada(ErroDeLeitura):
    def __init__(self, mensagem: str = "senha recusada") -> None:
        super().__init__(mensagem)


class RespostaInvalida(ErroDeLeitura):
    """HTTP 200 (ou outro) com um corpo que não tem a forma que a documentação descreve."""


@dataclass(frozen=True)
class Canal:
    numero: int
    nome: str | None
    video: str  # ok | sem_video | desconhecido
    gravando: bool | None


@dataclass(frozen=True)
class Disco:
    numero: int
    estado: str  # ok | defeito | cheio | ausente
    total_gb: float | None
    livre_gb: float | None


@dataclass(frozen=True)
class LeituraCftv:
    canais: list[Canal]
    discos: list[Disco]
    # Em UTC, sem fuso. ``None`` quando o equipamento não informou.
    hora_do_gravador: datetime | None
    dias_guardados: int | None = None
    modelo: str | None = None
    firmware: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Alvo:
    """Onde ler. ``usuario``/``senha`` nunca aparecem em ``repr`` (log, exceção)."""

    host: str
    esquema: str
    porta: int
    usuario: str = field(repr=False)
    senha: str = field(repr=False)

    @property
    def base(self) -> str:
        endereco = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"{self.esquema}://{endereco}:{self.porta}"


class CftvAdapter(ABC):
    #: ``intelbras`` | ``hikvision`` | ``unifi`` — o ``parametros.adaptador`` da verificação.
    adaptador_id: str = ""
    #: O módulo do manifesto que precisa estar aceito (CONTRATO §13.8).
    modulo: str = ""
    #: O tipo de credencial que o adaptador usa (ADR 0037 do NOC).
    tipo_de_credencial: str = "HTTP"

    @abstractmethod
    async def ler(self, alvo: Alvo, *, timeout_s: float = TIMEOUT_S) -> LeituraCftv:
        """Lê canais, discos e hora. Só leitura. Levanta ``ErroDeLeitura``."""


def cliente_http(
    timeout_s: float, *, auth: httpx.Auth | None = None, cookies: httpx.Cookies | None = None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        auth=auth,
        cookies=cookies,
        verify=False,  # noqa: S501 - gravador da loja, certificado autoassinado (item 19)
        timeout=timeout_s,
        follow_redirects=False,
        trust_env=False,
    )


async def obter(cliente: httpx.AsyncClient, url: str, caminho: str) -> httpx.Response:
    """Um ``GET``, com os erros de caminho traduzidos para a tela."""
    try:
        return await cliente.get(url)
    except httpx.TimeoutException as exc:
        raise ErroDeLeitura(f"o gravador não respondeu a tempo em {caminho}") from exc
    except httpx.ConnectError as exc:
        raise ErroDeLeitura(f"não conectou ao gravador: {str(exc).strip() or 'conexão recusada'}") from exc
    except httpx.HTTPError as exc:
        raise ErroDeLeitura(f"{type(exc).__name__} em {caminho}") from exc


def conferir_status(resposta: httpx.Response, caminho: str) -> None:
    if resposta.status_code in (401, 403):
        raise SenhaRecusada()
    if resposta.status_code != 200:
        raise ErroDeLeitura(f"HTTP {resposta.status_code} em {caminho}")
    if not resposta.content.strip():
        raise RespostaInvalida(f"HTTP 200 com a resposta vazia em {caminho}")


def gb(valor_bytes: float | None) -> float | None:
    """Bytes → GB (10⁹, como o fabricante anuncia o disco), com uma casa."""
    if valor_bytes is None:
        return None
    return round(valor_bytes / 1_000_000_000, 1)


def local_para_utc(local: datetime) -> datetime:
    """Hora sem fuso lida como a hora local DESTA máquina (que está na mesma loja
    do gravador) → UTC sem fuso, como o resto do lote. Com fuso, só converte."""
    return local.astimezone(UTC).replace(tzinfo=None)
