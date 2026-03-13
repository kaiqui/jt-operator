import pytest
from unittest.mock import AsyncMock, MagicMock

from src.application.services.remediation_service import (
    REMEDIABLE_RULE_IDS,
    DEPLOY_YAML_PATH,
    DD_GIT_REPO_ENV,
    RemediationService,
    ResourceRemediationAction,
    HPARemediationAction,
    _build_hpa_metrics_list,
    _HPA_RULE_IDS,
    _RESOURCE_RULE_IDS,
)
from src.domain.github_models import (
    DatadogProfilingMetrics,
    PullRequestResult,
    RemediationIssue,
    RemediationRequest,
    RemediationRuleCategory,
)
from src.domain.models import HPAProfile
from src.settings import RemediationSettings

# ---------------------------------------------------------------------------
# Body helper
# ---------------------------------------------------------------------------


def _make_body(dd_repo_url: str = "https://github.com/org/my-app") -> dict:
    return {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "my-app",
                            "env": [
                                {"name": "OTHER_VAR", "value": "foo"},
                                {"name": DD_GIT_REPO_ENV, "value": dd_repo_url},
                            ],
                        }
                    ]
                }
            }
        }
    }


def _make_body_without_dd_url() -> dict:
    return {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "my-app",
                            "env": [{"name": "OTHER_VAR", "value": "bar"}],
                        }
                    ]
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_github_port():
    port = AsyncMock()
    port.branch_exists.return_value = False
    port.create_branch.return_value = True
    port.commit_files.return_value = True
    port.find_open_remediation_pr.return_value = None
    port.find_merged_remediation_pr.return_value = None
    port.get_file_content.return_value = (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: my-app\n"
        "spec:\n  template:\n    spec:\n      containers:\n"
        "        - name: my-app\n          image: my-app:v1\n"
    )
    port.create_pull_request.return_value = PullRequestResult(
        number=42,
        title="fix(default/my-app): RESOURCES — 2 issue(s)",
        url="https://github.com/org/my-app/pull/42",
        branch="fix/auto-remediation-default-my-app-20240101000000",
        base_branch="develop",
    )
    return port


@pytest.fixture
def mock_slack_service():
    svc = MagicMock()
    svc.is_enabled.return_value = True
    svc.send_notification = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def mock_datadog():
    dd = MagicMock()
    dd.get_container_metrics.return_value = DatadogProfilingMetrics(
        cpu_avg_millicores=200,
        memory_avg_mib=256,
    )
    return dd


@pytest.fixture
def hpa_issue():
    return RemediationIssue(
        rule_id="RES-007",
        rule_name="HPA Configurado",
        description="Deployment deve ter HPA configurado",
        remediation="Configure HPA para auto-scaling",
    )


@pytest.fixture
def resource_issue():
    return RemediationIssue(
        rule_id="RES-003",
        rule_name="CPU Requests Definidos",
        description="Container deve ter requests de CPU definidos",
        remediation="Defina requests.cpu",
    )


@pytest.fixture
def base_request(hpa_issue, resource_issue):
    return RemediationRequest(
        resource_name="my-app",
        namespace="default",
        resource_kind="Deployment",
        issues=[hpa_issue, resource_issue],
        resource_body=_make_body(),
        base_branch="develop",
    )


@pytest.fixture
def service(mock_github_port, mock_slack_service, mock_datadog):
    return RemediationService(
        github_port=mock_github_port,
        slack_service=mock_slack_service,
        datadog_repository=mock_datadog,
    )


# ---------------------------------------------------------------------------
# Testes de constants
# ---------------------------------------------------------------------------


class TestRemediableRuleIds:
    def test_hpa_rule_ids_presente(self):
        assert "RES-007" in _HPA_RULE_IDS
        assert "RES-008" in _HPA_RULE_IDS
        assert "PERF-002" in _HPA_RULE_IDS

    def test_resource_rule_ids_presente(self):
        assert "RES-003" in _RESOURCE_RULE_IDS
        assert "RES-004" in _RESOURCE_RULE_IDS
        assert "RES-005" in _RESOURCE_RULE_IDS
        assert "RES-006" in _RESOURCE_RULE_IDS
        assert "PERF-001" in _RESOURCE_RULE_IDS

    def test_remediable_uniao(self):
        assert REMEDIABLE_RULE_IDS == _HPA_RULE_IDS | _RESOURCE_RULE_IDS


