"""Server and session expert operations."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..runtime import OperationContext
from ..server_lifecycle import stop_gns3_server


async def ensure_server(*, context: OperationContext, force: bool = False) -> Dict[str, Any]:
    return await context.ensure(force=force)


async def stop_server(*, context: OperationContext) -> Dict[str, Any]:
    return await stop_gns3_server(context.server_url)


async def cleanup_session(
    *, context: OperationContext, project_id: Optional[str] = None,
    stop_nodes: bool = False, close_project: bool = False, stop_server: bool = False,
) -> Dict[str, Any]:
    """Run requested cleanup steps in fixed order; all flags default inert."""
    steps: List[Dict[str, Any]] = []

    def append(step: str, status: str, **detail: Any) -> None:
        steps.append({"step": step, "status": status, **detail})

    if not stop_nodes:
        append("stop_nodes", "skipped", reason="stop_nodes=false")
    elif not project_id:
        append("stop_nodes", "error", error="project_id required")
    else:
        try:
            client = await context.client()
            nodes = await client.get_project_nodes(project_id)
            stopped: List[Dict[str, Any]] = []
            failed: List[Dict[str, Any]] = []
            for node in nodes:
                node_id = node.get("node_id")
                name = node.get("name")
                try:
                    await client.stop_node(project_id, node_id)
                    stopped.append({"node_id": node_id, "name": name})
                except Exception as exc:
                    failed.append({"node_id": node_id, "name": name, "error": str(exc)})
            if failed:
                append(
                    "stop_nodes",
                    "error",
                    stopped_nodes=stopped,
                    failed_nodes=failed,
                    error=(
                        "all node stop attempts failed"
                        if not stopped
                        else "some nodes failed to stop"
                    ),
                    mutated=bool(stopped),
                )
            else:
                append(
                    "stop_nodes",
                    "success",
                    stopped_nodes=stopped,
                    failed_nodes=failed,
                    total=len(nodes),
                )
        except Exception as exc:
            append("stop_nodes", "error", error=str(exc))

    if not close_project:
        append("close_project", "skipped", reason="close_project=false")
    elif not project_id:
        append("close_project", "error", error="project_id required")
    else:
        try:
            client = await context.client()
            append("close_project", "success", project=await client.close_project(project_id))
        except Exception as exc:
            append("close_project", "error", error=str(exc))

    if not stop_server:
        append("stop_server", "skipped", reason="stop_server=false")
    else:
        try:
            stopped = await stop_gns3_server(context.server_url)
            step_status = "success" if stopped.get("status") == "success" else "error"
            append("stop_server", step_status, result=stopped, **({"error": stopped.get("error")} if stopped.get("error") else {}))
        except Exception as exc:
            append("stop_server", "error", error=str(exc))

    active = [step for step in steps if step["status"] != "skipped"]
    errors = [step for step in active if step["status"] == "error"]
    successes = [step for step in active if step["status"] == "success"]
    mutated = any(bool(step.get("mutated")) for step in active)
    status = (
        "partial"
        if errors and (successes or mutated)
        else "error"
        if errors
        else "success"
    )
    return {
        "status": status,
        "server_url": context.server_url,
        "project_id": project_id,
        "steps": steps,
    }


async def get_server_info(*, context: OperationContext) -> Dict[str, Any]:
    client = await context.client()
    return {"server_info": await client.get_server_info()}


async def list_computes(*, context: OperationContext) -> Dict[str, Any]:
    client = await context.client()
    computes = await client.get_compute_list()
    return {"computes": computes, "total": len(computes)}
