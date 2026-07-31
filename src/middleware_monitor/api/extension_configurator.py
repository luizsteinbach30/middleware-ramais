"""Endpoints do Configurador de Ramais.

Padroes do projeto:
  - auth obrigatoria em GET (`get_current_user`)
  - CSRF + admin em mutacoes (`require_csrf`, `require_admin`)
  - Pydantic in/out, structlog, sem logica de dominio aqui (so orquestracao)
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import (
    get_current_user,
    get_session,
    require_admin,
    require_csrf,
)
from middleware_monitor.core.export_crypto import (
    ExportDecryptError,
    decrypt_export,
    encrypt_export,
)
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import (
    ExtensionApplyRun,
    ExtensionEnvironment,
    ExtensionLine,
    User,
)
from middleware_monitor.core.time import iso_utc
from middleware_monitor.domain.extension_configurator import (
    apply as apply_mod,
)
from middleware_monitor.domain.extension_configurator import (
    export as export_mod,
)
from middleware_monitor.domain.extension_configurator import (
    repository as repo,
)
from middleware_monitor.domain.extension_configurator import (
    run_state,
)
from middleware_monitor.domain.extension_configurator.defaults import PHONE_MODELS
from middleware_monitor.domain.extension_configurator.service import (
    adapter_for,
    build_row,
    build_template,
    compute_line_hash,
    compute_statuses,
    line_status,
)
from middleware_monitor.domain.extension_configurator.softkeys import softkey_catalog_for
from middleware_monitor.integrations.network import make_arp_probe, make_ping_probe
from middleware_monitor.integrations.network.base import is_valid_ip

router = APIRouter(prefix="/api/extension-configurator", tags=["extension-configurator"])
log = get_logger("api.extension_configurator")


def _status_resumo(lines: list[ExtensionLine]) -> dict[str, Any]:
    """Agrega o status das linhas do ambiente em 1 categoria + contadores.

    Classifica pelo ``ultimo_status`` gravado pela aplicação (``update_line_status``):
      * ``"ok"``   → applied
      * ``"erro"`` → error
      * ``None``   → registered (se o telefone já está registrado no PBX via
        device vinculado ``available``) ou pending (não está no sistema).

    Resumo rápido para os cards da lista (não recomputa hash — importante com
    muitos ambientes). O status fino por linha, incluindo ``outdated`` quando a
    config muda após aplicar, é calculado na tela de detalhe via
    ``compute_statuses``.
    """
    if not lines:
        return {"applied": 0, "registered": 0, "pending": 0, "error": 0, "agregado": "vazio"}
    applied = sum(1 for ln in lines if ln.ultimo_status == "ok")
    error = sum(1 for ln in lines if ln.ultimo_status == "erro")
    # nunca aplicados que já estão registrados no PBX não contam como pendentes
    registered = sum(
        1 for ln in lines
        if ln.ultimo_status not in ("ok", "erro")
        and ln.device is not None and ln.device.logical_status == "available"
    )
    pending = len(lines) - applied - error - registered
    if error > 0:
        agregado = "erros"
    elif pending > 0:
        agregado = "pendentes"
    else:
        agregado = "ok"
    return {
        "applied": applied, "registered": registered, "pending": pending,
        "error": error, "agregado": agregado,
    }


def _searchable_text(env: ExtensionEnvironment) -> str:
    """Texto de busca client-side (lowercase) — APENAS o nome do ambiente.

    Até a v2.6.0 concatenava também os dados internos da planilha (ramal, IP,
    MAC, user auth...), então buscar "3660" trazia qualquer ambiente que
    tivesse esse ramal — comportamento reportado como bug. O modelo do
    telefone tem filtro dedicado (dropdown) e também fica fora."""
    return (env.nome or "").lower()


def _env_summary(
    env: ExtensionEnvironment, lines: list[ExtensionLine],
) -> dict[str, Any]:
    runs = sorted(env.runs, key=lambda r: r.started_at, reverse=True)
    last = runs[0] if runs else None
    devices_vinculados = sum(1 for ln in lines if ln.device_id is not None)
    return {
        "id": env.id,
        "nome": env.nome,
        "modelo_telefone": env.modelo_telefone,
        "telefones": len(lines),
        "devices_vinculados": devices_vinculados,
        "atualizado_em": iso_utc(env.updated_at),
        "ultima_execucao": _run_dict(last) if last else None,
        "status_resumo": _status_resumo(lines),
        "searchable": _searchable_text(env),
    }


def _line_dict(
    line: ExtensionLine,
    status: dict[str, str],
    device_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": line.id,
        "ip": line.ip,
        "numero_ramal": line.numero_ramal,
        "user_auth": line.user_auth,
        "senha_sip": line.senha_sip,
        "servidor_sip": line.servidor_sip,
        "numero_abreviado": line.numero_abreviado,
        "nome_visivel": line.nome_visivel,
        "ultimo_hash_aplicado": line.ultimo_hash_aplicado,
        "ultimo_status": line.ultimo_status,
        "ultima_aplicacao": iso_utc(line.ultima_aplicacao),
        "ultimo_erro": line.ultimo_erro,
        "ultimo_modelo": line.ultimo_modelo,
        "ultimo_mac": line.ultimo_mac,
        "status": status.get("status", "pending"),
        "hash_atual": status.get("hash_atual", ""),
        "device_id": (device_info or {}).get("device_id"),
        "device_name": (device_info or {}).get("device_name"),
        "device_ip": (device_info or {}).get("device_ip"),
        "device_network_status": (device_info or {}).get("network_status"),
    }


def _run_dict(run: ExtensionApplyRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "environment_id": run.environment_id,
        "started_at": iso_utc(run.started_at),
        "finished_at": iso_utc(run.finished_at),
        "total": run.total,
        "ok": run.ok,
        "falha": run.falha,
        "forcado": run.forcado,
        "operador": run.operador,
    }


# --------------------------------------------------------------------- Models


class EnvironmentCreateIn(BaseModel):
    nome: str
    modelo_telefone: str = "HTEK UC902G"


class EnvironmentUpdateIn(BaseModel):
    nome: str | None = None
    config_padrao: dict[str, Any] | None = None


class EnvironmentDuplicateIn(BaseModel):
    nome: str | None = None  # nome do novo ambiente; vazio = "Cópia de {nome}"


class LinesIn(BaseModel):
    linhas: list[dict[str, Any]]


class ApplyIn(BaseModel):
    selected_ids: list[str] | None = None


class PingBatchIn(BaseModel):
    ips: list[str]
    # Quando informado, o MAC coletado via ARP é persistido nas linhas do ambiente
    # (casado por IP). Sem ele, o ping só reporta — não grava nada.
    environment_id: str | None = None


class PingResult(BaseModel):
    online: bool
    latency_ms: int | None = None
    mac: str | None = None


# Monitor ao vivo: 1 pacote ICMP por IP, timeout curto e concorrencia limitada
# para manter o impacto de rede baixo. Stateless — o front controla o intervalo
# entre rodadas e para o monitor quando a tela muda.
_PING_TIMEOUT_MS = 1000
_PING_CONCURRENCY = 16
_PING_MAX_IPS = 512


class ExportIn(BaseModel):
    passphrase: str


class ImportIn(BaseModel):
    passphrase: str
    blob: str  # envelope JSON (texto) gerado pelo export
    nome: str | None = None  # nome opcional para o ambiente importado


# Campos da linha que entram no export (sem ids/histórico — portável entre instalações).
_EXPORT_LINE_FIELDS = (
    "ip", "numero_ramal", "user_auth", "senha_sip",
    "servidor_sip", "numero_abreviado", "nome_visivel",
)
_EXPORT_SCHEMA_VERSION = 1


# --------------------------------------------------------------------- Routes


@router.get("/phone-models")
def phone_models(_user: User = Depends(get_current_user)) -> dict[str, list[str]]:
    return {"models": PHONE_MODELS}


@router.get("/softkey-catalog")
def softkey_catalog(
    modelo: str = Query(..., description="modelo do telefone do ambiente"),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Opções de tecla programável (slots + tipos) do fabricante do modelo.

    A UI da config padrão usa isto para mostrar as opções nativas de cada
    fabricante. Lista só os tipos com encoding confirmado (aplicáveis com
    segurança).
    """
    return softkey_catalog_for(modelo)


