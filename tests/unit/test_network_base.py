from middleware_monitor.integrations.network.base import is_valid_ip, normalize_mac


def test_is_valid_ip() -> None:
    assert is_valid_ip("10.20.30.40")
    assert is_valid_ip("::1")
    assert not is_valid_ip("not.an.ip")
    assert not is_valid_ip("10.20.30.; rm -rf /")


def test_normalize_mac() -> None:
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aa:bb:cc:dd:ee:ff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("invalid") is None
    assert normalize_mac(None) is None
