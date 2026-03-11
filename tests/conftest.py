import pytest
import sys
import os
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Dict, Any

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))


# Mock kopf first to avoid metaclass conflicts
class MockKopf:
    class TemporaryError(Exception):
        def __init__(self, message, delay=60):
            self.message = message
            self.delay = delay
            super().__init__(message)

    class _OnNamespace:
        @staticmethod
        def startup():
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def cleanup():
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def create(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def update(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def delete(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def field(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def resume(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

    on = _OnNamespace()

    @staticmethod
    def on_startup():
        def decorator(func):
            return func

        return decorator

    @staticmethod
    def on_cleanup():
        def decorator(func):
            return func

        return decorator

    class OperatorSettings:
        def __init__(self):
            self.health = type("obj", (object,), {"server": "0.0.0.0", "port": 8080})()
            self.peering = type(
                "obj", (object,), {"name": "titlis-operator", "namespace": "titlis"}
            )()


# Inject kopf mock
sys.modules["kopf"] = MockKopf()


@pytest.fixture(autouse=True)
def mock_all_external_calls():
    patches = []

    # Mock Kubernetes config
    patches.append(patch("kubernetes.config.load_incluster_config"))
    patches.append(patch("kubernetes.config.load_kube_config"))

    # Mock Kubernetes client APIs - patch ALL methods that might be called
    patches.append(patch("kubernetes.client.CoreV1Api", autospec=True))
    patches.append(patch("kubernetes.client.AppsV1Api", autospec=True))
    patches.append(patch("kubernetes.client.CustomObjectsApi", autospec=True))
    patches.append(patch("kubernetes.client.AutoscalingV2Api", autospec=True))
    patches.append(patch("kubernetes.client.NetworkingV1Api", autospec=True))

    # Mock ApiException
    patches.append(patch("kubernetes.client.rest.ApiException"))

    # Mock datadog_api_client modules
    patches.append(
        patch(
            "datadog_api_client.v1.api.service_level_objectives_api.ServiceLevelObjectivesApi"
        )
    )
    patches.append(
        patch(
            "datadog_api_client.v1.model.service_level_objective_request.ServiceLevelObjectiveRequest"
        )
    )
    patches.append(patch("datadog_api_client.v1.model.slo_type.SLOType"))
    patches.append(
        patch("datadog_api_client.v1.model.slo_time_slice_spec.SLOTimeSliceSpec")
    )
    patches.append(
        patch(
            "datadog_api_client.v1.model.slo_time_slice_condition.SLOTimeSliceCondition"
        )
    )
    patches.append(
        patch("datadog_api_client.v1.model.slo_time_slice_query.SLOTimeSliceQuery")
    )
    patches.append(
        patch(
            "datadog_api_client.v1.model.slo_time_slice_comparator.SLOTimeSliceComparator"
        )
    )
    patches.append(patch("datadog_api_client.v1.model.slo_formula.SLOFormula"))
    patches.append(
        patch(
            "datadog_api_client.v1.model.formula_and_function_metric_query_definition.FormulaAndFunctionMetricQueryDefinition"
        )
    )
    patches.append(
        patch(
            "datadog_api_client.v1.model.formula_and_function_metric_data_source.FormulaAndFunctionMetricDataSource"
        )
    )
    patches.append(patch("datadog_api_client.v1.model.slo_threshold.SLOThreshold"))
    patches.append(patch("datadog_api_client.v1.model.slo_timeframe.SLOTimeframe"))
    patches.append(
        patch(
            "datadog_api_client.v1.model.service_level_objective.ServiceLevelObjective"
        )
    )

    # Mock slack_sdk
    patches.append(patch("slack_sdk.web.async_client.AsyncWebClient"))
    patches.append(patch("slack_sdk.webhook.async_client.AsyncWebhookClient"))
    patches.append(patch("slack_sdk.errors.SlackApiError"))

    # Mock pythonjsonlogger
    patches.append(patch("pythonjsonlogger.jsonlogger.JsonFormatter"))

    # Mock KubeStateStore specifically - this is CRITICAL
    patches.append(patch("src.infrastructure.kubernetes.state_store.KubeStateStore"))

    # Mock get_k8s_apis to return mocked APIs
    patches.append(patch("src.infrastructure.kubernetes.client.get_k8s_apis"))

    # Mock ScorecardService to avoid KubeStateStore instantiation during import
    patches.append(patch("src.application.services.scorecard_service.KubeStateStore"))
    patches.append(patch("src.application.services.scorecard_service.get_k8s_apis"))

    # Mock get_scorecard_service to return a mock
    patches.append(patch("src.bootstrap.dependencies.get_scorecard_service"))

    # Start all patches
    mocks = [p.start() for p in patches]

    # Configure specific mocks
    for mock in mocks:
        if "KubeStateStore" in str(mock):
            # Configure KubeStateStore mock
            mock_instance = Mock()
            mock_instance.get.return_value = None
            mock_instance.set.return_value = None
            mock.return_value = mock_instance

    yield

    # Stop all patches
    for p in patches:
        p.stop()


@pytest.fixture
def mock_settings():
    with patch("src.settings.settings") as mock_settings:
        # Create a proper mock with all required attributes
        mock_settings.enable_scorecard_controller = True
        mock_settings.datadog_api_key = "test-api-key"
        mock_settings.datadog_app_key = "test-app-key"
        mock_settings.datadog_site = "datadoghq.com"
        mock_settings.kubernetes_namespace = "default"
        mock_settings.reconcile_interval_seconds = 300
        mock_settings.log_level = "INFO"

        # Mock slack settings as an object with attributes
        mock_slack_settings = Mock()
        mock_slack_settings.enabled = True
        mock_slack_settings.default_channel = "#test"
        mock_slack_settings.timeout_seconds = 10.0
        mock_slack_settings.rate_limit_per_minute = 60
        mock_slack_settings.bot_token = None
        mock_slack_settings.webhook_url = None
        mock_settings.slack = mock_slack_settings

        yield mock_settings


@pytest.fixture
def mock_kube_state_store():
    with patch(
        "src.infrastructure.kubernetes.state_store.KubeStateStore"
    ) as mock_store:
        mock_instance = Mock()
        mock_instance.get.return_value = None
        mock_instance.set.return_value = None
        mock_store.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_datadog_api():
    with patch("src.infrastructure.datadog.client.ApiClient") as mock_api_client:
        with patch("src.infrastructure.datadog.client.Configuration") as mock_config:
            mock_api = Mock()
            mock_api_client.return_value = mock_api
            mock_config.return_value = Mock()
            yield mock_api


@pytest.fixture
def mock_slack_sdk():
    with patch("src.infrastructure.slack.repository.AsyncWebClient") as mock_web_client:
        with patch(
            "src.infrastructure.slack.repository.AsyncWebhookClient"
        ) as mock_webhook_client:
            mock_client = AsyncMock()
            mock_web_client.return_value = mock_client
            mock_webhook = AsyncMock()
            mock_webhook_client.return_value = mock_webhook
            yield {"web_client": mock_client, "webhook_client": mock_webhook}


@pytest.fixture
def sample_slo_config():
    return {
        "name": "test-slo",
        "service": "test-service",
        "type": "metric",
        "target": 99.9,
        "warning": 99.0,
        "timeframe": "30d",
        "description": "Test SLO",
        "tags": ["env:test", "team:test"],
    }


@pytest.fixture
def sample_kubernetes_deployment():
    return {
        "metadata": {
            "name": "test-deployment",
            "namespace": "default",
            "uid": "test-uid",
            "labels": {"app": "test"},
            "annotations": {},
        },
        "spec": {
            "replicas": 2,
            "selector": {"matchLabels": {"app": "test"}},
            "template": {
                "metadata": {"labels": {"app": "test"}},
                "spec": {
                    "containers": [
                        {
                            "name": "test",
                            "image": "test:1.0.0",
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"cpu": "200m", "memory": "256Mi"},
                            },
                        }
                    ]
                },
            },
        },
    }


@pytest.fixture
def async_context():
    import asyncio

    def run_async(coro):
        return asyncio.run(coro)

    return run_async


# Fixture específica para mockar o ScorecardController
@pytest.fixture
def mock_scorecard_controller_deps():
    with patch(
        "src.controllers.scorecard_controller.get_scorecard_service"
    ) as mock_get_scorecard:
        with patch(
            "src.controllers.scorecard_controller.ScorecardService"
        ) as mock_scorecard_class:
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
            mock_scorecard_class.return_value = mock_scorecard
            mock_get_scorecard.return_value = mock_scorecard
            yield mock_scorecard
