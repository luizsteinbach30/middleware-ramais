"""Resolução da hora que vai para o telefone.

O que estes testes protegem:

1. **O caso normal não exige digitar nada** — sem configuração, o telefone
   recebe o fuso detectado do servidor.
2. **A precedência é respeitada** — ambiente vence a instalação, que vence a
   detecção.
3. **Nada disso pode quebrar** — fuso inválido, `tzlocal` ausente ou banco fora
   do ar caem para o próximo nível em vez de levantar. Esta resolução roda no
   caminho que calcula o status de toda a planilha de ramais.
4. **O hash não muda** para quem já estava em São Paulo — senão a atualização
   marcaria toda a planilha como desatualizada e pediria reaplicação (com
   reboot em vários modelos) sem nenhum ganho.
"""

from __future__ import annotations

import pytest

from middleware_monitor.core import timezone as core_tz
from middleware_monitor.domain.extension_configurator import time_settings as ts

SP = "America/Sao_Paulo"
MANAUS = "America/Manaus"


@pytest.fixture(autouse=True)
def _servidor_em_sao_paulo(monkeypatch: pytest.MonkeyPatch):
    """Fixa o servidor em São Paulo para os testes não dependerem da máquina."""
    monkeypatch.setattr(
        core_tz, "detect_server_timezone",
        lambda: core_tz.ServerTimezone(name=SP, offset_minutes=-180, source="tzlocal"),
    )
    ts.invalidate_cache()
    yield
    ts.invalidate_cache()


# ── precedência ──────────────────────────────────────────────────────────────


def test_sem_configuracao_nenhuma_herda_do_servidor() -> None:
    r = ts.resolve({}, {})
    assert r.timezone == SP
    assert r.offset_minutes == -180
    assert r.origem_tz == "servidor"
    assert r.herdado is True
    assert r.ntp_server == ts.FALLBACK_NTP


def test_configuracao_da_instalacao_vence_a_deteccao() -> None:
    r = ts.resolve({}, {"phone_timezone_mode": "proprio", "phone_timezone": MANAUS})
    assert r.timezone == MANAUS
    assert r.offset_minutes == -240
    assert r.origem_tz == "global"
    assert r.herdado is False


def test_ambiente_vence_a_instalacao() -> None:
    r = ts.resolve(
        {"timezone_mode": "proprio", "timezone": MANAUS},
        {"phone_timezone_mode": "proprio", "phone_timezone": SP},
    )
    assert r.timezone == MANAUS
    assert r.origem_tz == "ambiente"


def test_ambiente_antigo_com_fuso_gravado_ainda_herda() -> None:
    """A razão de existir `timezone_mode`.

    Todo ambiente criado antes desta feature tem `timezone` gravado no blob
    (``create_environment`` serializa os defaults inteiros). Se a herança fosse
    "campo vazio = herda", uma filial em Manaus ficaria presa em São Paulo para
    sempre — o valor gravado venceria a detecção sem ninguém entender por quê.
    """
    ambiente = {"timezone_mode": "herdar", "timezone": SP, "ntp_mode": "herdar"}
    r = ts.resolve(ambiente, {"phone_timezone_mode": "proprio", "phone_timezone": MANAUS})
    assert r.timezone == MANAUS  # o gravado é ignorado enquanto o modo é "herdar"
    assert r.origem_tz == "global"


# ── robustez ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ruim", ["", "   ", "Nao/Existe", "Hora oficial do Brasil", "-3"])
def test_fuso_invalido_cai_para_o_proximo_nivel(ruim: str) -> None:
    # Inclui o nome localizado que o Windows devolve por `astimezone()`, que
    # não é chave IANA e não pode ser aceito como se fosse.
    r = ts.resolve({"timezone_mode": "proprio", "timezone": ruim}, {})
    assert r.timezone == SP
    assert r.origem_tz == "servidor"


