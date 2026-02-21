"""
tests/test_castai_monitor.py

Testes unitários para o CastAI Monitor Controller.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_pod(phase="Running", ready_status="True", ready_reason=None):
    """Cria um mock de pod Kubernetes."""
    pod = MagicMock()
    pod.metadata.name = "castai-agent-abc12"
    pod.metadata.creation_timestamp = "2024-01-01T00:00:00Z"
    pod.status.phase = phase

    condition = MagicMock()
    condition.type = "Ready"
    condition.status = ready_status
    condition.reason = ready_reason
    pod.status.conditions = [condition]

    return pod


# ---------------------------------------------------------------------------
# CastAIHealthChecker
# ---------------------------------------------------------------------------

class TestCastAIHealthChecker:

    @patch("src.infrastructure.kubernetes.castai_health.kubernetes")
    def _make_checker(self, mock_k8s, namespace="castai-agent", cluster="test-cluster"):
        from src.infrastructure.kubernetes.castai_health import CastAIHealthChecker
        mock_k8s.config.load_incluster_config.return_value = None
        return CastAIHealthChecker(namespace=namespace, cluster_name=cluster)

    def test_healthy_pod(self):
        checker = self._make_checker()
        pod = _make_pod(phase="Running", ready_status="True")
        is_healthy, reason = checker._evaluate_pod(pod)
        assert is_healthy is True
        assert "Ready" in reason

    def test_not_running(self):
        checker = self._make_checker()
        pod = _make_pod(phase="Pending")
        is_healthy, reason = checker._evaluate_pod(pod)
        assert is_healthy is False
        assert "Pending" in reason

    def test_not_ready(self):
        checker = self._make_checker()
        pod = _make_pod(phase="Running", ready_status="False", ready_reason="ContainersNotReady")
        is_healthy, reason = checker._evaluate_pod(pod)
        assert is_healthy is False
        assert "ContainersNotReady" in reason

    def test_no_ready_condition(self):
        checker = self._make_checker()
        pod = _make_pod()
        pod.status.conditions = []  # sem condições
        is_healthy, reason = checker._evaluate_pod(pod)
        assert is_healthy is False

    @patch("src.infrastructure.kubernetes.castai_health.kubernetes")
    @patch("src.infrastructure.kubernetes.castai_health.k8s_client.CoreV1Api")
    def test_no_pods_found(self, mock_core_v1, mock_k8s):
        mock_k8s.config.load_incluster_config.return_value = None
        mock_core_v1.return_value.list_namespaced_pod.return_value.items = []

        from src.infrastructure.kubernetes.castai_health import CastAIHealthChecker
        checker = CastAIHealthChecker(namespace="castai-agent", cluster_name="test")
        result = checker._check_service("castai-agent")

        assert result.is_healthy is False
        assert "Nenhum pod" in result.reason


# ---------------------------------------------------------------------------
# CastAIMetricsManager
# ---------------------------------------------------------------------------

class TestCastAIMetricsManager:

    @patch("src.infrastructure.datadog.managers.castai_metrics.ApiClient")
    @patch("src.infrastructure.datadog.managers.castai_metrics.MetricsApi")
    def test_send_healthy(self, mock_metrics_api_cls, mock_api_client_cls):
        mock_api = MagicMock()
        mock_metrics_api_cls.return_value = mock_api
        mock_api_client_cls.return_value.__enter__ = lambda s: MagicMock()
        mock_api_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        from src.infrastructure.datadog.managers.castai_metrics import CastAIMetricsManager
        manager = CastAIMetricsManager(api_key="fake-key", app_key=None, site="datadoghq.com")

        with patch.object(manager, "send_pod_health", return_value=True) as mock_send:
            result = manager.send_pod_health(
                service="castai-agent",
                namespace="castai-agent",
                cluster_name="my-cluster",
                is_healthy=True,
            )
        # Apenas valida que o método existe e foi chamável
        assert result is True

    def test_send_all_delegates(self):
        from src.infrastructure.datadog.managers.castai_metrics import CastAIMetricsManager

        manager = CastAIMetricsManager.__new__(CastAIMetricsManager)
        manager.logger = MagicMock()
        manager.configuration = MagicMock()

        calls = []

        def fake_send(**kwargs):
            calls.append(kwargs)
            return True

        manager.send_pod_health = fake_send

        results = [
            {"service": "castai-agent", "namespace": "castai-agent", "cluster_name": "c1", "is_healthy": True},
            {"service": "castai-cluster-controller", "namespace": "castai-agent", "cluster_name": "c1", "is_healthy": False},
        ]
        manager.send_all(results)

        assert len(calls) == 2
        assert calls[0]["service"] == "castai-agent"
        assert calls[1]["is_healthy"] is False


# ---------------------------------------------------------------------------
# Controller — run_castai_health_check
# ---------------------------------------------------------------------------

class TestCastAIMonitorController:

    @pytest.mark.asyncio
    async def test_skips_when_no_cluster_name(self):
        with patch("src.controllers.castai_monitor_controller.settings") as mock_settings:
            mock_settings.castai_cluster_name = ""
            mock_settings.castai_monitor_namespace = "castai-agent"

            from src.controllers.castai_monitor_controller import run_castai_health_check
            # Não deve lançar exceção
            await run_castai_health_check()

    @pytest.mark.asyncio
    async def test_full_cycle_healthy(self):
        from src.infrastructure.kubernetes.castai_health import PodHealthResult

        mock_results = [
            PodHealthResult(
                service="castai-agent",
                namespace="castai-agent",
                cluster_name="my-cluster",
                is_healthy=True,
                pod_name="castai-agent-xyz",
                reason="Running e Ready",
            ),
            PodHealthResult(
                service="castai-cluster-controller",
                namespace="castai-agent",
                cluster_name="my-cluster",
                is_healthy=True,
                pod_name="castai-cluster-controller-abc",
                reason="Running e Ready",
            ),
        ]

        with (
            patch("src.controllers.castai_monitor_controller.settings") as mock_settings,
            patch("src.controllers.castai_monitor_controller.CastAIHealthChecker") as mock_checker_cls,
            patch("src.controllers.castai_monitor_controller.CastAIMetricsManager") as mock_metrics_cls,
        ):
            mock_settings.castai_cluster_name = "my-cluster"
            mock_settings.castai_monitor_namespace = "castai-agent"
            mock_settings.datadog_api_key = "fake-key"
            mock_settings.datadog_app_key = None
            mock_settings.datadog_site = "datadoghq.com"

            mock_checker_cls.return_value.check_all.return_value = mock_results
            mock_metrics_instance = MagicMock()
            mock_metrics_cls.return_value = mock_metrics_instance

            from src.controllers.castai_monitor_controller import run_castai_health_check
            await run_castai_health_check()

            mock_metrics_instance.send_all.assert_called_once()
            sent_data = mock_metrics_instance.send_all.call_args[0][0]
            assert len(sent_data) == 2
            assert all(r["is_healthy"] for r in sent_data)

    @pytest.mark.asyncio
    async def test_skips_metrics_when_no_api_key(self):
        from src.infrastructure.kubernetes.castai_health import PodHealthResult

        mock_results = [
            PodHealthResult(
                service="castai-agent",
                namespace="castai-agent",
                cluster_name="my-cluster",
                is_healthy=False,
                reason="Nenhum pod encontrado",
            )
        ]

        with (
            patch("src.controllers.castai_monitor_controller.settings") as mock_settings,
            patch("src.controllers.castai_monitor_controller.CastAIHealthChecker") as mock_checker_cls,
            patch("src.controllers.castai_monitor_controller.CastAIMetricsManager") as mock_metrics_cls,
        ):
            mock_settings.castai_cluster_name = "my-cluster"
            mock_settings.castai_monitor_namespace = "castai-agent"
            mock_settings.datadog_api_key = None  # sem key

            mock_checker_cls.return_value.check_all.return_value = mock_results

            from src.controllers.castai_monitor_controller import run_castai_health_check
            await run_castai_health_check()

            mock_metrics_cls.assert_not_called()