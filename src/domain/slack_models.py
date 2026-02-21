from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, validator

class NotificationSeverity(Enum):
    
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class NotificationChannel(Enum):
    
    OPERATIONAL = "operational"
    DEBUG = "debug"
    SECURITY = "security"
    ALERTS = "alerts"

@dataclass
class SlackMessageTemplate:
    
    title: str
    color_map: Dict[NotificationSeverity, str] = field(
        default_factory=lambda: {
            NotificationSeverity.INFO: "#36a64f",
            NotificationSeverity.WARNING: "#ffcc00",
            NotificationSeverity.ERROR: "#ff3333",
            NotificationSeverity.CRITICAL: "#990000"
        }
    )
    include_timestamp: bool = True
    include_cluster_info: bool = True
    include_namespace: bool = True
    max_message_length: int = 3000

class SlackConfig(BaseModel):
    
    webhook_url: Optional[str] = Field(None, min_length=1)
    bot_token: Optional[str] = Field(None, min_length=1)
    default_channel: str = Field("#kopf-notifications", min_length=1)
    enabled: bool = True
    timeout_seconds: float = Field(10.0, gt=0)
    max_retries: int = Field(3, ge=0)
    
    # Rate limiting
    rate_limit_per_minute: int = Field(60, gt=0)
    rate_limit_per_hour: int = Field(360, gt=0)
    
    # Filtering
    enabled_severities: List[NotificationSeverity] = Field(
        default_factory=lambda: list(NotificationSeverity)
    )
    enabled_channels: List[NotificationChannel] = Field(
        default_factory=lambda: list(NotificationChannel)
    )
    
    # Templates
    message_template: SlackMessageTemplate = Field(
        default_factory=lambda: SlackMessageTemplate(title="Kopf Notification")
    )
    
    class Config:
        use_enum_values = True
        arbitrary_types_allowed = True
    
    @validator('enabled_severities', pre=True)
    def parse_enabled_severities(cls, v):
        if isinstance(v, list) and v and isinstance(v[0], str):
            return [NotificationSeverity(s) for s in v]
        return v
    
    @validator('enabled_channels', pre=True)
    def parse_enabled_channels(cls, v):
        if isinstance(v, list) and v and isinstance(v[0], str):
            return [NotificationChannel(c) for c in v]
        return v
    
    @property
    def is_configured(self) -> bool:
        
        return bool(self.webhook_url or self.bot_token)

@dataclass
class SlackNotification:
    
    title: str
    message: str
    severity: NotificationSeverity
    channel: NotificationChannel
    namespace: Optional[str] = None
    pod_name: Optional[str] = None
    additional_fields: Optional[List[Dict[str, str]]] = None
    custom_channel: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)