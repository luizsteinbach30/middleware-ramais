"""A chave e o certificado do agente para o canal mTLS do NOC (ADR 0006 do NOC).

**A chave privada nasce aqui e nunca sai daqui.** No enrolamento o agente gera o
par EC P-256, manda só o pedido de certificado (CSR) e recebe de volta o
certificado assinado pela autoridade do NOC.

Onde fica, e por quê:

- **Em arquivo, no diretório de dados** (``<data_dir>/noc/``), porque o ``ssl`` do
  Python só carrega certificado de cliente a partir de arquivo. No Linux o
  diretório é ``/var/lib/middleware-monitor`` (dono ``mmonitor``); no desktop,
  ``%LOCALAPPDATA%\\MiddlewareMonitor``.
- **A chave vai cifrada** (PKCS#8 com senha), e a senha é derivada da
  ``APP_SECRET_KEY`` por HKDF — a mesma proteção dos outros segredos em repouso. A
  chave copiada sozinha para outra máquina não abre.
- **Troca atômica**: a chave nova é gravada como ``.nova`` e só substitui a atual
  depois que o NOC devolveu o certificado. Renovação que falha no meio deixa o
  agente com o par que ainda funciona.
- **Fora do pacote portável do backup**, por construção: o pacote leva o banco, e
  isto não está no banco.
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import certifi
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.x509.oid import NameOID

from middleware_monitor.settings import get_settings

NOME_DA_CHAVE = "agente.key"
NOME_DO_CERTIFICADO = "agente.crt"
SUFIXO_NOVO = ".nova"


class CertificadoAusente(Exception):
    """Enrolado, mas sem o par de arquivos — ou com a chave que esta instalação não abre."""


@dataclass(frozen=True)
class ParNovo:
    csr_pem: str
    caminho_da_chave: Path


def diretorio() -> Path:
    d = get_settings().data_dir / "noc"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _senha() -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"noc_chave_do_agente_v1").derive(
        get_settings().secret_key.encode("utf-8")
    )


def _gravar_privado(caminho: Path, conteudo: bytes) -> None:
    """Grava com permissão só do dono (onde o sistema de arquivos respeita)."""
    temporario = caminho.with_name(caminho.name + ".tmp")
    fd = os.open(temporario, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(conteudo)
    os.replace(temporario, caminho)


def gerar_par(nome_da_maquina: str) -> ParNovo:
    """Gera a chave nova (gravada como ``.nova``, cifrada) e o CSR dela.

    O nome no CSR é só informativo: o NOC ignora e emite o certificado para o
    identificador que ele mesmo deu ao agente.
    """
    chave = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nome_da_maquina[:60] or "agente")]))
        .sign(chave, hashes.SHA256())
    )
    caminho = diretorio() / (NOME_DA_CHAVE + SUFIXO_NOVO)
    _gravar_privado(
        caminho,
        chave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(_senha()),
        ),
    )
    return ParNovo(csr.public_bytes(serialization.Encoding.PEM).decode("ascii"), caminho)


def instalar(par: ParNovo, certificado_pem: str) -> datetime:
    """Confere que o certificado é da chave nova e troca os dois arquivos.

    Certificado que não casa com a chave não é instalado: um ``load_cert_chain``
    que falhasse só no próximo heartbeat transformaria "o NOC mandou errado" em
    "o agente parou de falar".
    """
    certificado = x509.load_pem_x509_certificate(certificado_pem.encode("ascii"))
    chave = serialization.load_pem_private_key(par.caminho_da_chave.read_bytes(), password=_senha())
    if certificado.public_key().public_numbers() != chave.public_key().public_numbers():  # type: ignore[union-attr]
        raise ValueError("O certificado recebido não corresponde à chave gerada.")
    d = diretorio()
    _gravar_privado(d / NOME_DO_CERTIFICADO, certificado_pem.encode("ascii"))
    os.replace(par.caminho_da_chave, d / NOME_DA_CHAVE)
    return certificado.not_valid_after_utc.replace(tzinfo=None)


def descartar_par(par: ParNovo) -> None:
    par.caminho_da_chave.unlink(missing_ok=True)


def apagar() -> None:
    d = diretorio()
    for nome in (NOME_DA_CHAVE, NOME_DO_CERTIFICADO, NOME_DA_CHAVE + SUFIXO_NOVO):
        (d / nome).unlink(missing_ok=True)


def existe() -> bool:
    d = diretorio()
    return (d / NOME_DA_CHAVE).is_file() and (d / NOME_DO_CERTIFICADO).is_file()


def expira_em() -> datetime | None:
    caminho = diretorio() / NOME_DO_CERTIFICADO
    if not caminho.is_file():
        return None
    return x509.load_pem_x509_certificate(caminho.read_bytes()).not_valid_after_utc.replace(tzinfo=None)


def contexto_do_servidor() -> ssl.SSLContext:
    """TLS que confere o NOC: a cadeia pública (certifi) e, só em laboratório, a
    autoridade extra de ``APP_NOC_CA_EXTRA``. Nunca ``verify=False``."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    extra = get_settings().noc_ca_extra
    if extra:
        ctx.load_verify_locations(cafile=str(extra))
    return ctx


_cache: tuple[str, float, ssl.SSLContext] | None = None


def contexto_do_agente() -> ssl.SSLContext:
    """O TLS do canal: confere o NOC **e** apresenta o certificado do agente.

    Guardado em memória enquanto o arquivo do certificado não muda: abrir a chave
    cifrada custa, e o canal é usado a cada minuto.
    """
    global _cache
    d = diretorio()
    certificado = d / NOME_DO_CERTIFICADO
    if not existe():
        raise CertificadoAusente("Este agente não tem certificado — enrole de novo com um código do NOC.")
    marca = certificado.stat().st_mtime
    if _cache and _cache[0] == str(certificado) and _cache[1] == marca:
        return _cache[2]
    ctx = contexto_do_servidor()
    try:
        ctx.load_cert_chain(str(certificado), str(d / NOME_DA_CHAVE), password=_senha())
    except (ssl.SSLError, ValueError) as exc:
        raise CertificadoAusente(
            "A chave do certificado não abre com a chave desta instalação — "
            "enrole de novo com um código do NOC."
        ) from exc
    _cache = (str(certificado), marca, ctx)
    return ctx


def limpar_cache_para_testes() -> None:
    global _cache
    _cache = None


def agora_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
