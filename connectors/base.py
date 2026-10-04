"""
Base connector abstraction for real (non-mock) external services --
Gmail, GitHub, Slack, Outlook, SMS providers, etc.

Deliberately SYNCHRONOUS: agentguard.guardrail.AgentGuard.call() invokes
`getattr(self.connector, action)(*args, **kwargs)` synchronously, and
103 existing tests depend on that interface -- this project's own rule
is "don't rewrite what works," so connectors match it rather than the
other way around. When a connector is called from the async apps/api
layer, bridge with starlette.concurrency.run_in_threadpool (or
asyncio.to_thread) so a blocking HTTP call doesn't block the event
loop -- that's the API route's job, not the connector's.

A connector instance IS the `connector` object AgentGuard wraps. Its
action methods (read_message, send_email, ...) are exactly the action
names agentguard.policy.Policy.task_scope / sensitive_actions already
govern -- there is no separate "capability" concept layered on top;
capabilities ARE action method names. Only define the methods a given
connector genuinely supports (e.g. an SMS connector has no meaningful
"list_resources") -- do not stub out methods with NotImplementedError
just to satisfy a generic interface. supported_actions exists so the
platform can introspect what a connector type offers without importing
every SDK to find out.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class ConnectorError(Exception):
    """Base class for connector-specific failures. Catch this at the API
    boundary rather than letting a connector's own SDK exceptions
    (google-api-python-client, PyGithub, ...) leak through unhandled."""


class ConnectorAuthenticationError(ConnectorError):
    """The stored credential is missing, expired, or was rejected by the
    upstream service. Distinct from ConnectorError so callers can
    specifically prompt "reconnect this integration" rather than a
    generic failure message."""


class BaseConnector(ABC):
    #: Short, stable identifier -- matches Credential.connector_type in
    #: infrastructure/database/models/credential.py, e.g. "gmail".
    connector_type: ClassVar[str]

    #: Action method names this connector CLASS can support in principle
    #: (before any per-tenant OAuth scope narrows it further). Informs
    #: what a policy author can legally put in Policy.task_scope for
    #: this connector_type -- not enforced here, just declared.
    supported_actions: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    def authenticate(self, credential: dict[str, Any]) -> None:
        """Load whatever this connector needs from a decrypted credential
        (infrastructure/secrets) -- shape is connector-specific (an OAuth
        token dict for Gmail, an API key string for an SMS provider).
        Must not make a network call itself; connect() does that."""

    @abstractmethod
    def connect(self) -> None:
        """Open the real session/client (e.g. build the Gmail API client)
        using what authenticate() loaded. Must be called before any
        action method or validate()."""

    @abstractmethod
    def disconnect(self) -> None:
        """Release any open session/client. Must be safe to call even if
        connect() was never called (e.g. in a finally block)."""

    @abstractmethod
    def validate(self) -> bool:
        """A cheap, real check that the current credential still works
        (e.g. a lightweight "who am I" call) -- lets a caller detect a
        revoked/expired OAuth grant before it fails loudly mid-task,
        rather than as a side effect of the first real action."""
