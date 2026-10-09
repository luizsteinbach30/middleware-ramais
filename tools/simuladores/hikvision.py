"""Simulador de gravador Hikvision — ISAPI com digest, respostas em XML.

Responde os caminhos que o adaptador ``hikvision`` lê, no formato da documentação
da ISAPI (namespace ``ver20``). Subir: ``python tools/simuladores/hikvision.py --porta 18082``.

``"analogico": true`` no cenário faz o gravador se comportar como DVR: o caminho
dos canais IP responde 404 e os canais saem de ``/ISAPI/System/Video/inputs/channels``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _comum import Manipulador, Simulador, montar_servidor, rodar

__all__ = ["PORTA_PADRAO", "SimuladorHikvision", "montar_servidor"]

PORTA_PADRAO = 18082
_NS = 'xmlns="http://www.hikvision.com/ver20/XMLSchema" version="2.0"'
_MB_POR_GB = 1_000_000_000 / 1_048_576


class SimuladorHikvision(Simulador):
    fabricante = "hikvision"
    realm = "DS-7616NI"

    def responder(self, h: Manipulador, metodo: str) -> None:
        if metodo != "GET":
            h.enviar(405, "Method Not Allowed")
            return
        if not self.digest_confere(metodo, h.headers.get("Authorization")):
            h.pedir_digest()
            return
        est = self.ler()
        rota = _ROTAS.get(h.path.split("?")[0])
        if rota is None or (est.get("analogico") and h.path.startswith("/ISAPI/ContentMgmt/InputProxy")):
            h.enviar(
                404,
                _xml(
                    "ResponseStatus",
                    "<statusCode>4</statusCode><statusString>Invalid Operation</statusString>",
                ),
                "application/xml",
            )
            return
        if rota is _info and est.get("resposta") == "vazia":
            h.enviar(200, "", "application/xml")
            return
        if rota is _info and est.get("resposta") == "malformada":
            h.enviar(200, "<html><body>login</body></html>", "text/html")
            return
        h.enviar(200, rota(self, est), "application/xml")


def _xml(raiz: str, corpo: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<{raiz} {_NS}>{corpo}</{raiz}>'


def _info(_s: SimuladorHikvision, _e: dict[str, Any]) -> str:
    return _xml(
        "DeviceInfo",
        "<deviceName>NVR Simulado</deviceName><deviceID>sim</deviceID><model>DS-7616NI-K2</model>"
        "<serialNumber>DS-7616NI-SIM0001</serialNumber><firmwareVersion>V4.30.085</firmwareVersion>"
        "<deviceType>NVR</deviceType>",
    )


def _status_ip(_s: SimuladorHikvision, e: dict[str, Any]) -> str:
    sem = set(e.get("semVideo") or [])
    itens = "".join(
        f"<InputProxyChannelStatus><id>{n}</id><online>{'false' if n in sem else 'true'}</online>"
        f"<chanDetectResult>{'offline' if n in sem else 'connect'}</chanDetectResult>"
        "</InputProxyChannelStatus>"
        for n in range(1, int(e["canais"]) + 1)
    )
    return _xml("InputProxyChannelStatusList", itens)


def _nomes_ip(_s: SimuladorHikvision, e: dict[str, Any]) -> str:
    itens = "".join(
        f"<InputProxyChannel><id>{n}</id><name>{escape(f'Camera {n:02d}')}</name></InputProxyChannel>"
        for n in range(1, int(e["canais"]) + 1)
    )
    return _xml("InputProxyChannelList", itens)


def _analogicos(_s: SimuladorHikvision, e: dict[str, Any]) -> str:
    sem = set(e.get("semVideo") or [])
    itens = "".join(
        f"<VideoInputChannel><id>{n}</id><inputPort>{n}</inputPort><name>Camera {n:02d}</name>"
        "<videoInputEnabled>true</videoInputEnabled>"
        f"<resDesc>{'NO VIDEO' if n in sem else '1920*1080P25'}</resDesc>"
        "</VideoInputChannel>"
        for n in range(1, int(e["canais"]) + 1)
    )
    return _xml("VideoInputChannelList", itens)


def _trilhas(_s: SimuladorHikvision, e: dict[str, Any]) -> str:
    parados = set(e.get("naoGravando") or [])
    itens = "".join(
        f"<Track><id>{n}01</id><Channel>{n}</Channel><Enable>{'false' if n in parados else 'true'}</Enable>"
        "<TrackSchedule><ScheduleBlock><ScheduleAction><id>1</id></ScheduleAction></ScheduleBlock></TrackSchedule>"
        "</Track>"
        for n in range(1, int(e["canais"]) + 1)
    )
    return _xml("TrackList", itens)


def _discos(_s: SimuladorHikvision, e: dict[str, Any]) -> str:
    status = {"ok": "ok", "defeito": "error", "cheio": "full", "ausente": "notexist"}
    itens = "".join(
        f"<hdd><id>{i}</id><hddName>hdd{i}</hddName><hddType>SATA</hddType>"
        f"<status>{status.get(d.get('estado', 'ok'), 'error')}</status>"
        f"<capacity>{round(float(d.get('totalGb', 0)) * _MB_POR_GB)}</capacity>"
        f"<freeSpace>{round(float(d.get('livreGb', 0)) * _MB_POR_GB)}</freeSpace>"
        "<property>RW</property></hdd>"
        for i, d in enumerate(e.get("discos") or [], start=1)
    )
    return _xml("hddList", itens)


def _hora(s: SimuladorHikvision, _e: dict[str, Any]) -> str:
    agora = s.hora_local().replace(microsecond=0)
    return _xml(
        "Time",
        f"<timeMode>NTP</timeMode><localTime>{agora.isoformat()}</localTime><timeZone>CST+3:00:00</timeZone>",
    )


_ROTAS = {
    "/ISAPI/System/deviceInfo": _info,
    "/ISAPI/ContentMgmt/InputProxy/channels/status": _status_ip,
    "/ISAPI/ContentMgmt/InputProxy/channels": _nomes_ip,
    "/ISAPI/System/Video/inputs/channels": _analogicos,
    "/ISAPI/ContentMgmt/record/tracks": _trilhas,
    "/ISAPI/ContentMgmt/Storage/hdd": _discos,
    "/ISAPI/System/time": _hora,
}


if __name__ == "__main__":
    rodar(SimuladorHikvision, PORTA_PADRAO, "Hikvision")
