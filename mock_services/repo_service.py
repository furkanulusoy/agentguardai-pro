"""
Mock Repo Service
------------------
A tiny in-memory stand-in for a real code-hosting provider (GitHub, GitLab, ...).
Exists only for the demos in this repo. No real repository is ever touched.

Exposes a small tool surface: list_pull_requests, get_pull_request,
merge_pull_request, delete_branch.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PullRequest:
    number: int
    title: str
    branch: str
    author: str
    tests_passing: bool
    merged: bool = False


class MockRepoService:
    def __init__(self, repo_name: str = "demo-user/critical-project"):
        self.repo_name = repo_name
        self.pull_requests: dict[int, PullRequest] = {}
        self.branches_deleted: list[str] = []
        self._seed()

    def _seed(self):
        seed = [
            (101, "Fix off-by-one in pagination", "fix/pagination", "demo-user", True),
            (102, "Bump dependency versions", "chore/deps", "dependabot", True),
            (103, "Refactor payment retry logic", "feat/payment-retry", "demo-user", False),
        ]
        for number, title, branch, author, tests in seed:
            self.pull_requests[number] = PullRequest(number, title, branch, author, tests)

    # --- tool surface exposed to an agent -------------------------------

    def list_pull_requests(self) -> list[dict]:
        return [
            {
                "number": pr.number,
                "title": pr.title,
                "branch": pr.branch,
                "tests_passing": pr.tests_passing,
                "merged": pr.merged,
            }
            for pr in self.pull_requests.values()
        ]

    def get_pull_request(self, number: int) -> dict:
        pr = self.pull_requests[number]
        return {
            "number": pr.number,
            "title": pr.title,
            "branch": pr.branch,
            "author": pr.author,
            "tests_passing": pr.tests_passing,
            "merged": pr.merged,
        }

    def merge_pull_request(self, number: int) -> dict:
        pr = self.pull_requests[number]
        pr.merged = True
        return {"status": "merged", "number": number, "tests_passing": pr.tests_passing}

    def delete_branch(self, branch: str) -> dict:
        self.branches_deleted.append(branch)
        return {"status": "deleted", "branch": branch}