@router.post("/ping")
async def ping_batch(
    payload: PingBatchIn = Body(...),
    _user: User = Depends(get_current_user),
    _csrf: None = Depends(require_csrf),
    db: DBSession = Depends(get_session),
) -> dict[str, PingResult]:
    """Pinga um lote de IPs para o monitor ao vivo do ambiente.

    Dedup + valida IPv4, limita a quantidade e dispara os pings em paralelo
    com um semaforo. Cada IP recebe 1 echo ICMP (via ``make_ping_probe``, que
    no Windows roda ``ping`` sem abrir janela de console no ``.exe``).

    Para cada host que responde, tenta resolver o **MAC** via ARP
    (``arp -a <ip>``): isso lê o cache ARP local do SO — que o proprio ping
    acabou de popular — **sem gerar pacotes extras na rede**. Hosts em outra
    sub-rede/L2 nao aparecem no cache e voltam sem MAC ("nao foi possivel
    coletar"). Se ``environment_id`` vier, persiste os MACs novos nas linhas.

    Reporta ``{ip: {online, latency_ms, mac}}``.
    """
    seen: list[str] = []
    for raw in payload.ips:
        ip = (raw or "").strip()
        if ip and is_valid_ip(ip) and ip not in seen:
            seen.append(ip)
        if len(seen) >= _PING_MAX_IPS:
            break

    probe = make_ping_probe()
    arp = make_arp_probe()
    sem = asyncio.Semaphore(_PING_CONCURRENCY)

    async def _one(ip: str) -> tuple[str, int | None, str | None]:
        async with sem:
            latency = await probe.ping(ip, _PING_TIMEOUT_MS)
            mac = await arp.lookup(ip) if latency is not None else None
        return ip, latency, mac

    triples = await asyncio.gather(*(_one(ip) for ip in seen))

    result = {
        ip: PingResult(online=latency is not None, latency_ms=latency, mac=mac)
        for ip, latency, mac in triples
    }

    if payload.environment_id:
        mac_by_ip = {ip: mac for ip, _lat, mac in triples if mac}
        if repo.update_line_macs(db, payload.environment_id, mac_by_ip):
            db.commit()

    return result


