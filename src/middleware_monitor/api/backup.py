"""API de backup e restauracao.

Dois caminhos, com riscos diferentes e por isso separados:

* **Pacote portavel** (``.mwrbak``) — configuracao cifrada com passphrase.
  Exportar e importar valem entre instalacoes diferentes; a importacao aplica
  em transacao unica e responde o que entrou.
* **Snapshot** (``.db.gz``) — o banco inteiro desta instalacao. Restaurar nao
  troca o banco na hora: agenda a troca para o proximo boot (ver
  ``domain/backup/snapshot.py``), porque substituir o arquivo com o processo
  escrevendo nele corromperia o banco.

Tudo aqui exige sessao **e** perfil admin: o pacote carrega token do USCall,
senha do broker e senha SIP de cada ramal.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
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
from middleware_monitor.core.models import User
from middleware_monitor.domain.backup import bundle as bundle_mod
from middleware_monitor.domain.backup import snapshot as snap
from middleware_monitor.domain.backup.settings import (
    load_backup_settings,
    load_last_run,
    save_backup_settings,
)
from middleware_monitor.settings import get_settings

router = APIRouter(
    prefix="/api/backup",
    tags=["backup"],
    dependencies=[Depends(require_admin)],
)
log = get_logger("api.backup")

# Teto do upload de pacote portavel. O pacote e JSON de configuracao: 20 MB ja
# comporta milhares de ramais com folga, e o limite evita que um arquivo
# qualquer seja carregado inteiro na memoria.
_MAX_BUNDLE_BYTES = 20 * 1024 * 1024


class ExportIn(BaseModel):
    passphrase: str = Field(min_length=1)
    sections: list[str] = Field(default_factory=list)


class InspectIn(BaseModel):
    blob: str
    passphrase: str = Field(min_length=1)


class DiffIn(BaseModel):
    blob: str
    passphrase: str = Field(min_length=1)
    sections: list[str] = Field(default_factory=list)


class ImportIn(BaseModel):
    blob: str
    passphrase: str = Field(min_length=1)
    sections: list[str] = Field(default_factory=list)
    mode: str = "merge"
    # {"<grupo>:<id>": "atual"|"arquivo"} — só para os itens em conflito; o que
    # não vier segue o padrão do grupo.
    decisions: dict[str, str] = Field(default_factory=dict)


class SettingsIn(BaseModel):
    auto_enabled: bool | None = None
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    keep: int | None = Field(default=None, ge=1, le=365)
    max_mb: int | None = Field(default=None, ge=0, le=1_000_000)
    export_passphrase: str | None = None


class RestoreIn(BaseModel):
    name: str


def _sections(raw: list[str]) -> tuple[str, ...]:
    try:
        return bundle_mod.normalize_sections(raw)
    except bundle_mod.BundleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M")


# ------------------------------------------------------------ pacote portavel


@router.post("/export", dependencies=[Depends(require_csrf)])
def export_bundle(
    payload: ExportIn = Body(...),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> Response:
    """Gera o pacote portavel cifrado e devolve como download."""
    data = bundle_mod.build(db, _sections(payload.sections))
    blob = encrypt_export(bundle_mod.to_bytes(data), payload.passphrase)
    log.info("backup_exported", sections=list(data["sections"].keys()))
    return Response(
        content=blob,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="middleware-{_stamp()}.mwrbak"',
        },
    )


@router.post("/inspect", dependencies=[Depends(require_csrf)])
def inspect_bundle(
    payload: InspectIn = Body(...),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Decifra e resume o pacote — o que a tela mostra antes de restaurar."""
    return bundle_mod.summarize(_decode(payload.blob, payload.passphrase))


