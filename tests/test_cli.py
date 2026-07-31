from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import AsyncMock, patch

from gns3_skill import cli
from gns3_skill.contracts import (
    OperationError,
    OperationOutcome,
    OperationSpec,
    OperationStatus,
    OperationTier,
)
from gns3_skill.runtime import invoke


class _Stdin(io.StringIO):
    def isatty(self) -> bool:
        return False


def _call_cli(argv, *, stdin: str = ""):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("sys.stdin", _Stdin(stdin)), redirect_stdout(stdout), redirect_stderr(
        stderr
    ):
        exit_status = cli.main(argv)
    output = stdout.getvalue()
    return exit_status, json.loads(output), output, stderr.getvalue()


class DiscoveryCliTests(unittest.TestCase):
    def test_list_is_json_and_filters_exact_tier_counts(self):
        cases = [
            ([], 8, {"goal"}),
            (["--tier=goal"], 8, {"goal"}),
            (["--tier=expert"], 50, {"expert"}),
            (["--tier=all"], 58, {"goal", "expert"}),
        ]

        for args, expected_count, expected_tiers in cases:
            with self.subTest(args=args):
                status, payload, raw, stderr = _call_cli(["list", *args])
                operations = payload["operations"]
                self.assertEqual(status, 0)
                self.assertEqual(payload["total"], expected_count)
                self.assertEqual(len(operations), expected_count)
                self.assertEqual({entry["tier"] for entry in operations}, expected_tiers)
                self.assertTrue(raw.lstrip().startswith("{"))
                self.assertEqual(stderr, "")

    def test_describe_is_registry_metadata_json(self):
        status, payload, _raw, stderr = _call_cli(["describe", "prepare_lab"])

        self.assertEqual(status, 0)
        self.assertEqual(payload["identifier"], "prepare_lab")
        self.assertEqual(payload["tier"], "goal")
        self.assertTrue(payload["summary"])
        self.assertEqual(payload["schema"]["type"], "object")
        self.assertIn("properties", payload["schema"])
        self.assertFalse(payload["schema"]["additionalProperties"])
        self.assertEqual(stderr, "")

    def test_describe_marks_sensitive_fields_without_values(self):
        status, payload, raw, stderr = _call_cli(
            ["describe", "send_console_commands"]
        )

        self.assertEqual(status, 0)
        password = payload["schema"]["properties"]["login_password"]
        self.assertTrue(password["sensitive"])
        self.assertNotIn("value", password)
        self.assertNotIn("example", password)
        self.assertNotIn("secret-value", raw)
        self.assertEqual(stderr, "")

    def test_invalid_list_tier_and_unknown_describe_are_json_usage_errors(self):
        cases = (["list", "--tier=internal"], ["describe", "missing_operation"])

        for argv in cases:
            with self.subTest(argv=argv):
                status, payload, raw, _stderr = _call_cli(argv)
                self.assertEqual(status, 2)
                self.assertEqual(payload["status"], "error")
                self.assertEqual(payload["error"]["type"], "usage")
                self.assertEqual(json.loads(raw), payload)


