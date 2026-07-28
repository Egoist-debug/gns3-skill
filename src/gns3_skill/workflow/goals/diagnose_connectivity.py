"""gns3_diagnose_connectivity goal implementation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gns3_skill.gns3_client import GNS3APIClient, GNS3Config
from gns3_skill.server_lifecycle import ensure_gns3_server, normalize_server_url
from gns3_skill.workflow.console_ops import send_console_commands
from gns3_skill.workflow.envelopes import (
    STATUS_SUCCESS,
    STEP_CHANGED,
    STEP_FAILED,
    STEP_SKIPPED,
    STEP_SUCCESS,
    error_envelope,
    goal_envelope,
    step_entry,
)
from gns3_skill.workflow.resolve import ResolveAmbiguous, ResolveMissing, resolve_node, resolve_project
from gns3_skill.workflow.runner import Step, run_steps
from gns3_skill.workflow.topology import validate_topology_snapshot


async def diagnose_connectivity_goal(
    *,
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    suspect_nodes: Optional[List[Dict[str, Any]]] = None,
    probe_commands: Optional[List[str]] = None,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    goal = "diagnose_connectivity"
    url = normalize_server_url(server_url)
    suspect_nodes = suspect_nodes or []
    probe_commands = probe_commands or ["show ip interface brief", "show ip route"]
    if not project_id and not (project_name and str(project_name).strip()):
        return error_envelope(goal, "project_id or project_name is required")

    ctx: Dict[str, Any] = {
        "client": None,
        "project": None,
        "validation": None,
        "topology": None,
        "probes": [],
        "findings": [],
    }

    async def ensure_step() -> Dict[str, Any]:
        config = GNS3Config.from_env(server_url=url, username=username, password=password)
        result = await ensure_gns3_server(
            config.server_url, username=config.username, password=config.password
        )
        if result.get("status") != "success":
            return step_entry(
                "ensure_server",
                STEP_FAILED,
                error=result.get("error") or "GNS3 server not available",
            )
        ctx["client"] = GNS3APIClient(config)
        return step_entry("ensure_server", STEP_SUCCESS)

    async def resolve_project_step() -> Dict[str, Any]:
        try:
            project = await resolve_project(
                ctx["client"], project_id=project_id, project_name=project_name
            )
        except (ResolveMissing, ResolveAmbiguous, ValueError) as exc:
            return step_entry("resolve_project", STEP_FAILED, error=str(exc))
        ctx["project"] = project
        return step_entry(
            "resolve_project",
            STEP_SUCCESS,
            detail={"project_id": project.get("project_id")},
        )

    async def validate_step() -> Dict[str, Any]:
        client: GNS3APIClient = ctx["client"]
        pid = ctx["project"]["project_id"]
        nodes = await client.get_project_nodes(pid)
        links = await client.get_project_links(pid)
        ctx["validation"] = validate_topology_snapshot(nodes, links)
        for issue in ctx["validation"]["issues"]:
            ctx["findings"].append(
                {
                    "severity": "error",
                    "code": "topology_issue",
                    "message": issue,
                    "source_step": "validate_topology",
                }
            )
        for warning in ctx["validation"]["warnings"]:
            ctx["findings"].append(
                {
                    "severity": "warning",
                    "code": "topology_warning",
                    "message": warning,
                    "source_step": "validate_topology",
                }
            )
        ctx["topology"] = {
            "nodes": [
                {
                    "name": node.get("name"),
                    "node_id": node.get("node_id"),
                    "status": node.get("status"),
                    "node_type": node.get("node_type"),
                }
                for node in nodes
            ],
            "link_count": len(links),
        }
        return step_entry(
            "validate_topology", STEP_SUCCESS, detail=ctx["validation"]
        )

    async def probe_step() -> Dict[str, Any]:
        if not suspect_nodes:
            return step_entry(
                "console_probes",
                STEP_SKIPPED,
                detail={"reason": "no suspect_nodes"},
            )
        client: GNS3APIClient = ctx["client"]
        project_id = ctx["project"]["project_id"]
        mutated = False

        def probe_failure(
            error: str, detail: Optional[Dict[str, Any]] = None
        ) -> Dict[str, Any]:
            return step_entry(
                "console_probes",
                STEP_FAILED,
                error=error,
                detail=detail,
                mutated=mutated,
            )

        for spec in suspect_nodes:
            try:
                node = await resolve_node(
                    client,
                    project_id,
                    node_id=spec.get("node_id"),
                    node_name=spec.get("node_name") or spec.get("name"),
                )
            except (ResolveMissing, ResolveAmbiguous, ValueError) as exc:
                return probe_failure(str(exc))
            commands = list(spec.get("commands") or probe_commands)
            if node.get("status") != "started":
                try:
                    await client.start_node(project_id, node["node_id"])
                except Exception as exc:
                    return probe_failure(
                        f"cannot start {node.get('name')}: {exc}"
                    )
                mutated = True
            output = await send_console_commands(
                project_id=project_id,
                node_id=node["node_id"],
                commands=commands,
                server_url=url,
                username=username,
                password=password,
                enter_config_mode=False,
                save_config=False,
                login_username=spec.get("login_username"),
                login_password=spec.get("login_password"),
            )
            if output.get("status") != "success":
                return probe_failure(
                    output.get("error") or "probe failed",
                    {"node": node.get("name")},
                )
            ctx["probes"].append(
                {
                    "node_name": node.get("name"),
                    "node_id": node.get("node_id"),
                    "results": output.get("results"),
                }
            )
            for item in output.get("results") or []:
                body = (item.get("response") or "").strip()
                if not body:
                    continue
                ctx["findings"].append(
                    {
                        "severity": "info",
                        "code": "console_probe_output",
                        "message": f"Probe on {node.get('name')}: {item.get('command')}",
                        "source_step": "console_probes",
                        "node": node.get("name"),
                        "command": item.get("command"),
                        "response_excerpt": body[:500],
                    }
                )
        return step_entry(
            "console_probes",
            STEP_CHANGED if mutated else STEP_SUCCESS,
            detail={"probed": [probe["node_name"] for probe in ctx["probes"]]},
        )

    result = await run_steps(
        [
            Step("ensure_server", ensure_step),
            Step("resolve_project", resolve_project_step),
            Step("validate_topology", validate_step),
            Step("console_probes", probe_step),
        ]
    )
    return goal_envelope(
        goal,
        result.status,
        result.steps,
        result={
            "project_id": (ctx.get("project") or {}).get("project_id"),
            "validation": ctx["validation"],
            "topology": ctx["topology"],
            "probes": ctx["probes"],
            "findings": ctx["findings"],
        },
        error=result.error,
        next_hint=None
        if result.status == STATUS_SUCCESS
        else "Review findings; no automatic remediation was applied",
    )
