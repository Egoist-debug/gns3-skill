"""Unit tests for workflow runner, confirm tokens, resolve, envelopes."""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

from gns3_skill.workflow.confirm import (
    consume_token,
    issue_token,
    reset_tokens_for_tests,
    target_hash,
)
from gns3_skill.workflow.envelopes import (
    STATUS_CONFIRMATION_REQUIRED,
    STATUS_CONFLICT,
    STATUS_ERROR,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    confirmation_required_envelope,
    conflict_envelope,
    error_envelope,
    goal_envelope,
    step_entry,
)
from gns3_skill.workflow.resolve import (
    ResolveConflict,
    ResolveMissing,
    check_node_template_conflict,
    find_link_by_endpoints,
    image_filename,
    link_endpoint_key,
    unordered_link_key,
)
from gns3_skill.workflow.runner import Step, run_steps


class EnvelopeTests(unittest.TestCase):
    def test_goal_envelope_shape(self):
        env = goal_envelope(
            "prepare_lab",
            STATUS_SUCCESS,
            [step_entry("ensure_server", "success")],
            result={"project_id": "p1"},
        )
        self.assertEqual(env["status"], STATUS_SUCCESS)
        self.assertEqual(env["goal"], "prepare_lab")
        self.assertEqual(len(env["steps"]), 1)
        self.assertEqual(env["result"]["project_id"], "p1")
        self.assertIsNone(env["error"])

    def test_error_and_conflict_and_confirm(self):
        err = error_envelope("x", "boom")
        self.assertEqual(err["status"], STATUS_ERROR)
        conf = conflict_envelope(
            "x",
            [],
            existing={"name": "a"},
            expected={"name": "a", "template_id": "t2"},
            message="conflict",
        )
        self.assertEqual(conf["status"], STATUS_CONFLICT)
        tok = confirmation_required_envelope(
            "finish_lab",
            [],
            action="finish_lab",
            target={"stop_server": True},
            impact={"stop_server": True},
            confirmation_token="abc",
            expires_at=1.0,
        )
        self.assertEqual(tok["status"], STATUS_CONFIRMATION_REQUIRED)
        self.assertEqual(tok["result"]["confirmation_token"], "abc")


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_success(self):
        async def s1():
            return step_entry("a", "success")

        async def s2():
            return step_entry("b", "changed")

        result = await run_steps([Step("a", s1), Step("b", s2)])
        self.assertEqual(result.status, STATUS_SUCCESS)
        self.assertEqual([s["step"] for s in result.steps], ["a", "b"])

    async def test_fail_stop_partial(self):
        async def s1():
            return step_entry("a", "changed")

        async def s2():
            return step_entry("b", "failed", error="nope")

        async def s3():
            return step_entry("c", "success")

        result = await run_steps(
            [Step("a", s1), Step("b", s2), Step("c", s3)]
        )
        self.assertEqual(result.status, STATUS_PARTIAL)
        self.assertEqual(result.stopped_at, "b")
        self.assertEqual(len(result.steps), 2)
        self.assertNotIn("c", [s["step"] for s in result.steps])

    async def test_fail_first_is_error(self):
        async def s1():
            raise RuntimeError("down")

        result = await run_steps([Step("a", s1)])
        self.assertEqual(result.status, STATUS_ERROR)
        self.assertIn("down", result.error or "")


