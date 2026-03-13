from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pydantic import BaseModel, Field


class ComplianceStatus(str, Enum):
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    UNKNOWN = "unknown"
    PENDING = "pending"


class HPAProfile(str, Enum):
    LIGHT = "light"
    RIGID = "rigid"


class CriticalityLevel(str, Enum):
    STANDARD = "standard"
    HIGH = "high"


class ServiceTier(str, Enum):
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"
    TIER_4 = "tier_4"


class SLOTimeframe(str, Enum):
    SEVEN_DAYS = "7d"
    THIRTY_DAYS = "30d"
    NINETY_DAYS = "90d"


class SLOType(str, Enum):
    METRIC = "metric"
    MONITOR = "monitor"
    TIME_SLICE = "time_slice"


class SLOAppFramework(str, Enum):
    WSGI = "wsgi"
    FASTAPI = "fastapi"
    AIOHTTP = "aiohttp"


@dataclass
class KubernetesResource:
    name: str
    namespace: str
    kind: str
    api_version: str
    metadata: Dict[str, Any]
    spec: Dict[str, Any]
    status: Optional[Dict[str, Any]] = None
    annotations: Dict[str, str] = field(default_factory=dict)
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class ServiceDefinition:
    dd_service: str
    description: Optional[str] = None
    team: Optional[str] = None
    tier: Optional[ServiceTier] = None
    tags: List[str] = field(default_factory=list)
    contacts: List[Dict[str, str]] = field(default_factory=list)
    links: List[Dict[str, str]] = field(default_factory=list)
    integrations: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "v2.2"


@dataclass
class SLO:
    name: str
    service_name: str
    slo_type: SLOType
    target_threshold: float
    warning_threshold: Optional[float]
    timeframe: SLOTimeframe
    description: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    query: Optional[Dict[str, Any]] = None
    thresholds: List[Dict[str, Any]] = field(default_factory=list)
    slo_id: Optional[str] = None
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None


@dataclass
class ComplianceReport:
    resource_name: str
    resource_namespace: str
    resource_kind: str
    compliance_status: ComplianceStatus
    checks: List[Dict[str, Any]]
    last_check: datetime
    warnings: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


class SLOConfigSpec(BaseModel):
    service: str = Field(..., description="Nome do serviço")
    type: SLOType = Field(default=SLOType.METRIC, description="Tipo do SLO")
    app_framework: Optional[SLOAppFramework] = Field(
        None, description="Framework da aplicação"
    )
    target: float = Field(default=99.9, description="Target do SLO (0-100)")
    warning: Optional[float] = Field(default=99.0, description="Warning do SLO (0-100)")
    timeframe: SLOTimeframe = Field(
        default=SLOTimeframe.THIRTY_DAYS, description="Timeframe do SLO"
    )
    numerator: Optional[str] = Field(
        None, description="Query numerator para SLO métrico"
    )
    denominator: Optional[str] = Field(
        None, description="Query denominator para SLO métrico"
    )
    tags: List[str] = Field(default_factory=list, description="Tags adicionais")
    description: Optional[str] = Field(None, description="Descrição do SLO")


class SLOConfigStatus(BaseModel):
    slo_id: Optional[str] = Field(None, description="ID do SLO no Datadog")
    state: str = Field(default="pending", description="Estado do SLO")
    last_sync: Optional[datetime] = Field(None, description="Última sincronização")
    error: Optional[str] = Field(None, description="Erro se houver")
    conditions: List[Dict[str, Any]] = Field(
        default_factory=list, description="Condições do recurso"
    )


class ValidationPillar(str, Enum):
    RESILIENCE = "resilience"
    SECURITY = "security"
    COST = "cost"
    PERFORMANCE = "performance"
    OPERATIONAL = "operational"
    COMPLIANCE = "compliance"


class ValidationRuleType(str, Enum):
    BOOLEAN = "boolean"  # Passa/falha
    NUMERIC = "numeric"  # Valor numérico com thresholds
    ENUM = "enum"  # Valor em lista permitida
    REGEX = "regex"  # Valor corresponde a regex


