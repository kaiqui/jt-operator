from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta

import requests

from src.domain.enriched_scorecard import CostProfile
from src.utils.json_logger import get_logger


class CastaiCostEnricher:
    _BASE_URL = "https://api.cast.ai/v1"

    def __init__(
        self,
        api_key: str,
        cluster_id: str,
        cache_ttl_seconds: int = 300,
        timeout_seconds: float = 8.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key é obrigatório para CastaiCostEnricher")
        if not cluster_id:
            raise ValueError("cluster_id é obrigatório para CastaiCostEnricher")

        self._api_key = api_key
        self._cluster_id = cluster_id
        self._cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._timeout = timeout_seconds
        self._cache: Dict[str, tuple[CostProfile, datetime]] = {}
        self.logger = get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def get_cost_profile(self, workload_name: str, namespace: str) -> CostProfile:
        cache_key = f"{namespace}/{workload_name}"

        cached = self._cache.get(cache_key)
        if cached:
            profile, cached_at = cached
            if datetime.now(timezone.utc) - cached_at < self._cache_ttl:
                self.logger.debug(
                    "CostProfile retornado do cache",
                    extra={"workload": cache_key},
                )
                return profile

        try:
            profile = self._fetch_cost_profile(workload_name, namespace)
        except Exception:
            self.logger.exception(
                "Erro ao buscar custo no CAST AI — usando fallback",
                extra={"workload": cache_key},
            )
            profile = CostProfile.unavailable()

        self._cache[cache_key] = (profile, datetime.now(timezone.utc))
        return profile

    def get_squad_cost_summary(self, namespace: str) -> Dict[str, Any]:
        try:
            workloads = self._fetch_workloads_for_namespace(namespace)
            total_cost = sum(w.get("totalCost", 0) for w in workloads)
            total_savings = sum(w.get("savingsAvailable", 0) for w in workloads)
            return {
                "namespace": namespace,
                "total_monthly_cost_usd": round(total_cost, 2),
                "total_potential_savings_usd": round(total_savings, 2),
                "workload_count": len(workloads),
            }
        except Exception:
            self.logger.exception(
                "Erro ao buscar custo agregado do namespace",
                extra={"namespace": namespace},
            )
            return {
                "namespace": namespace,
                "total_monthly_cost_usd": 0.0,
                "total_potential_savings_usd": 0.0,
                "workload_count": 0,
            }

    def invalidate(self, workload_name: str, namespace: str) -> None:
        self._cache.pop(f"{namespace}/{workload_name}", None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_cost_profile(self, workload_name: str, namespace: str) -> CostProfile:
        # 1. Custo do workload
        workload_data = self._fetch_workload_cost(workload_name, namespace)

        # 2. Recomendações de rightsizing
        recommendations = self._fetch_rightsizing_recommendations(
            workload_name, namespace
        )

        if not workload_data:
            self.logger.info(
                "Workload não encontrado no CAST AI",
                extra={"workload": f"{namespace}/{workload_name}"},
            )
            return CostProfile.unavailable()

        # Extrai métricas de utilização dos containers
        containers = workload_data.get("containers", [])
        cpu_req, cpu_used, mem_req, mem_used = self._aggregate_container_metrics(
            containers
        )

        profile = CostProfile(
            monthly_cost_usd=round(workload_data.get("totalCost", 0.0), 4),
            monthly_savings_usd=round(workload_data.get("savings", 0.0), 4),
            potential_savings_usd=round(workload_data.get("savingsAvailable", 0.0), 4),
            cpu_requested_millicores=cpu_req,
            cpu_used_avg_millicores=cpu_used,
            memory_requested_mib=mem_req,
            memory_used_avg_mib=mem_used,
            rightsizing_recommendations=recommendations,
        )

        self.logger.info(
            "CostProfile obtido do CAST AI",
            extra={
                "workload": f"{namespace}/{workload_name}",
                "monthly_cost_usd": profile.monthly_cost_usd,
                "potential_savings_usd": profile.potential_savings_usd,
                "cpu_efficiency_pct": profile.cpu_efficiency_pct,
                "memory_efficiency_pct": profile.memory_efficiency_pct,
            },
        )

        return profile

    def _fetch_workload_cost(
        self, workload_name: str, namespace: str
    ) -> Optional[Dict[str, Any]]:
        url = f"{self._BASE_URL}/cost/workloads"
        params = {
            "clusterID": self._cluster_id,
            "namespace": namespace,
            "workloadName": workload_name,
        }
        resp = self._request("GET", url, params=params)
        if not resp or resp.status_code != 200:
            return None

        raw_data: Any = resp.json()
        data: Dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        items: List[Dict[str, Any]] = data.get(
            "items", raw_data if isinstance(raw_data, list) else []
        )
        # O endpoint pode retornar lista; pega o match exato pelo nome
        for item in items:
            if (
                item.get("name") == workload_name
                or item.get("workloadName") == workload_name
            ):
                return item
        return items[0] if items else None

    def _fetch_workloads_for_namespace(self, namespace: str) -> List[Dict[str, Any]]:
        url = f"{self._BASE_URL}/cost/workloads"
        params = {"clusterID": self._cluster_id, "namespace": namespace}
        resp = self._request("GET", url, params=params)
        if not resp or resp.status_code != 200:
            return []
        raw_data2: Any = resp.json()
        data2: Dict[str, Any] = raw_data2 if isinstance(raw_data2, dict) else {}
        result: List[Dict[str, Any]] = data2.get(
            "items", raw_data2 if isinstance(raw_data2, list) else []
        )
        return result

    def _fetch_rightsizing_recommendations(
        self, workload_name: str, namespace: str
    ) -> List[str]:
        url = f"{self._BASE_URL}/recommendations"
        params = {
            "clusterID": self._cluster_id,
            "namespace": namespace,
            "workloadName": workload_name,
        }
        resp = self._request("GET", url, params=params)
        if not resp or resp.status_code != 200:
            return []

        data = resp.json()
        items = data.get("items", data if isinstance(data, list) else [])
        recommendations: List[str] = []

        for item in items:
            container = item.get("containerName", "")
            rec_type = item.get("type", "")

            # CPU
            current_cpu = item.get("currentCpuRequest")
            recommended_cpu = item.get("recommendedCpuRequest")
            if current_cpu and recommended_cpu and current_cpu != recommended_cpu:
                recommendations.append(
                    f"[{container}] CPU request: {current_cpu} → {recommended_cpu} ({rec_type})"
                )

            # Memória
            current_mem = item.get("currentMemoryRequest")
            recommended_mem = item.get("recommendedMemoryRequest")
            if current_mem and recommended_mem and current_mem != recommended_mem:
                recommendations.append(
                    f"[{container}] Memory request: {current_mem} → {recommended_mem} ({rec_type})"
                )

        return recommendations

    @staticmethod
    def _aggregate_container_metrics(
        containers: List[Dict[str, Any]],
    ) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        cpu_req = cpu_used = mem_req = mem_used = None

        for c in containers:
            resources = c.get("resources", {})

            _cpu_req = resources.get("cpuRequestMillicores") or resources.get(
                "cpuRequest"
            )
            _cpu_used = resources.get("cpuUsageAvgMillicores") or resources.get(
                "cpuUsage"
            )
            _mem_req = resources.get("memoryRequestMiB") or resources.get(
                "memoryRequest"
            )
            _mem_used = resources.get("memoryUsageAvgMiB") or resources.get(
                "memoryUsage"
            )

            if _cpu_req is not None:
                cpu_req = (cpu_req or 0) + float(_cpu_req)
            if _cpu_used is not None:
                cpu_used = (cpu_used or 0) + float(_cpu_used)
            if _mem_req is not None:
                mem_req = (mem_req or 0) + float(_mem_req)
            if _mem_used is not None:
                mem_used = (mem_used or 0) + float(_mem_used)

        return cpu_req, cpu_used, mem_req, mem_used

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[requests.Response]:
        headers = {
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
        }
        try:
            return requests.request(
                method,
                url,
                headers=headers,
                params=params,
                timeout=self._timeout,
            )
        except requests.exceptions.Timeout:
            self.logger.warning("Timeout na chamada ao CAST AI", extra={"url": url})
            return None
        except Exception:
            self.logger.exception("Erro na chamada ao CAST AI", extra={"url": url})
            return None
