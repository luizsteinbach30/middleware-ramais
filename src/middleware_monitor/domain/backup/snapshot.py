"""Snapshot do banco inteiro: criacao, listagem, poda e restauracao.

O snapshot e uma copia **consistente** do SQLite (``VACUUM INTO``, que le uma
imagem coerente mesmo com o app escrevendo) comprimida com gzip. Diferente do
pacote portavel, ele leva tudo — historico, ledger, segredos como estao — e
serve para recuperar ESTA instalacao.

**Restaurar nao troca o banco a quente.** No Windows o arquivo esta aberto pelo
processo e a substituicao falharia no meio; pior, jobs e o coletor MQTT
continuariam escrevendo no banco antigo. Entao a restauracao e agendada: o
arquivo validado fica como ``restore.pending.db`` e a troca acontece no proximo
boot, antes de o engine abrir o banco (:func:`apply_pending_restore`, chamada
por ``core.db.init_engine``). O banco substituido nao e apagado — vira
``pre-restore-*.db`` na pasta de backups.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from middleware_monitor.settings import get_settings

log = logging.getLogger("backup")

SNAPSHOT_SUFFIX = ".db.gz"
PENDING_DB = "restore.pending.db"
PENDING_META = "restore.pending.json"
# Tabelas que qualquer banco desta aplicacao tem. Servem de sanidade: arquivo
# SQLite valido mas de outro sistema nao pode virar o banco do middleware.
_REQUIRED_TABLES = ("app_config", "users", "devices", "alembic_version")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_REVISION_RE = re.compile(r"""^revision(?::\s*str)?\s*=\s*["']([^"']+)["']""", re.M)


class SnapshotError(Exception):
    """Falha ao criar, validar ou agendar a restauracao de um snapshot."""


@dataclass(frozen=True)
class BackupFile:
    name: str
    size_bytes: int
    modified_at: datetime
    kind: str  # snapshot | bundle | pre-restore

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at.isoformat(timespec="seconds"),
            "kind": self.kind,
        }


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def db_path() -> Path:
    """Caminho do arquivo SQLite em uso.

    Levanta :class:`SnapshotError` se a instalacao nao usa SQLite — o snapshot
    e especifico do arquivo local; com outro banco o backup e do servidor.
    """
    url = get_settings().effective_db_url
    if not url.startswith("sqlite"):
        raise SnapshotError("snapshot disponivel apenas para bancos SQLite")
    raw = url.split("///", 1)[-1]
    return Path(raw)


def backups_dir() -> Path:
    d = get_settings().backups_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _kind_of(name: str) -> str:
    if name.startswith("pre-restore-"):
        return "pre-restore"
    if name.endswith(".mwrbak"):
        return "bundle"
    return "snapshot"


def create_snapshot(*, label: str = "manual") -> Path:
    """Gera ``backup-<data>-<label>.db.gz`` na pasta de backups."""
    origem = db_path()
    if not origem.exists():
        raise SnapshotError("banco de dados nao encontrado")
    destino = backups_dir() / f"backup-{_now_stamp()}-{label}{SNAPSHOT_SUFFIX}"
    bruto = get_settings().tmp_dir / f"snapshot-{os.getpid()}-{_now_stamp()}.db"
    bruto.parent.mkdir(parents=True, exist_ok=True)
    bruto.unlink(missing_ok=True)
    try:
        conn = sqlite3.connect(str(origem))
        try:
            # VACUUM INTO exige que o destino nao exista e escreve uma imagem
            # coerente sem bloquear escritores.
            conn.execute("VACUUM INTO ?", (str(bruto),))
        finally:
            conn.close()
        with open(bruto, "rb") as fin, gzip.open(destino, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout, length=1024 * 1024)
    except sqlite3.Error as exc:
        destino.unlink(missing_ok=True)
        raise SnapshotError(f"falha ao copiar o banco: {exc}") from exc
    finally:
        bruto.unlink(missing_ok=True)
    log.info("snapshot criado: %s (%d bytes)", destino.name, destino.stat().st_size)
    return destino


def list_backups() -> list[BackupFile]:
    """Arquivos da pasta de backups, do mais recente para o mais antigo."""
    out: list[BackupFile] = []
    for p in backups_dir().iterdir():
        if not p.is_file():
            continue
        if not (
            p.name.endswith(SNAPSHOT_SUFFIX)
            or p.name.endswith(".mwrbak")
            or p.name.endswith(".db")
        ):
            continue
        st = p.stat()
        out.append(BackupFile(
            name=p.name,
            size_bytes=st.st_size,
            modified_at=datetime.fromtimestamp(st.st_mtime),
            kind=_kind_of(p.name),
        ))
    out.sort(key=lambda b: b.modified_at, reverse=True)
    return out


def resolve(name: str) -> Path:
    """Caminho de um arquivo da pasta de backups, validando o nome.

    O nome vem da URL; sem esta checagem um ``..`` leria qualquer arquivo do
    disco pela rota de download.
    """
    if not name or not _NAME_RE.match(name) or name in (".", ".."):
        raise SnapshotError("nome de arquivo invalido")
    caminho = (backups_dir() / name).resolve()
    if caminho.parent != backups_dir().resolve() or not caminho.is_file():
        raise SnapshotError("arquivo nao encontrado")
    return caminho


def delete_backup(name: str) -> bool:
    caminho = resolve(name)
    caminho.unlink()
    log.info("backup removido: %s", name)
    return True


def prune(*, keep: int, max_bytes: int) -> list[str]:
    """Apaga os backups excedentes. Devolve os nomes removidos.

    Duas regras somadas: mantem no maximo ``keep`` arquivos e no maximo
    ``max_bytes`` de espaco (0 = sem teto). Vale o corte que vier primeiro. Os
    ``pre-restore-*`` entram na conta de espaco mas nunca sao apagados por
    quantidade: sao a rede de seguranca de uma restauracao recente.
    """
    arquivos = [b for b in list_backups() if b.kind != "pre-restore"]
    removidos: list[str] = []
    for b in (arquivos[keep:] if keep > 0 else []):
        try:
            (backups_dir() / b.name).unlink()
            removidos.append(b.name)
        except OSError as exc:  # pragma: no cover - disco/permissao
            log.warning("falha ao podar %s: %s", b.name, exc)
    if max_bytes > 0:
        restantes = [b for b in list_backups() if b.kind != "pre-restore"]
        total = sum(b.size_bytes for b in restantes)
        # Do mais antigo para o mais novo, sempre preservando o ultimo backup:
        # ficar sem nenhuma copia por causa do teto seria o pior resultado.
        for b in sorted(restantes, key=lambda x: x.modified_at)[:-1]:
            if total <= max_bytes:
                break
            try:
                (backups_dir() / b.name).unlink()
                total -= b.size_bytes
                removidos.append(b.name)
            except OSError as exc:  # pragma: no cover - disco/permissao
                log.warning("falha ao podar %s: %s", b.name, exc)
    if removidos:
        log.info("poda de backups removeu %d arquivo(s)", len(removidos))
    return removidos


def known_revisions() -> set[str]:
    """Revisoes Alembic que ESTA versao do app conhece."""
    versoes = Path(__file__).resolve().parents[2] / "core" / "migrations" / "versions"
    revs: set[str] = set()
    if not versoes.is_dir():
        return revs
    for arquivo in versoes.glob("*.py"):
        try:
            texto = arquivo.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - leitura de disco
            continue
        m = _REVISION_RE.search(texto)
        if m:
            revs.add(m.group(1))
    return revs


def _extract(path: Path, destino: Path) -> None:
    if path.name.endswith(".gz"):
        with gzip.open(path, "rb") as fin, open(destino, "wb") as fout:
            shutil.copyfileobj(fin, fout, length=1024 * 1024)
    else:
        shutil.copyfile(path, destino)


def inspect_file(path: Path) -> dict[str, object]:
    """Valida um snapshot e resume o que ha dentro.

    Confere assinatura SQLite, integridade, tabelas obrigatorias e a revisao de
    migration. Revisao desconhecida = arquivo gerado por uma versao mais nova
    do middleware; restaurar por cima traria um schema que este codigo nao sabe
    ler, entao vira erro e nao aviso.
    """
    tmp = get_settings().tmp_dir / f"inspect-{os.getpid()}-{_now_stamp()}.db"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    revisao_str = ""
    contagens: dict[str, int] = {}
    try:
        try:
            _extract(path, tmp)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise SnapshotError(f"arquivo ilegivel: {exc}") from exc
        with open(tmp, "rb") as fh:
            if fh.read(16) != b"SQLite format 3\x00":
                raise SnapshotError("o arquivo nao e um banco SQLite")
        conn = sqlite3.connect(str(tmp))
        try:
            integridade = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integridade != "ok":
                raise SnapshotError(f"banco corrompido ({integridade})")
            tabelas = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            faltando = [t for t in _REQUIRED_TABLES if t not in tabelas]
            if faltando:
                raise SnapshotError(
                    "banco de outro sistema (faltam tabelas: " + ", ".join(faltando) + ")"
                )
            revisao = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            revisao_str = str(revisao[0]) if revisao else ""
            conhecidas = known_revisions()
            if conhecidas and revisao_str not in conhecidas:
                raise SnapshotError(
                    f"backup gerado por uma versao mais nova (migration {revisao_str}); "
                    "atualize o middleware antes de restaurar"
                )
            for tabela in ("devices", "extension_environments", "extension_lines", "users"):
                if tabela in tabelas:
                    # Nome de tabela nao entra como parametro em SQL; a lista e
                    # literal aqui no codigo, entao nao ha entrada do usuario.
                    contagens[tabela] = int(
                        conn.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]  # noqa: S608
                    )
        finally:
            conn.close()
    finally:
        tmp.unlink(missing_ok=True)
    return {
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "migration": revisao_str,
        "counts": contagens,
    }


def schedule_restore(path: Path, *, origem: str = "arquivo") -> dict[str, object]:
    """Valida o snapshot e agenda a troca do banco para o proximo boot."""
    resumo = inspect_file(path)
    alvo = db_path().parent
    alvo.mkdir(parents=True, exist_ok=True)
    pendente = alvo / PENDING_DB
    pendente.unlink(missing_ok=True)
    _extract(path, pendente)
    meta = {
        "source": path.name,
        "origin": origem,
        "scheduled_at": datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds"),
        "migration": resumo.get("migration", ""),
        "counts": resumo.get("counts", {}),
    }
    (alvo / PENDING_META).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    log.info("restauracao agendada a partir de %s", path.name)
    return meta


def pending_restore() -> dict[str, object] | None:
    """Metadados da restauracao agendada, ou ``None``."""
    try:
        alvo = db_path().parent
    except SnapshotError:
        return None
    if not (alvo / PENDING_DB).exists():
        return None
    meta_path = alvo / PENDING_META
    if meta_path.exists():
        try:
            return dict(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return {"source": "(desconhecido)", "origin": "arquivo"}


def cancel_pending_restore() -> bool:
    try:
        alvo = db_path().parent
    except SnapshotError:
        return False
    achou = (alvo / PENDING_DB).exists()
    (alvo / PENDING_DB).unlink(missing_ok=True)
    (alvo / PENDING_META).unlink(missing_ok=True)
    if achou:
        log.info("restauracao pendente cancelada")
    return achou


def apply_pending_restore() -> dict[str, object] | None:
    """Troca o banco pelo snapshot pendente. Chamada no boot, antes do engine.

    Nao levanta excecao: se a troca falhar, o app precisa subir com o banco
    atual e mostrar o erro, e nao ficar sem subir. O arquivo pendente e mantido
    para uma nova tentativa.
    """
    try:
        atual = db_path()
    except SnapshotError:
        return None
    pendente = atual.parent / PENDING_DB
    if not pendente.exists():
        return None
    meta = pending_restore() or {}
    destino_seguranca = ""
    try:
        if atual.exists():
            destino_seguranca = f"pre-restore-{_now_stamp()}.db"
            shutil.move(str(atual), str(backups_dir() / destino_seguranca))
        for sufixo in ("-wal", "-shm"):
            Path(str(atual) + sufixo).unlink(missing_ok=True)
        shutil.move(str(pendente), str(atual))
        (atual.parent / PENDING_META).unlink(missing_ok=True)
    except OSError as exc:
        log.error("falha ao aplicar restauracao pendente: %s", exc)
        return {"status": "erro", "error": str(exc), **meta}
    log.warning(
        "banco restaurado de %s (o anterior virou %s)",
        meta.get("source", "?"), destino_seguranca or "(nenhum)",
    )
    return {"status": "ok", "previous_db": destino_seguranca, **meta}