class ValidationSeverity(str, Enum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    OPTIONAL = "optional"


@dataclass
class ValidationRule:
    id: str
    pillar: ValidationPillar
    name: str
    description: str
    rule_type: ValidationRuleType
    source: str  # Ex: "K8s API", "Custom", "External"

    # Critérios de validação
    required: bool = True
    expected_value: Optional[Any] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    allowed_values: Optional[List[Any]] = None
    regex_pattern: Optional[str] = None

    # Pontuação
    weight: float = 1.0  # Peso no cálculo do score
    severity: ValidationSeverity = ValidationSeverity.WARNING

    # Metadados
    enabled: bool = True
    applies_to: List[str] = field(
        default_factory=lambda: ["Deployment", "StatefulSet", "DaemonSet"]
    )
    framework_specific: Optional[str] = None  # Ex: "fastapi", "django", "celery"
    python_versions: Optional[List[str]] = None  # ["2.7", "3.6+"]

    # Ação recomendada
    remediation: Optional[str] = None
    documentation_url: Optional[str] = None

    # Perfil de criticidade necessário para aplicar esta regra
    # None = todos os apps; "rigid" = apenas apps críticas
    criticality_profile: Optional[str] = None


@dataclass
class ValidationResult:
    rule_id: str
    rule_name: str
    pillar: ValidationPillar
    passed: bool
    severity: ValidationSeverity
    weight: float

    # Detalhes
    message: str
    actual_value: Optional[Any] = None
    expected_value: Optional[Any] = None
    details: Dict[str, Any] = field(default_factory=dict)

    # Recomendações
    remediation: Optional[str] = None
    documentation_url: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PillarScore:
    pillar: ValidationPillar
    score: float  # 0-100
    max_score: float
    passed_checks: int
    total_checks: int
    weighted_score: float
    validation_results: List[ValidationResult]


@dataclass
class ResourceScorecard:
    resource_name: str
    resource_namespace: str
    resource_kind: str
    resource_uid: Optional[str] = None

    # Scores por pilar
    pillar_scores: Dict[ValidationPillar, PillarScore] = field(default_factory=dict)
    overall_score: float = 0.0  # 0-100

    # Estatísticas
    critical_issues: int = 0
    error_issues: int = 0
    warning_issues: int = 0
    passed_checks: int = 0
    total_checks: int = 0

    # Metadados
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    evaluated_by: str = "titlis-scorecard-service"
    criticality_level: str = CriticalityLevel.STANDARD.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_name": self.resource_name,
            "resource_namespace": self.resource_namespace,
            "resource_kind": self.resource_kind,
            "overall_score": self.overall_score,
            "pillar_scores": {
                pillar.value: {
                    "score": score.score,
                    "max_score": score.max_score,
                    "passed_checks": score.passed_checks,
                    "total_checks": score.total_checks,
                    "weighted_score": score.weighted_score,
                }
                for pillar, score in self.pillar_scores.items()
            },
            "critical_issues": self.critical_issues,
            "error_issues": self.error_issues,
            "warning_issues": self.warning_issues,
            "passed_checks": self.passed_checks,
            "total_checks": self.total_checks,
            "timestamp": self.timestamp.isoformat(),
            "criticality_level": self.criticality_level,
        }


@dataclass
class ScorecardConfig:
    rules: List[ValidationRule] = field(default_factory=list)

    notify_critical_threshold: float = 70.0
    notify_error_threshold: float = 80.0
    notify_warning_threshold: float = 90.0

    notification_cooldown_minutes: int = 60
    batch_notifications: bool = True
    batch_interval_minutes: int = 15

    excluded_namespaces: List[str] = field(
        default_factory=lambda: ["kube-system", "kube-public", "kube-node-lease"]
    )

    enable_drift_detection: bool = True
    store_history: bool = False
    max_history_per_resource: int = 10