# ---------------------------------------------------------------------------
# Testes de RemediationIssue (category inference)
# ---------------------------------------------------------------------------


class TestRemediationIssueCategory:
    def test_hpa_issue_categoria_correta(self):
        issue = RemediationIssue(
            rule_id="RES-007", rule_name="HPA", description="d", remediation="f"
        )
        assert issue.category == RemediationRuleCategory.HPA

    def test_resource_issue_categoria_correta(self):
        issue = RemediationIssue(
            rule_id="RES-003", rule_name="CPU", description="d", remediation="f"
        )
        assert issue.category == RemediationRuleCategory.RESOURCES


# ---------------------------------------------------------------------------
# Testes de DatadogProfilingMetrics
# ---------------------------------------------------------------------------


class TestDatadogProfilingMetrics:
    def test_suggestions_com_dados(self):
        m = DatadogProfilingMetrics(cpu_avg_millicores=200, memory_avg_mib=256)
        assert m.suggest_cpu_request() == "240m"  # 200 * 1.2
        assert m.suggest_cpu_limit() == "600m"  # 200 * 3
        assert m.suggest_memory_request() == "307Mi"  # 256 * 1.2 ≈ 307
        assert m.suggest_memory_limit() == "512Mi"  # 256 * 2

    def test_suggestions_sem_dados_usa_padrao(self):
        m = DatadogProfilingMetrics()
        assert m.suggest_cpu_request() == "100m"
        assert m.suggest_cpu_limit() == "500m"
        assert m.suggest_memory_request() == "128Mi"
        assert m.suggest_memory_limit() == "512Mi"

    def test_minimo_cpu_request(self):
        m = DatadogProfilingMetrics(cpu_avg_millicores=5)  # muito baixo
        request = m.suggest_cpu_request()
        assert request == "100m"  # mínimo garantido

    def test_minimo_memory_request(self):
        m = DatadogProfilingMetrics(memory_avg_mib=10)
        request = m.suggest_memory_request()
        assert request == "128Mi"  # mínimo garantido


# ---------------------------------------------------------------------------
# Testes de _extract_git_repo / _parse_github_url
# ---------------------------------------------------------------------------


class TestExtractGitRepo:
    def test_extrai_owner_repo_de_url_https(self, service):
        result = service._extract_git_repo(_make_body("https://github.com/org/my-app"))
        assert result == ("org", "my-app")

    def test_extrai_de_url_com_git_suffix(self, service):
        result = service._extract_git_repo(
            _make_body("https://github.com/org/my-app.git")
        )
        assert result == ("org", "my-app")

    def test_retorna_none_sem_dd_git_url(self, service):
        result = service._extract_git_repo(_make_body_without_dd_url())
        assert result is None

    def test_retorna_none_com_url_invalida(self, service):
        result = service._extract_git_repo(_make_body("https://gitlab.com/org/repo"))
        assert result is None

    def test_parse_github_url_ssh(self):
        result = RemediationService._parse_github_url("git@github.com:org/repo.git")
        assert result == ("org", "repo")

    def test_parse_github_url_trailing_slash(self):
        result = RemediationService._parse_github_url("https://github.com/org/repo/")
        assert result == ("org", "repo")


# ---------------------------------------------------------------------------
# Testes de _modify_deploy_yaml
# ---------------------------------------------------------------------------

_DEPLOY_YAML_WITH_RESOURCES = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: default
spec:
  template:
    spec:
      containers:
        - name: my-app
          # imagem principal
          image: my-app:v1
          resources:
            requests:
              cpu: "50m"    # valor anterior
              memory: "64Mi"
            limits:
              cpu: "200m"
              memory: "256Mi"
"""

_DEPLOY_YAML_WITHOUT_RESOURCES = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  template:
    spec:
      containers:
        - name: my-app
          image: my-app:v1
"""


