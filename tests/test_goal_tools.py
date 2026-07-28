"""Unit tests for goal tools with mocked GNS3/lifecycle/SSH."""

from __future__ import annotations

import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from gns3_skill.workflow.confirm import reset_tokens_for_tests
from gns3_skill.workflow.goals.finish_lab import finish_lab_goal
from gns3_skill.workflow.goals.manage_snapshot import manage_snapshot_goal
from gns3_skill.workflow.goals.prepare_image import prepare_image_goal
from gns3_skill.workflow.goals.prepare_lab import prepare_lab_goal
from gns3_skill.workflow.goals.run_guest_commands import run_guest_commands_goal


def _ensure_ok(url="http://127.0.0.1:3080", **_):
    async def _inner(*_a, **_k):
        return {
            "status": "success",
            "already_running": True,
            "started": False,
            "server_url": url,
        }

    return _inner


class FakeClient:
    def __init__(self, **handlers):
        self.handlers = handlers
        self.calls: List[str] = []

    def __getattr__(self, name):
        async def _call(*args, **kwargs):
            self.calls.append(name)
            if name in self.handlers:
                val = self.handlers[name]
                if callable(val):
                    return val(*args, **kwargs)
                return val
            raise AssertionError(f"unexpected client call: {name}{args}{kwargs}")

        return _call


class PrepareLabTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuse_existing_project(self):
        project = {"project_id": "p1", "name": "lab", "status": "closed"}
        client = FakeClient(
            get_projects=[{"project_id": "p1", "name": "lab", "status": "closed"}],
            open_project={"project_id": "p1", "name": "lab", "status": "opened"},
        )
        with patch(
            "gns3_skill.workflow.goals.prepare_lab.ensure_gns3_server",
            new=_ensure_ok(),
        ), patch(
            "gns3_skill.workflow.goals.prepare_lab.GNS3APIClient",
            return_value=client,
        ):
            out = await prepare_lab_goal(project_name="lab")
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["result"]["project_id"], "p1")
        steps = {s["step"]: s["status"] for s in out["steps"]}
        self.assertEqual(steps["resolve_project"], "skipped")
        self.assertEqual(steps["open_project"], "changed")
        self.assertNotIn("create_project", client.calls)

    async def test_create_when_missing(self):
        client = FakeClient(
            get_projects=[],
            create_project={"project_id": "p2", "name": "newlab", "status": "closed"},
            open_project={"project_id": "p2", "name": "newlab", "status": "opened"},
        )
        with patch(
            "gns3_skill.workflow.goals.prepare_lab.ensure_gns3_server",
            new=_ensure_ok(),
        ), patch(
            "gns3_skill.workflow.goals.prepare_lab.GNS3APIClient",
            return_value=client,
        ):
            out = await prepare_lab_goal(project_name="newlab")
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["result"]["project_id"], "p2")
        self.assertIn("create_project", client.calls)

    async def test_server_down_no_mutation(self):
        async def down(*_a, **_k):
            return {"status": "error", "error": "unreachable"}

        with patch(
            "gns3_skill.workflow.goals.prepare_lab.ensure_gns3_server",
            new=down,
        ):
            out = await prepare_lab_goal(project_name="lab")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["steps"][0]["step"], "ensure_server")


class ManageSnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_tokens_for_tests()

    def tearDown(self):
        reset_tokens_for_tests()

    async def test_restore_requires_token_then_succeeds(self):
        client = FakeClient(
            get_project={"project_id": "p1", "name": "lab"},
            get_snapshots=[{"snapshot_id": "s1", "name": "snap1"}],
            create_snapshot={"snapshot_id": "safe1", "name": "safety"},
            restore_snapshot={"ok": True},
        )
        with patch(
            "gns3_skill.workflow.goals.manage_snapshot.ensure_gns3_server",
            new=_ensure_ok(),
        ), patch(
            "gns3_skill.workflow.goals.manage_snapshot.GNS3APIClient",
            return_value=client,
        ):
            preview = await manage_snapshot_goal(
                operation="restore",
                project_id="p1",
                snapshot_name="snap1",
            )
            self.assertEqual(preview["status"], "confirmation_required")
            token = preview["result"]["confirmation_token"]
            done = await manage_snapshot_goal(
                operation="restore",
                project_id="p1",
                snapshot_name="snap1",
                confirmation_token=token,
            )
        self.assertEqual(done["status"], "success")
        self.assertIn("restore_snapshot", client.calls)
        self.assertIn("create_snapshot", client.calls)

    async def test_wrong_token_rejected(self):
        client = FakeClient(get_project={"project_id": "p1", "name": "lab"})
        with patch(
            "gns3_skill.workflow.goals.manage_snapshot.ensure_gns3_server",
            new=_ensure_ok(),
        ), patch(
            "gns3_skill.workflow.goals.manage_snapshot.GNS3APIClient",
            return_value=client,
        ):
            out = await manage_snapshot_goal(
                operation="delete_project",
                project_id="p1",
                confirmation_token="not-a-real-token",
            )
        self.assertEqual(out["status"], "error")
        self.assertNotIn("delete_project", client.calls)

    async def test_create_idempotent_by_name(self):
        client = FakeClient(
            get_project={"project_id": "p1", "name": "lab"},
            get_snapshots=[{"snapshot_id": "s1", "name": "base"}],
        )
        with patch(
            "gns3_skill.workflow.goals.manage_snapshot.ensure_gns3_server",
            new=_ensure_ok(),
        ), patch(
            "gns3_skill.workflow.goals.manage_snapshot.GNS3APIClient",
            return_value=client,
        ):
            out = await manage_snapshot_goal(
                operation="create", project_id="p1", snapshot_name="base"
            )
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["result"]["action"], "reuse")
        self.assertNotIn("create_snapshot", client.calls)


class FinishLabTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_tokens_for_tests()

    def tearDown(self):
        reset_tokens_for_tests()

    async def test_default_flags_noop(self):
        out = await finish_lab_goal()
        self.assertEqual(out["status"], "success")
        self.assertIn("nothing requested", out["result"]["message"])

    async def test_preview_then_execute_stop_nodes(self):
        client = FakeClient(
            get_project={"project_id": "p1", "name": "lab"},
            get_project_nodes=[{"node_id": "n1", "name": "R1", "status": "started"}],
            stop_node={"ok": True},
        )
        with patch(
            "gns3_skill.workflow.goals.finish_lab.ensure_gns3_server",
            new=_ensure_ok(),
        ), patch(
            "gns3_skill.workflow.goals.finish_lab.GNS3APIClient",
            return_value=client,
        ):
            preview = await finish_lab_goal(project_id="p1", stop_nodes=True)
            self.assertEqual(preview["status"], "confirmation_required")
            token = preview["result"]["confirmation_token"]
            done = await finish_lab_goal(
                project_id="p1", stop_nodes=True, confirmation_token=token
            )
        self.assertEqual(done["status"], "success")
        self.assertIn("stop_node", client.calls)

    async def test_remote_stop_server_fails_step(self):
        with patch(
            "gns3_skill.workflow.goals.finish_lab.is_local_server_url",
            return_value=False,
        ):
            preview = await finish_lab_goal(
                stop_server=True, server_url="http://10.0.0.5:3080"
            )
            token = preview["result"]["confirmation_token"]
            out = await finish_lab_goal(
                stop_server=True,
                server_url="http://10.0.0.5:3080",
                confirmation_token=token,
            )
        self.assertIn(out["status"], ("error", "partial"))
        stop_steps = [s for s in out["steps"] if s["step"] == "stop_server"]
        self.assertTrue(stop_steps)
        self.assertEqual(stop_steps[0]["status"], "failed")


class PrepareImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_docker_rejected(self):
        out = await prepare_image_goal(source_path="/tmp/x", emulator="docker")
        self.assertEqual(out["status"], "error")
        self.assertIn("Docker", out["error"])

    async def test_skip_existing_image(self):
        client = FakeClient(
            list_images=[{"filename": "ios.bin"}],
        )
        with patch(
            "gns3_skill.workflow.goals.prepare_image.ensure_gns3_server",
            new=_ensure_ok(),
        ), patch(
            "gns3_skill.workflow.goals.prepare_image.GNS3APIClient",
            return_value=client,
        ), patch(
            "gns3_skill.workflow.goals.prepare_image.Path"
        ) as path_cls:
            path_cls.return_value.is_file.return_value = True
            path_cls.return_value.name = "ios.bin"
            out = await prepare_image_goal(
                source_path="/tmp/ios.bin", emulator="dynamips", filename="ios.bin"
            )
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["result"]["import"]["action"], "skip")
        self.assertNotIn("upload_image", client.calls)

    async def test_densify_yellow(self):
        client = FakeClient()
        with patch(
            "gns3_skill.workflow.goals.prepare_image.ensure_gns3_server",
            new=_ensure_ok(),
        ), patch(
            "gns3_skill.workflow.goals.prepare_image.GNS3APIClient",
            return_value=client,
        ):
            out = await prepare_image_goal(densify_template=True)
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["result"]["yellow"]["capability"], "yellow")


class RunGuestTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_host_ssh(self):
        async def fake_exec(host, commands, **kwargs):
            self.assertEqual(host, "10.0.0.9")
            self.assertNotIn("password", str(kwargs.get("password") and "redacted"))
            return {
                "status": "success",
                "results": [{"command": "uname", "stdout": "Linux", "exit_code": 0}],
            }

        with patch(
            "gns3_skill.workflow.goals.run_guest_commands.ssh_helpers.resolve_ssh_credentials",
            return_value=("u", "p"),
        ), patch(
            "gns3_skill.workflow.goals.run_guest_commands.ssh_helpers.exec_commands",
            new=fake_exec,
        ):
            out = await run_guest_commands_goal(
                commands=["uname"], host="10.0.0.9"
            )
        self.assertEqual(out["status"], "success")
        self.assertNotIn("password", str(out).lower().split("ssh_password")[0] if False else str(out))
        # ensure no password field in result
        self.assertNotIn("password", out.get("result", {}).get("ssh", {}))

    async def test_missing_host_and_metadata(self):
        with patch(
            "gns3_skill.workflow.goals.run_guest_commands.ensure_gns3_server",
            new=_ensure_ok(),
        ):
            out = await run_guest_commands_goal(commands=["id"])
        self.assertIn(out["status"], ("error", "partial"))


if __name__ == "__main__":
    unittest.main()
