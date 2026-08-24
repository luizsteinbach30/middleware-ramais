"""Pacote portavel de configuracao (``.mwrbak``): montagem, comparacao e aplicacao.

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

**Importar nao sobrescreve calado.** Antes de aplicar, :func:`diff` compara item
a item o que esta no arquivo com o que esta no banco e classifica cada um em
*novo*, *identico* ou *conflito*. Identico nao vira escrita nenhuma — nao ha o
que decidir quando os dois lados dizem a mesma coisa. Conflito vai para a tela
com os dois valores lado a lado, e a decisao do operador (``atual`` ou
``arquivo``) volta em :func:`apply`.

Os dois lados da comparacao saem da **mesma** funcao :func:`build`: o que se
compara e exatamente o que se aplica, sem um segundo mapeamento para
divergir do primeiro.

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
from middleware_monitor.core.models import (
    AppConfig,
    Device,
    ExtensionEnvironment,
    MqttBroker,
    UscallServer,
    User,
)
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

# Grupos comparaveis. A secao "config" tem tres listas independentes dentro, e
# o operador decide conflito por item — entao a comparacao trabalha no nivel do
# grupo, nao da secao.
GROUPS: tuple[str, ...] = (
    "config.app_config",
    "config.uscall_servers",
    "config.mqtt_brokers",
    "environments",
    "users",
    "devices",
)
GROUP_SECTION: dict[str, str] = {g: g.split(".")[0] for g in GROUPS}
GROUP_LABEL: dict[str, str] = {
    "config.app_config": "Configurações do sistema",
    "config.uscall_servers": "Servidores USCall",
    "config.mqtt_brokers": "Brokers MQTT",
    "environments": "Ambientes do Configurador",
    "users": "Usuários",
    "devices": "Devices monitorados",
}
_ID_FIELD: dict[str, str] = {
    "config.app_config": "key",
    "config.uscall_servers": "nome",
    "config.mqtt_brokers": "nome",
    "environments": "id",
    "users": "username",
    "devices": "name",
}
# Campos cujo VALOR nunca aparece na comparacao — a tela diz que difere, e nada
# mais. Mostrar token e hash de senha lado a lado seria vazar pela janela o que
# o envelope cifrado protege.
_SECRET_FIELDS: dict[str, frozenset[str]] = {
    "config.uscall_servers": frozenset({"token"}),
    "config.mqtt_brokers": frozenset({"password"}),
    "users": frozenset({"password_hash"}),
}
# Grupos que o modo `replace` pode esvaziar. Usuario e device ficam de fora de
# proposito: apagar conta trava o acesso, e device a coleta recria sozinha.
_REPLACEABLE: frozenset[str] = frozenset(
    {"config.uscall_servers", "config.mqtt_brokers", "environments"}
)
# Lado que vence um conflito quando o operador nao decidiu. Restaurar backup
# quer dizer "traga o que esta no arquivo" — menos para conta de acesso, onde o
# padrao errado tranca o operador para fora da propria instalacao.
_DEFAULT_SIDE: dict[str, str] = {"users": "atual"}
SIDES: tuple[str, ...] = ("atual", "arquivo")

_LINE_FIELDS = (
    "ip", "numero_ramal", "user_auth", "senha_sip",
    "servidor_sip", "numero_abreviado", "nome_visivel",
)
# Teto de itens detalhados por grupo na resposta do diff. Com 1930 devices, uma
# lista completa de conflitos seria impossivel de ler e cara de trafegar; o
# resto se resolve pela decisao em massa do grupo.
_MAX_DETALHES = 200


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


def normalize_decisions(raw: object) -> dict[str, str]:
    """Decisoes de conflito: ``{"<grupo>:<id>": "atual"|"arquivo"}``.

    A chave pode ser o **grupo inteiro** (sem ``:id``), e ai vale como padrao
    daquele grupo — e o que permite decidir de uma vez um grupo com centenas de
    itens, que a tela nem lista item a item. Decisao de item vence a do grupo,
    que vence o padrao do sistema.

    Chave desconhecida e ignorada; lado invalido e erro, porque um typo que
    virasse silenciosamente o padrao decidiria sozinho o que o operador quis
    decidir.
    """
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise BundleError("decisoes em formato invalido")
    out: dict[str, str] = {}
    for chave, lado in raw.items():
        texto = str(lado)
        if texto not in SIDES:
            raise BundleError(f"decisao invalida para {chave!r}: {texto!r}")
        out[str(chave)] = texto
    return out


# --------------------------------------------------------------- montagem


# Segredo que não abre vira "" em vez de derrubar a exportação. Acontece de
# verdade: banco restaurado de outra máquina, ou APP_SECRET_KEY trocado — e é
# justamente nesse estado que o operador mais precisa conseguir tirar a
# configuração de dentro do sistema.
def _token_do_servidor(srv: UscallServer) -> str:
    try:
        return uscall_repo.load_server_token(srv) or ""
    except ValueError:
        return ""


def _senha_do_broker(broker: MqttBroker) -> str:
    try:
        return mqtt_repo.load_broker_password(broker)
    except ValueError:
        return ""


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
            "token": _token_do_servidor(s),
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
            "password": _senha_do_broker(b),
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


def _build_environments(
    db: DBSession, apenas: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for env in ec_repo.list_environments(db):
        if apenas is not None and env.id not in apenas:
            continue
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


def build(
    db: DBSession,
    sections: tuple[str, ...] = SECTIONS,
    *,
    environment_ids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Monta o dicionario do pacote com as secoes pedidas.

    ``environment_ids`` restringe a secao de ambientes a uma selecao — é o que
    a tela de Ambientes usa para exportar só o que está marcado. ``None`` leva
    todos; **tupla vazia leva nenhum**, e não todos, para uma seleção que se
    perdeu no caminho não virar export do sistema inteiro sem querer.
    """
    corpo: dict[str, Any] = {}
    if "config" in sections:
        corpo["config"] = _build_config(db)
    if "environments" in sections:
        corpo["environments"] = _build_environments(db, environment_ids)
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
    """Resumo do que ha no pacote, sem olhar o banco."""
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


