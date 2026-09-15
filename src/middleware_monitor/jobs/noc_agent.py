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

from datetime import UTC, datetime, timedelta

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.scheduler import add_interval_job, get_scheduler, remove_job, reschedule
from middleware_monitor.domain.noc import certificado, cliente, estado, manifesto, telemetria

log = get_logger("jobs.noc_agent")

JOB_ID = "noc_heartbeat"
JOB_TELEMETRIA = "noc_telemetria"
INTERVALO_TELEMETRIA_S = 60
# Uma loja que ficou dias fora esvazia a fila aos poucos, sem ocupar o ciclo inteiro.
LOTES_POR_CICLO = 10

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
        return
    if not get_scheduler().get_job(JOB_TELEMETRIA):
        extra_t = {"next_run_time": datetime.now(UTC) + timedelta(seconds=20)} if imediato else {}
        add_interval_job(run_noc_telemetria, job_id=JOB_TELEMETRIA, seconds=INTERVALO_TELEMETRIA_S, **extra_t)
    if get_scheduler().get_job(JOB_ID):
        reschedule(JOB_ID, atual.intervalo_s)
        return
    extra = {"next_run_time": datetime.now(UTC) + timedelta(seconds=5)} if imediato else {}
    add_interval_job(run_noc_heartbeat, job_id=JOB_ID, seconds=atual.intervalo_s, **extra)
    log.info("noc_heartbeat_agendado", agente_id=atual.agente_id, intervalo_s=atual.intervalo_s)
