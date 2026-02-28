import kopf
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from src.controllers.base import BaseController
from src.bootstrap.dependencies import (
    get_remediation_service,
    get_scorecard_service,
    get_appscorecard_writer,
)
from src.domain.github_models import RemediationIssue, RemediationRequest
from src.domain.slack_models import NotificationChannel, NotificationSeverity
from src.domain.models import ResourceScorecard
from src.application.services.remediation_service import REMEDIABLE_RULE_IDS
from src.application.services.namespace_notification_buffer import NamespaceNotificationBuffer
from src.settings import settings


class ScorecardController(BaseController):

    def __init__(self) -> None:
        super().__init__("scorecard")
        self.scorecard_service = get_scorecard_service()
        self.remediation_service = get_remediation_service()
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

    async def on_resource_event(self, body: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        ctx = self._get_resource_context(body)
        event_type = kwargs.get("event_type", "unknown")
        namespace = ctx["resource_namespace"]

        if self._is_namespace_excluded(namespace):
            self.logger.debug(
                f"Ignorando Deployment no namespace excluído: {namespace}",
                extra=ctx,
            )
            return {"ignored": True, "reason": f"Namespace {namespace} está na lista de exclusão"}

        self.logger.info(f"Deployment {event_type}", extra=ctx)

        try:
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

            # Persists scorecard in AppScorecard CRD (create or update).
            remediation_pr_meta: Optional[Dict[str, Any]] = None

            # Auto-remediation via GitHub PR (if enabled).
            if self.remediation_service:
                remediation_pr_meta = await self._maybe_create_remediation_pr(
                    scorecard, ctx, body
                )

            if self.appscorecard_writer:
                try:
                    self.appscorecard_writer.upsert(
                        scorecard=scorecard,
                        deployment_body=body,
                        remediation_pr=remediation_pr_meta,
                    )
                except Exception:
                    # CRD write failure must not block the operator loop.
                    self.logger.exception(
                        "Falha ao escrever AppScorecard CRD",
                        extra=ctx,
                    )

            # Buffer scorecard and maybe send namespace digest.
            should_notify = self.scorecard_service.should_notify(scorecard)
            if should_notify:
                to_send = self._notification_buffer.add_and_maybe_flush(scorecard)
                if to_send is not None:
                    await self._send_namespace_digest(namespace, to_send)

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

    # ------------------------------------------------------------------
    # Auto-remediation
    # ------------------------------------------------------------------

    async def _maybe_create_remediation_pr(
        self,
        scorecard: Any,
        ctx: Dict[str, Any],
        resource_body: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        If there are remediable issues (HPA / resources), create a GitHub PR.
        Returns PR metadata dict on success, None otherwise.
        """
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
        else:
            self.logger.warning(
                "Falha na remediacao automatica",
                extra={**ctx, "error": result.error},
            )
            return None

    # ------------------------------------------------------------------
    # Namespace digest notification (Decision 3 — C)
    # ------------------------------------------------------------------

    async def _send_namespace_digest(
        self,
        namespace: str,
        scorecards: List[ResourceScorecard],
    ) -> None:
        """Send a single Slack message summarising all apps in *namespace*."""
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
            # Update notification metadata in each AppScorecard that was included.
            for sc in scorecards:
                try:
                    self.appscorecard_writer.update_notification(
                        namespace=sc.resource_namespace,
                        name=sc.resource_name,
                        severity=severity.value,
                    )
                except Exception:
                    pass  # Non-critical

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
    ):
        """
        Build a compact digest message for all apps in a namespace.

        Returns (title, message, severity).
        """
        # Sort: worst first (critical → low score)
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

        # Overall severity of the digest
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

        # Summary line
        summary_parts = [f"*{total}* app{'s' if total != 1 else ''} avaliado{'s' if total != 1 else ''}"]
        if total_critical:
            summary_parts.append(f"🔴 {total_critical} crítico{'s' if total_critical != 1 else ''}")
        if total_errors:
            summary_parts.append(f"❌ {total_errors} erro{'s' if total_errors != 1 else ''}")
        if total_warnings:
            summary_parts.append(f"⚠️ {total_warnings} warning{'s' if total_warnings != 1 else ''}")
        summary_line = " | ".join(summary_parts)

        lines = [summary_line, ""]

        # Per-app summary rows
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

        # Top critical/error findings across all apps
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

        # kubectl hint
        lines += [
            "",
            f"`kubectl get appscorecard -n {namespace}`",
        ]

        message = "\n".join(lines)
        # Slack hard limit
        if len(message) > 3000:
            message = message[:2997] + "..."

        return title, message, severity

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _score_emoji(self, score: float) -> str:
        if score >= 90:
            return "🟢"
        elif score >= 80:
            return "🟡"
        elif score >= 70:
            return "🟠"
        else:
            return "🔴"

    def _get_score_status(self, score: float) -> str:
        if score >= 90:
            return "Excelente"
        elif score >= 80:
            return "Bom"
        elif score >= 70:
            return "Regular"
        elif score >= 50:
            return "Insatisfatório"
        else:
            return "Crítico"


# Singleton global
scorecard_controller = ScorecardController()


# Kopf handlers
@kopf.on.create("apps", "v1", "deployments")
async def on_deployment_create(body, **kwargs):
    return await scorecard_controller.on_resource_event(body, event_type="create", **kwargs)


@kopf.on.update("apps", "v1", "deployments")
async def on_deployment_update(body, **kwargs):
    return await scorecard_controller.on_resource_event(body, event_type="update", **kwargs)


@kopf.on.delete("apps", "v1", "deployments")
async def on_deployment_delete(body, **kwargs):
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
