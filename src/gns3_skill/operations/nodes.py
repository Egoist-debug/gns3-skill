"""Node management and lifecycle expert operations."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..runtime import OperationContext


async def list_nodes(
    *, context: OperationContext, project_id: str
) -> Dict[str, Any]:
    """List nodes with their stable identity, console, position, and port fields."""
    client = await context.client()
    nodes = await client.get_project_nodes(project_id)
    summaries: List[Dict[str, Any]] = []
    for node in nodes:
        summaries.append(
            {
                "name": node.get("name"),
                "node_id": node.get("node_id"),
                "node_type": node.get("node_type"),
                "status": node.get("status"),
                "console": node.get("console"),
                "console_type": node.get("console_type"),
                "console_host": node.get("console_host"),
                "x": node.get("x"),
                "y": node.get("y"),
                "ports": len(node.get("ports", [])),
            }
        )
    return {"nodes": summaries, "total_nodes": len(summaries)}


async def add_node(
    *,
    context: OperationContext,
    project_id: str,
    node_name: str,
    template_id: str,
    x: int = 0,
    y: int = 0,
    compute_id: str = "local",
) -> Dict[str, Any]:
    client = await context.client()
    node = await client.create_node_from_template(
        project_id=project_id,
        template_id=template_id,
        x=x,
        y=y,
        compute_id=compute_id,
        name=node_name,
    )
    return {"node": node}


async def get_node(
    *, context: OperationContext, project_id: str, node_id: str
) -> Dict[str, Any]:
    client = await context.client()
    return {"node": await client.get_node(project_id, node_id)}


async def update_node(
    *,
    context: OperationContext,
    project_id: str,
    node_id: str,
    name: Optional[str] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
    properties: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    update_data: Dict[str, Any] = {}
    if name is not None:
        update_data["name"] = name
    if x is not None:
        update_data["x"] = x
    if y is not None:
        update_data["y"] = y
    if properties is not None:
        update_data["properties"] = properties

    client = await context.client()
    return {"node": await client.update_node(project_id, node_id, update_data)}


async def delete_node(
    *, context: OperationContext, project_id: str, node_id: str
) -> Dict[str, Any]:
    client = await context.client()
    await client.delete_node(project_id, node_id)
    return {"message": f"Node {node_id} deleted successfully"}


async def start_node(
    *, context: OperationContext, project_id: str, node_id: str
) -> Dict[str, Any]:
    client = await context.client()
    node = await client.start_node(project_id, node_id)
    return {"node": node, "message": "Node started"}


async def stop_node(
    *, context: OperationContext, project_id: str, node_id: str
) -> Dict[str, Any]:
    client = await context.client()
    node = await client.stop_node(project_id, node_id)
    return {"node": node, "message": "Node stopped"}


async def suspend_node(
    *, context: OperationContext, project_id: str, node_id: str
) -> Dict[str, Any]:
    client = await context.client()
    node = await client.suspend_node(project_id, node_id)
    return {"node": node, "message": "Node suspended"}


async def reload_node(
    *, context: OperationContext, project_id: str, node_id: str
) -> Dict[str, Any]:
    client = await context.client()
    node = await client.reload_node(project_id, node_id)
    return {"node": node, "message": "Node reloaded"}


async def duplicate_node(
    *,
    context: OperationContext,
    project_id: str,
    node_id: str,
    x: int = 50,
    y: int = 50,
) -> Dict[str, Any]:
    client = await context.client()
    node = await client.duplicate_node(project_id, node_id, x, y)
    return {"node": node, "message": "Node duplicated"}


async def start_all_nodes(
    *, context: OperationContext, project_id: str
) -> Dict[str, Any]:
    """Attempt every node start and report per-node failures without fail-fast."""
    client = await context.client()
    nodes = await client.get_project_nodes(project_id)
    started: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for node in nodes:
        try:
            await client.start_node(project_id, node["node_id"])
            started.append({"node_id": node["node_id"], "name": node["name"]})
        except Exception as exc:
            failed.append(
                {
                    "node_id": node["node_id"],
                    "name": node["name"],
                    "error": str(exc),
                }
            )
    if failed:
        status = "partial" if started else "error"
        return {
            "status": status,
            "error": "some nodes failed to start" if started else "all node start attempts failed",
            "started_nodes": started,
            "failed_nodes": failed,
            "total": len(nodes),
            "successful": len(started),
        }
    return {
        "started_nodes": started,
        "failed_nodes": [],
        "total": len(nodes),
        "successful": len(started),
    }


async def stop_all_nodes(
    *, context: OperationContext, project_id: str
) -> Dict[str, Any]:
    """Attempt every node stop and report per-node failures without fail-fast."""
    client = await context.client()
    nodes = await client.get_project_nodes(project_id)
    stopped: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for node in nodes:
        try:
            await client.stop_node(project_id, node["node_id"])
            stopped.append({"node_id": node["node_id"], "name": node["name"]})
        except Exception as exc:
            failed.append(
                {
                    "node_id": node["node_id"],
                    "name": node["name"],
                    "error": str(exc),
                }
            )
    if failed:
        status = "partial" if stopped else "error"
        return {
            "status": status,
            "error": "some nodes failed to stop" if stopped else "all node stop attempts failed",
            "stopped_nodes": stopped,
            "failed_nodes": failed,
            "total": len(nodes),
            "successful": len(stopped),
        }
    return {
        "stopped_nodes": stopped,
        "failed_nodes": [],
        "total": len(nodes),
        "successful": len(stopped),
    }
