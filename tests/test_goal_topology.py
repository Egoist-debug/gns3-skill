"""Independent tests for topology build and diagnosis goals."""

from __future__ import annotations

import unittest
from typing import Any, Dict, List
from unittest.mock import patch

from gns3_skill.workflow.goals.build_topology import build_topology_goal
from gns3_skill.workflow.goals.diagnose_connectivity import diagnose_connectivity_goal
from gns3_skill.operations.topology import validate_topology
from gns3_skill.workflow.topology import node_port_keys, validate_topology_snapshot
from gns3_skill.runtime import normalize_outcome


async def _ensure_ok(*_args, **_kwargs):
    return {"status": "success", "already_running": True}


def _nodes() -> List[Dict[str, Any]]:
    return [
        {
            "node_id": "n1",
            "name": "R1",
            "status": "started",
            "node_type": "dynamips",
            "x": 0,
            "y": 0,
            "ports": [
                {"adapter_number": 2, "port_number": 5},
                {"adapter_number": 2, "port_number": 7},
            ],
        },
        {
            "node_id": "n2",
            "name": "R2",
            "status": "started",
            "node_type": "dynamips",
            "x": 100,
            "y": 0,
            "ports": [
                {"adapter_number": 4, "port_number": 1},
                {"adapter_number": 4, "port_number": 3},
            ],
        },
    ]


class FakeClient:
    def __init__(self, links=None, fail_create_at=None):
        self.nodes = _nodes()
        self.links = list(links or [])
        self.calls: List[str] = []
        self.created_payloads: List[Dict[str, Any]] = []
        self.fail_create_at = fail_create_at

    async def get_project(self, _project_id):
        self.calls.append("get_project")
        return {"project_id": "p1", "name": "lab", "status": "opened"}

    async def get_project_nodes(self, _project_id):
        self.calls.append("get_project_nodes")
        return self.nodes

    async def get_project_links(self, _project_id):
        self.calls.append("get_project_links")
        return self.links

    async def create_link(self, _project_id, payload):
        self.calls.append("create_link")
        call_number = len(self.created_payloads) + 1
        if self.fail_create_at == call_number:
            raise RuntimeError("create failed")
        self.created_payloads.append(payload)
        created = {"link_id": f"l{len(self.links) + 1}", **payload}
        self.links.append(created)
        return created

    async def start_node(self, _project_id, _node_id):
        self.calls.append("start_node")
        return {"ok": True}

    async def get_template(self, template_id):
        self.calls.append("get_template")
        return {"template_id": template_id, "name": template_id}

    async def create_node_from_template(
        self, _project_id, template_id, *, x, y, name, compute_id
    ):
        self.calls.append("create_node_from_template")
        created = {
            "node_id": f"created-{name}",
            "name": name,
            "template_id": template_id,
            "x": x,
            "y": y,
            "compute_id": compute_id,
            "ports": [],
        }
        self.nodes.append(created)
        return created

class FakeContext:
    def __init__(self, client):
        self.server_url = "http://127.0.0.1:3080"
        self._client = client

    async def ensure(self, *, force=False):
        return await _ensure_ok(force=force)

    async def client(self, *, force_ensure=False):
        return self._client


class TopologyPureTests(unittest.TestCase):
    def test_port_inventory_and_overlap_validation(self):
        nodes = _nodes()
        self.assertEqual(node_port_keys(nodes[0]), [(2, 5), (2, 7)])
        nodes[1]["x"] = 0
        validation = validate_topology_snapshot(nodes, [])
        self.assertFalse(validation["is_valid"])
        self.assertTrue(validation["issues"])
        self.assertEqual(validation["disconnected_nodes"], 2)


class BuildTopologyTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, client, links, *, validate=False):
        return await build_topology_goal(
            context=FakeContext(client),
            project_id="p1",
            links=links,
            validate=validate,
        )

    async def test_implicit_link_uses_advertised_ports_and_retry_reuses(self):
        client = FakeClient()
        spec = [{"a": "R1", "b": "R2"}]
        first = await self._run(client, spec)
        second = await self._run(client, spec)
        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        self.assertEqual(len(client.created_payloads), 1)
        endpoints = client.created_payloads[0]["nodes"]
        self.assertEqual(
            (endpoints[0]["adapter_number"], endpoints[0]["port_number"]),
            (2, 5),
        )
        self.assertEqual(
            (endpoints[1]["adapter_number"], endpoints[1]["port_number"]),
            (4, 1),
        )
        self.assertEqual(len(second["result"]["skipped_links"]), 1)

    async def test_half_specified_endpoint_fails_without_mutation(self):
        client = FakeClient()
        result = await self._run(
            client,
            [
                {
                    "a": {"node_name": "R1", "adapter": 2},
                    "b": {"node_name": "R2"},
                }
            ],
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("provided together", result["error"])
        self.assertNotIn("create_link", client.calls)

    async def test_parallel_implicit_links_replay_stably(self):
        existing = [
            {
                "link_id": "l1",
                "nodes": [
                    {"node_id": "n1", "adapter_number": 2, "port_number": 5},
                    {"node_id": "n2", "adapter_number": 4, "port_number": 1},
                ],
            },
            {
                "link_id": "l2",
                "nodes": [
                    {"node_id": "n1", "adapter_number": 2, "port_number": 7},
                    {"node_id": "n2", "adapter_number": 4, "port_number": 3},
                ],
            },
        ]
        client = FakeClient(existing)
        result = await self._run(
            client,
            [{"a": "R1", "b": "R2"}, {"a": "R1", "b": "R2"}],
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["result"]["skipped_links"]), 2)
        self.assertNotIn("create_link", client.calls)
    async def test_later_invalid_link_does_not_create_earlier_link(self):
        client = FakeClient()
        result = await self._run(
            client,
            [
                {"a": "R1", "b": "R2"},
                {
                    "a": {"node_name": "R1", "adapter": 2},
                    "b": {"node_name": "R2"},
                },
            ],
        )
        self.assertEqual(result["status"], "error")
        self.assertNotIn("create_link", client.calls)

    async def test_later_node_conflict_does_not_create_planned_node(self):
        client = FakeClient()
        client.nodes[1]["template_id"] = "existing-template"
        result = await build_topology_goal(
            context=FakeContext(client),
            project_id="p1",
            nodes=[
                {"name": "R3", "template_id": "new-template"},
                {"name": "R2", "template_id": "other-template"},
            ],
            validate=False,
        )
        self.assertEqual(result["status"], "conflict")
        self.assertNotIn("create_node_from_template", client.calls)

    async def test_node_creation_defaults_to_local_compute(self):
        client = FakeClient()
        result = await build_topology_goal(
            context=FakeContext(client),
            project_id="p1",
            nodes=[{"name": "R3", "template_id": "template-3"}],
            validate=False,
        )

        self.assertEqual(result["status"], "success")
        created = next(node for node in client.nodes if node.get("name") == "R3")
        self.assertEqual(created["compute_id"], "local")

    async def test_second_link_failure_reports_partial(self):
        client = FakeClient(fail_create_at=2)
        result = await self._run(
            client,
            [{"a": "R1", "b": "R2"}, {"a": "R1", "b": "R2"}],
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(client.created_payloads), 1)
        step = next(
            item for item in result["steps"] if item["step"] == "converge_links"
        )
        self.assertTrue(step["mutated"])

    async def test_build_validation_marks_overlap_invalid(self):
        client = FakeClient()
        client.nodes[1]["x"] = 0
        result = await self._run(client, [], validate=True)
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["result"]["validation"]["is_valid"])
        self.assertTrue(result["result"]["validation"]["issues"])




class ExpertTopologyTests(unittest.IsolatedAsyncioTestCase):
    async def test_expert_tool_uses_shared_validation(self):
        client = FakeClient()
        client.nodes[1]["x"] = 0
        raw = await validate_topology(
            context=FakeContext(client), project_id="p1"
        )
        result = normalize_outcome(raw).to_dict("validate_topology", "expert")
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["result"]["validation"]["is_valid"])
        self.assertTrue(result["result"]["validation"]["issues"])


class DiagnoseTopologyTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_project_selector_returns_goal_error(self):
        result = await diagnose_connectivity_goal(context=FakeContext(FakeClient()))

        self.assertEqual(result["status"], "error")
        self.assertIn("project_id or project_name", result["error"])

    async def test_shared_validation_marks_overlap_invalid(self):
        client = FakeClient()
        client.nodes[1]["x"] = 0
        result = await diagnose_connectivity_goal(
            context=FakeContext(client), project_id="p1"
        )
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["result"]["validation"]["is_valid"])
        issue_findings = [
            finding
            for finding in result["result"]["findings"]
            if finding["code"] == "topology_issue"
        ]
        self.assertTrue(issue_findings)

    async def test_probe_failure_after_start_reports_partial(self):
        client = FakeClient()
        client.nodes[0]["status"] = "stopped"

        async def probe_error(**_kwargs):
            return {"status": "error", "error": "console unavailable"}

        with patch(
            "gns3_skill.workflow.goals.diagnose_connectivity.send_console_commands",
            new=probe_error,
        ):
            result = await diagnose_connectivity_goal(
                context=FakeContext(client),
                project_id="p1",
                suspect_nodes=[{"node_name": "R1"}],
            )
        self.assertEqual(result["status"], "partial")
        self.assertIn("start_node", client.calls)
        probe_step = next(
            item for item in result["steps"] if item["step"] == "console_probes"
        )
        self.assertTrue(probe_step["mutated"])


if __name__ == "__main__":
    unittest.main()
