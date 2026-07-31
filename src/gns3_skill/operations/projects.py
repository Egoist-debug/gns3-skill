"""Project management expert operations."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..runtime import OperationContext


async def list_projects(*, context: OperationContext) -> Dict[str, Any]:
    """List projects with the stable summary fields used by the CLI."""
    client = await context.client()
    projects = await client.get_projects()
    summaries: List[Dict[str, Any]] = []
    for project in projects:
        summaries.append(
            {
                "name": project.get("name", "Unnamed"),
                "project_id": project.get("project_id", ""),
                "status": project.get("status", "unknown"),
                "path": project.get("path", ""),
                "filename": project.get("filename", ""),
                "auto_close": project.get("auto_close", False),
                "auto_open": project.get("auto_open", False),
                "auto_start": project.get("auto_start", False),
            }
        )
    return {"projects": summaries, "total_projects": len(summaries)}


async def create_project(
    *,
    context: OperationContext,
    name: str,
    auto_close: bool = False,
    auto_open: bool = False,
    auto_start: bool = False,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    client = await context.client()
    project = await client.create_project(name, auto_close, auto_open, auto_start, path)
    return {"project": project}


async def get_project(*, context: OperationContext, project_id: str) -> Dict[str, Any]:
    client = await context.client()
    return {"project": await client.get_project(project_id)}


async def update_project(
    *,
    context: OperationContext,
    project_id: str,
    name: Optional[str] = None,
    auto_close: Optional[bool] = None,
    auto_open: Optional[bool] = None,
    auto_start: Optional[bool] = None,
) -> Dict[str, Any]:
    update_data: Dict[str, Any] = {}
    if name is not None:
        update_data["name"] = name
    if auto_close is not None:
        update_data["auto_close"] = auto_close
    if auto_open is not None:
        update_data["auto_open"] = auto_open
    if auto_start is not None:
        update_data["auto_start"] = auto_start

    client = await context.client()
    return {"project": await client.update_project(project_id, **update_data)}


async def open_project(*, context: OperationContext, project_id: str) -> Dict[str, Any]:
    client = await context.client()
    return {"project": await client.open_project(project_id)}


async def close_project(*, context: OperationContext, project_id: str) -> Dict[str, Any]:
    """Close a project; GNS3 also stops its nodes."""
    client = await context.client()
    project = await client.close_project(project_id)
    return {"project": project, "message": "Project closed successfully"}


async def delete_project(*, context: OperationContext, project_id: str) -> Dict[str, Any]:
    client = await context.client()
    await client.delete_project(project_id)
    return {"message": f"Project {project_id} deleted permanently"}


async def duplicate_project(
    *,
    context: OperationContext,
    project_id: str,
    new_name: str,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    client = await context.client()
    project = await client.duplicate_project(project_id, new_name, path)
    return {"project": project, "message": "Project duplicated successfully"}


async def save_project(
    *,
    context: OperationContext,
    project_id: str,
    snapshot_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Read the auto-persisted project state and optionally create a snapshot."""
    client = await context.client()
    project = await client.get_project(project_id)
    snapshot = None
    if snapshot_name:
        snapshot = await client.create_snapshot(project_id, snapshot_name)
    return {
        "project": {
            "project_id": project.get("project_id"),
            "name": project.get("name"),
            "project_status": project.get("status"),
            "filename": project.get("filename"),
            "path": project.get("path"),
        },
        "snapshot": snapshot,
        "message": (
            f"Project checkpointed with snapshot '{snapshot_name}'"
            if snapshot_name
            else "Project status retrieved (GNS3 auto-persists project files)"
        ),
    }


async def export_project(
    *,
    context: OperationContext,
    project_id: str,
    output_path: str,
    include_images: bool = False,
    include_snapshots: bool = False,
    reset_mac_addresses: bool = False,
    keep_compute_ids: bool = False,
    compression: str = "zip",
) -> Dict[str, Any]:
    client = await context.client()
    exported = await client.export_project(
        project_id=project_id,
        output_path=output_path,
        include_images=include_images,
        include_snapshots=include_snapshots,
        reset_mac_addresses=reset_mac_addresses,
        keep_compute_ids=keep_compute_ids,
        compression=compression,
    )
    return {"export": exported}