class TestModifyDeployYaml:
    def test_modifica_resources_existentes(self, service):
        metrics = DatadogProfilingMetrics(cpu_avg_millicores=200, memory_avg_mib=256)
        resource_issue = RemediationIssue(
            rule_id="RES-003", rule_name="CPU", description="d", remediation="f"
        )
        content, categories = service._modify_deploy_yaml(
            content=_DEPLOY_YAML_WITH_RESOURCES,
            issues=[resource_issue],
            metrics=metrics,
            resource_name="my-app",
            namespace="default",
            resource_kind="Deployment",
        )
        assert "resources" in categories
        # Verifica os novos valores
        assert "240m" in content  # cpu request
        assert "600m" in content  # cpu limit

    def test_adiciona_resources_quando_ausentes(self, service):
        resource_issue = RemediationIssue(
            rule_id="RES-003", rule_name="CPU", description="d", remediation="f"
        )
        content, categories = service._modify_deploy_yaml(
            content=_DEPLOY_YAML_WITHOUT_RESOURCES,
            issues=[resource_issue],
            metrics=None,  # sem métricas — usa padrão
            resource_name="my-app",
            namespace="default",
            resource_kind="Deployment",
        )
        assert "resources" in categories
        assert "100m" in content  # cpu request padrão

    def test_adiciona_hpa_quando_ausente(self, service):
        hpa_issue = RemediationIssue(
            rule_id="RES-007", rule_name="HPA", description="d", remediation="f"
        )
        content, categories = service._modify_deploy_yaml(
            content=_DEPLOY_YAML_WITHOUT_RESOURCES,
            issues=[hpa_issue],
            metrics=None,
            resource_name="my-app",
            namespace="default",
            resource_kind="Deployment",
        )
        assert "hpa-create" in categories
        assert "HorizontalPodAutoscaler" in content

    def test_hpa_e_resources_na_mesma_passagem(self, service):
        hpa_issue = RemediationIssue(
            rule_id="RES-007", rule_name="HPA", description="d", remediation="f"
        )
        res_issue = RemediationIssue(
            rule_id="RES-003", rule_name="CPU", description="d", remediation="f"
        )
        content, categories = service._modify_deploy_yaml(
            content=_DEPLOY_YAML_WITHOUT_RESOURCES,
            issues=[hpa_issue, res_issue],
            metrics=None,
            resource_name="my-app",
            namespace="default",
            resource_kind="Deployment",
        )
        assert "resources" in categories
        assert "hpa-create" in categories
        assert "HorizontalPodAutoscaler" in content

    def test_retorna_vazio_sem_issues_remediaveis(self, service):
        non_remediable = RemediationIssue(
            rule_id="SEC-001", rule_name="Imagem", description="d", remediation="f"
        )
        content, categories = service._modify_deploy_yaml(
            content=_DEPLOY_YAML_WITH_RESOURCES,
            issues=[non_remediable],
            metrics=None,
            resource_name="my-app",
            namespace="default",
            resource_kind="Deployment",
        )
        assert content == ""
        assert categories == []

    def test_preserva_comentarios_yaml(self, service):
        resource_issue = RemediationIssue(
            rule_id="RES-003", rule_name="CPU", description="d", remediation="f"
        )
        content, _ = service._modify_deploy_yaml(
            content=_DEPLOY_YAML_WITH_RESOURCES,
            issues=[resource_issue],
            metrics=None,
            resource_name="my-app",
            namespace="default",
            resource_kind="Deployment",
        )
        assert "imagem principal" in content  # comentário preservado


# ---------------------------------------------------------------------------
# Testes do fluxo completo: create_remediation_pr
# ---------------------------------------------------------------------------


