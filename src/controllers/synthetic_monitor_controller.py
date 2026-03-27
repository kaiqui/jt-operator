import asyncio
from typing import Any

import kopf

from src.infrastructure.datadog.managers.synthetic_metrics import (
    SyntheticSiteMetricsManager,
)
from src.infrastructure.synthetic.site_health import SyntheticSiteHealthChecker
from src.settings import settings
from src.utils.json_logger import get_logger

logger = get_logger("SyntheticMonitorController")


@kopf.on.startup()
async def synthetic_monitor_startup(**kwargs: Any) -> None:
    if not settings.enable_synthetic_monitor:
        logger.info(
            "Monitor sintético desabilitado — startup ignorado",
            extra={"feature": "synthetic_monitor"},
        )
        return

    asyncio.create_task(_monitor_loop(), name="synthetic-monitor-loop")

    logger.info(
        "Monitor sintético iniciado",
        extra={
            "monitor_name": settings.synthetic_monitor_name,
            "target_url": settings.synthetic_monitor_url,
            "interval_seconds": settings.synthetic_monitor_interval_seconds,
            "timeout_seconds": settings.synthetic_monitor_timeout_seconds,
        },
    )


async def _monitor_loop() -> None:
    await asyncio.sleep(10)

    while True:
        try:
            await run_synthetic_site_check()
        except asyncio.CancelledError:
            logger.info("Monitor sintético cancelado")
            raise
        except Exception:
            logger.exception(
                "Erro não tratado no loop do monitor sintético — continuando",
                extra={"feature": "synthetic_monitor"},
            )

        await asyncio.sleep(settings.synthetic_monitor_interval_seconds)


async def run_synthetic_site_check() -> None:
    monitor_name = settings.synthetic_monitor_name
    target_url = settings.synthetic_monitor_url
    timeout_seconds = settings.synthetic_monitor_timeout_seconds

    if not target_url:
        logger.error(
            "SYNTHETIC_MONITOR_URL não configurada — métrica não será enviada",
            extra={"feature": "synthetic_monitor"},
        )
        return

    logger.info(
        "Iniciando verificação sintética HTTP",
        extra={
            "monitor_name": monitor_name,
            "target_url": target_url,
            "timeout_seconds": timeout_seconds,
        },
    )

    checker = SyntheticSiteHealthChecker(
        monitor_name=monitor_name,
        target_url=target_url,
        timeout_seconds=timeout_seconds,
    )
    result = await checker.check()

    if not settings.datadog_api_key:
        logger.error(
            "DD_API_KEY não configurada — métricas sintéticas não serão enviadas",
            extra={
                "monitor_name": monitor_name,
                "target_url": target_url,
            },
        )
        return

    try:
        metrics_manager = SyntheticSiteMetricsManager(
            api_key=settings.datadog_api_key,
            app_key=settings.datadog_app_key,
            site=settings.datadog_site,
        )
        metrics_manager.send_check_result(result.to_dict())
    except Exception:
        logger.exception(
            "Falha ao enviar métricas sintéticas para Datadog",
            extra={
                "monitor_name": monitor_name,
                "target_url": target_url,
            },
        )
        return

    logger.info(
        "Ciclo de monitoramento sintético concluído",
        extra={
            "monitor_name": monitor_name,
            "target_url": target_url,
            "is_healthy": result.is_healthy,
            "status_code": result.status_code,
            "response_time_ms": result.response_time_ms,
            "reason": result.reason,
        },
    )


def register_synthetic_monitor() -> bool:
    if not settings.enable_synthetic_monitor:
        logger.info(
            "Monitor sintético desabilitado (ENABLE_SYNTHETIC_MONITOR=false)",
            extra={"feature": "synthetic_monitor"},
        )
        return False

    if not settings.synthetic_monitor_url:
        logger.warning(
            "SYNTHETIC_MONITOR_URL não definida — as métricas não serão enviadas até configurar.",
            extra={"feature": "synthetic_monitor"},
        )

    if not settings.datadog_api_key:
        logger.warning(
            "DD_API_KEY não definida — as métricas sintéticas não serão enviadas.",
            extra={"feature": "synthetic_monitor"},
        )

    logger.info(
        "Monitor sintético habilitado",
        extra={
            "feature": "synthetic_monitor",
            "monitor_name": settings.synthetic_monitor_name,
            "target_url": settings.synthetic_monitor_url,
            "interval_seconds": settings.synthetic_monitor_interval_seconds,
            "timeout_seconds": settings.synthetic_monitor_timeout_seconds,
        },
    )
    return True
