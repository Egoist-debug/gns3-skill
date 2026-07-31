"""Independent completion-condition tests for configure and image goals."""

from __future__ import annotations

import unittest
from typing import List
from unittest.mock import patch

from gns3_skill.workflow.goals.configure_devices import configure_devices_goal
from gns3_skill.workflow.goals.prepare_image import prepare_image_goal


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
            return value

        return call

class FakeContext:
    def __init__(self, client):
        self.server_url = "http://127.0.0.1:3080"
        self._client = client

    async def ensure(self, *, force=False):
        return await _ensure_ok(force=force)

    async def client(self, *, force_ensure=False):
        return self._client


class ConfigureCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, console_results, target=None):
        client = FakeClient(
            get_project={"project_id": "p1", "name": "lab"},
            get_node={"node_id": "n1", "name": "R1", "status": "started"},
        )
        remaining = list(console_results)

        async def send_console(**_kwargs):
            return remaining.pop(0)

        with patch(
            "gns3_skill.workflow.goals.configure_devices.send_console_commands",
            new=send_console,
        ):
            result = await configure_devices_goal(
                context=FakeContext(client),
                project_id="p1",
                targets=[
                    target
                    or {
                        "node_id": "n1",
                        "commands": ["hostname R1"],
                    }
                ],
            )
        return result

    async def test_incomplete_configuration_fails(self):
        result = await self._run(
            [
                {
                    "status": "success",
                    "results": [
                        {
                            "command": "hostname R1",
                            "response": "",
                            "completed": False,
                        }
                    ],
                }
            ]
        )
        self.assertEqual(result["status"], "partial")
        self.assertIn("did not complete", result["error"])

    async def test_incomplete_verification_fails(self):
        target = {
            "node_id": "n1",
            "commands": ["hostname R1"],
            "verify_commands": ["show hostname"],
        }
        result = await self._run(
            [
                {
                    "status": "success",
                    "results": [{"command": "hostname R1", "completed": True}],
                },
                {
                    "status": "success",
                    "results": [{"command": "show hostname", "completed": False}],
                },
            ],
            target=target,
        )
        self.assertEqual(result["status"], "partial")
        self.assertIn("verification did not complete", result["error"])

    async def test_completed_configuration_succeeds(self):
        result = await self._run(
            [
                {
                    "status": "success",
                    "results": [{"command": "hostname R1", "completed": True}],
                }
            ]
        )
        self.assertEqual(result["status"], "success")


class PrepareImageCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, image_lists):
        client = FakeClient(
            list_images=list(image_lists),
            upload_image={"filename": "ios.bin", "size_bytes": 10},
        )
        with patch("gns3_skill.workflow.goals.prepare_image.Path") as path_class:
            path = path_class.return_value
            path.is_file.return_value = True
            path.name = "ios.bin"
            path.__str__.return_value = "/tmp/ios.bin"
            result = await prepare_image_goal(
                context=FakeContext(client),
                source_path="/tmp/ios.bin",
                emulator="dynamips",
                filename="ios.bin",
            )
        return result, client

    async def test_uploaded_image_missing_is_partial(self):
        result, client = await self._run([[], []])
        self.assertEqual(result["status"], "partial")
        self.assertIn("not visible", result["error"])
        self.assertEqual(client.calls.count("list_images"), 2)

    async def test_uploaded_image_visible_succeeds(self):
        result, client = await self._run([[], [{"filename": "ios.bin"}]])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result"]["import"]["observed"]["filename"], "ios.bin")
        self.assertEqual(client.calls.count("list_images"), 2)


if __name__ == "__main__":
    unittest.main()
