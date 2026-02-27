"""
Serviço de remediação automática de HPA e Resources.

Fluxo ao detectar issues remediáveis no scorecard:
  1. Verifica se o Deployment tem a env DD_GIT_REPOSITORY_URL
     (pré-condição obrigatória — sem ela, remediação não é realizada)
  2. Extrai owner/repo da URL para identificar o repositório da app
  3. Coleta métricas de profiling (CPU/memória) do Datadog para embasar os valores
  4. Lê manifests/kubernetes/main/deploy.yaml no repositório da app
  5. Modifica APENAS as seções necessárias, preservando comentários (ruamel.yaml)
  6. Cria uma branch a partir da 'develop'
  7. Commita o deploy.yaml modificado
  8. Abre Pull Request para 'develop' com categorização, disclaimer de IA e checklist
  9. Notifica o canal Slack
"""
import re
from datetime import datetime, timezone
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple

from src.application.ports.github_port import GitHubPort
from src.application.services.slack_service import SlackNotificationService
from src.domain.github_models import (
    DatadogProfilingMetrics,
    PullRequestResult,
    RemediationFile,
    RemediationIssue,
    RemediationRequest,
    RemediationResult,
    RemediationRuleCategory,
)
from src.domain.slack_models import (
    NotificationChannel,
    NotificationSeverity,
    SlackNotification,
)
from src.utils.json_logger import get_logger

logger = get_logger(__name__)

# IDs de regras que podem ser remediadas automaticamente
_HPA_RULE_IDS = frozenset({"RES-007", "RES-008", "PERF-002"})
_RESOURCE_RULE_IDS = frozenset({"RES-003", "RES-004", "RES-005", "RES-006", "PERF-001"})
REMEDIABLE_RULE_IDS = _HPA_RULE_IDS | _RESOURCE_RULE_IDS

# Caminho fixo do manifesto no repositório da aplicação
DEPLOY_YAML_PATH = "manifests/kubernetes/main/deploy.yaml"

# Env var que indica o repositório Git da aplicação (obrigatório para remediação)
DD_GIT_REPO_ENV = "DD_GIT_REPOSITORY_URL"


