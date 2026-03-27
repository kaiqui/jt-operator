import re
from datetime import datetime, timezone
from io import StringIO
from typing import Any, Dict, List, Optional, Set, Tuple

from src.application.ports.github_port import GitHubPort
from src.settings import RemediationSettings
from src.application.services.slack_service import SlackNotificationService
from src.domain.github_models import (
    DatadogProfilingMetrics,
    PullRequestResult,
    RemediationFile,
    RemediationIssue,
    RemediationRequest,
    RemediationResult,
)
from src.domain.models import HPAProfile
from src.domain.slack_models import (
    NotificationChannel,
    NotificationSeverity,
)
from src.utils.json_logger import get_logger

logger = get_logger(__name__)


def _parse_cpu_millicores(value: str) -> int:
    v = str(value).strip()
    if v.endswith("m"):
        return int(float(v[:-1]))
    return int(float(v) * 1000)


def _parse_memory_mib(value: str) -> int:
    v = str(value).strip()
    if v.endswith("Gi"):
        return int(float(v[:-2]) * 1024)
    if v.endswith("Mi"):
        return int(float(v[:-2]))
    if v.endswith("G"):
        return int(float(v[:-1]) * 1024)
    if v.endswith("M"):
        return int(float(v[:-1]))
    if v.endswith("Ki"):
        return max(1, int(float(v[:-2]) // 1024))
    return max(1, int(float(v) / 1_048_576))


def _keep_max(current: str, suggested: str, parser: Any) -> str:
    try:
        return suggested if parser(suggested) >= parser(current) else current
    except (ValueError, TypeError):
        return suggested


def _extract_hpa_utilization(
    metrics: List[Dict[str, Any]], resource_name: str
) -> Optional[int]:
    for m in metrics or []:
        if m.get("type") == "Resource":
            resource = m.get("resource", {})
            if resource.get("name") == resource_name:
                target = resource.get("target", {})
                if target.get("type") == "Utilization":
                    val = target.get("averageUtilization")
                    return int(val) if val is not None else None
    return None


_HPA_RULE_IDS = frozenset({"RES-007", "RES-008", "PERF-002"})
_RESOURCE_RULE_IDS = frozenset({"RES-003", "RES-004", "RES-005", "RES-006", "PERF-001"})
REMEDIABLE_RULE_IDS = _HPA_RULE_IDS | _RESOURCE_RULE_IDS

DEPLOY_YAML_PATH = "manifests/kubernetes/main/deploy.yaml"
DD_GIT_REPO_ENV = "DD_GIT_REPOSITORY_URL"

_CRITICALITY_ANNOTATION = "titlis.io/criticality"


class ResourceRemediationAction:
    def __init__(self, settings: RemediationSettings) -> None:
        self._settings = settings
        self.logger = get_logger(self.__class__.__name__)

    def apply(
        self,
        document: Any,
        metrics: Optional[DatadogProfilingMetrics],
    ) -> bool:
        containers = (
            document.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        if not containers:
            return False

        container = containers[0]
        if "resources" not in container:
            container["resources"] = {}
        res = container["resources"]
        if "requests" not in res:
            res["requests"] = {}
        if "limits" not in res:
            res["limits"] = {}

        s = self._settings
        dm = metrics or DatadogProfilingMetrics()

        res["requests"]["cpu"] = _keep_max(
            res["requests"].get("cpu", "0m"),
            dm.suggest_cpu_request(default=s.default_cpu_request),
            _parse_cpu_millicores,
        )
        res["requests"]["memory"] = _keep_max(
            res["requests"].get("memory", "0Mi"),
            dm.suggest_memory_request(default=s.default_memory_request),
            _parse_memory_mib,
        )
        res["limits"]["cpu"] = _keep_max(
            res["limits"].get("cpu", "0m"),
            dm.suggest_cpu_limit(default=s.default_cpu_limit),
            _parse_cpu_millicores,
        )
        res["limits"]["memory"] = _keep_max(
            res["limits"].get("memory", "0Mi"),
            dm.suggest_memory_limit(default=s.default_memory_limit),
            _parse_memory_mib,
        )
        return True


class HPARemediationAction:
    def __init__(self, settings: RemediationSettings) -> None:
        self._settings = settings
        self.logger = get_logger(self.__class__.__name__)

    def apply_update(
        self,
        hpa_doc: Any,
        hpa_profile: HPAProfile,
    ) -> None:
        s = self._settings
        spec = hpa_doc.setdefault("spec", {})

        spec["minReplicas"] = max(spec.get("minReplicas", 0), s.hpa_min_replicas)
        spec["maxReplicas"] = max(spec.get("maxReplicas", 0), s.hpa_max_replicas)

        current_metrics = spec.get("metrics", [])
        current_cpu_util = _extract_hpa_utilization(current_metrics, "cpu")
        current_mem_util = _extract_hpa_utilization(current_metrics, "memory")
        cpu_util = (
            min(current_cpu_util, s.hpa_cpu_utilization)
            if current_cpu_util
            else s.hpa_cpu_utilization
        )
        mem_util = (
            min(current_mem_util, s.hpa_memory_utilization)
            if current_mem_util
            else s.hpa_memory_utilization
        )
        spec["metrics"] = _build_hpa_metrics_list(cpu_util, mem_util)

        if hpa_profile == HPAProfile.RIGID:
            spec["behavior"] = self._build_behavior_dict()

    def build_manifest(
        self,
        resource_name: str,
        namespace: str,
        resource_kind: str,
        hpa_profile: HPAProfile,
    ) -> Dict[str, Any]:
        s = self._settings
        manifest: Dict[str, Any] = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {
                "name": resource_name,
                "namespace": namespace,
                "annotations": {
                    "titlis.io/auto-generated": "true",
                    "titlis.io/generated-by": "titlis-operator-remediation",
                },
            },
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": resource_kind,
                    "name": resource_name,
                },
                "minReplicas": s.hpa_min_replicas,
                "maxReplicas": s.hpa_max_replicas,
                "metrics": _build_hpa_metrics_list(
                    s.hpa_cpu_utilization, s.hpa_memory_utilization
                ),
            },
        }
        if hpa_profile == HPAProfile.RIGID:
            manifest["spec"]["behavior"] = self._build_behavior_dict()
        return manifest

    def _build_behavior_dict(self) -> Dict[str, Any]:
        s = self._settings
        return {
            "scaleUp": {
                "stabilizationWindowSeconds": s.hpa_behavior_scale_up_stabilization,
                "policies": [
                    {
                        "type": "Pods",
                        "value": s.hpa_behavior_scale_up_pods,
                        "periodSeconds": s.hpa_behavior_scale_up_period,
                    },
                    {
                        "type": "Percent",
                        "value": s.hpa_behavior_scale_up_percent,
                        "periodSeconds": s.hpa_behavior_scale_up_period,
                    },
                ],
                "selectPolicy": "Max",
            },
            "scaleDown": {
                "stabilizationWindowSeconds": s.hpa_behavior_scale_down_stabilization,
                "policies": [
                    {
                        "type": "Pods",
                        "value": s.hpa_behavior_scale_down_pods,
                        "periodSeconds": s.hpa_behavior_scale_down_period,
                    },
                ],
            },
        }


