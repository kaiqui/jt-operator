"""
application/services/namespace_notification_buffer.py

Buffers ResourceScorecard evaluations per namespace and flushes them as a
single digest notification.

Behaviour (Decision 3 — C, by namespace):
  - The first scorecard added to an empty namespace buffer triggers an
    immediate flush (fast feedback on the very first event).
  - Subsequent additions within the configured interval are accumulated.
  - The next add after the interval expires flushes all buffered scorecards
    as a single namespace-level digest.

Thread-safety: the internal dict mutation is atomic enough for CPython's GIL
and Kopf's single-threaded event loop. No explicit lock is needed here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from src.domain.models import ResourceScorecard


class NamespaceNotificationBuffer:
    """
    Groups scorecard results by namespace and emits digest batches.

    Usage::

        buffer = NamespaceNotificationBuffer(digest_interval_minutes=15)

        scorecards_to_send = buffer.add_and_maybe_flush(scorecard)
        if scorecards_to_send is not None:
            await send_digest(namespace, scorecards_to_send)
    """

    def __init__(self, digest_interval_minutes: int = 15) -> None:
        # {namespace: {app_name: scorecard}}  — inner dict keyed by app_name
        # so repeated evaluations of the same app replace the previous entry.
        self._buffer: Dict[str, Dict[str, ResourceScorecard]] = {}
        self._last_sent: Dict[str, datetime] = {}
        self._interval = timedelta(minutes=digest_interval_minutes)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_and_maybe_flush(
        self, scorecard: ResourceScorecard
    ) -> Optional[List[ResourceScorecard]]:
        """
        Add *scorecard* to the namespace buffer.

        Returns a list of all buffered scorecards for that namespace if it is
        time to emit a digest, otherwise returns ``None``.
        """
        ns = scorecard.resource_namespace
        if ns not in self._buffer:
            self._buffer[ns] = {}
        # Replace previous entry for this app (idempotent).
        self._buffer[ns][scorecard.resource_name] = scorecard

        if self._should_flush(ns):
            return self._flush(ns)
        return None

    def pending_count(self, namespace: str) -> int:
        """Number of scorecards waiting in *namespace*'s buffer."""
        return len(self._buffer.get(namespace, {}))

    def all_namespaces(self) -> List[str]:
        """Namespaces that have at least one buffered scorecard."""
        return [ns for ns, apps in self._buffer.items() if apps]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _should_flush(self, namespace: str) -> bool:
        last = self._last_sent.get(namespace)
        # Never sent for this namespace → flush immediately (fast first feedback).
        if last is None:
            return True
        return datetime.now(timezone.utc) - last >= self._interval

    def _flush(self, namespace: str) -> List[ResourceScorecard]:
        scorecards = list(self._buffer.pop(namespace, {}).values())
        self._last_sent[namespace] = datetime.now(timezone.utc)
        return scorecards
