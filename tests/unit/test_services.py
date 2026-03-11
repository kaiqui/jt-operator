import pytest
from unittest.mock import Mock, patch, MagicMock
from src.application.services.slo_service import SLOService
from src.application.services.scorecard_service import ScorecardService
from src.domain.models import SLOConfigSpec, SLOType, SLOTimeframe, SLOAppFramework
import sys
from tests.mock_kopf import MockKopf

sys.modules["kopf"] = MockKopf()


class TestSLOService:
    @pytest.fixture
    def mock_datadog_port(self):
        mock = Mock()

        # Mock get_service_slos
        mock.get_service_slos.return_value = []

        # Mock create_slo
        mock.create_slo.return_value = "test-slo-id"

        # Mock update_slo_apps
        mock.update_slo_apps.return_value = True

        return mock

    @pytest.fixture
    def slo_service(self, mock_datadog_port):
        return SLOService(mock_datadog_port)

    @pytest.fixture
    def sample_slo_spec(self):
        return SLOConfigSpec(
            service="test-service",
            type=SLOType.METRIC,
            target=99.9,
            warning=99.0,
            timeframe=SLOTimeframe.THIRTY_DAYS,
            app_framework=SLOAppFramework.FASTAPI,
            tags=["env:test"],
        )

    def test_build_slo_from_spec(self, slo_service, sample_slo_spec):
        slo = slo_service._build_slo_from_spec(
            namespace="default", service="test-service", spec=sample_slo_spec
        )

        assert slo.name == "SLO - default/test-service"
        assert slo.service_name == "test-service"
        assert slo.slo_type == SLOType.METRIC
        assert slo.target_threshold == 99.9
        assert slo.warning_threshold == 99.0
        assert slo.timeframe == SLOTimeframe.THIRTY_DAYS
        assert "managed_by:titlis_operator" in slo.tags
        assert "namespace:default" in slo.tags
        assert len(slo.thresholds) == 1
        assert slo.thresholds[0]["target"] == 99.9

    def test_build_slo_without_warning(self, slo_service):
        spec = SLOConfigSpec(
            service="test-service",
            type=SLOType.METRIC,
            target=99.9,
            warning=None,  # No warning
            timeframe=SLOTimeframe.THIRTY_DAYS,
        )

        slo = slo_service._build_slo_from_spec(
            namespace="default", service="test-service", spec=spec
        )

        assert slo.warning_threshold is None
        assert "warning" not in slo.thresholds[0]

    def test_build_slo_with_custom_query(self, slo_service):
        spec = SLOConfigSpec(
            service="test-service",
            type=SLOType.METRIC,
            target=99.9,
            numerator="sum:test.numerator",
            denominator="sum:test.denominator",
        )

        slo = slo_service._build_slo_from_spec(
            namespace="default", service="test-service", spec=spec
        )

        assert slo.query is not None
        assert slo.query["numerator"] == "sum:test.numerator"
        assert slo.query["denominator"] == "sum:test.denominator"

    def test_reconcile_slo_new(self, slo_service, sample_slo_spec, mock_datadog_port):
        result = slo_service.reconcile_slo(
            namespace="default", service="test-service", spec=sample_slo_spec
        )

        assert result["success"] is True
        assert result["action"] == "created"
        assert result["slo_id"] == "test-slo-id"
        mock_datadog_port.create_slo.assert_called_once()

    def test_reconcile_slo_existing(
        self, slo_service, sample_slo_spec, mock_datadog_port
    ):
        # Mock existing SLO
        from src.domain.models import SLO, SLOType, SLOTimeframe

        existing_slo = Mock(spec=SLO)
        existing_slo.tags = [
            "slo_uid:default:test-service",
            "managed_by:titlis_operator",
        ]
        existing_slo.slo_id = "existing-id"
        existing_slo.target_threshold = 99.0  # Different from spec (99.9)
        existing_slo.warning_threshold = 98.0
        existing_slo.timeframe = SLOTimeframe.THIRTY_DAYS
        existing_slo.description = "Old description"

        mock_datadog_port.get_service_slos.return_value = [existing_slo]

        result = slo_service.reconcile_slo(
            namespace="default", service="test-service", spec=sample_slo_spec
        )

        # Should update existing SLO
        assert result["success"] is True
        assert result["action"] == "updated"
        mock_datadog_port.update_slo_apps.assert_called_once()

    def test_compare_slo_parameters(self, slo_service):
        from src.domain.models import SLO, SLOType, SLOTimeframe

        existing_slo = Mock(spec=SLO)
        existing_slo.target_threshold = 99.0
        existing_slo.warning_threshold = 98.0
        existing_slo.timeframe = SLOTimeframe.SEVEN_DAYS
        existing_slo.description = "Old"

        desired_slo = Mock(spec=SLO)
        desired_slo.target_threshold = 99.9
        desired_slo.warning_threshold = 99.0
        desired_slo.timeframe = SLOTimeframe.THIRTY_DAYS
        desired_slo.description = "New"

        changes = slo_service._compare_slo_parameters(existing_slo, desired_slo)

        assert "target_threshold" in changes
        assert "warning_threshold" in changes
        assert "timeframe" in changes
        assert "description" in changes
        assert changes["target_threshold"]["old"] == 99.0
        assert changes["target_threshold"]["new"] == 99.9


