from typing import Dict, Any, Optional
from datetime import datetime

from src.domain.slack_models import (
    SlackNotification, 
    NotificationSeverity, 
    NotificationChannel
)
from src.application.ports.slack_port import SlackNotifierPort
from src.utils.json_logger import get_logger

logger = get_logger(__name__)


class SlackNotificationService:
    
    
    def __init__(self, notifier: Optional[SlackNotifierPort] = None):
        """
        Inicializa o serviço Slack.
        
        Args:
            notifier: Implementação do SlackNotifierPort
        """
        self.notifier = notifier
        self._initialized = False
        
        logger.info(
            "SlackNotificationService inicializado",
            extra={"has_notifier": notifier is not None}
        )
    
    async def initialize(self) -> bool:
        """
        Inicializa o serviço de forma assíncrona.
        
        Returns:
            True se inicializado com sucesso
        """
        if self._initialized or not self.notifier:
            return False
        
        try:
            await self.notifier.initialize()
            self._initialized = True
            logger.info("SlackNotificationService inicializado com sucesso")
            return True
        except Exception:
            logger.exception("Falha ao inicializar Slack service: ")
            return False
    
    async def shutdown(self) -> None:
        
        if self.notifier and self._initialized:
            await self.notifier.shutdown()
            self._initialized = False
            logger.info("SlackNotificationService finalizado")
    
    def is_enabled(self) -> bool:
        return self._initialized and self.notifier is not None
    
    async def send_notification(
        self,
        title: str,
        message: str,
        severity: NotificationSeverity = NotificationSeverity.INFO,
        channel: NotificationChannel = NotificationChannel.OPERATIONAL,
        namespace: Optional[str] = None,
        pod_name: Optional[str] = None,
        **kwargs
    ) -> bool:
        if not self.is_enabled():
            logger.debug(f"Slack desabilitado, ignorando: {title}")
            return False
        
        try:
            # Cria a notificação
            notification = SlackNotification(
                title=title,
                message=message,
                severity=severity,
                channel=channel,
                namespace=namespace,
                pod_name=pod_name,
                additional_fields=kwargs.get('additional_fields'),
                custom_channel=kwargs.get('custom_channel'),
                metadata={
                    'timestamp': datetime.utcnow().isoformat(),
                    **kwargs.get('metadata', {})
                }
            )
            
            # Envia usando o notifier
            success = await self.notifier.send_notification(notification)
            
            if success:
                logger.debug(
                    "Notificação Slack enviada com sucesso",
                    extra={
                        "title": title[:50],  # Limita tamanho do log
                        "severity": severity.value,
                        "channel": channel.value
                    }
                )
            else:
                logger.warning(
                    "Falha ao enviar notificação Slack",
                    extra={
                        "title": title[:50],
                        "severity": severity.value,
                        "channel": channel.value
                    }
                )
            
            return success
            
        except Exception:
            logger.exception("Erro ao enviar notificação Slack: ")
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
        if not self.is_enabled():
            return False
        
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
        
        # Formata título
        title = f"{kind} {event_type.title()}: {name}"
        if namespace:
            title = f"[{namespace}] {title}"
        
        # Formata mensagem
        full_message = f"*Reason:* {reason}\n*Message:* {message}"
        
        if metadata.get('uid'):
            full_message += f"\n*UID:* {metadata['uid']}"
        
        return await self.send_notification(
            title=title,
            message=full_message,
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
    
    async def send_health_check(self) -> bool:
        return await self.send_notification(
            title="🔄 Titlis Operator Health Check",
            message="Titlis Operator está UP e rodando! ✅",
            severity=NotificationSeverity.INFO,
            channel=NotificationChannel.DEBUG,
            metadata={'type': 'health_check'}
        )
    
    async def test_connection(self) -> bool:
        if not self.is_enabled():
            logger.warning("Slack não está habilitado para teste")
            return False
        
        try:
            # Primeiro verifica se o serviço está inicializado
            if not self._initialized:
                logger.warning("Slack service não inicializado")
                return False
            
            # Tenta enviar uma notificação de teste simples
            success = await self.send_notification(
                title="🔌 Teste de Conexão Slack - Scorecard",
                message="Teste de conexão realizado pelo Titlis Operator Scorecard.",
                severity=NotificationSeverity.INFO,
                channel=NotificationChannel.DEBUG,
                metadata={'type': 'connection_test', 'component': 'scorecard'}
            )
            
            if success:
                logger.info("✅ Teste de conexão Slack bem-sucedido")
            else:
                logger.warning(
                    "❌ Teste de conexão Slack falhou",
                    extra={
                        "service_enabled": self.is_enabled(),
                        "service_initialized": self._initialized,
                        "notifier_available": self.notifier is not None
                    }
                )
            
            return success
            
        except Exception:
            logger.exception(
                "Erro no teste de conexão Slack",
                extra={
                    "service_status": self.get_status()
                }
            )
            return False
    
    def get_status(self) -> Dict[str, Any]:
        return {
            'enabled': self.is_enabled(),
            'initialized': self._initialized,
            'notifier_available': self.notifier is not None,
            'service_name': 'SlackNotificationService'
        }