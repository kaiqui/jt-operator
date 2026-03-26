import kopf
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from src.controllers.base import BaseController
from src.bootstrap.dependencies import (
    get_remediation_service,
    get_remediation_writer,
    get_scorecard_service,
    get_appscorecard_writer,
    get_titlis_api_client,
)
from src.domain.github_models import RemediationIssue, RemediationRequest
from src.domain.slack_models import NotificationChannel, NotificationSeverity
from src.domain.models import ResourceScorecard
from src.application.services.remediation_service import REMEDIABLE_RULE_IDS
from src.application.services.namespace_notification_buffer import (
    NamespaceNotificationBuffer,
)
from src.settings import settings


class ScorecardController(BaseController):
    def __init__(self) -> None:
        super().__init__("scorecard")
        self.scorecard_service = get_scorecard_service()
        self.remediation_service = get_remediation_service()
        self.remediation_writer = get_remediation_writer()
        self.appscorecard_writer = get_appscorecard_writer()
        self._notification_buffer = NamespaceNotificationBuffer(
            digest_interval_minutes=15
        )
        self.logger.info(
            "ScorecardController inicializado",
            extra={
                "scorecard_service_available": self.scorecard_service is not None,
                "slack_service_available": self.slack_service is not None,
                "remediation_service_available": self.remediation_service is not None,
                "appscorecard_writer_available": self.appscorecard_writer is not None,
            },
        )

    async def on_resource_event(
        self, body: Dict[str, Any], **kwargs: Any
    ) -> Dict[str, Any]:
        ctx = self._get_resource_context(body)
        event_type = kwargs.get("event_type", "unknown")
        namespace = ctx["resource_namespace"]

        if self._is_namespace_excluded(namespace):
            self.logger.debug(
                f"Ignorando Deployment no namespace excluído: {namespace}",
                extra=ctx,
            )
            return {
                "ignored": True,
                "reason": f"Namespace {namespace} está na lista de exclusão",
            }

        self.logger.info(f"Deployment {event_type}", extra=ctx)

        try:
            if not self.scorecard_service:
                return {"evaluated": False, "error": "ScorecardService não disponível"}
            scorecard = self.scorecard_service.evaluate_resource(
                ctx["resource_namespace"],
                ctx["resource_name"],
                ctx["resource_kind"],
            )

            self.logger.info(
                "Scorecard avaliado",
                extra={
                    **ctx,
                    "overall_score": scorecard.overall_score,
                    "critical_issues": scorecard.critical_issues,
                    "error_issues": scorecard.error_issues,
                    "warning_issues": scorecard.warning_issues,
                    "passed_checks": scorecard.passed_checks,
                    "total_checks": scorecard.total_checks,
                },
            )

            remediation_pr_meta: Optional[Dict[str, Any]] = None

            if self.remediation_service:
                remediation_pr_meta = await self._maybe_create_remediation_pr(
                    scorecard, ctx, body
                )
                if remediation_pr_meta and self.remediation_writer:
                    self._record_remediation(remediation_pr_meta, ctx, body)

            if self.appscorecard_writer:
                try:
                    self.appscorecard_writer.upsert(
                        scorecard=scorecard,
                        deployment_body=body,
                        remediation_pr=remediation_pr_meta,
                    )
                except Exception:
                    self.logger.exception(
                        "Falha ao escrever AppScorecard CRD",
                        extra=ctx,
                    )

            should_notify = self.scorecard_service.should_notify(scorecard)
            if should_notify:
                to_send = self._notification_buffer.add_and_maybe_flush(scorecard)
                if to_send is not None:
                    await self._send_namespace_digest(namespace, to_send)
            titlis_client = get_titlis_api_client()
            if titlis_client is not None:
                workload_uid = body.get("metadata", {}).get("uid", "")
                compliance_status = (
                    "compliant" if scorecard.overall_score >= 90 else "non_compliant"
                )
                await titlis_client.send_scorecard_evaluated(
                    {
                        "workload_id": workload_uid,
                        "namespace": namespace,
                        "workload": ctx["resource_name"],
                        "cluster": settings.kubernetes_namespace,
                        "k8s_event_type": event_type,
                        "overall_score": scorecard.overall_score,
                        "compliance_status": compliance_status,
                        "total_rules": scorecard.total_checks,
                        "passed_rules": scorecard.passed_checks,
                        "failed_rules": scorecard.total_checks
                        - scorecard.passed_checks,
                        "critical_failures": scorecard.critical_issues,
                        "error_count": scorecard.error_issues,
                        "warning_count": scorecard.warning_issues,
                        "scorecard_version": 1,
                        "pillar_scores": [
                            {
                                "pillar": ps.pillar.value,
                                "score": ps.score,
                                "passed_checks": ps.passed_checks,
                                "failed_checks": ps.total_checks - ps.passed_checks,
                                "weighted_score": ps.weighted_score,
                            }
                            for ps in scorecard.pillar_scores.values()
                        ],
                        "evaluated_at": scorecard.timestamp.isoformat(),
                    }
                )

            return {
                "evaluated": True,
                "resource_name": ctx["resource_name"],
                "resource_namespace": ctx["resource_namespace"],
                "overall_score": scorecard.overall_score,
                "critical_issues": scorecard.critical_issues,
                "error_issues": scorecard.error_issues,
                "warning_issues": scorecard.warning_issues,
                "should_notify": should_notify,
            }

        except Exception:
            self.logger.exception("Erro ao processar Deployment", extra=ctx)
            return {"evaluated": False, "error": "Erro ao processar Deployment"}

    async def _maybe_create_remediation_pr(
        self,
        scorecard: Any,
        ctx: Dict[str, Any],
        resource_body: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        remediable_issues: List[RemediationIssue] = []

        for pillar_score in scorecard.pillar_scores.values():
            for validation in pillar_score.validation_results:
                if not validation.passed and validation.rule_id in REMEDIABLE_RULE_IDS:
                    remediable_issues.append(
                        RemediationIssue(
                            rule_id=validation.rule_id,
                            rule_name=validation.rule_name,
                            description=validation.message,
                            remediation=validation.remediation or "",
                        )
                    )

        if not remediable_issues:
            return None

        request = RemediationRequest(
            resource_name=scorecard.resource_name,
            namespace=scorecard.resource_namespace,
            resource_kind=scorecard.resource_kind,
            issues=remediable_issues,
            resource_body=resource_body,
            base_branch=settings.github.base_branch,
        )

        self.logger.info(
            "Disparando remediacao automatica",
            extra={**ctx, "remediable_issues": [i.rule_id for i in remediable_issues]},
        )

        result = await self.remediation_service.create_remediation_pr(request)

        if result.success and result.pull_request:
            pr = result.pull_request
            self.logger.info(
                "PR de remediacao criado com sucesso",
                extra={
                    **ctx,
                    "pr_number": pr.number,
                    "pr_url": pr.url,
                    "branch": result.branch_name,
                },
            )
            return {
                "prNumber": pr.number,
                "prUrl": pr.url,
                "prBranch": result.branch_name,
                "status": "open",
                "createdAt": pr.created_at.isoformat(),
                "issuesFixed": [i.rule_id for i in remediable_issues],
            }
        self.logger.warning(
            "Falha na remediacao automatica",
            extra={**ctx, "error": result.error},
        )
        return None

    def _record_remediation(
        self,
        pr_meta: Dict[str, Any],
        ctx: Dict[str, Any],
        deployment_body: Dict[str, Any],
    ) -> None:
        try:
            if not self.remediation_writer:
                return
            meta = deployment_body.get("metadata", {})
            issues = [
                {"ruleId": rule_id, "ruleName": rule_id}
                for rule_id in pr_meta.get("issuesFixed", [])
            ]
            self.remediation_writer.record(
                namespace=ctx["resource_namespace"],
                deployment_name=ctx["resource_name"],
                deployment_uid=meta.get("uid", ""),
                pr_meta=pr_meta,
                issues=issues,
            )
        except Exception:
            self.logger.exception(
                "Falha ao registrar AppRemediation CRD",
                extra=ctx,
            )

    async def _send_namespace_digest(
        self,
        namespace: str,
        scorecards: List[ResourceScorecard],
    ) -> None:
        if not self.slack_service or not scorecards:
            return

        title, message, severity = self._format_namespace_digest(namespace, scorecards)

        success = await self._send_slack_notification_safe(
            title=title,
            message=message,
            severity=severity,
            channel=NotificationChannel.ALERTS,
            namespace=namespace,
            pod_name=None,
        )

        if success and self.appscorecard_writer:
            for sc in scorecards:
                try:
                    self.appscorecard_writer.update_notification(
                        namespace=sc.resource_namespace,
                        name=sc.resource_name,
                        severity=severity.value,
                    )
                except Exception:
                    pass

        if success:
            self.logger.info(
                "Namespace digest enviado",
                extra={"namespace": namespace, "apps_count": len(scorecards)},
            )
        else:
            self.logger.warning(
                "Falha ao enviar namespace digest",
                extra={"namespace": namespace},
            )

    def _format_namespace_digest(
        self,
        namespace: str,
        scorecards: List[ResourceScorecard],
    ) -> tuple[str, str, Any]:
        sorted_sc = sorted(
            scorecards,
            key=lambda s: (
                -s.critical_issues,
                -s.error_issues,
                s.overall_score,
            ),
        )

        total = len(sorted_sc)
        total_critical = sum(s.critical_issues for s in sorted_sc)
        total_errors = sum(s.error_issues for s in sorted_sc)
        total_warnings = sum(s.warning_issues for s in sorted_sc)

        if total_critical > 0 or any(s.overall_score < 70 for s in sorted_sc):
            severity = NotificationSeverity.CRITICAL
            header_emoji = "🔴"
        elif total_errors > 0 or any(s.overall_score < 80 for s in sorted_sc):
            severity = NotificationSeverity.ERROR
            header_emoji = "🟠"
        elif total_warnings > 0 or any(s.overall_score < 90 for s in sorted_sc):
            severity = NotificationSeverity.WARNING
            header_emoji = "🟡"
        else:
            severity = NotificationSeverity.INFO
            header_emoji = "🟢"

        title = f"{header_emoji} Scorecard Digest — namespace: {namespace}"

        summary_parts = [
            f"*{total}* app{'s' if total != 1 else ''} avaliado{'s' if total != 1 else ''}"
        ]
        if total_critical:
            summary_parts.append(
                f"🔴 {total_critical} crítico{'s' if total_critical != 1 else ''}"
            )
        if total_errors:
            summary_parts.append(
                f"❌ {total_errors} erro{'s' if total_errors != 1 else ''}"
            )
        if total_warnings:
            summary_parts.append(
                f"⚠️ {total_warnings} warning{'s' if total_warnings != 1 else ''}"
            )
        summary_line = " | ".join(summary_parts)

        lines = [summary_line, ""]

        for sc in sorted_sc:
            emoji = self._score_emoji(sc.overall_score)
            name_padded = sc.resource_name[:35].ljust(35)
            score_str = f"{sc.overall_score:5.1f}/100"
            issue_parts = []
            if sc.critical_issues:
                issue_parts.append(f"🔴 {sc.critical_issues} crít.")
            if sc.error_issues:
                issue_parts.append(f"❌ {sc.error_issues} erros")
            if sc.warning_issues:
                issue_parts.append(f"⚠️ {sc.warning_issues} warn.")
            if not issue_parts:
                issue_parts.append("✅ ok")
            issues_str = "  ".join(issue_parts)
            lines.append(f"{emoji} `{name_padded}` {score_str}  {issues_str}")

        top_issues: List[str] = []
        for sc in sorted_sc:
            for ps in sc.pillar_scores.values():
                for v in ps.validation_results:
                    if not v.passed and v.severity.value in ("critical", "error"):
                        top_issues.append(
                            f"• *{sc.resource_name}*: [{v.rule_id}] {v.rule_name}"
                        )
            if len(top_issues) >= 5:
                break

        if top_issues:
            lines += ["", "*Issues críticos/errors:*"] + top_issues[:5]

        lines += [
            "",
            f"`kubectl get appscorecard -n {namespace}`",
        ]

        message = "\n".join(lines)
        if len(message) > 3000:
            message = message[:2997] + "..."

        return title, message, severity

    def _score_emoji(self, score: float) -> str:
        if score >= 90:
            return "🟢"
        if score >= 80:
            return "🟡"
        if score >= 70:
            return "🟠"
        return "🔴"

    def _get_score_status(self, score: float) -> str:
        if score >= 90:
            return "Excelente"
        if score >= 80:
            return "Bom"
        if score >= 70:
            return "Regular"
        if score >= 50:
            return "Insatisfatório"
        return "Crítico"


scorecard_controller = ScorecardController()


@kopf.on.resume("apps", "v1", "deployments")  # type: ignore[arg-type]
async def on_deployment_resume(body: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    return await scorecard_controller.on_resource_event(
        body, event_type="resume", **kwargs
    )


@kopf.on.create("apps", "v1", "deployments")  # type: ignore[arg-type]
async def on_deployment_create(body: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    return await scorecard_controller.on_resource_event(
        body, event_type="create", **kwargs
    )


@kopf.on.update("apps", "v1", "deployments")  # type: ignore[arg-type]
async def on_deployment_update(body: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    return await scorecard_controller.on_resource_event(
        body, event_type="update", **kwargs
    )


@kopf.on.delete("apps", "v1", "deployments")  # type: ignore[arg-type]
async def on_deployment_delete(body: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    ctx = scorecard_controller._get_resource_context(body)

    await scorecard_controller._send_slack_notification_safe(
        title=f"🗑️ Deployment Deletado: {ctx['resource_name']}",
        message=(
            f"*Deployment deletado do cluster*\n\n"
            f"*Aplicação:* {ctx['resource_name']}\n"
            f"*Namespace:* {ctx['resource_namespace']}\n"
            f"*Timestamp:* {datetime.now(timezone.utc).isoformat()}\n\n"
            f"*Observação:* Este deployment não está mais sendo monitorado pelo Titlis Operator."
        ),
        severity=NotificationSeverity.WARNING,
        channel=NotificationChannel.ALERTS,
        namespace=ctx["resource_namespace"],
    )

    scorecard_controller.logger.warning("Deployment deleted", extra=ctx)
    return {"deleted": True}
