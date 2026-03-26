from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class RemediationState:
    status: str
    version: int
    github_pr_url: Optional[str]
    github_pr_number: Optional[int]


class TitlisApiPort(ABC):
    @abstractmethod
    async def send_scorecard_evaluated(self, payload: dict) -> None:
        ...

    @abstractmethod
    async def send_remediation_event(self, payload: dict) -> None:
        ...

    @abstractmethod
    async def send_slo_reconciled(self, payload: dict) -> None:
        ...

    @abstractmethod
    async def send_notification_log(self, payload: dict) -> None:
        ...

    @abstractmethod
    async def send_resource_metrics(self, payload: dict) -> None:
        ...

    @abstractmethod
    async def get_remediation(self, workload_id: str) -> Optional[RemediationState]:
        ...