class ConfirmTests(unittest.TestCase):
    def setUp(self):
        # Isolate the on-disk token store per test so file-backed persistence
        # tests don't share ~/.cache state across tests or runs.
        self._tmp = tempfile.NamedTemporaryFile(
            prefix="gns3_confirm_test_", suffix=".json", delete=False
        )
        self._tmp.close()
        os.chmod(self._tmp.name, 0o600)
        self._prev_store = os.environ.get("GNS3_CONFIRM_TOKEN_STORE")
        os.environ["GNS3_CONFIRM_TOKEN_STORE"] = self._tmp.name
        reset_tokens_for_tests()

    def tearDown(self):
        reset_tokens_for_tests()
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass
        if self._prev_store is None:
            os.environ.pop("GNS3_CONFIRM_TOKEN_STORE", None)
        else:
            os.environ["GNS3_CONFIRM_TOKEN_STORE"] = self._prev_store
    def test_issue_consume_once(self):
        target = {"project_id": "p1", "op": "restore"}
        token, exp = issue_token("restore", target, ttl_seconds=60)
        self.assertTrue(exp > 0)
        ok = consume_token(token, "restore", target)
        self.assertTrue(ok["ok"])
        again = consume_token(token, "restore", target)
        self.assertFalse(again["ok"])

    def test_action_mismatch(self):
        target = {"project_id": "p1"}
        token, _ = issue_token("restore", target, ttl_seconds=60)
        bad = consume_token(token, "delete_project", target)
        self.assertFalse(bad["ok"])
        self.assertIn("action", bad["error"])

    def test_target_mismatch(self):
        token, _ = issue_token("restore", {"project_id": "p1"}, ttl_seconds=60)
        bad = consume_token(token, "restore", {"project_id": "p2"})
        self.assertFalse(bad["ok"])
        self.assertIn("target", bad["error"])

    def test_expired(self):
        target = {"x": 1}
        token, _ = issue_token("restore", target, ttl_seconds=0.001)
        import time

        time.sleep(0.02)
        bad = consume_token(token, "restore", target)
        self.assertFalse(bad["ok"])

    def test_target_hash_stable(self):
        self.assertEqual(
            target_hash({"b": 2, "a": 1}),
            target_hash({"a": 1, "b": 2}),
        )

    def test_token_survives_across_process(self):
        """Regression: a token issued in one CLI process must be consumable in
        a later one — the documented cross-call confirm flow depends on it.
        Simulates two CLI invocations by spawning a fresh interpreter."""
        import json
        import subprocess
        import sys

        target = {"project_id": "p1", "op": "restore"}
        token, _ = issue_token("restore", target, ttl_seconds=60)

        # New Python process: consume the token (different in-memory state).
        consume_code = (
            "import json, sys, os;\n"
            "from gns3_skill.workflow.confirm import consume_token;\n"
            "r = consume_token(sys.argv[1], 'restore', "
            "{'project_id': 'p1', 'op': 'restore'});\n"
            "sys.stdout.write(json.dumps(r));\n"
        )
        env = dict(os.environ)
        env["GNS3_CONFIRM_TOKEN_STORE"] = self._tmp.name
        env["PYTHONPATH"] = os.pathsep.join([p for p in sys.path if p])
        out = subprocess.check_output(
            [sys.executable, "-c", consume_code, token],
            env=env,
        )
        self.assertEqual(json.loads(out), {"ok": True})

        # The same token is now used — a third process must reject it.
        out2 = subprocess.check_output(
            [sys.executable, "-c", consume_code, token],
            env=env,
        )
        self.assertFalse(json.loads(out2)["ok"])
        self.assertIn("already used", json.loads(out2)["error"])


class ResolvePureTests(unittest.IsolatedAsyncioTestCase):
    def test_link_key_unordered(self):
        a = link_endpoint_key("R1", 0, 0)
        b = link_endpoint_key("R2", 0, 1)
        self.assertEqual(unordered_link_key(a, b), unordered_link_key(b, a))

    def test_node_template_conflict(self):
        existing = {"name": "R1", "template_id": "t1", "node_id": "n1"}
        with self.assertRaises(ResolveConflict):
            check_node_template_conflict(existing, expected_template_id="t2")
        check_node_template_conflict(existing, expected_template_id="t1")

    def test_image_filename(self):
        self.assertEqual(image_filename({"filename": "ios.bin"}), "ios.bin")
        self.assertEqual(image_filename({"path": "/x/ios.bin"}), "/x/ios.bin")

    async def test_find_link_by_endpoints(self):
        client = MagicMock()
        client.get_project_nodes = AsyncMock(
            return_value=[
                {"node_id": "n1", "name": "R1"},
                {"node_id": "n2", "name": "R2"},
            ]
        )
        client.get_project_links = AsyncMock(
            return_value=[
                {
                    "link_id": "l1",
                    "nodes": [
                        {"node_id": "n1", "adapter_number": 0, "port_number": 0},
                        {"node_id": "n2", "adapter_number": 0, "port_number": 1},
                    ],
                }
            ]
        )
        found = await find_link_by_endpoints(
            client,
            "p1",
            link_endpoint_key("R1", 0, 0),
            link_endpoint_key("R2", 0, 1),
        )
        self.assertIsNotNone(found)
        self.assertEqual(found["link_id"], "l1")
        missing = await find_link_by_endpoints(
            client,
            "p1",
            link_endpoint_key("R1", 0, 0),
            link_endpoint_key("R2", 0, 2),
        )
        self.assertIsNone(missing)


if __name__ == "__main__":
    unittest.main()
