import kopf
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from src.utils.json_logger import get_logger
from src.bootstrap.dependencies import get_status_writer, get_slack_service
from src.domain.slack_models import NotificationSeverity, NotificationChannel
from src.domain.models import ComplianceReport


class BaseController:
    def __init__(self, name: str):
        self.name = name
        self.logger = get_logger(f"controller.{name}")
        self.status_writer = get_status_writer()
        self.slack_service = get_slack_service()
        self._check_enabled()
        self.logger.debug(
            f"Controller {name} inicializado",
            extra={"slack_enabled": self.slack_service is not None},
        )

    def _check_enabled(self) -> None:
        from src.settings import settings

        if self.name == "slo" and not settings.enable_slo_controller:
            raise RuntimeError(
                "SLO Controller está desabilitado via feature flag ENABLE_SLO_CONTROLLER"
            )

        if self.name == "scorecard" and not settings.enable_scorecard_controller:
            raise RuntimeError(
                "Scorecard Controller está desabilitado via feature flag ENABLE_SCORECARD_CONTROLLER"
            )

    def _get_resource_context(self, body: Dict[str, Any]) -> Dict[str, Any]:
        metadata = body.get("metadata", {})
        return {
            "resource_name": metadata.get("name", "unknown"),
            "resource_namespace": metadata.get("namespace", "default"),
            "resource_kind": body.get("kind", "Unknown"),
            "resource_uid": metadata.get("uid", "unknown"),
            "controller": self.name,
        }

    def _update_status(
        self,
        body: Dict[str, Any],
        status: Dict[str, Any],
        logger_context: Dict[str, Any],
    ) -> None:
        try:
            if "lastTransitionTime" not in status:
                status["lastTransitionTime"] = datetime.now(timezone.utc).isoformat()

            self.status_writer.update(body, status)

            self.logger.info(
                "Status atualizado com sucesso",
                extra={**logger_context, "status": status},
            )

        except Exception as exc:
            self.logger.exception(
                "Erro ao atualizar status",
                extra={**logger_context},
            )
            raise kopf.TemporaryError(
                f"Erro ao atualizar status",
                delay=60,
            ) from exc

    async def _send_slack_notification_safe(
        self,
        title: str,
        message: str,
        severity: NotificationSeverity = NotificationSeverity.INFO,
        channel: NotificationChannel = NotificationChannel.OPERATIONAL,
        namespace: Optional[str] = None,
        pod_name: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        if not self.slack_service:
            self.logger.debug(f"Slack desabilitado, ignorando: {title}")
            return False

        try:
            success = await self.slack_service.send_notification(
                title=title,
                message=message,
                severity=severity,
                channel=channel,
                namespace=namespace,
                pod_name=pod_name,
                **kwargs,
            )

            if success:
                self.logger.debug(
                    f"Notificação Slack enviada: {title[:50]}",
                    extra={
                        "severity": severity.value,
                        "channel": channel.value,
                        "namespace": namespace,
                    },
                )
            else:
                self.logger.warning(
                    f"Falha ao enviar notificação Slack: {title[:50]}",
                    extra={"severity": severity.value, "channel": channel.value},
                )

            return success

        except Exception:
            self.logger.exception(f"Erro ao enviar notificação Slack: ")
            return False

    async def _send_kopf_event_to_slack(
        self,
        event_type: str,
        body: Dict[str, Any],
        reason: str,
        message: str,
        severity: Optional[NotificationSeverity] = None,
        **kwargs: Any,
    ) -> bool:
        if not self.slack_service:
            return False

        try:
            return await self.slack_service.send_kopf_event(
                event_type=event_type,
                body=body,
                reason=reason,
                message=message,
                severity=severity,
                **kwargs,
            )
        except Exception:
            self.logger.exception(f"Erro ao enviar evento Kopf para Slack: ")
            return False

    async def _test_slack_connection(self) -> bool:
        if not self.slack_service:
            self.logger.warning("Slack não está habilitado para teste")
            return False

        try:
            success = await self.slack_service.send_health_check()

            if success:
                self.logger.info("✅ Teste de conexão Slack bem-sucedido")
            else:
                self.logger.warning("❌ Teste de conexão Slack falhou")

            return success

        except Exception:
            self.logger.exception(f"Erro no teste de conexão Slack: ")
            return False

    async def _send_compliance_issues_notification(
        self, body: Dict[str, Any], report: ComplianceReport
    ) -> None:
        ns = report.resource_namespace
        name = report.resource_name

        message = (
            f"🚨 *Deployment {name} tem {len(report.issues)} issue(s) de compliance*\n\n"
        )

        critical_issues = [i for i in report.issues if "[CRITICAL]" in i]
        error_issues = [
            i for i in report.issues if "[ERROR]" in i and "[CRITICAL]" not in i
        ]
        warning_issues = [i for i in report.issues if "[WARNING]" in i]
        other_issues = [
            i
            for i in report.issues
            if "[CRITICAL]" not in i and "[ERROR]" not in i and "[WARNING]" not in i
        ]

        if critical_issues:
            message += "*🔴 CRITICAL Issues:*\n"
            for issue in critical_issues[:5]:
                message += f"• {issue.replace('[CRITICAL] ', '')}\n"
            if len(critical_issues) > 5:
                message += f"• ... e mais {len(critical_issues) - 5} critical issues\n"
            message += "\n"

        if error_issues:
            message += "*❌ ERROR Issues:*\n"
            for issue in error_issues[:5]:
                message += f"• {issue.replace('[ERROR] ', '')}\n"
            if len(error_issues) > 5:
                message += f"• ... e mais {len(error_issues) - 5} error issues\n"
            message += "\n"

        if warning_issues:
            message += "*⚠️ WARNING Issues:*\n"
            for issue in warning_issues[:3]:
                message += f"• {issue.replace('[WARNING] ', '')}\n"
            if len(warning_issues) > 3:
                message += f"• ... e mais {len(warning_issues) - 3} warnings\n"
            message += "\n"

        if other_issues:
            message += "*📝 Other Issues:*\n"
            for issue in other_issues[:3]:
                message += f"• {issue}\n"
            if len(other_issues) > 3:
                message += f"• ... e mais {len(other_issues) - 3} issues\n"
            message += "\n"

        if report.recommendations:
            message += "*💡 Principais Recomendações:*\n"
            for rec in report.recommendations[:3]:
                if rec.startswith("**"):
                    message += f"\n{rec}\n"
                else:
                    message += f"• {rec}\n"

        await self._send_slack_notification_safe(
            title=f"🚨 Compliance Issues: {name} ({len(report.issues)} issues)",
            message=message,
            severity=NotificationSeverity.WARNING,
            channel=NotificationChannel.ALERTS,
            namespace=ns,
            pod_name=name,
            additional_fields=[
                {
                    "title": "Total Issues",
                    "value": str(len(report.issues)),
                    "short": True,
                },
                {
                    "title": "Critical",
                    "value": str(len(critical_issues)),
                    "short": True,
                },
                {"title": "Errors", "value": str(len(error_issues)), "short": True},
                {"title": "Warnings", "value": str(len(warning_issues)), "short": True},
                {"title": "Namespace", "value": ns, "short": True},
                {
                    "title": "Status",
                    "value": report.compliance_status.value,
                    "short": True,
                },
            ],
        )

    async def _send_compliance_warnings_notification(
        self, body: Dict[str, Any], report: ComplianceReport
    ) -> None:
        ns = report.resource_namespace
        name = report.resource_name

        message = f"⚠️ *Deployment {name} tem {len(report.warnings)} warning(s) de compliance*\n\n"

        if report.warnings:
            message += "*⚠️ Warnings Encontrados:*\n"
            for warning in report.warnings[:5]:
                clean_warning = warning.replace("[WARNING] ", "")
                message += f"• {clean_warning}\n"

            if len(report.warnings) > 5:
                message += f"• ... e mais {len(report.warnings) - 5} warnings\n"

        if report.recommendations:
            message += "\n*💡 Recomendações:*\n"
            for rec in report.recommendations[:2]:
                if not rec.startswith("**"):
                    message += f"• {rec}\n"

        await self._send_slack_notification_safe(
            title=f"⚠️ Compliance Warnings: {name}",
            message=message,
            severity=NotificationSeverity.WARNING,
            channel=NotificationChannel.OPERATIONAL,
            namespace=ns,
            pod_name=name,
            additional_fields=[
                {
                    "title": "Total Warnings",
                    "value": str(len(report.warnings)),
                    "short": True,
                },
                {"title": "Namespace", "value": ns, "short": True},
                {
                    "title": "Status",
                    "value": report.compliance_status.value,
                    "short": True,
                },
            ],
        )

    def _build_compliance_summary_fields(
        self, report: ComplianceReport
    ) -> List[Dict[str, str]]:
        fields: List[Dict[str, str]] = [
            {
                "title": "Status",
                "value": report.compliance_status.value,
                "short": "true",
            },
            {
                "title": "Total Checks",
                "value": str(len(report.checks)),
                "short": "true",
            },
        ]

        passed_checks = sum(1 for check in report.checks if check.get("ok", False))
        failed_checks = len(report.checks) - passed_checks

        fields.append({"title": "Passed", "value": str(passed_checks), "short": "true"})
        fields.append({"title": "Failed", "value": str(failed_checks), "short": "true"})

        if report.issues:
            fields.append(
                {"title": "Issues", "value": str(len(report.issues)), "short": "true"}
            )

        if report.warnings:
            fields.append(
                {
                    "title": "Warnings",
                    "value": str(len(report.warnings)),
                    "short": "true",
                }
            )

        return fields

    async def cleanup(self) -> None:
        pass

    def _get_excluded_namespaces(self) -> List[str]:
        excluded = [
            "kube-system",
            "kube-public",
            "kube-node-lease",
            "datadog",
            "titlis-operator",
        ]

        import os

        env_excluded = os.getenv("TITLIS_EXCLUDED_NAMESPACES", "")
        if env_excluded:
            excluded.extend([ns.strip() for ns in env_excluded.split(",")])

        return excluded

    def _is_namespace_excluded(self, namespace: str) -> bool:
        return namespace in self._get_excluded_namespaces()
