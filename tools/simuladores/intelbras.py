"""Simulador de gravador Intelbras/Dahua — CGI com digest, respostas ``chave=valor``.

Responde os caminhos que o adaptador ``intelbras`` lê, no formato da documentação
da API HTTP Dahua. Subir: ``python tools/simuladores/intelbras.py --porta 18081``.

**DVR falso do acesso pelo navegador (ADR 0043 do NOC):**

- ``GET /plugin.data`` e ``GET /`` sem senha, como os DVRs de verdade servem antes do login. A
  variante vem de ``estado.pagina``: ``legado-4`` (pede ``WebActiveX.Plugin.4.1.511.0``),
  ``legado-5``, ``moderno`` (sem plugin) ou ``desconhecido`` (plugin fora do catálogo).
- **Bloqueio depois de ``bloqueioAposFalhas`` senhas erradas** (5, como a Intelbras), por
  ``bloqueioS`` segundos, contando HTTP e RTSP juntos. ``falhas`` e ``tentativasRtsp`` aparecem em
  ``/_sim/estado``.
- ``--rtsp-porta`` liga a frente RTSP (``rtsp.py``): digest, os caminhos Intelbras/Hikvision
  traduzidos para o servidor de vídeo do laboratório (mediamtx), e o resto passado adiante.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _comum import Manipulador, Simulador, montar_servidor

__all__ = ["PORTA_PADRAO", "SimuladorIntelbras", "montar_servidor"]

PORTA_PADRAO = 18081
_GB = 1_000_000_000


class SimuladorIntelbras(Simulador):
    fabricante = "intelbras"
    realm = "Login to SIM0001"

    def __init__(self, usuario: str = "admin", senha: str = "simulador123") -> None:
        super().__init__(usuario, senha)
        self._dvr_inicial()

    def _dvr_inicial(self) -> None:
        self.estado.update(
            {"pagina": "legado-4", "bloqueioAposFalhas": 5, "bloqueioS": 1800, "falhas": 0, "bloqueadoAte": 0, "tentativasRtsp": 0}
        )

    def reiniciar(self) -> None:
        super().reiniciar()
        with self._trava:
            self._dvr_inicial()

    def bloqueado(self) -> bool:
        return time.time() < float(self.ler().get("bloqueadoAte") or 0)

    def conferir_login(self, metodo: str, cabecalho: str | None, rtsp: bool = False) -> bool:
        """Digest com bloqueio: sem cabeçalho é só o desafio (não conta); cabeçalho errado conta falha."""
        if rtsp and cabecalho:
            with self._trava:
                self.estado["tentativasRtsp"] = int(self.estado.get("tentativasRtsp") or 0) + 1
        if self.bloqueado() or not cabecalho:
            return False
        if self.digest_confere(metodo, cabecalho):
            with self._trava:
                self.estado["falhas"] = 0
            return True
        with self._trava:
            self.estado["falhas"] = int(self.estado.get("falhas") or 0) + 1
            if self.estado["falhas"] >= int(self.estado.get("bloqueioAposFalhas") or 5):
                self.estado["bloqueadoAte"] = time.time() + int(self.estado.get("bloqueioS") or 1800)
        return False

    def responder(self, h: Manipulador, metodo: str) -> None:
        if metodo != "GET":
            h.enviar(405, "Method Not Allowed")
            return
        caminho = urlsplit(h.path).path
        if caminho == "/plugin/webplugin.exe":
            # O instalador do plugin, sem senha como nos DVRs de verdade: um executável mínimo, sem assinatura.
            h.enviar(200, _INSTALADOR, "application/octet-stream")
            return
        if caminho in ("/plugin.data", "/", "/index.html"):
            pagina = _PAGINAS.get(str(self.ler().get("pagina")), _PAGINAS["legado-4"])
            if caminho != "/plugin.data":
                h.enviar(200, pagina["html"], "text/html; charset=utf-8")
            elif pagina["plugin"] is None:
                h.enviar(404, "Not Found")
            else:
                h.enviar(200, f"PluginName={pagina['plugin']}\r\nPluginVersion={pagina['versao']}\r\nClassid={pagina['classid']}\r\n")
            return
        if not self.conferir_login(metodo, h.headers.get("Authorization")):
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


# A página de entrada de cada variante. A legada pede o plugin do jeito que os DVRs antigos pedem:
# literal no JavaScript, ``new ActiveXObject`` e ``<object classid>`` (o NOC lê sem executar).
_LEGADO = (
    "<html><head><title>WEB SERVICE</title>\n"
    '<script>var PLUGINS_NAME = "{plugin}"; var PluginVersion = "{versao}";\n'
    'function carregar() {{ try {{ new ActiveXObject("{plugin}"); }} catch (e) {{ location.href = "/plugin/webplugin.exe"; }} }}'
    "</script></head>\n"
    '<body onload="carregar()"><object id="ocx" classid="clsid:{classid}" width="100%" height="100%"></object></body></html>'
)
_PAGINAS: dict[str, dict[str, Any]] = {
    "legado-4": {"plugin": "WebActiveX.Plugin.4.1.511.0", "versao": "4.1.511.0", "classid": "9A21544E-5D4C-4F5B-9C52-5A3D1F6E7C01"},
    "legado-5": {"plugin": "WebActiveX.Plugin.5.1.412.6.5.0", "versao": "5.1.412.6.5.0", "classid": "E67EBEA0-2B3C-4D5E-8F90-1A2B3C4D5E6F"},
    "desconhecido": {"plugin": "FabricanteX.Plugin.2.0", "versao": "2.0", "classid": "0F0F0F0F-1111-2222-3333-444455556666"},
}
for _p in _PAGINAS.values():
    _p["html"] = _LEGADO.format(**_p)
_PAGINAS["moderno"] = {
    "plugin": None,
    "html": '<html><head><title>Intelbras</title><script src="/RPC2_Login"></script></head><body>Intelbras</body></html>',
}


def _executavel_minimo() -> bytes:
    """Um PE32 mínimo (cabeçalho DOS, ``PE\\0\\0``, cabeçalho opcional) sem tabela de certificados."""
    b = bytearray(0x80 + 24 + 224)
    b[0:2] = b"MZ"
    b[0x3C:0x40] = (0x80).to_bytes(4, "little")
    b[0x80:0x84] = b"PE\0\0"
    b[0x80 + 24 : 0x80 + 26] = (0x10B).to_bytes(2, "little")
    return bytes(b)


_INSTALADOR = _executavel_minimo()


def _principal() -> None:
    """Como ``_comum.rodar``, mais a variante da página e a frente RTSP do DVR falso."""
    import argparse
    import threading

    ap = argparse.ArgumentParser(description="Simulador de gravador Intelbras/Dahua (só laboratório).")
    ap.add_argument("--porta", type=int, default=PORTA_PADRAO)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--usuario", default="admin")
    ap.add_argument("--senha", default="simulador123")
    ap.add_argument("--pagina", default="legado-4", choices=sorted(_PAGINAS))
    ap.add_argument("--rtsp-porta", type=int, default=0, help="liga a frente RTSP (0 = desligada)")
    ap.add_argument("--rtsp-destino", default="127.0.0.1:8554", help="o servidor de vídeo do laboratório (mediamtx)")
    args = ap.parse_args()
    sim = SimuladorIntelbras(args.usuario, args.senha)
    sim.mudar({"pagina": args.pagina})
    servidor = montar_servidor(sim, args.porta, args.host)
    print(f"Simulador Intelbras/Dahua no ar em http://{args.host}:{args.porta} (página {args.pagina})", flush=True)
    print(f"  cenário: http://{args.host}:{args.porta}/_sim/estado  (GET mostra, POST muda)", flush=True)
    if args.rtsp_porta:
        from rtsp import FrenteRtsp

        host, _, porta = args.rtsp_destino.rpartition(":")
        frente = FrenteRtsp(sim, args.host, args.rtsp_porta, (host, int(porta)))
        threading.Thread(target=frente.servir, daemon=True).start()
        print(f"  RTSP em rtsp://{args.host}:{args.rtsp_porta} -> {args.rtsp_destino}", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()


if __name__ == "__main__":
    _principal()
