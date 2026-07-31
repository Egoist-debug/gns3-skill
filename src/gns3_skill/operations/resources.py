"""Template, appliance, image, and Dynamips resource expert operations."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ..runtime import OperationContext


async def list_templates(*, context: OperationContext) -> Dict[str, Any]:
    """List available device templates using a stable summary payload."""
    client = await context.client()
    templates = await client.get_templates()
    summaries = [
        {
            "name": template.get("name"),
            "template_id": template.get("template_id"),
            "template_type": template.get("template_type"),
            "category": template.get("category"),
            "builtin": template.get("builtin", False),
            "symbol": template.get("symbol"),
        }
        for template in templates
    ]
    return {"templates": summaries, "total": len(summaries)}


async def list_appliances(*, context: OperationContext) -> Dict[str, Any]:
    """List available appliances using a stable summary payload."""
    client = await context.client()
    appliances = await client.get_appliances()
    summaries = [
        {
            "name": appliance.get("name"),
            "appliance_id": appliance.get("appliance_id"),
            "category": appliance.get("category"),
            "vendor": appliance.get("vendor"),
            "product_name": appliance.get("product_name"),
            "status": appliance.get("status"),
        }
        for appliance in appliances
    ]
    return {"appliances": summaries, "total": len(summaries)}


async def list_images(
    *,
    context: OperationContext,
    emulator: str = "qemu",
    compute_id: str = "local",
) -> Dict[str, Any]:
    """List images available on one compute for an emulator."""
    client = await context.client()
    images = await client.list_images(compute_id=compute_id, emulator=emulator)
    return {
        "compute_id": compute_id,
        "emulator": emulator,
        "images": images,
        "total": len(images) if isinstance(images, list) else 0,
    }


async def import_image(
    *,
    context: OperationContext,
    source_path: str,
    emulator: str = "qemu",
    filename: Optional[str] = None,
    compute_id: str = "local",
) -> Dict[str, Any]:
    """Upload a local image to a GNS3 compute image store."""
    if emulator.lower() == "docker":
        return {
            "status": "error",
            "error": (
                "Docker images are managed by Docker (pull/load), not GNS3 "
                "image upload. Use emulator=qemu|dynamips|iou."
            ),
        }

    path = Path(source_path)
    if not path.is_file():
        return {"status": "error", "error": f"Image file not found: {source_path}"}

    client = await context.client()
    result = await client.upload_image(
        compute_id=compute_id,
        emulator=emulator,
        filename=filename or path.name,
        source_path=str(path),
    )
    return {"import": result}


async def get_idle_pc_values(
    *,
    context: OperationContext,
    project_id: str,
    node_id: str,
    auto_compute: bool = True,
) -> Dict[str, Any]:
    """Get an automatic Idle-PC value or candidate proposals for Dynamips."""
    client = await context.client()
    if auto_compute:
        return {
            "idlepc": await client.get_node_dynamips_auto_idlepc(
                project_id, node_id
            )
        }
    return {
        "idlepc_proposals": await client.get_node_dynamips_idlepc_proposals(
            project_id, node_id
        )
    }
