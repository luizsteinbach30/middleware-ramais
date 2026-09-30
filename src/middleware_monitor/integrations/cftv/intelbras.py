"""Intelbras e Dahua — a família de CGI com autenticação digest (``cftv.intelbras``).

Escrito pela documentação pública da API HTTP Dahua (a Intelbras usa o mesmo
firmware nos NVD/MHDX): respostas ``chave=valor``, uma por linha. **Não
homologado** até a prova num gravador real (ADR 0037, decisão 3).

O que é lido, **só com GET**:

| Caminho | Para quê | Obrigatório |
|---|---|---|
| ``magicBox.cgi?action=getSystemInfo`` | prova que é o gravador e o modelo (``deviceType``) | sim |
| ``magicBox.cgi?action=getSoftwareVersion`` | firmware (``version``) | não |
| ``configManager.cgi?action=getConfig&name=ChannelTitle`` | quantos canais, e o nome | sim |
| ``eventManager.cgi?action=getEventIndexes&code=VideoLoss`` | canais sem vídeo agora | não |
| ``configManager.cgi?action=getConfig&name=RecordMode`` | canal configurado para gravar | não |
| ``storageDevice.cgi?action=getDeviceAllInfo`` | discos: estado, total, usado | sim |
| ``eventManager.cgi?action=getEventIndexes&code=StorageLowSpace`` | disco cheio (alarme do gravador) | não |
| ``global.cgi?action=getCurrentTime`` | a hora do gravador | sim |

Decisões que não se re-derivam lendo o código:

- **``getEventIndexes`` sem evento ativo responde vazio** (a documentação só mostra
  o exemplo com eventos, ``channels[0]=2``). Aqui, e só aqui, 200 vazio quer dizer
  "nenhum canal com o evento" — as leituras obrigatórias já provaram a forma. Uma
  resposta com ``Error`` deixa o vídeo ``desconhecido``, nunca ``ok``.
- **``gravando`` é o modo configurado** (``RecordMode``: 2 = parado), não a prova de
  que há arquivo sendo escrito — a CGI não diz isso sem uma busca, e busca é mais
  pesada do que a monitoria pode ser.
- **A hora vem sem fuso** (``result=2026-9-30 10:00:00``). É lida como a hora local
  desta máquina, que está na mesma loja; o desvio mede a diferença entre as duas.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from .base import (
    DISCO_AUSENTE,
    DISCO_CHEIO,
    DISCO_DEFEITO,
    DISCO_OK,
    SEM_VIDEO,
    TIMEOUT_S,
    VIDEO_DESCONHECIDO,
    VIDEO_OK,
    Alvo,
    Canal,
    CftvAdapter,
    Disco,
    ErroDeLeitura,
    LeituraCftv,
    RespostaInvalida,
    cliente_http,
    conferir_status,
    gb,
    local_para_utc,
    obter,
)

SISTEMA = "/cgi-bin/magicBox.cgi?action=getSystemInfo"
VERSAO = "/cgi-bin/magicBox.cgi?action=getSoftwareVersion"
CANAIS = "/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle"
SEM_VIDEO_AGORA = "/cgi-bin/eventManager.cgi?action=getEventIndexes&code=VideoLoss"
MODO_DE_GRAVACAO = "/cgi-bin/configManager.cgi?action=getConfig&name=RecordMode"
DISCOS = "/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo"
DISCO_SEM_ESPACO = "/cgi-bin/eventManager.cgi?action=getEventIndexes&code=StorageLowSpace"
HORA = "/cgi-bin/global.cgi?action=getCurrentTime"

_TITULO = re.compile(r"^table\.ChannelTitle\[(\d+)\]\.Name$")
_MODO = re.compile(r"^table\.RecordMode\[(\d+)\]\.Mode$")
_INDICE = re.compile(r"^channels\[\d+\]$")
_DISCO = re.compile(r"^list\.info\[(\d+)\]\.(Name|State)$")
_DETALHE = re.compile(r"^list\.info\[(\d+)\]\.Detail\[\d+\]\.(TotalBytes|UsedBytes|IsError)$")
_ESTADOS_OK = {"success", "running", "normal", "ok"}
_ESTADOS_AUSENTE = {"offline", "notexist", "nodisk", "none"}
MODO_PARADO = "2"


def chave_valor(texto: str) -> dict[str, str]:
    """``chave=valor`` por linha; linha sem ``=`` é ignorada."""
    saida: dict[str, str] = {}
    for linha in texto.replace("\r", "").split("\n"):
        if "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        saida[chave.strip()] = valor.strip()
    return saida


def indices_do_evento(texto: str) -> set[int] | None:
    """Os canais (a partir de 0) com o evento ativo. ``None`` quando a resposta não tem
    a forma (``Error``, lixo): quem chama deixa o estado ``desconhecido``."""
    corpo = texto.strip()
    if not corpo:
        return set()
    pares = chave_valor(corpo)
    linhas = [ln for ln in corpo.replace("\r", "").split("\n") if ln.strip()]
    if not pares or len(pares) != len(linhas) or not all(_INDICE.match(k) for k in pares):
        return None
    try:
        return {int(v) for v in pares.values()}
    except ValueError:
        return None


def hora_local(texto: str) -> datetime:
    valor = chave_valor(texto).get("result")
    if not valor:
        raise RespostaInvalida("a hora do gravador veio sem result=")
    try:
        local = datetime.strptime(valor, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise RespostaInvalida(f"a hora do gravador não é uma data: {valor[:40]!r}") from exc
    return local_para_utc(local)


def discos(texto: str, *, sem_espaco: bool) -> list[Disco]:
    pares = chave_valor(texto)
    info: dict[int, dict[str, str]] = {}
    total: dict[int, float] = {}
    usado: dict[int, float] = {}
    com_erro: set[int] = set()
    for chave, valor in pares.items():
        if m := _DISCO.match(chave):
            info.setdefault(int(m.group(1)), {})[m.group(2)] = valor
        elif m := _DETALHE.match(chave):
            i, campo = int(m.group(1)), m.group(2)
            info.setdefault(i, {})
            if campo == "IsError":
                if valor.lower() == "true":
                    com_erro.add(i)
                continue
            try:
                numero = float(valor)
            except ValueError:
                continue
            alvo = total if campo == "TotalBytes" else usado
            alvo[i] = alvo.get(i, 0.0) + numero
    if not info and "list" not in texto:
        raise RespostaInvalida("a lista de discos não tem a forma list.info[…]")
    saida = []
    for i in sorted(info):
        estado_bruto = (info[i].get("State") or "").strip().lower()
        if i in com_erro:
            estado = DISCO_DEFEITO
        elif estado_bruto in _ESTADOS_AUSENTE:
            estado = DISCO_AUSENTE
        elif estado_bruto in _ESTADOS_OK:
            estado = DISCO_CHEIO if sem_espaco else DISCO_OK
        else:
            estado = DISCO_DEFEITO
        tot = total.get(i)
        livre = (tot - usado.get(i, 0.0)) if tot is not None else None
        saida.append(Disco(numero=i + 1, estado=estado, total_gb=gb(tot), livre_gb=gb(livre)))
    return saida


class IntelbrasAdapter(CftvAdapter):
    adaptador_id = "intelbras"
    modulo = "cftv.intelbras"
    tipo_de_credencial = "HTTP"

    async def ler(self, alvo: Alvo, *, timeout_s: float = TIMEOUT_S) -> LeituraCftv:
        async with cliente_http(timeout_s, auth=httpx.DigestAuth(alvo.usuario, alvo.senha)) as cli:

            async def texto(caminho: str) -> str:
                r = await obter(cli, alvo.base + caminho, caminho.split("?", maxsplit=1)[0])
                conferir_status(r, caminho.split("?", maxsplit=1)[0])
                return r.text

            async def opcional(caminho: str) -> str | None:
                r = await obter(cli, alvo.base + caminho, caminho.split("?", maxsplit=1)[0])
                if r.status_code in (401, 403):
                    conferir_status(r, caminho)
                return r.text if r.status_code == 200 else None

            sistema = chave_valor(await texto(SISTEMA))
            if not (sistema.get("deviceType") or sistema.get("serialNumber")):
                raise RespostaInvalida("getSystemInfo sem deviceType nem serialNumber: não é um gravador")
            versao = chave_valor(await opcional(VERSAO) or "").get("version")

            titulos = {
                int(m.group(1)): valor
                for chave, valor in chave_valor(await texto(CANAIS)).items()
                if (m := _TITULO.match(chave))
            }
            if not titulos:
                raise RespostaInvalida("ChannelTitle sem nenhum canal")

            bruto_sem_video = await opcional(SEM_VIDEO_AGORA)
            sem_video = indices_do_evento(bruto_sem_video) if bruto_sem_video is not None else None
            modos = {
                int(m.group(1)): valor
                for chave, valor in chave_valor(await opcional(MODO_DE_GRAVACAO) or "").items()
                if (m := _MODO.match(chave))
            }
            bruto_espaco = await opcional(DISCO_SEM_ESPACO)
            espaco = indices_do_evento(bruto_espaco) if bruto_espaco is not None else None
            lista_de_discos = discos(await texto(DISCOS), sem_espaco=bool(espaco))
            hora = hora_local(await texto(HORA))

        canais = [
            Canal(
                numero=i + 1,
                nome=titulos[i] or None,
                video=(
                    VIDEO_DESCONHECIDO if sem_video is None else SEM_VIDEO if i in sem_video else VIDEO_OK
                ),
                gravando=(modos[i] != MODO_PARADO) if i in modos else None,
            )
            for i in sorted(titulos)
        ]
        return LeituraCftv(
            canais=canais,
            discos=lista_de_discos,
            hora_do_gravador=hora,
            modelo=sistema.get("deviceType") or None,
            firmware=versao or None,
        )


__all__ = ["ErroDeLeitura", "IntelbrasAdapter"]
