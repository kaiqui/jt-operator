import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.infrastructure.titlis_api.udp_client import TitlisApiUdpClient


@pytest.mark.asyncio
async def test_send_scorecard_evaluated_sends_udp():
    client = TitlisApiUdpClient(
        host="localhost",
        udp_port=8125,
        http_base_url="http://localhost:8080",
    )
    mock_transport = MagicMock()
    client._transport = mock_transport

    await client.send_scorecard_evaluated({"namespace": "prod", "workload": "api"})

    mock_transport.sendto.assert_called_once()
    raw = mock_transport.sendto.call_args[0][0]
    envelope = json.loads(raw.decode())
    assert envelope["v"] == 1
    assert envelope["t"] == "scorecard_evaluated"
    assert "ts" in envelope


@pytest.mark.asyncio
async def test_get_remediation_returns_none_on_404():
    client = TitlisApiUdpClient("localhost", 8125, "http://localhost:8080")
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = AsyncMock()
        mock_resp.status_code = 404
        mock_get.return_value.__aenter__.return_value.get.return_value = mock_resp
        result = await client.get_remediation("some-uuid")
        assert result is None
