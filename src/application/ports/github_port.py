from abc import ABC, abstractmethod
from typing import List, Optional

from src.domain.github_models import PullRequestResult, RemediationFile


class GitHubPort(ABC):
    @abstractmethod
    async def branch_exists(
        self, repo_owner: str, repo_name: str, branch_name: str
    ) -> bool:
        pass

    @abstractmethod
    async def create_branch(
        self,
        repo_owner: str,
        repo_name: str,
        branch_name: str,
        base_branch: str,
    ) -> bool:
        pass

    @abstractmethod
    async def get_file_content(
        self,
        repo_owner: str,
        repo_name: str,
        file_path: str,
        ref: str,
    ) -> Optional[str]:
        pass

    @abstractmethod
    async def commit_files(
        self,
        repo_owner: str,
        repo_name: str,
        branch_name: str,
        files: List[RemediationFile],
    ) -> bool:
        pass

    @abstractmethod
    async def create_pull_request(
        self,
        repo_owner: str,
        repo_name: str,
        branch_name: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> PullRequestResult:
        pass

    @abstractmethod
    async def find_open_remediation_pr(
        self,
        repo_owner: str,
        repo_name: str,
        namespace: str,
        resource_name: str,
        base_branch: str,
    ) -> Optional[PullRequestResult]:
        pass

    @abstractmethod
    async def find_merged_remediation_pr(
        self,
        repo_owner: str,
        repo_name: str,
        namespace: str,
        resource_name: str,
        base_branch: str,
    ) -> Optional[PullRequestResult]:
        pass
