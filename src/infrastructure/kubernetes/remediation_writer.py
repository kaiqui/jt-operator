"""
infrastructure/kubernetes/remediation_writer.py

Creates AppRemediation CRDs to record every GitHub PR produced by the
auto-remediation flow.  One AppRemediation per PR, named:

    {deployment-name}-{yyyyMMddHHmmss}

The resource is owned by the Deployment so Kubernetes GC deletes it
automatically when the parent Deployment is removed.

Idempotency: creation is best-effort (one PR → one CRD).  If the write
fails the remediation itself is not rolled back — only the audit record
is missing.  The caller must log the failure but must NOT raise.
"""

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
    """
    Manages the lifecycle of AppRemediation custom resources.

    Synchronous, same rationale as AppScorecardWriter.
    """

    def __init__(self) -> None:
        self._api: Optional[client.CustomObjectsApi] = None

    @property
    def _custom_api(self) -> client.CustomObjectsApi:
        if self._api is None:
            self._api = client.CustomObjectsApi()
        return self._api

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def record(
        self,
        namespace: str,
        deployment_name: str,
        deployment_uid: str,
        pr_meta: Dict[str, Any],
        issues: List[Dict[str, str]],
    ) -> str:
        """
        Persist an AppRemediation CRD for a successfully created GitHub PR.

        Args:
            namespace:        Deployment namespace.
            deployment_name:  Deployment name (also used as label + targetRef).
            deployment_uid:   Deployment UID for ownerReference.
            pr_meta:          Dict with keys prNumber, prUrl, prBranch, createdAt,
                              issuesFixed (list of rule IDs).
            issues:           List of {"ruleId": ..., "ruleName": ..., "category": ...}
                              for the spec.issuesFixed field.

        Returns:
            Name of the created AppRemediation resource.

        Raises:
            ApiException if the Kubernetes API call fails.
        """
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

        # Patch the status subresource (CRD has status: {} subresource).
        self._patch_status(resource_name, namespace, body["status"])

        return resource_name

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