class TestCreateRemediationPr:
    @pytest.mark.asyncio
    async def test_ignora_sem_dd_git_url(self, service, base_request):
        base_request.resource_body = _make_body_without_dd_url()
        result = await service.create_remediation_pr(base_request)
        assert result.success is False
        assert DD_GIT_REPO_ENV in (result.error or "")

    @pytest.mark.asyncio
    async def test_fluxo_completo_sucesso(
        self, service, mock_github_port, mock_slack_service, base_request
    ):
        result = await service.create_remediation_pr(base_request)

        assert result.success is True
        assert result.pull_request is not None
        assert result.pull_request.number == 42

        mock_github_port.find_open_remediation_pr.assert_called_once()
        mock_github_port.get_file_content.assert_called_once()
        mock_github_port.create_branch.assert_called_once()
        mock_github_port.commit_files.assert_called_once()
        mock_github_port.create_pull_request.assert_called_once()
        mock_slack_service.send_notification.assert_called_once()

    @pytest.mark.asyncio
    async def test_idempotencia_pr_existente_aborta(
        self, service, mock_github_port, base_request
    ):
        existing = PullRequestResult(
            number=10,
            title="fix(default/my-app): RESOURCES — 1 issue(s)",
            url="https://github.com/org/my-app/pull/10",
            branch="fix/auto-remediation-default-my-app-20240101000000",
            base_branch="develop",
        )
        mock_github_port.find_open_remediation_pr.return_value = existing

        result = await service.create_remediation_pr(base_request)

        assert result.success is False
        assert result.pull_request is not None
        assert result.pull_request.number == 10
        # Não deve ter criado branch nem PR novo
        mock_github_port.create_branch.assert_not_called()
        mock_github_port.create_pull_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_pr_mergeada_aborta_sem_criar_nova(
        self, service, mock_github_port, base_request
    ):
        merged = PullRequestResult(
            number=20,
            title="fix(default/my-app): RESOURCES — 1 issue(s)",
            url="https://github.com/org/my-app/pull/20",
            branch="fix/auto-remediation-default-my-app-20240101000000",
            base_branch="develop",
        )
        mock_github_port.find_open_remediation_pr.return_value = None
        mock_github_port.find_merged_remediation_pr.return_value = merged

        result = await service.create_remediation_pr(base_request)

        assert result.success is False
        assert result.pull_request is not None
        assert result.pull_request.number == 20
        assert "mergeada" in (result.error or "").lower()
        mock_github_port.create_branch.assert_not_called()
        mock_github_port.create_pull_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_pr_cancelada_cria_nova_pr(
        self, service, mock_github_port, base_request
    ):
        mock_github_port.find_open_remediation_pr.return_value = None
        mock_github_port.find_merged_remediation_pr.return_value = None

        result = await service.create_remediation_pr(base_request)

        assert result.success is True
        mock_github_port.create_branch.assert_called_once()
        mock_github_port.create_pull_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_idempotencia_trava_em_memoria(
        self, service, mock_github_port, base_request
    ):
        repo_info = service._extract_git_repo(base_request.resource_body)
        assert repo_info is not None
        repo_owner, repo_name = repo_info
        key = RemediationService._resource_key(
            repo_owner, repo_name, base_request.namespace, base_request.resource_name
        )
        # Simula execução em andamento
        service._pending.add(key)

        result = await service.create_remediation_pr(base_request)

        assert result.success is False
        assert "em andamento" in (result.error or "")
        mock_github_port.create_branch.assert_not_called()

    @pytest.mark.asyncio
    async def test_trava_liberada_apos_execucao(
        self, service, mock_github_port, base_request
    ):
        mock_github_port.create_branch.return_value = False  # força falha

        await service.create_remediation_pr(base_request)

        # _pending deve estar vazio após a execução
        assert len(service._pending) == 0

    @pytest.mark.asyncio
    async def test_usa_repo_da_dd_git_url(
        self, service, mock_github_port, base_request
    ):
        base_request.resource_body = _make_body("https://github.com/myorg/myrepo")
        await service.create_remediation_pr(base_request)

        # Verifica que get_file_content foi chamado com o repo correto
        call = mock_github_port.get_file_content.call_args
        assert call[1]["repo_owner"] == "myorg"
        assert call[1]["repo_name"] == "myrepo"

    @pytest.mark.asyncio
    async def test_arquivo_modificado_e_deploy_yaml(
        self, service, mock_github_port, base_request
    ):
        await service.create_remediation_pr(base_request)

        commit_call = mock_github_port.commit_files.call_args
        files = commit_call[1]["files"]
        assert len(files) == 1
        assert files[0].path == DEPLOY_YAML_PATH

    @pytest.mark.asyncio
    async def test_retorna_falha_quando_create_branch_falha(
        self, service, mock_github_port, base_request
    ):
        mock_github_port.create_branch.return_value = False
        result = await service.create_remediation_pr(base_request)
        assert result.success is False
        assert "branch" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_retorna_falha_quando_commit_falha(
        self, service, mock_github_port, base_request
    ):
        mock_github_port.commit_files.return_value = False
        result = await service.create_remediation_pr(base_request)
        assert result.success is False
        assert "commitar" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_retorna_falha_quando_create_pr_levanta_excecao(
        self, service, mock_github_port, base_request
    ):
        mock_github_port.create_pull_request.side_effect = RuntimeError("API error")
        result = await service.create_remediation_pr(base_request)
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_nao_falha_quando_slack_falha(
        self, service, mock_slack_service, base_request
    ):
        mock_slack_service.send_notification.side_effect = RuntimeError("slack down")
        result = await service.create_remediation_pr(base_request)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_issues_fixed_populado(self, service, base_request):
        result = await service.create_remediation_pr(base_request)
        assert result.success is True
        assert set(result.pull_request.issues_fixed) == {  # type: ignore[union-attr]
            i.rule_id for i in base_request.issues
        }


