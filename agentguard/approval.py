"""
A real (not scripted) approval callback: prompts a human and blocks
until they answer, instead of a hardcoded function deciding for a demo.

Use it as:

    from agentguard import AgentGuard, Policy
    from agentguard.approval import TerminalApprover

    guard = AgentGuard(connector=..., policy=..., approval_callback=TerminalApprover())

Honest limitation: a terminal prompt only works for an agent you're
actively watching run. It is NOT a real approval channel for an
unattended/scheduled agent -- nobody is looking at a terminal at 3am.
For that you need a real notification channel (Slack, paging, a web
approval queue with a timeout-and-deny default) -- see
docs/ROADMAP_TO_PRODUCTION.md. This class exists to make "real approval,
not a scripted stand-in" concretely runnable today, not to claim it's
production-sufficient on its own.
"""
from __future__ import annotations

import itertools
import json
import logging
import threading
import urllib.request
from collections.abc import Callable

logger = logging.getLogger(__name__)


def _safe_context_summary(ctx: dict) -> str:
    """Describe an approval request without exposing argument values."""
    args = ctx.get("args")
    kwargs = ctx.get("kwargs")
    positional_count = len(args) if isinstance(args, (list, tuple)) else 0
    keyword_count = len(kwargs) if isinstance(kwargs, dict) else 0
    return (
        f"positional_argument_count={positional_count} "
        f"keyword_argument_count={keyword_count}"
    )


class TerminalApprover:
    """Blocks on an interactive y/n prompt at the terminal for every sensitive action."""

    def __init__(
        self,
        input_fn: Callable[[str], str] = input,
        print_fn: Callable[[str], None] = print,
    ):
        # Injected so this is unit-testable without a real terminal.
        self._input = input_fn
        self._print = print_fn

    def __call__(self, action: str, ctx: dict) -> bool:
        self._print(f"\n[AgentGuard] Onay gerekiyor: '{action}'")
        self._print(f"  {_safe_context_summary(ctx)}")
        while True:
            answer = self._input("  Onaylıyor musunuz? [e/h]: ").strip().lower()
            if answer in ("e", "evet", "y", "yes"):
                return True
            if answer in ("h", "hayır", "hayir", "n", "no"):
                return False
            self._print("  Lütfen 'e' (evet) ya da 'h' (hayır) yazın.")


class AutoDenyApprover:
    """
    Fail-closed default: denies every sensitive action without asking
    anyone. Useful as the default for an unattended agent until a real
    approval channel is wired up -- refusing safely beats approving
    silently.
    """

    def __call__(self, action: str, ctx: dict) -> bool:
        return False


class QueueApprover:
    """
    Async human-in-the-loop over a queue: the thread that calls
    AgentGuard.call() BLOCKS (with a timeout) until something else --
    an HTTP endpoint, a Slack button webhook, whatever your real
    channel is -- calls resolve(id, approved). Fails closed (denies) on
    timeout, so a channel that never gets a response doesn't leave an
    agent's sensitive action hanging open forever.

    This is the piece server/app.py wires up behind two HTTP endpoints
    (submit a tool call that blocks on this, resolve it from another
    request) -- see that file for the concrete deployment shape.
    """

    def __init__(self, timeout_seconds: float = 120.0):
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._id_counter = itertools.count(1)
        self._pending: dict[int, dict] = {}  # id -> {"event": Event, "approved": bool}

    def __call__(self, action: str, ctx: dict) -> bool:
        approval_id = next(self._id_counter)
        event = threading.Event()
        with self._lock:
            self._pending[approval_id] = {
                "event": event,
                "approved": False,
                "resolved": False,
                "action": action,
                "ctx": ctx,
            }
        got_answer = event.wait(timeout=self._timeout)
        with self._lock:
            entry = self._pending.pop(approval_id, {"approved": False})
        return bool(got_answer and entry["approved"])

    def pending(self) -> list[dict]:
        with self._lock:
            return [
                {"id": aid, "action": e["action"], "ctx": e["ctx"]}
                for aid, e in self._pending.items()
            ]

    def resolve(self, approval_id: int, approved: bool) -> bool:
        """Returns True if a pending, not-yet-resolved approval with this id was found.

        Idempotent by design: a second resolve() call for the same id (a
        duplicate submission, a replayed request) is ignored rather than
        overwriting the first decision -- otherwise a race between two
        resolve() calls could flip an already-denied action to approved
        before AgentGuard.call() consumes the result.
        """
        with self._lock:
            entry = self._pending.get(approval_id)
            if entry is None or entry["resolved"]:
                return False
            entry["resolved"] = True
            entry["approved"] = approved
            entry["event"].set()
        return True


def _post_to_slack(webhook_url: str, body: bytes) -> None:
    # urlopen() itself raises urllib.error.HTTPError/URLError on a failed
    # request/non-2xx response -- SlackNotifyApprover.__call__ already
    # catches and logs that, so no manual status check is needed here.
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5):  # noqa: S310 -- fixed https webhook URL
        pass


class SlackNotifyApprover:
    """
    Wraps another approval callback (typically QueueApprover, wired to
    server/app.py's /approvals/<id> endpoint) and posts a one-way
    notification to a Slack Incoming Webhook every time a sensitive
    action needs approval, before delegating to the wrapped approver for
    the actual decision.

    Honest limitation, on purpose: this is a notification, not an
    interactive Slack button. Slack can't call back into AgentGuard from
    a button click -- that needs a public HTTPS endpoint plus Slack
    request-signature verification, a materially bigger and more
    security-sensitive piece of surface (see
    docs/ROADMAP_TO_PRODUCTION.md). This class exists to close the gap
    TerminalApprover has ("nobody is looking at a terminal at 3am") by
    making sure a human actually SEES that an approval is pending -- the
    approval itself still happens through the existing /approvals/<id>
    API.

    If the Slack POST itself fails (bad URL, network issue, Slack
    outage), that failure is logged and swallowed, never raised -- a
    missing notification must never be the reason a legitimate approval
    silently hangs or gets denied. The wrapped approver is always still
    asked for a decision.
    """

    def __init__(
        self,
        webhook_url: str,
        wrapped: Callable[[str, dict], bool],
        post_fn: Callable[[str, bytes], None] | None = None,
    ):
        self._webhook_url = webhook_url
        self._wrapped = wrapped
        # Injected so this is unit-testable without a real network call.
        # Deliberately NOT a `post_fn: ... = _post_to_slack` default argument:
        # default argument values are bound once, at import time, which would
        # make `unittest.mock.patch("agentguard.approval._post_to_slack")`
        # silently not apply to instances created without an explicit
        # post_fn -- this late-binding lookup (inside __init__, at call time)
        # is what makes that patch actually take effect.
        self._post = post_fn or _post_to_slack

    def __call__(self, action: str, ctx: dict) -> bool:
        body = json.dumps({"text": self._format_message(action, ctx)}).encode("utf-8")
        try:
            self._post(self._webhook_url, body)
        except Exception:
            logger.warning("Slack bildirimi gonderilemedi (aksiyon: %s)", action, exc_info=True)
        return self._wrapped(action, ctx)

    @staticmethod
    def _format_message(action: str, ctx: dict) -> str:
        return (
            f":rotating_light: AgentGuard onayi bekleniyor: `{action}`\n"
            f"{_safe_context_summary(ctx)}\n"
            f"Mevcut onay API'sini kullanarak onaylayin/reddedin "
            f"(GET /approvals/pending, POST /approvals/<id>)."
        )
