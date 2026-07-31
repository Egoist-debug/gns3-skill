"""Configure-devices goal implementation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gns3_skill.config_templates import ConfigTemplates
from gns3_skill.gns3_client import GNS3APIClient
from gns3_skill.runtime import OperationContext
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


def _template_commands(template_name: str, params: Dict[str, Any]) -> List[str]:
    mapping = {
        "basic_router": lambda p: ConfigTemplates.basic_router_config(
            p["hostname"], p.get("domain", "lab.local")
        ),
        "interface": lambda p: ConfigTemplates.interface_config(
            p["interface"],
            p.get("ip") or p.get("ip_address"),
            p.get("mask") or p.get("subnet_mask"),
            description=p.get("description"),
        ),
        "vlan": lambda p: ConfigTemplates.vlan_config(
            int(p["vlan_id"]), p.get("name") or f"VLAN{p['vlan_id']}"
        ),
        "ospf": lambda p: ConfigTemplates.ospf_config(
            int(p["process_id"]),
            p.get("router_id") or "1.1.1.1",
            p.get("networks") or [],
        ),
    }
    fn = mapping.get(template_name)
    if not fn:
        raise ValueError(f"unsupported template_name: {template_name}")
    return list(fn(params or {}))

def _incomplete_commands(
    commands: List[str], result: Dict[str, Any]
) -> List[str]:
    entries = result.get("results")
    if not isinstance(entries, list):
        return list(commands)
    incomplete: List[str] = []
    for index, command in enumerate(commands):
        if (
            index >= len(entries)
            or not isinstance(entries[index], dict)
            or entries[index].get("completed") is not True
        ):
            incomplete.append(command)
    return incomplete


async def configure_devices_goal(
    *,
    context: OperationContext,
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    targets: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    goal = "configure_devices"
    targets = targets or []
    if not targets:
        return error_envelope(goal, "targets list is required")
    if not project_id and not (project_name and str(project_name).strip()):
        return error_envelope(goal, "project_id or project_name is required")

    ctx: Dict[str, Any] = {
        "client": None,
        "project": None,
        "results": [],
    }

    async def ensure_step() -> Dict[str, Any]:
        result = await context.ensure()
        if result.get("status") != "success":
            return step_entry(
                "ensure_server",
                STEP_FAILED,
                error=result.get("error") or "GNS3 server not available",
                detail=result,
            )
        ctx["client"] = await context.client()
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

    async def configure_step() -> Dict[str, Any]:
        client: GNS3APIClient = ctx["client"]
        pid = ctx["project"]["project_id"]
        mutated = False

        def configure_failure(
            error: str, detail: Optional[Dict[str, Any]] = None
        ) -> Dict[str, Any]:
            return step_entry(
                "configure_targets",
                STEP_FAILED,
                error=error,
                detail=detail,
                mutated=mutated,
            )

        for idx, target in enumerate(targets):
            node_name = target.get("node_name")
            node_id = target.get("node_id")
            try:
                node = await resolve_node(
                    client, pid, node_id=node_id, node_name=node_name
                )
            except ResolveMissing as exc:
                return configure_failure(
                    f"{exc}; run list_nodes to resolve names",
                    {
                        "target_index": idx,
                        "target": {"node_name": node_name, "node_id": node_id},
                    },
                )
            except (ResolveAmbiguous, ValueError) as exc:
                return configure_failure(str(exc), {"target_index": idx})

            node_id = node.get("node_id")
            if node.get("status") != "started":
                try:
                    await client.start_node(pid, node_id)
                except Exception as exc:
                    return configure_failure(
                        f"failed to start node {node.get('name')}: {exc}"
                    )
                mutated = True

            commands = list(target.get("commands") or [])
            template_name = target.get("template_name")
            if template_name:
                try:
                    commands = _template_commands(
                        template_name, target.get("params") or {}
                    )
                except Exception as exc:
                    return configure_failure(
                        str(exc), {"node": node.get("name")}
                    )
            if not commands:
                return configure_failure(
                    f"no commands for node {node.get('name')}"
                )

            console_result = await send_console_commands(
                context=context,
                project_id=pid,
                node_id=node_id,
                commands=commands,
                enter_config_mode=bool(target.get("enter_config_mode", True)),
                save_config=bool(target.get("save_config", False)),
                enable_password=target.get("enable_password"),
                login_username=target.get("login_username"),
                login_password=target.get("login_password"),
            )
            if console_result.get("status") != "success":
                mutated = mutated or bool(console_result.get("results"))
                return configure_failure(
                    console_result.get("error") or "console failed",
                    {"node": node.get("name"), "console": console_result},
                )

            mutated = True
            incomplete = _incomplete_commands(commands, console_result)
            if incomplete:
                return configure_failure(
                    f"console did not complete commands on {node.get('name')}",
                    {
                        "node": node.get("name"),
                        "incomplete_commands": incomplete,
                    },
                )

            verify_commands = list(target.get("verify_commands") or [])
            verify_result = None
            if verify_commands:
                verify_result = await send_console_commands(
                    context=context,
                    project_id=pid,
                    node_id=node_id,
                    commands=verify_commands,
                    enter_config_mode=False,
                    save_config=False,
                    login_username=target.get("login_username"),
                    login_password=target.get("login_password"),
                )
                if verify_result.get("status") != "success":
                    return configure_failure(
                        verify_result.get("error") or "verify failed",
                        {"node": node.get("name"), "verify": verify_result},
                    )
                incomplete_verify = _incomplete_commands(
                    verify_commands, verify_result
                )
                if incomplete_verify:
                    return configure_failure(
                        f"verification did not complete on {node.get('name')}",
                        {
                            "node": node.get("name"),
                            "incomplete_commands": incomplete_verify,
                        },
                    )

            ctx["results"].append(
                {
                    "node_name": node.get("name"),
                    "node_id": node_id,
                    "commands": console_result.get("results"),
                    "verify": None
                    if not verify_result
                    else verify_result.get("results"),
                }
            )

        return step_entry(
            "configure_targets",
            STEP_CHANGED,
            detail={"configured": [item["node_name"] for item in ctx["results"]]},
        )

    result = await run_steps(
        [
            Step("ensure_server", ensure_step),
            Step("resolve_project", resolve_project_step),
            Step("configure_targets", configure_step),
        ]
    )
    return goal_envelope(
        goal,
        result.status,
        result.steps,
        result={
            "project_id": (ctx.get("project") or {}).get("project_id"),
            "targets": ctx["results"],
        },
        error=result.error,
        next_hint=None
        if result.status == STATUS_SUCCESS
        else "Resolve node names with list_nodes and retry the failed target",
    )
