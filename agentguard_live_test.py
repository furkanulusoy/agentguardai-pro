"""Run a manual, real API test through the AgentGuard Python SDK.

This example is intended to be launched from the user's PowerShell session.
It is not part of the automated CI test suite and never embeds credentials.
"""

import asyncio
import os

from agentguard_sdk import AgentGuardClient, AgentGuardDenied, AgentGuardError, AgentGuardTimeout

AGENT_KEY_PLACEHOLDER = "PASTE_YOUR_AGENTGUARD_AGENT_KEY_HERE"
CONNECTOR_ID_PLACEHOLDER = "PASTE_YOUR_AGENTGUARD_CONNECTOR_ID_HERE"


async def main() -> None:
    agent_key = os.environ.get("AGENTGUARD_AGENT_KEY", "").strip()
    connector_id = os.environ.get("AGENTGUARD_CONNECTOR_ID", "").strip()
    if not agent_key or agent_key == AGENT_KEY_PLACEHOLDER:
        print(
            "AGENTGUARD_AGENT_KEY is missing. Do not put the value in source code; "
            "set it securely in the current PowerShell session."
        )
        return
    if not connector_id or connector_id == CONNECTOR_ID_PLACEHOLDER:
        print(
            "AGENTGUARD_CONNECTOR_ID is missing. Copy the connector ID from the dashboard "
            "and set it in the current PowerShell session."
        )
        return

    action = os.environ.get("AGENTGUARD_ACTION", "list_repos")
    if action == "list_repos":
        params = {"max_results": 10}
    elif action == "close_issue":
        repo = os.environ.get("AGENTGUARD_GITHUB_REPO", "").strip()
        issue_number = os.environ.get("AGENTGUARD_GITHUB_ISSUE_NUMBER", "").strip()
        if not repo or not issue_number.isdigit():
            print(
                "AGENTGUARD_GITHUB_REPO and a numeric AGENTGUARD_GITHUB_ISSUE_NUMBER "
                "are required for close_issue."
            )
            return
        params = {"repo": repo, "issue_number": int(issue_number)}
    else:
        print(f"Action not supported by this safe example: {action}")
        return

    base_url = os.environ.get("AGENTGUARD_BASE_URL", "http://localhost:5000/api")
    async with AgentGuardClient(agent_key, base_url=base_url) as client:
        print(f"--> Connecting to AgentGuard for action: github.{action}")
        if action == "close_issue":
            print("--> This is a HIGH-risk action; waiting for human approval.")
            print("--> Review the request on the Approvals page in the dashboard.")
        try:
            outcome = await client.run(connector_id, action, params)
        except AgentGuardDenied:
            print("--> RESULT: A human reviewer denied the action.")
            return
        except AgentGuardTimeout:
            print("--> RESULT: The approval request expired before a decision was made.")
            return
        except AgentGuardError as exc:
            print(f"--> RESULT: AgentGuard denied the request or the provider failed:\n    {exc}")
            return

        print(f"--> RESULT: Success. Returned data:\n{outcome['result']}")


if __name__ == "__main__":
    asyncio.run(main())
