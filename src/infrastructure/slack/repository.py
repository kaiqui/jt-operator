"""
Implementação simplificada do repositório Slack usando SDK oficial.
"""
import asyncio
from typing import Optional, Dict, Any, List

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.webhook.async_client import AsyncWebhookClient
from slack_sdk.errors import SlackApiError

from src.domain.slack_models import (
    SlackNotification, 
    NotificationSeverity, 
    NotificationChannel,
    SlackMessageTemplate
)
from src.application.ports.slack_port import SlackNotifierPort
from src.infrastructure.slack.message_builder import SlackMessageBuilder
from src.utils.json_logger import get_logger

logger = get_logger(__name__)


class SlackRepository(SlackNotifierPort):
    
    def __init__(
        self,
        bot_token: Optional[str] = None,
        webhook_url: Optional[str] = None,
        default_channel: str = "#titlis-notifications",
        enabled: bool = True,
        timeout_seconds: float = 10.0,
        rate_limit_per_minute: int = 60,
        enabled_severities: List[NotificationSeverity] = None,
        enabled_channels: List[NotificationChannel] = None,
        message_template: Optional[SlackMessageTemplate] = None,
        operator_name: str = "titlis-operator",
    ):
        self.bot_token = bot_token
        self.webhook_url = webhook_url
        self.default_channel = default_channel
        self.enabled = enabled
        self.timeout = int(timeout_seconds)
        self.operator_name = operator_name
        
        # Filtros
        self.enabled_severities = enabled_severities or list(NotificationSeverity)
        self.enabled_channels = enabled_channels or list(NotificationChannel)
        
        # Template
        self.message_template = message_template or SlackMessageTemplate(
            title="Titlis Operator Notification"
        )
        
        # Clientes
        self._bot_client: Optional[AsyncWebClient] = None
        self._webhook_client: Optional[AsyncWebhookClient] = None
        self._initialized = False
        
        # Contador de rate limiting
        self._message_count = 0
        self._rate_limit = rate_limit_per_minute
        
        logger.info(
            "SlackRepository inicializado",
            extra={
                "enabled": enabled,
                "has_bot_token": bool(bot_token),
                "has_webhook": bool(webhook_url),
                "default_channel": default_channel,
                "rate_limit": rate_limit_per_minute
            }
        )
    
    async def initialize(self) -> None:
        
        if not self.enabled or self._initialized:
            return
        
        try:
            if self.bot_token:
                self._bot_client = AsyncWebClient(
                    token=self.bot_token,
                    timeout=self.timeout
                )
                logger.debug("Cliente bot do Slack inicializado")
            
            if self.webhook_url:
                self._webhook_client = AsyncWebhookClient(
                    url=self.webhook_url,
                    timeout=self.timeout
                )
                logger.debug("Cliente webhook do Slack inicializado")
            
            self._initialized = True
            logger.info("SlackRepository inicializado com sucesso")
            
        except Exception:
            logger.exception("Falha ao inicializar SlackRepository: ")
            raise
    
    async def shutdown(self) -> None:
        
        # O SDK do Slack não requer shutdown explícito
        self._bot_client = None
        self._webhook_client = None
        self._initialized = False
        logger.info("SlackRepository finalizado")
    
    def _should_send(self, notification: SlackNotification) -> bool:
        if not self.enabled or not self._initialized:
            return False
        
        # Verifica severidade
        if notification.severity not in self.enabled_severities:
            logger.debug(
                f"Severidade {notification.severity.value} não habilitada",
                extra={"severity": notification.severity.value}
            )
            return False
        
        # Verifica canal
        if notification.channel not in self.enabled_channels:
            logger.debug(
                f"Canal {notification.channel.value} não habilitado",
                extra={"channel": notification.channel.value}
            )
            return False
        
        # Verifica rate limiting
        if self._message_count >= self._rate_limit:
            logger.warning("Limite de taxa excedido, ignorando notificação")
            return False
        
        return True
    
    async def send_notification(self, notification: SlackNotification) -> bool:
        # Verifica se deve enviar
        if not self._should_send(notification):
            logger.error(
                "Notificação não enviada: verificação falhou",
                extra={
                    "title": notification.title,
                    "severity": notification.severity.value,
                    "channel": notification.channel.value,
                    "enabled": self.enabled,
                    "initialized": self._initialized
                }
            )
            return False
        
        # Incrementa contador (simples rate limiting)
        self._message_count += 1
        
        # Reseta contador a cada minuto (simplificado)
        if self._message_count >= self._rate_limit:
            asyncio.create_task(self._reset_message_count())
        
        try:
            # Constrói os blocos da mensagem
            blocks = SlackMessageBuilder.create_blocks(
                title=notification.title,
                message=notification.message,
                severity=notification.severity,
                template=self.message_template,
                metadata={
                    "namespace": notification.namespace,
                    "pod_name": notification.pod_name,
                    "operator": self.operator_name,
                    **notification.metadata
                }
            )
            
            # Determina canal
            channel = notification.custom_channel or self.default_channel
            
            logger.info(
                "Preparando para enviar notificação Slack",
                extra={
                    "title": notification.title[:50],
                    "channel": channel,
                    "has_webhook": self._webhook_client is not None,
                    "has_bot_client": self._bot_client is not None,
                    "blocks_count": len(blocks)
                }
            )
            
            # Tenta enviar via webhook primeiro (mais simples)
            if self._webhook_client:
                logger.debug("Tentando enviar via webhook")
                success = await self._send_via_webhook(blocks, notification.title)
                if success:
                    logger.debug("Notificação enviada via webhook com sucesso")
                    return True
            
            # Fallback para bot token
            if self._bot_client:
                logger.debug("Tentando enviar via bot token")
                success = await self._send_via_bot(blocks, notification.title, channel)
                if success:
                    logger.debug("Notificação enviada via bot token com sucesso")
                    return True
            
            # Se chegou aqui, falhou
            logger.error(
                "Nenhum método de envio disponível",
                extra={
                    "title": notification.title,
                    "has_webhook": self._webhook_client is not None,
                    "has_bot": self._bot_client is not None
                }
            )
            return False
            
        except Exception:
            logger.exception(
                "Erro ao enviar notificação Slack",
                extra={
                    "title": notification.title,
                    "severity": notification.severity.value,
                    "channel": notification.channel.value,
                }
            )
            return False
    
    async def send_kopf_event(
        self,
        event_type: str,
        body: Dict[str, Any],
        reason: str,
        message: str,
        severity: Optional[NotificationSeverity] = None,
        **kwargs
    ) -> bool:
        # Determina severidade automática
        if severity is None:
            if event_type in ["delete", "error"]:
                severity = NotificationSeverity.WARNING
            elif event_type in ["create", "update"]:
                severity = NotificationSeverity.INFO
            else:
                severity = NotificationSeverity.INFO
        
        # Extrai informações do recurso
        metadata = body.get('metadata', {})
        name = metadata.get('name', 'Unknown')
        namespace = metadata.get('namespace')
        kind = body.get('kind', 'Resource')
        
        # Cria notificação
        notification = SlackNotification(
            title=f"{kind} {event_type.title()}: {name}",
            message=f"*Reason:* {reason}\n*Message:* {message}",
            severity=severity,
            channel=kwargs.get('channel', NotificationChannel.OPERATIONAL),
            namespace=namespace,
            pod_name=name,
            metadata={
                'event_type': event_type,
                'kind': kind,
                'reason': reason
            }
        )
        
        return await self.send_notification(notification)
    
    async def _send_via_webhook(self, blocks: list, text: str) -> bool:
        
        try:
            payload = {
                "text": text,
                "blocks": blocks,
                "username": self.operator_name,
                "icon_emoji": ":kubernetes:"
            }
            
            response = await self._webhook_client.send(**payload)
            return response.status_code == 200
            
        except Exception:
            logger.exception("Erro ao enviar via webhook: ")
            return False
    
    async def _send_via_bot(self, blocks: list, text: str, channel: str) -> bool:
        
        try:
            response = await self._bot_client.chat_postMessage(
                channel=channel,
                text=text,
                blocks=blocks,
                unfurl_links=False,
                unfurl_media=False
            )
            
            return response.get("ok", False)
            
        except SlackApiError:
            logger.exception("Erro da API Slack")
            return False
        except Exception:
            logger.exception("Erro ao enviar via bot")
            return False
    
    async def _reset_message_count(self):
        
        await asyncio.sleep(60)
        self._message_count = 0
        logger.debug("Contador de mensagens resetado")
    
    def health_check(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "initialized": self._initialized,
            "has_bot_client": self._bot_client is not None,
            "has_webhook_client": self._webhook_client is not None,
            "message_count": self._message_count,
            "rate_limit": self._rate_limit
        }