# ---------------------------------------------------------------------------
# Testes de _build_pr_body (disclaimer + categorias + checklist)
# ---------------------------------------------------------------------------


class TestBuildPrBody:
    def test_body_contem_titlis_operator(self, service, base_request):
        body = service._build_pr_body(base_request, ["resources"], None)
        assert "titlis-operator" in body.lower()
        assert "revisao humana" in body.lower()

    def test_body_nao_contem_ia(self, service, base_request):
        body = service._build_pr_body(base_request, ["resources"], None)
        assert "inteligencia artificial" not in body.lower()
        assert "[ia]" not in body.lower()

    def test_body_contem_checklist(self, service, base_request):
        body = service._build_pr_body(base_request, ["resources"], None)
        assert "- [ ]" in body

    def test_body_contem_metricas_datadog(self, service, base_request):
        metrics = DatadogProfilingMetrics(cpu_avg_millicores=200, memory_avg_mib=256)
        body = service._build_pr_body(base_request, ["resources"], metrics)
        assert "200" in body  # cpu avg
        assert "256" in body  # memory avg

    def test_body_contem_aviso_sem_metricas(self, service, base_request):
        body = service._build_pr_body(base_request, ["resources"], None)
        assert "indisponiveis" in body.lower() or "valores padrao" in body.lower()

    def test_body_contem_ids_das_issues(self, service, base_request):
        body = service._build_pr_body(base_request, ["resources", "hpa-create"], None)
        for issue in base_request.issues:
            assert issue.rule_id in body

    def test_body_contem_deploy_yaml_path(self, service, base_request):
        body = service._build_pr_body(base_request, ["resources"], None)
        assert DEPLOY_YAML_PATH in body


# ---------------------------------------------------------------------------
# Testes de _build_pr_title
# ---------------------------------------------------------------------------


class TestBuildPrTitle:
    def test_titulo_nao_contem_ia(self, service, base_request):
        title = service._build_pr_title(base_request, ["resources"])
        assert "[IA]" not in title
        assert "IA" not in title.split()

    def test_titulo_contem_namespace_e_recurso(self, service, base_request):
        title = service._build_pr_title(base_request, ["resources"])
        assert base_request.namespace in title
        assert base_request.resource_name in title

    def test_titulo_contem_categoria(self, service, base_request):
        title = service._build_pr_title(base_request, ["resources", "hpa-create"])
        assert "RESOURCES" in title or "HPA-CREATE" in title


# ---------------------------------------------------------------------------
# Testes de ResourceRemediationAction
# ---------------------------------------------------------------------------


