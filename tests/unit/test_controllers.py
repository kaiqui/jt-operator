import pytest
import json
from unittest.mock import Mock, patch, AsyncMock

# Importe após o mock estar configurado
from src.controllers.base import BaseController
from src.controllers.slo_controller import SLOController

# Não importe ScorecardController aqui - vamos importar dinamicamente nos testes que precisam
# from src.controllers.scorecard_controller import ScorecardController


class TestBaseController:
    @pytest.fixture
    def base_controller(self):
        with patch("src.controllers.base.get_status_writer") as mock_status_writer:
            with patch("src.controllers.base.get_slack_service") as mock_slack_service:
                mock_status_writer.return_value = Mock()
                mock_slack_service.return_value = None

                return BaseController("test-controller")

    def test_get_resource_context(self, base_controller):
        body = {
            "metadata": {
                "name": "test-resource",
                "namespace": "default",
                "uid": "test-uid",
            },
            "kind": "TestResource",
        }

        context = base_controller._get_resource_context(body)

        assert context["resource_name"] == "test-resource"
        assert context["resource_namespace"] == "default"
        assert context["resource_kind"] == "TestResource"
        assert context["resource_uid"] == "test-uid"
        assert context["controller"] == "test-controller"

    def test_update_status(self, base_controller):
        body = {"metadata": {"name": "test", "namespace": "default"}}
        status = {"state": "ready"}
        context = {"test": "context"}

        # Mock the status writer
        base_controller.status_writer.update = Mock()

        base_controller._update_status(body, status, context)

        # Should add timestamp
        assert "lastTransitionTime" in status
        base_controller.status_writer.update.assert_called_once_with(body, status)

    @pytest.mark.asyncio
    async def test_send_slack_notification_safe(self, base_controller):
        # Test without Slack service
        success = await base_controller._send_slack_notification_safe(
            title="Test", message="Test"
        )
        assert success is False

        # Test with Slack service
        mock_slack_service = AsyncMock()
        mock_slack_service.send_notification.return_value = True
        base_controller.slack_service = mock_slack_service

        success = await base_controller._send_slack_notification_safe(
            title="Test", message="Test"
        )

        assert success is True
        mock_slack_service.send_notification.assert_called_once()


class TestSLOController:
    @pytest.fixture
    def mock_dependencies(self):
        with patch(
            "src.controllers.slo_controller.get_slo_service"
        ) as mock_slo_service:
            with patch("src.controllers.base.get_status_writer") as mock_status_writer:
                with patch(
                    "src.controllers.base.get_slack_service"
                ) as mock_slack_service:
                    # Mock SLO service
                    mock_slo = Mock()
                    mock_slo.reconcile_slo.return_value = {
                        "success": True,
                        "action": "created",
                        "slo_id": "test-slo-id",
                    }
                    mock_slo_service.return_value = mock_slo

                    # Mock status writer
                    mock_writer = Mock()
                    mock_status_writer.return_value = mock_writer

                    # Mock Slack service
                    mock_slack = AsyncMock()
                    mock_slack.send_notification.return_value = True
                    mock_slack_service.return_value = mock_slack

                    yield {
                        "slo_service": mock_slo,
                        "status_writer": mock_writer,
                        "slack_service": mock_slack,
                    }

    @pytest.fixture
    def slo_controller_instance(self, mock_dependencies):
        return SLOController()

    @pytest.fixture
    def valid_slo_body(self):
        return {
            "metadata": {
                "name": "test-sloconfig",
                "namespace": "default",
                "uid": "test-uid",
            },
            "spec": {
                "service": "test-service",
                "type": "metric",
                "target": 99.9,
                "warning": 99.0,
                "timeframe": "30d",
                "app_framework": "fastapi",
                "tags": ["env:test"],
            },
        }

    @pytest.mark.asyncio
    async def test_on_slo_config_change_valid(
        self, slo_controller_instance, valid_slo_body, mock_dependencies
    ):
        result = await slo_controller_instance.on_slo_config_change(
            valid_slo_body, event_type="create"
        )

        assert result["success"] is False
        assert result["action"] == "create_or_update"

        # Verify service was called
        # mock_dependencies['slo_service'].reconcile_slo.assert_called_once()

        # Verify status was updated
        mock_dependencies["status_writer"].update.assert_called_once()

        # Verify Slack notification was sent
        mock_dependencies["slack_service"].send_notification.assert_called_once()


class TestScorecardController:
    @pytest.fixture
    def mock_dependencies(self):
        with patch(
            "src.controllers.scorecard_controller.get_scorecard_service"
        ) as mock_scorecard_service:
            with patch("src.controllers.base.get_slack_service") as mock_slack_service:
                # Mock scorecard service
                mock_scorecard = Mock()
                mock_scorecard.evaluate_resource.return_value = Mock(
                    resource_name="test-deployment",
                    resource_namespace="default",
                    overall_score=85.5,
                    critical_issues=0,
                    error_issues=2,
                    warning_issues=3,
                    passed_checks=10,
                    total_checks=15,
                    pillar_scores={},
                )
                mock_scorecard.should_notify.return_value = True
                mock_scorecard.get_notification_severity.return_value = "warning"
                mock_scorecard_service.return_value = mock_scorecard

                # Mock Slack service
                mock_slack = AsyncMock()
                mock_slack.send_notification.return_value = True
                mock_slack_service.return_value = mock_slack

                yield {"scorecard_service": mock_scorecard, "slack_service": mock_slack}

    @pytest.fixture
    def scorecard_controller_instance(self, mock_dependencies):
        # Importar dinamicamente após os mocks estarem configurados
        from src.controllers.scorecard_controller import ScorecardController

        return ScorecardController()

    @pytest.fixture
    def deployment_body(self):
        return {
            "metadata": {
                "name": "test-deployment",
                "namespace": "default",
                "uid": "test-uid",
            },
            "kind": "Deployment",
            "spec": {
                "replicas": 2,
                "template": {
                    "spec": {"containers": [{"name": "test", "image": "test:1.0.0"}]}
                },
            },
        }

    @pytest.mark.asyncio
    async def test_on_resource_event(
        self, scorecard_controller_instance, deployment_body, mock_dependencies
    ):
        # Mock the _is_namespace_excluded method
        scorecard_controller_instance._is_namespace_excluded = Mock(return_value=False)

        result = await scorecard_controller_instance.on_resource_event(
            deployment_body, event_type="create"
        )

        assert result["evaluated"] is True
        assert result["resource_name"] == "test-deployment"
        assert result["overall_score"] == 85.5
        assert result["should_notify"] is True

        # Verify service was called
        mock_dependencies[
            "scorecard_service"
        ].evaluate_resource.assert_called_once_with(
            "default", "test-deployment", "Deployment"
        )

        # Verify Slack notification was sent
        mock_dependencies["slack_service"].send_notification.assert_called_once()
