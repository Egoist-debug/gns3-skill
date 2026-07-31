"""Run-guest-commands goal implementation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gns3_skill import ssh_client as ssh_helpers
from gns3_skill.gns3_client import GNS3APIClient
from gns3_skill.runtime import OperationContext
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


async def run_guest_commands_goal(
    *,
    context: OperationContext,
    commands: Optional[List[str]] = None,
    host: Optional[str] = None,
    port: int = 22,
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    node_name: Optional[str] = None,
    node_id: Optional[str] = None,
    ssh_username: Optional[str] = None,
    ssh_password: Optional[str] = None,
    stop_on_error: bool = True,
    host_key_policy: str = "accept_new",
    connect_timeout: Optional[float] = None,
) -> Dict[str, Any]:
    goal = "run_guest_commands"
    commands = list(commands or [])
    if not commands:
        return error_envelope(goal, "commands list is required")

    ctx: Dict[str, Any] = {
        "client": None,
        "project": None,
        "node": None,
        "host": host,
        "ssh_result": None,
    }

    async def ensure_step() -> Dict[str, Any]:
        # Only ensure when resolving via GNS3 metadata
        if host:
            return step_entry(
                "ensure_server",
                STEP_SKIPPED,
                detail={"reason": "explicit host provided"},
            )
        result = await context.ensure()
        if result.get("status") != "success":
            return step_entry(
                "ensure_server",
                STEP_FAILED,
                error=result.get("error") or "GNS3 server not available",
            )
        ctx["client"] = await context.client()
        return step_entry("ensure_server", STEP_SUCCESS)

    async def resolve_host_step() -> Dict[str, Any]:
        if host:
            ctx["host"] = host
            return step_entry(
                "resolve_host",
                STEP_SUCCESS,
                detail={"host": host, "source": "explicit"},
            )
        if not ctx["client"]:
            return step_entry(
                "resolve_host",
                STEP_FAILED,
                error="host is required, or provide project and node for metadata lookup",
            )
        try:
            project = await resolve_project(
                ctx["client"], project_id=project_id, project_name=project_name
            )
        except (ResolveMissing, ResolveAmbiguous, ValueError) as exc:
            return step_entry("resolve_host", STEP_FAILED, error=str(exc))
        ctx["project"] = project
        pid = project["project_id"]
        try:
            node = await resolve_node(
                ctx["client"], pid, node_id=node_id, node_name=node_name
            )
        except (ResolveMissing, ResolveAmbiguous, ValueError) as exc:
            return step_entry("resolve_host", STEP_FAILED, error=str(exc))
        ctx["node"] = node
        started = False
        if node.get("status") != "started":
            try:
                await ctx["client"].start_node(pid, node["node_id"])
            except Exception as exc:
                return step_entry(
                    "resolve_host",
                    STEP_FAILED,
                    error=f"failed to start node for SSH: {exc}",
                )
            started = True
        ips = ssh_helpers.extract_ips_from_node(
            node if isinstance(node, dict) else {}
        )
        if not ips:
            return step_entry(
                "resolve_host",
                STEP_FAILED,
                error="Could not resolve guest IP from node metadata; pass host explicitly",
                mutated=started,
            )
        ctx["host"] = ips[0]
        return step_entry(
            "resolve_host",
            STEP_CHANGED if started else STEP_SUCCESS,
            detail={
                "host": ctx["host"],
                "source": "node_metadata",
                "node_name": node.get("name"),
            },
        )

    async def exec_step() -> Dict[str, Any]:
        user, passwd = ssh_helpers.resolve_ssh_credentials(ssh_username, ssh_password)
        out = await ssh_helpers.exec_commands(
            ctx["host"],
            commands,
            port=port,
            username=user,
            password=passwd,
            stop_on_error=stop_on_error,
            host_key_policy=host_key_policy,
            connect_timeout=connect_timeout,
        )
        # scrub any accidental password fields
        if isinstance(out, dict):
            out = {k: v for k, v in out.items() if "password" not in k.lower()}
            if "results" in out and isinstance(out["results"], list):
                cleaned = []
                for item in out["results"]:
                    if isinstance(item, dict):
                        cleaned.append(
                            {k: v for k, v in item.items() if "password" not in k.lower()}
                        )
                    else:
                        cleaned.append(item)
                out["results"] = cleaned
        ctx["ssh_result"] = out
        if isinstance(out, dict) and out.get("status") == "error":
            return step_entry(
                "ssh_exec",
                STEP_FAILED,
                error=out.get("error") or "ssh exec failed",
                detail={"host": ctx["host"]},
            )
        return step_entry(
            "ssh_exec",
            STEP_SUCCESS,
            detail={"host": ctx["host"], "command_count": len(commands)},
        )

    result = await run_steps(
        [
            Step("ensure_server", ensure_step),
            Step("resolve_host", resolve_host_step),
            Step("ssh_exec", exec_step),
        ]
    )
    ssh_out = ctx.get("ssh_result") if isinstance(ctx.get("ssh_result"), dict) else {}
    return goal_envelope(
        goal,
        result.status,
        result.steps,
        result={
            "host": ctx.get("host"),
            "node_name": (ctx.get("node") or {}).get("name"),
            "ssh": ssh_out,
        },
        error=result.error,
        next_hint=None if result.status == STATUS_SUCCESS else "Provide host or fix node metadata IP",
    )
