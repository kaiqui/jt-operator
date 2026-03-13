import time
from typing import List, Optional

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v2.api.metrics_api import MetricsApi
from datadog_api_client.v2.model.metric_intake_type import MetricIntakeType
from datadog_api_client.v2.model.metric_payload import MetricPayload
from datadog_api_client.v2.model.metric_point import MetricPoint
from datadog_api_client.v2.model.metric_resource import MetricResource
from datadog_api_client.v2.model.metric_series import MetricSeries

from src.utils.json_logger import get_logger
from src.settings import settings


class CastAIMetricsManager:
    METRIC_NAME = "castai.pod.health"

    def __init__(
        self,
        api_key: Optional[str] = None,
        app_key: Optional[str] = None,
        site: Optional[str] = None,
    ):
        self.logger = get_logger(self.__class__.__name__)
        self.api_key = api_key or settings.datadog_api_key
        self.app_key = app_key or settings.datadog_app_key
        self.site = site or settings.datadog_site

        if not self.api_key:
            raise ValueError("DD_API_KEY é obrigatória para CastAIMetricsManager")

        self.configuration = self._build_configuration()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def send_pod_health(
        self,
        service: str,
        namespace: str,
        cluster_name: str,
        is_healthy: bool,
    ) -> bool:
        value = 1.0 if is_healthy else 0.0
        tags = [
            f"cluster_name:{cluster_name}",
            f"service:{service}",
            f"namespace:{namespace}",
        ]

        self.logger.info(
            "Enviando métrica CAST AI para Datadog",
            extra={
                "metric": self.METRIC_NAME,
                "value": value,
                "service": service,
                "cluster_name": cluster_name,
                "namespace": namespace,
            },
        )

        try:
            with ApiClient(self.configuration) as api_client:
                api = MetricsApi(api_client)
                payload = MetricPayload(
                    series=[
                        MetricSeries(
                            metric=self.METRIC_NAME,
                            type=MetricIntakeType.GAUGE,
                            points=[
                                MetricPoint(
                                    timestamp=int(time.time()),
                                    value=value,
                                )
                            ],
                            tags=tags,
                            resources=[
                                MetricResource(
                                    name=cluster_name,
                                    type="cluster",
                                )
                            ],
                        )
                    ]
                )
                response = api.submit_metrics(body=payload)

            self.logger.info(
                "Métrica CAST AI enviada com sucesso",
                extra={
                    "metric": self.METRIC_NAME,
                    "service": service,
                    "cluster_name": cluster_name,
                    "response_status": getattr(response, "errors", []),
                },
            )
            return True

        except Exception:
            self.logger.exception(
                "Erro ao enviar métrica CAST AI para Datadog",
                extra={
                    "metric": self.METRIC_NAME,
                    "service": service,
                    "cluster_name": cluster_name,
                },
            )
            return False

    def send_all(self, results: List[dict]) -> None:
        for result in results:
            self.send_pod_health(
                service=result["service"],
                namespace=result["namespace"],
                cluster_name=result["cluster_name"],
                is_healthy=result["is_healthy"],
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_configuration(self) -> Configuration:
        config = Configuration()
        config.api_key["apiKeyAuth"] = self.api_key
        if self.app_key:
            config.api_key["appKeyAuth"] = self.app_key
        config.server_variables["site"] = self.site
        return config
