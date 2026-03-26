import time
from typing import Optional

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v2.api.metrics_api import MetricsApi
from datadog_api_client.v2.model.metric_intake_type import MetricIntakeType
from datadog_api_client.v2.model.metric_payload import MetricPayload
from datadog_api_client.v2.model.metric_point import MetricPoint
from datadog_api_client.v2.model.metric_resource import MetricResource
from datadog_api_client.v2.model.metric_series import MetricSeries

from src.settings import settings
from src.utils.json_logger import get_logger


class SyntheticSiteMetricsManager:
    HEALTH_METRIC_NAME = "synthetic.site.health"
    LATENCY_METRIC_NAME = "synthetic.site.response_time_ms"

    def __init__(
        self,
        api_key: Optional[str] = None,
        app_key: Optional[str] = None,
        site: Optional[str] = None,
    ) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self.api_key = api_key or settings.datadog_api_key
        self.app_key = app_key or settings.datadog_app_key
        self.site = site or settings.datadog_site

        if not self.api_key:
            raise ValueError(
                "DD_API_KEY é obrigatória para SyntheticSiteMetricsManager"
            )

        self.configuration = self._build_configuration()

    def send_check_result(self, result: dict) -> bool:
        checked_at = result.get("checked_at") or int(time.time())
        status_code = result.get("status_code")
        tags = [
            f"monitor_name:{result['monitor_name']}",
            f"target_host:{result['target_host']}",
            f"status_code:{status_code if status_code is not None else 'unknown'}",
        ]
        series = [
            MetricSeries(
                metric=self.HEALTH_METRIC_NAME,
                type=MetricIntakeType.GAUGE,
                points=[
                    MetricPoint(
                        timestamp=checked_at,
                        value=1.0 if result["is_healthy"] else 0.0,
                    )
                ],
                tags=tags,
                resources=[
                    MetricResource(name=result["target_host"], type="host"),
                ],
            )
        ]

        response_time_ms = result.get("response_time_ms")
        if response_time_ms is not None:
            series.append(
                MetricSeries(
                    metric=self.LATENCY_METRIC_NAME,
                    type=MetricIntakeType.GAUGE,
                    points=[
                        MetricPoint(
                            timestamp=checked_at,
                            value=float(response_time_ms),
                        )
                    ],
                    tags=tags,
                    resources=[
                        MetricResource(name=result["target_host"], type="host"),
                    ],
                )
            )

        self.logger.info(
            "Enviando métricas sintéticas para Datadog",
            extra={
                "monitor_name": result["monitor_name"],
                "target_host": result["target_host"],
                "is_healthy": result["is_healthy"],
                "status_code": status_code,
                "response_time_ms": response_time_ms,
            },
        )

        try:
            with ApiClient(self.configuration) as api_client:
                api = MetricsApi(api_client)
                response = api.submit_metrics(body=MetricPayload(series=series))

            self.logger.info(
                "Métricas sintéticas enviadas com sucesso",
                extra={
                    "monitor_name": result["monitor_name"],
                    "target_host": result["target_host"],
                    "response_status": getattr(response, "errors", []),
                },
            )
            return True
        except Exception:
            self.logger.exception(
                "Erro ao enviar métricas sintéticas para Datadog",
                extra={
                    "monitor_name": result["monitor_name"],
                    "target_host": result["target_host"],
                },
            )
            return False

    def _build_configuration(self) -> Configuration:
        config = Configuration()
        config.api_key["apiKeyAuth"] = self.api_key
        if self.app_key:
            config.api_key["appKeyAuth"] = self.app_key
        config.server_variables["site"] = self.site
        return config
