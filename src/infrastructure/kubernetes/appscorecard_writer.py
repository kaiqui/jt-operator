from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes import client
from kubernetes.client.rest import ApiException

from src.domain.models import ResourceScorecard
from src.domain.enriched_scorecard import EnrichedScorecard
from src.utils.json_logger import get_logger

GROUP = "titlis.io"
VERSION = "v1"
PLURAL = "appscorecards"

logger = get_logger(__name__)


class AppScorecardWriter:
    def __init__(self) -> None:
        self._api: Optional[client.CustomObjectsApi] = None

    @property
    def _custom_api(self) -> client.CustomObjectsApi:
        if self._api is None:
            self._api = client.CustomObjectsApi()
        return self._api

    def upsert(
        self,
        scorecard: ResourceScorecard,
        deployment_body: Dict[str, Any],
        enriched: Optional[EnrichedScorecard] = None,
        remediation_pr: Optional[Dict[str, Any]] = None,
    ) -> None:
        namespace = scorecard.resource_namespace
        name = scorecard.resource_name

        body = self._build_body(scorecard, deployment_body, enriched, remediation_pr)

        try:
            existing = self._custom_api.get_namespaced_custom_object(
                group=GROUP,
                version=VERSION,
                namespace=namespace,
                plural=PLURAL,
                name=name,
            )
            self._update(existing, body, namespace, name)
            logger.debug(
                "AppScorecard atualizado",
                extra={
                    "resource_name": name,
                    "namespace": namespace,
                    "score": scorecard.overall_score,
                },
            )
        except ApiException as exc:
            if exc.status == 404:
                self._create(body, namespace, name)
                logger.info(
                    "AppScorecard criado",
                    extra={
                        "resource_name": name,
                        "namespace": namespace,
                        "score": scorecard.overall_score,
                    },
                )
            else:
                logger.error(
                    "Erro ao fazer upsert do AppScorecard",
                    extra={
                        "resource_name": name,
                        "namespace": namespace,
                        "status": exc.status,
                    },
                )
                raise

    def update_notification(
        self,
        namespace: str,
        name: str,
        severity: str,
    ) -> None:
        try:
            existing = self._custom_api.get_namespaced_custom_object(
                group=GROUP,
                version=VERSION,
                namespace=namespace,
                plural=PLURAL,
                name=name,
            )
            now = datetime.now(timezone.utc).isoformat()
            status = existing.get("status", {})
            status["notification"] = {
                "lastSentAt": now,
                "lastSeverity": severity,
            }
            existing["status"] = status
            self._custom_api.replace_namespaced_custom_object_status(
                group=GROUP,
                version=VERSION,
                namespace=namespace,
                plural=PLURAL,
                name=name,
                body=existing,
            )
        except ApiException:
            logger.warning(
                "Falha ao atualizar notification status no AppScorecard",
                extra={"resource_name": name, "namespace": namespace},
            )

    def _create(self, body: Dict[str, Any], namespace: str, name: str) -> None:
        self._custom_api.create_namespaced_custom_object(
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural=PLURAL,
            body=body,
        )

    def _update(
        self,
        existing: Dict[str, Any],
        new_body: Dict[str, Any],
        namespace: str,
        name: str,
    ) -> None:
        existing["metadata"]["labels"] = new_body["metadata"].get("labels", {})
        existing["spec"] = new_body["spec"]
        self._custom_api.replace_namespaced_custom_object(
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural=PLURAL,
            name=name,
            body=existing,
        )

        refreshed = self._custom_api.get_namespaced_custom_object(
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural=PLURAL,
            name=name,
        )
        refreshed["status"] = new_body["status"]
        self._custom_api.replace_namespaced_custom_object_status(
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural=PLURAL,
            name=name,
            body=refreshed,
        )

    def _build_body(
        self,
        scorecard: ResourceScorecard,
        deployment_body: Dict[str, Any],
        enriched: Optional[EnrichedScorecard],
        remediation_pr: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        name = scorecard.resource_name
        namespace = scorecard.resource_namespace
        meta = deployment_body.get("metadata", {})

        owner_refs = [
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "name": meta.get("name", name),
                "uid": meta.get("uid", ""),
                "blockOwnerDeletion": True,
                "controller": True,
            }
        ]

        labels: Dict[str, str] = {
            "app.kubernetes.io/managed-by": "titlis-operator",
            "titlis.io/resource-kind": scorecard.resource_kind.lower(),
        }
        if enriched:
            squad = enriched.backstage.squad
            if squad and squad != "unknown":
                labels["titlis.io/squad"] = squad
            tier = enriched.backstage.tier
            if tier:
                labels["titlis.io/tier"] = tier

        return {
            "apiVersion": f"{GROUP}/{VERSION}",
            "kind": "AppScorecard",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "ownerReferences": owner_refs,
            },
            "spec": {
                "targetRef": {
                    "apiVersion": "apps/v1",
                    "kind": scorecard.resource_kind,
                    "name": name,
                    "namespace": namespace,
                }
            },
            "status": self._build_status(scorecard, enriched, remediation_pr),
        }

    def _build_status(
        self,
        scorecard: ResourceScorecard,
        enriched: Optional[EnrichedScorecard],
        remediation_pr: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()

        if (
            scorecard.critical_issues > 0
            or scorecard.error_issues > 0
            or scorecard.overall_score < 80
        ):
            compliance = "non_compliant"
        else:
            compliance = "compliant"

        findings: List[Dict[str, Any]] = []
        for pillar_score in scorecard.pillar_scores.values():
            for result in pillar_score.validation_results:
                findings.append(
                    {
                        "ruleId": result.rule_id,
                        "ruleName": result.rule_name,
                        "pillar": result.pillar.value,
                        "passed": result.passed,
                        "severity": result.severity.value,
                        "weight": result.weight,
                        "message": result.message,
                        "actualValue": (
                            str(result.actual_value)
                            if result.actual_value is not None
                            else None
                        ),
                        "expectedValue": (
                            str(result.expected_value)
                            if result.expected_value is not None
                            else None
                        ),
                        "remediation": result.remediation,
                        "timestamp": result.timestamp.isoformat(),
                    }
                )

        pillars: Dict[str, Any] = {}
        for pillar, ps in scorecard.pillar_scores.items():
            pillars[pillar.value] = {
                "score": round(ps.score, 2),
                "passedChecks": ps.passed_checks,
                "totalChecks": ps.total_checks,
                "weightedScore": round(ps.weighted_score, 2),
            }

        status: Dict[str, Any] = {
            "overallScore": round(scorecard.overall_score, 2),
            "complianceStatus": compliance,
            "lastEvaluatedAt": scorecard.timestamp.isoformat(),
            "criticalIssues": scorecard.critical_issues,
            "errorIssues": scorecard.error_issues,
            "warningIssues": scorecard.warning_issues,
            "passedChecks": scorecard.passed_checks,
            "totalChecks": scorecard.total_checks,
            "pillars": pillars,
            "findings": findings,
            "conditions": [
                {
                    "type": "Evaluated",
                    "status": "True",
                    "lastTransitionTime": now,
                    "reason": "EvaluationSucceeded",
                    "message": (
                        f"Scorecard evaluated: {scorecard.overall_score:.1f}/100 "
                        f"({scorecard.passed_checks}/{scorecard.total_checks} checks passed)"
                    ),
                }
            ],
        }

        if enriched:
            status["backstage"] = {
                "entityRef": enriched.backstage.entity_ref,
                "owner": enriched.backstage.owner,
                "squad": enriched.backstage.squad,
                "tier": enriched.backstage.tier,
                "system": enriched.backstage.system,
                "sloTargetOverride": enriched.backstage.slo_target_override,
                "fetchedAt": enriched.backstage.fetched_at.isoformat(),
            }
            status["cost"] = {
                "monthlyCostUsd": enriched.cost.monthly_cost_usd,
                "monthlySavingsUsd": enriched.cost.monthly_savings_usd,
                "potentialSavingsUsd": enriched.cost.potential_savings_usd,
                "cpuEfficiencyPct": enriched.cost.cpu_efficiency_pct,
                "memoryEfficiencyPct": enriched.cost.memory_efficiency_pct,
                "rightsizingRecommendations": enriched.cost.rightsizing_recommendations,
            }

        if remediation_pr:
            status["remediation"] = remediation_pr

        return status
