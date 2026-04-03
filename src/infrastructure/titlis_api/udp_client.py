import asyncio
import json
import time
import logging

from src.application.ports.titlis_api_port import TitlisApiPort, RemediationState
from typing import Optional

logger = logging.getLogger(__name__)


class TitlisApiUdpClient(TitlisApiPort):
    def __init__(
        self,
        host: str,
        udp_port: int,
        http_base_url: str,
        default_tenant_id: Optional[int] = None,
    ):
        self._host = host
        self._udp_port = udp_port
        self._http_base_url = http_base_url
        self._default_tenant_id = default_tenant_id
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def _ensure_socket(self) -> None:
        transport_is_closing = False
        if self._transport is not None:
            is_closing = getattr(self._transport, "is_closing", None)
            if callable(is_closing):
                transport_is_closing = is_closing() is True

        if self._transport is None or transport_is_closing:
            self._loop = asyncio.get_event_loop()
            self._transport, _ = await self._loop.create_datagram_endpoint(
                asyncio.DatagramProtocol,
                remote_addr=(self._host, self._udp_port),
            )

    async def _send_udp(self, event_type: str, data: dict) -> None:
        tenant_id = data.get("tenant_id", self._default_tenant_id)
        if tenant_id is not None:
            data = {**data, "tenant_id": tenant_id}
        envelope = {
            "v": 1,
            "t": event_type,
            "ts": int(time.time() * 1000),
            "tenant_id": tenant_id,
            "data": data,
        }
        try:
            await self._ensure_socket()
            payload = json.dumps(envelope, default=str).encode("utf-8")
            transport = self._transport
            if transport is None:
                raise RuntimeError("UDP transport indisponivel")
            transport.sendto(payload)
        except Exception as exc:
            logger.warning(
                "titlis_api_udp_send_failed",
                extra={"event": event_type, "error": str(exc)},
            )

    async def send_scorecard_evaluated(self, payload: dict) -> None:
        await self._send_udp("scorecard_evaluated", payload)

    async def send_remediation_event(self, payload: dict) -> None:
        await self._send_udp("remediation_updated", payload)

    async def send_slo_reconciled(self, payload: dict) -> None:
        await self._send_udp("slo_reconciled", payload)

    async def send_notification_log(self, payload: dict) -> None:
        await self._send_udp("notification_sent", payload)

    async def send_resource_metrics(self, payload: dict) -> None:
        await self._send_udp("resource_metrics", payload)

    async def get_remediation(self, workload_id: str) -> Optional[RemediationState]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self._http_base_url}/v1/workloads/{workload_id}/remediation"
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                data = resp.json()
                return RemediationState(
                    status=data["status"],
                    version=data["version"],
                    github_pr_url=data.get("github_pr_url"),
                    github_pr_number=data.get("github_pr_number"),
                )
        except Exception as exc:
            logger.warning(
                "titlis_api_http_get_failed",
                extra={"workload_id": workload_id, "error": str(exc)},
            )
            return None

    async def close(self) -> None:
        if self._transport and not self._transport.is_closing():
            self._transport.close()