# --------------------------------------------------------------- comparacao


def _itens(secoes: dict[str, Any], grupo: str) -> list[dict[str, Any]]:
    if grupo.startswith("config."):
        cfg = secoes.get("config")
        if not isinstance(cfg, dict):
            return []
        bruto = cfg.get(grupo.split(".", 1)[1])
    else:
        bruto = secoes.get(grupo)
    if not isinstance(bruto, list):
        return []
    return [it for it in bruto if isinstance(it, dict)]


def _tem_grupo(secoes: dict[str, Any], grupo: str) -> bool:
    """A secao veio no pacote? Secao ausente não é secao vazia — sem isto, um
    export só de ambientes apagaria os servidores no modo `replace`."""
    if grupo.startswith("config."):
        cfg = secoes.get("config")
        return isinstance(cfg, dict) and isinstance(cfg.get(grupo.split(".", 1)[1]), list)
    return isinstance(secoes.get(grupo), list)


def _indexar(secoes: dict[str, Any], grupo: str) -> dict[str, dict[str, Any]]:
    campo = _ID_FIELD[grupo]
    out: dict[str, dict[str, Any]] = {}
    for item in _itens(secoes, grupo):
        ident = str(item.get(campo) or "").strip()
        if not ident:
            continue
        if grupo == "config.app_config" and ident in LOCAL_ONLY_KEYS:
            continue
        out[ident] = item
    return out


def _fmt(valor: Any) -> str:
    if valor is None or valor == "":
        return "(vazio)"
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    if isinstance(valor, (list, tuple)):
        return ", ".join(str(v) for v in valor) or "(vazio)"
    if isinstance(valor, dict):
        return json.dumps(valor, ensure_ascii=False)[:120]
    texto = str(valor)
    return texto if len(texto) <= 120 else texto[:117] + "…"


def _campo_dif(grupo: str, campo: str, atual: Any, arquivo: Any) -> dict[str, Any]:
    if campo in _SECRET_FIELDS.get(grupo, frozenset()):
        return {
            "campo": campo,
            "atual": "••••" if atual else "(vazio)",
            "arquivo": "••••" if arquivo else "(vazio)",
            "secreto": True,
        }
    return {"campo": campo, "atual": _fmt(atual), "arquivo": _fmt(arquivo), "secreto": False}