def _build_hpa_metrics_list(
    cpu_utilization: int, memory_utilization: int
) -> List[Dict[str, Any]]:
    return [
        {
            "type": "Resource",
            "resource": {
                "name": "cpu",
                "target": {
                    "type": "Utilization",
                    "averageUtilization": cpu_utilization,
                },
            },
        },
        {
            "type": "Resource",
            "resource": {
                "name": "memory",
                "target": {
                    "type": "Utilization",
                    "averageUtilization": memory_utilization,
                },
            },
        },
    ]


class RemediationService:
    def __init__(
        self,
        github_port: GitHubPort,
        slack_service: Optional[SlackNotificationService] = None,
        datadog_repository: Optional[Any] = None,
        remediation_settings: Optional[RemediationSettings] = None,
        titlis_api_client: Optional[Any] = None,
    ) -> None:
        self._github = github_port
        self._slack = slack_service
        self._datadog = datadog_repository
        self._remediation_settings = remediation_settings or RemediationSettings()
        self._titlis_api_client = titlis_api_client
        self.logger = get_logger(self.__class__.__name__)
        self._pending: Set[str] = set()
        self._resource_action = ResourceRemediationAction(self._remediation_settings)
        self._hpa_action = HPARemediationAction(self._remediation_settings)

    async def create_remediation_pr(
        self, request: RemediationRequest
    ) -> RemediationResult:
        repo_info = self._extract_git_repo(request.resource_body)
        if not repo_info:
            await self._emit_remediation_event(
                request=request,
                status="SKIPPED",
                previous_status="PENDING",
                error=(
                    f"Env {DD_GIT_REPO_ENV} nao encontrada no Deployment "
                    f"'{request.resource_name}' — remediacao ignorada"
                ),
            )
            self.logger.info(
                "Remediacao ignorada: DD_GIT_REPOSITORY_URL ausente no Deployment",
                extra={"resource": f"{request.namespace}/{request.resource_name}"},
            )
            return RemediationResult(
                success=False,
                error=(
                    f"Env {DD_GIT_REPO_ENV} nao encontrada no Deployment "
                    f"'{request.resource_name}' — remediacao ignorada"
                ),
            )

        repo_owner, repo_name = repo_info

        resource_key = self._resource_key(
            repo_owner, repo_name, request.namespace, request.resource_name
        )
        if resource_key in self._pending:
            await self._emit_remediation_event(
                request=request,
                status="SKIPPED",
                previous_status="PENDING",
                error=f"Remediacao ja em andamento para '{resource_key}'",
            )
            self.logger.info(
                "Remediacao ignorada: ja em andamento para este recurso",
                extra={"resource": f"{request.namespace}/{request.resource_name}"},
            )
            return RemediationResult(
                success=False,
                error=f"Remediacao ja em andamento para '{resource_key}'",
            )

        existing_pr = await self._github.find_open_remediation_pr(
            repo_owner=repo_owner,
            repo_name=repo_name,
            namespace=request.namespace,
            resource_name=request.resource_name,
            base_branch=request.base_branch,
        )
        if existing_pr:
            await self._emit_remediation_event(
                request=request,
                status="SKIPPED",
                previous_status="PENDING",
                github_pr_number=existing_pr.number,
                github_pr_title=existing_pr.title,
                github_pr_url=existing_pr.url,
                github_branch=existing_pr.branch,
                error=(
                    f"PR de remediacao ja existe: "
                    f"#{existing_pr.number} — {existing_pr.url}"
                ),
                repository_url=self._repository_url(repo_owner, repo_name),
            )
            self.logger.info(
                "PR de remediacao ja existe aberta — remediacao ignorada",
                extra={
                    "resource": f"{request.namespace}/{request.resource_name}",
                    "pr_number": existing_pr.number,
                    "pr_url": existing_pr.url,
                },
            )
            return RemediationResult(
                success=False,
                error=(
                    f"PR de remediacao ja existe: "
                    f"#{existing_pr.number} — {existing_pr.url}"
                ),
                pull_request=existing_pr,
            )

        merged_pr = await self._github.find_merged_remediation_pr(
            repo_owner=repo_owner,
            repo_name=repo_name,
            namespace=request.namespace,
            resource_name=request.resource_name,
            base_branch=request.base_branch,
        )
        if merged_pr:
            await self._emit_remediation_event(
                request=request,
                status="SKIPPED",
                previous_status="PENDING",
                github_pr_number=merged_pr.number,
                github_pr_title=merged_pr.title,
                github_pr_url=merged_pr.url,
                github_branch=merged_pr.branch,
                error=(
                    f"PR de remediacao ja foi mergeada: "
                    f"#{merged_pr.number} — {merged_pr.url}"
                ),
                repository_url=self._repository_url(repo_owner, repo_name),
            )
            self.logger.info(
                "PR de remediacao ja foi mergeada — remediacao ignorada",
                extra={
                    "resource": f"{request.namespace}/{request.resource_name}",
                    "pr_number": merged_pr.number,
                    "pr_url": merged_pr.url,
                },
            )
            return RemediationResult(
                success=False,
                error=(
                    f"PR de remediacao ja foi mergeada: "
                    f"#{merged_pr.number} — {merged_pr.url}"
                ),
                pull_request=merged_pr,
            )

        self._pending.add(resource_key)
        try:
            return await self._execute_remediation(request, repo_owner, repo_name)
        finally:
            self._pending.discard(resource_key)

    async def _execute_remediation(
        self,
        request: RemediationRequest,
        repo_owner: str,
        repo_name: str,
    ) -> RemediationResult:
        self.logger.info(
            "Remediacao iniciada",
            extra={
                "resource": f"{request.namespace}/{request.resource_name}",
                "kind": request.resource_kind,
                "issues_count": len(request.issues),
                "issue_ids": [i.rule_id for i in request.issues],
                "target_repo": f"{repo_owner}/{repo_name}",
            },
        )
        await self._emit_remediation_event(
            request=request,
            status="IN_PROGRESS",
            previous_status="PENDING",
            repository_url=self._repository_url(repo_owner, repo_name),
        )

        metrics = self._fetch_profiling_metrics(
            request.resource_name, request.namespace
        )
        await self._emit_resource_metrics(request, metrics)
        hpa_profile = self._detect_hpa_profile(
            request.resource_body, request.resource_name
        )

        current_content = await self._github.get_file_content(
            repo_owner=repo_owner,
            repo_name=repo_name,
            file_path=DEPLOY_YAML_PATH,
            ref=request.base_branch,
        )

        modified_content, categories = self._modify_deploy_yaml(
            content=current_content or "",
            issues=request.issues,
            metrics=metrics,
            resource_name=request.resource_name,
            namespace=request.namespace,
            resource_kind=request.resource_kind,
            hpa_profile=hpa_profile,
        )

        if not modified_content:
            await self._emit_remediation_event(
                request=request,
                status="FAILED",
                previous_status="IN_PROGRESS",
                error="Nenhuma modificacao gerada para o deploy.yaml",
                repository_url=self._repository_url(repo_owner, repo_name),
            )
            return RemediationResult(
                success=False,
                error="Nenhuma modificacao gerada para o deploy.yaml",
            )

        branch_name = self._build_branch_name(request)
        deploy_file = RemediationFile(
            path=DEPLOY_YAML_PATH,
            content=modified_content,
            commit_message=self._build_commit_message(request, categories),
        )

        created = await self._github.create_branch(
            repo_owner=repo_owner,
            repo_name=repo_name,
            branch_name=branch_name,
            base_branch=request.base_branch,
        )
        if not created:
            await self._emit_remediation_event(
                request=request,
                status="FAILED",
                previous_status="IN_PROGRESS",
                github_branch=branch_name,
                error=f"Falha ao criar branch '{branch_name}'",
                repository_url=self._repository_url(repo_owner, repo_name),
            )
            return RemediationResult(
                success=False,
                branch_name=branch_name,
                error=f"Falha ao criar branch '{branch_name}'",
            )

        committed = await self._github.commit_files(
            repo_owner=repo_owner,
            repo_name=repo_name,
            branch_name=branch_name,
            files=[deploy_file],
        )
        if not committed:
            await self._emit_remediation_event(
                request=request,
                status="FAILED",
                previous_status="IN_PROGRESS",
                github_branch=branch_name,
                error="Falha ao commitar as modificacoes no deploy.yaml",
                repository_url=self._repository_url(repo_owner, repo_name),
            )
            return RemediationResult(
                success=False,
                branch_name=branch_name,
                error="Falha ao commitar as modificacoes no deploy.yaml",
            )

        try:
            pr = await self._github.create_pull_request(
                repo_owner=repo_owner,
                repo_name=repo_name,
                branch_name=branch_name,
                base_branch=request.base_branch,
                title=self._build_pr_title(request, categories),
                body=self._build_pr_body(request, categories, metrics),
            )
        except Exception as exc:
            self.logger.exception("Erro ao criar Pull Request")
            await self._emit_remediation_event(
                request=request,
                status="FAILED",
                previous_status="IN_PROGRESS",
                github_branch=branch_name,
                error=f"Falha ao criar Pull Request: {exc}",
                repository_url=self._repository_url(repo_owner, repo_name),
            )
            return RemediationResult(
                success=False,
                branch_name=branch_name,
                error=f"Falha ao criar Pull Request: {exc}",
            )

        pr.issues_fixed = [i.rule_id for i in request.issues]

        await self._notify_slack(request, pr, categories, metrics)
        await self._emit_remediation_event(
            request=request,
            status="PR_OPEN",
            previous_status="IN_PROGRESS",
            github_pr_number=pr.number,
            github_pr_title=pr.title,
            github_pr_url=pr.url,
            github_branch=pr.branch,
            repository_url=self._repository_url(repo_owner, repo_name),
        )

        self.logger.info(
            "Remediacao concluida com sucesso",
            extra={"pr_number": pr.number, "pr_url": pr.url, "branch": branch_name},
        )
        return RemediationResult(success=True, pull_request=pr, branch_name=branch_name)

    def _extract_git_repo(
        self, resource_body: Dict[str, Any]
    ) -> Optional[Tuple[str, str]]:
        containers: List[Dict[str, Any]] = (
            resource_body.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        for container in containers:
            for env_var in container.get("env", []):
                if env_var.get("name") == DD_GIT_REPO_ENV:
                    url = env_var.get("value", "")
                    parsed = self._parse_github_url(url)
                    if parsed:
                        return parsed
        return None

    @staticmethod
    def _parse_github_url(url: str) -> Optional[Tuple[str, str]]:
        url = url.strip().rstrip("/").removesuffix(".git")
        match = re.search(r"github\.com[/:]([^/]+)/([^/]+)$", url)
        if match:
            return match.group(1), match.group(2)
        return None

    @staticmethod
    def _resource_key(
        repo_owner: str, repo_name: str, namespace: str, resource_name: str
    ) -> str:
        return f"{repo_owner}/{repo_name}:{namespace}/{resource_name}"

    def _detect_hpa_profile(
        self,
        resource_body: Dict[str, Any],
        resource_name: str,
    ) -> HPAProfile:
        annotations = resource_body.get("metadata", {}).get("annotations") or {}
        if annotations.get(_CRITICALITY_ANNOTATION) == "high":
            self.logger.info(
                "Perfil HPA RIGID detectado via annotation",
                extra={"resource": resource_name},
            )
            return HPAProfile.RIGID

        if self._datadog:
            try:
                threshold = self._remediation_settings.hpa_critical_threshold_rpm
                count = self._datadog.get_request_count(resource_name, days=30)
                if count is not None and count > threshold:
                    self.logger.info(
                        "Perfil HPA RIGID detectado via Datadog request count",
                        extra={
                            "resource": resource_name,
                            "count": count,
                            "threshold": threshold,
                        },
                    )
                    return HPAProfile.RIGID
            except Exception:
                self.logger.warning(
                    "Falha ao detectar criticidade via Datadog — usando perfil LIGHT",
                    extra={"resource": resource_name},
                )

        return HPAProfile.LIGHT

    def _fetch_profiling_metrics(
        self, deployment_name: str, namespace: str
    ) -> Optional[DatadogProfilingMetrics]:
        if not self._datadog:
            return None
        try:
            result: Optional[
                DatadogProfilingMetrics
            ] = self._datadog.get_container_metrics(deployment_name, namespace)
            return result
        except Exception:
            self.logger.warning(
                "Falha ao coletar metricas de profiling do Datadog",
                extra={"deployment": deployment_name, "namespace": namespace},
            )
            return None

    def _modify_deploy_yaml(
        self,
        content: str,
        issues: List[RemediationIssue],
        metrics: Optional[DatadogProfilingMetrics],
        resource_name: str,
        namespace: str,
        resource_kind: str,
        hpa_profile: HPAProfile = HPAProfile.LIGHT,
    ) -> Tuple[str, List[str]]:
        try:
            from ruamel.yaml import YAML

            ryaml = YAML()
            ryaml.preserve_quotes = True
            ryaml.width = 10_000

            documents: List[Any] = []
            if content:
                for doc in ryaml.load_all(content):
                    if doc is not None:
                        documents.append(doc)

            hpa_issues = [i for i in issues if i.rule_id in _HPA_RULE_IDS]
            resource_issues = [i for i in issues if i.rule_id in _RESOURCE_RULE_IDS]
            categories: List[str] = []

            deployment_doc = next(
                (d for d in documents if d.get("kind") == "Deployment"), None
            )
            hpa_doc = next(
                (d for d in documents if d.get("kind") == "HorizontalPodAutoscaler"),
                None,
            )

            s = self._remediation_settings

            # ── Ação: resources (feature flag) ─────────────────────────────────
            if (
                resource_issues
                and s.enable_remediation_resources
                and deployment_doc is not None
            ):
                if self._resource_action.apply(deployment_doc, metrics):
                    categories.append("resources")

            # ── Ação: HPA (feature flag) ────────────────────────────────────────
            if hpa_issues and s.enable_remediation_hpa:
                if hpa_doc is not None:
                    self._hpa_action.apply_update(hpa_doc, hpa_profile)
                    categories.append("hpa-update")
                else:
                    import yaml as stdlib_yaml

                    hpa_manifest = self._hpa_action.build_manifest(
                        resource_name, namespace, resource_kind, hpa_profile
                    )
                    hpa_yaml_str = "\n---\n" + stdlib_yaml.dump(
                        hpa_manifest, default_flow_style=False, allow_unicode=True
                    )
                    categories.append("hpa-create")

                    stream = StringIO()
                    if documents:
                        ryaml.dump_all(documents, stream)
                    return stream.getvalue() + hpa_yaml_str, categories

            if not categories:
                return "", []

            stream = StringIO()
            ryaml.dump_all(documents, stream)
            return stream.getvalue(), categories

        except Exception:
            self.logger.exception("Erro ao modificar deploy.yaml com ruamel.yaml")
            return "", []

    def _build_branch_name(self, request: RemediationRequest) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        safe_name = request.resource_name.replace("/", "-")
        return f"fix/auto-remediation-{request.namespace}-{safe_name}-{timestamp}"

    def _build_commit_message(
        self, request: RemediationRequest, categories: List[str]
    ) -> str:
        cats = "+".join(categories) if categories else "misc"
        return (
            f"fix({cats}): auto-remediacao em "
            f"{request.namespace}/{request.resource_name} [titlis-operator]"
        )

    def _build_pr_title(
        self, request: RemediationRequest, categories: List[str]
    ) -> str:
        cats_str = ", ".join(categories).upper() if categories else "MISC"
        return (
            f"fix({request.namespace}/{request.resource_name}): "
            f"{cats_str} — {len(request.issues)} issue(s)"
        )

    def _build_pr_body(
        self,
        request: RemediationRequest,
        categories: List[str],
        metrics: Optional[DatadogProfilingMetrics],
    ) -> str:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        dm = metrics or DatadogProfilingMetrics()

        cat_rows: List[str] = []
        for cat in categories:
            rules = (
                ", ".join(
                    i.rule_id for i in request.issues if i.rule_id in _HPA_RULE_IDS
                )
                if "hpa" in cat
                else ", ".join(
                    i.rule_id for i in request.issues if i.rule_id in _RESOURCE_RULE_IDS
                )
            )
            label = (
                "HPA (Auto Scaling)" if "hpa" in cat else "Resources (Requests/Limits)"
            )
            cat_rows.append(f"| {label} | {rules} | `{DEPLOY_YAML_PATH}` |")
        categories_table = "\n".join(cat_rows) if cat_rows else "| — | — | — |"

        if metrics:
            metrics_table = (
                "| Metrica | Media (1h) | Request Sugerido | Limit Sugerido |\n"
                "|---|---|---|---|\n"
                f"| CPU | {metrics.cpu_avg_millicores or 'N/D'}m"
                f" | {dm.suggest_cpu_request()} | {dm.suggest_cpu_limit()} |\n"
                f"| Memoria | {metrics.memory_avg_mib or 'N/D'}Mi"
                f" | {dm.suggest_memory_request()} | {dm.suggest_memory_limit()} |"
            )
        else:
            metrics_table = (
                "> Metricas do Datadog indisponiveis — valores padrao utilizados.\n"
                "> Revise e ajuste conforme o uso real da aplicacao."
            )

        issues_md = "\n".join(
            f"- **{i.rule_id}** — {i.rule_name}: {i.description}"
            for i in request.issues
        )

        return (
            f"> [!WARNING]\n"
            f"> **Este PR foi gerado pelo servico titlis-operator.**\n"
            f"> **Revisao humana e obrigatoria antes do merge.**\n\n"
            f"---\n\n"
            f"## Auto-Remediacao: `{request.namespace}/{request.resource_name}`"
            f" ({request.resource_kind})\n\n"
            f"### Categorias das Modificacoes\n\n"
            f"| Categoria | Regras Corrigidas | Arquivo |\n"
            f"|---|---|---|\n"
            f"{categories_table}\n\n"
            f"### Metricas Coletadas do Datadog\n\n"
            f"{metrics_table}\n\n"
            f"### Issues Detectadas\n\n"
            f"{issues_md}\n\n"
            f"### Arquivo Modificado\n\n"
            f"- `{DEPLOY_YAML_PATH}`\n\n"
            f"### Checklist de Revisao\n\n"
            f"- [ ] Verificar valores de CPU e memoria sugeridos vs uso real da aplicacao\n"
            f"- [ ] Confirmar configuracao do HPA (minReplicas, maxReplicas, target)\n"
            f"- [ ] Testar em ambiente de staging antes do merge\n"
            f"- [ ] Validar compatibilidade das modificacoes com a aplicacao\n\n"
            f"---\n"
            f"*Gerado pelo titlis-operator em {now_iso}*  \n"
            f"*Baseado em metricas de profiling coletadas do Datadog*"
        )

    async def _notify_slack(
        self,
        request: RemediationRequest,
        pr: PullRequestResult,
        categories: List[str],
        metrics: Optional[DatadogProfilingMetrics],
    ) -> None:
        if not self._slack or not self._slack.is_enabled():
            return

        cats_str = " + ".join(c.upper() for c in categories) if categories else "MISC"
        issues_text = "\n".join(
            f"• *{i.rule_id}*: {i.rule_name}" for i in request.issues
        )

        metrics_line = ""
        if metrics:
            metrics_line = (
                f"\n*Metricas Datadog:* CPU avg={metrics.cpu_avg_millicores}m,"
                f" MEM avg={metrics.memory_avg_mib}Mi"
            )

        additional_fields: List[Dict[str, str]] = [
            {"title": "PR URL", "value": pr.url, "short": "false"},
            {"title": "PR Number", "value": str(pr.number), "short": "true"},
            {"title": "Branch", "value": pr.branch, "short": "true"},
            {
                "title": "Resource",
                "value": f"{request.namespace}/{request.resource_name}",
                "short": "true",
            },
            {"title": "Categories", "value": cats_str, "short": "true"},
            {"title": "Generated By", "value": "titlis-operator", "short": "true"},
        ]
        title = f"Auto-Remediacao PR Criado — {cats_str}"
        message = (
            f"*Recurso:* `{request.namespace}/{request.resource_name}`"
            f" ({request.resource_kind})\n"
            f"*Categorias:* {cats_str}\n"
            f"*Branch:* `{pr.branch}` -> `{pr.base_branch}`\n"
            f"*Issues ({len(request.issues)}):*\n{issues_text}"
            f"{metrics_line}\n"
            f"*PR:* <{pr.url}|#{pr.number} — revisao obrigatoria>"
        )

        try:
            success = await self._slack.send_notification(
                title=title,
                message=message,
                severity=NotificationSeverity.WARNING,
                channel=NotificationChannel.OPERATIONAL,
                namespace=request.namespace,
                additional_fields=additional_fields,
            )
            await self._emit_notification_log(
                namespace=request.namespace,
                notification_type="remediation",
                severity=NotificationSeverity.WARNING,
                channel=NotificationChannel.OPERATIONAL,
                title=title,
                message=message,
                success=success,
                workload_id=request.resource_body.get("metadata", {}).get("uid"),
            )
            self.logger.info(
                "Notificacao Slack de remediacao enviada",
                extra={"pr_number": pr.number},
            )
        except Exception:
            self.logger.exception("Falha ao enviar notificacao Slack de remediacao")
            await self._emit_notification_log(
                namespace=request.namespace,
                notification_type="remediation",
                severity=NotificationSeverity.WARNING,
                channel=NotificationChannel.OPERATIONAL,
                title=title,
                message=message,
                success=False,
                workload_id=request.resource_body.get("metadata", {}).get("uid"),
                error_message="Falha ao enviar notificacao Slack de remediacao",
            )

    async def _emit_remediation_event(
        self,
        request: RemediationRequest,
        status: str,
        previous_status: Optional[str] = None,
        github_pr_number: Optional[int] = None,
        github_pr_title: Optional[str] = None,
        github_pr_url: Optional[str] = None,
        github_branch: Optional[str] = None,
        error: Optional[str] = None,
        repository_url: Optional[str] = None,
    ) -> None:
        if not self._titlis_api_client:
            return

        try:
            await self._titlis_api_client.send_remediation_event(
                {
                    "workload_id": request.resource_body.get("metadata", {}).get(
                        "uid", ""
                    ),
                    "namespace": request.namespace,
                    "workload": request.resource_name,
                    "status": status,
                    "previous_status": previous_status,
                    "version": 1,
                    "github_pr_title": github_pr_title,
                    "github_pr_number": github_pr_number,
                    "github_pr_url": github_pr_url,
                    "github_branch": github_branch,
                    "repository_url": repository_url,
                    "issues_snapshot": [
                        {
                            "rule_id": issue.rule_id,
                            "rule_name": issue.rule_name,
                            "category": issue.category.value,
                            "description": issue.description,
                            "remediation": issue.remediation,
                        }
                        for issue in request.issues
                    ],
                    "error_message": error,
                    "triggered_at": datetime.now(timezone.utc).isoformat(),
                    "resolved_at": datetime.now(timezone.utc).isoformat()
                    if status in {"FAILED", "PR_OPEN", "SKIPPED"}
                    else None,
                }
            )
        except Exception:
            self.logger.exception("Falha ao enviar evento de remediação para a API")

    async def _emit_resource_metrics(
        self,
        request: RemediationRequest,
        metrics: Optional[DatadogProfilingMetrics],
    ) -> None:
        if not self._titlis_api_client or not metrics:
            return

        containers = (
            request.resource_body.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        container_name = containers[0].get("name") if containers else None

        try:
            await self._titlis_api_client.send_resource_metrics(
                {
                    "workload_id": request.resource_body.get("metadata", {}).get(
                        "uid", ""
                    ),
                    "namespace": request.namespace,
                    "workload": request.resource_name,
                    "container_name": container_name,
                    "cpu_avg_millicores": metrics.cpu_avg_millicores,
                    "mem_avg_mib": metrics.memory_avg_mib,
                    "suggested_cpu_request": metrics.suggest_cpu_request(
                        default=self._remediation_settings.default_cpu_request
                    ),
                    "suggested_cpu_limit": metrics.suggest_cpu_limit(
                        default=self._remediation_settings.default_cpu_limit
                    ),
                    "suggested_mem_request": metrics.suggest_memory_request(
                        default=self._remediation_settings.default_memory_request
                    ),
                    "suggested_mem_limit": metrics.suggest_memory_limit(
                        default=self._remediation_settings.default_memory_limit
                    ),
                    "sample_window": "1h",
                }
            )
        except Exception:
            self.logger.exception("Falha ao enviar métricas de recurso para a API")

    async def _emit_notification_log(
        self,
        namespace: str,
        notification_type: str,
        severity: NotificationSeverity,
        channel: NotificationChannel,
        title: str,
        message: str,
        success: bool,
        workload_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        if not self._titlis_api_client:
            return

        try:
            await self._titlis_api_client.send_notification_log(
                {
                    "workload_id": workload_id,
                    "namespace": namespace,
                    "notification_type": notification_type,
                    "severity": severity.value.upper(),
                    "channel": channel.value,
                    "title": title,
                    "message_preview": message[:500],
                    "success": success,
                    "error_message": error_message,
                }
            )
        except Exception:
            self.logger.exception("Falha ao enviar log de notificação para a API")

    @staticmethod
    def _repository_url(repo_owner: str, repo_name: str) -> str:
        return f"https://github.com/{repo_owner}/{repo_name}"
