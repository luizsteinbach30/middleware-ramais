"""Simulador de console UniFi Protect — login com cookie + token CSRF, bootstrap em JSON.

Responde o que o adaptador ``unifi`` pede: ``POST /api/auth/login`` (devolve o cookie
``TOKEN`` e o cabeçalho ``X-CSRF-Token``) e ``GET /proxy/protect/api/bootstrap`` (as
câmeras e o ``nvr``). O relógio do console é o cabeçalho ``Date``, com o desvio do
cenário. Em http, não https — é laboratório. Subir:
``python tools/simuladores/unifi.py --porta 18083``.

``logins`` no ``GET /_sim/estado`` conta quantos logins o agente fez: a sessão
tem de ser reaproveitada, não refeita a cada leitura.
"""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _comum import SENHA_PADRAO, USUARIO_PADRAO, Manipulador, Simulador, montar_servidor, rodar

__all__ = ["PORTA_PADRAO", "SimuladorUnifi", "montar_servidor"]

PORTA_PADRAO = 18083
_GB = 1_000_000_000


class SimuladorUnifi(Simulador):
    fabricante = "unifi"

    def __init__(self, usuario: str = USUARIO_PADRAO, senha: str = SENHA_PADRAO) -> None:
        super().__init__(usuario, senha)
        self._sessoes: dict[str, str] = {}
        self.estado["logins"] = 0

    def reiniciar(self) -> None:
        super().reiniciar()
        self._sessoes.clear()
        self.estado["logins"] = 0

    def responder(self, h: Manipulador, metodo: str) -> None:
        if metodo == "POST" and h.path == "/api/auth/login":
            self._login(h)
            return
        if metodo == "GET" and h.path == "/proxy/protect/api/bootstrap":
            self._bootstrap(h)
            return
        h.enviar(404, json.dumps({"error": "not found"}), "application/json")

    def _login(self, h: Manipulador) -> None:
        corpo = h.corpo_json()
        if (
            not isinstance(corpo, dict)
            or corpo.get("username") != self.usuario
            or corpo.get("password") != self.senha
        ):
            h.enviar(
                401, json.dumps({"code": "AUTHENTICATION_FAILED_INVALID_CREDENTIALS"}), "application/json"
            )
            return
        token, csrf = secrets.token_hex(16), secrets.token_hex(16)
        self._sessoes[token] = csrf
        self.mudar({"logins": int(self.ler().get("logins") or 0) + 1})
        h.enviar(
            200,
            json.dumps({"username": self.usuario, "isOwner": False}),
            "application/json",
            {"Set-Cookie": f"TOKEN={token}; path=/; httponly; samesite=strict", "X-CSRF-Token": csrf},
        )

    def _bootstrap(self, h: Manipulador) -> None:
        token = ""
        for parte in (h.headers.get("Cookie") or "").split(";"):
            nome, _, valor = parte.strip().partition("=")
            if nome == "TOKEN":
                token = valor
        if token not in self._sessoes or h.headers.get("X-CSRF-Token") != self._sessoes[token]:
            h.enviar(401, json.dumps({"error": "unauthorized"}), "application/json")
            return
        est = self.ler()
        if est.get("resposta") == "vazia":
            h.enviar(200, "", "application/json")
            return
        if est.get("resposta") == "malformada":
            h.enviar(200, "<html>login</html>", "text/html")
            return
        h.enviar(200, json.dumps(_bootstrap(est)), "application/json")


def _bootstrap(e: dict[str, Any]) -> dict[str, Any]:
    sem = set(e.get("semVideo") or [])
    parados = set(e.get("naoGravando") or [])
    cameras = [
        {
            "id": f"cam{n:04d}",
            "name": f"Camera {n:02d}",
            "type": "UVC G4 Bullet",
            "state": "DISCONNECTED" if n in sem else "CONNECTED",
            "isConnected": n not in sem,
            "isRecording": n not in sem and n not in parados,
            "isAdopted": True,
        }
        for n in range(1, int(e["canais"]) + 1)
    ]
    discos = e.get("discos") or []
    dispositivos = [
        {
            "model": "ST4000VX016",
            "size": int(float(d.get("totalGb", 0)) * _GB),
            "healthy": "good" if d.get("estado", "ok") in ("ok", "cheio") else "bad",
        }
        for d in discos
        if d.get("estado") != "ausente"
    ]
    total = sum(x["size"] for x in dispositivos)
    livre = int(sum(float(d.get("livreGb", 0)) for d in discos if d.get("estado") != "ausente") * _GB)
    return {
        "authUserId": "sim",
        "nvr": {
            "id": "nvr0001",
            "name": "UNVR Simulado",
            "type": "UNVR",
            "hardwarePlatform": "al324",
            "version": "4.0.21",
            "timezone": "America/Sao_Paulo",
            "systemInfo": {
                "storage": {"size": total, "available": livre, "used": total - livre, "devices": dispositivos}
            },
        },
        "cameras": cameras,
        "users": [],
        "groups": [],
    }


if __name__ == "__main__":
    rodar(SimuladorUnifi, PORTA_PADRAO, "UniFi Protect")