class TestResourceRemediationAction:
    @pytest.fixture
    def action(self):
        return ResourceRemediationAction(RemediationSettings())

    def _make_deployment_doc(
        self, cpu_req=None, cpu_lim=None, mem_req=None, mem_lim=None
    ):
        resources: dict = {}
        if any([cpu_req, cpu_lim, mem_req, mem_lim]):
            resources = {
                "requests": {
                    k: v for k, v in {"cpu": cpu_req, "memory": mem_req}.items() if v
                },
                "limits": {
                    k: v for k, v in {"cpu": cpu_lim, "memory": mem_lim}.items() if v
                },
            }
        return {
            "spec": {
                "template": {
                    "spec": {"containers": [{"name": "app", "resources": resources}]}
                }
            }
        }

    def test_apply_sem_containers_retorna_false(self, action):
        doc = {"spec": {"template": {"spec": {"containers": []}}}}
        assert action.apply(doc, None) is False

    def test_apply_sem_metrics_usa_defaults(self, action):
        doc = self._make_deployment_doc()
        result = action.apply(doc, None)
        assert result is True
        res = doc["spec"]["template"]["spec"]["containers"][0]["resources"]
        assert res["requests"]["cpu"] == "100m"
        assert res["requests"]["memory"] == "128Mi"
        assert res["limits"]["cpu"] == "500m"
        assert res["limits"]["memory"] == "512Mi"

    def test_apply_nunca_reduz_valor_existente(self, action):
        doc = self._make_deployment_doc(cpu_req="800m", mem_req="512Mi")
        result = action.apply(doc, None)
        assert result is True
        res = doc["spec"]["template"]["spec"]["containers"][0]["resources"]
        # default é 100m, existente é 800m → mantém 800m
        assert res["requests"]["cpu"] == "800m"
        # default é 128Mi, existente é 512Mi → mantém 512Mi
        assert res["requests"]["memory"] == "512Mi"

    def test_apply_com_metricas_usa_sugestao(self, action):
        metrics = DatadogProfilingMetrics(cpu_avg_millicores=300, memory_avg_mib=400)
        doc = self._make_deployment_doc()
        action.apply(doc, metrics)
        res = doc["spec"]["template"]["spec"]["containers"][0]["resources"]
        # 300 * 1.2 = 360m, max(100, 360) = 360
        assert res["requests"]["cpu"] == "360m"


# ---------------------------------------------------------------------------
# Testes de HPARemediationAction
# ---------------------------------------------------------------------------


class TestHPARemediationAction:
    @pytest.fixture
    def settings(self):
        return RemediationSettings()

    @pytest.fixture
    def action(self, settings):
        return HPARemediationAction(settings)

    def test_build_manifest_light_sem_behavior(self, action):
        manifest = action.build_manifest("app", "ns", "Deployment", HPAProfile.LIGHT)
        assert manifest["kind"] == "HorizontalPodAutoscaler"
        assert manifest["spec"]["minReplicas"] == 2
        assert manifest["spec"]["maxReplicas"] == 10
        assert "behavior" not in manifest["spec"]

    def test_build_manifest_rigid_com_behavior(self, action):
        manifest = action.build_manifest("app", "ns", "Deployment", HPAProfile.RIGID)
        assert "behavior" in manifest["spec"]
        behavior = manifest["spec"]["behavior"]
        assert behavior["scaleUp"]["stabilizationWindowSeconds"] == 0
        assert behavior["scaleDown"]["stabilizationWindowSeconds"] == 300
        assert len(behavior["scaleUp"]["policies"]) == 2
        assert len(behavior["scaleDown"]["policies"]) == 1
        assert behavior["scaleUp"]["selectPolicy"] == "Max"

    def test_apply_update_light_nao_adiciona_behavior(self, action):
        hpa_doc = {"spec": {"minReplicas": 1, "maxReplicas": 5, "metrics": []}}
        action.apply_update(hpa_doc, HPAProfile.LIGHT)
        assert "behavior" not in hpa_doc["spec"]

    def test_apply_update_rigid_adiciona_behavior(self, action):
        hpa_doc = {"spec": {"minReplicas": 1, "maxReplicas": 5, "metrics": []}}
        action.apply_update(hpa_doc, HPAProfile.RIGID)
        assert "behavior" in hpa_doc["spec"]

    def test_apply_update_nunca_reduz_replicas(self, action):
        hpa_doc = {"spec": {"minReplicas": 5, "maxReplicas": 20, "metrics": []}}
        action.apply_update(hpa_doc, HPAProfile.LIGHT)
        assert hpa_doc["spec"]["minReplicas"] == 5  # max(5, default=2) = 5
        assert hpa_doc["spec"]["maxReplicas"] == 20  # max(20, default=10) = 20

    def test_apply_update_metrics_preenchidos(self, action):
        hpa_doc = {"spec": {"minReplicas": 2, "maxReplicas": 10, "metrics": []}}
        action.apply_update(hpa_doc, HPAProfile.LIGHT)
        metrics = hpa_doc["spec"]["metrics"]
        assert len(metrics) == 2
        cpu_metric = next(m for m in metrics if m["resource"]["name"] == "cpu")
        assert cpu_metric["resource"]["target"]["averageUtilization"] == 70


