import pytest

from middleware_monitor.core.crypto import SecretBox


def test_round_trip() -> None:
    box = SecretBox("a-very-strong-secret-key-of-significant-length")
    enc = box.encrypt("hello-world")
    assert enc != "hello-world"
    assert box.decrypt(enc) == "hello-world"


def test_short_key_rejected() -> None:
    with pytest.raises(ValueError):
        SecretBox("too-short")


def test_tampered_token_rejected() -> None:
    box = SecretBox("a-very-strong-secret-key-of-significant-length")
    enc = box.encrypt("data")
    bad = enc[:-2] + "xx"
    with pytest.raises(ValueError):
        box.decrypt(bad)
