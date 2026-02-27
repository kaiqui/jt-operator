from typing import Optional
from src.infrastructure.datadog.managers.slo import SLOManager


class DatadogManagerFactory:
    def __init__(self, api_key: Optional[str], app_key: Optional[str], site: Optional[str] = None):
        self.api_key = api_key
        self.app_key = app_key
        self.site = site

    def create_manager(self, manager_name: str):
        manager_name = manager_name.lower()
        common_kwargs = {"api_key": self.api_key, "app_key": self.app_key, "site": self.site}
        if manager_name in ("slo", "slo_manager"):
            return SLOManager(**common_kwargs)
        if manager_name in ("metrics", "metrics_manager"):
            from src.infrastructure.datadog.managers.metrics import DatadogMetricsManager
            return DatadogMetricsManager(**common_kwargs)
        raise ValueError(f"Manager Datadog desconhecido: {manager_name}")