# ---------------------------------------------------------------------------
# Testes de _detect_hpa_profile
# ---------------------------------------------------------------------------


class TestDetectHpaProfile:
    @pytest.fixture
    def service(self, mock_github_port, mock_slack_service):
        return RemediationService(
            github_port=mock_github_port,
            slack_service=mock_slack_service,
            datadog_repository=None,
        )

    @pytest.fixture
    def service_with_datadog(self, mock_github_port, mock_slack_service, mock_datadog):
        return RemediationService(
            github_port=mock_github_port,
            slack_service=mock_slack_service,
            datadog_repository=mock_datadog,
        )

    def _make_body_with_annotation(self, criticality: str) -> dict:
        body = _make_body()
        body["metadata"] = {"annotations": {"titlis.io/criticality": criticality}}
        return body

    def test_sem_annotation_retorna_light(self, service):
        profile = service._detect_hpa_profile(_make_body(), "app")
        assert profile == HPAProfile.LIGHT

    def test_annotation_high_retorna_rigid(self, service):
        body = self._make_body_with_annotation("high")
        profile = service._detect_hpa_profile(body, "app")
        assert profile == HPAProfile.RIGID

    def test_annotation_low_retorna_light(self, service):
        body = self._make_body_with_annotation("low")
        profile = service._detect_hpa_profile(body, "app")
        assert profile == HPAProfile.LIGHT

    def test_datadog_acima_threshold_retorna_rigid(
        self, service_with_datadog, mock_datadog
    ):
        mock_datadog.get_request_count.return_value = 200_000  # > default 100_000
        profile = service_with_datadog._detect_hpa_profile(_make_body(), "app")
        assert profile == HPAProfile.RIGID

    def test_datadog_abaixo_threshold_retorna_light(
        self, service_with_datadog, mock_datadog
    ):
        mock_datadog.get_request_count.return_value = 50_000  # < default 100_000
        profile = service_with_datadog._detect_hpa_profile(_make_body(), "app")
        assert profile == HPAProfile.LIGHT

    def test_datadog_none_retorna_light(self, service_with_datadog, mock_datadog):
        mock_datadog.get_request_count.return_value = None
        profile = service_with_datadog._detect_hpa_profile(_make_body(), "app")
        assert profile == HPAProfile.LIGHT

    def test_annotation_tem_prioridade_sobre_datadog(
        self, service_with_datadog, mock_datadog
    ):
        body = self._make_body_with_annotation("high")
        profile = service_with_datadog._detect_hpa_profile(body, "app")
        assert profile == HPAProfile.RIGID
        mock_datadog.get_request_count.assert_not_called()


# ---------------------------------------------------------------------------
# Testes de feature flags de remediação
# ---------------------------------------------------------------------------


