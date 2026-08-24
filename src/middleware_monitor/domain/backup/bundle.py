"""Pacote portavel de configuracao (``.mwrbak``): montagem e aplicacao.

O pacote leva **configuracao**, nunca historico: o que precisa existir para
outra instalacao operar igual a esta. Sao quatro secoes independentes, cada uma
podendo ser exportada e restaurada sozinha:

``config``
    ``app_config`` (retencoes, ping, webhooks, auto-update, hora dos
    telefones), servidores USCall e brokers MQTT.
``environments``
    Ambientes do Configurador com ``config_padrao``, function keys e as linhas.
``users``
    Contas de acesso, com o hash da senha (nunca a senha em claro).
``devices``
    Cadastro dos telefones monitorados, sem historico de ping.

**Por que o arquivo tem de ser cifrado.** Ele carrega token do USCall, senha do
broker e senha SIP de cada ramal em claro. Em claro porque a cifra local
(``SecretBox``) deriva do ``APP_SECRET_KEY`` da maquina de origem: o destino tem
outra chave e nao decifraria nada. A protecao passa a ser a passphrase do
envelope (``core.export_crypto``), e por isso a API nao aceita exportar sem uma.

O ``schema_version`` e o portao de compatibilidade: pacote de versao diferente
e recusado inteiro, em vez de importado pela metade.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.crypto import SecretBox
from middleware_monitor.core.models import AppConfig, Device, ExtensionEnvironment, User
from middleware_monitor.domain.backup.settings import LOCAL_ONLY_KEYS
from middleware_monitor.domain.extension_configurator import repository as ec_repo
from middleware_monitor.domain.mqtt import repository as mqtt_repo
from middleware_monitor.domain.uscall import repository as uscall_repo
from middleware_monitor.settings import get_settings
from middleware_monitor.version import __version__

FORMAT = "mwr-backup"
SCHEMA_VERSION = 1
SECTIONS: tuple[str, ...] = ("config", "environments", "users", "devices")
MODES: tuple[str, ...] = ("merge", "replace")

_LINE_FIELDS = (
    "ip", "numero_ramal", "user_auth", "senha_sip",
    "servidor_sip", "numero_abreviado", "nome_visivel",
)


class BundleError(Exception):
    """Pacote invalido, de outra versao, ou impossivel de aplicar."""


def _box() -> SecretBox:
    return SecretBox(get_settings().secret_key)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def normalize_sections(raw: object) -> tuple[str, ...]:
    """Filtra a lista de secoes pedida; vazia significa todas."""
    if not raw:
        return SECTIONS
    if isinstance(raw, str):
        itens = [p.strip() for p in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        itens = [str(p).strip() for p in raw]
    else:
        return SECTIONS
    escolhidas = tuple(s for s in SECTIONS if s in itens)
    if not escolhidas:
        raise BundleError("nenhuma secao valida selecionada")
    return escolhidas


# --------------------------------------------------------------- montagem


def _build_config(db: DBSession) -> dict[str, Any]:
    linhas: list[dict[str, Any]] = []
    for row in db.scalars(select(AppConfig).order_by(AppConfig.key)).all():
        if row.key in LOCAL_ONLY_KEYS:
            continue
        valor = row.value
        if row.is_secret and valor:
            try:
                valor = _box().decrypt(valor)
            except ValueError:
                # Segredo cifrado com outra chave (APP_SECRET_KEY trocado):
                # exportar o ciphertext seria exportar lixo — some a chave.
                continue
        linhas.append({"key": row.key, "value": valor, "is_secret": row.is_secret})

    servidores = [
        {
            "nome": s.nome,
            "host": s.host,
            "token": uscall_repo.load_server_token(s) or "",
            "verify_ssl": s.verify_ssl,
            "enabled": s.enabled,
        }
        for s in uscall_repo.list_servers(db)
    ]
    brokers = [
        {
            "nome": b.nome,
            "address_input": b.address_input,
            "host": b.host,
            "port": b.port,
            "transport": b.transport,
            "tls": b.tls,
            "ws_path": b.ws_path,
            "username": b.username,
            "password": mqtt_repo.load_broker_password(b),
            "tls_verify": b.tls_verify,
            "tls_fingerprint": b.tls_fingerprint,
            "topics": mqtt_repo.broker_topics(b),
            "qos": b.qos,
            "clean_session": b.clean_session,
            "client_id": b.client_id,
            "max_payload_kb": b.max_payload_kb,
            "enabled": b.enabled,
        }
        for b in mqtt_repo.list_brokers(db)
    ]
    return {"app_config": linhas, "uscall_servers": servidores, "mqtt_brokers": brokers}


def _build_environments(db: DBSession) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for env in ec_repo.list_environments(db):
        linhas = ec_repo.list_lines(db, env.id)
        out.append({
            "id": env.id,
            "nome": env.nome,
            "modelo_telefone": env.modelo_telefone,
            "config_padrao": ec_repo.merged_config_padrao(env),
            "lines": [
                {f: getattr(ln, f) for f in _LINE_FIELDS} | {"posicao": ln.posicao}
                for ln in linhas
            ],
        })
    return out


def _build_users(db: DBSession) -> list[dict[str, Any]]:
    return [
        {
            "username": u.username,
            "password_hash": u.password_hash,
            "role": u.role,
            "must_change_password": u.must_change_password,
        }
        for u in db.scalars(select(User).order_by(User.username)).all()
    ]


def _build_devices(db: DBSession) -> list[dict[str, Any]]:
    servidores = {s.id: s.nome for s in uscall_repo.list_servers(db)}
    return [
        {
            "name": d.name,
            "ip": d.ip,
            "mac": d.mac,
            "model": d.model,
            "notes": d.notes,
            # Pelo nome, nao pelo id: o id nao sobrevive a outra instalacao.
            "uscall_server": servidores.get(d.uscall_server_id) if d.uscall_server_id else None,
        }
        for d in db.scalars(select(Device).order_by(Device.name)).all()
    ]


def build(db: DBSession, sections: tuple[str, ...] = SECTIONS) -> dict[str, Any]:
    """Monta o dicionario do pacote com as secoes pedidas."""
    corpo: dict[str, Any] = {}
    if "config" in sections:
        corpo["config"] = _build_config(db)
    if "environments" in sections:
        corpo["environments"] = _build_environments(db)
    if "users" in sections:
        corpo["users"] = _build_users(db)
    if "devices" in sections:
        corpo["devices"] = _build_devices(db)
    return {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now().isoformat(timespec="seconds"),
        "app_version": __version__,
        "sections": corpo,
    }


def to_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


# ----------------------------------------------------------------- leitura


def parse(raw: bytes) -> dict[str, Any]:
    """Le o JSON ja decifrado e valida o cabecalho."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError("conteudo invalido") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BundleError("nao e um pacote de backup do middleware")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise BundleError(
            f"pacote em formato {data.get('schema_version')!r}; esta versao le "
            f"{SCHEMA_VERSION}"
        )
    if not isinstance(data.get("sections"), dict):
        raise BundleError("pacote sem secoes")
    return data


