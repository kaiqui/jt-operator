from abc import ABC, abstractmethod
from typing import Dict, Any


class StatusWriter(ABC):

    @abstractmethod
    def update(
        self,
        body: Dict[str, Any],
        status: Dict[str, Any]
    ) -> None:
        pass
