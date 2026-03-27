import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx

from src.utils.json_logger import get_logger


@dataclass
class SyntheticCheckResult:
    monitor_name: str
    target_url: str
    target_host: str
    is_healthy: bool
    response_time_ms: float
    status_code: Optional[int] = None
    reason: str = ""
    checked_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> dict:
        return {
            "monitor_name": self.monitor_name,
            "target_url": self.target_url,
            "target_host": self.target_host,
            "is_healthy": self.is_healthy,
            "response_time_ms": self.response_time_ms,
            "status_code": self.status_code,
            "reason": self.reason,
            "checked_at": self.checked_at,
        }


class SyntheticSiteHealthChecker:
    def __init__(
        self,
        monitor_name: str,
        target_url: str,
        timeout_seconds: float,
    ) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self.monitor_name = monitor_name
        self.target_url = target_url
        self.timeout_seconds = timeout_seconds
        self.target_host = urlparse(target_url).netloc or target_url

    async def check(self) -> SyntheticCheckResult:
        started_at = time.perf_counter()

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
            ) as http:
                response = await http.get(
                    self.target_url,
                    headers={"User-Agent": "titlis-operator-synthetic-monitor/1.0"},
                )

            response_time_ms = round((time.perf_counter() - started_at) * 1000, 2)
            is_healthy = 200 <= response.status_code < 400
            reason = f"HTTP {response.status_code}"

            self.logger.info(
                "Monitoramento sintético concluído",
                extra={
                    "monitor_name": self.monitor_name,
                    "target_url": self.target_url,
                    "status_code": response.status_code,
                    "is_healthy": is_healthy,
                    "response_time_ms": response_time_ms,
                },
            )

            return SyntheticCheckResult(
                monitor_name=self.monitor_name,
                target_url=self.target_url,
                target_host=self.target_host,
                is_healthy=is_healthy,
                response_time_ms=response_time_ms,
                status_code=response.status_code,
                reason=reason,
            )

        except httpx.TimeoutException:
            response_time_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self.logger.exception(
                "Timeout no monitoramento sintético",
                extra={
                    "monitor_name": self.monitor_name,
                    "target_url": self.target_url,
                    "timeout_seconds": self.timeout_seconds,
                },
            )
            return SyntheticCheckResult(
                monitor_name=self.monitor_name,
                target_url=self.target_url,
                target_host=self.target_host,
                is_healthy=False,
                response_time_ms=response_time_ms,
                reason="timeout",
            )
        except httpx.RequestError as exc:
            response_time_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self.logger.exception(
                "Erro de rede no monitoramento sintético",
                extra={
                    "monitor_name": self.monitor_name,
                    "target_url": self.target_url,
                    "error": str(exc),
                },
            )
            return SyntheticCheckResult(
                monitor_name=self.monitor_name,
                target_url=self.target_url,
                target_host=self.target_host,
                is_healthy=False,
                response_time_ms=response_time_ms,
                reason=str(exc),
            )
