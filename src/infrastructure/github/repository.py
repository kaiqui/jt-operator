import base64
from typing import List, Optional

import httpx

from src.application.ports.github_port import GitHubPort
from src.domain.github_models import PullRequestResult, RemediationFile
from src.infrastructure.github.client import GitHubAPIClient
from src.utils.json_logger import get_logger

logger = get_logger(__name__)

DEPLOY_YAML_PATH = "manifests/kubernetes/main/deploy.yaml"


class GitHubRepository(GitHubPort):
    def __init__(self, client: GitHubAPIClient) -> None:
        self._client = client

    async def branch_exists(
        self, repo_owner: str, repo_name: str, branch_name: str
    ) -> bool:
        try:
            await self._client.get(
                f"/repos/{repo_owner}/{repo_name}/git/ref/heads/{branch_name}"
            )
            return True
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return False
            raise
        except Exception:
            logger.exception(
                "Erro ao verificar existência da branch",
                extra={"branch": branch_name},
            )
            return False

    async def create_branch(
        self,
        repo_owner: str,
        repo_name: str,
        branch_name: str,
        base_branch: str,
    ) -> bool:
        try:
            ref = await self._client.get(
                f"/repos/{repo_owner}/{repo_name}/git/ref/heads/{base_branch}"
            )
            base_sha: str = ref["object"]["sha"]

            await self._client.post(
                f"/repos/{repo_owner}/{repo_name}/git/refs",
                {"ref": f"refs/heads/{branch_name}", "sha": base_sha},
            )

            logger.info(
                "Branch criada",
                extra={
                    "branch": branch_name,
                    "base": base_branch,
                    "sha": base_sha[:7],
                },
            )
            return True

        except Exception:
            logger.exception(
                "Erro ao criar branch",
                extra={"branch": branch_name, "base": base_branch},
            )
            return False

    async def get_file_content(
        self,
        repo_owner: str,
        repo_name: str,
        file_path: str,
        ref: str,
    ) -> Optional[str]:
        try:
            response = await self._client.get(
                f"/repos/{repo_owner}/{repo_name}/contents/{file_path}",
                params={"ref": ref},
            )
            raw: str = response.get("content", "")
            # A API do GitHub retorna o conteúdo com quebras de linha no base64
            decoded = base64.b64decode(raw.replace("\n", "")).decode("utf-8")
            return decoded
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.info(
                    "Arquivo não encontrado no repositório",
                    extra={"path": file_path, "ref": ref},
                )
                return None
            logger.exception(
                "Erro HTTP ao ler arquivo",
                extra={"path": file_path, "status": exc.response.status_code},
            )
            return None
        except Exception:
            logger.exception(
                "Erro ao ler conteúdo do arquivo", extra={"path": file_path}
            )
            return None

    async def commit_files(
        self,
        repo_owner: str,
        repo_name: str,
        branch_name: str,
        files: List[RemediationFile],
    ) -> bool:
        all_ok = True

        for f in files:
            try:
                # Verifica se o arquivo já existe para obter o SHA (necessário para update)
                existing_sha: Optional[str] = None
                try:
                    existing = await self._client.get(
                        f"/repos/{repo_owner}/{repo_name}/contents/{f.path}",
                        params={"ref": branch_name},
                    )
                    existing_sha = existing.get("sha")
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404:
                        raise

                payload: dict = {
                    "message": f.commit_message,
                    "content": base64.b64encode(f.content.encode()).decode(),
                    "branch": branch_name,
                }
                if existing_sha:
                    payload["sha"] = existing_sha

                await self._client.put(
                    f"/repos/{repo_owner}/{repo_name}/contents/{f.path}",
                    payload,
                )

                logger.info(
                    "Arquivo commitado",
                    extra={"path": f.path, "branch": branch_name},
                )

            except Exception:
                logger.exception(
                    "Erro ao commitar arquivo",
                    extra={"path": f.path, "branch": branch_name},
                )
                all_ok = False

        return all_ok

    async def create_pull_request(
        self,
        repo_owner: str,
        repo_name: str,
        branch_name: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> PullRequestResult:
        response = await self._client.post(
            f"/repos/{repo_owner}/{repo_name}/pulls",
            {
                "title": title,
                "body": body,
                "head": branch_name,
                "base": base_branch,
            },
        )

        pr = PullRequestResult(
            number=int(response["number"]),
            title=str(response["title"]),
            url=str(response["html_url"]),
            branch=branch_name,
            base_branch=base_branch,
        )

        logger.info(
            "Pull Request criado",
            extra={
                "pr_number": pr.number,
                "pr_url": pr.url,
                "branch": branch_name,
                "base": base_branch,
            },
        )
        return pr

    async def find_open_remediation_pr(
        self,
        repo_owner: str,
        repo_name: str,
        namespace: str,
        resource_name: str,
        base_branch: str,
    ) -> Optional[PullRequestResult]:
        return await self._find_remediation_pr_by_state(
            repo_owner=repo_owner,
            repo_name=repo_name,
            namespace=namespace,
            resource_name=resource_name,
            base_branch=base_branch,
            state="open",
            only_merged=False,
        )

    async def find_merged_remediation_pr(
        self,
        repo_owner: str,
        repo_name: str,
        namespace: str,
        resource_name: str,
        base_branch: str,
    ) -> Optional[PullRequestResult]:
        return await self._find_remediation_pr_by_state(
            repo_owner=repo_owner,
            repo_name=repo_name,
            namespace=namespace,
            resource_name=resource_name,
            base_branch=base_branch,
            state="closed",
            only_merged=True,
        )

    async def _find_remediation_pr_by_state(
        self,
        repo_owner: str,
        repo_name: str,
        namespace: str,
        resource_name: str,
        base_branch: str,
        state: str,
        only_merged: bool,
    ) -> Optional[PullRequestResult]:
        safe_name = resource_name.replace("/", "-")
        branch_prefix = f"fix/auto-remediation-{namespace}-{safe_name}-"

        try:
            page = 1
            while True:
                prs = await self._client.get_list(
                    f"/repos/{repo_owner}/{repo_name}/pulls",
                    params={
                        "state": state,
                        "base": base_branch,
                        "per_page": 100,
                        "page": page,
                    },
                )

                if not prs:
                    break

                for pr_data in prs:
                    head_ref: str = pr_data.get("head", {}).get("ref", "")
                    if not head_ref.startswith(branch_prefix):
                        continue
                    if only_merged and not pr_data.get("merged_at"):
                        continue
                    pr = PullRequestResult(
                        number=int(pr_data["number"]),
                        title=str(pr_data["title"]),
                        url=str(pr_data["html_url"]),
                        branch=head_ref,
                        base_branch=base_branch,
                    )
                    logger.info(
                        "PR de remediacao encontrado",
                        extra={
                            "pr_number": pr.number,
                            "pr_url": pr.url,
                            "branch": head_ref,
                            "state": state,
                            "merged": bool(pr_data.get("merged_at")),
                            "resource": f"{namespace}/{resource_name}",
                        },
                    )
                    return pr

                if len(prs) < 100:
                    break
                page += 1

            return None

        except Exception:
            logger.exception(
                "Erro ao buscar PR de remediacao",
                extra={"resource": f"{namespace}/{resource_name}", "state": state},
            )
            return None
