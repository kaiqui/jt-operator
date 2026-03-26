import kopf
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import ValidationError

from src.controllers.base import BaseController
from src.domain.models import SLOConfigSpec, SLOType
from src.bootstrap.dependencies import get_slo_service, get_slo_metrics_service
from src.domain.slack_models import NotificationSeverity, NotificationChannel
from src.application.services.slo_metrics_service import SLOAction, SLOErrorKind


class SLOController(BaseController):
    def __init__(self) -> None:
        super().__init__("slo")
        self.slo_service = get_slo_service()
        self.metrics = get_slo_metrics_service()

    async def on_slo_config_change(
        self, body: Dict[str, Any], **kwargs: Any
    ) -> Dict[str, Any]:
        context = self._get_resource_context(body)
        resource_name = context["resource_name"]
        resource_namespace = context["resource_namespace"]
        resource_uid = context.get("resource_uid")
        known_slo_id = (body.get("status") or {}).get("slo_id")
        k8s_annotations = (body.get("metadata") or {}).get("annotations") or {}
        event_type = kwargs.get("event_type", "unknown")

        if self._is_namespace_excluded(resource_namespace):
            self.logger.debug(
                f"Ignorando SLOConfig no namespace excluído: {resource_namespace}",
                extra=context,
            )
            return {
                "ignored": True,
                "reason": f"Namespace {resource_namespace} está na lista de exclusão",
            }

        self.logger.info(
            "SLOConfig alterado",
            extra={
                "resource_name": resource_name,
                "resource_namespace": resource_namespace,
                "action": event_type,
            },
        )

        result = {
            "success": False,
            "action": "create_or_update",
            "slo_id": None,
            "error": None,
            "validation_errors": [],
            "datadog_error": None,
            "spec": None,
            "event_type": event_type,
        }

        try:
            spec_dict = body.get("spec", {}) or {}
            spec = SLOConfigSpec(**spec_dict)
            result["spec"] = spec

            validation_passed = True

            if not spec.service:
                result["validation_errors"].append("spec.service é obrigatório")
                validation_passed = False

            if spec.type == SLOType.METRIC:
                has_app_framework = spec.app_framework is not None
                has_numerator_denominator = bool(spec.numerator and spec.denominator)
                has_auto_detect = spec.auto_detect_framework
                if (
                    not has_app_framework
                    and not has_numerator_denominator
                    and not has_auto_detect
                ):
                    result["validation_errors"].append(
                        "SLOs métricos requerem app_framework, numerator e denominator, ou auto_detect_framework: true"
                    )
                    validation_passed = False

            if spec.warning is not None and spec.warning <= spec.target:
                result["validation_errors"].append(
                    f"warning ({spec.warning}) deve ser MAIOR que target ({spec.target})"
                )
                validation_passed = False

            if not 0 <= spec.target <= 100:
                result["validation_errors"].append(
                    f"target ({spec.target}) deve estar entre 0 e 100"
                )
                validation_passed = False

            if spec.warning is not None and not 0 <= spec.warning <= 100:
                result["validation_errors"].append(
                    f"warning ({spec.warning}) deve estar entre 0 e 100"
                )
                validation_passed = False

            if spec.app_framework and spec.type != SLOType.METRIC:
                warning_msg = (
                    f"app_framework será ignorado para SLO do tipo {spec.type}"
                )
                self.logger.warning(warning_msg, extra=context)
                result["validation_errors"].append(warning_msg)

            if not validation_passed and result["validation_errors"]:
                self._update_status_with_error(
                    body,
                    "; ".join(
                        [
                            e
                            for e in result["validation_errors"]
                            if "será ignorado" not in e
                        ]
                    ),
                    context,
                )
                await self._send_complete_slo_notification(
                    body=body, result=result, event_type=event_type, context=context
                )

                # ── Métrica: erro de validação ──────────────────────────────
                self._emit_reconciliation_metric(
                    result=result,
                    spec=spec,
                    namespace=resource_namespace,
                    error_kind=SLOErrorKind.VALIDATION,
                )
                return result

            # Reconciliação com Datadog
            try:
                self.logger.info(
                    "Reconciliando SLO com Datadog",
                    extra={
                        "service": spec.service,
                        "type": spec.type.value,
                        "target": spec.target,
                        "namespace": resource_namespace,
                    },
                )

                if not self.slo_service:
                    raise RuntimeError("SLOService não disponível")
                reconciliation_result = (
                    self.slo_service.reconcile_slo(
                        namespace=resource_namespace,
                        service=spec.service,
                        spec=spec,
                        resource_uid=resource_uid,
                        known_slo_id=known_slo_id,
                        k8s_annotations=k8s_annotations,
                    )
                    or {}
                )

                self.logger.debug(
                    "Resultado da reconciliação do SLO",
                    extra={"result": reconciliation_result},
                )

                result.update(
                    {
                        "success": bool(reconciliation_result.get("success")),
                        "action": reconciliation_result.get("action")
                        or "create_or_update",
                        "slo_id": reconciliation_result.get("slo_id"),
                        "datadog_error": reconciliation_result.get("error"),
                        "reconciliation_result": reconciliation_result,
                    }
                )

            except Exception as dd_exc:
                err_msg = str(dd_exc)
                self.logger.exception(
                    "Erro ao criar/reconciliar SLO no Datadog",
                    extra={**context, "exception": err_msg, "service": spec.service},
                )
                result.update(
                    {
                        "success": False,
                        "action": "create_or_update",
                        "slo_id": None,
                        "datadog_error": err_msg,
                    }
                )

            status_payload = {
                "slo_id": result["slo_id"],
                "state": "ok" if result["success"] else "error",
                "last_sync": datetime.now(timezone.utc).isoformat(),
                "error": None
                if result["success"]
                else (result["datadog_error"] or "Erro desconhecido"),
                "detected_framework": result.get("reconciliation_result", {}).get(
                    "detected_framework"
                ),
            }

            if result["validation_errors"] and any(
                "será ignorado" not in e for e in result["validation_errors"]
            ):
                status_payload["error"] = "; ".join(result["validation_errors"])

            self._update_status(body, status_payload, context)

        except ValidationError as ve:
            error_msg = f"Erro de validação: {ve}"
            self.logger.error(
                "Erro de validação no SLOConfig",
                extra={
                    **context,
                    "validation_error": str(ve),
                    "validation_details": ve.errors()
                    if hasattr(ve, "errors")
                    else None,
                },
            )
            result.update(
                {"error": error_msg, "validation_errors": [f"Erro de schema: {ve}"]}
            )
            self._update_status_with_error(body, error_msg, context)

            # ── Métrica: erro de schema ──────────────────────────────────
            self._emit_reconciliation_metric(
                result=result,
                spec=result.get("spec"),
                namespace=resource_namespace,
                error_kind=SLOErrorKind.VALIDATION,
            )

        except Exception as exc:
            exc_msg = str(exc)
            self.logger.exception(
                "Erro inesperado ao processar SLOConfig",
                extra={
                    **context,
                    "exception": exc_msg,
                    "exception_type": type(exc).__name__,
                },
            )
            result.update({"error": exc_msg})
            self._update_status_with_error(body, exc_msg, context)

            # ── Métrica: erro inesperado ─────────────────────────────────
            self._emit_reconciliation_metric(
                result=result,
                spec=result.get("spec"),
                namespace=resource_namespace,
                error_kind=SLOErrorKind.UNEXPECTED,
            )

        # Notificação Slack (comportamento original mantido)
        await self._send_complete_slo_notification(
            body=body, result=result, event_type=event_type, context=context
        )

        # ── Métrica: resultado final ───────────────────────────────────────
        error_kind = SLOErrorKind.NONE
        if not result["success"]:
            if result.get("datadog_error"):
                error_kind = SLOErrorKind.DATADOG_API
            elif result.get("validation_errors"):
                error_kind = SLOErrorKind.VALIDATION
            else:
                error_kind = SLOErrorKind.UNEXPECTED

        self._emit_reconciliation_metric(
            result=result,
            spec=result.get("spec"),
            namespace=resource_namespace,
            error_kind=error_kind,
        )

        # ── Compliance / adesão ───────────────────────────────────────────
        if result.get("spec") and self.metrics:
            self.metrics.record_compliance_status(
                is_compliant=result["success"],
                slo_type=result["spec"].type.value if result.get("spec") else "unknown",
                namespace=resource_namespace,
            )

        return {
            "success": result["success"],
            "action": result["action"],
            "slo_id": result["slo_id"],
            "error": result["error"] or result["datadog_error"],
            "service": result["spec"].service if result["spec"] else None,
        }

    async def _send_complete_slo_notification(
        self,
        body: Dict[str, Any],
        result: Dict[str, Any],
        event_type: str,
        context: Dict[str, Any],
    ) -> None:
        resource_name = context["resource_name"]
        resource_namespace = context["resource_namespace"]
        spec = result.get("spec")

        # Determina severidade geral
        severity = self._determine_slo_severity(result)

        # Determina canal
        channel = self._determine_slo_channel(severity, result)

        # Título da notificação
        title = self._build_slo_notification_title(
            event_type=event_type,
            resource_name=resource_name,
            success=result["success"],
            has_validation_errors=bool(result.get("validation_errors")),
            has_datadog_error=bool(result.get("datadog_error")),
        )

        # Constrói a mensagem completa
        message = self._build_complete_slo_message(
            event_type=event_type,
            resource_name=resource_name,
            resource_namespace=resource_namespace,
            spec=spec,
            result=result,
        )

        # Campos adicionais para o Slack
        additional_fields = self._build_slo_additional_fields(
            spec=spec, result=result, resource_namespace=resource_namespace
        )

        # Envia a notificação única
        await self._send_slack_notification_safe(
            title=title,
            message=message,
            severity=severity,
            channel=channel,
            namespace=resource_namespace,
            pod_name=resource_name,
            additional_fields=additional_fields,
        )

    def _determine_slo_severity(self, result: Dict[str, Any]) -> NotificationSeverity:
        has_error = bool(result.get("error"))
        has_datadog_error = bool(result.get("datadog_error"))
        has_validation_errors = bool(result.get("validation_errors"))
        is_success = result.get("success", False)

        # 1. Erro inesperado ou de validação - CRITICAL/ERROR
        if has_error:
            return NotificationSeverity.CRITICAL

        # 2. Erro no Datadog
        if has_datadog_error:
            return NotificationSeverity.ERROR

        # 3. Erros de validação (exceto warnings)
        if has_validation_errors and any(
            "será ignorado" not in e for e in result["validation_errors"]
        ):
            return NotificationSeverity.ERROR

        # 4. Apenas warnings de validação
        if has_validation_errors:
            return NotificationSeverity.WARNING

        # 5. Sucesso
        if is_success:
            return NotificationSeverity.INFO

        # 6. Estado desconhecido
        return NotificationSeverity.WARNING

    def _determine_slo_channel(
        self, severity: NotificationSeverity, result: Dict[str, Any]
    ) -> NotificationChannel:
        action = result.get("action", "")

        # Deleções sempre vão para ALERTS
        if action == "delete":
            return NotificationChannel.ALERTS

        # Severidades altas vão para ALERTS
        if severity in [NotificationSeverity.CRITICAL, NotificationSeverity.ERROR]:
            return NotificationChannel.ALERTS

        # Para criação/atualização bem-sucedida, usa OPERATIONAL
        if result.get("success") and severity == NotificationSeverity.INFO:
            return NotificationChannel.OPERATIONAL

        # Warnings vão para OPERATIONAL
        return NotificationChannel.OPERATIONAL

    def _build_slo_notification_title(
        self,
        event_type: str,
        resource_name: str,
        success: bool,
        has_validation_errors: bool,
        has_datadog_error: bool,
    ) -> str:
        # Emoji baseado no evento
        event_emoji = {
            "create": "🆕",
            "update": "🔄",
            "delete": "🗑️",
            "unknown": "📊",
        }.get(event_type, "📊")

        # Emoji baseado no resultado
        if has_datadog_error:
            result_emoji = "🚨"
        elif has_validation_errors:
            result_emoji = "⚠️"
        elif success:
            result_emoji = "✅"
        else:
            result_emoji = "❓"

        # Texto da ação
        action_text = {
            "create": "Criado",
            "update": "Atualizado",
            "delete": "Deletado",
        }.get(event_type, "Processado")

        if has_datadog_error:
            return f"{event_emoji} {result_emoji} SLOConfig {action_text} com ERRO: {resource_name}"
        if has_validation_errors:
            return f"{event_emoji} {result_emoji} SLOConfig {action_text} com VALIDAÇÃO: {resource_name}"
        if success:
            return f"{event_emoji} {result_emoji} SLOConfig {action_text} com SUCESSO: {resource_name}"
        return f"{event_emoji} {result_emoji} SLOConfig {action_text}: {resource_name}"

    def _build_complete_slo_message(
        self,
        event_type: str,
        resource_name: str,
        resource_namespace: str,
        spec: Optional[SLOConfigSpec],
        result: Dict[str, Any],
    ) -> str:
        message = ""

        # 1. Cabeçalho com resumo executivo
        message += f"*📊 RESUMO DO SLOCONFIG*\n"
        message += f"*Recurso:* {resource_name}\n"
        message += f"*Namespace:* {resource_namespace}\n"
        message += f"*Evento:* {event_type.upper()}\n"
        message += f"*Status:* {'✅ SUCESSO' if result['success'] else '❌ FALHA'}\n"

        if spec:
            message += f"*Serviço Alvo:* {spec.service}\n"
        message += "\n"

        # 2. Configuração do SLO (se disponível)
        if spec:
            message += f"*🔧 CONFIGURAÇÃO DO SLO*\n"
            message += f"• *Serviço:* {spec.service}\n"
            message += f"• *Tipo:* {spec.type.value}\n"
            message += f"• *Target:* {spec.target}%\n"

            if spec.warning:
                message += f"• *Warning:* {spec.warning}%\n"

            if spec.timeframe:
                message += f"• *Timeframe:* {spec.timeframe.value}\n"

            if spec.description:
                # Limita o tamanho da descrição
                desc = (
                    spec.description[:100] + "..."
                    if len(spec.description) > 100
                    else spec.description
                )
                message += f"• *Descrição:* {desc}\n"

            if spec.app_framework:
                message += f"• *Framework:* {spec.app_framework.value}\n"

            if spec.tags:
                tags_text = ", ".join(spec.tags[:5])  # Limita a 5 tags
                if len(spec.tags) > 5:
                    tags_text += f" e mais {len(spec.tags) - 5}"
                message += f"• *Tags:* {tags_text}\n"

            message += "\n"

        # 3. Resultado da Operação
        message += f"*🔄 RESULTADO DA OPERAÇÃO*\n"

        action = result.get("action", "create_or_update")
        action_text = {
            "created": "criado",
            "updated": "atualizado",
            "deleted": "deletado",
            "noop": "não modificado (já sincronizado)",
        }.get(action, action)

        if result["success"]:
            message += f"✅ *SLO {action_text} com sucesso*\n"

            if result.get("slo_id"):
                message += f"• *SLO ID:* {result['slo_id']}\n"

            if action == "noop":
                message += f"• *Status:* Já sincronizado com Datadog\n"
            else:
                message += f"• *Status:* Sincronizado com Datadog\n"

            message += f"• *Ação executada:* {action_text}\n"

        message += "\n"

        # 4. Problemas Encontrados (se houver)
        has_validation_errors = bool(result.get("validation_errors"))
        has_datadog_error = bool(result.get("datadog_error"))
        has_error = bool(result.get("error"))

        if has_error or has_datadog_error or has_validation_errors:
            message += f"*🚨 PROBLEMAS ENCONTRADOS*\n"

            if has_error:
                message += f"🔴 *Erro Inesperado:*\n"
                error_msg = result["error"]
                if len(error_msg) > 300:
                    error_msg = error_msg[:297] + "..."
                message += f"  • {error_msg}\n\n"

            if has_datadog_error:
                message += f"❌ *Erro no Datadog:*\n"
                error_msg = result["datadog_error"]
                if len(error_msg) > 300:
                    error_msg = error_msg[:297] + "..."
                message += f"  • {error_msg}\n\n"

            if has_validation_errors:
                validation_errors = result["validation_errors"]

                # Separa warnings de erros
                warnings = [e for e in validation_errors if "será ignorado" in e]
                errors = [e for e in validation_errors if "será ignorado" not in e]

                if errors:
                    message += f"❌ *Erros de Validação:*\n"
                    for error in errors[:3]:  # Limita a 3 erros
                        message += f"  • {error}\n"
                    if len(errors) > 3:
                        message += f"  • ... e mais {len(errors) - 3} erros\n"
                    message += "\n"

                if warnings:
                    message += f"⚠️ *Avisos de Validação:*\n"
                    for warning in warnings[:2]:  # Limita a 2 warnings
                        message += f"  • {warning}\n"
                    if len(warnings) > 2:
                        message += f"  • ... e mais {len(warnings) - 2} avisos\n"
                    message += "\n"

        # 5. Recomendações e Próximos Passos
        message += f"*💡 RECOMENDAÇÕES E PRÓXIMOS PASSOS*\n"

        if result["success"]:
            if action == "created":
                message += f"1. ✅ SLO criado com sucesso\n"
                message += f"2. 📊 Monitorar métricas no Datadog\n"
                message += f"3. 🔄 Revisar periodicamente (mensal)\n"
            elif action == "updated":
                message += f"1. ✅ SLO atualizado com sucesso\n"
                message += f"2. 📈 Verificar impacto nas métricas\n"
                message += f"3. 🔄 Monitorar por 24h após alteração\n"
            elif action == "noop":
                message += f"1. ✅ SLO já está sincronizado\n"
                message += f"2. 📊 Continuar monitoramento regular\n"
                message += f"3. 🔄 Revisar em próxima janela de manutenção\n"
        elif has_datadog_error:
            message += f"1. 🔍 Verificar conexão com Datadog\n"
            message += f"2. 📋 Revisar configuração do SLO\n"
            message += f"3. 🛠️ Corrigir erro e reaplicar\n"
            message += f"4. 📞 Contatar suporte Datadog se necessário\n"
        elif has_validation_errors:
            message += f"1. 📝 Corrigir erros de validação listados\n"
            message += f"2. ✅ Reaplicar SLOConfig após correções\n"
            message += f"3. 🔄 Validar especificação antes de aplicar\n"
        else:
            message += f"1. 🔍 Investigar causa do problema\n"
            message += f"2. 📋 Revisar logs do operador\n"
            message += f"3. 🛠️ Corrigir e tentar novamente\n"

        # 6. Informações de Suporte
        message += f"\n*📞 SUPORTE E INFORMAÇÕES*\n"
        message += f"• *Logs do operador:* `kubectl logs -n titlis-system -l app=titlis-operator`\n"

        if result.get("slo_id"):
            message += f"• *Link Datadog:* `https://us5.datadoghq.com/slo?slo_id={result['slo_id']}`\n"

        message += f"• *Documentação:* Confluence/SLO Management\n"
        message += f"• *Slack:* #sre-platform\n"
        message += f"• *Timestamp:* {datetime.now(timezone.utc).isoformat()}\n"

        return message

    def _build_slo_additional_fields(
        self,
        spec: Optional[SLOConfigSpec],
        result: Dict[str, Any],
        resource_namespace: str,
    ) -> List[Dict[str, str]]:
        fields: List[Dict[str, str]] = [
            {"title": "Namespace", "value": resource_namespace, "short": "true"},
            {
                "title": "Status",
                "value": "Sucesso" if result["success"] else "Falha",
                "short": "true",
            },
            {
                "title": "Ação",
                "value": result.get("action", "unknown"),
                "short": "true",
            },
        ]

        if spec:
            fields.append({"title": "Serviço", "value": spec.service, "short": "true"})
            fields.append({"title": "Tipo", "value": spec.type.value, "short": "true"})
            fields.append(
                {"title": "Target", "value": f"{spec.target}%", "short": "true"}
            )

        if result.get("slo_id"):
            fields.append(
                {"title": "SLO ID", "value": str(result["slo_id"]), "short": "true"}
            )

        # Adiciona contagem de erros se houver
        validation_errors = result.get("validation_errors", [])
        if validation_errors:
            # Conta apenas erros (não warnings)
            errors = [e for e in validation_errors if "será ignorado" not in e]
            warnings = [e for e in validation_errors if "será ignorado" in e]

            if errors:
                fields.append(
                    {
                        "title": "Erros Validação",
                        "value": str(len(errors)),
                        "short": "true",
                    }
                )
            if warnings:
                fields.append(
                    {"title": "Warnings", "value": str(len(warnings)), "short": "true"}
                )

        if result.get("datadog_error"):
            fields.append({"title": "Erro Datadog", "value": "Sim", "short": "true"})

        # Adiciona tags se disponíveis
        if spec and spec.tags:
            tags_text = ", ".join(spec.tags[:3])  # Limita a 3 tags
            if len(spec.tags) > 3:
                tags_text += f" (+{len(spec.tags)-3})"
            fields.append({"title": "Tags", "value": tags_text, "short": "false"})

        return fields

    def _update_status_with_error(
        self, body: Dict[str, Any], error_msg: str, context: Dict[str, Any]
    ) -> None:
        fallback_status = {
            "slo_id": None,
            "state": "error",
            "last_sync": datetime.now(timezone.utc).isoformat(),
            "error": error_msg,
        }
        try:
            self._update_status(body, fallback_status, context)
        except Exception:
            self.logger.exception(
                "Falha ao atualizar status com erro",
                extra={**context, "error_message": error_msg},
            )

    def _emit_reconciliation_metric(
        self,
        *,
        result: Dict[str, Any],
        spec: Any,
        namespace: str,
        error_kind: SLOErrorKind,
    ) -> None:
        if not self.metrics:
            return

        # Converte action string → SLOAction enum (fail-safe)
        action_str = (result.get("action") or "unknown").lower()
        try:
            action = SLOAction(action_str)
        except ValueError:
            action = SLOAction.UNKNOWN

        slo_type = "unknown"
        if spec and hasattr(spec, "type"):
            slo_type = spec.type.value

        self.metrics.record_reconciliation(
            success=bool(result.get("success")),
            action=action,
            slo_type=slo_type,
            namespace=namespace,
            error_kind=error_kind,
        )


