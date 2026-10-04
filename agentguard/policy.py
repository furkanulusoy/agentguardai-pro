"""
Policy definitions for AgentGuard.

A Policy describes, for a given task, which actions an agent is actually
allowed to take against a given connector -- and which of those actions
are sensitive enough to require a human's explicit approval before they
run, no matter what the declared scope says.

The core idea: "task scope" (what this specific job actually needs) is
almost always narrower than "granted scope" (what the connector/OAuth
app was given). AgentGuard enforces the narrower one.

Policies can be built directly in Python (as the demos do) or loaded from
a config file with Policy.from_file() / Policy.from_dict() -- JSON always
works (stdlib only); YAML works if PyYAML is installed
(`pip install agentguard[yaml]`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class PolicyConfigError(ValueError):
    """Raised when a policy config file is missing required fields or malformed."""


@dataclass
class Policy:
    connector_name: str
    task_scope: set[str]                          # actions actually needed for the task at hand
    sensitive_actions: set[str] = field(default_factory=set)  # always gate these behind approval

    def is_permitted(self, action: str) -> bool:
        return action in self.task_scope

    def requires_approval(self, action: str) -> bool:
        return action in self.sensitive_actions

    # -- config loading ---------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Policy:
        try:
            connector_name = data["connector_name"]
            task_scope = data["task_scope"]
        except KeyError as e:
            raise PolicyConfigError(f"policy config is missing required field: {e}") from e

        if not isinstance(task_scope, list) or not all(isinstance(a, str) for a in task_scope):
            raise PolicyConfigError("'task_scope' must be a list of action-name strings")

        sensitive_actions = data.get("sensitive_actions", [])
        if not isinstance(sensitive_actions, list) or not all(
            isinstance(a, str) for a in sensitive_actions
        ):
            raise PolicyConfigError("'sensitive_actions' must be a list of action-name strings")

        unknown = set(sensitive_actions) - set(task_scope)
        if unknown:
            raise PolicyConfigError(
                f"'sensitive_actions' contains action(s) not present in 'task_scope': "
                f"{sorted(unknown)} -- an action that's never permitted doesn't need an "
                f"approval gate on top of being blocked."
            )

        private_looking = {a for a in task_scope if a.startswith("_")}
        if private_looking:
            raise PolicyConfigError(
                f"'task_scope' contains action name(s) starting with '_': "
                f"{sorted(private_looking)} -- AgentGuard.call() reaches the connector "
                f"via getattr(connector, action), so a private/dunder-looking name here "
                f"could expose unintended attributes instead of a real tool method."
            )

        return cls(
            connector_name=connector_name,
            task_scope=set(task_scope),
            sensitive_actions=set(sensitive_actions),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> Policy:
        """
        Load a Policy from a .json, .yaml, or .yml file.

        JSON always works (stdlib `json` only). YAML requires PyYAML --
        raises a clear PolicyConfigError telling you to
        `pip install agentguard[yaml]` if it's missing, rather than a raw
        ImportError.
        """
        path = Path(path)
        text = path.read_text(encoding="utf-8")

        if path.suffix in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore[import-untyped]
            except ImportError as e:
                raise PolicyConfigError(
                    "reading a .yaml policy file requires PyYAML -- install it with "
                    "`pip install agentguard[yaml]`, or write the policy as .json instead."
                ) from e
            data = yaml.safe_load(text)
        elif path.suffix == ".json":
            data = json.loads(text)
        else:
            raise PolicyConfigError(
                f"unrecognized policy file extension '{path.suffix}' -- use .json, .yaml, or .yml"
            )

        if not isinstance(data, dict):
            raise PolicyConfigError(f"{path}: policy file must contain a single object/mapping")

        return cls.from_dict(data)
