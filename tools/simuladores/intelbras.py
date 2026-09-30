"""Simulador de gravador Intelbras/Dahua — CGI com digest, respostas ``chave=valor``.

Responde os caminhos que o adaptador ``intelbras`` lê, no formato da documentação
da API HTTP Dahua. Subir: ``python tools/simuladores/intelbras.py --porta 18081``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _comum import Manipulador, Simulador, montar_servidor, rodar

__all__ = ["PORTA_PADRAO", "SimuladorIntelbras", "montar_servidor"]

PORTA_PADRAO = 18081
_GB = 1_000_000_000


class SimuladorIntelbras(Simulador):
    fabricante = "intelbras"
    realm = "Login to SIM0001"

    def responder(self, h: Manipulador, metodo: str) -> None:
        if metodo != "GET":
            h.enviar(405, "Method Not Allowed")
            return
        if not self.digest_confere(metodo, h.headers.get("Authorization")):
            h.pedir_digest()
            return
        partes = urlsplit(h.path)
        chave = f"{partes.path}?{partes.query}"
        est = self.ler()
        resposta = _ROTAS.get(chave)
        if resposta is None:
            h.enviar(400, "Error\r\nBad Request!\r\n")
            return
        if chave == _SISTEMA and est.get("resposta") == "vazia":
            h.enviar(200, "")
            return
        if chave == _SISTEMA and est.get("resposta") == "malformada":
            h.enviar(200, "<html><body>pagina de login</body></html>", "text/html")
            return
        h.enviar(200, resposta(self, est))


def _linhas(pares: list[tuple[str, Any]]) -> str:
    return "".join(f"{k}={v}\r\n" for k, v in pares)


def _sistema(_s: SimuladorIntelbras, _e: dict[str, Any]) -> str:
    return _linhas(
        [
            ("deviceType", "NVD1432"),
            ("processor", "ST7108"),
            ("serialNumber", "SIM0001"),
            ("updateSerial", "NVD1432"),
        ]
    )


def _versao(_s: SimuladorIntelbras, _e: dict[str, Any]) -> str:
    return _linhas([("version", "4.001.00IB000.0.R,build:2024-01-01")])


def _titulos(_s: SimuladorIntelbras, e: dict[str, Any]) -> str:
    return _linhas([(f"table.ChannelTitle[{i}].Name", f"CAM {i + 1}") for i in range(int(e["canais"]))])


def _sem_video(_s: SimuladorIntelbras, e: dict[str, Any]) -> str:
    # Sem nenhum canal com o evento, a resposta é vazia (é o que o adaptador espera).
    canais = [n - 1 for n in e.get("semVideo") or [] if 1 <= n <= int(e["canais"])]
    return _linhas([(f"channels[{k}]", idx) for k, idx in enumerate(canais)])


def _modo(_s: SimuladorIntelbras, e: dict[str, Any]) -> str:
    parados = set(e.get("naoGravando") or [])
    return _linhas(
        [(f"table.RecordMode[{i}].Mode", 2 if (i + 1) in parados else 0) for i in range(int(e["canais"]))]
    )


def _discos(_s: SimuladorIntelbras, e: dict[str, Any]) -> str:
    pares: list[tuple[str, Any]] = []
    for i, d in enumerate(e.get("discos") or []):
        estado = d.get("estado", "ok")
        total = float(d.get("totalGb", 0)) * _GB
        livre = float(d.get("livreGb", 0)) * _GB
        pares += [
            (f"list.info[{i}].Detail[0].IsError", "true" if estado == "defeito" else "false"),
            (f"list.info[{i}].Detail[0].Path", f"/dev/sd{chr(97 + i)}1"),
            (f"list.info[{i}].Detail[0].TotalBytes", f"{total:.6f}"),
            (f"list.info[{i}].Detail[0].Type", "ReadWrite"),
            (f"list.info[{i}].Detail[0].UsedBytes", f"{total - livre:.6f}"),
            (f"list.info[{i}].Name", f"/dev/sd{chr(97 + i)}"),
            (
                f"list.info[{i}].State",
                {"defeito": "Error", "ausente": "Offline"}.get(estado, "Success"),
            ),
        ]
    return _linhas(pares) or "list.info=\r\n"


def _sem_espaco(_s: SimuladorIntelbras, e: dict[str, Any]) -> str:
    cheio = any(d.get("estado") == "cheio" for d in e.get("discos") or [])
    return _linhas([("channels[0]", 0)]) if cheio else ""


def _hora(s: SimuladorIntelbras, _e: dict[str, Any]) -> str:
    agora = s.hora_local()
    return _linhas([("result", f"{agora.year}-{agora.month}-{agora.day} {agora:%H:%M:%S}")])


_SISTEMA = "/cgi-bin/magicBox.cgi?action=getSystemInfo"
_ROTAS = {
    _SISTEMA: _sistema,
    "/cgi-bin/magicBox.cgi?action=getSoftwareVersion": _versao,
    "/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle": _titulos,
    "/cgi-bin/eventManager.cgi?action=getEventIndexes&code=VideoLoss": _sem_video,
    "/cgi-bin/configManager.cgi?action=getConfig&name=RecordMode": _modo,
    "/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo": _discos,
    "/cgi-bin/eventManager.cgi?action=getEventIndexes&code=StorageLowSpace": _sem_espaco,
    "/cgi-bin/global.cgi?action=getCurrentTime": _hora,
}


if __name__ == "__main__":
    rodar(SimuladorIntelbras, PORTA_PADRAO, "Intelbras/Dahua")
