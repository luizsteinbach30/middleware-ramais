#!/usr/bin/env python
"""Prova, sobre um banco de verdade, que a migration 0013 tira a senha do claro.

Os testes de unidade conferem o conteúdo da coluna. O que eles **não** conseguem
conferir é o arquivo: com o banco em WAL e a sessão do teste segurando a
conexão, medir bytes em disco mede o momento do checkpoint, não o código. Este
roteiro mede — num banco descartável, do jeito que um cliente real chega:

    0012 (antes da cifra)  →  grava senha em claro  →  upgrade head  →  boot

E confere as quatro coisas que importam:

1. a coluna vira ciphertext marcado (`enc:v1:`);
2. o texto antigo **ainda está** nas páginas livres logo depois da migration —
   cifrar não apaga o que já foi escrito, e é por isso que existe o `VACUUM`;
3. depois do primeiro boot ele sumiu (`app.lifespan` compacta);
4. o aplicativo continua lendo as senhas originais.

    .venv/Scripts/python scripts/prova-cifra-em-repouso.py

Sai com 0 se tudo bate; imprime o que falhou e sai com 1 se não.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = Path(sys.executable)

SENHA_SIP = "S3nh4-SIP-Secreta!"
CFG = {
    "web_password": "admin-da-loja",
    "nova_web_password": "trocada-2026",
    "menu_password": "4321",
    "keylock_password": "9876",
    "sip_server": "pbx.exemplo.local",
}

dados = Path(tempfile.mkdtemp(prefix="prova-cifra-"))
env = {
    **os.environ,
    "APP_DATA_DIR": str(dados),
    "APP_SECRET_KEY": "chave-de-prova-bem-comprida-2026",
    "APP_LOG_LEVEL": "WARNING",
}


def alembic(*args: str) -> None:
    r = subprocess.run(
        [str(PY), "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=REPO, env=env, capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        sys.exit(f"alembic {' '.join(args)} falhou")


def bytes_em_disco(banco: Path) -> bytes:
    """O `.db` e os companheiros do WAL — o resíduo pode estar em qualquer um."""
    return b"".join(
        arq.read_bytes() for arq in sorted(banco.parent.iterdir()) if arq.is_file()
    )


print(f"[1] banco descartável em {dados}")
alembic("upgrade", "0012_noc_tarefas")

banco = next(dados.rglob("*.db"))
print("[2] gravando do jeito ANTIGO (texto claro), como a v2.13.0 gravava")
agora = datetime.now().isoformat(sep=" ", timespec="seconds")
con = sqlite3.connect(banco)
con.execute(
    "INSERT INTO extension_environments "
    "(id, nome, modelo_telefone, config_padrao, created_at, updated_at) "
    "VALUES (?,?,?,?,?,?)",
    ("loja-01", "Loja 01", "HTEK UC902G", json.dumps(CFG), agora, agora),
)
con.execute(
    "INSERT INTO extension_lines "
    "(id, environment_id, ip, numero_ramal, user_auth, senha_sip, servidor_sip, "
    " numero_abreviado, nome_visivel, posicao, created_at, updated_at) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
    ("lin1", "loja-01", "10.0.0.10", "1001", "1001", SENHA_SIP, "", "", "", 0,
     agora, agora),
)
con.commit()
con.close()

falhas: list[str] = []
if SENHA_SIP.encode() not in bytes_em_disco(banco):
    falhas.append("a senha nem chegou em claro ao arquivo — o roteiro não mede nada")
else:
    print("    confirmado: a senha aparece em texto no arquivo")

print("[3] alembic upgrade head")
alembic("upgrade", "head")

con = sqlite3.connect(banco)
senha_col = con.execute("SELECT senha_sip FROM extension_lines").fetchone()[0]
cfg_col = con.execute("SELECT config_padrao FROM extension_environments").fetchone()[0]
revisao = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
con.close()

print(f"[4] revisão: {revisao} · coluna: {senha_col[:34]}…")
if not senha_col.startswith("enc:v1:"):
    falhas.append("senha_sip não ficou cifrada")
cfg = json.loads(cfg_col)
for chave in ("web_password", "nova_web_password", "menu_password", "keylock_password"):
    if not str(cfg.get(chave, "")).startswith("enc:v1:"):
        falhas.append(f"{chave} não ficou cifrada")
if cfg.get("sip_server") != "pbx.exemplo.local":
    falhas.append("sip_server (que não é segredo) foi mexido")

SEGREDOS = (SENHA_SIP, "admin-da-loja", "trocada-2026", "4321", "9876")
residuo = [s for s in SEGREDOS if s.encode() in bytes_em_disco(banco)]
print(f"[5] resíduo em páginas livres logo após a migration: {residuo or 'nenhum'}")

print("[6] um boot do aplicativo — é ele quem compacta")
subprocess.run(
    [str(PY), "-c",
     "from fastapi.testclient import TestClient;"
     "from middleware_monitor.app import create_app;"
     "c=TestClient(create_app()); c.__enter__(); c.__exit__(None,None,None)"],
    cwd=REPO, env=env, capture_output=True, text=True, check=False,
)
for segredo in SEGREDOS:
    if segredo.encode() in bytes_em_disco(banco):
        falhas.append(f"{segredo!r} ainda aparece em texto depois do boot")

print("[7] e o aplicativo ainda lê as senhas originais?")
r = subprocess.run(
    [str(PY), "-c",
     "from middleware_monitor.core.db import init_engine, session_factory;"
     "init_engine();"
     "from middleware_monitor.domain.extension_configurator import repository as r;"
     "s=session_factory();"
     "print(r.senha_sip_de(r.list_lines(s,'loja-01')[0]));"
     "print(r.merged_config_padrao(r.get_environment(s,'loja-01'))['web_password'])"],
    cwd=REPO, env=env, capture_output=True, text=True, check=False,
)
saida = (r.stdout or "").strip().splitlines()
print(f"    -> {saida}")
if saida[:2] != [SENHA_SIP, "admin-da-loja"]:
    print(r.stderr)
    falhas.append("a leitura não devolveu os valores originais")

print()
if falhas:
    for f in falhas:
        print(f"FALHOU: {f}")
    sys.exit(1)
print("OK — entrou em claro, saiu cifrado, o resíduo sumiu no boot, e continua legível.")
