"""
Scenario B -- Unapproved autonomous action ("Onaysız otonom aksiyon")

The task here genuinely needs merge access -- this is a "daily PR
maintenance" agent, merging is the point. The problem isn't scope, it's
that merging is irreversible and the agent does it unattended, with no
human in the loop, even for a PR whose tests are failing.

AgentGuard doesn't take merge_pull_request out of scope (the task needs
it) -- it marks it "sensitive", so it always pauses for a yes/no from a
human before it actually happens, regardless of what the agent decided.
"""
from __future__ import annotations

from agentguard import AgentGuard, ApprovalDenied, Policy
from demo.narrate import header, line, result, sub
from mock_services.repo_service import MockRepoService


class AutonomousMaintenanceAgent:
    """Runs unattended (e.g. on a nightly schedule) and merges every open PR it finds."""

    def __init__(self, connector):
        self.connector = connector

    def run_task(self, call):
        prs = call("list_pull_requests")
        merged = []
        for pr in prs:
            if pr["merged"]:
                continue
            line(f"  agent, PR #{pr['number']} '{pr['title']}' "
                 f"(testler geçiyor mu={pr['tests_passing']}) merge etmeye karar veriyor")
            try:
                call("merge_pull_request", pr["number"])
                merged.append(pr["number"])
            except ApprovalDenied:
                line(f"    -> onay reddedildi, PR #{pr['number']} merge edilmedi")
        return merged


def run_unprotected():
    sub("AgentGuard OLMADAN -- agent otonom olarak merge ediyor, insan kontrolü yok")
    repo = MockRepoService()
    agent = AutonomousMaintenanceAgent(repo)

    def raw_call(action, *a, **kw):
        return getattr(repo, action)(*a, **kw)

    merged = agent.run_task(raw_call)
    bad_merges = [
        n for n in merged if not repo.pull_requests[n].tests_passing
    ]
    line(f"  merge edilenler: {merged}")
    result("testleri geçmeyen hiçbir PR merge edilmedi", len(bad_merges) == 0,
           f"testleri geçmediği halde merge edilenler: {bad_merges}" if bad_merges else "")
    return len(bad_merges) == 0


# set by run_protected() so human_reviewer() can inspect PR state
_last_repo: MockRepoService | None = None


def human_reviewer(action: str, ctx: dict) -> bool:
    """
    Stands in for a real person getting a notification and tapping
    approve/deny. Here, scripted to reflect an obviously reasonable
    policy: don't approve merging a PR whose tests are failing.
    """
    number = ctx["args"][0] if ctx["args"] else ctx["kwargs"].get("number")
    # In the real thing this would look up the PR; for the demo we just
    # peek at the repo passed in via closure below.
    assert _last_repo is not None, "human_reviewer called before run_protected() set _last_repo"
    return _last_repo.pull_requests[number].tests_passing


def run_protected():
    global _last_repo
    sub("AgentGuard İLE -- merge işlemleri açık insan onayı gerektiriyor")
    repo = MockRepoService()
    _last_repo = repo
    agent = AutonomousMaintenanceAgent(repo)

    policy = Policy(
        connector_name="repo",
        task_scope={"list_pull_requests", "get_pull_request", "merge_pull_request"},
        sensitive_actions={"merge_pull_request"},
    )
    guard = AgentGuard(connector=repo, policy=policy, approval_callback=human_reviewer)

    merged = agent.run_task(guard.call)
    bad_merges = [n for n in merged if not repo.pull_requests[n].tests_passing]
    line(f"  merge edilenler: {merged}")
    result("testleri geçmeyen hiçbir PR merge edilmedi", len(bad_merges) == 0,
           f"testleri geçmediği halde merge edilenler: {bad_merges}" if bad_merges else "")
    return len(bad_merges) == 0


def main():
    header("SENARYO B: Onaysız otonom aksiyon")
    without_ok = run_unprotected()
    with_ok = run_protected()

    sub("Özet")
    result("AgentGuard olmadan, kötü merge önlendi", without_ok)
    result("AgentGuard ile, kötü merge önlendi", with_ok)


if __name__ == "__main__":
    main()
