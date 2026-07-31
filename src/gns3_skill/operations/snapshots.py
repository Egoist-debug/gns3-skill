"""Expert project snapshot operations."""
from __future__ import annotations

from typing import Any, Dict

from ..runtime import OperationContext


async def list_snapshots(
    *, context: OperationContext, project_id: str
) -> Dict[str, Any]:
    """List all snapshots for a project."""
    client = await context.client()
    snapshots = await client.get_snapshots(project_id)
    return {"snapshots": snapshots, "total": len(snapshots)}


async def create_snapshot(
    *, context: OperationContext, project_id: str, snapshot_name: str
) -> Dict[str, Any]:
    """Create a snapshot of the current project state."""
    client = await context.client()
    snapshot = await client.create_snapshot(project_id, snapshot_name)
    return {"snapshot": snapshot}


async def restore_snapshot(
    *, context: OperationContext, project_id: str, snapshot_id: str
) -> Dict[str, Any]:
    """Restore a project from a user-confirmed snapshot."""
    client = await context.client()
    result = await client.restore_snapshot(project_id, snapshot_id)
    return {"result": result, "message": "Snapshot restored successfully"}


async def delete_snapshot(
    *, context: OperationContext, project_id: str, snapshot_id: str
) -> Dict[str, Any]:
    """Permanently delete a user-confirmed snapshot."""
    client = await context.client()
    await client.delete_snapshot(project_id, snapshot_id)
    return {"message": f"Snapshot {snapshot_id} deleted"}
