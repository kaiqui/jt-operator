from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class SlackSettings(BaseSettings):
    
    
    # Habilitar/desabilitar
    enabled: bool = Field(default=True, validation_alias="SLACK_ENABLED")
    
    webhook_url: Optional[SecretStr] = Field(default=None, validation_alias="SLACK_WEBHOOK_URL")
    # Credenciais OAuth do Slack App
    client_id: Optional[SecretStr] = Field(default=None, validation_alias="SLACK_CLIENT_ID")
    client_secret: Optional[SecretStr] = Field(default=None, validation_alias="SLACK_CLIENT_SECRET")
    signing_secret: Optional[SecretStr] = Field(default=None, validation_alias="SLACK_SIGNING_SECRET")
    verification_token: Optional[SecretStr] = Field(default=None, validation_alias="SLACK_VERIFICATION_TOKEN")
    
    # Bot Token (obtido via OAuth ou fornecido diretamente)
    bot_token: Optional[SecretStr] = Field(default=None, validation_alias="SLACK_BOT_TOKEN")
    
    # Configurações gerais
    default_channel: str = Field(default="#titlis-notifications", validation_alias="SLACK_DEFAULT_CHANNEL")
    secret_name: str = Field(default="titlis-slack-keys", validation_alias="SLACK_SECRET_NAME")
    
    # Rate limiting
    rate_limit_per_minute: int = Field(default=60, validation_alias="SLACK_RATE_LIMIT_PER_MINUTE")
    rate_limit_per_hour: int = Field(default=360, validation_alias="SLACK_RATE_LIMIT_PER_HOUR")
    
    # Timeout e retries
    timeout_seconds: float = Field(default=10.0, validation_alias="SLACK_TIMEOUT_SECONDS")
    max_retries: int = Field(default=3, validation_alias="SLACK_MAX_RETRIES")
    
    # Severidades habilitadas (separadas por vírgula)
    enabled_severities: str = Field(default="info,warning,error,critical", validation_alias="SLACK_ENABLED_SEVERITIES")
    
    # Canais habilitados (separadas por vírgula)
    enabled_channels: str = Field(default="operational,alerts", validation_alias="SLACK_ENABLED_CHANNELS")
    
    # Template da mensagem
    message_title: str = Field(default="Kopf Operator Notification", validation_alias="SLACK_MESSAGE_TITLE")
    include_timestamp: bool = Field(default=True, validation_alias="SLACK_INCLUDE_TIMESTAMP")
    include_cluster_info: bool = Field(default=True, validation_alias="SLACK_INCLUDE_CLUSTER_INFO")
    include_namespace: bool = Field(default=True, validation_alias="SLACK_INCLUDE_NAMESPACE")
    max_message_length: int = Field(default=3000, validation_alias="SLACK_MAX_MESSAGE_LENGTH")
    
    # Configuração por arquivo
    config_path: Optional[str] = Field(default=None, validation_alias="SLACK_CONFIG_PATH")
    
    model_config = SettingsConfigDict(
        env_prefix="SLACK_",
        case_sensitive=False,
        extra="ignore"
    )


class Settings(BaseSettings):
    

    slack: SlackSettings = Field(default_factory=SlackSettings)

    # Kubernetes
    kubernetes_namespace: str = Field(default="titlis-system", validation_alias="KUBERNETES_NAMESPACE")
    service_account_name: str = Field(default="titlis-operator", validation_alias="SERVICE_ACCOUNT_NAME")

    # Datadog
    datadog_api_key: Optional[str] = Field(default=None, validation_alias="DD_API_KEY")
    datadog_app_key: Optional[str] = Field(default=None, validation_alias="DD_APP_KEY")
    datadog_site: str = Field(default="datadoghq.com", validation_alias="DD_SITE")
    datadog_secret_name: str = Field(default="titlis-datadog-keys", validation_alias="DD_SECRET_NAME")

    # Operator behavior
    reconcile_interval_seconds: int = Field(default=300, validation_alias="RECONCILE_INTERVAL_SECONDS")
    debounce_seconds: int = Field(default=30, validation_alias="DEBOUNCE_SECONDS")
    enable_leader_election: bool = Field(default=True, validation_alias="ENABLE_LEADER_ELECTION")
    leader_election_namespace: str = Field(default="titlis", validation_alias="LEADER_ELECTION_NAMESPACE")

    # Logging
    log_level: str = Field(default="DEBUG", validation_alias="LOG_LEVEL")
    log_format: str = Field(default="json", validation_alias="LOG_FORMAT")

    # Observability
    metrics_enabled: bool = Field(default=True, validation_alias="METRICS_ENABLED")
    tracing_enabled: bool = Field(default=False, validation_alias="TRACING_ENABLED")

    # Novas flags para ativar/desativar controllers
    enable_scorecard_controller: bool = Field(default=False, validation_alias="ENABLE_SCORECARD_CONTROLLER")
    enable_slo_controller: bool = Field(default=True, validation_alias="ENABLE_SLO_CONTROLLER")

    enable_castai_monitor: bool = Field(default=True, validation_alias="ENABLE_CASTAI_MONITOR")
    castai_monitor_namespace: str = Field(default="castai-agent", validation_alias="CASTAI_MONITOR_NAMESPACE")
    castai_monitor_interval_seconds: int = Field(default=60, validation_alias="CASTAI_MONITOR_INTERVAL_SECONDS")
    castai_cluster_name: str = Field(default="develop", validation_alias="CASTAI_CLUSTER_NAME")

    # Backstage
    backstage_url: Optional[str] = Field(default=None, validation_alias="BACKSTAGE_URL")
    backstage_token: Optional[str] = Field(default=None, validation_alias="BACKSTAGE_TOKEN")
    backstage_cache_ttl_seconds: int = Field(default=300, validation_alias="BACKSTAGE_CACHE_TTL_SECONDS")

    # CAST AI Cost Enricher
    castai_api_key: Optional[str] = Field(default=None, validation_alias="CASTAI_API_KEY")
    castai_cluster_id: Optional[str] = Field(default=None, validation_alias="CASTAI_CLUSTER_ID")
    castai_cost_cache_ttl_seconds: int = Field(default=300, validation_alias="CASTAI_COST_CACHE_TTL_SECONDS")

    # Scorecard Enricher
    enable_backstage_enrichment: bool = Field(default=False, validation_alias="ENABLE_BACKSTAGE_ENRICHMENT")
    enable_castai_cost_enrichment: bool = Field(default=False, validation_alias="ENABLE_CASTAI_COST_ENRICHMENT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


# Singleton de settings (boa prática)
settings = Settings()