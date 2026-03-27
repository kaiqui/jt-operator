from typing import List, Optional, Dict, Any, Tuple

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
        resource_uid: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        slo_uid = f"slo_uid:{namespace}:{service}"

        for existing_slo in existing_slos:
            if (
                slo_uid in existing_slo.tags
                and "managed_by:titlis_operator" in existing_slo.tags
            ):
                desired_slo = self._build_slo_from_spec(
                    namespace, service, spec, resource_uid
                )

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

        if existing_slo.timeframe != desired_slo.timeframe:
            changes["timeframe"] = {
                "old": existing_slo.timeframe.value,
                "new": desired_slo.timeframe.value,
            }

        if existing_slo.description != desired_slo.description:
            changes["description"] = {
                "old": existing_slo.description,
                "new": desired_slo.description,
            }

        return changes

    def _detect_framework(
        self,
        spec: SLOConfigSpec,
        k8s_annotations: Optional[dict],
    ) -> Tuple[SLOAppFramework, str]:
        annotation_key = "titlis.io/app-framework"
        if k8s_annotations and annotation_key in k8s_annotations:
            raw = k8s_annotations[annotation_key].strip().upper()
            try:
                fw = SLOAppFramework(raw.lower())
                self.logger.info(
                    "Framework detectado via annotation",
                    extra={
                        "event": "framework_detected",
                        "slo_config": spec.service,
                        "detected_framework": fw.value,
                        "detection_source": "annotation",
                    },
                )
                return fw, "annotation"
            except ValueError:
                self.logger.warning(
                    "Annotation titlis.io/app-framework com valor inválido, ignorando",
                    extra={"value": raw, "service": spec.service},
                )

        service_def = self.datadog_port.get_service_definition(spec.service)
        if service_def:
            for tag in service_def.tags:
                if tag.lower().startswith("framework:"):
                    raw_fw = tag.split(":", 1)[1].strip().lower()
                    try:
                        fw = SLOAppFramework(raw_fw)
                        self.logger.info(
                            "Framework detectado via Datadog Service Definition",
                            extra={
                                "event": "framework_detected",
                                "slo_config": spec.service,
                                "detected_framework": fw.value,
                                "detection_source": "datadog_tag",
                            },
                        )
                        return fw, "datadog_tag"
                    except ValueError:
                        self.logger.warning(
                            "Tag framework no Datadog com valor não suportado",
                            extra={"tag": tag, "service": spec.service},
                        )

        self.logger.info(
            "Framework não detectado, usando fallback WSGI",
            extra={
                "event": "framework_detected",
                "slo_config": spec.service,
                "detected_framework": SLOAppFramework.WSGI.value,
                "detection_source": "fallback",
            },
        )
        return SLOAppFramework.WSGI, "fallback"

    def reconcile_slo(
        self,
        namespace: str,
        service: str,
        spec: SLOConfigSpec,
        resource_uid: Optional[str] = None,
        known_slo_id: Optional[str] = None,
        k8s_annotations: Optional[dict] = None,
    ) -> Dict[str, Any]:
        self.logger.info(
            "Reconciliando SLO",
            extra={
                "namespace": namespace,
                "service": service,
                "slo_type": spec.type.value,
                "known_slo_id": known_slo_id,
                "resource_uid": resource_uid,
            },
        )

        effective_spec = spec
        detection_source = "explicit"
        if spec.auto_detect_framework and spec.app_framework is None:
            detected_fw, detection_source = self._detect_framework(
                spec, k8s_annotations
            )
            effective_spec = spec.model_copy(update={"app_framework": detected_fw})

        detected_framework_value = (
            effective_spec.app_framework.value if effective_spec.app_framework else None
        )
        slo_name = f"slo_uid:{namespace}:{service}"

        try:
            # Path A: known_slo_id from status — fast path, skip search
            if known_slo_id:
                desired_slo = self._build_slo_from_spec(
                    namespace, service, effective_spec, resource_uid
                )
                success = self.datadog_port.update_slo_apps(known_slo_id, desired_slo)
                self.logger.info(
                    "SLO atualizado via known_slo_id (fast path)",
                    extra={"slo_id": known_slo_id, "success": success},
                )
                return {
                    "success": success,
                    "action": "updated",
                    "slo_id": known_slo_id,
                    "slo_name": slo_name,
                    "detected_framework": detected_framework_value,
                    "detection_source": detection_source,
                    "error": None if success else "Falha ao atualizar SLO",
                }

            # Path B: no known_slo_id but resource_uid present — orphan safety check
            if resource_uid:
                orphan = self.datadog_port.find_slo_by_tags(
                    [f"titlis_resource_uid:{resource_uid}"]
                )
                if orphan and orphan.slo_id:
                    desired_slo = self._build_slo_from_spec(
                        namespace, service, effective_spec, resource_uid
                    )
                    success = self.datadog_port.update_slo_apps(
                        orphan.slo_id, desired_slo
                    )
                    self.logger.info(
                        "SLO órfão encontrado e atualizado via tag resource_uid",
                        extra={"slo_id": orphan.slo_id, "success": success},
                    )
                    return {
                        "success": success,
                        "action": "updated",
                        "slo_id": orphan.slo_id,
                        "slo_name": slo_name,
                        "detected_framework": detected_framework_value,
                        "detection_source": detection_source,
                        "error": None if success else "Falha ao atualizar SLO órfão",
                    }

            # Path C: original flow — search by service tags, then create
            existing_slos = self.datadog_port.get_service_slos(service)

            update_result = self.check_and_update_existing_slo(
                namespace, service, effective_spec, existing_slos, resource_uid
            )

            if update_result:
                update_result["detected_framework"] = detected_framework_value
                update_result["detection_source"] = detection_source
                return update_result

            self.logger.info("Criando novo SLO", extra={"slo_name": slo_name})

            new_slo = self._build_slo_from_spec(
                namespace, service, effective_spec, resource_uid
            )

            slo_id = self.datadog_port.create_slo(new_slo)

            return {
                "success": slo_id is not None,
                "action": "created",
                "slo_id": slo_id,
                "slo_name": slo_name,
                "detected_framework": detected_framework_value,
                "detection_source": detection_source,
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
                "slo_name": slo_name,
                "detected_framework": detected_framework_value,
                "detection_source": detection_source,
            }

    def delete_slo(self, slo_id: str) -> bool:
        self.logger.info("Deletando SLO", extra={"slo_id": slo_id})
        result: bool = self.datadog_port.delete_slo(slo_id)
        return result

    def get_service_slos(self, service_name: str) -> List[SLO]:
        return self.datadog_port.get_service_slos(service_name)

    def _build_slo_from_spec(
        self,
        namespace: str,
        service: str,
        spec: SLOConfigSpec,
        resource_uid: Optional[str] = None,
    ) -> SLO:
        tags = [
            f"namespace:{namespace}",
            f"service:{service}",
            "managed_by:titlis_operator",
            f"slo_uid:{namespace}:{service}",
        ]
        if resource_uid:
            tags.append(f"titlis_resource_uid:{resource_uid}")
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
            "target": float(spec.target),
        }

        if spec.warning:
            threshold_data["warning"] = float(spec.warning)

        thresholds = [threshold_data]

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
            thresholds=thresholds,
        )
