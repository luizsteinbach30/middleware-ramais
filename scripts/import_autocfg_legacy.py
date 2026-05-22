"""Importa ambientes do autocfg-ramais standalone (JSON files) para o
banco do middleware (tabelas extension_*).

Estrutura esperada do JSON (em <DIR>/*.json):
  { id, nome, modelo_telefone, criado_em, atualizado_em,
    config_padrao: {...},
    linhas: [
      { id, ip, numero_ramal, user_auth, senha_sip, servidor_sip,
        numero_abreviado, nome_visivel,
        ultimo_hash_aplicado, ultimo_status, ultima_aplicacao,
        ultimo_erro, ultimo_modelo, ultimo_mac }
    ]
  }

Idempotente: rodar 2x nao duplica (skip por env id e linha id).
Uso:
  .venv/Scripts/python.exe scripts/import_autocfg_legacy.py [DIR]
DIR default: C:/Projetos/autocfg-ramais/data/ambientes
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from middleware_monitor.core.db import init_engine, session_factory
from middleware_monitor.core.models import (
    ExtensionEnvironment,
    ExtensionLine,
)

DEFAULT_DIR = Path("C:/Projetos/autocfg-ramais/data/ambientes")


def _utcnow_naive() -> datetime:
    # Schema usa DateTime sem tz — guarda em UTC mas naive p/ compatibilidade
    return datetime.now(UTC).replace(tzinfo=None)


def _parse_dt(s: str | None) -> datetime:
    if not s:
        return _utcnow_naive()
    try:
        # JSONs autocfg salvam sem timezone (ISO local naive)
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        return _utcnow_naive()


def _import_env(db, payload: dict) -> tuple[bool, int, int]:
    """Importa um ambiente. Devolve (env_created, lines_created, lines_skipped)."""
    env_id = str(payload["id"])
    nome = str(payload.get("nome") or env_id)
    modelo = str(payload.get("modelo_telefone") or "HTEK UC902G")
    cfg = payload.get("config_padrao") or {}
    criado = _parse_dt(payload.get("criado_em"))
    atualizado = _parse_dt(payload.get("atualizado_em"))

    env = db.get(ExtensionEnvironment, env_id)
    env_created = False
    if env is None:
        env = ExtensionEnvironment(
            id=env_id,
            nome=nome,
            modelo_telefone=modelo,
            config_padrao=json.dumps(cfg, ensure_ascii=False),
            created_at=criado,
            updated_at=atualizado,
        )
        db.add(env)
        db.flush()
        env_created = True

    lines_created = 0
    lines_skipped = 0
    seen_ids: set[str] = set()
    for ln in payload.get("linhas") or []:
        ln_id = str(ln.get("id") or "")
        if not ln_id:
            continue
        if ln_id in seen_ids:
            # Duplicata dentro do mesmo JSON (mantem o primeiro)
            lines_skipped += 1
            continue
        seen_ids.add(ln_id)
        if db.get(ExtensionLine, ln_id) is not None:
            lines_skipped += 1
            continue
        new_line = ExtensionLine(
            id=ln_id,
            environment_id=env.id,
            ip=str(ln.get("ip", "") or ""),
            numero_ramal=str(ln.get("numero_ramal", "") or ""),
            user_auth=str(ln.get("user_auth", "") or ln.get("numero_ramal", "") or ""),
            senha_sip=str(ln.get("senha_sip", "") or ""),
            servidor_sip=str(ln.get("servidor_sip", "") or ""),
            numero_abreviado=str(ln.get("numero_abreviado", "") or ""),
            nome_visivel=str(ln.get("nome_visivel", "") or ""),
            ultimo_hash_aplicado=ln.get("ultimo_hash_aplicado") or None,
            ultimo_status=ln.get("ultimo_status") or None,
            ultima_aplicacao=(
                _parse_dt(ln.get("ultima_aplicacao"))
                if ln.get("ultima_aplicacao") else None
            ),
            ultimo_erro=ln.get("ultimo_erro") or None,
            ultimo_modelo=ln.get("ultimo_modelo") or None,
            ultimo_mac=ln.get("ultimo_mac") or None,
            created_at=criado,
            updated_at=atualizado,
        )
        db.add(new_line)
        lines_created += 1

    db.flush()
    return env_created, lines_created, lines_skipped


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR
    if not src.is_dir():
        print(f"[ERRO] Diretorio nao existe: {src}")
        return 1

    jsons = sorted(src.glob("*.json"))
    if not jsons:
        print(f"[INFO] Nenhum .json em {src}")
        return 0

    init_engine()
    bar = "=" * 60
    print(bar)
    print(f"Importando {len(jsons)} arquivo(s) de {src}")
    print(bar)

    total_envs_new = 0
    total_envs_existing = 0
    total_lines_new = 0
    total_lines_skip = 0

    with session_factory() as db:
        for path in jsons:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"  [PULA] {path.name}: {exc}")
                continue
            try:
                env_created, lines_new, lines_skip = _import_env(db, payload)
            except Exception as exc:  # noqa: BLE001 — relatorio amigavel
                print(f"  [FALHA] {path.name}: {type(exc).__name__}: {exc}")
                db.rollback()
                continue
            tag = "NOVO" if env_created else "JA EXISTE"
            print(
                f"  [{tag:9s}] {payload.get('nome', path.stem)!r:30s} "
                f"+{lines_new} linha(s)"
                + (f"  (skip {lines_skip})" if lines_skip else "")
            )
            if env_created:
                total_envs_new += 1
            else:
                total_envs_existing += 1
            total_lines_new += lines_new
            total_lines_skip += lines_skip
        db.commit()

    print(bar)
    print(
        f"OK: {total_envs_new} ambiente(s) criado(s), "
        f"{total_envs_existing} ja existiam.",
    )
    print(
        f"    {total_lines_new} linha(s) importada(s), "
        f"{total_lines_skip} ja existiam.",
    )
    print(bar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