def summarize(data: dict[str, Any]) -> dict[str, Any]:
    """Resumo do que ha no pacote — o que a tela mostra antes de restaurar."""
    secoes = data.get("sections") or {}
    resumo: dict[str, Any] = {}
    if isinstance(secoes.get("config"), dict):
        cfg = secoes["config"]
        resumo["config"] = {
            "chaves": len(cfg.get("app_config") or []),
            "servidores_uscall": len(cfg.get("uscall_servers") or []),
            "brokers_mqtt": len(cfg.get("mqtt_brokers") or []),
        }
    if isinstance(secoes.get("environments"), list):
        ambientes = secoes["environments"]
        resumo["environments"] = {
            "ambientes": len(ambientes),
            "linhas": sum(len(a.get("lines") or []) for a in ambientes if isinstance(a, dict)),
            "nomes": [str(a.get("nome", "")) for a in ambientes if isinstance(a, dict)][:20],
        }
    if isinstance(secoes.get("users"), list):
        resumo["users"] = {"usuarios": len(secoes["users"])}
    if isinstance(secoes.get("devices"), list):
        resumo["devices"] = {"devices": len(secoes["devices"])}
    return {
        "generated_at": data.get("generated_at", ""),
        "app_version": data.get("app_version", ""),
        "sections": resumo,
    }


# --------------------------------------------------------------- aplicacao


