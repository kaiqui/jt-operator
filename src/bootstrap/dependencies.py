import os
import logging
from typing import Optional
from functools import lru_cache

from src.settings import settings
from src.infrastructure.kubernetes.k8s_status_writer import KubernetesStatusWriter
from src.infrastructure.kubernetes.appscorecard_writer import AppScorecardWriter
from src.infrastructure.datadog.repository import DatadogRepository
from src.infrastructure.slack.repository import SlackRepository
from src.application.services.slo_service import SLOService
from src.application.services.slack_service import SlackNotificationService
from src.domain.slack_models import NotificationSeverity, NotificationChannel, SlackMessageTemplate
from src.application.services.scorecard_service import ScorecardService
from src.application.services.slo_metrics_service import SLOMetricsService
from src.utils.json_logger import configure_logging, get_logger


logger = get_logger(__name__)

def init_logging():
    """
    Inicialização leve de logging para Kubernetes.
    """
    configure_logging(logging.INFO)

@lru_cache()
def get_backstage_enricher():
    """
    Retorna BackstageEnricher se ENABLE_BACKSTAGE_ENRICHMENT=true e BACKSTAGE_URL configurada.
    Retorna None silenciosamente caso contrário.
    """
    from src.infrastructure.backstage.enricher import BackstageEnricher

    if not settings.enable_backstage_enrichment:
        logger.info("Backstage enrichment desabilitado via feature flag")
        return None

    if not settings.backstage_url:
        logger.warning("BACKSTAGE_URL não configurada — backstage enrichment desabilitado")
        return None

    enricher = BackstageEnricher(
        backstage_url=settings.backstage_url,
        token=settings.backstage_token,
        cache_ttl_seconds=settings.backstage_cache_ttl_seconds,
    )
    logger.info(
        "BackstageEnricher inicializado",
        extra={"backstage_url": settings.backstage_url},
    )
    return enricher


@lru_cache()
def get_castai_cost_enricher():
    """
    Retorna CastaiCostEnricher se ENABLE_CASTAI_COST_ENRICHMENT=true e credenciais presentes.
    """
    from src.infrastructure.castai.cost_enricher import CastaiCostEnricher

    if not settings.enable_castai_cost_enrichment:
        logger.info("CAST AI cost enrichment desabilitado via feature flag")
        return None

    api_key = settings.castai_api_key
    cluster_id = settings.castai_cluster_id

    if not api_key or not cluster_id:
        logger.warning(
            "CASTAI_API_KEY ou CASTAI_CLUSTER_ID não configurados — cost enrichment desabilitado",
            extra={"has_api_key": bool(api_key), "has_cluster_id": bool(cluster_id)},
        )
        return None

    enricher = CastaiCostEnricher(
        api_key=api_key,
        cluster_id=cluster_id,
        cache_ttl_seconds=settings.castai_cost_cache_ttl_seconds,
    )
    logger.info(
        "CastaiCostEnricher inicializado",
        extra={"cluster_id": cluster_id},
    )
    return enricher


# Store e Enricher são singletons — um por processo
from src.application.services.scorecard_enricher import ScorecardsStore, ScorecardEnricher

_scorecard_store = ScorecardsStore()


@lru_cache()
def get_scorecard_store() -> ScorecardsStore:
    return _scorecard_store


@lru_cache()
def get_scorecard_enricher() -> ScorecardEnricher:
    return ScorecardEnricher(
        store=get_scorecard_store(),
        backstage_enricher=get_backstage_enricher(),
        castai_enricher=get_castai_cost_enricher(),
    )

lru_cache()
def get_slo_metrics_service() -> Optional[SLOMetricsService]:
    """
    Retorna instância singleton de SLOMetricsService.

    Retorna None se o SLO controller estiver desabilitado ou
    se as credenciais do Datadog não estiverem disponíveis,
    garantindo que o controller nunca falhe por causa das métricas.
    """
    if not settings.enable_slo_controller:
        logger.info("SLO controller desabilitado; SLOMetricsService não será inicializado")
        return None

    try:
        api_key = settings.datadog_api_key
        if not api_key:
            logger.warning(
                "DD_API_KEY não configurada; métricas SLO serão desabilitadas",
            )
            return None

        # APP_ENV é a variável canônica de ambiente; fallback para DD_ENV ou "unknown"
        env = (
            os.environ.get("APP_ENV")
            or os.environ.get("DD_ENV")
            or "unknown"
        )

        service = SLOMetricsService(
            api_key=api_key,
            app_key=settings.datadog_app_key,
            site=settings.datadog_site,
            env=env,
        )

        logger.info(
            "SLOMetricsService inicializado",
            extra={"env": env, "site": settings.datadog_site},
        )
        return service

    except Exception:
        logger.exception("Erro ao inicializar SLOMetricsService; métricas serão desabilitadas")
        return None

@lru_cache()
def get_status_writer():
    return KubernetesStatusWriter()


@lru_cache()
def get_appscorecard_writer() -> Optional[AppScorecardWriter]:
    """
    Returns a singleton AppScorecardWriter.
    Only active when the scorecard controller is enabled.
    """
    if not settings.enable_scorecard_controller:
        return None
    writer = AppScorecardWriter()
    logger.info("AppScorecardWriter inicializado")
    return writer

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


@lru_cache()
def get_github_repository():
    """
    Retorna instância do GitHubRepository se a integração estiver habilitada.

    Retorna None silenciosamente caso ENABLE_AUTO_REMEDIATION ou GITHUB_ENABLED
    estejam desabilitados, ou se as credenciais não estiverem configuradas.
    """
    from src.infrastructure.github.client import GitHubAPIClient
    from src.infrastructure.github.repository import GitHubRepository

    if not settings.enable_auto_remediation:
        logger.info("Auto-remediacao desabilitada via feature flag")
        return None

    if not settings.github.enabled:
        logger.info("Integracao GitHub desabilitada via GITHUB_ENABLED")
        return None

    token = (
        settings.github.token.get_secret_value() if settings.github.token else None
    )
    if not token:
        logger.warning("GITHUB_TOKEN nao configurado — auto-remediacao desabilitada")
        return None

    client = GitHubAPIClient(
        token=token,
        timeout=settings.github.timeout_seconds,
    )
    repo = GitHubRepository(client)

    logger.info(
        "GitHubRepository inicializado",
        extra={
            # repo_owner/repo_name sao extraidos por Deployment via DD_GIT_REPOSITORY_URL
            "base_branch": settings.github.base_branch,
        },
    )
    return repo


@lru_cache()
def get_remediation_service():
    """
    Retorna instância do RemediationService se a auto-remediação estiver habilitada.

    Combina GitHubRepository + SlackNotificationService + DatadogRepository
    no serviço de orquestração.
    """
    from src.application.services.remediation_service import RemediationService

    github_repo = get_github_repository()
    if not github_repo:
        return None

    slack_service = get_slack_service()

    # DatadogRepository é opcional — se não estiver configurado, as métricas
    # serão ignoradas e valores padrão serão usados para resources
    datadog_repo: Optional[DatadogRepository] = None
    try:
        datadog_repo = get_datadog_repository()
    except Exception:
        logger.warning(
            "DatadogRepository indisponivel para RemediationService — "
            "valores de resources padrao serao usados"
        )

    service = RemediationService(
        github_port=github_repo,
        slack_service=slack_service,
        datadog_repository=datadog_repo,
    )

    logger.info("RemediationService inicializado")
    return service