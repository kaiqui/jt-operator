from typing import List, Optional, Dict, Any

from src.domain.models import SLO, SLOConfigSpec, SLOType, SLOAppFramework
from src.application.ports.datadog_port import DatadogPort
from src.utils.json_logger import get_logger


class SLOService:
    def __init__(self, datadog_port: DatadogPort):
        self.datadog_port = datadog_port
        self.logger = get_logger(self.__class__.__name__)

    def check_and_update_existing_slo(
        self,
        namespace: str,
        service: str,
        spec: SLOConfigSpec,
        existing_slos: List[SLO],
    ) -> Optional[Dict[str, Any]]:
        slo_uid = f"slo_uid:{namespace}:{service}"

        for existing_slo in existing_slos:
            # Verifica se é um SLO gerenciado por nós
            if (
                slo_uid in existing_slo.tags
                and "managed_by:titlis_operator" in existing_slo.tags
            ):
                # Constrói o SLO desejado
                desired_slo = self._build_slo_from_spec(namespace, service, spec)

                # Compara os parâmetros
                needs_update = self._compare_slo_parameters(existing_slo, desired_slo)

                if needs_update:
                    self.logger.info(
                        "Atualizando SLO existente",
                        extra={
                            "slo_id": existing_slo.slo_id,
                            "slo_name": slo_uid,
                            "changes": needs_update,
                        },
                    )

                    if not existing_slo.slo_id:
                        return {
                            "success": False,
                            "action": "updated",
                            "slo_id": None,
                            "slo_name": slo_uid,
                            "error": "SLO ID não disponível para atualização",
                        }
                    success = self.datadog_port.update_slo_apps(
                        existing_slo.slo_id, desired_slo
                    )

                    return {
                        "success": success,
                        "action": "updated",
                        "slo_id": existing_slo.slo_id,
                        "slo_name": slo_uid,
                        "error": None if success else "Falha ao atualizar SLO",
                    }
                else:
                    self.logger.info(
                        "SLO já está sincronizado",
                        extra={"slo_id": existing_slo.slo_id, "slo_name": slo_uid},
                    )

                    return {
                        "success": True,
                        "action": "noop",
                        "slo_id": existing_slo.slo_id,
                        "slo_name": slo_uid,
                        "message": "SLO já está sincronizado",
                    }

        return None

    def _compare_slo_parameters(
        self, existing_slo: SLO, desired_slo: SLO
    ) -> Dict[str, Any]:
        changes: Dict[str, Any] = {}

        # Compara target_threshold
        existing_target = (
            float(existing_slo.target_threshold)
            if existing_slo.target_threshold
            else None
        )
        desired_target = (
            float(desired_slo.target_threshold)
            if desired_slo.target_threshold
            else None
        )

        if existing_target != desired_target:
            changes["target_threshold"] = {
                "old": existing_target,
                "new": desired_target,
            }

        # Compara warning_threshold
        existing_warning = (
            float(existing_slo.warning_threshold)
            if existing_slo.warning_threshold
            else None
        )
        desired_warning = (
            float(desired_slo.warning_threshold)
            if desired_slo.warning_threshold
            else None
        )

        if existing_warning != desired_warning:
            changes["warning_threshold"] = {
                "old": existing_warning,
                "new": desired_warning,
            }

        # Compara timeframe
        if existing_slo.timeframe != desired_slo.timeframe:
            changes["timeframe"] = {
                "old": existing_slo.timeframe.value,
                "new": desired_slo.timeframe.value,
            }

        # Compara description
        if existing_slo.description != desired_slo.description:
            changes["description"] = {
                "old": existing_slo.description,
                "new": desired_slo.description,
            }

        return changes

    def reconcile_slo(
        self, namespace: str, service: str, spec: SLOConfigSpec
    ) -> Dict[str, Any]:
        self.logger.info(
            "Reconciliando SLO",
            extra={
                "namespace": namespace,
                "service": service,
                "slo_type": spec.type.value,
            },
        )

        try:
            # Verifica se o SLO já existe
            existing_slos = self.datadog_port.get_service_slos(service)

            # Verifica se já existe e se precisa atualizar
            update_result = self.check_and_update_existing_slo(
                namespace, service, spec, existing_slos
            )

            if update_result:
                return update_result

            # Se não existe, cria novo
            self.logger.info(
                "Criando novo SLO", extra={"slo_name": f"slo_uid:{namespace}:{service}"}
            )

            # Constrói o objeto SLO
            new_slo = self._build_slo_from_spec(namespace, service, spec)

            # Cria o SLO
            slo_id = self.datadog_port.create_slo(new_slo)

            return {
                "success": slo_id is not None,
                "action": "created",
                "slo_id": slo_id,
                "slo_name": f"slo_uid:{namespace}:{service}",
            }

        except Exception:
            self.logger.exception(
                "Erro ao reconciliar SLO",
                extra={
                    "namespace": namespace,
                    "service": service,
                },
            )

            return {
                "success": False,
                "action": "failed",
                "slo_name": f"slo_uid:{namespace}:{service}",
            }

    def delete_slo(self, slo_id: str) -> bool:
        self.logger.info("Deletando SLO", extra={"slo_id": slo_id})
        result: bool = self.datadog_port.delete_slo(slo_id)
        return result

    def get_service_slos(self, service_name: str) -> List[SLO]:
        return self.datadog_port.get_service_slos(service_name)

    def _build_slo_from_spec(
        self, namespace: str, service: str, spec: SLOConfigSpec
    ) -> SLO:
        tags = [
            f"namespace:{namespace}",
            f"service:{service}",
            "managed_by:titlis_operator",
            f"slo_uid:{namespace}:{service}",
        ]
        tags.extend(spec.tags)

        query = None
        if spec.type:
            if (
                spec.app_framework == SLOAppFramework.WSGI
                and spec.type == SLOType.METRIC
            ):
                query = {
                    "numerator": f"sum:trace.wsgi.request.hits{{env:dev,service:{service},span.kind:server}}.as_count()",
                    "denominator": f"sum:trace.wsgi.request.hits{{env:dev,service:{service},span.kind:server}}.as_count() - sum:trace.wsgi.request.errors{{env:dev,service:{service},span.kind:server}}.as_count()",
                }
            elif (
                spec.app_framework == SLOAppFramework.FASTAPI
                and spec.type == SLOType.METRIC
            ):
                query = {
                    "numerator": f"sum:trace.fastapi.request.hits{{env:dev,service:{service},span.kind:server}}.as_count()",
                    "denominator": f"sum:trace.fastapi.request.hits{{env:dev,service:{service},span.kind:server}}.as_count() - sum:trace.fastapi.request.errors{{env:dev,service:{service},span.kind:server}}.as_count()",
                }
            elif (
                spec.app_framework == SLOAppFramework.AIOHTTP
                and spec.type == SLOType.METRIC
            ):
                query = {
                    "numerator": f"sum:trace.aiohttp.request.hits{{env:dev,service:{service},span.kind:server}}.as_count()",
                    "denominator": f"sum:trace.aiohttp.request.hits{{env:dev,service:{service},span.kind:server}}.as_count() - sum:trace.aiohttp.request.errors{{env:dev,service:{service},span.kind:server}}.as_count()",
                }
            elif spec.type == SLOType.METRIC and spec.numerator and spec.denominator:
                query = {"numerator": spec.numerator, "denominator": spec.denominator}

        threshold_data = {
            "timeframe": spec.timeframe.value,
            "target": float(spec.target),  # Garantir que é float
        }

        if spec.warning:
            threshold_data["warning"] = float(spec.warning)

        thresholds = [threshold_data]  # Lista com APENAS UM threshold

        self.logger.info(
            "Thresholds construídos para SLO",
            extra={
                "thresholds": thresholds,
                "target": spec.target,
                "warning": spec.warning,
            },
        )

        return SLO(
            name=f"SLO - {namespace}/{service}",
            service_name=service,
            slo_type=spec.type,
            target_threshold=float(spec.target),
            warning_threshold=float(spec.warning) if spec.warning else None,
            timeframe=spec.timeframe,
            description=spec.description
            or f"SLO para {service} no namespace {namespace}",
            tags=tags,
            query=query,
            thresholds=thresholds,  # Usar nossa estrutura corrigida
        )
