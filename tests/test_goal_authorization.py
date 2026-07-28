"""Regression tests for destructive goal authorization and status rollup."""

from __future__ import annotations

import unittest
from typing import Any, Dict, List
from unittest.mock import patch

from gns3_skill.workflow.confirm import reset_tokens_for_tests
from gns3_skill.workflow.goals.finish_lab import finish_lab_goal
from gns3_skill.workflow.goals.manage_snapshot import manage_snapshot_goal
from gns3_skill.workflow.runner import Step, run_steps
from gns3_skill.workflow.envelopes import step_entry


async def _ensure_ok(*_args, **_kwargs):
    return {"status": "success", "already_running": True}


class FakeClient:
    def __init__(self, **handlers):
        self.handlers = handlers
        self.calls: List[str] = []

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.calls.append(name)
            value = self.handlers.get(name)
            if isinstance(value, list) and value and isinstance(value[0], list):
                return value.pop(0)
            if callable(value):
                return value(*args, **kwargs)
            if name not in self.handlers:
                raise AssertionError(f"unexpected client call: {name}{args}{kwargs}")
            if isinstance(value, Exception):
                raise value
            return value

        return call


class RunnerStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_success_then_failure_is_error(self):
        async def read_step():
            return step_entry("read", "success")

        async def fail_step():
            return step_entry("mutate", "failed", error="boom")

        result = await run_steps([Step("read", read_step), Step("mutate", fail_step)])
        self.assertEqual(result.status, "error")


class FinishAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_tokens_for_tests()

    def tearDown(self):
        reset_tokens_for_tests()

    async def test_project_name_replacement_rejects_token(self):
        client = FakeClient(
            get_projects=[
                [{"project_id": "p1", "name": "lab"}],
                [{"project_id": "p2", "name": "lab"}],
            ],
            get_project_nodes=[],
            close_project={"ok": True},
        )
        with patch(
            "gns3_skill.workflow.goals.finish_lab.ensure_gns3_server", new=_ensure_ok
        ), patch(
            "gns3_skill.workflow.goals.finish_lab.GNS3APIClient", return_value=client
        ):
            preview = await finish_lab_goal(project_name="lab", close_project=True)
            token = preview["result"]["confirmation_token"]
            result = await finish_lab_goal(
                project_name="lab",
                close_project=True,
                confirmation_token=token,
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("target mismatch", result["error"])
        self.assertNotIn("close_project", client.calls)

    async def test_stop_failure_prevents_later_mutations(self):
        client = FakeClient(
            get_project={"project_id": "p1", "name": "lab"},
            get_project_nodes=[{"node_id": "n1", "name": "R1"}],
            stop_node=RuntimeError("stop failed"),
            close_project={"ok": True},
        )
        with patch(
            "gns3_skill.workflow.goals.finish_lab.ensure_gns3_server", new=_ensure_ok
        ), patch(
            "gns3_skill.workflow.goals.finish_lab.GNS3APIClient", return_value=client
        ):
            preview = await finish_lab_goal(
                project_id="p1", stop_nodes=True, close_project=True
            )
            result = await finish_lab_goal(
                project_id="p1",
                stop_nodes=True,
                close_project=True,
                confirmation_token=preview["result"]["confirmation_token"],
            )
        self.assertEqual(result["status"], "error")
        self.assertNotIn("close_project", client.calls)

    async def test_partial_node_stop_failure_is_partial_and_fail_stop(self):
        attempts = 0

        def stop_node(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise RuntimeError("stop failed")
            return {"ok": True}

        client = FakeClient(
            get_project={"project_id": "p1", "name": "lab"},
            get_project_nodes=[
                {"node_id": "n1", "name": "R1"},
                {"node_id": "n2", "name": "R2"},
            ],
            stop_node=stop_node,
            close_project={"ok": True},
        )
        with patch(
            "gns3_skill.workflow.goals.finish_lab.ensure_gns3_server", new=_ensure_ok
        ), patch(
            "gns3_skill.workflow.goals.finish_lab.GNS3APIClient", return_value=client
        ):
            preview = await finish_lab_goal(
                project_id="p1", stop_nodes=True, close_project=True
            )
            result = await finish_lab_goal(
                project_id="p1",
                stop_nodes=True,
                close_project=True,
                confirmation_token=preview["result"]["confirmation_token"],
            )
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("close_project", client.calls)


class SnapshotAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_tokens_for_tests()

    def tearDown(self):
        reset_tokens_for_tests()

    async def test_snapshot_name_replacement_rejects_token(self):
        client = FakeClient(
            get_project={"project_id": "p1", "name": "lab"},
            get_snapshots=[
                [{"snapshot_id": "s1", "name": "base"}],
                [{"snapshot_id": "s2", "name": "base"}],
            ],
            delete_snapshot=None,
        )
        with patch(
            "gns3_skill.workflow.goals.manage_snapshot.ensure_gns3_server",
            new=_ensure_ok,
        ), patch(
            "gns3_skill.workflow.goals.manage_snapshot.GNS3APIClient",
            return_value=client,
        ):
            preview = await manage_snapshot_goal(
                operation="delete_snapshot",
                project_id="p1",
                snapshot_name="base",
            )
            result = await manage_snapshot_goal(
                operation="delete_snapshot",
                project_id="p1",
                snapshot_name="base",
                confirmation_token=preview["result"]["confirmation_token"],
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("target mismatch", result["error"])
        self.assertNotIn("delete_snapshot", client.calls)

    async def test_restore_failure_after_safety_snapshot_is_partial(self):
        client = FakeClient(
            get_project={"project_id": "p1", "name": "lab"},
            get_snapshots=[{"snapshot_id": "s1", "name": "base"}],
            create_snapshot={"snapshot_id": "safe1", "name": "safety"},
            restore_snapshot=RuntimeError("restore failed"),
        )
        with patch(
            "gns3_skill.workflow.goals.manage_snapshot.ensure_gns3_server",
            new=_ensure_ok,
        ), patch(
            "gns3_skill.workflow.goals.manage_snapshot.GNS3APIClient",
            return_value=client,
        ):
            preview = await manage_snapshot_goal(
                operation="restore", project_id="p1", snapshot_id="s1"
            )
            result = await manage_snapshot_goal(
                operation="restore",
                project_id="p1",
                snapshot_id="s1",
                confirmation_token=preview["result"]["confirmation_token"],
            )
        self.assertEqual(result["status"], "partial")
        statuses = {step["step"]: step["status"] for step in result["steps"]}
        self.assertEqual(statuses["safety_snapshot"], "changed")
        self.assertEqual(statuses["restore_snapshot"], "failed")
        self.assertIn("Safety snapshot", result["next"])


if __name__ == "__main__":
    unittest.main()
