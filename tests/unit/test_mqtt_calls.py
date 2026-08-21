"""Reconstrução de chamadas — exercitada com sequências capturadas do PBX real.

Todas as sequências abaixo são transcrições de tráfego do broker do cliente
(2026-08-21). Isso importa porque a documentação do publicador descreve um
comportamento que os dados **não** confirmam: o campo ``duracao`` não é o início
da chamada (varia em 98% delas) e o ``uniqueid`` não identifica um par de ramais
(um grupo de captura chega a tocar 5).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from middleware_monitor.domain.mqtt.calls import reconstruir

BASE = datetime(2026, 8, 21, 18, 0, 0)


def _ev(
    seg: float, ramal: str, status: str, numero: str | None = None,
    uniqueid: str | None = None, inicio: float | None = None,
) -> SimpleNamespace:
    """Um evento, posicionado em segundos a partir de BASE."""
    return SimpleNamespace(
        id=int(seg * 1000),
        ramal=ramal,
        status=status,
        numero=numero,
        uniqueid=uniqueid,
        received_at=BASE + timedelta(seconds=seg),
        call_started_at=BASE + timedelta(seconds=inicio) if inicio is not None else None,
    )


def _por_ramal(pernas: list, ramal: str) -> list:
    return [p for p in pernas if p.ramal == ramal]


# ── ligação interna ──────────────────────────────────────────────────────────


def test_ligacao_interna_atendida_gera_as_duas_pernas() -> None:
    """Captura real (1211 → 9959, 18:02:49).

    Repare que o **chamador entra direto em `ocupado`, sem número** — o PBX não
    o passa por `discando` numa ligação interna. É por isso que `ocupado` sem
    número, abrindo o trecho, é lido como saída.
    """
    eventos = [
        _ev(0, "1211", "ocupado", None, "X", inicio=-2),
        _ev(0.5, "9959", "tocando", "1211", "X", inicio=-2),
        _ev(8, "9959", "ocupado", "1211", "X", inicio=6),
        _ev(47, "9959", "disponivel"),
        _ev(53, "1211", "disponivel"),
    ]
    pernas = reconstruir(eventos)

    recebida = _por_ramal(pernas, "9959")[0]
    assert recebida.direcao == "entrante"
    assert recebida.numero == "1211"
    assert recebida.outcome == "atendida"
    assert recebida.ring_seconds == 5  # tocou 18:02:49.5 → atendeu 18:02:55(duracao)
    assert recebida.talk_seconds == 41

    origem = _por_ramal(pernas, "1211")[0]
    assert origem.direcao == "sainte"
    assert origem.outcome == "atendida"
    # Nunca tocou: quem liga não toca. Toque nulo é diferente de toque zero.
    assert origem.ring_seconds is None

    assert recebida.uniqueid == origem.uniqueid == "X"


def test_ligacao_de_saida_nao_tem_uniqueid() -> None:
    """Captura real (21320 → 800). Só 8 de 384 `discando` trazem uniqueid."""
    eventos = [
        _ev(0, "21320", "discando", "800", inicio=-2),
        _ev(2.5, "21320", "ocupado", "800", inicio=1),
        _ev(29, "21320", "disponivel"),
    ]
    (perna,) = reconstruir(eventos)
    assert perna.direcao == "sainte"
    assert perna.numero == "800"
    assert perna.outcome == "atendida"
    assert perna.uniqueid is None
    assert perna.talk_seconds == 28


# ── não atendidas ────────────────────────────────────────────────────────────


def test_tocou_e_nao_atendeu_e_perdida() -> None:
    eventos = [
        _ev(0, "9950", "tocando", "1909", "Y"),
        _ev(9, "9950", "disponivel"),
    ]
    (perna,) = reconstruir(eventos)
    assert perna.outcome == "perdida"
    assert perna.ring_seconds == 9
    assert perna.talk_seconds is None


def test_discou_e_ninguem_atendeu_nao_e_perdida() -> None:
    """"Perdida" e "não atendida" são problemas diferentes.

    Perdida é chamada que **entrou** e ninguém atendeu — é problema de
    atendimento. Não atendida é chamada que **saiu** e o outro lado não
    respondeu. Somar as duas esconderia justamente o número que interessa.
    """
    eventos = [
        _ev(0, "4212", "discando", "800"),
        _ev(15, "4212", "disponivel"),
    ]
    (perna,) = reconstruir(eventos)
    assert perna.outcome == "nao_atendida"
    assert perna.direcao == "sainte"


def test_grupo_de_captura_gera_uma_perna_por_ramal_tocado() -> None:
    """Captura real: um mesmo uniqueid tocou 4 ramais em rodízio, sem atendimento.

    Cada ramal ganha a própria perna — do ponto de vista do ramal, ele tocou e
    não foi atendido. O ``uniqueid`` compartilhado é o que permite, mais tarde,
    entender que foi **uma** chamada só.
    """
    eventos = [
        _ev(0, "3660", "tocando", "11966715065", "Z"),
        _ev(8, "3660", "disponivel"),
        _ev(9, "3670", "tocando", "11966715065", "Z"),
        _ev(16, "3670", "disponivel"),
        _ev(18, "3668", "tocando", "11966715065", "Z"),
        _ev(25, "3668", "disponivel"),
    ]
    pernas = reconstruir(eventos)
    assert len(pernas) == 3
    assert {p.ramal for p in pernas} == {"3660", "3670", "3668"}
    assert all(p.outcome == "perdida" for p in pernas)
    assert {p.uniqueid for p in pernas} == {"Z"}


# ── armadilhas medidas em produção ───────────────────────────────────────────


def test_indisponivel_nao_contamina_a_perna_com_uniqueid_velho() -> None:
    """Armadilha real: `indisponivel` ecoa o uniqueid da chamada anterior.

    Visto num ramal cujo registro oscilava: minutos depois de a chamada acabar,
    ele seguia publicando `Indisponivel` carregando o identificador antigo. Se o
    encerramento herdasse esse id, pernas de chamadas diferentes ficariam
    grudadas — e o relatório mostraria uma conversa que nunca existiu.
    """
    eventos = [
        _ev(0, "0318", "ocupado", None, "VELHO", inicio=-2),
        _ev(1, "0318", "indisponivel", None, "VELHO"),
        # Minutos depois, chamada NOVA, sem identificador.
        _ev(300, "0318", "tocando", "5555"),
        _ev(310, "0318", "disponivel"),
    ]
    primeira, segunda = reconstruir(eventos)
    assert primeira.uniqueid == "VELHO"
    assert segunda.uniqueid is None, "a chamada nova não pode herdar o id da velha"
    assert segunda.numero == "5555"


def test_duracao_variando_nao_quebra_o_trecho() -> None:
    """O campo `duracao` muda a cada estado — medido em 98% das chamadas.

    O ADR-0005 dizia que ele era o início da chamada e serviria de chave. Se o
    trecho dependesse dele, esta sequência viraria três chamadas em vez de uma.
    """
    eventos = [
        _ev(0, "9959", "tocando", "1211", inicio=-2),
        _ev(8, "9959", "ocupado", "1211", inicio=6),
        _ev(20, "9959", "ocupado", "1211", inicio=18),
        _ev(40, "9959", "disponivel"),
    ]
    pernas = reconstruir(eventos)
    assert len(pernas) == 1
    assert pernas[0].outcome == "atendida"


def test_numero_e_uniqueid_chegam_depois_e_sao_preenchidos() -> None:
    # O primeiro estado costuma vir sem número; o dado aparece no evento seguinte.
    eventos = [
        _ev(0, "1001", "ocupado", None, None),
        _ev(3, "1001", "ocupado", "800", "W"),
        _ev(30, "1001", "disponivel"),
    ]
    (perna,) = reconstruir(eventos)
    assert perna.numero == "800"
    assert perna.uniqueid == "W"


def test_chamada_ainda_em_curso_fica_aberta() -> None:
    eventos = [_ev(0, "1001", "ocupado", "800", inicio=-2)]
    (perna,) = reconstruir(eventos)
    assert perna.outcome == "em_curso"
    assert perna.ended_at is None
    assert perna.talk_seconds is None


def test_atendimento_usa_a_hora_do_pbx_quando_ela_e_coerente() -> None:
    """`duracao` no `ocupado` é mais preciso que a hora de recebimento.

    O publicador demora alguns segundos para contar o evento; usar a hora de
    recebimento infla o tempo de toque e encolhe o de conversa.
    """
    eventos = [
        _ev(0, "9959", "tocando", "1211"),
        _ev(10, "9959", "ocupado", "1211", inicio=6),  # PBX viu às 6s
        _ev(30, "9959", "disponivel"),
    ]
    (perna,) = reconstruir(eventos)
    assert perna.ring_seconds == 6
    assert perna.talk_seconds == 24


def test_duracao_anterior_ao_inicio_do_trecho_e_ignorada() -> None:
    # Relógio torto no PBX não pode gerar toque negativo nem conversa inflada.
    eventos = [
        _ev(10, "9959", "tocando", "1211"),
        _ev(12, "9959", "ocupado", "1211", inicio=-500),
        _ev(40, "9959", "disponivel"),
    ]
    (perna,) = reconstruir(eventos)
    assert perna.ring_seconds == 2
    assert perna.talk_seconds == 28


def test_quem_ligou_ganha_a_outra_ponta_pelo_uniqueid() -> None:
    """O PBX só manda o número para quem RECEBE.

    O chamador aparece em `ocupado` sem número, e a tela mostraria "feita
    para —" — justamente o dado que o operador foi procurar. Como as duas
    pernas dividem o `uniqueid`, dá para dizer quem era o outro lado.
    """
    eventos = [
        _ev(0, "1211", "ocupado", None, "X"),
        _ev(0.5, "9959", "tocando", "1211", "X"),
        _ev(8, "9959", "ocupado", "1211", "X", inicio=6),
        _ev(47, "9959", "disponivel"),
        _ev(53, "1211", "disponivel"),
    ]
    pernas = reconstruir(eventos)
    origem = _por_ramal(pernas, "1211")[0]
    assert origem.numero == "9959", "a outra ponta sai do par do uniqueid"


def test_grupo_de_captura_nao_inventa_outra_ponta() -> None:
    """Com três ramais no mesmo id não existe "a outra ponta".

    Escolher uma seria inventar — e o número inventado apareceria na tela com a
    mesma cara de dado apurado.
    """
    eventos = [
        _ev(0, "9000", "ocupado", None, "Z"),
        _ev(1, "3660", "tocando", "9000", "Z"),
        _ev(9, "3660", "disponivel"),
        _ev(10, "3670", "tocando", "9000", "Z"),
        _ev(18, "3670", "disponivel"),
        _ev(20, "9000", "disponivel"),
    ]
    pernas = reconstruir(eventos)
    origem = _por_ramal(pernas, "9000")[0]
    assert origem.numero is None