def _apply_config(db: DBSession, cfg: dict[str, Any], *, mode: str, user_id: int | None) -> dict[str, int]:
    agora = _now()
    existentes = {row.key: row for row in db.scalars(select(AppConfig)).all()}
    chaves = 0
    for item in cfg.get("app_config") or []:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        key = str(item["key"])
        if key in LOCAL_ONLY_KEYS:
            continue
        secreto = bool(item.get("is_secret"))
        valor = str(item.get("value") or "")
        guardado = _box().encrypt(valor) if (secreto and valor) else valor
        row = existentes.get(key)
        if row is None:
            db.add(AppConfig(
                key=key, value=guardado, is_secret=secreto,
                updated_at=agora, updated_by=user_id,
            ))
        else:
            row.value = guardado
            row.is_secret = secreto
            row.updated_at = agora
            row.updated_by = user_id
        chaves += 1

    servidores = cfg.get("uscall_servers") or []
    if mode == "replace" and servidores:
        for s in uscall_repo.list_servers(db):
            uscall_repo.delete_server(db, s.id)
    atuais = {s.nome: s for s in uscall_repo.list_servers(db)}
    n_srv = 0
    for item in servidores:
        if not isinstance(item, dict) or not item.get("nome"):
            continue
        nome = str(item["nome"])
        host = str(item.get("host") or "")
        token = str(item.get("token") or "")
        verify_ssl = bool(item.get("verify_ssl", True))
        habilitado = bool(item.get("enabled", True))
        existente = atuais.get(nome)
        if existente is None:
            uscall_repo.create_server(
                db, nome=nome, host=host, token_plain=token,
                verify_ssl=verify_ssl, enabled=habilitado,
            )
        else:
            uscall_repo.update_server(
                db, existente, nome=nome, host=host, token_plain=token,
                verify_ssl=verify_ssl, enabled=habilitado,
            )
        n_srv += 1

    brokers = cfg.get("mqtt_brokers") or []
    if mode == "replace" and brokers:
        for b in mqtt_repo.list_brokers(db):
            mqtt_repo.delete_broker(db, b.id)
    atuais_b = {b.nome: b for b in mqtt_repo.list_brokers(db)}
    n_brk = 0
    for item in brokers:
        if not isinstance(item, dict) or not item.get("nome"):
            continue
        nome = str(item["nome"])
        ws_path = str(item["ws_path"]) if item.get("ws_path") else None
        fingerprint = str(item["tls_fingerprint"]) if item.get("tls_fingerprint") else None
        campos_broker: dict[str, Any] = {
            "address_input": str(item.get("address_input") or ""),
            "host": str(item.get("host") or ""),
            "port": int(str(item.get("port") or 1883)),
            "transport": str(item.get("transport") or "tcp"),
            "tls": bool(item.get("tls", False)),
            "ws_path": ws_path,
            "username": str(item.get("username") or ""),
            "password_plain": str(item.get("password") or ""),
            "tls_verify": bool(item.get("tls_verify", True)),
            "tls_fingerprint": fingerprint,
            "topics": [str(t) for t in (item.get("topics") or [])],
            "qos": int(str(item.get("qos") or 1)),
            "clean_session": bool(item.get("clean_session", False)),
            "client_id": str(item.get("client_id") or ""),
            "max_payload_kb": int(str(item.get("max_payload_kb") or 0)),
            "enabled": bool(item.get("enabled", True)),
        }
        existente_b = atuais_b.get(nome)
        if existente_b is None:
            mqtt_repo.create_broker(db, nome=nome, **campos_broker)
        else:
            mqtt_repo.update_broker(db, existente_b, nome=nome, **campos_broker)
        n_brk += 1

    return {"chaves": chaves, "servidores_uscall": n_srv, "brokers_mqtt": n_brk}


def _apply_environments(db: DBSession, ambientes: list[Any], *, mode: str) -> dict[str, int]:
    if mode == "replace":
        for env in ec_repo.list_environments(db):
            ec_repo.delete_environment(db, env.id)
    n_amb = 0
    n_linhas = 0
    for item in ambientes:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("nome") or "").strip()
        modelo = str(item.get("modelo_telefone") or "").strip()
        if not nome or not modelo:
            continue
        env_id = str(item.get("id") or "").strip()
        if mode == "replace" and env_id and db.get(ExtensionEnvironment, env_id) is None:
            # Preserva o identificador original — os links salvos pelo operador
            # (/extension-configurator/environments/<id>) continuam validos.
            agora = _now()
            env = ExtensionEnvironment(
                id=env_id, nome=nome, modelo_telefone=modelo,
                config_padrao="{}", created_at=agora, updated_at=agora,
            )
            db.add(env)
            db.flush()
        else:
            env = ec_repo.create_environment(db, nome=nome, modelo_telefone=modelo)
        cfg = item.get("config_padrao")
        if isinstance(cfg, dict):
            ec_repo.update_environment(db, env, config_padrao=cfg)
        linhas = [ln for ln in (item.get("lines") or []) if isinstance(ln, dict)]
        linhas.sort(key=lambda ln: int(ln.get("posicao") or 0))
        rows = [{f: str(ln.get(f, "") or "") for f in _LINE_FIELDS} for ln in linhas]
        ec_repo.save_lines(db, env, rows)
        n_amb += 1
        n_linhas += len(rows)
    return {"ambientes": n_amb, "linhas": n_linhas}


