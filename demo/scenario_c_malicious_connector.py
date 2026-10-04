"""
Scenario C -- Malicious / fake connector ("Kötü niyetli / sahte bağlayıcı")

The agent just wants to read one message through what looks like an
ordinary, narrowly-scoped ("read_messages" only) inbox connector. The
connector is not what it claims to be: every read also silently
exfiltrates the message body to an outside sink AND silently deletes an
older message behind the scenes.

Honest note (see docs/THREAT_MODEL.md): AgentGuard's integrity check
catches the connector doing something it was never authorized to do
(the undeclared delete) and quarantines it immediately, which stops
further damage. It does NOT retroactively un-leak the one message the
connector legitimately had permission to read -- no guardrail can
un-ring that bell. That's why "only wire up connectors you trust" still
matters; AgentGuard narrows the blast radius, it doesn't replace vetting.
"""
from __future__ import annotations

from agentguard import AgentGuard, ConnectorQuarantined, Policy
from agentguard.monitor import CallRecorder
from demo.narrate import header, line, result, sub
from mock_services.inbox import MockInbox
from mock_services.malicious_connector import AttackerSink, MaliciousConnector


class ReadOnlyAgent:
    """Just wants to read the newest message. Nothing more."""

    def __init__(self, connector):
        self.connector = connector

    def run_task(self, call):
        messages = call("list_messages")
        newest = messages[-1]
        line(f"  agent, #{newest['id']} numaralı mesajı okuyor: '{newest['subject']}'")
        return call("read_message", newest["id"])


def run_unprotected():
    sub("AgentGuard OLMADAN -- bağlayıcının beyan ettiği kapsama körü körüne güveniliyor")
    inbox = MockInbox()
    sink = AttackerSink()
    connector = MaliciousConnector(inbox, sink)
    agent = ReadOnlyAgent(connector)

    def raw_call(action, *a, **kw):
        return getattr(connector, action)(*a, **kw)

    before = inbox.remaining_count()
    agent.run_task(raw_call)
    after = inbox.remaining_count()

    line(f"  önce: {before} mesaj, sonra: {after} mesaj")
    line(f"  saldırgan sunucusuna gizlice sızdırılan mesaj sayısı: {len(sink.received)}")
    ok = after == before and len(sink.received) == 0
    result("bildirilmemiş hiçbir yan etki oluşmadı", ok)
    return ok


def run_protected():
    sub("AgentGuard İLE -- gerçek kaynak, bildirilmemiş yan etkiler için izleniyor")
    real_inbox = MockInbox()
    monitored_inbox = CallRecorder(real_inbox, label="inbox")
    sink = AttackerSink()
    connector = MaliciousConnector(monitored_inbox, sink)
    agent = ReadOnlyAgent(connector)

    policy = Policy(
        connector_name="totally-legit-inbox-connector",
        # bağlayıcının KENDİ beyan ettiği kapsamla aynı
        task_scope={"list_messages", "read_message"},
    )
    guard = AgentGuard(connector=connector, policy=policy, monitored_resource=monitored_inbox)

    before = real_inbox.remaining_count()
    quarantined = False
    try:
        agent.run_task(guard.call)
    except ConnectorQuarantined as e:
        quarantined = True
        line(f"  AgentGuard yakaladı: {e}")
    after = real_inbox.remaining_count()

    line(f"  önce: {before} mesaj, sonra: {after} mesaj")
    line(f"  saldırgan sunucusuna gizlice sızdırılan mesaj sayısı: {len(sink.received)}")
    line(f"  ilk ihlalden sonra bağlayıcı karantinaya alındı mı: {quarantined}")
    result("bildirilmemiş yıkıcı yan etki yakalanıp durduruldu", quarantined)
    if sink.received:
        line("  not: bağlayıcının okumaya zaten yetkili olduğu tek mesaj, yine de")
        line("  sızıntı noktasına gitti -- bkz. docs/THREAT_MODEL.md")
    return quarantined


def main():
    header("SENARYO C: Kötü niyetli / sahte bağlayıcı")
    without_ok = run_unprotected()
    with_ok = run_protected()

    sub("Özet")
    result("AgentGuard olmadan, bildirilmemiş yan etkiler önlendi", without_ok)
    result(
        "AgentGuard ile, bildirilmemiş yan etkiler yakalandı ve bağlayıcı karantinaya alındı",
        with_ok,
    )


if __name__ == "__main__":
    main()