# Placeholder ASCII para mascarar senhas no preview (gera XML válido em todos os
# vendors; o XML real, com a senha de verdade, nunca é exposto na tela).
_MASK_PLAIN = "********"
_SECRET_CFG_KEYS = ("web_password", "nova_web_password", "menu_password", "keylock_password")


def _xml_to_campos(xml_bytes: bytes, *, url_decode: bool) -> list[dict[str, str]]:
    """Converte o XML em pares legíveis {label, valor} para o preview.

    HTEK usa ``<Pnnn para="Label">valor</Pnnn>`` → usa o atributo ``para`` como
    rótulo; demais vendors caem no nome da tag. ``url_decode`` desfaz o
    URL-encode que o HTEK aplica aos valores, deixando o preview legível.
    """
    try:
        # XML gerado pelo nosso próprio adapter (generate_config), não entrada
        # externa não-confiável — sem risco de XXE/billion-laughs.
        root = ET.fromstring(xml_bytes)  # noqa: S314
    except ET.ParseError:
        return []
    campos: list[dict[str, str]] = []
    for el in root.iter():
        text = (el.text or "").strip()
        if not text:
            continue
        valor = urllib.parse.unquote(text) if url_decode else text
        campos.append({"label": el.get("para") or el.tag, "valor": valor})
    return campos


