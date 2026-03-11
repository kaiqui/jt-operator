from __future__ import annotations

import re
from typing import Any, Dict, Optional
from datetime import datetime, timezone, timedelta

import requests

from src.domain.enriched_scorecard import BackstageProfile
from src.utils.json_logger import get_logger


class BackstageEnricher:
    _ANNOTATION_SLO_TARGET = "titlis.io/slo-target"
    _ANNOTATION_SCORECARD_ENABLED = "titlis.io/scorecard-enabled"
    _ANNOTATION_TIER = "titlis.io/tier"
    _ANNOTATION_TECH_LEAD = "titlis.io/tech-lead"

    def __init__(
        self,
        backstage_url: str,
        token: Optional[str] = None,
        cache_ttl_seconds: int = 300,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._base_url = backstage_url.rstrip("/")
        self._token = token
        self._cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._timeout = timeout_seconds
        self._cache: Dict[str, tuple[BackstageProfile, datetime]] = {}
        self.logger = get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def get_profile(
        self, resource_name: str, namespace: str = "default"
    ) -> BackstageProfile:
        cache_key = f"{namespace}/{resource_name}"

        # Verifica cache
        cached = self._cache.get(cache_key)
        if cached:
            profile, cached_at = cached
            if datetime.now(timezone.utc) - cached_at < self._cache_ttl:
                self.logger.debug(
                    "BackstageProfile retornado do cache",
                    extra={"resource": cache_key},
                )
                return profile

        try:
            profile = self._fetch_profile(resource_name, namespace)
        except Exception:
            self.logger.exception(
                "Erro ao buscar perfil no Backstage — usando fallback",
                extra={"resource": cache_key},
            )
            profile = BackstageProfile.unknown(resource_name)

        # Armazena no cache independente de ser fallback (evita flood em caso de serviço ausente)
        self._cache[cache_key] = (profile, datetime.now(timezone.utc))
        return profile

    def invalidate(self, resource_name: str, namespace: str = "default") -> None:
        self._cache.pop(f"{namespace}/{resource_name}", None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_profile(self, resource_name: str, namespace: str) -> BackstageProfile:
        # Tenta lookup por kubernetes-id primeiro (mais preciso)
        entity = self._lookup_by_k8s_id(resource_name) or self._lookup_by_name(
            resource_name
        )

        if not entity:
            self.logger.info(
                "Serviço não encontrado no Backstage — usando fallback",
                extra={"resource_name": resource_name, "namespace": namespace},
            )
            return BackstageProfile.unknown(resource_name)

        return self._parse_entity(entity, resource_name)

    def _lookup_by_k8s_id(self, resource_name: str) -> Optional[Dict[str, Any]]:
        url = (
            f"{self._base_url}/api/catalog/entities"
            f"?filter=metadata.annotations.backstage.io/kubernetes-id={resource_name}"
        )
        return self._get_first(url)

    def _lookup_by_name(self, resource_name: str) -> Optional[Dict[str, Any]]:
        url = f"{self._base_url}/api/catalog/entities/by-name/component/default/{resource_name}"
        try:
            resp = self._request("GET", url)
            if resp and resp.status_code == 200:
                result: Dict[str, Any] = resp.json()
                return result
        except Exception:
            pass
        return None

    def _get_first(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self._request("GET", url)
            if resp and resp.status_code == 200:
                items: Any = resp.json()
                if isinstance(items, list) and items:
                    first: Dict[str, Any] = items[0]
                    return first
        except Exception:
            pass
        return None

    def _request(self, method: str, url: str) -> Optional[requests.Response]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        return requests.request(
            method,
            url,
            headers=headers,
            timeout=self._timeout,
        )

    def _parse_entity(
        self, entity: Dict[str, Any], resource_name: str
    ) -> BackstageProfile:
        metadata = entity.get("metadata", {})
        spec = entity.get("spec", {})
        annotations = metadata.get("annotations", {})

        # owner: "group:squad-pagamentos" → squad: "squad-pagamentos"
        raw_owner = spec.get("owner", "group:unknown")
        squad = re.sub(r"^group:", "", raw_owner).strip() or "unknown"

        # system: "system:checkout" → "checkout"
        raw_system = spec.get("system")
        system = re.sub(r"^system:", "", raw_system).strip() if raw_system else None

        # Anotações customizadas Titlis
        tier = annotations.get(self._ANNOTATION_TIER)
        tech_lead = annotations.get(self._ANNOTATION_TECH_LEAD)
        scorecard_enabled = (
            annotations.get(self._ANNOTATION_SCORECARD_ENABLED, "true").lower()
            != "false"
        )

        slo_target_override: Optional[float] = None
        raw_slo_target = annotations.get(self._ANNOTATION_SLO_TARGET)
        if raw_slo_target:
            try:
                slo_target_override = float(raw_slo_target)
            except ValueError:
                self.logger.warning(
                    f"Valor inválido para {self._ANNOTATION_SLO_TARGET}: {raw_slo_target}",
                    extra={"resource_name": resource_name},
                )

        entity_ref = (
            f"{entity.get('kind', 'Component').lower()}:"
            f"{metadata.get('namespace', 'default')}/"
            f"{metadata.get('name', resource_name)}"
        )

        profile = BackstageProfile(
            entity_ref=entity_ref,
            component_name=metadata.get("name", resource_name),
            component_kind=entity.get("kind", "Component"),
            owner=raw_owner,
            squad=squad,
            system=system,
            tier=tier,
            slo_target_override=slo_target_override,
            scorecard_enabled=scorecard_enabled,
            tech_lead_email=tech_lead,
        )

        self.logger.info(
            "BackstageProfile obtido",
            extra={
                "resource_name": resource_name,
                "squad": squad,
                "system": system,
                "tier": tier,
                "entity_ref": entity_ref,
            },
        )

        return profile
