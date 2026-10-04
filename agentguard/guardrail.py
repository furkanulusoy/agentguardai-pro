"""
AgentGuard -- the guardrail layer this project is actually about.

Sits between an agent and a connector and enforces three things at once:

1. Permission auditing  -- an action only runs if it's inside the task's
   declared scope. Anything wider than what the task actually needs is
   refused, even if the underlying connector *could* do it. This is the
   defense against "over-broad permissions".

2. Approval gate         -- actions marked "sensitive" in the policy
   (deletes, merges, sends, anything hard to undo) never run silently,
   even when they're in-scope. A human has to say yes first. This is the
   defense against "unapproved autonomous action".

3. Connector integrity   -- the guard also watches the REAL resource
   behind the connector (via a CallRecorder) and compares what actually
   happened to it against what the agent asked for and was approved to
   do. If a connector does something on the side that nobody authorized
   -- the classic malicious/compromised-connector move -- AgentGuard
   catches the mismatch and quarantines the connector immediately. This
   is the defense against a "fake/malicious connector".

Honest limitation, on purpose: integrity checking here catches undeclared
WRITE side effects on a monitored resource. It does not, by itself, stop
a connector from reading data it was legitimately given read access to
and exfiltrating it out-of-band (see docs/THREAT_MODEL.md). No single
layer catches everything -- least privilege plus vetted connectors still
matters. AgentGuard narrows the blast radius; it isn't a silver bullet.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from agentguard.monitor import CallRecorder
from agentguard.policy import Policy


class AuditLog(Protocol):
    """
    Structural type for anything that can receive audit events -- e.g.
    agentguard.audit_log.JsonlAuditLog, or your own sink (a logger, a
    message queue producer, whatever your real deployment needs). Not
    imported directly here to avoid a circular import; duck typing only.
    """

    def record(self, connector_name: str, event: GuardEvent) -> None: ...


class QuarantineStore(Protocol):
    """
    Structural type for anything that can persist quarantine state -- e.g.
    agentguard.quarantine_store.SqliteQuarantineStore. Not imported
    directly here to avoid a circular import; duck typing only.
    """

    def is_quarantined(self, connector_name: str) -> bool: ...
    def quarantine(self, connector_name: str, reason: str = "") -> None: ...


class PermissionDenied(Exception):
    pass


class ApprovalDenied(Exception):
    pass


class ConnectorQuarantined(Exception):
    pass


@dataclass
class GuardEvent:
    kind: str  # allowed | blocked_scope | approval_requested | approval_denied
               # | anomaly_detected | quarantined | blocked_quarantine
    action: str
    detail: str = ""


class AgentGuard:
    def __init__(
        self,
        connector,
        policy: Policy,
        monitored_resource: CallRecorder | None = None,
        approval_callback: Callable[[str, dict], bool] | None = None,
        audit_log: AuditLog | None = None,
        quarantine_store: QuarantineStore | None = None,
    ):
        self.connector = connector
        self.policy = policy
        self.monitored_resource = monitored_resource
        self.approval_callback = approval_callback or (lambda action, ctx: False)
        self.audit_log = audit_log
        self.quarantine_store = quarantine_store
        self.events: list[GuardEvent] = []
        # If a quarantine_store is wired up and this connector was already
        # quarantined in a previous run, restore that -- a restart must
        # never silently un-quarantine a connector caught misbehaving.
        self.quarantined = (
            quarantine_store.is_quarantined(policy.connector_name)
            if quarantine_store is not None
            else False
        )

    def _log(self, kind: str, action: str, detail: str = ""):
        event = GuardEvent(kind, action, detail)
        self.events.append(event)
        if self.audit_log is not None:
            self.audit_log.record(self.policy.connector_name, event)

    def call(self, action: str, *args, **kwargs):
        if self.quarantined:
            self._log("blocked_quarantine", action, "bağlayıcı karantinada")
            raise ConnectorQuarantined(
                f"'{self.policy.connector_name}' daha önceki bir bütünlük ihlali "
                f"sonrası karantinada -- yeni çağrılar reddediliyor."
            )

        # 1. İzin denetimi: bu aksiyon görevin kapsamında mı?
        if not self.policy.is_permitted(action):
            reason = (
                f"'{action}' görevin tanımlı kapsamının dışında "
                f"{sorted(self.policy.task_scope)}"
            )
            self._log("blocked_scope", action, reason)
            raise PermissionDenied(
                f"'{action}' aksiyonu görev kapsamı için izinli değil: "
                f"{sorted(self.policy.task_scope)}."
            )

        # 2. Onay kapısı: hassas aksiyonlar asla sessizce çalışmaz.
        if self.policy.requires_approval(action):
            self._log("approval_requested", action)
            approved = self.approval_callback(action, {"args": args, "kwargs": kwargs})
            if not approved:
                self._log("approval_denied", action)
                raise ApprovalDenied(f"'{action}' için insan onayı reddedildi.")

        # 3. Bu çağrının gerçekte neyi tetiklediğini anlayabilmek için,
        #    çalıştırmadan önce alttaki kaynağın çağrı geçmişinin anlık
        #    görüntüsünü al.
        watermark = len(self.monitored_resource.log) if self.monitored_resource else None

        result = getattr(self.connector, action)(*args, **kwargs)

        # 4. Bağlayıcı bütünlük kontrolü: bu tek yetkilendirilmiş çağrı,
        #    arkasındaki gerçek kaynak üzerinde yetkilendirdiğimizden
        #    FAZLASINI mı tetikledi?
        if self.monitored_resource is not None:
            assert watermark is not None  # set above whenever monitored_resource is set
            new_calls = self.monitored_resource.calls_since(watermark)
            unexpected = [c for c in new_calls if c.action != action]
            if unexpected:
                names = ", ".join(sorted({c.action for c in unexpected}))
                self._log(
                    "anomaly_detected",
                    action,
                    f"bağlayıcı, gerçek kaynak üzerinde bildirilmemiş aksiyon(lar) "
                    f"tetikledi: {names}",
                )
                self.quarantined = True
                quarantine_detail = f"'{self.policy.connector_name}' karantinaya alındı"
                if self.quarantine_store is not None:
                    self.quarantine_store.quarantine(
                        self.policy.connector_name, reason=quarantine_detail
                    )
                self._log("quarantined", action, quarantine_detail)
                raise ConnectorQuarantined(
                    f"'{self.policy.connector_name}', '{action}' işlenirken sessizce "
                    f"bildirilmemiş aksiyon(lar) tetikledi ({names}). "
                    f"Bağlayıcı karantinaya alınıyor."
                )

        self._log("allowed", action)
        return result
