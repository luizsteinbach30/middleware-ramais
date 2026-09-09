"""A unidade systemd entregue pelo `.run` deixa o serviço pingar.

Caso de campo 2026-09-09: `NoNewPrivileges=yes` ignora a capability de arquivo
do `/usr/bin/ping`, e o host tinha `net.ipv4.ping_group_range = 1 0` — todo
aparelho aparecia offline com `socket: Operation not permitted`. A capability
ambiente atravessa o execve mesmo sob NoNewPrivileges e não depende do sysctl.
Estes testes prendem a unidade a esse acordo, para o endurecimento não voltar a
tirar o ping sem ninguém notar.
"""

from __future__ import annotations

from pathlib import Path

UNIT = Path(__file__).resolve().parents[2] / "packaging" / "linux" / "middleware-monitor.service"


def _service_section() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    secao = ""
    for bruta in UNIT.read_text(encoding="utf-8").splitlines():
        linha = bruta.strip()
        if not linha or linha.startswith("#"):
            continue
        if linha.startswith("["):
            secao = linha
            continue
        if secao == "[Service]" and "=" in linha:
            k, v = linha.split("=", 1)
            out.setdefault(k.strip(), []).append(v.strip())
    return out


def test_servico_roda_sem_privilegios_novos_e_com_cap_net_raw_ambiente() -> None:
    svc = _service_section()
    assert svc.get("NoNewPrivileges") == ["yes"]
    assert svc.get("User") == ["mmonitor"]
    # sem isto o ping falha em "socket: Operation not permitted" sempre que o
    # host não libera ICMP sem privilégio (ping_group_range)
    assert svc.get("AmbientCapabilities") == ["CAP_NET_RAW"]


def test_bounding_set_nao_tira_a_capability_que_o_ping_precisa() -> None:
    """`CapabilityBoundingSet` é a lista fechada do que o serviço pode ter; se
    alguém restringir e esquecer o CAP_NET_RAW, a ambiente deixa de valer."""
    svc = _service_section()
    bounding = " ".join(svc.get("CapabilityBoundingSet", []))
    assert "CAP_NET_RAW" in bounding
    assert not bounding.startswith("~"), "lista negativa: CAP_NET_RAW sairia junto com o resto"


def test_unidade_continua_endurecida() -> None:
    svc = _service_section()
    assert svc.get("ProtectSystem") == ["strict"]
    assert svc.get("PrivateTmp") == ["yes"]
    assert svc.get("ReadWritePaths") == ["/var/lib/middleware-monitor"]
