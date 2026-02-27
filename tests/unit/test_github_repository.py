"""
Testes unitários para GitHubRepository.

Cobre:
- branch_exists (encontrada / não encontrada / erro HTTP genérico)
- get_file_content (arquivo encontrado / 404 / erro HTTP)
- create_branch (sucesso / falha ao obter SHA / falha ao criar ref)
- commit_files (arquivo novo / arquivo existente / falha parcial)
- create_pull_request (sucesso / payload correto)
"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from src.domain.github_models import RemediationFile
from src.infrastructure.github.repository import GitHubRepository


# ---------------------------------------------------------------------------
# Helper para construir resposta httpx falsa
# ---------------------------------------------------------------------------


def _make_http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.github.com/test")
    response = httpx.Response(status_code=status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    return AsyncMock()


@pytest.fixture
def repo(mock_client):
    return GitHubRepository(client=mock_client)


@pytest.fixture
def sample_file():
    return RemediationFile(
        path="k8s/default/my-app-hpa.yaml",
        content="apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\n",
        commit_message="fix(hpa): adiciona HPA",
    )


# ---------------------------------------------------------------------------
# branch_exists
# ---------------------------------------------------------------------------


class TestBranchExists:
    @pytest.mark.asyncio
    async def test_retorna_true_quando_branch_existe(self, repo, mock_client):
        mock_client.get.return_value = {"ref": "refs/heads/main"}
        result = await repo.branch_exists("org", "repo", "main")
        assert result is True

    @pytest.mark.asyncio
    async def test_retorna_false_quando_branch_nao_existe_404(
        self, repo, mock_client
    ):
        mock_client.get.side_effect = _make_http_error(404)
        result = await repo.branch_exists("org", "repo", "nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_propaga_erro_http_nao_404(self, repo, mock_client):
        mock_client.get.side_effect = _make_http_error(500)
        with pytest.raises(httpx.HTTPStatusError):
            await repo.branch_exists("org", "repo", "main")

    @pytest.mark.asyncio
    async def test_retorna_false_em_excecao_generica(self, repo, mock_client):
        mock_client.get.side_effect = Exception("network error")
        result = await repo.branch_exists("org", "repo", "main")
        assert result is False


# ---------------------------------------------------------------------------
# get_file_content
# ---------------------------------------------------------------------------


class TestGetFileContent:
    @pytest.mark.asyncio
    async def test_retorna_conteudo_decodificado(self, repo, mock_client):
        import base64

        raw = base64.b64encode(b"apiVersion: apps/v1\n").decode()
        mock_client.get.return_value = {"content": raw}
        result = await repo.get_file_content("org", "repo", "path/file.yaml", "main")
        assert result == "apiVersion: apps/v1\n"

    @pytest.mark.asyncio
    async def test_retorna_none_quando_arquivo_nao_existe(self, repo, mock_client):
        mock_client.get.side_effect = _make_http_error(404)
        result = await repo.get_file_content("org", "repo", "missing.yaml", "main")
        assert result is None

    @pytest.mark.asyncio
    async def test_retorna_none_em_erro_generico(self, repo, mock_client):
        mock_client.get.side_effect = Exception("network error")
        result = await repo.get_file_content("org", "repo", "file.yaml", "main")
        assert result is None

    @pytest.mark.asyncio
    async def test_retorna_none_em_http_error_nao_404(self, repo, mock_client):
        mock_client.get.side_effect = _make_http_error(403)
        result = await repo.get_file_content("org", "repo", "file.yaml", "main")
        assert result is None

    @pytest.mark.asyncio
    async def test_parametros_corretos_na_chamada(self, repo, mock_client):
        import base64

        mock_client.get.return_value = {
            "content": base64.b64encode(b"content").decode()
        }
        await repo.get_file_content("org", "repo", "manifests/deploy.yaml", "develop")
        mock_client.get.assert_called_once_with(
            "/repos/org/repo/contents/manifests/deploy.yaml",
            params={"ref": "develop"},
        )


# ---------------------------------------------------------------------------
# create_branch
# ---------------------------------------------------------------------------


class TestCreateBranch:
    @pytest.mark.asyncio
    async def test_cria_branch_com_sucesso(self, repo, mock_client):
        mock_client.get.return_value = {"object": {"sha": "abc123"}}
        mock_client.post.return_value = {"ref": "refs/heads/my-branch"}

        result = await repo.create_branch("org", "repo", "my-branch", "develop")

        assert result is True
        mock_client.get.assert_called_once_with(
            "/repos/org/repo/git/ref/heads/develop"
        )
        mock_client.post.assert_called_once_with(
            "/repos/org/repo/git/refs",
            {"ref": "refs/heads/my-branch", "sha": "abc123"},
        )

    @pytest.mark.asyncio
    async def test_retorna_false_quando_get_sha_falha(self, repo, mock_client):
        mock_client.get.side_effect = Exception("not found")
        result = await repo.create_branch("org", "repo", "my-branch", "develop")
        assert result is False

    @pytest.mark.asyncio
    async def test_retorna_false_quando_post_falha(self, repo, mock_client):
        mock_client.get.return_value = {"object": {"sha": "abc123"}}
        mock_client.post.side_effect = Exception("already exists")
        result = await repo.create_branch("org", "repo", "my-branch", "develop")
        assert result is False


# ---------------------------------------------------------------------------
# commit_files
# ---------------------------------------------------------------------------


class TestCommitFiles:
    @pytest.mark.asyncio
    async def test_commita_arquivo_novo(self, repo, mock_client, sample_file):
        # Arquivo não existe ainda → 404 no GET contents
        mock_client.get.side_effect = _make_http_error(404)
        mock_client.put.return_value = {"content": {"sha": "new-sha"}}

        result = await repo.commit_files("org", "repo", "my-branch", [sample_file])

        assert result is True
        call_kwargs = mock_client.put.call_args
        payload = call_kwargs[0][1]
        assert "sha" not in payload  # arquivo novo não tem SHA

    @pytest.mark.asyncio
    async def test_commita_arquivo_existente_inclui_sha(
        self, repo, mock_client, sample_file
    ):
        # Arquivo já existe → GET retorna SHA
        mock_client.get.return_value = {"sha": "old-sha-xyz"}
        mock_client.put.return_value = {"content": {"sha": "updated-sha"}}

        result = await repo.commit_files("org", "repo", "my-branch", [sample_file])

        assert result is True
        call_kwargs = mock_client.put.call_args
        payload = call_kwargs[0][1]
        assert payload["sha"] == "old-sha-xyz"

    @pytest.mark.asyncio
    async def test_retorna_false_quando_put_falha(
        self, repo, mock_client, sample_file
    ):
        mock_client.get.side_effect = _make_http_error(404)
        mock_client.put.side_effect = Exception("write error")

        result = await repo.commit_files("org", "repo", "my-branch", [sample_file])

        assert result is False

    @pytest.mark.asyncio
    async def test_conteudo_base64_correto(self, repo, mock_client, sample_file):
        import base64

        mock_client.get.side_effect = _make_http_error(404)
        mock_client.put.return_value = {"content": {"sha": "sha"}}

        await repo.commit_files("org", "repo", "my-branch", [sample_file])

        payload = mock_client.put.call_args[0][1]
        decoded = base64.b64decode(payload["content"]).decode()
        assert decoded == sample_file.content

    @pytest.mark.asyncio
    async def test_falha_parcial_retorna_false(self, repo, mock_client):
        file1 = RemediationFile(
            path="k8s/ns/app-hpa.yaml", content="a", commit_message="m1"
        )
        file2 = RemediationFile(
            path="k8s/ns/app-resources.yaml", content="b", commit_message="m2"
        )

        # Primeiro arquivo OK, segundo falha
        mock_client.get.side_effect = _make_http_error(404)
        mock_client.put.side_effect = [
            {"content": {"sha": "sha1"}},
            Exception("write error"),
        ]

        result = await repo.commit_files("org", "repo", "my-branch", [file1, file2])

        assert result is False


# ---------------------------------------------------------------------------
# create_pull_request
# ---------------------------------------------------------------------------


class TestCreatePullRequest:
    @pytest.mark.asyncio
    async def test_cria_pr_com_sucesso(self, repo, mock_client):
        mock_client.post.return_value = {
            "number": 99,
            "title": "fix: auto-remediation",
            "html_url": "https://github.com/org/repo/pull/99",
        }

        pr = await repo.create_pull_request(
            repo_owner="org",
            repo_name="repo",
            branch_name="fix/my-branch",
            base_branch="develop",
            title="fix: auto-remediation",
            body="Body text",
        )

        assert pr.number == 99
        assert pr.url == "https://github.com/org/repo/pull/99"
        assert pr.branch == "fix/my-branch"
        assert pr.base_branch == "develop"

    @pytest.mark.asyncio
    async def test_payload_pr_correto(self, repo, mock_client):
        mock_client.post.return_value = {
            "number": 1,
            "title": "title",
            "html_url": "https://github.com/org/repo/pull/1",
        }

        await repo.create_pull_request(
            repo_owner="org",
            repo_name="repo",
            branch_name="fix/branch",
            base_branch="develop",
            title="My Title",
            body="My Body",
        )

        mock_client.post.assert_called_once_with(
            "/repos/org/repo/pulls",
            {
                "title": "My Title",
                "body": "My Body",
                "head": "fix/branch",
                "base": "develop",
            },
        )

    @pytest.mark.asyncio
    async def test_propaga_excecao_da_api(self, repo, mock_client):
        mock_client.post.side_effect = _make_http_error(422)
        with pytest.raises(httpx.HTTPStatusError):
            await repo.create_pull_request(
                repo_owner="org",
                repo_name="repo",
                branch_name="fix/branch",
                base_branch="develop",
                title="t",
                body="b",
            )