class TestScorecardService:
    @pytest.fixture
    def mock_kubernetes_apis(self):
        with patch(
            "src.application.services.scorecard_service.get_k8s_apis"
        ) as mock_get_apis:
            mock_core = Mock()
            mock_apps = Mock()
            mock_custom = Mock()

            mock_get_apis.return_value = (mock_core, mock_apps, mock_custom)

            # Mock autoscaling API
            with patch(
                "src.application.services.scorecard_service.client.AutoscalingV2Api"
            ) as mock_autoscaling:
                mock_autoscaling_instance = Mock()
                mock_autoscaling.return_value = mock_autoscaling_instance

                # Mock networking API
                with patch(
                    "src.application.services.scorecard_service.client.NetworkingV1Api"
                ) as mock_networking:
                    mock_networking_instance = Mock()
                    mock_networking.return_value = mock_networking_instance

                    yield {
                        "core": mock_core,
                        "apps": mock_apps,
                        "custom": mock_custom,
                        "autoscaling": mock_autoscaling_instance,
                        "networking": mock_networking_instance,
                    }

    @pytest.fixture
    def sample_deployment_dict(self):
        return {
            "metadata": {
                "name": "test-deployment",
                "namespace": "default",
                "uid": "test-uid",
            },
            "spec": {
                "replicas": 2,
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "test",
                                "image": "test:1.0.0",
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "128Mi"},
                                    "limits": {"cpu": "200m", "memory": "256Mi"},
                                },
                                "livenessProbe": {
                                    "httpGet": {"path": "/health", "port": 8080}
                                },
                                "readinessProbe": {
                                    "httpGet": {"path": "/ready", "port": 8080}
                                },
                                "securityContext": {
                                    "runAsNonRoot": True,
                                    "readOnlyRootFilesystem": True,
                                },
                            }
                        ],
                        "securityContext": {"runAsUser": 1000},
                        "terminationGracePeriodSeconds": 30,
                    }
                },
                "strategy": {"type": "RollingUpdate"},
            },
        }

    @pytest.fixture
    def scorecard_service(self, mock_kubernetes_apis):
        # Mock KubeStateStore
        with patch(
            "src.application.services.scorecard_service.KubeStateStore"
        ) as mock_state_store:
            mock_store_instance = Mock()
            mock_store_instance.get.return_value = None
            mock_store_instance.set.return_value = None
            mock_state_store.return_value = mock_store_instance

            service = ScorecardService(config_path=None)

            # Replace mocked APIs
            service.core = mock_kubernetes_apis["core"]
            service.apps = mock_kubernetes_apis["apps"]
            service.custom = mock_kubernetes_apis["custom"]
            service.autoscaling_v2 = mock_kubernetes_apis["autoscaling"]
            service.networking_v1 = mock_kubernetes_apis["networking"]

            return service

    def test_load_default_rules(self, scorecard_service):
        assert len(scorecard_service.config.rules) > 0

        # Check that we have rules for different pillars
        pillars = set(r.pillar for r in scorecard_service.config.rules)
        assert len(pillars) > 1

        # Check that rules have required fields
        for rule in scorecard_service.config.rules[:5]:  # Check first 5 rules
            assert rule.id
            assert rule.name
            assert rule.description
            assert rule.rule_type

    def test_extract_value_from_resource(
        self, scorecard_service, sample_deployment_dict
    ):
        # Test extracting liveness probe
        liveness = scorecard_service._extract_value_from_resource(
            "RES-001", sample_deployment_dict, "default", "test-deployment"
        )
        assert liveness is not None

        # Test extracting CPU requests
        cpu_requests = scorecard_service._extract_value_from_resource(
            "RES-003", sample_deployment_dict, "default", "test-deployment"
        )
        assert cpu_requests == "100m"

        # Test extracting memory limits
        memory_limits = scorecard_service._extract_value_from_resource(
            "RES-006", sample_deployment_dict, "default", "test-deployment"
        )
        assert memory_limits == "256Mi"

        # Test extracting termination grace period
        grace_period = scorecard_service._extract_value_from_resource(
            "RES-009", sample_deployment_dict, "default", "test-deployment"
        )
        assert grace_period == 30

        # Test extracting non-root
        non_root = scorecard_service._extract_value_from_resource(
            "RES-010", sample_deployment_dict, "default", "test-deployment"
        )
        assert non_root is True

    def test_calculate_pillar_scores(self, scorecard_service):
        from src.domain.models import (
            ValidationResult,
            ValidationPillar,
            ValidationSeverity,
            ValidationRuleType,
        )

        # Create test validation results
        results = [
            ValidationResult(
                rule_id="TEST-001",
                rule_name="Test Rule 1",
                pillar=ValidationPillar.RESILIENCE,
                passed=True,
                severity=ValidationSeverity.WARNING,
                weight=10.0,
                message="Passed",
            ),
            ValidationResult(
                rule_id="TEST-002",
                rule_name="Test Rule 2",
                pillar=ValidationPillar.RESILIENCE,
                passed=False,
                severity=ValidationSeverity.ERROR,
                weight=5.0,
                message="Failed",
            ),
            ValidationResult(
                rule_id="TEST-003",
                rule_name="Test Rule 3",
                pillar=ValidationPillar.SECURITY,
                passed=True,
                severity=ValidationSeverity.INFO,
                weight=3.0,
                message="Passed",
            ),
        ]

        pillar_scores = scorecard_service._calculate_pillar_scores(results)

        assert ValidationPillar.RESILIENCE in pillar_scores
        assert ValidationPillar.SECURITY in pillar_scores

        # Check resilience score calculation
        resilience_score = pillar_scores[ValidationPillar.RESILIENCE]
        assert resilience_score.passed_checks == 1
        assert resilience_score.total_checks == 2
        assert 0 <= resilience_score.score <= 100

        # Check security score
        security_score = pillar_scores[ValidationPillar.SECURITY]
        assert security_score.score == 100.0  # All passed

    def test_calculate_overall_score(self, scorecard_service):
        from src.domain.models import (
            PillarScore,
            ValidationPillar,
            ValidationResult,
            ValidationSeverity,
        )

        # Create mock pillar scores
        pillar_scores = {
            ValidationPillar.RESILIENCE: PillarScore(
                pillar=ValidationPillar.RESILIENCE,
                score=80.0,
                max_score=100.0,
                passed_checks=4,
                total_checks=5,
                weighted_score=4.0,
                validation_results=[],
            ),
            ValidationPillar.SECURITY: PillarScore(
                pillar=ValidationPillar.SECURITY,
                score=90.0,
                max_score=100.0,
                passed_checks=9,
                total_checks=10,
                weighted_score=9.0,
                validation_results=[],
            ),
        }

        overall_score = scorecard_service._calculate_overall_score(pillar_scores)

        assert 0 <= overall_score <= 100
        # Should be weighted average (resilience: 30, security: 25)
        # (80*30 + 90*25) / 55 = 84.55
        assert abs(overall_score - 84.55) < 0.1

    def test_should_notify_logic(self, scorecard_service):
        from src.domain.models import ResourceScorecard

        # Create test scorecards
        critical_scorecard = ResourceScorecard(
            resource_name="critical",
            resource_namespace="default",
            resource_kind="Deployment",
            overall_score=65.0,  # Below critical threshold
            critical_issues=0,
            error_issues=5,
            warning_issues=3,
            passed_checks=10,
            total_checks=18,
        )

        good_scorecard = ResourceScorecard(
            resource_name="good",
            resource_namespace="default",
            resource_kind="Deployment",
            overall_score=95.0,
            critical_issues=0,
            error_issues=0,
            warning_issues=1,
            passed_checks=17,
            total_checks=18,
        )

        # Critical scorecard should notify
        assert scorecard_service.should_notify(critical_scorecard) is True

        # Good scorecard shouldn't notify
        assert scorecard_service.should_notify(good_scorecard) is False

    def test_get_notification_severity(self, scorecard_service):
        from src.domain.models import ResourceScorecard

        # Test critical
        critical_scorecard = ResourceScorecard(
            resource_name="test",
            resource_namespace="default",
            resource_kind="Deployment",
            overall_score=65.0,
            critical_issues=1,
        )
        assert (
            scorecard_service.get_notification_severity(critical_scorecard)
            == "critical"
        )

        # Test error
        error_scorecard = ResourceScorecard(
            resource_name="test",
            resource_namespace="default",
            resource_kind="Deployment",
            overall_score=75.0,
            error_issues=2,
        )
        assert scorecard_service.get_notification_severity(error_scorecard) == "error"

        # Test warning
        warning_scorecard = ResourceScorecard(
            resource_name="test",
            resource_namespace="default",
            resource_kind="Deployment",
            overall_score=85.0,
            warning_issues=3,
        )
        assert (
            scorecard_service.get_notification_severity(warning_scorecard) == "warning"
        )

        # Test info
        info_scorecard = ResourceScorecard(
            resource_name="test",
            resource_namespace="default",
            resource_kind="Deployment",
            overall_score=95.0,
            warning_issues=0,
        )
        assert scorecard_service.get_notification_severity(info_scorecard) == "info"
