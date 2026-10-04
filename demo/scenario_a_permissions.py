"""
Scenario A -- Over-broad permissions ("Aşırı geniş izinler")

The task is simple: "check the inbox for a new invoice." That only
requires listing and reading messages. But like almost every real
connector setup, the agent was actually granted the full tool surface
(including delete), because narrowing scopes per-task is tedious and
nobody got around to it.

A bug (or a manipulated instruction -- doesn't matter which) makes the
agent call delete_all() instead of just reading. Nothing about the
*task* changes between the two runs below -- only whether a guardrail
was enforcing the difference between "what was granted" and "what the
task actually needed."
"""
from __future__ import annotations

from agentguard import AgentGuard, PermissionDenied, Policy
from demo.narrate import header, line, result, sub
from mock_services.inbox import MockInbox


class BuggyInboxAgent:
    """
    Simulates a real failure mode: the agent was asked to 'clean up and
    check for the invoice' and a bad interpretation of 'clean up' turns
    into wiping the inbox. It has no concept of scope of its own -- it
    just calls whatever the connector exposes.
    """

    def __init__(self, connector):
        self.connector = connector

    def run_task(self, call):
        # call() is either "call the connector directly" (unprotected)
        # or "call through AgentGuard" (protected) -- the agent's logic
        # doesn't know or care which.
        messages = call("list_messages")
        line(f"  agent, inbox'ta {len(messages)} mesaj görüyor")
        line("  agent'ın (hatalı) planı: okumadan önce 'temizlik yap' -> delete_all()")
        call("delete_all")
        return call("list_messages")


def run_unprotected():
    sub("AgentGuard OLMADAN -- agent'ın tam, denetimsiz erişimi var")
    inbox = MockInbox()
    agent = BuggyInboxAgent(inbox)

    def raw_call(action, *a, **kw):
        return getattr(inbox, action)(*a, **kw)

    before = inbox.remaining_count()
    agent.run_task(raw_call)
    after = inbox.remaining_count()

    line(f"  önce: {before} mesaj, sonra: {after} mesaj")
    result("inbox görevden sağ çıktı", after == before,
           "tamamen silindi" if after != before else "")
    return after == before


def run_protected():
    sub("AgentGuard İLE -- kapsam, görevin gerçekten ihtiyacına daraltıldı")
    inbox = MockInbox()
    agent = BuggyInboxAgent(inbox)

    policy = Policy(
        connector_name="inbox",
        task_scope={"list_messages", "read_message"},  # delete_all YOK
    )
    guard = AgentGuard(connector=inbox, policy=policy)

    before = inbox.remaining_count()
    try:
        agent.run_task(guard.call)
    except PermissionDenied as e:
        line(f"  AgentGuard çağrıyı engelledi: {e}")
    after = inbox.remaining_count()

    line(f"  önce: {before} mesaj, sonra: {after} mesaj")
    result("inbox görevden sağ çıktı", after == before,
           "tamamen silindi" if after != before else "")
    return after == before


def main():
    header("SENARYO A: Aşırı geniş izinler")
    without_ok = run_unprotected()
    with_ok = run_protected()

    sub("Özet")
    result("AgentGuard olmadan, inbox sağ çıktı", without_ok)
    result("AgentGuard ile, inbox sağ çıktı", with_ok)


if __name__ == "__main__":
    main()
