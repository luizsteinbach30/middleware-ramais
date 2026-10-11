"""Equipamentos na interface local — telas ``/equipamentos`` e ``/equipamentos/<id>``
(``docs/AGENTE-NOC.md`` item 18) — e o que o menu lateral mostra (``/api/menu``).

**Só leitura.** O cadastro nasce no NOC (verbo ``definir_equipamentos``); aqui não
há rota que grave equipamento. O "Verificar agora" é a mesma leitura avulsa do verbo
``verificar_equipamento``: não mexe no estado, não grava amostra nem evento.

**Nenhum segredo sai daqui:** da credencial vão só ``tipo``, ``usuario`` e
``definida`` — nem o segredo, nem a impressão.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import selectinload

from middleware_monitor.api.deps import get_current_user, get_session, require_admin, require_csrf
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import Equipamento, EventoDeEquipamento, LeituraDeCftv, User
from middleware_monitor.domain.equipamentos import estados, frentes, modulos
from middleware_monitor.domain.equipamentos.lista import parametros_de
from middleware_monitor.domain.noc import estado as estado_noc
from middleware_monitor.domain.noc import executor

log = get_logger("api.equipamentos")

router = APIRouter(tags=["equipamentos"])

EVENTOS_NA_TELA = 30


def _z(valor: datetime | None) -> str | None:
    """UTC com ``Z``, como o resto da API local (o navegador converte para o fuso dele)."""
    return valor.replace(tzinfo=None).isoformat(timespec="seconds") + "Z" if valor else None


def _noc(db: DBSession) -> dict[str, Any]:
    atual = estado_noc.carregar(db)
    return {"url": atual.url, "enrolado": atual.enrolado}


@router.get("/api/menu")
def ver_menu(_user: User = Depends(get_current_user), db: DBSession = Depends(get_session)) -> dict[str, Any]:
    """Módulos aceitos e frentes com equipamento — o menu lateral relê daqui."""
    return frentes.menu(db)


def _ultima_leitura(eq: Equipamento) -> datetime | None:
    horas = [vf.ultima_em for vf in eq.verificacoes if vf.ultima_em is not None]
    return max(horas) if horas else None


@router.get("/api/equipamentos")
def listar(
    frente: str | None = Query(default=None, max_length=16),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    if frente and frente not in frentes.FRENTES:
        raise HTTPException(400, f"frente: use {', '.join(frentes.FRENTES)}.")
    todos = db.scalars(
        select(Equipamento).options(selectinload(Equipamento.verificacoes)).order_by(Equipamento.nome)
    ).all()
    itens = []
    for eq in todos:
        da_frente = frentes.frente_do_tipo(eq.tipo)
        if frente and da_frente != frente:
            continue
        itens.append(
            {
                "id": eq.id,
                "nome": eq.nome,
                "tipo": eq.tipo,
                "frente": da_frente,
                "host": eq.host,
                "fabricante": eq.fabricante,
                "modelo": eq.modelo,
                # A tela do CFTV agrupa a câmera sob o gravador, na ordem do canal.
                "paiId": eq.pai_id,
                "canal": eq.canal,
                "estado": eq.estado,
                "estadoDesde": _z(eq.estado_desde),
                "verificacoes": [{"tipo": vf.tipo, "estado": vf.estado} for vf in eq.verificacoes],
                "ultimaLeitura": _z(_ultima_leitura(eq)),
            }
        )
    return {
        "itens": itens,
        "frente": frente,
        "contagem": {**frentes.contagem_por_frente(db), "total": len(todos)},
        "noc": _noc(db),
    }


def _equipamento(db: DBSession, equipamento_id: str) -> Equipamento:
    eq = db.get(Equipamento, equipamento_id) if len(equipamento_id) <= 24 else None
    if eq is None:
        raise HTTPException(404, "Equipamento não cadastrado neste agente.")
    return eq


def _leitura_de_cftv(db: DBSession, equipamento_id: str) -> dict[str, Any] | None:
    linha = db.get(LeituraDeCftv, equipamento_id)
    if linha is None:
        return None
    try:
        valor = json.loads(linha.leitura or "{}")
    except ValueError:
        return None
    return valor if isinstance(valor, dict) else None


@router.get("/api/equipamentos/{equipamento_id}")
def detalhar(
    equipamento_id: str,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    eq = _equipamento(db, equipamento_id)
    aceitos = modulos.aceitos(db)
    pai = db.get(Equipamento, eq.pai_id) if eq.pai_id else None
    verificacoes = []
    for vf in eq.verificacoes:
        parametros = parametros_de(vf)
        modulo = modulos.modulo_da_verificacao(vf.tipo, parametros)
        conhecido = modulos.POR_NOME.get(modulo)
        verificacoes.append(
            {
                "id": vf.id,
                "tipo": vf.tipo,
                "parametros": parametros,
                "intervaloS": vf.intervalo_s,
                "estado": vf.estado,
                "estadoDesde": _z(vf.estado_desde),
                "falhasSeguidas": vf.falhas_seguidas,
                "ultimoValor": vf.ultimo_valor,
                "ultimaEm": _z(vf.ultima_em),
                "ultimoErro": vf.ultimo_erro,
                "modulo": modulo,
                "moduloRotulo": conhecido.rotulo if conhecido else modulo,
                "moduloAceito": modulo in aceitos,
                "pausada": vf.estado == estados.PAUSADA or modulo not in aceitos,
            }
        )
    eventos = db.scalars(
        select(EventoDeEquipamento)
        .where(EventoDeEquipamento.equipamento_id == eq.id)
        .order_by(EventoDeEquipamento.em.desc(), EventoDeEquipamento.id.desc())
        .limit(EVENTOS_NA_TELA)
    ).all()
    return {
        "id": eq.id,
        "ref": eq.ref,
        "nome": eq.nome,
        "tipo": eq.tipo,
        "frente": frentes.frente_do_tipo(eq.tipo),
        "host": eq.host,
        "mac": eq.mac,
        "fabricante": eq.fabricante,
        "modelo": eq.modelo,
        "pai": {"id": pai.id, "nome": pai.nome} if pai else None,
        "tentativasParaCair": eq.tentativas_para_cair,
        "estado": eq.estado,
        "estadoDesde": _z(eq.estado_desde),
        "verificacoes": verificacoes,
        "cftv": _leitura_de_cftv(db, eq.id),
        # Nunca o segredo nem a impressão: só que existe.
        "credenciais": [
            {"tipo": c.tipo, "usuario": c.usuario, "definida": bool(c.segredo)} for c in eq.credenciais
        ],
        "eventos": [
            {
                "em": _z(ev.em),
                "tipo": ev.tipo,
                "de": ev.de,
                "para": ev.para,
                "detalhe": ev.detalhe,
                "verificacao": ev.verificacao_id,
            }
            for ev in eventos
        ],
        "noc": _noc(db),
        "pode_verificar": user.role == "admin",
    }


@router.post(
    "/api/equipamentos/{equipamento_id}/verificar",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def verificar_agora(
    equipamento_id: str,
    user: User = Depends(require_admin),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    """Roda as verificações do equipamento agora — a mesma função do verbo
    ``verificar_equipamento``. Só leitura: estado, amostras e eventos não mudam."""
    _equipamento(db, equipamento_id)
    r = await executor.verificar_equipamento_agora(equipamento_id)
    if not r.ok:
        status = 404 if (r.resultado or {}).get("recusa") else 502
        raise HTTPException(status, r.erro or "Não deu para verificar.")
    log.info("equipamento_verificado_localmente", equipamento=equipamento_id, operador=user.username)
    return r.resultado or {}
