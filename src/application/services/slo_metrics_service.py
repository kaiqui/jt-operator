from __future__ import annotations

import os
import time
from enum import Enum
from typing import Optional

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v2.api.metrics_api import MetricsApi
from datadog_api_client.v2.model.metric_intake_type import MetricIntakeType
from datadog_api_client.v2.model.metric_payload import MetricPayload
from datadog_api_client.v2.model.metric_point import MetricPoint
from datadog_api_client.v2.model.metric_series import MetricSeries

from src.utils.json_logger import get_logger

# ---------------------------------------------------------------------------
# Enumerações de suporte — mantêm os valores de tag previsíveis e finitos
# ---------------------------------------------------------------------------


class SLOAction(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    NOOP = "noop"
    UNKNOWN = "unknown"


class SLOErrorKind(str, Enum):
    VALIDATION = "validation"
    DATADOG_API = "datadog_api"
    UNEXPECTED = "unexpected"
    NONE = "none"


# ---------------------------------------------------------------------------
# Nomes canônicos das métricas
# ---------------------------------------------------------------------------

_METRIC_RECONCILIATION_TOTAL = "titlis.slo.reconciliation.total"
_METRIC_RECONCILIATION_SUCCESS = "titlis.slo.reconciliation.success"
_METRIC_RECONCILIATION_ERROR = "titlis.slo.reconciliation.error"
_METRIC_COMPLIANCE_STATUS = "titlis.slo.compliance.status"


class SLOMetricsService:
    # Conjunto fechado de namespaces permitidos como tag.
    # Se o namespace não estiver na lista, usa "other" para evitar cardinalidade
    # irrestrita caso novos namespaces apareçam dinamicamente.
    _ALLOWED_NAMESPACE_TAGS: frozenset[str] = frozenset(
        {
            "default",
            "production",
            "staging",
            "develop",
            "titlis-system",
            "kube-system",
        }
    )

    def __init__(
        self,
        api_key: str,
        env: str,
        site: str = "datadoghq.com",
        app_key: Optional[str] = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key é obrigatório para SLOMetricsService")
        if not env:
            raise ValueError("env é obrigatório para SLOMetricsService")

        self.logger = get_logger(self.__class__.__name__)
        self._env = self._sanitize_env(env)

        configuration = Configuration()
        configuration.api_key["apiKeyAuth"] = api_key
        if app_key:
            configuration.api_key["appKeyAuth"] = app_key
        configuration.server_variables["site"] = site

        self._api_client = ApiClient(configuration)
        self._metrics_api = MetricsApi(self._api_client)

        self.logger.info(
            "SLOMetricsService inicializado",
            extra={"env": self._env, "site": site},
        )

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def record_reconciliation(
        self,
        *,
        success: bool,
        action: SLOAction = SLOAction.UNKNOWN,
        slo_type: str = "unknown",
        namespace: str = "unknown",
        error_kind: SLOErrorKind = SLOErrorKind.NONE,
    ) -> None:
        tags = self._build_tags(
            action=action.value,
            slo_type=self._sanitize_slo_type(slo_type),
            namespace=self._sanitize_namespace(namespace),
            error_kind=error_kind.value,
        )

        now = int(time.time())

        series = [
            self._make_count(_METRIC_RECONCILIATION_TOTAL, now, 1, tags),
        ]

        if success:
            series.append(
                self._make_count(_METRIC_RECONCILIATION_SUCCESS, now, 1, tags)
            )
        else:
            series.append(self._make_count(_METRIC_RECONCILIATION_ERROR, now, 1, tags))

        self._submit(series)

    def record_compliance_status(
        self,
        *,
        is_compliant: bool,
        slo_type: str = "unknown",
        namespace: str = "unknown",
    ) -> None:
        tags = self._build_tags(
            slo_type=self._sanitize_slo_type(slo_type),
            namespace=self._sanitize_namespace(namespace),
        )

        now = int(time.time())
        value = 1.0 if is_compliant else 0.0

        series = [self._make_gauge(_METRIC_COMPLIANCE_STATUS, now, value, tags)]
        self._submit(series)

    # ------------------------------------------------------------------
    # Helpers de construção de métricas
    # ------------------------------------------------------------------

    def _build_tags(self, **kwargs: str) -> list[str]:
        tags = [f"env:{self._env}"]
        for key, value in kwargs.items():
            if value:
                tags.append(f"{key}:{value}")
        return tags

    @staticmethod
    def _make_count(
        name: str, timestamp: int, value: float, tags: list[str]
    ) -> MetricSeries:
        return MetricSeries(
            metric=name,
            type=MetricIntakeType.COUNT,
            points=[MetricPoint(timestamp=timestamp, value=value)],
            tags=tags,
        )

    @staticmethod
    def _make_gauge(
        name: str, timestamp: int, value: float, tags: list[str]
    ) -> MetricSeries:
        return MetricSeries(
            metric=name,
            type=MetricIntakeType.GAUGE,
            points=[MetricPoint(timestamp=timestamp, value=value)],
            tags=tags,
        )

    # ------------------------------------------------------------------
    # Sanitização — garante cardinalidade controlada
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_env(env: str) -> str:
        return env.strip().lower().replace(" ", "_") or "unknown"

    @staticmethod
    def _sanitize_slo_type(slo_type: str) -> str:
        allowed = {"metric", "monitor"}
        normalized = slo_type.strip().lower()
        return normalized if normalized in allowed else "unknown"

    def _sanitize_namespace(self, namespace: str) -> str:
        normalized = namespace.strip().lower()
        return normalized if normalized in self._ALLOWED_NAMESPACE_TAGS else "other"

    # ------------------------------------------------------------------
    # Envio — fail-safe para nunca travar o controller
    # ------------------------------------------------------------------

    def _submit(self, series: list[MetricSeries]) -> None:
        try:
            payload = MetricPayload(series=series)
            self._metrics_api.submit_metrics(body=payload)
            self.logger.debug(
                "Métricas SLO enviadas ao Datadog",
                extra={"metrics_count": len(series)},
            )
        except Exception:
            # Métricas nunca devem derrubar o controller
            self.logger.exception("Falha ao enviar métricas SLO ao Datadog")
