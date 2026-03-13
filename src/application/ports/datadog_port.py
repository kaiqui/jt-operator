from abc import ABC, abstractmethod
from typing import List, Optional
from src.domain.models import ServiceDefinition, SLO


class DatadogPort(ABC):
    @abstractmethod
    def get_service_definition(self, service_name: str) -> Optional[ServiceDefinition]:
        pass

    @abstractmethod
    def get_service_slos(self, service_name: str) -> List[SLO]:
        pass

    @abstractmethod
    def create_slo(self, slo: SLO) -> Optional[str]:
        pass

    @abstractmethod
    def update_slo_apps(self, slo_id: str, slo: SLO) -> bool:
        pass

    @abstractmethod
    def delete_slo(self, slo_id: str) -> bool:
        pass

    @abstractmethod
    def get_request_count(self, service_name: str, days: int = 30) -> Optional[int]:
        pass
