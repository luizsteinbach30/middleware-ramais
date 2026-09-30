"""Hikvision — ISAPI, digest, XML (``cftv.hikvision``).

Escrito pela documentação pública da ISAPI. **Não homologado** até a prova num
gravador real (ADR 0037, decisão 3). XML com a biblioteca padrão
(``xml.etree``), sem dependência nova; o namespace (``ver10``/``ver20``) é
ignorado — o que se confere é o nome local das tags.

O que é lido, **só com GET**:

| Caminho | Para quê | Obrigatório |
|---|---|---|
| ``/ISAPI/System/deviceInfo`` | prova que é o gravador; modelo e firmware | sim |
| ``/ISAPI/ContentMgmt/InputProxy/channels/status`` | canais IP: ``online`` | sim, ou o próximo |
| ``/ISAPI/System/Video/inputs/channels`` | canais analógicos: ``resDesc`` = ``NO VIDEO`` | sem o de cima |
| ``/ISAPI/ContentMgmt/InputProxy/channels`` | nome dos canais IP | não |
| ``/ISAPI/ContentMgmt/record/tracks`` | canal configurado para gravar (``Enable``) | não |
| ``/ISAPI/ContentMgmt/Storage/hdd`` | discos: ``status``, ``capacity``, ``freeSpace`` (MB) | sim |
| ``/ISAPI/System/time`` | ``localTime``, com fuso | sim |

Decisões que não se re-derivam lendo o código:

- **``gravando`` é a trilha habilitada** (``Track/Enable``), não a prova de gravação:
  saber se há arquivo sendo escrito pede uma busca (``POST /ISAPI/ContentMgmt/search``),
  e a monitoria não faz POST no gravador.
- **Disco:** ``ok``/``idle`` = ok; ``notexist`` = ausente; ``full`` = cheio (estado que
  alguns firmwares informam — a confirmar no laboratório); o resto (``error``,
  ``smartFailed``, ``unformatted``, ``mismatch``, ``offline``…) = defeito.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
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
    LeituraCftv,
    RespostaInvalida,
    cliente_http,
    conferir_status,
    gb,
    local_para_utc,
    obter,
)

INFO = "/ISAPI/System/deviceInfo"
CANAIS_IP = "/ISAPI/ContentMgmt/InputProxy/channels/status"
NOMES_IP = "/ISAPI/ContentMgmt/InputProxy/channels"
CANAIS_ANALOGICOS = "/ISAPI/System/Video/inputs/channels"
TRILHAS = "/ISAPI/ContentMgmt/record/tracks"
DISCOS = "/ISAPI/ContentMgmt/Storage/hdd"
HORA = "/ISAPI/System/time"

_BYTES_POR_MB = 1_048_576
_DISCO_OK = {"ok", "idle"}
_DISCO_AUSENTE = {"notexist"}
_DISCO_CHEIO = {"full"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def raiz(texto: str, esperada: str, caminho: str) -> ET.Element:
    if "<!DOCTYPE" in texto or "<!ENTITY" in texto:
        raise RespostaInvalida(f"{caminho}: XML com DTD recusado")
    try:
        elemento = ET.fromstring(texto)  # noqa: S314 - DTD/entidade recusados acima
    except ET.ParseError as exc:
        raise RespostaInvalida(f"{caminho}: a resposta não é XML") from exc
    if _local(elemento.tag) != esperada:
        raise RespostaInvalida(f"{caminho}: esperado <{esperada}>, veio <{_local(elemento.tag)}>")
    return elemento


def filhos(el: ET.Element, nome: str) -> list[ET.Element]:
    return [f for f in el if _local(f.tag) == nome]


def texto_de(el: ET.Element, nome: str) -> str | None:
    """O texto da tag ``nome``: filho direto primeiro (a ``Track`` tem ``id`` também
    dentro da agenda), depois qualquer descendente."""
    for f in el:
        if _local(f.tag) == nome:
            return (f.text or "").strip()
    for f in el.iter():
        if f is not el and _local(f.tag) == nome:
            return (f.text or "").strip()
    return None


def _inteiro(valor: str | None) -> int | None:
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def canais_ip(xml: ET.Element) -> dict[int, str]:
    saida = {}
    for st in filhos(xml, "InputProxyChannelStatus"):
        numero = _inteiro(texto_de(st, "id"))
        if numero is None:
            continue
        online = (texto_de(st, "online") or "").lower()
        saida[numero] = (
            VIDEO_OK if online == "true" else SEM_VIDEO if online == "false" else VIDEO_DESCONHECIDO
        )
    return saida


def canais_analogicos(xml: ET.Element) -> tuple[dict[int, str], dict[int, str | None]]:
    video, nomes = {}, {}
    for ch in filhos(xml, "VideoInputChannel"):
        numero = _inteiro(texto_de(ch, "id"))
        if numero is None:
            continue
        res = texto_de(ch, "resDesc")
        video[numero] = (
            VIDEO_DESCONHECIDO if res is None else SEM_VIDEO if "NO VIDEO" in res.upper() else VIDEO_OK
        )
        nomes[numero] = texto_de(ch, "name") or None
    return video, nomes


def trilhas(xml: ET.Element) -> dict[int, bool]:
    """Canal → trilha principal habilitada. A trilha ``101`` é o canal 1, fluxo principal."""
    saida: dict[int, bool] = {}
    for tr in filhos(xml, "Track"):
        tid = _inteiro(texto_de(tr, "id"))
        canal = _inteiro(texto_de(tr, "Channel"))
        if tid is None or tid % 100 != 1:
            continue
        canal = canal if canal is not None else tid // 100
        saida[canal] = (texto_de(tr, "Enable") or "").lower() == "true"
    return saida


def discos(xml: ET.Element) -> list[Disco]:
    saida = []
    for posicao, hd in enumerate(filhos(xml, "hdd"), start=1):
        numero = _inteiro(texto_de(hd, "id")) or posicao
        status = (texto_de(hd, "status") or "").strip()
        chave = status.lower()
        estado = (
            DISCO_OK
            if chave in _DISCO_OK
            else DISCO_AUSENTE
            if chave in _DISCO_AUSENTE
            else DISCO_CHEIO
            if chave in _DISCO_CHEIO
            else DISCO_DEFEITO
        )
        capacidade = _inteiro(texto_de(hd, "capacity"))
        livre = _inteiro(texto_de(hd, "freeSpace"))
        saida.append(
            Disco(
                numero=numero,
                estado=estado,
                total_gb=gb(capacidade * _BYTES_POR_MB) if capacidade is not None else None,
                livre_gb=gb(livre * _BYTES_POR_MB) if livre is not None else None,
            )
        )
    return saida


def hora(xml: ET.Element) -> datetime:
    valor = texto_de(xml, "localTime")
    if not valor:
        raise RespostaInvalida(f"{HORA}: sem localTime")
    try:
        lida = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RespostaInvalida(f"{HORA}: localTime não é uma data: {valor[:40]!r}") from exc
    return local_para_utc(lida)


class HikvisionAdapter(CftvAdapter):
    adaptador_id = "hikvision"
    modulo = "cftv.hikvision"
    tipo_de_credencial = "HTTP"

    async def ler(self, alvo: Alvo, *, timeout_s: float = TIMEOUT_S) -> LeituraCftv:
        async with cliente_http(timeout_s, auth=httpx.DigestAuth(alvo.usuario, alvo.senha)) as cli:

            async def xml(caminho: str, esperada: str) -> ET.Element:
                r = await obter(cli, alvo.base + caminho, caminho)
                conferir_status(r, caminho)
                return raiz(r.text, esperada, caminho)

            async def opcional(caminho: str, esperada: str) -> ET.Element | None:
                r = await obter(cli, alvo.base + caminho, caminho)
                if r.status_code == 401:
                    conferir_status(r, caminho)
                if r.status_code != 200 or not r.content.strip():
                    return None
                try:
                    return raiz(r.text, esperada, caminho)
                except RespostaInvalida:
                    return None

            info = await xml(INFO, "DeviceInfo")
            if not (texto_de(info, "model") or texto_de(info, "serialNumber")):
                raise RespostaInvalida(f"{INFO}: sem model nem serialNumber")

            nomes: dict[int, str | None] = {}
            status_ip = await opcional(CANAIS_IP, "InputProxyChannelStatusList")
            if status_ip is not None:
                video = canais_ip(status_ip)
                lista_de_nomes = await opcional(NOMES_IP, "InputProxyChannelList")
                if lista_de_nomes is not None:
                    for ch in filhos(lista_de_nomes, "InputProxyChannel"):
                        numero = _inteiro(texto_de(ch, "id"))
                        if numero is not None:
                            nomes[numero] = texto_de(ch, "name") or None
            else:
                video, nomes = canais_analogicos(await xml(CANAIS_ANALOGICOS, "VideoInputChannelList"))
            if not video:
                raise RespostaInvalida("o gravador não listou nenhum canal")

            lista_de_trilhas = await opcional(TRILHAS, "TrackList")
            gravando = trilhas(lista_de_trilhas) if lista_de_trilhas is not None else {}
            lista_de_discos = discos(await xml(DISCOS, "hddList"))
            hora_lida = hora(await xml(HORA, "Time"))

        canais = [
            Canal(numero=n, nome=nomes.get(n), video=video[n], gravando=gravando.get(n))
            for n in sorted(video)
        ]
        return LeituraCftv(
            canais=canais,
            discos=lista_de_discos,
            hora_do_gravador=hora_lida,
            modelo=texto_de(info, "model") or None,
            firmware=texto_de(info, "firmwareVersion") or None,
        )
