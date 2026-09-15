"""Enrolamento e estado do agente do NOC — tela ``/system/noc``.

O navegador **nunca** fala com o NOC: ele chama esta API local, e é o servidor do
middleware que abre a conexão para fora (a CSP da interface é
``connect-src 'self'``, e a credencial não passa pelo JavaScript).

Nada aqui loga o código de enrolamento nem a credencial.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import get_current_user, get_session, require_admin, require_csrf
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import User
from middleware_monitor.domain.noc import cliente, estado, manifesto
from middleware_monitor.jobs.noc_agent import apply_noc_schedule, run_noc_heartbeat
from middleware_monitor.version import __version__

log = get_logger("api.noc")

router = APIRouter(prefix="/api/noc", tags=["noc"])


class EnrolarIn(BaseModel):
    codigo: str = Field(min_length=4, max_length=40)
    url: str | None = Field(default=None, max_length=255)


class UrlIn(BaseModel):
    url: str = Field(min_length=3, max_length=255)


# Código do NOC → status HTTP daqui. O que não está no mapa é falha de caminho
# (rede, proxy, TLS): 502, porque quem falhou foi o outro lado.
_STATUS_DO_ERRO = {"CODIGO_INVALIDO": 400, "ENTRADA_INVALIDA": 400, "MUITAS_TENTATIVAS": 429}


def _saida(db: DBSession) -> dict[str, object]:
    atual = estado.carregar(db)
    return {
        **atual.as_dict(),
        "versao_atual": __version__,
        "maquina": manifesto.nome_da_maquina(),
        "url_padrao": estado.URL_PADRAO,
        "url_insegura": atual.url.startswith("http://"),
    }


@router.get("")
def obter(_user: User = Depends(get_current_user), db: DBSession = Depends(get_session)) -> dict[str, object]:
    return _saida(db)


@router.get("/manifesto")
def ver_manifesto(
    _user: User = Depends(get_current_user), db: DBSession = Depends(get_session)
) -> dict[str, object]:
    """O que este agente conta ao NOC — para quem instala conferir antes de enrolar."""
    return manifesto.montar(db)


@router.put("/url", dependencies=[Depends(require_csrf), Depends(require_admin)])
def trocar_url(
    payload: UrlIn, user: User = Depends(require_admin), db: DBSession = Depends(get_session)
) -> dict[str, object]:
    if estado.carregar(db).enrolado:
        # A credencial é daquele NOC. Trocar o endereço com ela guardada faria o
        # agente se apresentar com a identidade de um NOC para outro.
        raise HTTPException(409, "Este agente está enrolado. Desenrole antes de trocar o endereço do NOC.")
    try:
        url = cliente.normalizar_url(payload.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    estado.gravar(db, {estado.KEY_URL: url}, user_id=user.id)
    db.commit()
    return _saida(db)


@router.post("/enrolar", dependencies=[Depends(require_csrf), Depends(require_admin)])
async def enrolar(
    payload: EnrolarIn, user: User = Depends(require_admin), db: DBSession = Depends(get_session)
) -> dict[str, object]:
    atual = estado.carregar(db)
    try:
        url = cliente.normalizar_url(payload.url or atual.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        enrolado = await cliente.enrolar(
            url,
            codigo=payload.codigo.strip(),
            maquina=manifesto.nome_da_maquina(),
            sistema=manifesto.sistema(),
        )
    except cliente.ErroDoNoc as erro:
        log.warning("noc_enrolamento_recusado", codigo_erro=erro.codigo, status=erro.status, url=url)
        raise HTTPException(_STATUS_DO_ERRO.get(erro.codigo, 502), erro.mensagem) from erro

    estado.guardar_credencial(
        db,
        url=url,
        agente_id=enrolado.agente_id,
        credencial=enrolado.credencial,
        intervalo_s=enrolado.intervalo_s,
        user_id=user.id,
    )
    db.commit()
    log.info(
        "noc_enrolado",
        agente_id=enrolado.agente_id,
        url=url,
        operador=user.username,
        reenrolamento=atual.enrolado,
    )

    apply_noc_schedule(estado.carregar(db))
    # O primeiro heartbeat sai agora, e não daqui a um minuto: quem está com o
    # NOC aberto do outro lado quer ver o agente aparecer VIVO na hora.
    await run_noc_heartbeat()
    db.expire_all()
    return _saida(db)


@router.post("/heartbeat", dependencies=[Depends(require_csrf), Depends(require_admin)])
async def testar_agora(db: DBSession = Depends(get_session)) -> dict[str, object]:
    if not estado.carregar(db).enrolado:
        raise HTTPException(409, "Este agente ainda não está enrolado.")
    depois = await run_noc_heartbeat(forcar=True)
    # Um agente que estava revogado e voltou a responder precisa do job de novo.
    apply_noc_schedule(depois)
    db.expire_all()
    return _saida(db)


@router.post("/desenrolar", dependencies=[Depends(require_csrf), Depends(require_admin)])
def desenrolar(
    user: User = Depends(require_admin), db: DBSession = Depends(get_session)
) -> dict[str, object]:
    atual = estado.carregar(db)
    estado.esquecer(db, user_id=user.id)
    db.commit()
    apply_noc_schedule(estado.carregar(db))
    log.warning("noc_desenrolado", agente_id=atual.agente_id, operador=user.username)
    return _saida(db)