def _resumo_linhas(atual: list[Any], arquivo: list[Any]) -> dict[str, Any] | None:
    """Compara as linhas de um ambiente em números, não linha a linha: quem
    decide um ambiente decide o conjunto, e uma planilha de 60 ramais não cabe
    numa tela de comparação."""
    def indexar(linhas: list[Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for i, ln in enumerate(linhas):
            if not isinstance(ln, dict):
                continue
            out[str(ln.get("numero_ramal") or f"#{i + 1}")] = ln
        return out

    ia, ib = indexar(atual), indexar(arquivo)
    novas = [k for k in ib if k not in ia]
    sumidas = [k for k in ia if k not in ib]
    mudadas = [k for k in ib if k in ia and ia[k] != ib[k]]
    if not (novas or sumidas or mudadas):
        return None
    partes_arquivo = [f"{len(ib)} linha(s)"]
    if novas:
        partes_arquivo.append(f"{len(novas)} só no arquivo")
    if mudadas:
        partes_arquivo.append(f"{len(mudadas)} diferente(s)")
    partes_atual = [f"{len(ia)} linha(s)"]
    if sumidas:
        partes_atual.append(f"{len(sumidas)} só no sistema")
    return {
        "campo": "ramais",
        "atual": " · ".join(partes_atual),
        "arquivo": " · ".join(partes_arquivo),
        "secreto": False,
    }


def _dif_ambiente(atual: dict[str, Any], arquivo: dict[str, Any]) -> list[dict[str, Any]]:
    campos: list[dict[str, Any]] = []
    for campo in ("nome", "modelo_telefone"):
        if atual.get(campo) != arquivo.get(campo):
            campos.append(_campo_dif("environments", campo, atual.get(campo), arquivo.get(campo)))
    cfg_a: dict[str, Any] = atual.get("config_padrao") or {}
    cfg_b: dict[str, Any] = arquivo.get("config_padrao") or {}
    if not isinstance(cfg_a, dict):
        cfg_a = {}
    if not isinstance(cfg_b, dict):
        cfg_b = {}
    divergentes = sorted(k for k in set(cfg_a) | set(cfg_b) if cfg_a.get(k) != cfg_b.get(k))
    for chave in divergentes[:12]:
        campos.append(_campo_dif(
            "environments", f"config: {chave}", cfg_a.get(chave), cfg_b.get(chave),
        ))
    if len(divergentes) > 12:
        campos.append({
            "campo": "config: (demais)",
            "atual": f"e mais {len(divergentes) - 12} campo(s)",
            "arquivo": ", ".join(divergentes[12:18]) + "…",
            "secreto": False,
        })
    linhas_a = atual.get("lines")
    linhas_b = arquivo.get("lines")
    linhas = _resumo_linhas(
        linhas_a if isinstance(linhas_a, list) else [],
        linhas_b if isinstance(linhas_b, list) else [],
    )
    if linhas:
        campos.append(linhas)
    return campos


def _diferencas(grupo: str, atual: dict[str, Any], arquivo: dict[str, Any]) -> list[dict[str, Any]]:
    if grupo == "environments":
        return _dif_ambiente(atual, arquivo)
    ident = _ID_FIELD[grupo]
    campos: list[dict[str, Any]] = []
    for campo in sorted(set(atual) | set(arquivo)):
        if campo == ident:
            continue
        if atual.get(campo) != arquivo.get(campo):
            campos.append(_campo_dif(grupo, campo, atual.get(campo), arquivo.get(campo)))
    return campos


def _rotulo(grupo: str, item: dict[str, Any], ident: str) -> str:
    if grupo == "environments":
        return f"{item.get('nome') or ident} ({ident})"
    return ident


def diff(
    db: DBSession, data: dict[str, Any], sections: tuple[str, ...] = SECTIONS,
) -> dict[str, Any]:
    """Compara o pacote com o estado atual, grupo a grupo.

    Devolve, por grupo: quantos itens são novos, quantos estão idênticos (que
    não viram escrita nenhuma) e a lista de conflitos com os campos que
    divergem. O que só existe no banco aparece em ``ausentes`` — informação que
    só muda alguma coisa no modo ``replace``, onde esses itens são apagados.
    """
    secoes_arquivo = data.get("sections") or {}
    atual = build(db, sections)["sections"]
    grupos: dict[str, Any] = {}
    for grupo in GROUPS:
        if GROUP_SECTION[grupo] not in sections or not _tem_grupo(secoes_arquivo, grupo):
            continue
        do_arquivo = _indexar(secoes_arquivo, grupo)
        do_banco = _indexar(atual, grupo)
        novos: list[dict[str, Any]] = []
        conflitos: list[dict[str, Any]] = []
        identicos = 0
        for ident, item in do_arquivo.items():
            existente = do_banco.get(ident)
            if existente is None:
                novos.append({"id": ident, "label": _rotulo(grupo, item, ident)})
            elif existente == item:
                identicos += 1
            else:
                conflitos.append({
                    "key": f"{grupo}:{ident}",
                    "id": ident,
                    "label": _rotulo(grupo, item, ident),
                    "campos": _diferencas(grupo, existente, item),
                })
        ausentes = [
            {"id": ident, "label": _rotulo(grupo, do_banco[ident], ident)}
            for ident in do_banco
            if ident not in do_arquivo
        ]
        grupos[grupo] = {
            "label": GROUP_LABEL[grupo],
            "section": GROUP_SECTION[grupo],
            "default_side": _DEFAULT_SIDE.get(grupo, "arquivo"),
            "removable": grupo in _REPLACEABLE,
            "identicos": identicos,
            "novos": novos[:_MAX_DETALHES],
            "novos_total": len(novos),
            "conflitos": conflitos[:_MAX_DETALHES],
            "conflitos_total": len(conflitos),
            "ausentes": ausentes[:_MAX_DETALHES],
            "ausentes_total": len(ausentes),
        }
    return {
        "generated_at": data.get("generated_at", ""),
        "app_version": data.get("app_version", ""),
        "groups": grupos,
    }


# --------------------------------------------------------------- aplicacao


def _aplicar_config_kv(db: DBSession, item: dict[str, Any], user_id: int | None) -> None:
    key = str(item["key"])
    secreto = bool(item.get("is_secret"))
    valor = str(item.get("value") or "")
    guardado = _box().encrypt(valor) if (secreto and valor) else valor
    row = db.get(AppConfig, key)
    if row is None:
        db.add(AppConfig(
            key=key, value=guardado, is_secret=secreto,
            updated_at=_now(), updated_by=user_id,
        ))
    else:
        row.value = guardado
        row.is_secret = secreto
        row.updated_at = _now()
        row.updated_by = user_id


def _aplicar_servidor(db: DBSession, item: dict[str, Any]) -> None:
    nome = str(item["nome"])
    host = str(item.get("host") or "")
    token = str(item.get("token") or "")
    verify_ssl = bool(item.get("verify_ssl", True))
    habilitado = bool(item.get("enabled", True))
    existente = next((s for s in uscall_repo.list_servers(db) if s.nome == nome), None)
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


def _aplicar_broker(db: DBSession, item: dict[str, Any]) -> None:
    nome = str(item["nome"])
    ws_path = str(item["ws_path"]) if item.get("ws_path") else None
    fingerprint = str(item["tls_fingerprint"]) if item.get("tls_fingerprint") else None
    campos: dict[str, Any] = {
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
    existente = next((b for b in mqtt_repo.list_brokers(db) if b.nome == nome), None)
    if existente is None:
        mqtt_repo.create_broker(db, nome=nome, **campos)
    else:
        mqtt_repo.update_broker(db, existente, nome=nome, **campos)


def _aplicar_ambiente(db: DBSession, item: dict[str, Any]) -> None:
    """Cria ou sobrescreve o ambiente **com o id do arquivo**.

    Preservar o identificador mantém válidos os links que o operador já tem
    (`/extension-configurator/environments/<id>`) e é o que permite reconhecer
    o mesmo ambiente numa próxima importação — sem isso, cada import criaria
    uma cópia e nunca haveria conflito para decidir.
    """
    nome = str(item.get("nome") or "").strip()
    modelo = str(item.get("modelo_telefone") or "").strip()
    if not nome or not modelo:
        return
    env_id = str(item.get("id") or "").strip() or ec_repo.generate_slug(nome)
    env = db.get(ExtensionEnvironment, env_id)
    if env is None:
        agora = _now()
        env = ExtensionEnvironment(
            id=env_id, nome=nome, modelo_telefone=modelo,
            config_padrao="{}", created_at=agora, updated_at=agora,
        )
        db.add(env)
        db.flush()
    else:
        env.nome = nome
        env.modelo_telefone = modelo
    cfg = item.get("config_padrao")
    if isinstance(cfg, dict):
        ec_repo.update_environment(db, env, config_padrao=cfg)
    linhas = [ln for ln in (item.get("lines") or []) if isinstance(ln, dict)]
    linhas.sort(key=lambda ln: int(ln.get("posicao") or 0))
    ec_repo.save_lines(
        db, env, [{f: str(ln.get(f, "") or "") for f in _LINE_FIELDS} for ln in linhas],
    )


def _aplicar_usuario(db: DBSession, item: dict[str, Any]) -> None:
    username = str(item.get("username") or "")
    senha_hash = str(item.get("password_hash") or "")
    if not username or not senha_hash:
        return
    existente = db.scalar(select(User).where(User.username == username))
    if existente is None:
        db.add(User(
            username=username,
            password_hash=senha_hash,
            role=str(item.get("role") or "admin"),
            must_change_password=bool(item.get("must_change_password", False)),
            created_at=_now(),
        ))
    else:
        existente.password_hash = senha_hash
        existente.role = str(item.get("role") or existente.role)
        existente.must_change_password = bool(item.get("must_change_password", False))


def _aplicar_device(db: DBSession, item: dict[str, Any]) -> None:
    nome = str(item.get("name") or "")
    if not nome:
        return
    srv_id = next(
        (s.id for s in uscall_repo.list_servers(db) if s.nome == str(item.get("uscall_server") or "")),
        None,
    )
    campos = {
        "ip": item.get("ip") or None,
        "mac": item.get("mac") or None,
        "model": item.get("model") or None,
        "notes": item.get("notes") or None,
        "uscall_server_id": srv_id,
    }
    existente = db.scalar(select(Device).where(Device.name == nome))
    if existente is None:
        agora = _now()
        db.add(Device(name=nome, created_at=agora, updated_at=agora, **campos))
    else:
        for k, v in campos.items():
            setattr(existente, k, v)
        existente.updated_at = _now()


def _remover(db: DBSession, grupo: str, ident: str) -> None:
    if grupo == "config.uscall_servers":
        alvo = next((s for s in uscall_repo.list_servers(db) if s.nome == ident), None)
        if alvo is not None:
            uscall_repo.delete_server(db, alvo.id)
    elif grupo == "config.mqtt_brokers":
        broker = next((b for b in mqtt_repo.list_brokers(db) if b.nome == ident), None)
        if broker is not None:
            mqtt_repo.delete_broker(db, broker.id)
    elif grupo == "environments":
        ec_repo.delete_environment(db, ident)


def _aplicar(db: DBSession, grupo: str, item: dict[str, Any], user_id: int | None) -> None:
    if grupo == "config.app_config":
        _aplicar_config_kv(db, item, user_id)
    elif grupo == "config.uscall_servers":
        _aplicar_servidor(db, item)
    elif grupo == "config.mqtt_brokers":
        _aplicar_broker(db, item)
    elif grupo == "environments":
        _aplicar_ambiente(db, item)
    elif grupo == "users":
        _aplicar_usuario(db, item)
    elif grupo == "devices":
        _aplicar_device(db, item)


def apply(
    db: DBSession,
    data: dict[str, Any],
    *,
    sections: tuple[str, ...] = SECTIONS,
    mode: str = "merge",
    decisions: dict[str, str] | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Aplica o pacote ao banco. Tudo em UMA transacao: ou entra inteiro, ou
    nao entra nada — meia restauracao e pior que nenhuma.

    Item **identico** ao que ja existe nao vira escrita. Item em **conflito**
    segue a decisao do operador (``{"<grupo>:<id>": "atual"|"arquivo"}``); sem
    decisao vale o padrao do grupo — ``arquivo``, menos em ``users``.

    ``mode="replace"`` acrescenta uma coisa so: apaga, nos grupos que aceitam,
    o que existe no banco e nao existe no arquivo.
    """
    if mode not in MODES:
        raise BundleError(f"modo invalido: {mode!r}")
    escolhas = normalize_decisions(decisions)
    secoes_arquivo = data.get("sections") or {}
    atual = build(db, sections)["sections"]
    relatorio: dict[str, Any] = {}
    try:
        for grupo in GROUPS:
            if GROUP_SECTION[grupo] not in sections or not _tem_grupo(secoes_arquivo, grupo):
                continue
            do_arquivo = _indexar(secoes_arquivo, grupo)
            do_banco = _indexar(atual, grupo)
            padrao = escolhas.get(grupo, _DEFAULT_SIDE.get(grupo, "arquivo"))
            contagem = {
                "novos": 0, "atualizados": 0, "identicos": 0,
                "mantidos": 0, "removidos": 0,
            }
            for ident, item in do_arquivo.items():
                existente = do_banco.get(ident)
                if existente is None:
                    _aplicar(db, grupo, item, user_id)
                    contagem["novos"] += 1
                elif existente == item:
                    contagem["identicos"] += 1
                elif escolhas.get(f"{grupo}:{ident}", padrao) == "atual":
                    contagem["mantidos"] += 1
                else:
                    _aplicar(db, grupo, item, user_id)
                    contagem["atualizados"] += 1
            if mode == "replace" and grupo in _REPLACEABLE:
                for ident in [i for i in do_banco if i not in do_arquivo]:
                    _remover(db, grupo, ident)
                    contagem["removidos"] += 1
            db.flush()
            relatorio[grupo] = contagem
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"mode": mode, "applied": relatorio}