class RemediationService:
    """
    Orquestra a remediação automática de HPA e resources.

    Só atua em Deployments que possuem DD_GIT_REPOSITORY_URL — o valor
    dessa env indica o repositório que contém os manifestos da aplicação.
    """

    def __init__(
        self,
        github_port: GitHubPort,
        slack_service: Optional[SlackNotificationService] = None,
        datadog_repository: Optional[Any] = None,  # DatadogRepository
    ) -> None:
        self._github = github_port
        self._slack = slack_service
        self._datadog = datadog_repository
        self.logger = get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # Ponto de entrada principal
    # ------------------------------------------------------------------

    async def create_remediation_pr(
        self, request: RemediationRequest
    ) -> RemediationResult:
        """
        Executa o fluxo completo de remediação para o recurso informado.

        Retorna RemediationResult com success=True e dados do PR criado,
        ou success=False com mensagem de erro descritivo.
        """
        # 1. Valida pré-condição: DD_GIT_REPOSITORY_URL obrigatório
        repo_info = self._extract_git_repo(request.resource_body)
        if not repo_info:
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

        # 2. Coleta métricas do Datadog (opcional — falha silenciosa)
        metrics = self._fetch_profiling_metrics(request.resource_name, request.namespace)

        # 3. Lê o deploy.yaml atual do repositório
        current_content = await self._github.get_file_content(
            repo_owner=repo_owner,
            repo_name=repo_name,
            file_path=DEPLOY_YAML_PATH,
            ref=request.base_branch,
        )

        # 4. Gera o conteúdo modificado do deploy.yaml
        modified_content, categories = self._modify_deploy_yaml(
            content=current_content or "",
            issues=request.issues,
            metrics=metrics,
            resource_name=request.resource_name,
            namespace=request.namespace,
            resource_kind=request.resource_kind,
        )

        if not modified_content:
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

        # 5. Cria branch a partir da develop
        created = await self._github.create_branch(
            repo_owner=repo_owner,
            repo_name=repo_name,
            branch_name=branch_name,
            base_branch=request.base_branch,
        )
        if not created:
            return RemediationResult(
                success=False,
                branch_name=branch_name,
                error=f"Falha ao criar branch '{branch_name}'",
            )

        # 6. Commita o deploy.yaml modificado
        committed = await self._github.commit_files(
            repo_owner=repo_owner,
            repo_name=repo_name,
            branch_name=branch_name,
            files=[deploy_file],
        )
        if not committed:
            return RemediationResult(
                success=False,
                branch_name=branch_name,
                error="Falha ao commitar as modificacoes no deploy.yaml",
            )

        # 7. Cria PR para develop
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
            return RemediationResult(
                success=False,
                branch_name=branch_name,
                error=f"Falha ao criar Pull Request: {exc}",
            )

        pr.issues_fixed = [i.rule_id for i in request.issues]

        # 8. Notifica no Slack
        await self._notify_slack(request, pr, categories, metrics)

        self.logger.info(
            "Remediacao concluida com sucesso",
            extra={"pr_number": pr.number, "pr_url": pr.url, "branch": branch_name},
        )
        return RemediationResult(success=True, pull_request=pr, branch_name=branch_name)

    # ------------------------------------------------------------------
    # Extração de informações do Deployment
    # ------------------------------------------------------------------

    def _extract_git_repo(
        self, resource_body: Dict[str, Any]
    ) -> Optional[Tuple[str, str]]:
        """
        Procura DD_GIT_REPOSITORY_URL nas envs de todos os containers.
        Retorna (owner, repo) ou None.
        """
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
        """
        Extrai (owner, repo) de URLs no formato:
          - https://github.com/owner/repo
          - https://github.com/owner/repo.git
          - git@github.com:owner/repo.git
        """
        url = url.strip().rstrip("/").removesuffix(".git")
        match = re.search(r"github\.com[/:]([^/]+)/([^/]+)$", url)
        if match:
            return match.group(1), match.group(2)
        return None

    # ------------------------------------------------------------------
    # Métricas Datadog
    # ------------------------------------------------------------------

    def _fetch_profiling_metrics(
        self, deployment_name: str, namespace: str
    ) -> Optional[DatadogProfilingMetrics]:
        """Coleta métricas de CPU e memória do Datadog. Falha silenciosa."""
        if not self._datadog:
            return None
        try:
            return self._datadog.get_container_metrics(deployment_name, namespace)
        except Exception:
            self.logger.warning(
                "Falha ao coletar metricas de profiling do Datadog",
                extra={"deployment": deployment_name, "namespace": namespace},
            )
            return None

    # ------------------------------------------------------------------
    # Modificação precisa do deploy.yaml (preserva comentários)
    # ------------------------------------------------------------------

    def _modify_deploy_yaml(
        self,
        content: str,
        issues: List[RemediationIssue],
        metrics: Optional[DatadogProfilingMetrics],
        resource_name: str,
        namespace: str,
        resource_kind: str,
    ) -> Tuple[str, List[str]]:
        """
        Modifica manifests/kubernetes/main/deploy.yaml preservando comentários.

        Usa ruamel.yaml para editar apenas os campos necessários:
        - resources.requests/limits nos containers (issues de Resources)
        - HPA document (issues de HPA) — atualiza ou acrescenta ao final do arquivo

        Retorna (conteúdo_modificado, lista_de_categorias).
        """
        try:
            from ruamel.yaml import YAML

            ryaml = YAML()
            ryaml.preserve_quotes = True
            ryaml.width = 10_000  # evita quebra de linhas longas

            documents: List[Any] = []
            if content:
                for doc in ryaml.load_all(content):
                    if doc is not None:
                        documents.append(doc)

            hpa_issues = [i for i in issues if i.rule_id in _HPA_RULE_IDS]
            resource_issues = [i for i in issues if i.rule_id in _RESOURCE_RULE_IDS]
            categories: List[str] = []

            # Encontra o documento Deployment e HPA (se existir)
            deployment_doc = next(
                (d for d in documents if d.get("kind") == "Deployment"), None
            )
            hpa_doc = next(
                (d for d in documents if d.get("kind") == "HorizontalPodAutoscaler"),
                None,
            )

            # ----- Modifica resources no exato lugar -----
            if resource_issues and deployment_doc is not None:
                containers = (
                    deployment_doc.get("spec", {})
                    .get("template", {})
                    .get("spec", {})
                    .get("containers", [])
                )
                if containers:
                    container = containers[0]
                    if "resources" not in container:
                        container["resources"] = {}
                    res = container["resources"]
                    if "requests" not in res:
                        res["requests"] = {}
                    if "limits" not in res:
                        res["limits"] = {}

                    dm = metrics or DatadogProfilingMetrics()
                    res["requests"]["cpu"] = dm.suggest_cpu_request()
                    res["requests"]["memory"] = dm.suggest_memory_request()
                    res["limits"]["cpu"] = dm.suggest_cpu_limit()
                    res["limits"]["memory"] = dm.suggest_memory_limit()

                    categories.append("resources")

            # ----- Modifica / adiciona HPA -----
            if hpa_issues:
                if hpa_doc is not None:
                    spec = hpa_doc.setdefault("spec", {})
                    spec.setdefault("minReplicas", 2)
                    spec.setdefault("maxReplicas", 10)
                    spec["metrics"] = self._build_hpa_metrics_yaml()
                    categories.append("hpa-update")
                else:
                    # Serializa documentos existentes, depois appenda HPA
                    import yaml as stdlib_yaml

                    hpa_manifest = self._build_hpa_manifest_dict(
                        resource_name, namespace, resource_kind
                    )
                    hpa_yaml_str = (
                        "\n---\n"
                        + stdlib_yaml.dump(
                            hpa_manifest, default_flow_style=False, allow_unicode=True
                        )
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

    def _build_hpa_metrics_yaml(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "Resource",
                "resource": {
                    "name": "cpu",
                    "target": {"type": "Utilization", "averageUtilization": 70},
                },
            },
            {
                "type": "Resource",
                "resource": {
                    "name": "memory",
                    "target": {"type": "Utilization", "averageUtilization": 80},
                },
            },
        ]

    def _build_hpa_manifest_dict(
        self, resource_name: str, namespace: str, resource_kind: str
    ) -> Dict[str, Any]:
        return {
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
                "minReplicas": 2,
                "maxReplicas": 10,
                "metrics": self._build_hpa_metrics_yaml(),
            },
        }

    # ------------------------------------------------------------------
    # Helpers de branch / commit / PR
    # ------------------------------------------------------------------

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
            f"{request.namespace}/{request.resource_name} [IA]"
        )

    def _build_pr_title(
        self, request: RemediationRequest, categories: List[str]
    ) -> str:
        cats_str = ", ".join(categories).upper() if categories else "MISC"
        return (
            f"[IA] fix({request.namespace}/{request.resource_name}): "
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

        # Tabela de categorias
        cat_rows: List[str] = []
        for cat in categories:
            rules = (
                ", ".join(i.rule_id for i in request.issues if i.rule_id in _HPA_RULE_IDS)
                if "hpa" in cat
                else ", ".join(
                    i.rule_id for i in request.issues if i.rule_id in _RESOURCE_RULE_IDS
                )
            )
            label = "HPA (Auto Scaling)" if "hpa" in cat else "Resources (Requests/Limits)"
            cat_rows.append(
                f"| {label} | {rules} | `{DEPLOY_YAML_PATH}` |"
            )
        categories_table = "\n".join(cat_rows) if cat_rows else "| — | — | — |"

        # Métricas Datadog
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

        # Lista de issues
        issues_md = "\n".join(
            f"- **{i.rule_id}** — {i.rule_name}: {i.description}"
            for i in request.issues
        )

        return (
            f"> [!WARNING]\n"
            f"> **Este PR foi gerado automaticamente por inteligencia artificial"
            f" (titlis-operator).**\n"
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
            f"*Gerado automaticamente pelo titlis-operator em {now_iso}*  \n"
            f"*Baseado em metricas de profiling coletadas do Datadog*"
        )

    # ------------------------------------------------------------------
    # Notificação Slack
    # ------------------------------------------------------------------

    async def _notify_slack(
        self,
        request: RemediationRequest,
        pr: PullRequestResult,
        categories: List[str],
        metrics: Optional[DatadogProfilingMetrics],
    ) -> None:
        """Envia notificação no Slack após a criação do PR."""
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

        notification = SlackNotification(
            title=f"[IA] Auto-Remediacao PR Criado — {cats_str}",
            message=(
                f"*Recurso:* `{request.namespace}/{request.resource_name}`"
                f" ({request.resource_kind})\n"
                f"*Categorias:* {cats_str}\n"
                f"*Branch:* `{pr.branch}` -> `{pr.base_branch}`\n"
                f"*Issues ({len(request.issues)}):*\n{issues_text}"
                f"{metrics_line}\n"
                f"*PR:* <{pr.url}|#{pr.number} — revisao obrigatoria>"
            ),
            severity=NotificationSeverity.WARNING,
            channel=NotificationChannel.OPERATIONAL,
            namespace=request.namespace,
            additional_fields={
                "pr_url": pr.url,
                "pr_number": str(pr.number),
                "branch": pr.branch,
                "resource": f"{request.namespace}/{request.resource_name}",
                "categories": cats_str,
                "generated_by": "IA / titlis-operator",
            },
        )

        try:
            await self._slack.send_notification(notification)
            self.logger.info(
                "Notificacao Slack de remediacao enviada",
                extra={"pr_number": pr.number},
            )
        except Exception:
            self.logger.exception("Falha ao enviar notificacao Slack de remediacao")
