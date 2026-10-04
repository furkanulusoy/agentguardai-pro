"""
Automated tests for the three scenarios.

These don't need any external dependency (stdlib unittest only, on purpose
-- this project stays dependency-free). Each scenario module's
run_unprotected() / run_protected() functions already return a bool
("did the bad thing NOT happen"), so the tests just assert on those:

- run_unprotected() is expected to return False -- i.e. the vulnerability
  reproduces reliably. If this ever starts passing, the mock services
  changed and the "before" story is no longer demonstrating the risk.
- run_protected() is expected to return True -- AgentGuard caught it.

Run with:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import contextlib
import io
import unittest

from demo import (
    scenario_a_permissions,
    scenario_b_autonomous_action,
    scenario_c_malicious_connector,
    scenario_d_prompt_injection,
)


def _quiet(fn, *args, **kwargs):
    """Run fn with stdout swallowed -- the scenario scripts narrate a lot."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


class TestScenarioAPermissions(unittest.TestCase):
    def test_unprotected_inbox_gets_wiped(self):
        self.assertFalse(_quiet(scenario_a_permissions.run_unprotected))

    def test_protected_inbox_survives(self):
        self.assertTrue(_quiet(scenario_a_permissions.run_protected))


class TestScenarioBAutonomousAction(unittest.TestCase):
    def test_unprotected_merges_failing_pr(self):
        self.assertFalse(_quiet(scenario_b_autonomous_action.run_unprotected))

    def test_protected_blocks_failing_pr_merge(self):
        self.assertTrue(_quiet(scenario_b_autonomous_action.run_protected))


class TestScenarioCMaliciousConnector(unittest.TestCase):
    def test_unprotected_connector_causes_undeclared_side_effects(self):
        self.assertFalse(_quiet(scenario_c_malicious_connector.run_unprotected))

    def test_protected_quarantines_connector(self):
        self.assertTrue(_quiet(scenario_c_malicious_connector.run_protected))


class TestScenarioDPromptInjection(unittest.TestCase):
    def test_unprotected_agent_exfiltrates_and_covers_tracks(self):
        self.assertFalse(_quiet(scenario_d_prompt_injection.run_unprotected))

    def test_protected_agent_blocks_exfiltration(self):
        self.assertTrue(_quiet(scenario_d_prompt_injection.run_protected))


if __name__ == "__main__":
    unittest.main()
