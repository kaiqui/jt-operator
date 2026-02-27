"""
Manager para consulta de métricas de recursos de containers no Datadog.

Usado pelo RemediationService para obter CPU e memória médias de um Deployment
e embasar os valores sugeridos de requests/limits.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.domain.github_models import DatadogProfilingMetrics
from src.infrastructure.datadog.managers.base import DatadogManagerBase
from src.utils.json_logger import get_logger

logger = get_logger(__name__)

# Nanocores → millicores: dividir por 1_000_000
_NANOCORES_TO_MILLICORES = 1_000_000
# Bytes → MiB: dividir por 1_048_576
_BYTES_TO_MIB = 1_048_576


class DatadogMetricsManager(DatadogManagerBase):
    """
    Consulta métricas de recursos (CPU / memória) de Deployments via Datadog Metrics API v1.
    """

    def get_container_metrics(
        self,
        deployment_name: str,
        namespace: str,
        lookback_hours: int = 1,
    ) -> Optional[DatadogProfilingMetrics]:
        """
        Retorna a média de CPU (millicores) e memória (MiB) do Deployment
        no intervalo de tempo especificado.

        Retorna None se não houver dados ou se a API estiver indisponível.
        """
        from datadog_api_client.v1.api.metrics_api import MetricsApi

        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=lookback_hours)
        from_ts = int(start.timestamp())
        to_ts = int(now.timestamp())

        tags = (
            f"kube_deployment:{deployment_name},"
            f"kube_namespace:{namespace}"
        )
        cpu_query = f"avg:kubernetes.cpu.usage.total{{{tags}}}"
        mem_query = f"avg:kubernetes.memory.usage{{{tags}}}"

        api = MetricsApi(self.api_client)
        cpu_avg: Optional[int] = None
        mem_avg: Optional[int] = None

        try:
            cpu_resp = self.execute(
                api.query_metrics, _from=from_ts, to=to_ts, query=cpu_query
            )
            values = [
                point[1]
                for series in (cpu_resp.series or [])
                for point in (series.pointlist or [])
                if point[1] is not None
            ]
            if values:
                cpu_avg = max(1, int(sum(values) / len(values) / _NANOCORES_TO_MILLICORES))
                logger.info(
                    "Métrica CPU coletada",
                    extra={
                        "deployment": deployment_name,
                        "namespace": namespace,
                        "cpu_avg_millicores": cpu_avg,
                    },
                )
        except Exception:
            logger.warning(
                "Falha ao buscar métricas de CPU do Datadog",
                extra={"deployment": deployment_name},
            )

        try:
            mem_resp = self.execute(
                api.query_metrics, _from=from_ts, to=to_ts, query=mem_query
            )
            values = [
                point[1]
                for series in (mem_resp.series or [])
                for point in (series.pointlist or [])
                if point[1] is not None
            ]
            if values:
                mem_avg = max(1, int(sum(values) / len(values) / _BYTES_TO_MIB))
                logger.info(
                    "Métrica memória coletada",
                    extra={
                        "deployment": deployment_name,
                        "namespace": namespace,
                        "memory_avg_mib": mem_avg,
                    },
                )
        except Exception:
            logger.warning(
                "Falha ao buscar métricas de memória do Datadog",
                extra={"deployment": deployment_name},
            )

        if cpu_avg is None and mem_avg is None:
            return None

        return DatadogProfilingMetrics(
            cpu_avg_millicores=cpu_avg,
            memory_avg_mib=mem_avg,
        )
