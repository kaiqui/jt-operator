import logging
from typing import Optional
from functools import lru_cache

from src.settings import settings
from src.infrastructure.kubernetes.k8s_status_writer import KubernetesStatusWriter
from src.infrastructure.datadog.repository import DatadogRepository
from src.infrastructure.slack.repository import SlackRepository
from src.application.services.slo_service import SLOService
from src.application.services.slack_service import SlackNotificationService
from src.domain.slack_models import NotificationSeverity, NotificationChannel, SlackMessageTemplate
from src.application.services.scorecard_service import ScorecardService
from src.utils.json_logger import configure_logging, get_logger


logger = get_logger(__name__)

def init_logging():
    """
    Inicialização leve de logging para Kubernetes.
    """
    configure_logging(logging.INFO)

@lru_cache()
def get_status_writer():
    return KubernetesStatusWriter()

@lru_cache()
def get_datadog_credentials() -> tuple:
    """
    Obtém credenciais do Datadog das variáveis de ambiente.
    """
    # Usa variáveis de ambiente diretamente
    api_key = settings.datadog_api_key
    app_key = settings.datadog_app_key
    
    if not api_key:
        logger.error(
            "API Key do Datadog não encontrada nas variáveis de ambiente",
            extra={"env_var": "DD_API_KEY"}
        )
        raise ValueError(
            "API Key do Datadog não encontrada. "
            "Configure DD_API_KEY como variável de ambiente."
        )
    
    logger.info(
        "Credenciais Datadog carregadas das variáveis de ambiente",
        extra={"has_app_key": bool(app_key)}
    )
    
    return api_key, app_key

@lru_cache()
def get_datadog_repository() -> DatadogRepository:

    api_key, app_key = get_datadog_credentials()
    
    logger.info(
        "Inicializando repositório Datadog",
        extra={
            "has_app_key": bool(app_key),
            "site": settings.datadog_site
        }
    )
    
    return DatadogRepository(
        api_key=api_key,
        app_key=app_key,
        site=settings.datadog_site
    )


# @lru_cache()
# def get_slo_service() -> SLOService:

#     if not settings.enable_slo_management:
#         logger.warning("Gerenciamento de SLOs desabilitado")
#         return None
    
#     datadog_repo = get_datadog_repository()
#     return SLOService(datadog_repo)

@lru_cache()
def get_slack_repository() -> Optional[SlackRepository]:
    """
    Retorna instância do SlackRepository usando variáveis de ambiente.
    """
    from src.settings import settings
    
    if not settings.slack.enabled:
        return None
    
    try:
        # Obtém credenciais diretamente das variáveis de ambiente
        bot_token = None
        webhook_url = None
        
        if settings.slack.bot_token:
            bot_token = settings.slack.bot_token.get_secret_value()
        
        if settings.slack.webhook_url:
            webhook_url = settings.slack.webhook_url.get_secret_value()
        
        # Se não tem nenhuma credencial, retorna None
        if not bot_token and not webhook_url:
            logger.warning(
                "Slack habilitado mas não há credenciais configuradas",
                extra={
                    "has_bot_token": bool(bot_token),
                    "has_webhook": bool(webhook_url)
                }
            )
            return None
        
        # Parse severidades habilitadas
        enabled_severities = []
        if settings.slack.enabled_severities:
            for s in settings.slack.enabled_severities.split(','):
                s = s.strip().lower()
                try:
                    enabled_severities.append(NotificationSeverity(s))
                except ValueError:
                    logger.warning(f"Severidade inválida: {s}")
        
        # Parse canais habilitados
        enabled_channels = []
        if settings.slack.enabled_channels:
            for c in settings.slack.enabled_channels.split(','):
                c = c.strip().lower()
                try:
                    enabled_channels.append(NotificationChannel(c))
                except ValueError:
                    logger.warning(f"Canal inválida: {c}")
        
        # Cria template
        message_template = SlackMessageTemplate(
            title=settings.slack.message_title,
            include_timestamp=settings.slack.include_timestamp,
            include_cluster_info=settings.slack.include_cluster_info,
            include_namespace=settings.slack.include_namespace,
            max_message_length=settings.slack.max_message_length
        )
        
        # Cria repositório
        repository = SlackRepository(
            bot_token=bot_token,
            webhook_url=webhook_url,
            default_channel=settings.slack.default_channel,
            enabled=settings.slack.enabled,
            timeout_seconds=settings.slack.timeout_seconds,
            rate_limit_per_minute=settings.slack.rate_limit_per_minute,
            enabled_severities=enabled_severities or list(NotificationSeverity),
            enabled_channels=enabled_channels or [NotificationChannel.OPERATIONAL, NotificationChannel.ALERTS],
            message_template=message_template,
            operator_name="titlis-operator"
        )
        
        logger.info(
            "SlackRepository criado",
            extra={
                "enabled": settings.slack.enabled,
                "has_bot_token": bool(bot_token),
                "has_webhook": bool(webhook_url),
                "default_channel": settings.slack.default_channel
            }
        )
        
        return repository
        
    except Exception:
        logger.exception(
            "Erro ao criar SlackRepository"
        )
        return None


@lru_cache()
def get_slack_service() -> Optional[SlackNotificationService]:
    slack_repo = get_slack_repository()
    
    if not slack_repo:
        return None
    
    service = SlackNotificationService(slack_repo)
    logger.info("SlackNotificationService criado")
    
    return service


async def initialize_slack_service():
    
    slack_service = get_slack_service()
    
    if slack_service:
        try:
            await slack_service.initialize()
            logger.info("Slack service inicializado com sucesso")
            
            # Testa a conexão
            success = await slack_service.test_connection()
            if success:
                logger.info("✅ Conexão com Slack testada com sucesso")
            else:
                logger.warning("⚠️ Teste de conexão com Slack falhou")
                
        except Exception:
            logger.exception(f"Erro ao inicializar Slack service: ")


async def shutdown_slack_service():
    slack_service = get_slack_service()
    
    if slack_service:
        await slack_service.shutdown()
        logger.info("Slack service finalizado")

@lru_cache()
def get_scorecard_service() -> Optional[ScorecardService]:
    """Retorna instância do ScorecardService apenas se o controller estiver habilitado."""
    
    # Verifica se o controller está habilitado
    if not settings.enable_scorecard_controller:
        logger.info("Scorecard controller desabilitado via feature flag")
        return None
    
    # Tenta carregar configuração de ConfigMap
    config_path = None
    
    try:        
        # Tenta ler ConfigMap de configuração
        from kubernetes import client
        core = client.CoreV1Api()
        
        try:
            cm = core.read_namespaced_config_map("titlis-scorecard-config", settings.kubernetes_namespace)
            if cm.data and "config.yaml" in cm.data:
                # Salva localmente para carregar
                import tempfile
                with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                    f.write(cm.data["config.yaml"])
                    config_path = f.name
                
                logger.info("Configuração do scorecard carregada do ConfigMap")
        except Exception:
            logger.info("Usando configuração padrão do scorecard")
    
    except Exception:
        logger.warning(f"Erro ao carregar configuração do scorecard: ")
    
    return ScorecardService(config_path=config_path)


@lru_cache()
def get_slo_service() -> Optional[SLOService]:
    """Retorna instância do SLOService apenas se o controller estiver habilitado."""
    
    if not settings.enable_slo_controller:
        logger.info("SLO controller desabilitado via feature flag")
        return None
    datadog_repo = get_datadog_repository()
    return SLOService(datadog_repo)