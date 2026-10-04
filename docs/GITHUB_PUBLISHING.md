# GitHub publication checklist

Use this checklist after the first push. GitHub Desktop publishes Git commits, but repository metadata and security settings are configured on GitHub.com.

## Repository identity

- Repository name: `agentguardai-pro`
- Description: `Self-hosted control plane for AI agent actions, human approvals, scoped connectors, and audit trails.`
- Visibility: choose deliberately. A public repository exposes the complete Git history, Actions logs, issues, and source code.
- Default branch: `main`

Recommended topics:

```text
ai-security
agent-governance
ai-agents
human-in-the-loop
llm-security
mcp
fastapi
react
postgresql
self-hosted
oauth
audit-logging
```

Do not describe the project as compliance-certified, production-ready SaaS, or universally exactly-once. The current verified position is a controlled self-hosted pilot; see `docs/RELEASE_NOTES.md`.

## Social preview

Upload `docs/assets/agentguard-social-preview.jpg` from:

**Settings → General → Social preview → Edit → Upload an image**

The file is 1280×640 and under GitHub's 1 MB upload limit.

## Repository settings

After the first push:

1. Confirm `main` is the default branch.
2. Enable **Issues** and **Discussions** only if they will be monitored.
3. Enable **Private vulnerability reporting** under **Settings → Security → Code security and analysis**.
4. Enable the dependency graph, Dependabot alerts, and Dependabot security updates.
5. Enable secret scanning and push protection when GitHub makes them available for the repository.
6. Add a ruleset for `main`: require a pull request, require the `tests` workflow, block force pushes, and block deletion. Apply this after the initial push so the first branch can be published.
7. Pin the repository on the `furkanulusoy` profile.

## First release

Create a release only after the pushed GitHub Actions workflow is green. Suggested tag and title:

```text
Tag: v0.4.0
Title: AgentGuard AI Pro v0.4.0 — self-hosted pilot
```

Build the release notes from `docs/RELEASE_NOTES.md`. Keep its pilot limitations intact.

## Final checks before making the repository public

```powershell
python scripts/check-secrets.py
git status --short
git log --all -S "PASTE_YOUR" --oneline
```

Review GitHub Desktop's **Changes** tab before committing. Files under `.local/`, `.env`, dependency directories, build output, and editor state must remain untracked.
