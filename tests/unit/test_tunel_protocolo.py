"""Protocolo v2 do túnel (ADR 0009): as regras puras."""

from __future__ import annotations

import pytest

from middleware_monitor.domain.noc import tunel_protocolo as p

TEL = ("http", "192.168.0.20", 80)


def test_quadro_binario_vai_e_volta() -> None:
    q = p.empacotar(p.RESP_CORPO, 70000, b"\x00dados")
    assert len(q) == 5 + 6  # 1 byte de tipo + 4 do fluxo, sem base64
    assert p.desempacotar(q) == (p.RESP_CORPO, 70000, b"\x00dados")
    assert p.desempacotar(b"\x09\x00\x00\x00\x01x") is None  # tipo fora do contrato
    assert p.desempacotar(b"\x01\x00") is None


@pytest.mark.parametrize(
    ("valor", "kbps"), [("2048", 2048), ("0", 0), (None, 0), ("x", 0), ("-5", 0), (" 512 ", 512)]
)
def test_banda_anunciada(valor: str | None, kbps: int) -> None:
    assert p.banda_do_cabecalho(valor) == kbps


def test_balde_deixa_a_rajada_e_segura_o_resto() -> None:
    agora = [0.0]
    balde = p.Balde(2048, relogio=lambda: agora[0])  # 2 Mbit/s = 256 KB/s
    assert balde.espera_para(64 * 1024) == 0  # cabe na rajada
    espera = balde.espera_para(256 * 1024)  # 1 s de banda além do que sobrou
    assert espera == pytest.approx((256 * 1024 - (balde.capacidade - 64 * 1024)) / balde.taxa, rel=0.01)
    agora[0] += 10  # depois de parado, volta só até a capacidade (não acumula para sempre)
    assert balde.espera_para(int(balde.capacidade)) == 0
    assert p.Balde(0).espera_para(10**9) == 0  # 0 = sem limite


@pytest.mark.parametrize(
    ("host", "interno"),
    [
        ("192.168.0.20", True),
        ("10.1.2.3", True),
        ("127.0.0.1", True),
        ("169.254.1.1", True),
        ("100.64.0.9", True),
        ("pabx", True),
        ("nas.local", True),
        ("srv.corp", True),
        ("8.8.8.8", False),
        ("cdn.jsdelivr.net", False),
        ("uscall.exemplo.com.br", False),
    ],
)
def test_o_que_e_de_dentro(host: str, interno: bool) -> None:
    assert p.host_interno(host) is interno


def test_mesma_origem_vira_caminho_e_outra_de_dentro_vai_pelo_noc() -> None:
    assert p.para_o_tunel("http://192.168.0.20/cgi-bin/x?a=1#y", *TEL) == "/cgi-bin/x?a=1#y"
    assert p.para_o_tunel("//192.168.0.20:80/a", *TEL) == "/a"
    # Outra porta ou outro esquema do mesmo aparelho é outra origem (a v1 colapsava para caminho).
    assert p.para_o_tunel("https://192.168.0.20/", *TEL) == "/__tunel/ir?u=https%3A%2F%2F192.168.0.20%2F"
    assert (
        p.para_o_tunel("http://192.168.0.20:8080/x", *TEL)
        == "/__tunel/ir?u=http%3A%2F%2F192.168.0.20%3A8080%2Fx"
    )
    assert p.para_o_tunel("http://10.0.0.9/", *TEL) == "/__tunel/ir?u=http%3A%2F%2F10.0.0.9%2F"
    # Público fica direto; o que não é URL também.
    assert p.para_o_tunel("https://cdn.jsdelivr.net/npm/x.js", *TEL) is None
    assert p.para_o_tunel("ftp://192.168.0.20/", *TEL) is None


def test_reescrita_no_corpo() -> None:
    corpo = (
        b'<a href="http://192.168.0.20/menu.htm">m</a> <img src="http://192.168.0.30/logo.gif">'
        b' <script src="https://cdn.exemplo.com/x.js"></script>'
        b' {"url":"http:\\/\\/192.168.0.20:8443\\/api"} <!-- http://www.w3.org/1999/xhtml -->'
    )
    novo = p.reescrever_links_v2(corpo, *TEL)
    assert b'href="/menu.htm"' in novo
    assert b'src="/__tunel/ir?u=http%3A%2F%2F192.168.0.30%2Flogo.gif"' in novo
    assert b'src="https://cdn.exemplo.com/x.js"' in novo
    assert b'"url":"\\/__tunel\\/ir?u=http%3A%2F%2F192.168.0.20%3A8443%2Fapi"' in novo
    assert b"http://www.w3.org/1999/xhtml" in novo


def test_location() -> None:
    assert p.location_v2("http://192.168.0.20/login", *TEL) == "/login"
    assert p.location_v2("https://192.168.0.20/", *TEL) == "/__tunel/ir?u=https%3A%2F%2F192.168.0.20%2F"
    assert p.location_v2("/ja/relativo", *TEL) == "/ja/relativo"
    assert p.location_v2("https://www.google.com/", *TEL) == "https://www.google.com/"


def test_css_e_js_reescritos_voltam_comprimidos_e_o_html_cru() -> None:
    """Medido em 26/09: descomprimir para reescrever e mandar cru passava 3x mais bytes pelo link."""
    import gzip

    from middleware_monitor.domain.noc.tunel import comprimir_para_o_noc

    js = b"var a = function(){ return 'x'; };\n" * 200
    corpo, cab = comprimir_para_o_noc(js, [["Content-Type", "application/javascript"]])
    assert ["Content-Encoding", "gzip"] in cab and gzip.decompress(corpo) == js and len(corpo) < len(js) / 3
    html = b"<html><head></head><body>" + b"x" * 5000 + b"</body></html>"
    assert comprimir_para_o_noc(html, [["Content-Type", "text/html; charset=utf-8"]]) == (
        html,
        [["Content-Type", "text/html; charset=utf-8"]],
    )
    assert comprimir_para_o_noc(b"pequeno", [["Content-Type", "text/css"]])[1] == [
        ["Content-Type", "text/css"]
    ]
