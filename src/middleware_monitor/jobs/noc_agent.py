"""Heartbeat do agente do NOC — ``docs/AGENTE-NOC.md``, item 1 e 4.

Um laço só: a cada ``intervalo`` (vindo do próprio NOC), manda sinal de vida com
a versão, o relógio local e o sha256 do manifesto. Se o NOC pedir, manda o
manifesto inteiro.

**O NOC nunca chama o middleware.** Este job é a única coisa que abre conexão
para ele, sempre de dentro para fora.

Duas regras de ruído, porque todo WARNING vira linha em ``system_logs``:

- **Log só na mudança de situação.** Uma rede de loja fora por um dia daria
  1.440 linhas iguais; dá duas — a queda e a volta.
- **Revogado para.** Revogar é ato deliberado no NOC; insistir a cada minuto só
  enche o log dos dois lados. Credencial recusada, ao contrário, continua
  tentando: se o NOC restaurar um backup, parar a frota inteira seria uma visita
  técnica por site.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.scheduler import add_interval_job, get_scheduler, remove_job, reschedule
from middleware_monitor.domain.noc import certificado, cliente, estado, executor, manifesto, telemetria

log = get_logger("jobs.noc_agent")

JOB_ID = "noc_heartbeat"
JOB_TELEMETRIA = "noc_telemetria"
JOB_TAREFAS = "noc_tarefas"
INTERVALO_TELEMETRIA_S = 60
# Uma loja que ficou dias fora esvazia a fila aos poucos, sem ocupar o ciclo inteiro.
LOTES_POR_CICLO = 10
# Long-poll: o NOC segura até isto (e corta no teto dele, abaixo do timeout do proxy).
ESPERA_LONG_POLL_S = 25
BACKOFF_MAXIMO_S = 300
PODA_A_CADA_S = 3600

_DETALHE_ILEGIVEL = "A chave de cifra desta instalação mudou; enrole de novo com um código do NOC."


def _situacao_do_erro(codigo: str) -> str:
    if codigo == "AGENTE_REVOGADO":
        return estado.REVOGADO
    if codigo in {"CREDENCIAL_INVALIDA", "CERTIFICADO_INVALIDO"}:
        return estado.CREDENCIAL_RECUSADA
    return estado.SEM_CONEXAO


def _registrar_transicao(antes: str, depois: str, detalhe: str | None) -> None:
    if antes == depois:
        return
    if depois == estado.CONECTADO:
        log.info("noc_conectado", antes=antes)
    else:
        log.warning("noc_situacao_mudou", antes=antes, depois=depois, detalhe=detalhe)


async def run_noc_heartbeat(*, forcar: bool = False) -> estado.EstadoNoc:
    """Um ciclo. Devolve o estado depois do ciclo (a tela usa no "Testar agora").

    ``forcar`` tenta mesmo com a situação ``revogado`` — é o botão de quem quer
    confirmar com os próprios olhos.
    """
    agora = datetime.now(UTC)
    with session_factory() as db:
        atual = estado.carregar(db)
        if not atual.enrolado or (atual.situacao == estado.REVOGADO and not forcar):
            return atual
        try:
            credencial = estado.ler_credencial(db)
            certificado.contexto_do_agente()
        except certificado.CertificadoAusente as exc:
            estado.gravar(db, {estado.KEY_SITUACAO: estado.CREDENCIAL_ILEGIVEL, estado.KEY_DETALHE: str(exc)})
            db.commit()
            _registrar_transicao(atual.situacao, estado.CREDENCIAL_ILEGIVEL, str(exc))
            return estado.carregar(db)
        except ValueError:
            estado.gravar(
                db,
                {
                    estado.KEY_SITUACAO: estado.CREDENCIAL_ILEGIVEL,
                    estado.KEY_DETALHE: _DETALHE_ILEGIVEL,
                },
            )
            db.commit()
            _registrar_transicao(atual.situacao, estado.CREDENCIAL_ILEGIVEL, None)
            return estado.carregar(db)
        corpo = manifesto.montar(db)

    tentativa = {estado.KEY_ULTIMA_TENTATIVA: agora.replace(tzinfo=None).isoformat()}
    try:
        resposta = await cliente.heartbeat(
            atual.endereco_do_canal,
            credencial or "",
            relogio_iso=agora.isoformat(timespec="seconds").replace("+00:00", "Z"),
            manifesto_sha256=corpo["sha256"],
        )
    except cliente.ErroDoNoc as erro:
        situacao = _situacao_do_erro(erro.codigo)
        with session_factory() as db:
            estado.gravar(
                db, {**tentativa, estado.KEY_SITUACAO: situacao, estado.KEY_DETALHE: erro.mensagem[:500]}
            )
            db.commit()
            depois = estado.carregar(db)
        _registrar_transicao(atual.situacao, situacao, erro.mensagem)
        if situacao == estado.REVOGADO:
            remove_job(JOB_ID)
            remove_job(JOB_TELEMETRIA)
            remove_job(JOB_TAREFAS)
        return depois

    valores: dict[str, str | None] = {
        **tentativa,
        estado.KEY_SITUACAO: estado.CONECTADO,
        estado.KEY_DETALHE: None,
        estado.KEY_ULTIMO_CONTATO: agora.replace(tzinfo=None).isoformat(),
        estado.KEY_INTERVALO: str(resposta["intervaloHeartbeatS"]),
        estado.KEY_VERSAO_DESEJADA: str(resposta.get("versaoDesejada") or "") or None,
    }

    hora_do_noc = resposta.get("horaDoNoc")
    if isinstance(hora_do_noc, str):
        try:
            # Positivo: o relógio do NOC está à frente do nosso. Serve para a tela
            # dizer "seu relógio está 4 min atrasado" — não para corrigir hora.
            noc = datetime.fromisoformat(hora_do_noc.replace("Z", "+00:00"))
            valores[estado.KEY_OFFSET] = str(round((noc - agora).total_seconds()))
        except ValueError:
            pass

    if resposta.get("enviarManifesto"):
        try:
            await cliente.enviar_manifesto(atual.endereco_do_canal, credencial or "", corpo)
            valores[estado.KEY_MANIFESTO_SHA] = corpo["sha256"]
            valores[estado.KEY_MANIFESTO_EM] = agora.replace(tzinfo=None).isoformat()
        except cliente.ErroDoNoc as erro:
            # O heartbeat chegou; o manifesto não. O próximo heartbeat vai
            # carregar o mesmo hash, o NOC vai pedir de novo — não precisa de
            # retentativa própria.
            valores[estado.KEY_DETALHE] = f"Manifesto não enviado: {erro.mensagem}"[:500]

    canal = resposta.get("urlDoCanal")
    if isinstance(canal, str) and canal:
        # O NOC pode mudar o endereço do canal sem visita: ele avisa pelo próprio canal.
        valores[estado.KEY_URL_CANAL] = cliente.normalizar_url(canal)

    if resposta.get("renovarCertificado"):
        valores.update(await _renovar(atual.endereco_do_canal, credencial or ""))

    with session_factory() as db:
        estado.gravar(db, valores)
        db.commit()
        depois = estado.carregar(db)

    _registrar_transicao(atual.situacao, estado.CONECTADO, None)
    _garantir_tarefas()
    if depois.intervalo_s != atual.intervalo_s:
        # O intervalo é do NOC: mudar em /configuracao lá chega aqui sem visita.
        reschedule(JOB_ID, depois.intervalo_s)
    return depois


async def _renovar(canal: str, credencial: str) -> dict[str, str | None]:
    """Renova o certificado pelo canal. Falhar aqui não derruba o heartbeat: o
    certificado atual continua valendo, e o NOC volta a pedir no próximo ciclo."""
    par = certificado.gerar_par(manifesto.nome_da_maquina())
    try:
        pem = await cliente.renovar_certificado(canal, credencial, par.csr_pem)
        expira = certificado.instalar(par, pem)
    except (cliente.ErroDoNoc, ValueError) as exc:
        certificado.descartar_par(par)
        log.warning("noc_certificado_nao_renovado", motivo=str(exc))
        return {estado.KEY_DETALHE: f"Certificado não renovado: {exc}"[:500]}
    log.info("noc_certificado_renovado", expira_em=expira.isoformat())
    return {estado.KEY_CERTIFICADO_EXPIRA: expira.isoformat()}


async def run_noc_telemetria() -> int:
    """Entrega a telemetria pendente. Devolve quantos lotes o NOC aceitou.

    O cursor só avança com 202. Qualquer falha para o ciclo e fica para o próximo,
    com o mesmo ponto de partida — e o log só registra a mudança (ok → falha → ok).
    """
    entregues = 0
    for _ in range(LOTES_POR_CICLO):
        with session_factory() as db:
            atual = estado.carregar(db)
            if not atual.enrolado or atual.situacao == estado.REVOGADO:
                return entregues
            try:
                credencial = estado.ler_credencial(db) or ""
            except ValueError:
                return entregues
            cur = telemetria.cursores(db)
            lote, novos, mais = telemetria.montar_lote(db, cur)

        try:
            await cliente.enviar_telemetria(atual.endereco_do_canal, credencial, lote)
        except (cliente.ErroDoNoc, certificado.CertificadoAusente) as erro:
            mensagem = getattr(erro, "mensagem", str(erro))
            with session_factory() as db:
                if not estado.carregar(db).telemetria_detalhe:
                    log.warning("noc_telemetria_falhou", motivo=mensagem)
                estado.gravar(db, {estado.KEY_TELEMETRIA_DETALHE: mensagem[:500]})
                db.commit()
            return entregues

        with session_factory() as db:
            if estado.carregar(db).telemetria_detalhe:
                log.info("noc_telemetria_voltou")
            estado.gravar(
                db,
                {
                    **{estado.CURSORES[nome]: str(valor) for nome, valor in novos.items()},
                    estado.KEY_TELEMETRIA_EM: datetime.now(UTC).replace(tzinfo=None).isoformat(),
                    estado.KEY_TELEMETRIA_DETALHE: None,
                },
            )
            db.commit()
        entregues += 1
        if not mais:
            break
    return entregues


# --- Tarefas: o laço de long-poll ------------------------------------------------------
#
# Um só laço, no scheduler que já existe (AGENTE-NOC item 2): um job de disparo
# único que, ao terminar, se agenda de novo — já, se o ciclo foi bem; com backoff e
# jitter, se o NOC não respondeu. Job de intervalo não serve: um long-poll de 25 s
# mais uma escrita de minutos atropelaria o próximo disparo.


@dataclass
class _Laco:
    falhas: int = 0
    em_ciclo: bool = False
    recuperado: bool = False
    podado_em: float = 0.0


_laco = _Laco()


def _atraso_com_jitter(falhas: int) -> float:
    """*Full jitter*: sem ele, uma queda do NOC devolve a frota inteira no mesmo
    milissegundo quando ele volta."""
    teto = min(BACKOFF_MAXIMO_S, 2 ** min(falhas, 10))
    return random.uniform(1.0, max(1.0, float(teto)))  # noqa: S311 - espalhar, não cifrar


async def _entregar_resultados(canal: str, credencial: str) -> bool:
    """Esvazia o outbox. Devolve ``False`` se o NOC não pôde receber (tenta no próximo ciclo)."""
    for pronta in executor.a_entregar():
        try:
            await cliente.enviar_resultado(
                canal, credencial, pronta.tarefa_id, pronta.idempotencia, pronta.corpo
            )
        except cliente.ErroDoNoc as erro:
            if erro.status in (404, 422):
                # A tarefa não é mais deste agente, ou foi reoferecida com outra chave:
                # reenviar não muda a resposta, e insistir travaria o outbox inteiro.
                log.warning(
                    "noc_resultado_descartado",
                    tarefa=pronta.tarefa_id,
                    codigo=erro.codigo,
                    motivo=erro.mensagem,
                )
                executor.marcar_entregue(pronta.tarefa_id)
                continue
            executor.contar_tentativa(pronta.tarefa_id)
            return False
        executor.marcar_entregue(pronta.tarefa_id)
    return True


async def ciclo_de_tarefas() -> float | None:
    """Um long-poll e o que ele trouxer. Devolve em quantos segundos sai o próximo,
    ou ``None`` para o laço parar (não enrolado, revogado)."""
    laco = _laco
    with session_factory() as db:
        atual = estado.carregar(db)
        if not atual.enrolado or atual.situacao == estado.REVOGADO:
            return None
        try:
            credencial = estado.ler_credencial(db) or ""
        except ValueError:
            # Credencial ilegível: o heartbeat já pinta a tela; aqui só não gasta rede.
            return float(BACKOFF_MAXIMO_S)
    if not laco.recuperado:
        executor.recuperar_interrompidas()
        laco.recuperado = True
    canal = atual.endereco_do_canal

    try:
        if not await _entregar_resultados(canal, credencial):
            raise cliente.ErroDoNoc("SEM_CONEXAO", "O NOC não recebeu os resultados pendentes.")
        tarefas = await cliente.buscar_tarefas(canal, credencial, espera_s=ESPERA_LONG_POLL_S)
    except (cliente.ErroDoNoc, certificado.CertificadoAusente) as erro:
        if getattr(erro, "codigo", None) == "AGENTE_REVOGADO":
            return None
        laco.falhas += 1
        if laco.falhas == 1:
            log.warning("noc_tarefas_sem_canal", motivo=getattr(erro, "mensagem", str(erro)))
        return _atraso_com_jitter(laco.falhas)
    if laco.falhas:
        log.info("noc_tarefas_canal_voltou", falhas=laco.falhas)
        laco.falhas = 0

    for tarefa in tarefas:
        if await executor.processar(tarefa, canal=canal, credencial=credencial) is not None:
            # Resultado sai assim que existe; se o NOC não receber agora, o outbox
            # leva no próximo ciclo.
            await _entregar_resultados(canal, credencial)

    agora = datetime.now(UTC).timestamp()
    if agora - laco.podado_em > PODA_A_CADA_S:
        executor.podar()
        laco.podado_em = agora
    return 0.0


async def run_noc_tarefas() -> None:
    _laco.em_ciclo = True
    atraso: float | None = float(BACKOFF_MAXIMO_S)
    try:
        atraso = await ciclo_de_tarefas()
    except Exception as exc:  # o laço não pode morrer de uma exceção
        log.error("noc_tarefas_ciclo_quebrou", erro=f"{type(exc).__name__}: {exc}")
        atraso = 30.0
    finally:
        _laco.em_ciclo = False
        if atraso is None:
            remove_job(JOB_TAREFAS)
        elif get_scheduler().running:
            _armar_tarefas(atraso)


def _armar_tarefas(atraso_s: float) -> None:
    # Pelo menos 1 s: o scheduler precisa ter liberado a instância que acabou
    # (max_instances=1), senão o disparo seguinte é pulado e o laço morre.
    get_scheduler().add_job(
        run_noc_tarefas,
        "date",
        run_date=datetime.now(UTC) + timedelta(seconds=max(1.0, atraso_s)),
        id=JOB_TAREFAS,
        replace_existing=True,
    )


def _garantir_tarefas() -> None:
    """Vigia, chamado a cada heartbeat que deu certo: se o laço sumiu, volta."""
    if get_scheduler().running and not _laco.em_ciclo and not get_scheduler().get_job(JOB_TAREFAS):
        log.warning("noc_tarefas_laco_rearmado")
        _armar_tarefas(1.0)


def apply_noc_schedule(atual: estado.EstadoNoc, *, imediato: bool = False) -> None:
    """Agenda (ou remove) o heartbeat conforme o estado. Chamado no boot, no
    enrolamento e no desenrolamento.

    Sem enrolamento não existe job — e portanto nenhuma conexão com o NOC. É
    também o que mantém a suíte de testes sem HTTP de verdade.

    ``imediato`` é o boot: o primeiro sinal sai em segundos, e não daqui a um
    intervalo — senão todo reinício do serviço pintaria o agente de ATRASADO no
    NOC. No enrolamento não precisa: a própria rota já fez o primeiro heartbeat.
    """
    if not atual.enrolado or atual.situacao == estado.REVOGADO:
        remove_job(JOB_ID)
        remove_job(JOB_TELEMETRIA)
        remove_job(JOB_TAREFAS)
        return
    if not _laco.em_ciclo and not get_scheduler().get_job(JOB_TAREFAS):
        _armar_tarefas(15.0 if imediato else 1.0)
    if not get_scheduler().get_job(JOB_TELEMETRIA):
        extra_t = {"next_run_time": datetime.now(UTC) + timedelta(seconds=20)} if imediato else {}
        add_interval_job(run_noc_telemetria, job_id=JOB_TELEMETRIA, seconds=INTERVALO_TELEMETRIA_S, **extra_t)
    if get_scheduler().get_job(JOB_ID):
        reschedule(JOB_ID, atual.intervalo_s)
        return
    extra = {"next_run_time": datetime.now(UTC) + timedelta(seconds=5)} if imediato else {}
    add_interval_job(run_noc_heartbeat, job_id=JOB_ID, seconds=atual.intervalo_s, **extra)
    log.info("noc_heartbeat_agendado", agente_id=atual.agente_id, intervalo_s=atual.intervalo_s)
