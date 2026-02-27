from datetime import datetime, timezone
from typing import Dict, Any
from kubernetes.client import CustomObjectsApi
from kubernetes.client.rest import ApiException
from src.application.ports.status_writer import StatusWriter

GROUP = "titlis.io"
VERSION = "v1"
PLURAL = "sloconfigs"


class KubernetesStatusWriter(StatusWriter):
    def update(self, body: Dict[str, Any], status: Dict[str, Any]) -> None:
        metadata = body.get("metadata", {})
        name = metadata.get("name")
        namespace = metadata.get("namespace", "default")

        # Garante lastTransitionTime
        if "lastTransitionTime" not in status:
            status["lastTransitionTime"] = datetime.now(timezone.utc).isoformat()

        api = CustomObjectsApi()

        # Tenta atualizar com retry e controle de concorrência
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 1. Obtém o recurso atual para pegar a resourceVersion
                current_cr = api.get_namespaced_custom_object(
                    group=GROUP,
                    version=VERSION,
                    namespace=namespace,
                    plural=PLURAL,
                    name=name
                )

                # 2. Usa current_cr (já contém resourceVersion atualizado) como body mutável
                # Não modificamos o Body do kopf que é imutável
                current_cr['status'] = status

                api.replace_namespaced_custom_object_status(
                    group=GROUP,
                    version=VERSION,
                    namespace=namespace,
                    plural=PLURAL,
                    name=name,
                    body=current_cr
                )
                return
                
            except ApiException as exc:
                if exc.status == 409:  # Conflict
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(0.1 * (2 ** attempt))  # Exponential backoff
                        continue
                    else:
                        # Log mas não falha - deixa o Kopf lidar com o retry
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.warning(
                            f"Conflict ao atualizar status após {max_retries} tentativas",
                            extra={
                                "resource_name": name,
                                "namespace": namespace,
                                "attempt": attempt + 1
                            }
                        )
                        return
                elif exc.status == 404:
                    raise RuntimeError(
                        f"CR {name} not found in {namespace} ({GROUP}/{VERSION}/{PLURAL})"
                    ) from exc
                else:
                    raise