@router.post("/diff", dependencies=[Depends(require_csrf)])
def diff_bundle(
    payload: DiffIn = Body(...),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    """Compara o pacote com o que esta no banco, item a item.

    E o passo que faz a importacao deixar de sobrescrever calado: o que esta
    igual nao aparece para decidir (e nao vira escrita), e o que diverge vem
    com os dois valores lado a lado.
    """
    data = _decode(payload.blob, payload.passphrase)
    return bundle_mod.diff(db, data, _sections(payload.sections))


@router.post("/import", dependencies=[Depends(require_csrf)])
def import_bundle(
    payload: ImportIn = Body(...),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    """Aplica o pacote ao banco (transacao unica), respeitando as decisoes."""
    data = _decode(payload.blob, payload.passphrase)
    try:
        relatorio = bundle_mod.apply(
            db, data,
            sections=_sections(payload.sections),
            mode=payload.mode,
            decisions=payload.decisions,
            user_id=user.id,
        )
    except bundle_mod.BundleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.warning(
        "backup_imported",
        mode=payload.mode,
        applied=relatorio["applied"],
        operador=user.username,
    )
    return relatorio


def _decode(blob: str, passphrase: str) -> dict[str, Any]:
    dados = blob.encode("utf-8")
    if len(dados) > _MAX_BUNDLE_BYTES:
        raise HTTPException(status_code=400, detail="arquivo muito grande")
    try:
        raw = decrypt_export(dados, passphrase)
    except ExportDecryptError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return bundle_mod.parse(raw)
    except bundle_mod.BundleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ------------------------------------------------------------------ snapshots


@router.get("/files")
def list_files(_user: User = Depends(get_current_user)) -> dict[str, Any]:
    arquivos = snap.list_backups()
    return {
        "files": [b.as_dict() for b in arquivos],
        "total_bytes": sum(b.size_bytes for b in arquivos),
        "dir": str(get_settings().backups_dir),
    }


@router.post("/snapshot", dependencies=[Depends(require_csrf)])
def create_snapshot(
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    """Gera agora um snapshot do banco e aplica a poda configurada."""
    try:
        caminho = snap.create_snapshot(label="manual")
    except snap.SnapshotError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cfg = load_backup_settings(db)
    removidos = snap.prune(keep=cfg.keep, max_bytes=cfg.max_mb * 1024 * 1024)
    log.info("backup_snapshot_manual", file=caminho.name, operador=user.username)
    return {
        "name": caminho.name,
        "size_bytes": caminho.stat().st_size,
        "pruned": removidos,
    }


@router.get("/files/{name}")
def download_file(
    name: str,
    _user: User = Depends(get_current_user),
) -> Response:
    try:
        caminho = snap.resolve(name)
    except snap.SnapshotError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    media = "application/gzip" if name.endswith(".gz") else "application/octet-stream"
    return Response(
        content=caminho.read_bytes(),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.delete("/files/{name}", dependencies=[Depends(require_csrf)])
def delete_file(
    name: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        snap.delete_backup(name)
    except snap.SnapshotError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    log.warning("backup_file_deleted", file=name, operador=user.username)
    return {"ok": True, "name": name}


@router.post("/restore", dependencies=[Depends(require_csrf)])
def schedule_restore(
    payload: RestoreIn = Body(...),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Agenda a restauracao de um snapshot **da pasta de backups**."""
    try:
        caminho = snap.resolve(payload.name)
        meta = snap.schedule_restore(caminho, origem="pasta de backups")
    except snap.SnapshotError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.warning("backup_restore_scheduled", file=payload.name, operador=user.username)
    return {"ok": True, "pending": meta}


@router.post("/restore/upload", dependencies=[Depends(require_csrf)])
async def schedule_restore_upload(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Agenda a restauracao de um snapshot enviado do computador do operador.

    O upload vai para disco em blocos: o arquivo carrega o banco inteiro e
    pode ter centenas de MB.
    """
    nome = Path(file.filename or "upload.db.gz").name
    if not (nome.endswith(".db.gz") or nome.endswith(".db")):
        raise HTTPException(status_code=400, detail="envie um arquivo .db.gz ou .db")
    destino = get_settings().tmp_dir / f"upload-{os.getpid()}-{nome}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(destino, "wb") as fh:
            while chunk := await file.read(1024 * 1024):
                fh.write(chunk)
        meta = snap.schedule_restore(destino, origem=f"upload ({nome})")
    except snap.SnapshotError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        destino.unlink(missing_ok=True)
    log.warning("backup_restore_scheduled_upload", file=nome, operador=user.username)
    return {"ok": True, "pending": meta}


@router.get("/restore")
def get_pending(_user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {"pending": snap.pending_restore()}


@router.delete("/restore", dependencies=[Depends(require_csrf)])
def cancel_restore(user: User = Depends(get_current_user)) -> dict[str, Any]:
    cancelado = snap.cancel_pending_restore()
    if cancelado:
        log.warning("backup_restore_cancelled", operador=user.username)
    return {"ok": True, "cancelled": cancelado}


# ------------------------------------------------------------- agendamento


@router.get("/settings")
def get_settings_endpoint(
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    cfg = load_backup_settings(db)
    return {**cfg.as_dict(), "last_run": load_last_run(db)}


@router.put("/settings", dependencies=[Depends(require_csrf)])
def put_settings(
    payload: SettingsIn = Body(...),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    incoming = payload.model_dump(exclude_none=True)
    # ``export_passphrase=""`` significa apagar; exclude_none nao pega isso
    # porque "" nao e None — mas o model_dump acima ja o mantem.
    try:
        cfg = save_backup_settings(db, incoming, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from middleware_monitor.jobs.backup import apply_backup_schedule

    apply_backup_schedule(cfg)
    log.info("backup_settings_saved", **cfg.as_dict())
    return {**cfg.as_dict(), "last_run": load_last_run(db)}
