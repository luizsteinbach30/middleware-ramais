"""O executor das tarefas do NOC — ``docs/AGENTE-NOC.md``, itens 3 e 10.

O NOC decide **se** uma tarefa sai (raio, aprovação, frota congelada). Este módulo
decide, de novo e sozinho, **se ela roda aqui** — e chama as funções que a tela
já usa. Nenhuma ação nova nasce aqui: ``normalize`` é ``run_action_on_line``,
reaplicar é ``run_apply`` numa linha só.

As regras que não se re-derivam lendo o código:

- **Lista de permissão local.** O que não está em ``ACOES`` volta como não
  suportado, mesmo que o NOC mande. ``set_ip`` não está, e não vai estar: errar a
  rede de um telefone a 800 km é perder o aparelho até alguém ir lá. Campo com
  nome de rede é recusado antes de qualquer outra conferência.
- **O raio é o daqui.** Se o NOC chamar de LEITURA algo que aqui é escrita, a
  tarefa é recusada: é o raio que decide se ela pode rodar de novo.
- **Escrita que começou nunca roda de novo.** ``iniciada_em`` é gravado e
  confirmado no banco *antes* de tocar o aparelho. Se o serviço cair no meio, a
  resposta é "resultado desconhecido", nunca uma segunda tentativa — ``send_config``
  reinicia o telefone.
- **Escrita sem prazo não começa.** Se o lease já está no fim, o NOC vai marcar
  resultado desconhecido de qualquer jeito; começar agora seria escrever depois
  que o NOC desistiu de esperar.
- **Backup antes de escrever, e é obrigatório.** Sem o snapshot do banco, a
  escrita não sai.
- **Leitura não muda estado.** O ``ping`` a pedido do NOC não grava em
  ``devices``: marcar online um aparelho que estava offline engoliria a volta que
  o vigia de recuperação (``jobs/monitor_devices.py``) usa para reaplicar config.
- **A edição central grava na planilha, nunca no aparelho** (item 13). A lista do
  que pode mudar é daqui, e o ``de`` que o NOC viu é conferido contra o valor
  atual: diferente, nada é gravado — o NOC nunca sobrescreve mudança feita na loja.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import (
    Collection,
    Device,
    ExtensionApplyRunLine,
    ExtensionEnvironment,
    ExtensionLine,
    NocTarefa,
    SystemLog,
)

log = get_logger("noc.executor")

LEITURA = "LEITURA"
ESCRITA = "ESCRITA_REVERSIVEL"

# A mesma forma de ramal que o NOC aceita (api/src/dominio/tarefas/tarefas.ts).
_RAMAL = re.compile(r"^[0-9A-Za-z*#_.-]{1,32}$")
# Id e chave vão para a URL e para o cabeçalho: nada além de letras, dígitos e hífen.
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Nome de campo que fala de rede. A proteção é a lista de permissão por tarefa;
# isto só dá à recusa o nome certo, igual ao NOC.
_CAMPO_DE_REDE = re.compile(
    r"(^|_)(ip|ipv4|ipv6|mascara|netmask|mask|gateway|gw|dns|vlan|vpn|dhcp|http_?port|porta_?http"
    r"|rede|network|pppoe|lldp)(_|$)|^p\d{1,5}$",
    re.IGNORECASE,
)
_CHAVE_SENSIVEL = re.compile(r"senha|password|passwd|secret|token|credencial|authorization|chave", re.I)

# Escrita só começa com pelo menos isto de lease pela frente.
MARGEM_DA_ESCRITA_S = 60
# Reaplicar espera o run terminar (ping + envio + registro SIP). Passou disso, o
# worker continua sozinho e o relatório local conta o fim.
TETO_DO_REAPLICAR_S = 30 * 60
RETENCAO_DIAS = 30
_NIVEIS = {"INFO": None, "WARNING": ("warning", "error", "critical"), "ERROR": ("error", "critical")}


@dataclass(frozen=True)
class Resultado:
    ok: bool
    resultado: dict[str, Any] | None = None
    erro: str | None = None
    bruto: str | None = None
    nao_suportado: bool = False

    def corpo(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "naoSuportado": self.nao_suportado,
            "resultado": self.resultado,
            "bruto": self.bruto,
            "erro": self.erro,
        }


def _recusa(mensagem: str) -> Resultado:
    return Resultado(ok=False, nao_suportado=True, erro=mensagem)


@dataclass(frozen=True)
class Contexto:
    tarefa_id: str
    pedida_por: str | None
    canal: str
    credencial: str
    # time.monotonic() em que o lease do NOC vence.
    prazo: float = field(default=float("inf"))

    @property
    def operador(self) -> str:
        return f"noc:{self.pedida_por or 'desconhecido'}"[:64]


Executar = Callable[[dict[str, Any], Contexto], Awaitable[Resultado]]


@dataclass(frozen=True)
class Acao:
    raio: str
    executar: Executar
    obrigatorios: frozenset[str] = frozenset()
    opcionais: frozenset[str] = frozenset()


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(dt: datetime | None) -> str | None:
    return dt.replace(tzinfo=UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if dt else None


def _descrever(exc: BaseException) -> str:
    """``ConnectTimeout`` do httpx chega com a mensagem vazia — e "ConnectTimeout: "
    na tela do NOC não diz a ninguém que o aparelho não respondeu."""
    import httpx

    texto = str(exc).strip()
    if not texto and isinstance(exc, httpx.TimeoutException):
        texto = "o aparelho não respondeu a tempo"
    elif not texto and isinstance(exc, httpx.ConnectError):
        texto = "não foi possível conectar ao aparelho"
    return f"{type(exc).__name__}: {texto}" if texto else type(exc).__name__


def _sem_segredos(valor: Any) -> Any:
    if isinstance(valor, dict):
        return {k: ("***" if _CHAVE_SENSIVEL.search(str(k)) else _sem_segredos(v)) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_sem_segredos(v) for v in valor]
    return valor


# --- Onde está o ramal ---------------------------------------------------------------


def _linha_unica(db: Any, ramal: str) -> tuple[ExtensionLine | None, str | None]:
    """A linha do ramal, ou o motivo de não ter uma só. Escrita remota mira uma
    linha: com duas, qualquer escolha daqui seria um palpite."""
    linhas = list(db.scalars(select(ExtensionLine).where(ExtensionLine.numero_ramal == ramal)))
    if not linhas:
        return None, f"O ramal {ramal} não está cadastrado em nenhum ambiente deste middleware."
    if len(linhas) > 1:
        return None, (
            f"O ramal {ramal} aparece em {len(linhas)} linhas de ambiente; escrita remota mira "
            "uma linha só. Corrija o cadastro local."
        )
    if not linhas[0].ip:
        return None, f"A linha do ramal {ramal} não tem IP cadastrado."
    return linhas[0], None


def _ip_do_ramal(db: Any, ramal: str) -> tuple[str | None, str | None, str | None]:
    """``(ip, origem, erro)``. O IP sai do inventário daqui — o NOC nunca manda IP."""
    d = db.scalar(select(Device).where(Device.name == ramal))
    if d is not None and d.ip:
        return d.ip, "dispositivo", None
    ips = sorted(
        {
            ip
            for ip in db.scalars(
                select(ExtensionLine.ip).where(ExtensionLine.numero_ramal == ramal, ExtensionLine.ip != "")
            )
        }
    )
    if len(ips) == 1:
        return ips[0], "ambiente", None
    if len(ips) > 1:
        return None, None, f"O ramal {ramal} tem {len(ips)} IPs diferentes nos ambientes; não escolho um."
    return None, None, f"O ramal {ramal} não tem IP conhecido neste middleware."


# --- Leituras ----------------------------------------------------------------------------


async def _ping(p: dict[str, Any], _ctx: Contexto) -> Resultado:
    from middleware_monitor.domain.config.repository import load_config
    from middleware_monitor.integrations.network.factory import make_ping_probe

    ramal = p["ramal"]
    with session_factory() as db:
        ip, origem, erro = _ip_do_ramal(db, ramal)
        timeout_ms = load_config(db).ping_timeout_ms
    if ip is None:
        return Resultado(ok=False, resultado={"ramal": ramal}, erro=erro)
    latencia = await make_ping_probe().ping(ip, timeout_ms)
    return Resultado(
        ok=True,
        resultado={
            "ramal": ramal,
            "ip": ip,
            "origemDoIp": origem,
            "respondeu": latencia is not None,
            "latenciaMs": latencia,
            "timeoutMs": timeout_ms,
        },
    )


async def _status_do_ramal(p: dict[str, Any], _ctx: Contexto) -> Resultado:
    from middleware_monitor.domain.mqtt.parser import logical_from_status, normalize_status
    from middleware_monitor.domain.uscall import repository as uscall_repo
    from middleware_monitor.integrations.uscall_client import UscallClient, UscallError

    ramal = p["ramal"]
    with session_factory() as db:
        d = db.scalar(select(Device).where(Device.name == ramal))
        servidores = uscall_repo.list_servers(db, enabled_only=True)
        # O servidor que coletou o ramal da última vez vem primeiro.
        servidores.sort(key=lambda s: 0 if d is not None and s.id == d.uscall_server_id else 1)
        alvos = [
            (s.nome, s.host, token, s.verify_ssl)
            for s in servidores
            if (token := uscall_repo.load_server_token(s))
        ]
        local = (
            {
                "estadoLogico": d.logical_status,
                "rede": d.network_status,
                "telefonia": d.telephony_status,
                "latenciaMs": d.latency_ms,
                "ultimoPing": _iso(d.last_ping_at),
                "vistoPorUltimo": _iso(d.last_seen_at),
            }
            if d is not None
            else None
        )
    if not alvos:
        return Resultado(
            ok=False, resultado={"ramal": ramal, "local": local}, erro="Nenhum USCall configurado."
        )

    falhas: list[str] = []
    for nome, host, token, verify in alvos:
        try:
            linha = await UscallClient(host, token, verify_ssl=verify, timeout=10.0).fetch_extension(ramal)
        except UscallError as exc:
            # A mensagem do httpx pode carregar a URL, e o token do USCall viaja nela.
            falhas.append(f"{nome}: {type(exc).__name__}: {str(exc).replace(token, '***')}")
            continue
        # fetch_extension devolve a primeira linha quando não acha o ramal exato.
        if linha is None or str(linha.get("ramal", "")) != ramal:
            continue
        status = normalize_status(str(linha.get("status") or ""))
        return Resultado(
            ok=True,
            resultado={
                "ramal": ramal,
                "encontrado": True,
                "servidor": nome,
                "status": status,
                "registrado": logical_from_status(status) == "available",
                "local": local,
            },
            bruto=json.dumps(_sem_segredos(linha), ensure_ascii=False)[:20_000],
        )
    if falhas and len(falhas) == len(alvos):
        return Resultado(
            ok=False, resultado={"ramal": ramal, "local": local}, erro="USCall fora: " + "; ".join(falhas)
        )
    return Resultado(
        ok=True,
        resultado={"ramal": ramal, "encontrado": False, "local": local, "falhas": falhas or None},
    )


def _contagens(db: Any) -> dict[str, Any]:
    por_logico = dict(
        db.execute(select(Device.logical_status, func.count()).group_by(Device.logical_status)).all()
    )
    por_rede = dict(
        db.execute(select(Device.network_status, func.count()).group_by(Device.network_status)).all()
    )
    return {
        "dispositivos": sum(por_logico.values()),
        "porEstadoLogico": por_logico,
        "porRede": por_rede,
    }


async def _coletar_agora(_p: dict[str, Any], _ctx: Contexto) -> Resultado:
    """Só a coleta do USCall. O ciclo de ping fica no horário dele: rodá-lo fora de
    hora pode disparar a reaplicação automática de recuperação — escrita — a partir
    de um pedido de leitura."""
    from middleware_monitor.jobs.collect_extensions import run_collect_extensions

    inicio = _agora()
    await run_collect_extensions()
    with session_factory() as db:
        coletado = db.scalar(select(func.max(Collection.collected_at)).where(Collection.type == "extensions"))
        contagens = _contagens(db)
    if coletado is None or coletado < inicio - timedelta(seconds=1):
        return Resultado(
            ok=False,
            resultado=contagens,
            erro="A coleta não gravou nada: USCall sem configuração ou fora (veja os logs do agente).",
        )
    return Resultado(ok=True, resultado={**contagens, "coletadoEm": _iso(coletado)})


async def _inventario(_p: dict[str, Any], _ctx: Contexto) -> Resultado:
    from middleware_monitor.domain.uscall import repository as uscall_repo
    from middleware_monitor.domain.uscall import saude as uscall_saude
    from middleware_monitor.version import __version__

    with session_factory() as db:
        ambientes = db.execute(
            select(
                ExtensionEnvironment.nome, ExtensionEnvironment.modelo_telefone, func.count(ExtensionLine.id)
            )
            .outerjoin(ExtensionLine, ExtensionLine.environment_id == ExtensionEnvironment.id)
            .group_by(ExtensionEnvironment.id)
            .order_by(ExtensionEnvironment.nome)
        ).all()
        servidores = uscall_repo.list_servers(db, enabled_only=True)
        return Resultado(
            ok=True,
            resultado={
                "versao": __version__,
                **_contagens(db),
                "ambientes": [{"nome": n, "modelo": m, "linhas": int(q)} for n, m, q in ambientes],
                "uscall": [
                    {"nome": s.nome, "alcancavel": uscall_saude.alcancavel(s.nome)} for s in servidores
                ],
            },
        )


async def _capacidades(_p: dict[str, Any], ctx: Contexto) -> Resultado:
    from middleware_monitor.domain.noc import cliente, manifesto

    with session_factory() as db:
        corpo = manifesto.montar(db)
    enviado, falha = True, None
    try:
        await cliente.enviar_manifesto(ctx.canal, ctx.credencial, corpo)
    except cliente.ErroDoNoc as exc:
        enviado, falha = False, exc.mensagem
    return Resultado(ok=True, resultado={"manifesto": corpo, "reenviado": enviado, "falha": falha})


async def _logs(p: dict[str, Any], _ctx: Contexto) -> Resultado:
    limite = int(p.get("linhas") or 100)
    niveis = _NIVEIS[str(p.get("nivel") or "INFO")]
    consulta = select(SystemLog).order_by(SystemLog.timestamp.desc(), SystemLog.id.desc()).limit(limite)
    if niveis:
        consulta = consulta.where(func.lower(SystemLog.level).in_(niveis))
    with session_factory() as db:
        linhas = []
        for r in db.scalars(consulta):
            try:
                contexto = _sem_segredos(json.loads(r.context)) if r.context else None
            except ValueError:
                contexto = None
            linhas.append(
                {
                    "quando": _iso(r.timestamp),
                    "nivel": r.level,
                    "modulo": r.module,
                    "mensagem": r.message[:2000],
                    "contexto": contexto,
                }
            )
    return Resultado(ok=True, resultado={"linhas": linhas})


# --- Escritas ------------------------------------------------------------------------------


async def _backup() -> tuple[str | None, str | None]:
    from middleware_monitor.domain.backup import snapshot

    try:
        arquivo = await asyncio.to_thread(snapshot.create_snapshot, label="noc")
    except (snapshot.SnapshotError, OSError) as exc:
        return None, f"Backup obrigatório falhou; a escrita não foi iniciada: {exc}"
    return arquivo.name, None


def _sem_prazo(ctx: Contexto) -> Resultado | None:
    restante = ctx.prazo - time.monotonic()
    if restante < MARGEM_DA_ESCRITA_S:
        return Resultado(
            ok=False,
            erro=(
                f"A escrita chegou com {max(0, int(restante))} s de prazo; não foi iniciada. "
                "Escrita que perde o prazo não é reenviada — peça de novo no NOC."
            ),
        )
    return None


async def _normalize(p: dict[str, Any], ctx: Contexto) -> Resultado:  # noqa: PLR0911 - uma saída por recusa
    from middleware_monitor.domain.extension_configurator.actions import capabilities_for, run_action_on_line
    from middleware_monitor.integrations.extension_configurator.vendors.base import VendorActionUnsupported

    ramal = p["ramal"]
    with session_factory() as db:
        linha, erro = _linha_unica(db, ramal)
        if linha is None:
            return Resultado(ok=False, resultado={"ramal": ramal, "aplicado": False}, erro=erro)
        linha_id, modelo, ambiente = linha.id, linha.environment.modelo_telefone, linha.environment.nome
    if "normalize" not in capabilities_for(modelo):
        return _recusa(f"O modelo {modelo} não tem normalize homologado.")
    if (sem := _sem_prazo(ctx)) is not None:
        return sem
    backup, erro = await _backup()
    if backup is None:
        return Resultado(ok=False, resultado={"ramal": ramal, "aplicado": False}, erro=erro)
    base = {"ramal": ramal, "ambiente": ambiente, "modelo": modelo, "backup": backup}
    try:
        r = await run_action_on_line(linha_id, "normalize", operador=ctx.operador)
    except VendorActionUnsupported as exc:
        return _recusa(str(exc))
    except Exception as exc:
        return Resultado(ok=False, resultado={**base, "aplicado": False}, erro=_descrever(exc))
    return Resultado(
        ok=r.ok,
        resultado={**base, "aplicado": r.ok, "detalhe": r.detail, "reiniciou": r.rebooted},
        erro=None if r.ok else r.detail,
    )


async def _reaplicar(p: dict[str, Any], ctx: Contexto) -> Resultado:  # noqa: PLR0911 - uma saída por recusa
    from middleware_monitor.domain.extension_configurator import run_state
    from middleware_monitor.domain.extension_configurator.apply import run_apply

    ramal = p["ramal"]
    with session_factory() as db:
        linha, erro = _linha_unica(db, ramal)
        if linha is None:
            return Resultado(ok=False, resultado={"ramal": ramal, "linhas": []}, erro=erro)
        linha_id, env_id = linha.id, linha.environment_id
        modelo, ambiente = linha.environment.modelo_telefone, linha.environment.nome
    if (sem := _sem_prazo(ctx)) is not None:
        return sem
    backup, erro = await _backup()
    if backup is None:
        return Resultado(ok=False, resultado={"ramal": ramal, "linhas": []}, erro=erro)
    base = {"ramal": ramal, "ambiente": ambiente, "modelo": modelo, "backup": backup}
    try:
        run_id, _total = await run_apply(env_id, force=True, selected_ids=[linha_id], operador=ctx.operador)
    except Exception as exc:
        return Resultado(ok=False, resultado={**base, "linhas": []}, erro=_descrever(exc))

    rs = run_state.get(run_id)
    limite = time.monotonic() + TETO_DO_REAPLICAR_S
    while rs is not None and rs.finished_at is None and time.monotonic() < limite:
        await asyncio.sleep(1)
    if rs is None:
        return Resultado(
            ok=False, resultado={**base, "linhas": []}, erro="O run sumiu da memória antes do fim."
        )
    if rs.finished_at is None:
        return Resultado(
            ok=False,
            resultado={**base, "run": rs.db_run_id, "linhas": []},
            erro=(
                f"Sem desfecho em {TETO_DO_REAPLICAR_S // 60} min; "
                f"confira o relatório local do run {rs.db_run_id}."
            ),
        )
    with session_factory() as db:
        linhas = [
            {
                "ramal": rl.numero_ramal,
                "ip": rl.ip,
                "antes": rl.status_antes,
                "depois": rl.status_depois,
                "erro": rl.erro,
                "modelo": rl.modelo,
                "registroSip": rl.registro_sip,
            }
            for rl in db.scalars(
                select(ExtensionApplyRunLine)
                .where(ExtensionApplyRunLine.run_id == rs.db_run_id)
                .order_by(ExtensionApplyRunLine.id)
            )
        ]
    ok = bool(linhas) and all(linha["depois"] == "ok" for linha in linhas)
    erro = (
        None if ok else next((linha["erro"] for linha in linhas if linha["erro"]), "Nenhuma linha aplicada.")
    )
    return Resultado(ok=ok, resultado={**base, "run": rs.db_run_id, "linhas": linhas}, erro=erro)


# --- Edição central (item 13, etapa I5 do NOC) -------------------------------------------

# Os nomes do retrato (``retrato.py``) → a coluna da planilha. Tamanho = o da coluna (migration 0002).
CAMPOS_DA_LINHA: dict[str, tuple[str, int]] = {
    "nomeVisivel": ("nome_visivel", 64),
    "numeroAbreviado": ("numero_abreviado", 32),
}


def _recusa_da_edicao(recusa: str, mensagem: str, **extra: Any) -> Resultado:
    """Recusa com o código do contrato (CONTRATO-DO-AGENTE §10): a tela decide pelo código."""
    return Resultado(ok=False, nao_suportado=True, erro=mensagem, resultado={"recusa": recusa, **extra})


def _nao_permitido(nome: str) -> Resultado:
    rede = " (é de rede)" if _CAMPO_DE_REDE.search(nome) else ""
    return _recusa_da_edicao(
        "CAMPO_NAO_PERMITIDO", f"{nome!r} não é editável pelo NOC{rede}; nada foi gravado."
    )


def _campos_do_pedido(
    p: dict[str, Any], nome: str, permitidos: Any
) -> tuple[list[dict[str, Any]], Resultado | None]:
    """``campos`` na forma do contrato, sem repetição e só com o que a lista daqui permite."""
    campos = p.get("campos")
    if not isinstance(campos, list) or not campos:
        return [], _recusa_da_edicao("VALOR_INVALIDO", "campos: mande ao menos um item.")
    vistos: set[str] = set()
    for c in campos:
        if not isinstance(c, dict) or set(c) != {nome, "de", "para"} or not isinstance(c[nome], str):
            return [], _recusa_da_edicao("VALOR_INVALIDO", f"cada item de campos é {{ {nome}, de, para }}.")
        if c[nome] not in permitidos:
            return [], _nao_permitido(c[nome])
        if c[nome] in vistos:
            return [], _recusa_da_edicao("VALOR_INVALIDO", f"{c[nome]!r} aparece duas vezes no pedido.")
        vistos.add(c[nome])
    return campos, None


def _mesmo(a: Any, b: Any) -> bool:
    """Igual de verdade: ``True`` não é ``1``, e a ordem das chaves não importa."""
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(
        b, sort_keys=True, ensure_ascii=False
    )


def _ambiente_do_pedido(db: Any, p: dict[str, Any]) -> tuple[ExtensionEnvironment | None, Resultado | None]:
    amb = p.get("ambienteId")
    env = db.get(ExtensionEnvironment, amb) if isinstance(amb, str) and 0 < len(amb) <= 64 else None
    if env is None:
        return None, _recusa_da_edicao(
            "AMBIENTE_NAO_ENCONTRADO", f"O ambiente {str(amb)[:64]!r} não existe neste middleware."
        )
    return env, None


def _linha_do_ambiente(
    env: ExtensionEnvironment, ramal: str
) -> tuple[ExtensionLine | None, Resultado | None]:
    linhas = [ln for ln in env.lines if ln.numero_ramal == ramal]
    if not linhas:
        return None, _recusa_da_edicao(
            "RAMAL_NAO_ENCONTRADO", f"O ramal {ramal} não está na planilha do ambiente {env.nome}."
        )
    if len(linhas) > 1:
        return None, _recusa_da_edicao(
            "RAMAL_AMBIGUO",
            f"O ramal {ramal} aparece em {len(linhas)} linhas do ambiente {env.nome}; "
            "corrija a planilha local.",
        )
    return linhas[0], None


def _divergentes_da_linha(linha: ExtensionLine, campos: list[dict[str, Any]]) -> Resultado | None:
    atuais = [
        {"campo": c["campo"], "atual": getattr(linha, CAMPOS_DA_LINHA[c["campo"]][0]) or ""} for c in campos
    ]
    if all(_mesmo(a["atual"], c["de"]) for a, c in zip(atuais, campos, strict=True)):
        return None
    return _recusa_da_edicao(
        "DE_DIVERGENTE",
        "O valor mudou no middleware desde o retrato que o NOC viu; nada foi gravado.",
        atuais=atuais,
    )


async def _editar_linha(p: dict[str, Any], ctx: Contexto) -> Resultado:  # noqa: PLR0911 - uma saída por recusa
    from middleware_monitor.domain.extension_configurator.repository import merged_config_padrao
    from middleware_monitor.domain.extension_configurator.service import (
        adapter_for,
        build_template,
        compute_line_hash,
        line_status,
    )
    from middleware_monitor.integrations.extension_configurator.vendors.base import VendorConfigError

    campos, recusa = _campos_do_pedido(p, "campo", CAMPOS_DA_LINHA)
    if recusa is not None:
        return recusa
    for c in campos:
        teto = CAMPOS_DA_LINHA[c["campo"]][1]
        if not isinstance(c["para"], str) or len(c["para"]) > teto or not isinstance(c["de"], str):
            return _recusa_da_edicao("VALOR_INVALIDO", f"{c['campo']}: texto de até {teto} caracteres.")
    ramal = p["ramal"]
    with session_factory() as db:
        env, recusa = _ambiente_do_pedido(db, p)
        if env is None:
            return recusa  # type: ignore[return-value]
        linha, recusa = _linha_do_ambiente(env, ramal)
        if linha is None:
            return recusa  # type: ignore[return-value]
        if (divergente := _divergentes_da_linha(linha, campos)) is not None:
            return divergente
        # A mesma validação da planilha local: o fabricante recebe estes valores?
        novos = {CAMPOS_DA_LINHA[c["campo"]][0]: c["para"] for c in campos}
        nome = novos.get("nome_visivel", linha.nome_visivel) or linha.numero_ramal
        sonda = {
            "conta_sip": linha.numero_ramal,
            "senha_sip": "sonda",
            "servidor_sip": "192.0.2.1",
            "label": nome,
            "display_name": nome,
            "auth_id": linha.numero_ramal,
            "numero_abreviado": novos.get("numero_abreviado", linha.numero_abreviado),
            "account_active": 1,
        }
        try:
            adapter_for(env.modelo_telefone).generate_config(build_template(merged_config_padrao(env)), sonda)
        except VendorConfigError as exc:
            return _recusa_da_edicao("VALOR_INVALIDO", f"O {env.modelo_telefone} não aceita o valor: {exc}")
        env_id, linha_id = env.id, linha.id
    if (sem := _sem_prazo(ctx)) is not None:
        return sem
    backup, erro = await _backup()
    if backup is None:
        return Resultado(ok=False, resultado={"ambienteId": env_id, "ramal": ramal, "campos": []}, erro=erro)
    with session_factory() as db:
        linha = db.get(ExtensionLine, linha_id)
        if linha is None:
            return _recusa_da_edicao(
                "RAMAL_NAO_ENCONTRADO", f"A linha do ramal {ramal} sumiu durante o backup; nada foi gravado."
            )
        # De novo, já com o backup feito: a planilha pode ter mudado nesse meio-tempo.
        if (divergente := _divergentes_da_linha(linha, campos)) is not None:
            return divergente
        for coluna, valor in novos.items():
            setattr(linha, coluna, valor)
        linha.updated_at = linha.environment.updated_at = _agora()
        db.commit()
    with session_factory() as db:
        linha = db.get(ExtensionLine, linha_id)
        assert linha is not None
        gravados = [
            {"campo": c["campo"], "gravado": getattr(linha, CAMPOS_DA_LINHA[c["campo"]][0])} for c in campos
        ]
        status = line_status(linha, compute_line_hash(linha.environment, linha))
    certo = all(g["gravado"] == c["para"] for g, c in zip(gravados, campos, strict=True))
    return Resultado(
        ok=certo,
        resultado={
            "ambienteId": env_id,
            "ramal": ramal,
            "backup": backup,
            "campos": gravados,
            "status": status,
        },
        erro=None if certo else "A releitura não bate com o pedido; confira a planilha local.",
    )


async def _editar_config(p: dict[str, Any], ctx: Contexto) -> Resultado:  # noqa: PLR0911 - uma saída por recusa
    from middleware_monitor.domain.extension_configurator import repository as repo
    from middleware_monitor.domain.extension_configurator.service import (
        compute_statuses,
        validate_config_padrao,
    )
    from middleware_monitor.domain.noc.retrato import CONFIG_COM_VALOR
    from middleware_monitor.integrations.extension_configurator.vendors.base import VendorConfigError

    campos, recusa = _campos_do_pedido(p, "chave", CONFIG_COM_VALOR)
    if recusa is not None:
        return recusa

    def divergentes(cfg: dict[str, Any]) -> Resultado | None:
        atuais = [{"chave": c["chave"], "atual": cfg.get(c["chave"])} for c in campos]
        if all(_mesmo(a["atual"], c["de"]) for a, c in zip(atuais, campos, strict=True)):
            return None
        return _recusa_da_edicao(
            "DE_DIVERGENTE",
            "A config padrão mudou no middleware desde o retrato que o NOC viu; nada foi gravado.",
            atuais=atuais,
        )

    with session_factory() as db:
        env, recusa = _ambiente_do_pedido(db, p)
        if env is None:
            return recusa  # type: ignore[return-value]
        cfg = repo.merged_config_padrao(env)
        if (divergente := divergentes(cfg)) is not None:
            return divergente
        for c in campos:
            atual = cfg.get(c["chave"])
            # O tipo é o do valor guardado: número continua número, interruptor continua interruptor.
            if atual is not None and type(atual) is not type(c["para"]):
                return _recusa_da_edicao(
                    "VALOR_INVALIDO",
                    f"{c['chave']}: esperado {type(atual).__name__}, veio {type(c['para']).__name__}.",
                )
        novo = {**cfg, **{c["chave"]: c["para"] for c in campos}}
        try:
            validate_config_padrao(env.modelo_telefone, novo)
        except VendorConfigError as exc:
            return _recusa_da_edicao("VALOR_INVALIDO", f"O {env.modelo_telefone} não aceita a config: {exc}")
        env_id = env.id
    if (sem := _sem_prazo(ctx)) is not None:
        return sem
    backup, erro = await _backup()
    if backup is None:
        return Resultado(ok=False, resultado={"ambienteId": env_id, "campos": []}, erro=erro)
    with session_factory() as db:
        env = db.get(ExtensionEnvironment, env_id)
        if env is None:
            return _recusa_da_edicao(
                "AMBIENTE_NAO_ENCONTRADO", "O ambiente sumiu durante o backup; nada foi gravado."
            )
        if (divergente := divergentes(repo.merged_config_padrao(env))) is not None:
            return divergente
        repo.update_environment(db, env, config_padrao={c["chave"]: c["para"] for c in campos})
        db.commit()
    with session_factory() as db:
        env = db.get(ExtensionEnvironment, env_id)
        assert env is not None
        relida = repo.merged_config_padrao(env)
        gravados = [{"chave": c["chave"], "gravado": relida.get(c["chave"])} for c in campos]
        desatualizadas = sum(1 for st in compute_statuses(env, list(env.lines)) if st["status"] == "outdated")
    certo = all(_mesmo(g["gravado"], c["para"]) for g, c in zip(gravados, campos, strict=True))
    return Resultado(
        ok=certo,
        resultado={
            "ambienteId": env_id,
            "backup": backup,
            "campos": gravados,
            "linhasDesatualizadas": desatualizadas,
        },
        erro=None if certo else "A releitura não bate com o pedido; confira a config padrão local.",
    )


# --- A lista de permissão ---------------------------------------------------------------

ACOES: dict[str, Acao] = {
    "ping": Acao(LEITURA, _ping, obrigatorios=frozenset({"ramal"})),
    "status_do_ramal": Acao(LEITURA, _status_do_ramal, obrigatorios=frozenset({"ramal"})),
    "coletar_agora": Acao(LEITURA, _coletar_agora),
    "inventario": Acao(LEITURA, _inventario),
    "capacidades": Acao(LEITURA, _capacidades),
    "logs": Acao(LEITURA, _logs, opcionais=frozenset({"linhas", "nivel"})),
    "normalize": Acao(ESCRITA, _normalize, obrigatorios=frozenset({"ramal"})),
    "reaplicar_config_do_ambiente": Acao(ESCRITA, _reaplicar, obrigatorios=frozenset({"ramal"})),
    "editar_linha_do_ambiente": Acao(
        ESCRITA, _editar_linha, obrigatorios=frozenset({"ambienteId", "ramal", "campos"})
    ),
    "editar_config_do_ambiente": Acao(
        ESCRITA, _editar_config, obrigatorios=frozenset({"ambienteId", "campos"})
    ),
}


def conferir(tipo: str, raio: str, pedido: Any) -> Resultado | None:  # noqa: PLR0911 - uma saída por regra
    """A recusa, ou ``None`` se a tarefa pode rodar aqui. Nada disto toca o banco."""
    acao = ACOES.get(tipo)
    if acao is None:
        return _recusa(f"Este agente não executa {tipo!r} a pedido do NOC.")
    if not isinstance(pedido, dict):
        return _recusa("O pedido precisa ser um objeto.")
    rede = sorted(k for k in pedido if _CAMPO_DE_REDE.search(str(k)))
    if rede:
        return _recusa(f"P_CODE_DE_REDE: nenhuma tarefa remota mexe na rede do aparelho ({', '.join(rede)}).")
    if raio != acao.raio:
        return _recusa(f"O NOC chamou {tipo!r} de {raio}; aqui é {acao.raio}.")
    desconhecidos = sorted(set(pedido) - acao.obrigatorios - acao.opcionais)
    if desconhecidos:
        return _recusa(f"{tipo!r} não aceita: {', '.join(desconhecidos)}.")
    faltando = sorted(acao.obrigatorios - set(pedido))
    if faltando:
        return _recusa(f"{tipo!r} precisa de: {', '.join(faltando)}.")
    if "ramal" in pedido and not (isinstance(pedido["ramal"], str) and _RAMAL.match(pedido["ramal"])):
        return _recusa("ramal inválido.")
    if "linhas" in pedido and not (
        isinstance(pedido["linhas"], int)
        and not isinstance(pedido["linhas"], bool)
        and 1 <= pedido["linhas"] <= 500
    ):
        return _recusa("linhas: use um inteiro entre 1 e 500.")
    if "nivel" in pedido and pedido["nivel"] not in _NIVEIS:
        return _recusa("nivel: use INFO, WARNING ou ERROR.")
    return None


# --- Idempotência local ---------------------------------------------------------------------


@dataclass(frozen=True)
class Pronta:
    """Um resultado gravado, esperando confirmação do NOC."""

    tarefa_id: str
    idempotencia: str
    corpo: dict[str, Any]


def _gravar_resultado(tarefa_id: str, r: Resultado) -> None:
    with session_factory() as db:
        row = db.get(NocTarefa, tarefa_id)
        if row is None:
            return
        row.ok = r.ok
        row.nao_suportado = r.nao_suportado
        row.resultado = json.dumps(r.resultado, ensure_ascii=False) if r.resultado is not None else None
        row.bruto = r.bruto
        row.erro = r.erro
        row.concluida_em = _agora()
        row.entregue_em = None
        db.commit()


def _corpo_gravado(row: NocTarefa) -> dict[str, Any]:
    return Resultado(
        ok=bool(row.ok),
        resultado=json.loads(row.resultado) if row.resultado else None,
        erro=row.erro,
        bruto=row.bruto,
        nao_suportado=row.nao_suportado,
    ).corpo()


async def processar(tarefa: dict[str, Any], *, canal: str, credencial: str) -> Pronta | None:
    """Executa (ou não) uma tarefa entregue pelo NOC e grava o resultado.

    Devolve ``None`` só quando a tarefa não tem como ser respondida (id ou chave
    fora de forma). Todo o resto vira resultado — inclusive a recusa.
    """
    tarefa_id, chave = tarefa.get("id"), tarefa.get("idempotencia")
    if not (
        isinstance(tarefa_id, str) and _ID.match(tarefa_id) and isinstance(chave, str) and _ID.match(chave)
    ):
        log.warning("noc_tarefa_ilegivel", id=str(tarefa_id)[:80])
        return None
    tipo, raio, pedido = str(tarefa.get("tipo") or ""), str(tarefa.get("raio") or ""), tarefa.get("pedido")
    lease = tarefa.get("leaseSegundos")
    prazo = time.monotonic() + lease if isinstance(lease, int) and lease > 0 else float("inf")
    pedida_por = tarefa.get("pedidaPor") if isinstance(tarefa.get("pedidaPor"), str) else None

    with session_factory() as db:
        row = db.get(NocTarefa, tarefa_id)
        if (
            row is not None
            and row.concluida_em is not None
            and (row.idempotencia == chave or row.raio != LEITURA)
        ):
            # Reentrega: devolve o que já foi gravado, sem executar.
            row.entregue_em = None
            db.commit()
            return Pronta(tarefa_id, row.idempotencia, _corpo_gravado(row))
        if row is not None and row.iniciada_em is not None and row.raio != LEITURA:
            # Escrita que começou e não terminou (o serviço caiu no meio): nunca de novo.
            _interromper(db, row)
            db.commit()
            return Pronta(tarefa_id, row.idempotencia, _corpo_gravado(row))
        if row is None:
            row = NocTarefa(id=tarefa_id, recebida_em=_agora())
            db.add(row)
        row.tipo, row.raio, row.idempotencia = tipo[:64], raio[:32], chave
        row.pedido = json.dumps(pedido if isinstance(pedido, dict) else {}, ensure_ascii=False)
        row.pedida_por = (pedida_por or "")[:128] or None
        row.ok = row.resultado = row.bruto = row.erro = row.concluida_em = row.entregue_em = None
        row.nao_suportado = False
        # Gravado e confirmado ANTES de tocar em qualquer coisa.
        row.iniciada_em = _agora()
        db.commit()

    recusa = conferir(tipo, raio, pedido)
    if recusa is not None:
        log.warning("noc_tarefa_recusada", tarefa=tarefa_id, tipo=tipo[:64], motivo=recusa.erro)
        resultado = recusa
    else:
        ctx = Contexto(tarefa_id, pedida_por, canal, credencial, prazo)
        assert isinstance(pedido, dict)
        try:
            resultado = await ACOES[tipo].executar(pedido, ctx)
        except Exception as exc:  # a tarefa sempre ganha resposta
            log.error("noc_tarefa_quebrou", tarefa=tarefa_id, tipo=tipo, erro=_descrever(exc))
            resultado = Resultado(ok=False, erro=_descrever(exc))
        if ACOES[tipo].raio != LEITURA:
            log.info(
                "noc_escrita_executada", tarefa=tarefa_id, tipo=tipo, ok=resultado.ok, operador=ctx.operador
            )
    _gravar_resultado(tarefa_id, resultado)
    return Pronta(tarefa_id, chave, resultado.corpo())


def _interromper(db: Any, row: NocTarefa) -> None:
    row.ok = False
    row.nao_suportado = False
    row.erro = (
        f"A escrita começou em {_iso(row.iniciada_em)} e foi interrompida (o serviço parou no meio). "
        "O resultado é desconhecido e ela não é executada de novo: confira o aparelho."
    )
    row.concluida_em = _agora()
    row.entregue_em = None


def recuperar_interrompidas() -> int:
    """No boot: o que ficou pela metade. Escrita vira resultado desconhecido (e vai
    para o NOC); leitura é esquecida — o NOC reoferece quando o lease vencer, e
    repetir leitura não custa nada."""
    with session_factory() as db:
        pendentes = list(
            db.scalars(
                select(NocTarefa).where(NocTarefa.iniciada_em.is_not(None), NocTarefa.concluida_em.is_(None))
            )
        )
        for row in pendentes:
            if row.raio == LEITURA:
                db.delete(row)
            else:
                _interromper(db, row)
                log.warning("noc_escrita_interrompida", tarefa=row.id, tipo=row.tipo)
        db.commit()
    return len(pendentes)


def a_entregar(limite: int = 20) -> list[Pronta]:
    with session_factory() as db:
        return [
            Pronta(r.id, r.idempotencia, _corpo_gravado(r))
            for r in db.scalars(
                select(NocTarefa)
                .where(NocTarefa.concluida_em.is_not(None), NocTarefa.entregue_em.is_(None))
                .order_by(NocTarefa.concluida_em)
                .limit(limite)
            )
        ]


def marcar_entregue(tarefa_id: str) -> None:
    with session_factory() as db:
        row = db.get(NocTarefa, tarefa_id)
        if row is not None:
            row.entregue_em = _agora()
            row.tentativas_de_entrega += 1
            db.commit()


def contar_tentativa(tarefa_id: str) -> None:
    with session_factory() as db:
        row = db.get(NocTarefa, tarefa_id)
        if row is not None:
            row.tentativas_de_entrega += 1
            db.commit()


def podar(agora: datetime | None = None) -> None:
    corte = (agora or _agora()) - timedelta(days=RETENCAO_DIAS)
    with session_factory() as db:
        db.execute(delete(NocTarefa).where(NocTarefa.entregue_em.is_not(None), NocTarefa.entregue_em < corte))
        db.commit()


def resumo() -> dict[str, Any]:
    """Para a tela do agente: quem mexe no aparelho de longe precisa aparecer aqui também."""
    with session_factory() as db:
        linhas = db.execute(select(NocTarefa.concluida_em, NocTarefa.entregue_em, NocTarefa.ok)).all()
        ultima = db.scalar(
            select(NocTarefa)
            .where(NocTarefa.concluida_em.is_not(None))
            .order_by(NocTarefa.concluida_em.desc())
        )
        ultima_dict = (
            {
                "tipo": ultima.tipo,
                "ok": bool(ultima.ok),
                "pedida_por": ultima.pedida_por,
                "concluida_em": _iso(ultima.concluida_em),
                "erro": ultima.erro,
            }
            if ultima is not None
            else None
        )
    c: Counter[str] = Counter()
    for concluida, entregue, ok in linhas:
        if concluida is None:
            c["em_execucao"] += 1
        elif entregue is None:
            c["a_entregar"] += 1
        else:
            c["ok" if ok else "falhou"] += 1
    return {**c, "ultima": ultima_dict}
