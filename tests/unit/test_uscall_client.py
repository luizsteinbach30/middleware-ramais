import pytest

from middleware_monitor.integrations.uscall_client import (
    UscallClient,
)


@pytest.mark.asyncio
async def test_uscall_invalid_args() -> None:
    with pytest.raises(ValueError):
        UscallClient(host="", token="x")
    with pytest.raises(ValueError):
        UscallClient(host="x", token="")


@pytest.mark.asyncio
async def test_uscall_strips_protocol() -> None:
    c = UscallClient(host="https://uscall.test/", token="abc")
    assert c.host == "uscall.test"
