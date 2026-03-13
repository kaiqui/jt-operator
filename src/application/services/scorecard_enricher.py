from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.domain.enriched_scorecard import (
    BackstageProfile,
    CostProfile,
    EnrichedScorecard,
)
from src.domain.models import ResourceScorecard
from src.infrastructure.backstage.enricher import BackstageEnricher
from src.infrastructure.castai.cost_enricher import CastaiCostEnricher
from src.utils.json_logger import get_logger


class ScorecardsStore:
    def __init__(self) -> None:
        self._store: Dict[str, EnrichedScorecard] = {}
        self._squad_index: Dict[str, set] = defaultdict(set)
        self.logger = get_logger(self.__class__.__name__)

    def upsert(self, enriched: EnrichedScorecard) -> None:
        key = f"{enriched.namespace}/{enriched.service_name}"
        old = self._store.get(key)

        if old and old.squad != enriched.squad:
            self._squad_index[old.squad].discard(key)

        self._store[key] = enriched
        self._squad_index[enriched.squad].add(key)

        self.logger.debug(
            "ScorecardsStore atualizado",
            extra={
                "key": key,
                "squad": enriched.squad,
                "score": enriched.overall_score,
                "total_entries": len(self._store),
            },
        )

    def remove(self, namespace: str, name: str) -> None:
        key = f"{namespace}/{name}"
        entry = self._store.pop(key, None)
        if entry:
            self._squad_index[entry.squad].discard(key)

    def get(self, namespace: str, name: str) -> Optional[EnrichedScorecard]:
        return self._store.get(f"{namespace}/{name}")

    def get_by_squad(self, squad: str) -> List[EnrichedScorecard]:
        keys = self._squad_index.get(squad, set())
        return [self._store[k] for k in keys if k in self._store]

    def all(self) -> List[EnrichedScorecard]:
        return list(self._store.values())

    def squads(self) -> List[str]:
        return [s for s, keys in self._squad_index.items() if keys]

    def squad_summary(self, squad: str) -> Dict[str, Any]:
        services = self.get_by_squad(squad)
        if not services:
            return {"squad": squad, "services_count": 0}

        total_cost = sum(s.cost.monthly_cost_usd for s in services)
        total_savings = sum(s.cost.waste_usd for s in services)
        avg_score = sum(s.overall_score for s in services) / len(services)
        critical_services = [s for s in services if s.scorecard.critical_issues > 0]
        below_threshold = [s for s in services if s.overall_score < 70]

        return {
            "squad": squad,
            "services_count": len(services),
            "avg_score": round(avg_score, 1),
            "total_monthly_cost_usd": round(total_cost, 2),
            "total_potential_savings_usd": round(total_savings, 2),
            "critical_services_count": len(critical_services),
            "below_threshold_count": len(below_threshold),
            "services": [
                s.to_slack_summary()
                for s in sorted(services, key=lambda x: x.overall_score)
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def platform_summary(self) -> Dict[str, Any]:
        all_services = self.all()
        if not all_services:
            return {"services_count": 0}

        total_cost = sum(s.cost.monthly_cost_usd for s in all_services)
        total_savings = sum(s.cost.waste_usd for s in all_services)
        avg_score = sum(s.overall_score for s in all_services) / len(all_services)

        squads_summary = [self.squad_summary(sq) for sq in self.squads()]
        squads_summary.sort(key=lambda x: x.get("avg_score", 0))

        return {
            "services_count": len(all_services),
            "squads_count": len(self.squads()),
            "avg_score": round(avg_score, 1),
            "total_monthly_cost_usd": round(total_cost, 2),
            "total_potential_savings_usd": round(total_savings, 2),
            "critical_services_count": sum(
                1 for s in all_services if s.scorecard.critical_issues > 0
            ),
            "squads": squads_summary,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


class ScorecardEnricher:
    def __init__(
        self,
        store: ScorecardsStore,
        backstage_enricher: Optional[BackstageEnricher] = None,
        castai_enricher: Optional[CastaiCostEnricher] = None,
    ) -> None:
        self._store = store
        self._backstage = backstage_enricher
        self._castai = castai_enricher
        self.logger = get_logger(self.__class__.__name__)

    def enrich_and_store(self, scorecard: ResourceScorecard) -> EnrichedScorecard:
        name = scorecard.resource_name
        namespace = scorecard.resource_namespace

        backstage_profile = (
            self._backstage.get_profile(name, namespace)
            if self._backstage
            else BackstageProfile.unknown(name)
        )

        cost_profile = CostProfile.unavailable()
        if self._castai and backstage_profile.scorecard_enabled:
            cost_profile = self._castai.get_cost_profile(name, namespace)

        enriched = EnrichedScorecard(
            scorecard=scorecard,
            backstage=backstage_profile,
            cost=cost_profile,
        )

        self._store.upsert(enriched)

        self.logger.info(
            "Scorecard enriquecido e armazenado",
            extra={
                "service": name,
                "namespace": namespace,
                "squad": enriched.squad,
                "score": enriched.overall_score,
                "monthly_cost_usd": cost_profile.monthly_cost_usd,
                "potential_savings_usd": cost_profile.potential_savings_usd,
                "backstage_found": backstage_profile.squad != "unknown",
            },
        )

        return enriched

    def remove(self, namespace: str, name: str) -> None:
        self._store.remove(namespace, name)

    @property
    def store(self) -> ScorecardsStore:
        return self._store

    def format_slack_message(self, enriched: EnrichedScorecard) -> str:
        summary = enriched.to_slack_summary()
        sc = enriched.scorecard

        msg = (
            f"*📊 SCORECARD — {summary['service']}*\n"
            f"*Squad:* {summary['squad']}  |  "
            f"*Tier:* {summary['tier']}  |  "
            f"*Namespace:* {summary['namespace']}\n"
            f"*Score:* {summary['score_emoji']} {summary['score']}\n\n"
        )

        if sc.critical_issues:
            msg += f"🔴 *Issues Críticas:* {sc.critical_issues}\n"
        if sc.error_issues:
            msg += f"❌ *Issues de Erro:* {sc.error_issues}\n"
        if sc.warning_issues:
            msg += f"⚠️ *Warnings:* {sc.warning_issues}\n"
        msg += f"✅ *Checks Passados:* {sc.passed_checks}/{sc.total_checks}\n\n"

        if enriched.cost.monthly_cost_usd > 0:
            msg += "*💰 CUSTO (CAST AI)*\n"
            msg += f"• Custo mensal: *{summary['monthly_cost_usd']}*\n"
            msg += f"• Saving potencial: *{summary['potential_savings_usd']}*\n"
            msg += f"• Custo/ponto de score: *{summary['cost_per_score_point']}*\n"
            if summary["cpu_efficiency"] != "—":
                msg += f"• Eficiência CPU: *{summary['cpu_efficiency']}*\n"
            if summary["memory_efficiency"] != "—":
                msg += f"• Eficiência Memória: *{summary['memory_efficiency']}*\n"
            if enriched.cost.rightsizing_recommendations:
                msg += "\n*🔧 Rightsizing sugerido:*\n"
                for rec in enriched.cost.rightsizing_recommendations[:3]:
                    msg += f"  • {rec}\n"
            msg += "\n"

        msg += "*🏛️ DETALHES POR PILAR:*\n"
        for pillar, pillar_score in sc.pillar_scores.items():
            emoji_map = {
                "resilience": "🛡️",
                "security": "🔐",
                "performance": "⚡",
                "cost": "💰",
                "operational": "🛠️",
                "compliance": "📋",
            }
            emoji = emoji_map.get(pillar.value, "📊")
            msg += (
                f"\n{emoji} *{pillar.value.upper()}*: "
                f"{pillar_score.score:.1f}/100 "
                f"({pillar_score.passed_checks}/{pillar_score.total_checks})\n"
            )
            for v in pillar_score.validation_results:
                if not v.passed:
                    sev_emoji = {"critical": "🔴", "error": "❌", "warning": "⚠️"}.get(
                        v.severity.value, "ℹ️"
                    )
                    msg += f"  {sev_emoji} {v.rule_name}: {v.message[:120]}\n"
                    if v.remediation:
                        msg += f"    💡 {v.remediation[:100]}\n"

        return msg[:3000]

    def format_squad_slack_message(self, squad: str) -> str:
        summary = self._store.squad_summary(squad)
        if summary.get("services_count", 0) == 0:
            return f"*Squad {squad}*: sem dados disponíveis."

        score_emoji = (
            "🟢"
            if summary["avg_score"] >= 90
            else ("🟡" if summary["avg_score"] >= 70 else "🔴")
        )

        msg = (
            f"*📊 RESUMO DO SQUAD — {squad.upper()}*\n\n"
            f"*Score Médio:* {score_emoji} {summary['avg_score']}/100\n"
            f"*Serviços Monitorados:* {summary['services_count']}\n"
            f"*Custo Mensal Total:* ${summary['total_monthly_cost_usd']:.2f}\n"
            f"*Saving Potencial:* ${summary['total_potential_savings_usd']:.2f}\n"
        )

        if summary["critical_services_count"] > 0:
            msg += f"🔴 *Serviços com issues críticas:* {summary['critical_services_count']}\n"
        if summary["below_threshold_count"] > 0:
            msg += f"⚠️ *Serviços abaixo de 70:* {summary['below_threshold_count']}\n"

        msg += "\n*📋 SERVIÇOS (ordenados por score):*\n"
        for svc in summary.get("services", [])[:10]:
            msg += (
                f"  {svc['score_emoji']} *{svc['service']}* — "
                f"Score: {svc['score']} | "
                f"Custo: {svc['monthly_cost_usd']} | "
                f"Saving: {svc['potential_savings_usd']}\n"
            )

        return msg[:3000]
