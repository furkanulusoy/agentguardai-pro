"""Tests for Policy.from_dict() / Policy.from_file() (JSON + YAML)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentguard.policy import Policy, PolicyConfigError

try:
    import yaml  # noqa: F401

    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


class TestPolicyFromDict(unittest.TestCase):
    def test_valid_config(self):
        policy = Policy.from_dict(
            {
                "connector_name": "inbox",
                "task_scope": ["list_messages", "read_message"],
                "sensitive_actions": ["read_message"],
            }
        )
        self.assertEqual(policy.connector_name, "inbox")
        self.assertEqual(policy.task_scope, {"list_messages", "read_message"})
        self.assertEqual(policy.sensitive_actions, {"read_message"})

    def test_sensitive_actions_defaults_to_empty(self):
        policy = Policy.from_dict({"connector_name": "x", "task_scope": ["a"]})
        self.assertEqual(policy.sensitive_actions, set())

    def test_missing_connector_name_raises(self):
        with self.assertRaises(PolicyConfigError):
            Policy.from_dict({"task_scope": ["a"]})

    def test_missing_task_scope_raises(self):
        with self.assertRaises(PolicyConfigError):
            Policy.from_dict({"connector_name": "x"})

    def test_task_scope_must_be_list_of_strings(self):
        with self.assertRaises(PolicyConfigError):
            Policy.from_dict({"connector_name": "x", "task_scope": "not-a-list"})
        with self.assertRaises(PolicyConfigError):
            Policy.from_dict({"connector_name": "x", "task_scope": [1, 2]})

    def test_sensitive_action_outside_scope_raises(self):
        # An action that's never permitted doesn't need an approval gate --
        # this is almost certainly a config mistake, so it's rejected loudly
        # instead of silently accepted.
        with self.assertRaises(PolicyConfigError):
            Policy.from_dict(
                {
                    "connector_name": "x",
                    "task_scope": ["list_messages"],
                    "sensitive_actions": ["delete_all"],
                }
            )


class TestPolicyFromFile(unittest.TestCase):
    def test_from_json_file(self):
        data = {
            "connector_name": "inbox",
            "task_scope": ["list_messages", "read_message", "send_message"],
            "sensitive_actions": ["send_message"],
        }
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "policy.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            policy = Policy.from_file(path)
        self.assertEqual(policy.connector_name, "inbox")
        self.assertEqual(policy.sensitive_actions, {"send_message"})

    def test_example_policy_files_are_valid(self):
        # The example files shipped in examples/policies/ must actually load --
        # if they don't, the README's documented usage is a lie.
        repo_root = Path(__file__).resolve().parent.parent
        json_policy = Policy.from_file(repo_root / "examples" / "policies" / "inbox.json")
        self.assertEqual(json_policy.connector_name, "inbox")
        if YAML_AVAILABLE:
            yaml_policy = Policy.from_file(repo_root / "examples" / "policies" / "inbox.yaml")
            self.assertEqual(yaml_policy.connector_name, "inbox")
            self.assertEqual(yaml_policy.task_scope, json_policy.task_scope)
            self.assertEqual(yaml_policy.sensitive_actions, json_policy.sensitive_actions)

    def test_unrecognized_extension_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "policy.toml"
            path.write_text("connector_name = 'x'", encoding="utf-8")
            with self.assertRaises(PolicyConfigError):
                Policy.from_file(path)

    def test_non_mapping_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "policy.json"
            path.write_text(json.dumps(["not", "a", "mapping"]), encoding="utf-8")
            with self.assertRaises(PolicyConfigError):
                Policy.from_file(path)

    @unittest.skipUnless(YAML_AVAILABLE, "pyyaml not installed (pip install agentguard[yaml])")
    def test_from_yaml_file(self):
        yaml_text = (
            "connector_name: repo\n"
            "task_scope:\n"
            "  - list_pull_requests\n"
            "  - merge_pull_request\n"
            "sensitive_actions:\n"
            "  - merge_pull_request\n"
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "policy.yaml"
            path.write_text(yaml_text, encoding="utf-8")
            policy = Policy.from_file(path)
        self.assertEqual(policy.connector_name, "repo")
        self.assertEqual(policy.sensitive_actions, {"merge_pull_request"})


if __name__ == "__main__":
    unittest.main()
