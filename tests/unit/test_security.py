from middleware_monitor.core.security import hash_password, verify_password


def test_password_round_trip() -> None:
    h = hash_password("hunter22-good-pass")
    assert verify_password("hunter22-good-pass", h) is True
    assert verify_password("wrong-password", h) is False


def test_invalid_hash_returns_false() -> None:
    assert verify_password("anything", "not-a-real-hash") is False
