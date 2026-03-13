from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes import client
from kubernetes.client.rest import ApiException

from src.utils.json_logger import get_logger

GROUP = "titlis.io"
VERSION = "v1"
PLURAL = "appremediations"

logger = get_logger(__name__)


class RemediationWriter:
    def __init__(self) -> None:
        self._api: Optional[client.CustomObjectsApi] = None

    @property
    def _custom_api(self) -> client.CustomObjectsApi:
        if self._api is None:
            self._api = client.CustomObjectsApi()
        return self._api

    def record(
        self,
        namespace: str,
        deployment_name: str,
        deployment_uid: str,
        pr_meta: Dict[str, Any],
        issues: List[Dict[str, str]],
    ) -> str:
        now = datetime.now(timezone.utc)
        resource_name = f"{deployment_name}-{now.strftime('%Y%m%d%H%M%S')}"

        body = self._build_body(
            resource_name=resource_name,
            namespace=namespace,
            deployment_name=deployment_name,
            deployment_uid=deployment_uid,
            pr_meta=pr_meta,
            issues=issues,
            now=now,
        )

        self._custom_api.create_namespaced_custom_object(
            group=GROUP,
            version=VERSION,
            namespace=namespace,
            plural=PLURAL,
            body=body,
        )
        logger.info(
            "AppRemediation criado",
            extra={
                "resource_name": resource_name,
                "namespace": namespace,
                "pr_number": pr_meta.get("prNumber"),
                "issue_count": len(issues),
            },
        )

        self._patch_status(resource_name, namespace, body["status"])

        return resource_name

    def _patch_status(
        self,
        resource_name: str,
        namespace: str,
        status: Dict[str, Any],
    ) -> None:
        try:
            existing = self._custom_api.get_namespaced_custom_object(
                group=GROUP,
                version=VERSION,
                namespace=namespace,
                plural=PLURAL,
                name=resource_name,
            )
            existing["status"] = status
            self._custom_api.replace_namespaced_custom_object_status(
                group=GROUP,
                version=VERSION,
                namespace=namespace,
                plural=PLURAL,
                name=resource_name,
                body=existing,
            )
        except ApiException:
            logger.warning(
                "Falha ao atualizar status do AppRemediation",
                extra={"resource_name": resource_name, "namespace": namespace},
            )

    def _build_body(
        self,
        resource_name: str,
        namespace: str,
        deployment_name: str,
        deployment_uid: str,
        pr_meta: Dict[str, Any],
        issues: List[Dict[str, str]],
        now: datetime,
    ) -> Dict[str, Any]:
        return {
            "apiVersion": f"{GROUP}/{VERSION}",
            "kind": "AppRemediation",
            "metadata": {
                "name": resource_name,
                "namespace": namespace,
                "labels": {
                    "app.kubernetes.io/managed-by": "titlis-operator",
                    "titlis.io/deployment": deployment_name,
                },
                "ownerReferences": [
                    {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "name": deployment_name,
                        "uid": deployment_uid,
                        "blockOwnerDeletion": True,
                        "controller": False,
                    }
                ],
            },
            "spec": {
                "targetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": deployment_name,
                    "namespace": namespace,
                },
                "issuesFixed": issues,
                "baseBranch": pr_meta.get("prBranch", ""),
            },
            "status": {
                "phase": "PRCreated",
                "prNumber": pr_meta.get("prNumber"),
                "prUrl": pr_meta.get("prUrl"),
                "prBranch": pr_meta.get("prBranch"),
                "issueCount": len(issues),
                "createdAt": pr_meta.get("createdAt", now.isoformat()),
                "updatedAt": now.isoformat(),
            },
        }
