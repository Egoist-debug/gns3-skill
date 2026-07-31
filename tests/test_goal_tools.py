"""Unit tests for goal tools with mocked GNS3/lifecycle/SSH."""

from __future__ import annotations

import unittest
from typing import List
from unittest.mock import patch

from gns3_skill.operations.device_io import apply_config_template, bulk_configure_nodes
from gns3_skill.operations.nodes import start_all_nodes
from gns3_skill.workflow.confirm import reset_tokens_for_tests
from gns3_skill.workflow.goals.finish_lab import finish_lab_goal
from gns3_skill.workflow.goals.manage_snapshot import manage_snapshot_goal
from gns3_skill.workflow.goals.prepare_image import prepare_image_goal
from gns3_skill.workflow.goals.prepare_lab import prepare_lab_goal
from gns3_skill.workflow.goals.run_guest_commands import run_guest_commands_goal




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

class FakeContext:
    def __init__(self, client=None, ensure_result=None):
        self.server_url = "http://127.0.0.1:3080"
        self._client = client
        self._ensure_result = ensure_result or {
            "status": "success",
            "already_running": True,
            "started": False,
            "server_url": self.server_url,
        }

    async def ensure(self, *, force=False):
        return self._ensure_result

    async def client(self, *, force_ensure=False):
        if self._client is None:
            raise AssertionError("goal unexpectedly requested a client")
        return self._client


class PrepareLabTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuse_existing_project(self):
        client = FakeClient(
            get_projects=[{"project_id": "p1", "name": "lab", "status": "closed"}],
            open_project={"project_id": "p1", "name": "lab", "status": "opened"},
        )
        out = await prepare_lab_goal(
            context=FakeContext(client), project_name="lab"
        )
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
        out = await prepare_lab_goal(
            context=FakeContext(client), project_name="newlab"
        )
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["result"]["project_id"], "p2")
        self.assertIn("create_project", client.calls)

    async def test_server_down_no_mutation(self):

        out = await prepare_lab_goal(
            context=FakeContext(ensure_result={"status": "error", "error": "unreachable"}),
            project_name="lab",
        )
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["steps"][0]["step"], "ensure_server")


class ManageSnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_tokens_for_tests()

    def tearDown(self):
        reset_tokens_for_tests()
    async def test_invalid_operation_returns_goal_error(self):
        result = await manage_snapshot_goal(
            context=FakeContext(), operation="delete"
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("operation must be", result["error"])


    async def test_restore_requires_token_then_succeeds(self):
        client = FakeClient(
            get_project={"project_id": "p1", "name": "lab", "status": "opened"},
            get_snapshots=[{"snapshot_id": "s1", "name": "snap1"}],
            get_project_nodes=[],
            create_snapshot={"snapshot_id": "safe1", "name": "safety"},
            restore_snapshot={"ok": True},
        )
        context = FakeContext(client)
        preview = await manage_snapshot_goal(
            context=context,
            operation="restore",
            project_id="p1",
            snapshot_name="snap1",
        )
        self.assertEqual(preview["status"], "confirmation_required")
        token = preview["result"]["confirmation_token"]
        done = await manage_snapshot_goal(
            context=context,
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
        out = await manage_snapshot_goal(
            context=FakeContext(client),
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
        out = await manage_snapshot_goal(
            context=FakeContext(client),
            operation="create",
            project_id="p1",
            snapshot_name="base",
        )
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["result"]["action"], "reuse")
        self.assertNotIn("create_snapshot", client.calls)
    async def test_restore_prepares_project_avoids_collision_and_reopens(self):
        project_status = "closed"
        nodes = [{"node_id": "n1", "name": "R1", "status": "started"}]
        snapshots = [
            {"snapshot_id": "s1", "name": "base"},
            {
                "snapshot_id": "safe1",
                "name": "safety-before-restore-base",
            },
        ]

        def get_project(_project_id):
            return {"project_id": "p1", "name": "lab", "status": project_status}

        def open_project(_project_id):
            nonlocal project_status
            project_status = "opened"
            return {"project_id": "p1", "name": "lab", "status": project_status}

        def stop_node(_project_id, node_id):
            next(node for node in nodes if node["node_id"] == node_id)["status"] = "stopped"
            return {"ok": True}

        def create_snapshot(_project_id, name):
            self.assertEqual(project_status, "opened")
            self.assertTrue(all(node["status"] == "stopped" for node in nodes))
            self.assertEqual(name, "safety-before-restore-base-2")
            snapshot = {"snapshot_id": "safe2", "name": name}
            snapshots.append(snapshot)
            return snapshot

        def restore_snapshot(_project_id, _snapshot_id):
            nonlocal project_status
            project_status = "closed"
            return {"ok": True}

        client = FakeClient(
            get_project=get_project,
            get_snapshots=snapshots,
            get_project_nodes=nodes,
            open_project=open_project,
            stop_node=stop_node,
            create_snapshot=create_snapshot,
            restore_snapshot=restore_snapshot,
        )
        context = FakeContext(client)
        preview = await manage_snapshot_goal(
            context=context,
            operation="restore",
            project_id="p1",
            snapshot_name="base",
        )
        result = await manage_snapshot_goal(
            context=context,
            operation="restore",
            project_id="p1",
            snapshot_name="base",
            confirmation_token=preview["result"]["confirmation_token"],
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(project_status, "opened")
        self.assertEqual(client.calls.count("open_project"), 2)
        self.assertLess(client.calls.index("open_project"), client.calls.index("stop_node"))
        self.assertLess(client.calls.index("stop_node"), client.calls.index("create_snapshot"))
        self.assertEqual(
            result["result"]["safety_snapshot"]["name"],
            "safety-before-restore-base-2",
        )

    async def test_failed_restore_recovers_open_project_state(self):
        project_status = "opened"

        def get_project(_project_id):
            return {"project_id": "p1", "name": "lab", "status": project_status}

        def open_project(_project_id):
            nonlocal project_status
            project_status = "opened"
            return {"project_id": "p1", "status": project_status}

        def restore_snapshot(_project_id, _snapshot_id):
            nonlocal project_status
            project_status = "closed"
            raise RuntimeError("restore failed")

        client = FakeClient(
            get_project=get_project,
            get_snapshots=[{"snapshot_id": "s1", "name": "base"}],
            get_project_nodes=[],
            open_project=open_project,
            create_snapshot={"snapshot_id": "safe1", "name": "safety"},
            restore_snapshot=restore_snapshot,
        )
        context = FakeContext(client)
        preview = await manage_snapshot_goal(
            context=context,
            operation="restore",
            project_id="p1",
            snapshot_name="base",
        )
        result = await manage_snapshot_goal(
            context=context,
            operation="restore",
            project_id="p1",
            snapshot_name="base",
            confirmation_token=preview["result"]["confirmation_token"],
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(project_status, "opened")
        self.assertIn("open_project", client.calls)



class FinishLabTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_tokens_for_tests()

    def tearDown(self):
        reset_tokens_for_tests()

    async def test_default_flags_noop(self):
        out = await finish_lab_goal(context=FakeContext())
        self.assertEqual(out["status"], "success")
        self.assertIn("nothing requested", out["result"]["message"])

    async def test_preview_then_execute_stop_nodes(self):
        client = FakeClient(
            get_project={"project_id": "p1", "name": "lab"},
            get_project_nodes=[{"node_id": "n1", "name": "R1", "status": "started"}],
            stop_node={"ok": True},
        )
        context = FakeContext(client)
        preview = await finish_lab_goal(
            context=context, project_id="p1", stop_nodes=True
        )
        self.assertEqual(preview["status"], "confirmation_required")
        token = preview["result"]["confirmation_token"]
        done = await finish_lab_goal(
            context=context,
            project_id="p1",
            stop_nodes=True,
            confirmation_token=token,
        )
        self.assertEqual(done["status"], "success")
        self.assertIn("stop_node", client.calls)

    async def test_already_closed_project_cleanup_is_convergent(self):
        client = FakeClient(
            get_project={
                "project_id": "p1",
                "name": "lab",
                "status": "closed",
            }
        )
        context = FakeContext(client)
        preview = await finish_lab_goal(
            context=context,
            project_id="p1",
            stop_nodes=True,
            close_project=True,
        )
        result = await finish_lab_goal(
            context=context,
            project_id="p1",
            stop_nodes=True,
            close_project=True,
            confirmation_token=preview["result"]["confirmation_token"],
        )

        self.assertEqual(result["status"], "success")
        self.assertNotIn("get_project_nodes", client.calls)
        self.assertNotIn("stop_node", client.calls)
        self.assertNotIn("close_project", client.calls)

    async def test_already_stopped_nodes_are_not_stopped_again(self):
        client = FakeClient(
            get_project={
                "project_id": "p1",
                "name": "lab",
                "status": "opened",
            },
            get_project_nodes=[
                {"node_id": "n1", "name": "R1", "status": "stopped"}
            ],
        )
        context = FakeContext(client)
        preview = await finish_lab_goal(
            context=context, project_id="p1", stop_nodes=True
        )
        result = await finish_lab_goal(
            context=context,
            project_id="p1",
            stop_nodes=True,
            confirmation_token=preview["result"]["confirmation_token"],
        )

        self.assertEqual(result["status"], "success")
        self.assertNotIn("stop_node", client.calls)

    async def test_remote_stop_server_fails_step(self):
        with patch(
            "gns3_skill.workflow.goals.finish_lab.is_local_server_url",
            return_value=False,
        ):
            context = FakeContext()
            preview = await finish_lab_goal(
                context=context, stop_server=True
            )
            token = preview["result"]["confirmation_token"]
            out = await finish_lab_goal(
                context=context,
                stop_server=True,
                confirmation_token=token,
            )
        self.assertIn(out["status"], ("error", "partial"))
        stop_steps = [s for s in out["steps"] if s["step"] == "stop_server"]
        self.assertTrue(stop_steps)
        self.assertEqual(stop_steps[0]["status"], "failed")


class PrepareImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_docker_rejected(self):
        out = await prepare_image_goal(
            context=FakeContext(), source_path="/tmp/x", emulator="docker"
        )
        self.assertEqual(out["status"], "error")
        self.assertIn("Docker", out["error"])

    async def test_skip_existing_image(self):
        client = FakeClient(
            list_images=[{"filename": "ios.bin"}],
        )
        with patch("gns3_skill.workflow.goals.prepare_image.Path") as path_cls:
            path_cls.return_value.is_file.return_value = True
            path_cls.return_value.name = "ios.bin"
            out = await prepare_image_goal(
                context=FakeContext(client),
                source_path="/tmp/ios.bin",
                emulator="dynamips",
                filename="ios.bin",
            )
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["result"]["import"]["action"], "skip")
        self.assertNotIn("upload_image", client.calls)

    async def test_densify_yellow(self):
        client = FakeClient()
        out = await prepare_image_goal(
            context=FakeContext(client), densify_template=True
        )
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["result"]["yellow"]["capability"], "yellow")


class ExpertOperationTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_node_start_reports_partial_mutation(self):
        def start_node(_project_id, node_id):
            if node_id == "n2":
                raise RuntimeError("start failed")
            return {"ok": True}

        client = FakeClient(
            get_project_nodes=[
                {"node_id": "n1", "name": "R1"},
                {"node_id": "n2", "name": "R2"},
            ],
            start_node=start_node,
        )
        result = await start_all_nodes(
            context=FakeContext(client), project_id="p1"
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["successful"], 1)
        self.assertEqual(len(result["failed_nodes"]), 1)

    async def test_bulk_configuration_reports_partial_mutation(self):
        async def send_console(**kwargs):
            if kwargs["node_id"] == "n2":
                return {"status": "error", "error": "console failed"}
            return {"status": "success", "results": []}

        with patch(
            "gns3_skill.operations.device_io.send_console_commands_impl",
            new=send_console,
        ):
            result = await bulk_configure_nodes(
                context=FakeContext(FakeClient()),
                project_id="p1",
                configurations=[
                    {"node_id": "n1", "commands": ["show version"]},
                    {"node_id": "n2", "commands": ["show version"]},
                ],
            )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["successful"], 1)
        self.assertEqual(result["failed"], 1)

    async def test_template_result_redacts_nested_secret_values(self):
        secret = "template-password-value"

        async def send_console(**kwargs):
            return {
                "status": "success",
                "results": [
                    {"command": command, "response": command, "completed": True}
                    for command in kwargs["commands"]
                ],
            }

        with patch(
            "gns3_skill.operations.device_io.send_console_commands_impl",
            new=send_console,
        ):
            result = await apply_config_template(
                context=FakeContext(FakeClient()),
                project_id="p1",
                node_id="n1",
                template_name="ssh",
                template_params={
                    "domain": "lab.example",
                    "username": "operator",
                    "password": secret,
                },
            )

        self.assertNotIn(secret, str(result))
        self.assertIn("<redacted>", str(result))


class RunGuestEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_resolution_preserves_node_name_evidence(self):
        client = FakeClient(
            get_project={"project_id": "p1", "name": "lab"},
            get_node={"node_id": "n1", "name": "vm1", "status": "started"},
        )

        async def fake_exec(*_args, **_kwargs):
            return {"status": "success", "results": []}

        with patch(
            "gns3_skill.workflow.goals.run_guest_commands.ssh_helpers.extract_ips_from_node",
            return_value=["192.0.2.50"],
        ), patch(
            "gns3_skill.workflow.goals.run_guest_commands.ssh_helpers.resolve_ssh_credentials",
            return_value=("operator", "secret"),
        ), patch(
            "gns3_skill.workflow.goals.run_guest_commands.ssh_helpers.exec_commands",
            new=fake_exec,
        ):
            result = await run_guest_commands_goal(
                context=FakeContext(client),
                commands=["uname"],
                project_id="p1",
                node_id="n1",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result"]["node_name"], "vm1")


class RunGuestTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_host_ssh(self):
        secret = "ssh-test-secret"

        async def fake_exec(host, commands, **kwargs):
            self.assertEqual(host, "10.0.0.9")
            self.assertEqual(kwargs["password"], secret)
            return {
                "status": "success",
                "results": [{"command": "uname", "stdout": "Linux", "exit_code": 0}],
            }

        with patch(
            "gns3_skill.workflow.goals.run_guest_commands.ssh_helpers.resolve_ssh_credentials",
            return_value=("u", secret),
        ), patch(
            "gns3_skill.workflow.goals.run_guest_commands.ssh_helpers.exec_commands",
            new=fake_exec,
        ):
            out = await run_guest_commands_goal(
                context=FakeContext(),
                commands=["uname"],
                host="10.0.0.9",
            )

        self.assertEqual(out["status"], "success")
        self.assertNotIn(secret, str(out))
        self.assertNotIn("password", out.get("result", {}).get("ssh", {}))

    async def test_missing_host_and_metadata(self):
        out = await run_guest_commands_goal(
            context=FakeContext(FakeClient()), commands=["id"]
        )
        self.assertIn(out["status"], ("error", "partial"))


if __name__ == "__main__":
    unittest.main()
