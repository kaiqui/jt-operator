import kopf
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from src.controllers.base import BaseController
from src.bootstrap.dependencies import get_remediation_service, get_scorecard_service
from src.domain.github_models import RemediationIssue, RemediationRequest
from src.domain.slack_models import NotificationChannel, NotificationSeverity
from src.application.services.remediation_service import REMEDIABLE_RULE_IDS
from src.settings import settings


class ScorecardController(BaseController):

    def __init__(self) -> None:
        super().__init__("scorecard")
        self.scorecard_service = get_scorecard_service()
        self.remediation_service = get_remediation_service()
        self.logger.info(
            "ScorecardController inicializado",
            extra={
                "scorecard_service_available": self.scorecard_service is not None,
                "slack_service_available": self.slack_service is not None,
                "remediation_service_available": self.remediation_service is not None,
            },
        )
    
    async def on_resource_event(self, body: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        ctx = self._get_resource_context(body)
        event_type = kwargs.get('event_type', 'unknown')

        namespace = ctx["resource_namespace"]
        
        # Verifica se o namespace está na lista de exclusão
        if self._is_namespace_excluded(namespace):
            self.logger.debug(
                f"Ignorando Deployment no namespace excluído: {namespace}",
                extra=ctx
            )
            return {"ignored": True, "reason": f"Namespace {namespace} está na lista de exclusão"}
        
        self.logger.info(f"Deployment {event_type}", extra=ctx)

        try:
            # Avalia o deployment com o scorecard
            scorecard = self.scorecard_service.evaluate_resource(
                ctx["resource_namespace"],
                ctx["resource_name"],
                ctx["resource_kind"]
            )
            
            # Log do scorecard
            self.logger.info(
                "Scorecard avaliado",
                extra={
                    **ctx,
                    "overall_score": scorecard.overall_score,
                    "critical_issues": scorecard.critical_issues,
                    "error_issues": scorecard.error_issues,
                    "warning_issues": scorecard.warning_issues,
                    "passed_checks": scorecard.passed_checks,
                    "total_checks": scorecard.total_checks
                }
            )
            
            # Remediação automática via GitHub PR (se habilitada)
            if self.remediation_service:
                await self._maybe_create_remediation_pr(scorecard, ctx, body)

            # Verifica se deve enviar notificação
            should_notify = self.scorecard_service.should_notify(scorecard)

            if should_notify:
                self.logger.info(
                    "Enviando notificação do scorecard",
                    extra={
                        **ctx,
                        "overall_score": scorecard.overall_score,
                        "notification_severity": self.scorecard_service.get_notification_severity(scorecard)
                    }
                )
                
                # Envia notificação para o Slack
                success = await self._send_scorecard_notification(body, scorecard)
                
                if success:
                    self.logger.info("Notificação do scorecard enviada com sucesso", extra=ctx)
                else:
                    self.logger.warning("Falha ao enviar notificação do scorecard", extra=ctx)
            else:
                self.logger.debug(
                    "Notificação do scorecard não necessária",
                    extra={
                        **ctx,
                        "overall_score": scorecard.overall_score,
                        "should_notify": should_notify
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
                "notification_severity": self.scorecard_service.get_notification_severity(scorecard) if should_notify else None
            }
            
        except Exception:
            self.logger.exception(
                "Erro ao processar Deployment",
                extra=ctx
            )
            return {"evaluated": False, "error": "Erro ao processar Deployment"}
    
    async def _maybe_create_remediation_pr(
        self,
        scorecard: Any,
        ctx: Dict[str, Any],
        resource_body: Dict[str, Any],
    ) -> None:
        """
        Se existirem issues remediáveis (HPA / resources), dispara a criação
        automática de uma branch + PR no GitHub e notifica o Slack.

        Só executa se o Deployment tiver a env DD_GIT_REPOSITORY_URL.
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
            return

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
            extra={
                **ctx,
                "remediable_issues": [i.rule_id for i in remediable_issues],
            },
        )

        result = await self.remediation_service.create_remediation_pr(request)

        if result.success and result.pull_request:
            self.logger.info(
                "PR de remediacao criado com sucesso",
                extra={
                    **ctx,
                    "pr_number": result.pull_request.number,
                    "pr_url": result.pull_request.url,
                    "branch": result.branch_name,
                },
            )
        else:
            self.logger.warning(
                "Falha na remediacao automatica",
                extra={**ctx, "error": result.error},
            )

    async def _send_scorecard_notification(self, body: Dict[str, Any], scorecard: Any) -> bool:
        """Envia notificação do scorecard para o Slack."""
        
        if not self.slack_service:
            self.logger.warning("Slack service não disponível para enviar notificação do scorecard")
            return False
        
        # Formata a mensagem
        title = self._format_scorecard_title(scorecard)
        message = self._format_scorecard_message(scorecard)
        severity = self._determine_scorecard_severity(scorecard)
        
        # Cria campos adicionais para o Slack
        additional_fields = self._create_scorecard_fields(scorecard)
        
        # Envia a notificação
        return await self._send_slack_notification_safe(
            title=title,
            message=message,
            severity=severity,
            channel=NotificationChannel.ALERTS,  # Usa canal de alertas para scorecards
            namespace=scorecard.resource_namespace,
            pod_name=scorecard.resource_name,
            additional_fields=additional_fields
        )
    
    def _format_scorecard_title(self, scorecard: Any) -> str:
        """Formata o título da notificação do scorecard."""
        
        emoji = "🔴"
        if scorecard.overall_score >= 90:
            emoji = "🟢"
        elif scorecard.overall_score >= 70:
            emoji = "🟡"
        elif scorecard.overall_score >= 50:
            emoji = "🟠"
        
        return f"{emoji} Scorecard: {scorecard.resource_name} ({scorecard.overall_score:.1f}/100)"
    
    def _format_scorecard_message(self, scorecard: Any) -> str:
        """Formata a mensagem do scorecard."""
        
        message = f"*📊 SCORECARD - {scorecard.resource_name}*\n"
        message += f"*Namespace:* {scorecard.resource_namespace}\n"
        message += f"*Score Geral:* {scorecard.overall_score:.1f}/100\n"
        message += f"*Status:* {self._get_score_status(scorecard.overall_score)}\n\n"
        
        # Issues
        if scorecard.critical_issues > 0:
            message += f"🔴 *Issues Críticas:* {scorecard.critical_issues}\n"
        if scorecard.error_issues > 0:
            message += f"❌ *Issues de Erro:* {scorecard.error_issues}\n"
        if scorecard.warning_issues > 0:
            message += f"⚠️ *Warnings:* {scorecard.warning_issues}\n"
        
        message += f"✅ *Checks Passados:* {scorecard.passed_checks}/{scorecard.total_checks}\n\n"
        
        # Detalhes por pilar com TODOS os itens encontrados
        message += "*🏛️ DETALHES COMPLETOS POR PILAR:*\n"
        
        # Primeiro, vamos coletar todas as validações que falharam
        all_failed_validations = []
        
        for pillar, pillar_score in scorecard.pillar_scores.items():
            pillar_emoji = self._get_pillar_emoji(pillar)
            message += f"\n{pillar_emoji} *{pillar.value.upper()}*: {pillar_score.score:.1f}/100 "
            message += f"({pillar_score.passed_checks}/{pillar_score.total_checks} checks)\n"
            
            # Lista TODAS as validações para este pilar
            for validation in pillar_score.validation_results:
                if not validation.passed:
                    # Adiciona à lista geral para resumo
                    all_failed_validations.append(validation)
                    
                    # Adiciona detalhe específico
                    severity_emoji = {
                        "critical": "🔴",
                        "error": "❌",
                        "warning": "⚠️",
                        "info": "ℹ️",
                        "optional": "🔵"
                    }.get(validation.severity.value, "⚪")
                    
                    # Limita o tamanho da mensagem para não exceder limite do Slack
                    clean_message = validation.message
                    if len(clean_message) > 150:
                        clean_message = clean_message[:147] + "..."
                    
                    message += f"  {severity_emoji} {validation.rule_name}: {clean_message}\n"
                    
                    # Adiciona valor atual e esperado se disponível
                    if validation.actual_value is not None:
                        message += f"    *Valor Atual:* {validation.actual_value}\n"
                    if validation.expected_value is not None:
                        message += f"    *Valor Esperado:* {validation.expected_value}\n"
                    
                    # Adiciona recomendação se disponível
                    if validation.remediation:
                        clean_remediation = validation.remediation
                        if len(clean_remediation) > 100:
                            clean_remediation = clean_remediation[:97] + "..."
                        message += f"    *Recomendação:* {clean_remediation}\n"
        
        # Se houver muitas validações, adiciona um resumo
        if len(all_failed_validations) > 15:
            message = message[:3000] + "\n\n... (mensagem truncada devido ao limite do Slack)"
        else:
            # Adiciona contagem resumida
            message += f"\n*📋 RESUMO DE ISSUES:*\n"
            
            critical_count = sum(1 for v in all_failed_validations if v.severity.value == "critical")
            error_count = sum(1 for v in all_failed_validations if v.severity.value == "error")
            warning_count = sum(1 for v in all_failed_validations if v.severity.value == "warning")
            
            if critical_count > 0:
                message += f"🔴 *Críticas:* {critical_count}\n"
            if error_count > 0:
                message += f"❌ *Erros:* {error_count}\n"
            if warning_count > 0:
                message += f"⚠️ *Warnings:* {warning_count}\n"
        
        # Recomendações baseadas no score
        message += f"\n*💡 RECOMENDAÇÕES PRINCIPAIS:*\n"
        
        # Primeiro, prioriza issues críticas
        critical_validations = [v for v in all_failed_validations if v.severity.value == "critical"]
        if critical_validations:
            message += "1. 🔴 *CORRIGIR ISSUES CRÍTICAS IMEDIATAMENTE:*\n"
            for i, validation in enumerate(critical_validations[:3], 1):
                message += f"   {i}. {validation.rule_name}\n"
            message += "\n"
        
        # Depois, issues de erro
        error_validations = [v for v in all_failed_validations if v.severity.value == "error"]
        if error_validations:
            message += "2. ❌ *RESOLVER ISSUES DE ERRO:*\n"
            for i, validation in enumerate(error_validations[:3], 1):
                message += f"   {i}. {validation.rule_name}\n"
            message += "\n"
        
        # Finalmente, recomendações gerais baseadas no score
        if scorecard.overall_score < 70:
            message += "3. ⚠️ *Revisar configurações de segurança e resiliência*\n"
            message += "4. 📊 *Monitorar após correções*\n"
            message += "5. 🔄 *Reavaliar em 24 horas*\n"
        elif scorecard.overall_score < 80:
            message += "3. ⚠️ *Corrigir issues de erro e warnings prioritários*\n"
            message += "4. 🛡️ *Melhorar configurações de segurança*\n"
            message += "5. 🔄 *Reavaliar após ajustes*\n"
        elif scorecard.overall_score < 90:
            message += "3. ✅ *Manter boas práticas atuais*\n"
            message += "4. ⚡ *Otimizar performance onde possível*\n"
            message += "5. 📈 *Continuar monitoramento*\n"
        else:
            message += "3. 🏆 *Excelente score! Manter configurações*\n"
            message += "4. 📊 *Continuar monitoramento regular*\n"
            message += "5. 🔄 *Revisar periodicamente*\n"
        
        # Limita o tamanho total da mensagem para o Slack (3000 caracteres)
        if len(message) > 3000:
            message = message[:2997] + "..."
        
        return message
        
    def _create_scorecard_fields(self, scorecard: Any) -> list:
        """Cria campos adicionais para a notificação do Slack."""
        
        fields = [
            {"title": "Score", "value": f"{scorecard.overall_score:.1f}/100", "short": True},
            {"title": "Status", "value": self._get_score_status(scorecard.overall_score), "short": True},
            {"title": "Críticas", "value": str(scorecard.critical_issues), "short": True},
            {"title": "Erros", "value": str(scorecard.error_issues), "short": True},
            {"title": "Warnings", "value": str(scorecard.warning_issues), "short": True},
            {"title": "Passed", "value": f"{scorecard.passed_checks}/{scorecard.total_checks}", "short": True},
        ]
        
        # Adiciona scores por pilar se disponíveis
        for pillar, pillar_score in scorecard.pillar_scores.items():
            fields.append({
                "title": pillar.value[:15],  # Limita tamanho
                "value": f"{pillar_score.score:.1f}/100",
                "short": True
            })
        
        return fields
    
    def _determine_scorecard_severity(self, scorecard: Any) -> NotificationSeverity:
        """Determina a severidade com base no score."""
        
        if scorecard.overall_score < 70 or scorecard.critical_issues > 0:
            return NotificationSeverity.CRITICAL
        elif scorecard.overall_score < 80 or scorecard.error_issues > 0:
            return NotificationSeverity.ERROR
        elif scorecard.overall_score < 90 or scorecard.warning_issues > 0:
            return NotificationSeverity.WARNING
        else:
            return NotificationSeverity.INFO
    
    def _get_score_status(self, score: float) -> str:
        """Retorna status textual do score."""
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
    
    def _get_pillar_emoji(self, pillar) -> str:
        """Retorna emoji para cada pilar."""
        emoji_map = {
            "resilience": "🛡️",
            "security": "🔐",
            "performance": "⚡",
            "cost": "💰",
            "operational": "🛠️",
            "compliance": "📋"
        }
        return emoji_map.get(pillar.value, "📊")


# Singleton global
scorecard_controller = ScorecardController()


# Handlers do Kopf
@kopf.on.create('apps', 'v1', 'deployments')
async def on_deployment_create(body, **kwargs):
    return await scorecard_controller.on_resource_event(body, event_type="create", **kwargs)


@kopf.on.update('apps', 'v1', 'deployments')
async def on_deployment_update(body, **kwargs):
    return await scorecard_controller.on_resource_event(body, event_type="update", **kwargs)


@kopf.on.delete('apps', 'v1', 'deployments')
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
        namespace=ctx["resource_namespace"]
    )
    
    scorecard_controller.logger.warning("Deployment deleted", extra=ctx)
    return {"deleted": True}

