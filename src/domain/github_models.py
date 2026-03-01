from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class RemediationRuleCategory(str, Enum):
    HPA = "hpa"
    RESOURCES = "resources"


@dataclass
class DatadogProfilingMetrics:
    """Métricas de profiling coletadas do Datadog para embasar os valores sugeridos."""

    cpu_avg_millicores: Optional[int] = None   # CPU média em millicores
    memory_avg_mib: Optional[int] = None       # Memória média em MiB

    def suggest_cpu_request(self, default: str = "100m") -> str:
        """Request de CPU: média + 20% de buffer, mínimo 100m."""
        if self.cpu_avg_millicores:
            return f"{max(100, int(self.cpu_avg_millicores * 1.2))}m"
        return default

    def suggest_cpu_limit(self, default: str = "500m") -> str:
        """Limit de CPU: 3x a média, mínimo 300m."""
        if self.cpu_avg_millicores:
            return f"{max(300, int(self.cpu_avg_millicores * 3))}m"
        return default

    def suggest_memory_request(self, default: str = "128Mi") -> str:
        """Request de memória: média + 20% de buffer, mínimo 128Mi."""
        if self.memory_avg_mib:
            return f"{max(128, int(self.memory_avg_mib * 1.2))}Mi"
        return default

    def suggest_memory_limit(self, default: str = "512Mi") -> str:
        """Limit de memória: 2x a média, mínimo 256Mi."""
        if self.memory_avg_mib:
            return f"{max(256, int(self.memory_avg_mib * 2))}Mi"
        return default


@dataclass
class RemediationIssue:
    """Issue detectada pelo scorecard que pode ser remediada automaticamente."""

    rule_id: str
    rule_name: str
    description: str
    remediation: str
    category: RemediationRuleCategory = RemediationRuleCategory.RESOURCES

    def __post_init__(self) -> None:
        if self.rule_id in {"RES-007", "RES-008", "PERF-002"}:
            self.category = RemediationRuleCategory.HPA
        elif self.rule_id in {"RES-003", "RES-004", "RES-005", "RES-006", "PERF-001"}:
            self.category = RemediationRuleCategory.RESOURCES


@dataclass
class RemediationFile:
    """Arquivo gerado para remediação, que será commitado no PR."""

    path: str
    content: str
    commit_message: str


@dataclass
class PullRequestResult:
    """Resultado da criação de um Pull Request no GitHub."""

    number: int
    title: str
    url: str
    branch: str
    base_branch: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    issues_fixed: List[str] = field(default_factory=list)


@dataclass
class RemediationRequest:
    """
    Solicitação de remediação automática para um recurso Kubernetes.

    O repo_owner e repo_name são extraídos internamente pelo RemediationService
    a partir de DD_GIT_REPOSITORY_URL no resource_body.
    """

    resource_name: str
    namespace: str
    resource_kind: str
    issues: List[RemediationIssue]
    resource_body: Dict[str, Any]   # corpo completo do recurso K8s (spec, env vars, etc.)
    base_branch: str = "develop"


@dataclass
class RemediationResult:
    """Resultado da execução de uma remediação automática."""

    success: bool
    pull_request: Optional[PullRequestResult] = None
    branch_name: Optional[str] = None
    error: Optional[str] = None