# Instância global do controller — inicializada de forma lazy
_slo_controller: Optional[SLOController] = None


def _get_slo_controller() -> SLOController:
    global _slo_controller
    if _slo_controller is None:
        _slo_controller = SLOController()
    return _slo_controller


@kopf.on.create("titlis.io", "v1", "sloconfigs")  # type: ignore[arg-type]
async def on_slo_create(body: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    return await _get_slo_controller().on_slo_config_change(
        body, event_type="create", **kwargs
    )


@kopf.on.update("titlis.io", "v1", "sloconfigs")  # type: ignore[arg-type]
async def on_slo_update(body: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    return await _get_slo_controller().on_slo_config_change(
        body, event_type="update", **kwargs
    )


@kopf.on.delete("titlis.io", "v1", "sloconfigs")  # type: ignore[arg-type]
async def on_slo_delete(body: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    slo_controller = _get_slo_controller()
    context = slo_controller._get_resource_context(body)
    resource_name = context["resource_name"]
    resource_namespace = context["resource_namespace"]

    # Para deleção, mantemos uma notificação simples mas completa
    await slo_controller._send_slack_notification_safe(
        title=f"🗑️ SLOConfig Deletado: {resource_name}",
        message=(
            f"*🗑️ SLOCONFIG DELETADO*\n\n"
            f"*Recurso:* {resource_name}\n"
            f"*Namespace:* {resource_namespace}\n"
            f"*Evento:* DELETE\n"
            f"*Status:* REMOVIDO\n\n"
            f"*🔍 DETALHES DA OPERAÇÃO:*\n"
            f"• SLOConfig foi removido do cluster Kubernetes\n"
            f"• O SLO correspondente no Datadog NÃO foi deletado\n"
            f"• Monitoramento continua ativo no Datadog\n\n"
            f"*💡 OBSERVAÇÕES:*\n"
            f"1. Para deletar o SLO do Datadog, use a UI ou API do Datadog\n"
            f"2. O operador Titlis não deleta SLOs automaticamente\n"
            f"3. Considere manter o SLO se ainda for necessário\n\n"
            f"*📞 SUPORTE:*\n"
            f"• Slack: #sre-platform\n"
            f"• Timestamp: {datetime.now(timezone.utc).isoformat()}"
        ),
        severity=NotificationSeverity.WARNING,
        channel=NotificationChannel.ALERTS,
        namespace=resource_namespace,
        pod_name=resource_name,
        additional_fields=[
            {"title": "Namespace", "value": resource_namespace, "short": True},
            {"title": "Evento", "value": "DELETE", "short": True},
            {"title": "Status", "value": "Removido", "short": True},
            {"title": "Observação", "value": "SLO Datadog mantido", "short": False},
        ],
    )

    slo_controller.logger.warning(
        "SLOConfig deletado",
        extra={
            "resource_name": resource_name,
            "resource_namespace": resource_namespace,
        },
    )

    return {"deleted": True, "namespace": resource_namespace, "name": resource_name}