@router.get("/environments/{env_id}/lines/{line_id}/preview")
def preview_line(
    env_id: str,
    line_id: str,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    """Dry-run: mostra o que SERÁ escrito no aparelho, sem tocar nele e sem gravar.

    Reusa o mesmo caminho de geração do apply (`generate_config`). O status/`vai_mudar`
    usa a config REAL (hash fiel); o XML exibido é gerado com as senhas trocadas por um
    placeholder — a senha real nunca chega ao navegador.
    """
    env = repo.get_environment(db, env_id)
    if env is None:
        raise HTTPException(status_code=404, detail="environment_not_found")
    line = repo.get_line(db, line_id)
    if line is None or line.environment_id != env_id:
        raise HTTPException(status_code=404, detail="line_not_found")

    cfg = repo.merged_config_padrao(env)
    adapter = adapter_for(env.modelo_telefone)

    # status fiel usa a config real (sem máscara)
    status = line_status(line, compute_line_hash(env, line))

    # XML de EXIBIÇÃO: senhas trocadas por placeholder antes de gerar
    disp_cfg = dict(cfg)
    for k in _SECRET_CFG_KEYS:
        if disp_cfg.get(k):
            disp_cfg[k] = _MASK_PLAIN
    disp_row = build_row(line, disp_cfg)
    if line.senha_sip:
        disp_row["senha_sip"] = _MASK_PLAIN
    xml_display = adapter.generate_config(build_template(disp_cfg), disp_row)

    # HTEK URL-encoda os valores no XML → desfaz só na lista legível de campos
    is_htek = env.modelo_telefone.lower().startswith("htek")
    return {
        "line_id": line.id,
        "ip": line.ip,
        "numero_ramal": line.numero_ramal,
        "modelo_telefone": env.modelo_telefone,
        "status": status,
        "vai_mudar": status != "applied",
        "campos": _xml_to_campos(xml_display, url_decode=is_htek),
        "xml": xml_display.decode("utf-8", errors="replace"),
    }


@router.post(
    "/environments/{env_id}/export",
    dependencies=[Depends(require_csrf)],
)
def export_environment(
    env_id: str,
    payload: ExportIn = Body(...),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> Response:
    """Exporta o ambiente (config padrão + linhas) cifrado com uma passphrase.

    O arquivo carrega as senhas (SIP/web) para que, ao importar noutra
    instalação, o aparelho receba a credencial correta — por isso é cifrado.
    """
    env = repo.get_environment(db, env_id)
    if env is None:
        raise HTTPException(status_code=404, detail="environment_not_found")
    if not payload.passphrase:
        raise HTTPException(status_code=400, detail="passphrase_required")
    lines = repo.list_lines(db, env_id)
    data = {
        "schema_version": _EXPORT_SCHEMA_VERSION,
        "environment": {
            "nome": env.nome,
            "modelo_telefone": env.modelo_telefone,
            "config_padrao": repo.merged_config_padrao(env),
        },
        "lines": [
            {f: getattr(ln, f) for f in _EXPORT_LINE_FIELDS} for ln in lines
        ],
    }
    blob = encrypt_export(json.dumps(data, ensure_ascii=False).encode("utf-8"), payload.passphrase)
    log.info("ec_environment_exported", env_id=env_id, lines=len(lines))
    return Response(
        content=blob,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{env_id}.mwrenv"'},
    )


@router.post(
    "/environments/import",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def import_environment(
    payload: ImportIn = Body(...),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    """Importa um ambiente de um arquivo cifrado, criando um NOVO ambiente.

    Não sobrescreve nada: em colisão de nome, gera um slug novo
    (``create_environment``). As senhas decifradas vão para o banco como hoje,
    de modo que o ``send`` use o valor correto no aparelho.
    """
    if not payload.passphrase:
        raise HTTPException(status_code=400, detail="passphrase_required")
    try:
        raw = decrypt_export(payload.blob.encode("utf-8"), payload.passphrase)
        data = json.loads(raw)
    except ExportDecryptError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="conteudo_invalido") from exc

    if not isinstance(data, dict) or data.get("schema_version") != _EXPORT_SCHEMA_VERSION:
        raise HTTPException(status_code=400, detail="schema_nao_suportado")
    envd = data.get("environment") or {}
    nome = (payload.nome or envd.get("nome") or "").strip()
    modelo = str(envd.get("modelo_telefone") or "").strip()
    if not nome or not modelo:
        raise HTTPException(status_code=400, detail="ambiente_incompleto")

    new_env = repo.create_environment(db, nome=nome, modelo_telefone=modelo)
    cfg = envd.get("config_padrao")
    if isinstance(cfg, dict):
        repo.update_environment(db, new_env, config_padrao=cfg)
    rows = [
        {f: str(ln.get(f, "") or "") for f in _EXPORT_LINE_FIELDS}
        for ln in (data.get("lines") or [])
        if isinstance(ln, dict)
    ]
    repo.save_lines(db, new_env, rows)
    db.commit()
    log.info("ec_environment_imported", env_id=new_env.id, lines=len(rows))
    return {"id": new_env.id, "nome": new_env.nome, "linhas": len(rows)}


@router.get("/environments")
def list_environments(
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    envs = repo.list_environments(db)
    out = [_env_summary(e, repo.list_lines(db, e.id)) for e in envs]
    return {"environments": out}


@router.get("/export")
def export_environments(
    format: str = Query("xlsx"),
    ids: str = Query("", description="ids separados por vírgula; vazio = todos"),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> Response:
    """Exporta um ou mais ambientes (modelo, configurações e ramais) em XLSX/PDF.

    GET com auth por sessão (sem CSRF) para permitir download direto via link.
    """
    env_ids = [x.strip() for x in ids.split(",") if x.strip()]
    if not env_ids:
        env_ids = [e.id for e in repo.list_environments(db)]
    reports = export_mod.build_reports(db, env_ids)
    if not reports:
        raise HTTPException(404, "nenhum ambiente para exportar")
    fmt = format.lower()
    if fmt == "xlsx":
        data = export_mod.to_xlsx(reports)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    elif fmt == "pdf":
        data = export_mod.to_pdf(reports)
        media = "application/pdf"
        ext = "pdf"
    else:
        raise HTTPException(400, "formato deve ser 'xlsx' ou 'pdf'")
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    fname = f"{reports[0].id}.{ext}" if len(reports) == 1 else f"ambientes_{stamp}.{ext}"
    log.info("environments_exported", count=len(reports), format=fmt)
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post(
    "/environments",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def create_environment(
    payload: EnvironmentCreateIn,
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    nome = payload.nome.strip()
    modelo = payload.modelo_telefone.strip() or "HTEK UC902G"
    if not nome:
        raise HTTPException(400, "nome obrigatorio")
    env = repo.create_environment(db, nome=nome, modelo_telefone=modelo)
    db.commit()
    log.info("environment_created", env_id=env.id, modelo=modelo)
    return _env_summary(env, [])


@router.post(
    "/environments/{env_id}/duplicate",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def duplicate_environment(
    env_id: str,
    payload: EnvironmentDuplicateIn = Body(default_factory=EnvironmentDuplicateIn),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    """Cria um novo ambiente copiando modelo + config padrão (sem ramais)."""
    src = repo.get_environment(db, env_id)
    if src is None:
        raise HTTPException(404, "ambiente nao encontrado")
    nome = (payload.nome or "").strip() or None
    new_env = repo.clone_environment(db, src, nome=nome)
    db.commit()
    log.info("environment_duplicated", src_id=env_id, env_id=new_env.id)
    return _env_summary(new_env, [])


@router.get("/environments/{env_id}")
def environment_detail(
    env_id: str,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    env = repo.get_environment(db, env_id)
    if env is None:
        raise HTTPException(404, "ambiente nao encontrado")
    lines = repo.list_lines(db, env_id)
    statuses = {s["id"]: s for s in compute_statuses(env, lines)}
    devices = repo.get_device_info_for_lines(db, lines)
    return {
        "id": env.id,
        "nome": env.nome,
        "modelo_telefone": env.modelo_telefone,
        "config_padrao": repo.merged_config_padrao(env),
        "criado_em": iso_utc(env.created_at),
        "atualizado_em": iso_utc(env.updated_at),
        "linhas": [
            _line_dict(ln, statuses.get(ln.id, {}), devices.get(ln.id))
            for ln in lines
        ],
    }


@router.put(
    "/environments/{env_id}",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def update_environment(
    env_id: str,
    payload: EnvironmentUpdateIn,
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    env = repo.get_environment(db, env_id)
    if env is None:
        raise HTTPException(404, "ambiente nao encontrado")
    repo.update_environment(
        db, env, nome=payload.nome, config_padrao=payload.config_padrao,
    )
    db.commit()
    return _env_summary(env, repo.list_lines(db, env_id))


@router.delete(
    "/environments/{env_id}",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def delete_environment(
    env_id: str, db: DBSession = Depends(get_session),
) -> dict[str, str]:
    if not repo.delete_environment(db, env_id):
        raise HTTPException(404, "ambiente nao encontrado")
    db.commit()
    log.info("environment_deleted", env_id=env_id)
    return {"deleted": env_id}


@router.put(
    "/environments/{env_id}/lines",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def save_lines(
    env_id: str,
    payload: LinesIn,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    env = repo.get_environment(db, env_id)
    if env is None:
        raise HTTPException(404, "ambiente nao encontrado")
    repo.save_lines(db, env, payload.linhas)
    db.commit()
    return environment_detail(env_id, _user=_user, db=db)


@router.post(
    "/environments/{env_id}/apply",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def apply_environment(
    env_id: str,
    force: bool = Query(default=False),
    rolling_delay_ms: int = Query(default=1000, ge=0, le=60000),
    payload: ApplyIn = Body(default_factory=ApplyIn),
    user: User = Depends(require_admin),
) -> dict[str, Any]:
    selected = payload.selected_ids or None
    try:
        run_id, total = await apply_mod.run_apply(
            env_id,
            force=force,
            selected_ids=selected,
            operador=user.username,
            rolling_delay_ms=rolling_delay_ms,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"run_id": run_id, "total": total}


@router.get("/environments/{env_id}/runs")
def list_runs(
    env_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    runs = repo.list_runs(db, env_id=env_id, limit=limit)
    return {"runs": [_run_dict(r) for r in runs]}


@router.get("/runs")
def list_all_runs(
    limit: int = Query(default=100, ge=1, le=500),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    runs = repo.list_runs(db, limit=limit)
    return {"runs": [_run_dict(r) for r in runs]}


@router.get("/runs/{run_id}/detail")
def run_detail(
    run_id: int,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    """Detalhe de um run: somente as linhas que ESTE run impactou, com o status
    de antes e depois (snapshot imutável gravado na execução).

    Runs antigos (anteriores ao snapshot por linha) caem no fallback: estado
    ATUAL das linhas do ambiente, marcado com ``tem_snapshot=False``.
    """
    db_run = db.get(ExtensionApplyRun, run_id)
    if db_run is None:
        raise HTTPException(404, "run nao encontrado")
    env = repo.get_environment(db, db_run.environment_id)
    env_dict = (
        {"id": env.id, "nome": env.nome, "modelo_telefone": env.modelo_telefone}
        if env else None
    )

    run_lines = repo.list_run_lines(db, run_id)
    if run_lines:
        return {
            "run": _run_dict(db_run),
            "environment": env_dict,
            "tem_snapshot": True,
            "impactadas": [
                {
                    "line_id": rl.line_id,
                    "numero_ramal": rl.numero_ramal,
                    "ip": rl.ip,
                    "nome_visivel": rl.nome_visivel,
                    "status_antes": rl.status_antes,
                    "status_depois": rl.status_depois,
                    "erro": rl.erro,
                    "modelo": rl.modelo,
                    "registro_sip": rl.registro_sip,
                }
                for rl in run_lines
            ],
        }

    # Fallback (runs antigos sem snapshot): estado atual do ambiente.
    lines = repo.list_lines(db, db_run.environment_id) if env else []
    statuses = (
        {s["id"]: s for s in compute_statuses(env, lines)} if env else {}
    )
    devices = repo.get_device_info_for_lines(db, lines) if lines else {}
    return {
        "run": _run_dict(db_run),
        "environment": env_dict,
        "tem_snapshot": False,
        "linhas": [
            _line_dict(ln, statuses.get(ln.id, {}), devices.get(ln.id))
            for ln in lines
        ],
    }


@router.get("/runs/{run_id}/live")
def live_status(
    run_id: str, _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    rs = run_state.get(run_id)
    if rs is None:
        raise HTTPException(404, "run nao encontrado (pode ter expirado da memoria)")
    return {
        "run_id": rs.run_id,
        "env_id": rs.env_id,
        "db_run_id": rs.db_run_id,
        "started_at": rs.started_at,
        "finished_at": rs.finished_at,
        "cancelled": rs.cancelled,
        "summary": rs.summary(),
        "rows": [
            {
                "line_id": r.line_id,
                "ip": r.ip,
                "numero_ramal": r.numero_ramal,
                "stage": r.stage,
                "msg": r.msg,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
            }
            for r in rs.rows
        ],
    }


@router.post(
    "/runs/{run_id}/cancel",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def cancel_run(run_id: str) -> dict[str, bool]:
    cancelled = run_state.cancel(run_id)
    if not cancelled:
        raise HTTPException(409, "run ja finalizado ou inexistente")
    return {"cancelled": True}
