from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from src.domain.models import ResourceScorecard


class NamespaceNotificationBuffer:
    def __init__(self, digest_interval_minutes: int = 15) -> None:
        self._buffer: Dict[str, Dict[str, ResourceScorecard]] = {}
        self._last_sent: Dict[str, datetime] = {}
        self._interval = timedelta(minutes=digest_interval_minutes)

    def add_and_maybe_flush(
        self, scorecard: ResourceScorecard
    ) -> Optional[List[ResourceScorecard]]:
        ns = scorecard.resource_namespace
        if ns not in self._buffer:
            self._buffer[ns] = {}
        self._buffer[ns][scorecard.resource_name] = scorecard

        if self._should_flush(ns):
            return self._flush(ns)
        return None

    def pending_count(self, namespace: str) -> int:
        return len(self._buffer.get(namespace, {}))

    def all_namespaces(self) -> List[str]:
        return [ns for ns, apps in self._buffer.items() if apps]

    def _should_flush(self, namespace: str) -> bool:
        last = self._last_sent.get(namespace)
        if last is None:
            return True
        return datetime.now(timezone.utc) - last >= self._interval

    def _flush(self, namespace: str) -> List[ResourceScorecard]:
        scorecards = list(self._buffer.pop(namespace, {}).values())
        self._last_sent[namespace] = datetime.now(timezone.utc)
        return scorecards
