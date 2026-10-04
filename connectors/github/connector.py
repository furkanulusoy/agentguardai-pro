"""
Real GitHub connector -- PyGithub wraps the actual GitHub REST API.

Two actions for this first pass, deliberately paired to demonstrate
both halves of AgentGuard's approval gate on a second, different kind
of connector than Gmail: list_repos (a safe read) and close_issue (a
real, consequential WRITE). Gmail's first pass only ever demonstrated
the gate on a *read* (read_message); this proves the same mechanism
covers mutating actions on a completely different service too, not
something special-cased for Gmail.
"""

from __future__ import annotations

from itertools import islice
from typing import Any

from github import Auth, Github
from github.GithubException import BadCredentialsException, GithubException

from connectors.base import BaseConnector, ConnectorAuthenticationError, ConnectorError

SCOPES = ["repo"]


class GitHubConnector(BaseConnector):
    connector_type = "github"
    supported_actions = frozenset({"list_repos", "close_issue"})

    def __init__(self) -> None:
        self._token: str | None = None
        self._client: Github | None = None

    def authenticate(self, credential: dict[str, Any]) -> None:
        """`credential` is whatever infrastructure/secrets decrypted --
        shape matches connectors/github/oauth.py's exchange_code_for_credential()."""
        self._token = credential["access_token"]

    def connect(self) -> None:
        if self._token is None:
            raise RuntimeError("authenticate() must be called before connect()")
        try:
            self._client = Github(auth=Auth.Token(self._token), retry=0, timeout=20)
        except Exception as exc:
            raise ConnectorAuthenticationError(f"Could not build GitHub client: {exc}") from exc

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None

    def validate(self) -> bool:
        if self._client is None:
            return False
        try:
            _ = self._client.get_user().login  # a cheap, real "who am I" call
            return True
        except GithubException:
            return False

    def list_repos(self, max_results: int = 10) -> list[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("not connected")
        try:
            repos = islice(self._client.get_user().get_repos(), max_results)
            return [
                {
                    "full_name": r.full_name,
                    "private": r.private,
                    "open_issues": r.open_issues_count,
                }
                for r in repos
            ]
        except BadCredentialsException as exc:
            raise ConnectorAuthenticationError("GitHub credential was rejected") from exc
        except GithubException as exc:
            raise ConnectorError(f"GitHub list_repos failed: {exc}") from exc

    def close_issue(self, repo: str, issue_number: int) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("not connected")
        try:
            gh_repo = self._client.get_repo(repo)
            issue = gh_repo.get_issue(issue_number)
            issue.edit(state="closed")
            return {
                "repo": repo,
                "issue_number": issue_number,
                "title": issue.title,
                "state": issue.state,
            }
        except BadCredentialsException as exc:
            raise ConnectorAuthenticationError("GitHub credential was rejected") from exc
        except GithubException as exc:
            raise ConnectorError(f"GitHub close_issue failed: {exc}") from exc
