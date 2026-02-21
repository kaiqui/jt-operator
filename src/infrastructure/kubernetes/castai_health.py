from dataclasses import dataclass, field
from typing import List, Optional

import kubernetes
from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException

from src.utils.json_logger import get_logger


CASTAI_SERVICES = [
    "castai-agent",
    "castai-cluster-controller",
]


@dataclass
class PodHealthResult:
    service: str
    namespace: str
    cluster_name: str
    is_healthy: bool
    pod_name: Optional[str] = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "service": self.service,
            "namespace": self.namespace,
            "cluster_name": self.cluster_name,
            "is_healthy": self.is_healthy,
            "pod_name": self.pod_name,
            "reason": self.reason,
        }


class CastAIHealthChecker:
    def __init__(self, namespace: str, cluster_name: str):
        self.logger = get_logger(self.__class__.__name__)
        self.namespace = namespace
        self.cluster_name = cluster_name
        self._init_k8s_client()

    def check_all(self) -> List[PodHealthResult]:
        results = []
        for service in CASTAI_SERVICES:
            result = self._check_service(service)
            self.logger.info(
                "Health check CAST AI concluído",
                extra={
                    "service": service,
                    "is_healthy": result.is_healthy,
                    "pod_name": result.pod_name,
                    "reason": result.reason,
                    "cluster_name": self.cluster_name,
                },
            )
            results.append(result)
        return results

    def _check_service(self, service: str) -> PodHealthResult:
        base_result = PodHealthResult(
            service=service,
            namespace=self.namespace,
            cluster_name=self.cluster_name,
            is_healthy=False,
        )

        try:
            pods = self._find_pods(service)
        except ApiException as exc:
            base_result.reason = f"Erro ao listar pods: {exc.status} {exc.reason}"
            self.logger.exception(
                "ApiException ao buscar pods CAST AI",
                extra={"service": service, "namespace": self.namespace},
            )
            return base_result
        except Exception:
            base_result.reason = "Erro inesperado ao consultar Kubernetes API"
            self.logger.exception(
                "Erro inesperado ao buscar pods CAST AI",
                extra={"service": service, "namespace": self.namespace},
            )
            return base_result

        if not pods:
            base_result.reason = "Nenhum pod encontrado"
            return base_result

        pod = sorted(
            pods,
            key=lambda p: p.metadata.creation_timestamp or "",
            reverse=True,
        )[0]

        base_result.pod_name = pod.metadata.name
        base_result.is_healthy, base_result.reason = self._evaluate_pod(pod)
        return base_result

    def _find_pods(self, service: str):
        v1 = k8s_client.CoreV1Api()

        for selector in [f"app={service}", f"app.kubernetes.io/name={service}"]:
            pod_list = v1.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=selector,
            )
            if pod_list.items:
                self.logger.debug(
                    "Pods encontrados",
                    extra={
                        "service": service,
                        "selector": selector,
                        "count": len(pod_list.items),
                    },
                )
                return pod_list.items

        return []

    @staticmethod
    def _evaluate_pod(pod) -> tuple[bool, str]:
        phase = pod.status.phase if pod.status else None

        if phase != "Running":
            return False, f"Phase atual: {phase or 'desconhecida'}"

        conditions = (pod.status.conditions or []) if pod.status else []
        ready_conditions = [c for c in conditions if c.type == "Ready"]

        if not ready_conditions:
            return False, "Condição Ready ausente"

        ready = ready_conditions[0].status == "True"
        if not ready:
            reason = ready_conditions[0].reason or "não-Ready"
            return False, f"Pod não está Ready: {reason}"

        return True, "Running e Ready"

    def _init_k8s_client(self) -> None:
        try:
            kubernetes.config.load_incluster_config()
            self.logger.info("Kubernetes in-cluster config carregada (CastAIHealthChecker)")
        except kubernetes.config.ConfigException:
            kubernetes.config.load_kube_config()
            self.logger.info("Kubernetes kubeconfig local carregada (CastAIHealthChecker)")