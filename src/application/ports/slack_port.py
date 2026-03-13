from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from src.domain.slack_models import SlackNotification, NotificationSeverity


class SlackNotifierPort(ABC):
    @abstractmethod
    async def send_notification(self, notification: SlackNotification) -> bool:
        pass

    @abstractmethod
    async def send_kopf_event(
        self,
        event_type: str,
        body: Dict[str, Any],
        reason: str,
        message: str,
        severity: Optional[NotificationSeverity] = None,
        **kwargs: Any,
    ) -> bool:
        pass

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def initialize(self) -> None:
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        pass
