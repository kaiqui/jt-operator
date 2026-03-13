from typing import Optional, Dict
import threading

from kubernetes import client
from kubernetes.client.rest import ApiException
from src.infrastructure.kubernetes.client import get_k8s_apis
from src.utils.json_logger import get_logger

logger = get_logger(__name__)


class KubeStateStore:
    def __init__(self, namespace: str, name: str = "titlis-state"):
        self.namespace = namespace
        self.name = name
        self.core, _, _ = get_k8s_apis()
        self._cache: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._ensure_cm()

    def _ensure_cm(self) -> None:
        try:
            self.core.read_namespaced_config_map(
                name=self.name, namespace=self.namespace
            )
        except ApiException as e:
            if e.status == 404:
                cm = client.V1ConfigMap(
                    metadata=client.V1ObjectMeta(name=self.name), data={}
                )
                try:
                    self.core.create_namespaced_config_map(
                        namespace=self.namespace, body=cm
                    )
                    logger.info(
                        "ConfigMap de state store criado",
                        extra={"name": self.name, "namespace": self.namespace},
                    )
                except ApiException as ex:
                    logger.error(
                        "Erro criando configmap de state store",
                        extra={"exception": str(ex)},
                    )
            else:
                logger.error(
                    "Erro assegurando configmap state store",
                    extra={"status": e.status, "reason": e.reason},
                )

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            try:
                cm = self.core.read_namespaced_config_map(
                    name=self.name, namespace=self.namespace
                )
                if cm.data and key in cm.data:
                    self._cache[key] = cm.data[key]
                    return str(cm.data[key])
            except ApiException as e:
                logger.debug("Erro lendo configmap")
            return None

    def set(self, key: str, value: str) -> None:
        with self._lock:
            # Atualiza cache
            self._cache[key] = value

            try:
                # 1. Obtém o ConfigMap atual
                cm = self.core.read_namespaced_config_map(
                    name=self.name, namespace=self.namespace
                )

                # 2. Atualiza os dados
                if cm.data is None:
                    cm.data = {}
                cm.data[key] = value

                # 3. Usa replace_namespaced_config_map em vez de patch
                self.core.replace_namespaced_config_map(
                    name=self.name, namespace=self.namespace, body=cm
                )

                logger.debug(
                    "ConfigMap atualizado com sucesso",
                    extra={"key": key, "namespace": self.namespace},
                )

            except ApiException as e:
                if e.status == 404:
                    # Cria novo ConfigMap
                    cm = client.V1ConfigMap(
                        metadata=client.V1ObjectMeta(
                            name=self.name, namespace=self.namespace
                        ),
                        data={key: value},
                    )
                    try:
                        self.core.create_namespaced_config_map(
                            namespace=self.namespace, body=cm
                        )
                        logger.info(
                            "ConfigMap criado no set",
                            extra={"key": key, "namespace": self.namespace},
                        )
                    except ApiException as ex:
                        logger.error(
                            "Erro criando configmap no set",
                            extra={"exception": str(ex)},
                        )
                        raise
                else:
                    logger.error(
                        "Erro atualizando configmap no set",
                        extra={
                            "status": e.status,
                            "reason": e.reason,
                            "body": e.body if e.body else "N/A",
                        },
                    )
