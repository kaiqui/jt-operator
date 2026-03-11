from typing import Tuple
from kubernetes import client, config
from src.utils.json_logger import get_logger

logger = get_logger(__name__)


def get_k8s_apis() -> (
    Tuple[client.CoreV1Api, client.AppsV1Api, client.CustomObjectsApi]
):
    try:
        config.load_incluster_config()
        logger.debug("Kubernetes in-cluster config loaded")
    except Exception:
        config.load_kube_config()
        logger.debug("Kubernetes local kubeconfig loaded")

    core = client.CoreV1Api()
    apps = client.AppsV1Api()
    custom = client.CustomObjectsApi()
    return core, apps, custom
