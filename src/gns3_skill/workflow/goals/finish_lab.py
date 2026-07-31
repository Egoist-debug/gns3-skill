"""Finish-lab goal implementation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gns3_skill.gns3_client import GNS3APIClient
from gns3_skill.runtime import OperationContext
from gns3_skill.server_lifecycle import is_local_server_url, stop_gns3_server
from gns3_skill.workflow.confirm import consume_token, issue_token
from gns3_skill.workflow.envelopes import (
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    STEP_CHANGED,
    STEP_FAILED,
    STEP_SKIPPED,
    STEP_SUCCESS,
    confirmation_required_envelope,
    goal_envelope,
    step_entry,
)
from gns3_skill.workflow.resolve import ResolveAmbiguous, ResolveMissing, resolve_project


async def finish_lab_goal(
    *,
    context: OperationContext,
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    stop_nodes: bool = False,
    close_project: bool = False,
    stop_server: bool = False,
    confirmation_token: Optional[str] = None,
) -> Dict[str, Any]:
    goal = "finish_lab"
    url = context.server_url
    destructive = bool(stop_nodes or close_project or stop_server)

    if not destructive:
        return goal_envelope(
            goal,
            STATUS_SUCCESS,
            [
                step_entry(
                    "intent",
                    STEP_SKIPPED,
                    detail={
                        "reason": "all cleanup flags false",
                        "hint": "Ask the user which of stop_nodes/close_project/stop_server to enable",
                    },
                )
            ],
            result={
                "stop_nodes": False,
                "close_project": False,
                "stop_server": False,
                "message": "nothing requested; ask user before enabling destructive flags",
            },
            next_hint="Confirm with user, then re-call with explicit flags (and token if needed)",
        )

    steps: List[Dict[str, Any]] = []
    client: Optional[GNS3APIClient] = None
    project: Optional[Dict[str, Any]] = None
    pid: Optional[str] = None
    needs_project = stop_nodes or close_project

    if needs_project:
        ensure = await context.ensure()
        if ensure.get("status") != "success":
            error = ensure.get("error") or "server unavailable"
            steps.append(step_entry("ensure_server", STEP_FAILED, error=error))
            return goal_envelope(goal, "error", steps, error=error)
        steps.append(step_entry("ensure_server", STEP_SUCCESS))
        client = await context.client()
        try:
            project = await resolve_project(
                client, project_id=project_id, project_name=project_name
            )
        except (ResolveMissing, ResolveAmbiguous, ValueError) as exc:
            steps.append(step_entry("resolve_project", STEP_FAILED, error=str(exc)))
            return goal_envelope(goal, "error", steps, error=str(exc))
        pid = project["project_id"]
        steps.append(
            step_entry(
                "resolve_project",
                STEP_SUCCESS,
                detail={"project_id": pid, "name": project.get("name")},
            )
        )

    target = {
        "action": "finish_lab",
        "project_id": pid,
        "stop_nodes": bool(stop_nodes),
        "close_project": bool(close_project),
        "stop_server": bool(stop_server),
        "server_url": url,
    }
    impact = {
        "project_id": pid,
        "project_name": None if project is None else project.get("name"),
        "stop_nodes": bool(stop_nodes),
        "close_project": bool(close_project),
        "stop_server": bool(stop_server),
        "server_url": url,
        "local_server": is_local_server_url(url),
        "order": ["stop_nodes", "close_project", "stop_server"],
    }

    if not confirmation_token:
        token, expires = issue_token("finish_lab", target)
        steps.append(
            step_entry(
                "authorization",
                STEP_SUCCESS,
                detail={"phase": "preview", "impact": impact},
            )
        )
        return confirmation_required_envelope(
            goal,
            steps,
            action="finish_lab",
            target=target,
            impact=impact,
            confirmation_token=token,
            expires_at=expires,
        )

    if stop_server and not is_local_server_url(url):
        error = "refusing to stop remote GNS3 server"
        steps.append(
            step_entry(
                "stop_server",
                STEP_FAILED,
                error=error,
                detail={"server_url": url},
            )
        )
        return goal_envelope(goal, "error", steps, error=error)

    consumed = consume_token(confirmation_token, "finish_lab", target)
    if not consumed.get("ok"):
        error = consumed.get("error") or "token rejected"
        steps.append(step_entry("authorization", STEP_FAILED, error=error))
        return goal_envelope(goal, "error", steps, error=error)
    steps.append(
        step_entry("authorization", STEP_SUCCESS, detail={"phase": "consumed"})
    )

    def failed_result(
        step: str,
        error: str,
        *,
        detail: Optional[Dict[str, Any]] = None,
        mutated: bool = False,
    ) -> Dict[str, Any]:
        steps.append(step_entry(step, STEP_FAILED, error=error, detail=detail))
        had_change = mutated or any(
            entry.get("status") == STEP_CHANGED for entry in steps
        )
        return goal_envelope(
            goal,
            STATUS_PARTIAL if had_change else "error",
            steps,
            result={
                "project_id": pid,
                "stop_nodes": stop_nodes,
                "close_project": close_project,
                "stop_server": stop_server,
                "server_url": url,
            },
            error=error,
            next_hint="Fix the failed cleanup step, observe current state, and retry safely",
        )

    project_status = (project or {}).get("status")

    if stop_nodes:
        if project_status == "closed":
            steps.append(
                step_entry(
                    "stop_nodes",
                    STEP_SKIPPED,
                    detail={"reason": "project already closed"},
                )
            )
        else:
            assert client is not None and pid is not None
            nodes: List[Dict[str, Any]] = []
            try:
                nodes = await client.get_project_nodes(pid)
            except Exception as exc:
                error = str(exc)
                steps.append(step_entry("stop_nodes", STEP_FAILED, error=error))
                return goal_envelope(goal, "error", steps, error=error)
            stopped: List[Any] = []
            failed: List[Dict[str, Any]] = []
            for node in nodes:
                if node.get("status") == "stopped":
                    continue
                try:
                    await client.stop_node(pid, node["node_id"])
                    stopped.append(node.get("name"))
                except Exception as exc:
                    failed.append({"name": node.get("name"), "error": str(exc)})
            if failed:
                return failed_result(
                    "stop_nodes",
                    "some nodes failed to stop" if stopped else "all node stop attempts failed",
                    detail={"stopped": stopped, "failed": failed},
                    mutated=bool(stopped),
                )
            steps.append(
                step_entry(
                    "stop_nodes",
                    STEP_CHANGED if stopped else STEP_SKIPPED,
                    detail={
                        "stopped": stopped,
                        "reason": None if stopped else "all nodes already stopped",
                    },
                )
            )
    else:
        steps.append(
            step_entry("stop_nodes", STEP_SKIPPED, detail={"reason": "stop_nodes=false"})
        )

    if close_project:
        if project_status == "closed":
            steps.append(
                step_entry(
                    "close_project",
                    STEP_SKIPPED,
                    detail={"reason": "project already closed"},
                )
            )
        else:
            assert client is not None and pid is not None
            try:
                await client.close_project(pid)
            except Exception as exc:
                return failed_result("close_project", str(exc))
            steps.append(
                step_entry(
                    "close_project", STEP_CHANGED, detail={"project_id": pid}
                )
            )
    else:
        steps.append(
            step_entry(
                "close_project", STEP_SKIPPED, detail={"reason": "close_project=false"}
            )
        )

    if stop_server:
        try:
            stop_result = await stop_gns3_server(url)
        except Exception as exc:
            return failed_result("stop_server", str(exc))
        if stop_result.get("status") != "success":
            return failed_result(
                "stop_server",
                stop_result.get("error") or "stop failed",
                detail=stop_result,
            )
        steps.append(
            step_entry(
                "stop_server",
                STEP_CHANGED if stop_result.get("stopped") else STEP_SUCCESS,
                detail=stop_result,
            )
        )
    else:
        steps.append(
            step_entry(
                "stop_server", STEP_SKIPPED, detail={"reason": "stop_server=false"}
            )
        )

    return goal_envelope(
        goal,
        STATUS_SUCCESS,
        steps,
        result={
            "project_id": pid,
            "stop_nodes": stop_nodes,
            "close_project": close_project,
            "stop_server": stop_server,
            "server_url": url,
        },
    )
