import pytest
from datetime import datetime
from src.domain.models import (
    SLO,
    SLOType,
    SLOTimeframe,
    ServiceDefinition,
    ServiceTier,
    ComplianceStatus,
    SLOConfigSpec,
    ValidationPillar,
    ValidationRule,
    ValidationResult,
    ResourceScorecard,
)
from src.domain.slack_models import (
    NotificationSeverity,
    NotificationChannel,
    SlackNotification,
    SlackMessageTemplate,
)


class TestDomainModels:
    def test_slo_model(self):
        slo = SLO(
            name="test-slo",
            service_name="test-service",
            slo_type=SLOType.METRIC,
            target_threshold=99.9,
            warning_threshold=99.0,
            timeframe=SLOTimeframe.THIRTY_DAYS,
            description="Test SLO",
            tags=["env:test"],
            slo_id="test-id",
        )

        assert slo.name == "test-slo"
        assert slo.service_name == "test-service"
        assert slo.slo_type == SLOType.METRIC
        assert slo.target_threshold == 99.9
        assert slo.timeframe == SLOTimeframe.THIRTY_DAYS
        assert slo.slo_id == "test-id"

    def test_service_definition(self):
        service_def = ServiceDefinition(
            dd_service="test-service",
            description="Test service",
            team="platform",
            tier=ServiceTier.TIER_1,
            tags=["env:prod"],
            contacts=[{"name": "team", "type": "slack", "contact": "#platform"}],
        )

        assert service_def.dd_service == "test-service"
        assert service_def.tier == ServiceTier.TIER_1
        assert len(service_def.tags) == 1

    def test_slo_config_spec(self):
        spec = SLOConfigSpec(
            service="test-service",
            type=SLOType.METRIC,
            target=99.9,
            warning=99.0,
            timeframe=SLOTimeframe.THIRTY_DAYS,
            tags=["env:test"],
        )

        assert spec.service == "test-service"
        assert spec.type == SLOType.METRIC
        assert 0 <= spec.target <= 100

    def test_validation_rule(self):
        rule = ValidationRule(
            id="TEST-001",
            pillar=ValidationPillar.RESILIENCE,
            name="Test Rule",
            description="Test validation rule",
            rule_type="boolean",
            source="K8s API",
            severity="warning",
            weight=1.0,
        )

        assert rule.id == "TEST-001"
        assert rule.pillar == ValidationPillar.RESILIENCE
        assert rule.enabled is True

    def test_slack_models(self):
        # Test NotificationSeverity enum
        assert NotificationSeverity.INFO.value == "info"
        assert NotificationSeverity.CRITICAL.value == "critical"

        # Test NotificationChannel enum
        assert NotificationChannel.OPERATIONAL.value == "operational"
        assert NotificationChannel.ALERTS.value == "alerts"

        # Test SlackMessageTemplate
        template = SlackMessageTemplate(
            title="Test Template", include_timestamp=True, max_message_length=1000
        )

        assert template.title == "Test Template"
        assert template.include_timestamp is True

        # Test SlackNotification
        notification = SlackNotification(
            title="Test",
            message="Test message",
            severity=NotificationSeverity.INFO,
            channel=NotificationChannel.OPERATIONAL,
            namespace="default",
            pod_name="test-pod",
        )

        assert notification.title == "Test"
        assert notification.severity == NotificationSeverity.INFO
        assert notification.channel == NotificationChannel.OPERATIONAL

    def test_resource_scorecard(self):
        scorecard = ResourceScorecard(
            resource_name="test-deployment",
            resource_namespace="default",
            resource_kind="Deployment",
            overall_score=85.5,
            critical_issues=0,
            error_issues=2,
            warning_issues=3,
            passed_checks=10,
            total_checks=15,
        )

        assert scorecard.resource_name == "test-deployment"
        assert scorecard.overall_score == 85.5
        assert scorecard.critical_issues == 0

        # Test to_dict method
        scorecard_dict = scorecard.to_dict()
        assert "resource_name" in scorecard_dict
        assert "overall_score" in scorecard_dict
        assert "pillar_scores" in scorecard_dict