class RunInputCliTests(unittest.TestCase):
    def _successful_invoke(self):
        return patch(
            "gns3_skill.cli.invoke",
            new=AsyncMock(
                return_value=OperationOutcome(
                    status=OperationStatus.SUCCESS,
                    result={"accepted": True},
                )
            ),
        )

    def test_key_value_input_coerces_scalar_boolean_and_json_values(self):
        nodes = [{"name": "R1", "template_id": "t1"}]
        with self._successful_invoke() as invoke_mock:
            status, payload, _raw, stderr = _call_cli(
                [
                    "run",
                    "build_topology",
                    "--project_id=p1",
                    "--start=true",
                    f"--nodes={json.dumps(nodes)}",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(payload["status"], "success")
        inputs = invoke_mock.await_args.args[1]
        self.assertEqual(inputs["project_id"], "p1")
        self.assertIs(inputs["start"], True)
        self.assertEqual(inputs["nodes"], nodes)
        self.assertEqual(stderr, "")

    def test_json_argument_preserves_typed_objects(self):
        body = {
            "project_id": "p1",
            "nodes": [{"name": "R1", "template_id": "t1"}],
            "validate": False,
        }
        with self._successful_invoke() as invoke_mock:
            status, payload, _raw, stderr = _call_cli(
                ["run", "build_topology", "--json", json.dumps(body)]
            )

        self.assertEqual(status, 0)
        self.assertEqual(payload["operation"], "build_topology")
        self.assertEqual(invoke_mock.await_args.args[1], body)
        self.assertEqual(stderr, "")

    def test_stdin_json_preserves_typed_objects(self):
        body = {"commands": ["id", "uname"], "host": "192.0.2.10", "port": 2222}
        with self._successful_invoke() as invoke_mock:
            status, payload, _raw, stderr = _call_cli(
                ["run", "run_guest_commands"], stdin=json.dumps(body)
            )

        self.assertEqual(status, 0)
        self.assertEqual(payload["operation"], "run_guest_commands")
        self.assertEqual(invoke_mock.await_args.args[1], body)
        self.assertEqual(stderr, "")

    def test_duplicate_key_value_fields_are_rejected(self):
        with patch("gns3_skill.cli.invoke", new=AsyncMock()) as invoke_mock:
            status, payload, _raw, _stderr = _call_cli(
                [
                    "run",
                    "build_topology",
                    "--project_id=p1",
                    "--project_id=p2",
                ]
            )

        self.assertEqual(status, 2)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["operation"], "build_topology")
        invoke_mock.assert_not_awaited()

    def test_json_and_key_value_duplicate_fields_are_rejected(self):
        with patch("gns3_skill.cli.invoke", new=AsyncMock()) as invoke_mock:
            status, payload, _raw, _stderr = _call_cli(
                [
                    "run",
                    "finish_lab",
                    "--stop_nodes=true",
                    "--json",
                    '{"stop_nodes": false}',
                ]
            )

        self.assertEqual(status, 2)
        self.assertEqual(payload["error"]["type"], "usage")
        invoke_mock.assert_not_awaited()

    def test_duplicate_keys_inside_json_objects_are_rejected(self):
        duplicate_json = '{"stop_nodes": true, "stop_nodes": false}'
        sources = [
            (["run", "finish_lab", "--json", duplicate_json], ""),
            (["run", "finish_lab"], duplicate_json),
        ]

        for argv, stdin in sources:
            with self.subTest(argv=argv, stdin=stdin):
                with patch("gns3_skill.cli.invoke", new=AsyncMock()) as invoke_mock:
                    status, payload, _raw, _stderr = _call_cli(argv, stdin=stdin)
                self.assertEqual(status, 2)
                self.assertEqual(payload["error"]["type"], "usage")
                invoke_mock.assert_not_awaited()

    def test_json_argument_and_stdin_are_mutually_exclusive(self):
        status, payload, _raw, _stderr = _call_cli(
            ["run", "finish_lab", "--json", "{}"], stdin="{}"
        )

        self.assertEqual(status, 2)
        self.assertEqual(payload["error"]["type"], "usage")

    def test_unknown_missing_and_malformed_fields_are_usage_errors(self):
        cases = [
            (["run", "finish_lab", "--wat=true"], ""),
            (["run", "manage_snapshot"], ""),
            (["run", "finish_lab", "--stop_nodes=maybe"], ""),
            (["run", "build_topology", "--nodes=not-json"], ""),
            (["run", "finish_lab", "--json", "{"], ""),
            (["run", "finish_lab"], "[1, 2]"),
            (["run", "finish_lab"], "{"),
            (["run", "finish_lab", "--json", '{"stop_nodes": NaN}'], ""),
        ]

        for argv, stdin in cases:
            with self.subTest(argv=argv, stdin=stdin):
                with patch("gns3_skill.cli.invoke", new=AsyncMock()) as invoke_mock:
                    status, payload, raw, _stderr = _call_cli(argv, stdin=stdin)
                self.assertEqual(status, 2)
                self.assertEqual(payload["status"], "error")
                self.assertIn("error", payload)
                self.assertNotIn("result", payload)
                self.assertEqual(json.loads(raw), payload)
                invoke_mock.assert_not_awaited()

    def test_usage_error_does_not_echo_sensitive_values(self):
        secret = "do-not-leak-this-password"
        status, payload, raw, stderr = _call_cli(
            [
                "run",
                "ensure_server",
                f"--password={secret}",
                "--unknown=true",
            ]
        )

        self.assertEqual(status, 2)
        self.assertEqual(payload["status"], "error")
        self.assertNotIn(secret, raw)
        self.assertNotIn(secret, stderr)


class RunEnvelopeCliTests(unittest.TestCase):
    def test_every_runtime_status_has_unified_envelope_and_exit_mapping(self):
        cases = [
            (OperationOutcome(OperationStatus.SUCCESS, result={"ok": True}), 0),
            (
                OperationOutcome(
                    OperationStatus.CONFIRMATION_REQUIRED,
                    result={"confirmation_token": "token"},
                ),
                0,
            ),
            (OperationOutcome(OperationStatus.PARTIAL, result={"steps": []}), 1),
            (OperationOutcome(OperationStatus.CONFLICT, result={"existing": {}}), 1),
            (
                OperationOutcome(
                    OperationStatus.ERROR,
                    error=OperationError("runtime", "boom", {"retryable": False}),
                ),
                1,
            ),
        ]

        for outcome, expected_exit in cases:
            with self.subTest(status=outcome.status):
                with patch(
                    "gns3_skill.cli.invoke", new=AsyncMock(return_value=outcome)
                ):
                    status, payload, raw, stderr = _call_cli(
                        ["run", "finish_lab"]
                    )
                self.assertEqual(status, expected_exit)
                self.assertEqual(payload["operation"], "finish_lab")
                self.assertEqual(payload["tier"], "goal")
                self.assertEqual(payload["status"], outcome.status.value)
                self.assertEqual(
                    int("result" in payload) + int("error" in payload), 1
                )
                self.assertEqual(json.loads(raw), payload)
                self.assertEqual(stderr, "")

    def test_unknown_operation_is_structured_usage_error(self):
        status, payload, raw, stderr = _call_cli(
            ["run", "operation_that_does_not_exist"]
        )

        self.assertEqual(status, 2)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["operation"], "operation_that_does_not_exist")
        self.assertIsNone(payload["tier"])
        self.assertEqual(payload["error"]["type"], "usage")
        self.assertNotIn("result", payload)
        self.assertEqual(json.loads(raw), payload)
        self.assertEqual(stderr, "")

    def test_old_direct_commands_are_rejected_with_exit_two(self):
        for command in ("gns3_prepare_lab", "prepare_lab"):
            with self.subTest(command=command):
                status, payload, raw, _stderr = _call_cli([command])
                self.assertEqual(status, 2)
                self.assertEqual(payload["status"], "error")
                self.assertNotIn("result", payload)
                self.assertEqual(json.loads(raw), payload)

    def test_keyboard_interrupt_is_a_structured_operation_error(self):
        with patch(
            "gns3_skill.cli.invoke",
            new=AsyncMock(side_effect=KeyboardInterrupt),
        ):
            status, payload, raw, _stderr = _call_cli(["run", "finish_lab"])

        self.assertEqual(status, 1)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error"]["type"], "interrupted")
        self.assertEqual(json.loads(raw), payload)


class RuntimeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_sensitive_inputs_are_redacted_from_results(self):
        secret = "runtime-secret-value"

        async def leaking_operation(*, context):
            return {"echo": context.password}

        spec = OperationSpec(
            "leaking_operation",
            OperationTier.EXPERT,
            leaking_operation,
            "Exercise runtime redaction.",
            frozenset({"password"}),
        )
        outcome = await invoke(spec, {"password": secret})

        self.assertEqual(outcome.status, OperationStatus.SUCCESS)
        self.assertNotIn(secret, str(outcome.result))
        self.assertEqual(outcome.result["echo"], "<redacted>")

    async def test_non_json_operation_data_becomes_runtime_error(self):
        async def invalid_operation(*, context):
            return {"value": object()}

        spec = OperationSpec(
            "invalid_operation",
            OperationTier.EXPERT,
            invalid_operation,
            "Exercise JSON validation.",
        )
        outcome = await invoke(spec, {})

        self.assertEqual(outcome.status, OperationStatus.ERROR)
        self.assertEqual(outcome.error.type, "runtime")
        self.assertIn("valid JSON", outcome.error.message)


if __name__ == "__main__":
    unittest.main()
