from typing import Any, Callable
from src.infrastructure.datadog.client import DatadogClientBase


class DatadogManagerBase(DatadogClientBase):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def execute(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return self.execute_with_retry(func, *args, **kwargs)