def _apply_users(db: DBSession, usuarios: list[Any], *, mode: str) -> dict[str, int]:
    """Cria os usuarios que faltam; em ``replace`` tambem atualiza os que ja
    existem. **Nunca apaga conta** — restauracao que remove o login de quem
    esta operando deixa a instalacao inacessivel."""
    atuais = {u.username: u for u in db.scalars(select(User)).all()}
    criados = 0
    atualizados = 0
    for item in usuarios:
        if not isinstance(item, dict) or not item.get("username") or not item.get("password_hash"):
            continue
        username = str(item["username"])
        existente = atuais.get(username)
        if existente is None:
            db.add(User(
                username=username,
                password_hash=str(item["password_hash"]),
                role=str(item.get("role") or "admin"),
                must_change_password=bool(item.get("must_change_password", False)),
                created_at=_now(),
            ))
            criados += 1
        elif mode == "replace":
            existente.password_hash = str(item["password_hash"])
            existente.role = str(item.get("role") or existente.role)
            existente.must_change_password = bool(item.get("must_change_password", False))
            atualizados += 1
    db.flush()
    return {"criados": criados, "atualizados": atualizados}


def _apply_devices(db: DBSession, devices: list[Any]) -> dict[str, int]:
    """Upsert por nome. Nunca apaga: a coleta REST recria o que existir de
    verdade, e remover device leva junto ping/vinculo de linha."""
    atuais = {d.name: d for d in db.scalars(select(Device)).all()}
    servidores = {s.nome: s.id for s in uscall_repo.list_servers(db)}
    criados = 0
    atualizados = 0
    for item in devices:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        nome = str(item["name"])
        srv_id = servidores.get(str(item.get("uscall_server") or ""))
        campos = {
            "ip": item.get("ip") or None,
            "mac": item.get("mac") or None,
            "model": item.get("model") or None,
            "notes": item.get("notes") or None,
            "uscall_server_id": srv_id,
        }
        existente = atuais.get(nome)
        if existente is None:
            agora = _now()
            db.add(Device(name=nome, created_at=agora, updated_at=agora, **campos))
            criados += 1
        else:
            for k, v in campos.items():
                if v is not None:
                    setattr(existente, k, v)
            existente.updated_at = _now()
            atualizados += 1
    db.flush()
    return {"criados": criados, "atualizados": atualizados}


def apply(
    db: DBSession,
    data: dict[str, Any],
    *,
    sections: tuple[str, ...] = SECTIONS,
    mode: str = "merge",
    user_id: int | None = None,
) -> dict[str, Any]:
    """Aplica o pacote ao banco. Tudo em UMA transacao: ou entra inteiro, ou
    nao entra nada — meia restauracao e pior que nenhuma.

    ``merge`` acrescenta sem destruir (ambientes viram novos, config existente e
    sobrescrita chave a chave); ``replace`` troca ambientes, servidores USCall e
    brokers MQTT pelo conteudo do arquivo.
    """
    if mode not in MODES:
        raise BundleError(f"modo invalido: {mode!r}")
    secoes = data.get("sections") or {}
    relatorio: dict[str, Any] = {"mode": mode, "applied": {}}
    try:
        if "config" in sections and isinstance(secoes.get("config"), dict):
            relatorio["applied"]["config"] = _apply_config(
                db, secoes["config"], mode=mode, user_id=user_id,
            )
        if "environments" in sections and isinstance(secoes.get("environments"), list):
            relatorio["applied"]["environments"] = _apply_environments(
                db, secoes["environments"], mode=mode,
            )
        if "users" in sections and isinstance(secoes.get("users"), list):
            relatorio["applied"]["users"] = _apply_users(db, secoes["users"], mode=mode)
        if "devices" in sections and isinstance(secoes.get("devices"), list):
            relatorio["applied"]["devices"] = _apply_devices(db, secoes["devices"])
        db.commit()
    except Exception:
        db.rollback()
        raise
    return relatorio