class TestFeatureFlags:
    def _make_service(self, enable_resources=True, enable_hpa=True):
        settings = RemediationSettings(
            ENABLE_REMEDIATION_RESOURCES=str(enable_resources).lower(),
            ENABLE_REMEDIATION_HPA=str(enable_hpa).lower(),
        )
        port = AsyncMock()
        return RemediationService(github_port=port, remediation_settings=settings)

    def test_recursos_desabilitados_nao_modifica_resources(self):
        svc = self._make_service(enable_resources=False, enable_hpa=False)
        content = (
            "apiVersion: apps/v1\nkind: Deployment\n"
            "spec:\n  template:\n    spec:\n      containers:\n"
            "        - name: app\n          image: app:v1\n"
        )
        issues = [
            RemediationIssue(
                rule_id="RES-003", rule_name="CPU", description="d", remediation="r"
            )
        ]
        result, categories = svc._modify_deploy_yaml(
            content, issues, None, "app", "ns", "Deployment"
        )
        assert categories == []
        assert result == ""

    def test_hpa_desabilitado_nao_cria_hpa(self):
        svc = self._make_service(enable_resources=False, enable_hpa=False)
        content = (
            "apiVersion: apps/v1\nkind: Deployment\n"
            "spec:\n  template:\n    spec:\n      containers:\n"
            "        - name: app\n          image: app:v1\n"
        )
        issues = [
            RemediationIssue(
                rule_id="RES-007", rule_name="HPA", description="d", remediation="r"
            )
        ]
        result, categories = svc._modify_deploy_yaml(
            content, issues, None, "app", "ns", "Deployment"
        )
        assert "hpa-create" not in categories
        assert result == ""


# ---------------------------------------------------------------------------
# Testes de _modify_deploy_yaml com HPAProfile
# ---------------------------------------------------------------------------


class TestModifyDeployYamlHpaProfile:
    @pytest.fixture
    def service(self):
        port = AsyncMock()
        return RemediationService(github_port=port)

    _DEPLOY_YAML = (
        "apiVersion: apps/v1\nkind: Deployment\n"
        "metadata:\n  name: my-app\n"
        "spec:\n  template:\n    spec:\n      containers:\n"
        "        - name: my-app\n          image: my-app:v1\n"
    )

    def _hpa_issue(self):
        return RemediationIssue(
            rule_id="RES-007", rule_name="HPA", description="d", remediation="r"
        )

    def test_hpa_create_light_sem_behavior(self, service):
        _, categories = service._modify_deploy_yaml(
            self._DEPLOY_YAML,
            [self._hpa_issue()],
            None,
            "my-app",
            "ns",
            "Deployment",
            hpa_profile=HPAProfile.LIGHT,
        )
        assert "hpa-create" in categories

    def test_hpa_create_rigid_inclui_behavior_no_yaml(self, service):
        result, categories = service._modify_deploy_yaml(
            self._DEPLOY_YAML,
            [self._hpa_issue()],
            None,
            "my-app",
            "ns",
            "Deployment",
            hpa_profile=HPAProfile.RIGID,
        )
        assert "hpa-create" in categories
        assert "stabilizationWindowSeconds" in result
        assert "scaleUp" in result
        assert "scaleDown" in result

    def test_hpa_update_rigid_adiciona_behavior(self, service):
        deploy_and_hpa = (
            "apiVersion: apps/v1\nkind: Deployment\n"
            "metadata:\n  name: my-app\n"
            "spec:\n  template:\n    spec:\n      containers:\n"
            "        - name: my-app\n          image: my-app:v1\n"
            "---\n"
            "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\n"
            "metadata:\n  name: my-app\n"
            "spec:\n  minReplicas: 1\n  maxReplicas: 5\n  metrics: []\n"
        )
        result, categories = service._modify_deploy_yaml(
            deploy_and_hpa,
            [self._hpa_issue()],
            None,
            "my-app",
            "ns",
            "Deployment",
            hpa_profile=HPAProfile.RIGID,
        )
        assert "hpa-update" in categories
        assert "stabilizationWindowSeconds" in result


# ---------------------------------------------------------------------------
# Testes de _build_hpa_metrics_list (função módulo)
# ---------------------------------------------------------------------------


class TestBuildHpaMetricsList:
    def test_retorna_dois_itens(self):
        metrics = _build_hpa_metrics_list(60, 70)
        assert len(metrics) == 2

    def test_cpu_utilization_correta(self):
        metrics = _build_hpa_metrics_list(60, 70)
        cpu = next(m for m in metrics if m["resource"]["name"] == "cpu")
        assert cpu["resource"]["target"]["averageUtilization"] == 60

    def test_memory_utilization_correta(self):
        metrics = _build_hpa_metrics_list(60, 70)
        mem = next(m for m in metrics if m["resource"]["name"] == "memory")
        assert mem["resource"]["target"]["averageUtilization"] == 70
