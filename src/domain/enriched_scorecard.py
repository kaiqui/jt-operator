from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.domain.models import ResourceScorecard

# ---------------------------------------------------------------------------
# Perfil de ownership vindo do Backstage
# ---------------------------------------------------------------------------


@dataclass
class BackstageProfile:
    # Identificação no catálogo
    entity_ref: str  # Ex: "component:default/payment-api"
    component_name: str
    component_kind: str  # Component | Service | ...

    # Ownership
    owner: str  # Ex: "group:squad-pagamentos"
    squad: str  # Extraído de owner (sem o prefixo "group:")
    system: Optional[str] = None  # Ex: "checkout"

    # Tier / criticidade (anotação customizada)
    tier: Optional[str] = None  # Ex: "tier-1"

    # Configuração customizada injetada via anotações do catalog-info.yaml
    slo_target_override: Optional[float] = None  # titlis.io/slo-target
    scorecard_enabled: bool = True  # titlis.io/scorecard-enabled

    # Contatos
    tech_lead_email: Optional[str] = None

    # Timestamp da consulta
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def unknown(cls, component_name: str) -> "BackstageProfile":
        return cls(
            entity_ref=f"component:unknown/{component_name}",
            component_name=component_name,
            component_kind="Component",
            owner="group:unknown",
            squad="unknown",
        )


# ---------------------------------------------------------------------------
# Perfil de custo vindo do CAST AI
# ---------------------------------------------------------------------------


@dataclass
class CostProfile:
    # Custo mensal estimado (USD ou moeda configurada)
    monthly_cost_usd: float = 0.0

    # Economia realizada pelo CAST AI (spot, bin packing, etc.)
    monthly_savings_usd: float = 0.0

    # Economia potencial ainda não realizada (rightsizing)
    potential_savings_usd: float = 0.0

    # CPU/Memória — uso vs request (para detectar over-provisioning)
    cpu_requested_millicores: Optional[float] = None
    cpu_used_avg_millicores: Optional[float] = None
    memory_requested_mib: Optional[float] = None
    memory_used_avg_mib: Optional[float] = None

    # Recomendações de rightsizing
    rightsizing_recommendations: List[str] = field(default_factory=list)

    # Indicadores derivados
    @property
    def cpu_efficiency_pct(self) -> Optional[float]:
        if self.cpu_requested_millicores and self.cpu_used_avg_millicores:
            return round(
                (self.cpu_used_avg_millicores / self.cpu_requested_millicores) * 100, 1
            )
        return None

    @property
    def memory_efficiency_pct(self) -> Optional[float]:
        if self.memory_requested_mib and self.memory_used_avg_mib:
            return round(
                (self.memory_used_avg_mib / self.memory_requested_mib) * 100, 1
            )
        return None

    @property
    def waste_usd(self) -> float:
        return round(self.potential_savings_usd, 2)

    # Timestamp da consulta
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def unavailable(cls) -> "CostProfile":
        return cls()


# ---------------------------------------------------------------------------
# Scorecard enriquecido — modelo principal
# ---------------------------------------------------------------------------


@dataclass
class EnrichedScorecard:
    # Scorecard original (calculado pelo ScorecardService)
    scorecard: ResourceScorecard

    # Dados de ownership (Backstage)
    backstage: BackstageProfile

    # Dados de custo (CAST AI)
    cost: CostProfile

    # Timestamp de quando o enriquecimento foi gerado
    enriched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ---------------------------------------------------------------------------
    # Atalhos de conveniência
    # ---------------------------------------------------------------------------

    @property
    def service_name(self) -> str:
        return self.scorecard.resource_name

    @property
    def namespace(self) -> str:
        return self.scorecard.resource_namespace

    @property
    def squad(self) -> str:
        return self.backstage.squad

    @property
    def overall_score(self) -> float:
        return self.scorecard.overall_score

    @property
    def tier(self) -> Optional[str]:
        return self.backstage.tier

    # ---------------------------------------------------------------------------
    # Métrica composta: custo por ponto de score
    # "Estamos gastando bem com esse serviço?"
    # ---------------------------------------------------------------------------

    @property
    def cost_per_score_point(self) -> Optional[float]:
        if self.overall_score > 0 and self.cost.monthly_cost_usd > 0:
            return round(self.cost.monthly_cost_usd / self.overall_score, 2)
        return None

    # ---------------------------------------------------------------------------
    # Serialização para uso em Slack, API, etc.
    # ---------------------------------------------------------------------------

    def to_slack_summary(self) -> Dict[str, Any]:
        score_emoji = (
            "🟢"
            if self.overall_score >= 90
            else (
                "🟡"
                if self.overall_score >= 70
                else ("🟠" if self.overall_score >= 50 else "🔴")
            )
        )

        return {
            "service": self.service_name,
            "namespace": self.namespace,
            "squad": self.squad,
            "tier": self.tier or "—",
            "score": f"{self.overall_score:.1f}/100",
            "score_emoji": score_emoji,
            "critical_issues": self.scorecard.critical_issues,
            "error_issues": self.scorecard.error_issues,
            "warning_issues": self.scorecard.warning_issues,
            "monthly_cost_usd": f"${self.cost.monthly_cost_usd:.2f}",
            "potential_savings_usd": f"${self.cost.waste_usd:.2f}",
            "cost_per_score_point": (
                f"${self.cost_per_score_point:.2f}/pt"
                if self.cost_per_score_point
                else "—"
            ),
            "cpu_efficiency": (
                f"{self.cost.cpu_efficiency_pct:.1f}%"
                if self.cost.cpu_efficiency_pct
                else "—"
            ),
            "memory_efficiency": (
                f"{self.cost.memory_efficiency_pct:.1f}%"
                if self.cost.memory_efficiency_pct
                else "—"
            ),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service": self.service_name,
            "namespace": self.namespace,
            "enriched_at": self.enriched_at.isoformat(),
            "scorecard": self.scorecard.to_dict(),
            "backstage": {
                "entity_ref": self.backstage.entity_ref,
                "squad": self.backstage.squad,
                "owner": self.backstage.owner,
                "system": self.backstage.system,
                "tier": self.backstage.tier,
                "slo_target_override": self.backstage.slo_target_override,
            },
            "cost": {
                "monthly_cost_usd": self.cost.monthly_cost_usd,
                "monthly_savings_usd": self.cost.monthly_savings_usd,
                "potential_savings_usd": self.cost.potential_savings_usd,
                "cost_per_score_point": self.cost_per_score_point,
                "cpu_efficiency_pct": self.cost.cpu_efficiency_pct,
                "memory_efficiency_pct": self.cost.memory_efficiency_pct,
                "rightsizing_recommendations": self.cost.rightsizing_recommendations,
            },
        }