def test_servidor_sem_nome_iana_ainda_entrega_o_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Máquina com fuso customizado: o nome some, o offset continua valendo.

    Importa porque é o offset que o Yealink e o Intelbras V-series consomem —
    esses continuam com a hora certa mesmo sem nome de fuso.
    """
    monkeypatch.setattr(
        core_tz, "detect_server_timezone",
        lambda: core_tz.ServerTimezone(name=None, offset_minutes=-240, source="offset_only"),
    )
    r = ts.resolve({}, {})
    assert r.offset_minutes == -240
    assert r.origem_tz == "fallback"
    assert r.timezone == core_tz.FALLBACK_TZ


def test_banco_fora_do_ar_nao_impede_de_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    # `global_settings` é chamado de dentro da geração de config; banco indisponível
    # não pode impedir de renderizar a planilha.
    def explode() -> None:
        raise RuntimeError("banco fora")

    monkeypatch.setattr("middleware_monitor.core.db.session_factory", explode)
    ts.invalidate_cache()
    r = ts.resolve({})
    assert r.origem_tz == "servidor"


# ── NTP ──────────────────────────────────────────────────────────────────────


def test_ntp_segue_a_mesma_precedencia() -> None:
    assert ts.resolve({}, {}).ntp_server == "a.ntp.br"
    assert ts.resolve({}, {"phone_ntp_server": "ntp.local"}).ntp_server == "ntp.local"
    r = ts.resolve(
        {"ntp_mode": "proprio", "ntp_server": "ntp.filial"},
        {"phone_ntp_server": "ntp.local"},
    )
    assert r.ntp_server == "ntp.filial"
    assert r.origem_ntp == "ambiente"


# ── idempotência (a garantia que evita "desatualizado" em massa) ─────────────


def test_resolucao_e_estavel_entre_chamadas() -> None:
    # `compute_line_hash` chama isto uma vez por ramal; oscilar entre chamadas
    # faria a mesma planilha ora bater, ora não.
    primeira = ts.resolve({}, {})
    for _ in range(50):
        assert ts.resolve({}, {}) == primeira


# Modelos cujo payload mudou de propósito nesta entrega, ao ganhar o
# provisionamento de hora que não tinham. Ver o teste logo abaixo.
MODELOS_QUE_GANHARAM_HORA = (
    "Intelbras V3001", "Intelbras V3101", "Intelbras V3501", "Intelbras V5501",
)


def test_payload_de_ambiente_legado_e_identico_byte_a_byte() -> None:
    """A garantia central desta entrega, para **todos** os modelos suportados.

    Ambiente que já existia, com o fuso default (São Paulo), em servidor
    brasileiro: o payload gerado tem de sair byte a byte igual ao que a v2.8.0
    gerava. Se mudasse, toda a planilha viraria "desatualizado" e pediria
    reaplicação — com reboot em vários modelos — sem nenhum ganho.

    O "antes" é reconstruído aqui do jeito que a v2.8.0 montava o template: hora
    crua do `config_padrao`, sem o offset. Comparar os bytes (e não só um campo)
    é o que pega um adapter que passe a emitir qualquer coisa nova sem querer.
    """
    from middleware_monitor.domain.extension_configurator.defaults import (
        PHONE_MODELS,
        default_config_padrao,
    )
    from middleware_monitor.domain.extension_configurator.service import (
        adapter_for,
        build_row,
        build_template,
    )

    class _Linha:
        numero_ramal = "1001"
        senha_sip = "s3nh4"
        servidor_sip = "10.0.0.1"
        nome_visivel = "Recepcao"
        user_auth = "1001"
        numero_abreviado = "800"

    cfg = default_config_padrao()
    row = build_row(_Linha(), cfg)  # type: ignore[arg-type]

    def template_v280(base: dict) -> dict:
        antigo = build_template(base)
        antigo["ntp_server"] = base.get("ntp_server", "a.ntp.br")
        antigo["timezone"] = base.get("timezone", "America/Sao_Paulo")
        antigo.pop("timezone_offset_minutes", None)
        return antigo

    divergentes = []
    for modelo in PHONE_MODELS:
        if modelo in MODELOS_QUE_GANHARAM_HORA:
            continue
        adapter = adapter_for(modelo)
        antes = adapter.generate_config(template_v280(cfg), row)
        depois = adapter.generate_config(build_template(cfg), row)
        if antes != depois:
            divergentes.append(modelo)

    assert not divergentes, f"payload mudou em: {divergentes}"


def test_intelbras_v_series_muda_de_proposito_ao_ganhar_a_hora() -> None:
    """A exceção da garantia acima, e ela é intencional.

    O V-series não provisionava hora nenhuma; passou a emitir `<date>` com NTP e
    fuso. O payload **tem** de mudar — é o que faz o telefone receber a hora — e
    o efeito colateral é conhecido: toda linha Intelbras V aparece uma vez como
    desatualizada e é reaplicada. Este teste existe para que essa mudança seja
    uma decisão registrada, e não uma surpresa no cliente.
    """
    from middleware_monitor.domain.extension_configurator.defaults import (
        default_config_padrao,
    )
    from middleware_monitor.domain.extension_configurator.service import (
        adapter_for,
        build_row,
        build_template,
    )

    class _Linha:
        numero_ramal = "1001"
        senha_sip = "s3nh4"
        servidor_sip = "10.0.0.1"
        nome_visivel = "Recepcao"
        user_auth = "1001"
        numero_abreviado = "800"

    cfg = default_config_padrao()
    row = build_row(_Linha(), cfg)  # type: ignore[arg-type]
    for modelo in MODELOS_QUE_GANHARAM_HORA:
        xml = adapter_for(modelo).generate_config(build_template(cfg), row).decode("utf-8")
        assert "<date>" in xml, modelo
        assert "<SNTPServer>a.ntp.br</SNTPServer>" in xml, modelo
        assert "<TimeZoneName>UTC-3</TimeZoneName>" in xml, modelo
