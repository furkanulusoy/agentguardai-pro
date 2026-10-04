"""
agentguard command-line interface.

Requires the `cli` extra: pip install agentguard[cli]
"""
from __future__ import annotations

import sys

try:
    import click
except ImportError:  # pragma: no cover - exercised only when click isn't installed
    click = None  # type: ignore[assignment]


def main() -> None:
    if click is None:
        print(
            "The agentguard CLI requires click. Install it with:\n"
            "  pip install agentguard[cli]",
            file=sys.stderr,
        )
        sys.exit(1)
    cli()


if click is not None:

    @click.group()
    @click.version_option(package_name="agentguard")
    def cli() -> None:
        """AgentGuard: a guardrail layer for agents connected to real accounts."""

    @cli.command("audit")
    @click.argument("path", type=click.Path(exists=True))
    @click.option(
        "--kind", default=None, help="Only show events of this kind (e.g. anomaly_detected)."
    )
    def audit_cmd(path: str, kind: str | None) -> None:
        """Pretty-print a JSON Lines audit log written by JsonlAuditLog."""
        from agentguard.audit_log import JsonlAuditLog

        entries = JsonlAuditLog.read_all(path)
        if kind:
            entries = [e for e in entries if e["kind"] == kind]
        if not entries:
            click.echo("Kayıt yok.")
            return
        for e in entries:
            detail = f"  -- {e['detail']}" if e.get("detail") else ""
            click.echo(
                f"{e['timestamp']:.3f}  {e['kind']:<20} {e['connector']}.{e['action']}{detail}"
            )

    @cli.command("policy-check")
    @click.argument("path", type=click.Path(exists=True))
    def policy_check_cmd(path: str) -> None:
        """Validate a policy .json/.yaml/.yml file without running anything."""
        from agentguard.policy import Policy, PolicyConfigError

        try:
            policy = Policy.from_file(path)
        except PolicyConfigError as e:
            click.echo(f"GEÇERSİZ policy dosyası: {e}", err=True)
            sys.exit(1)
        click.echo(f"OK -- connector={policy.connector_name!r}")
        click.echo(f"  task_scope: {sorted(policy.task_scope)}")
        click.echo(f"  sensitive_actions: {sorted(policy.sensitive_actions)}")

    @cli.group("quarantine")
    def quarantine_group() -> None:
        """Inspect and manage persisted connector quarantine state (SqliteQuarantineStore)."""

    @quarantine_group.command("list")
    @click.argument("db_path", type=click.Path())
    def quarantine_list_cmd(db_path: str) -> None:
        """List every currently-quarantined connector in a quarantine DB."""
        from agentguard.quarantine_store import SqliteQuarantineStore

        store = SqliteQuarantineStore(db_path)
        entries = store.list_quarantined()
        store.close()
        if not entries:
            click.echo("Karantinada bağlayıcı yok.")
            return
        for e in entries:
            click.echo(f"{e['connector_name']}  (t={e['quarantined_at']:.0f})  -- {e['reason']}")

    @quarantine_group.command("clear")
    @click.argument("db_path", type=click.Path())
    @click.argument("connector_name")
    def quarantine_clear_cmd(db_path: str, connector_name: str) -> None:
        """Clear a connector's persisted quarantine -- only do this after
        confirming and fixing the underlying issue; this does not
        re-run any check, it just lifts the block."""
        from agentguard.quarantine_store import SqliteQuarantineStore

        store = SqliteQuarantineStore(db_path)
        found = store.clear(connector_name)
        store.close()
        if found:
            click.echo(f"OK -- '{connector_name}' karantinadan çıkarıldı.")
        else:
            click.echo(f"'{connector_name}' zaten karantinada değildi.")

    @cli.command("demo")
    @click.option(
        "--scenario",
        type=click.Choice(["a", "b", "c", "d", "all"]),
        default="all",
        help="Which bundled scenario to run.",
    )
    def demo_cmd(scenario: str) -> None:
        """
        Run the bundled before/after demo scenarios.

        Only works from inside a checkout of the agentguard repo -- the
        mock services and demo scripts are dev-time material, not part
        of the installable `agentguard` library (see
        docs/ROADMAP_TO_PRODUCTION.md on why demo/fake data shouldn't
        ship inside a library other people depend on).
        """
        try:
            from demo import (
                scenario_a_permissions,
                scenario_b_autonomous_action,
                scenario_c_malicious_connector,
                scenario_d_prompt_injection,
            )
        except ImportError:
            click.echo(
                "Demo modülleri bulunamadı. Bu komut sadece agentguard reposunun "
                "içinden (klonlanmış haliyle) çalışır -- kurulu pip paketinin "
                "parçası değildir.",
                err=True,
            )
            sys.exit(1)

        mapping = {
            "a": scenario_a_permissions,
            "b": scenario_b_autonomous_action,
            "c": scenario_c_malicious_connector,
            "d": scenario_d_prompt_injection,
        }
        if scenario == "all":
            for mod in mapping.values():
                mod.main()
        else:
            mapping[scenario].main()


if __name__ == "__main__":
    main()
