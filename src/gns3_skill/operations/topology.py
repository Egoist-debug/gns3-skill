"""Link, topology, capture, and canvas expert operations."""
from __future__ import annotations

from typing import Any, Dict

from ..runtime import OperationContext
from ..workflow.topology import validate_topology_snapshot


async def list_links(*, context: OperationContext, project_id: str) -> Dict[str, Any]:
    """List project links with endpoint node names and port details."""
    client = await context.client()
    links = await client.get_project_links(project_id)
    nodes = await client.get_project_nodes(project_id)
    node_lookup = {node["node_id"]: node["name"] for node in nodes}

    summaries = []
    for link in links:
        node_a = link["nodes"][0]
        node_b = link["nodes"][1]
        summaries.append(
            {
                "link_id": link.get("link_id"),
                "node_a": node_lookup.get(node_a["node_id"], "Unknown"),
                "node_a_id": node_a["node_id"],
                "port_a": node_a.get("port_name", ""),
                "adapter_a": node_a.get("adapter_number"),
                "port_number_a": node_a.get("port_number"),
                "node_b": node_lookup.get(node_b["node_id"], "Unknown"),
                "node_b_id": node_b["node_id"],
                "port_b": node_b.get("port_name", ""),
                "adapter_b": node_b.get("adapter_number"),
                "port_number_b": node_b.get("port_number"),
                "link_type": link.get("link_type"),
                "capturing": link.get("capturing", False),
            }
        )
    return {"links": summaries, "total_links": len(summaries)}


async def add_link(
    *,
    context: OperationContext,
    project_id: str,
    node_a_id: str,
    node_b_id: str,
    adapter_a: int = 0,
    port_a: int = 0,
    adapter_b: int = 0,
    port_b: int = 0,
) -> Dict[str, Any]:
    """Create a link between two provenance-supplied node endpoints."""
    client = await context.client()
    link = await client.create_link(
        project_id,
        {
            "nodes": [
                {
                    "node_id": node_a_id,
                    "adapter_number": adapter_a,
                    "port_number": port_a,
                },
                {
                    "node_id": node_b_id,
                    "adapter_number": adapter_b,
                    "port_number": port_b,
                },
            ]
        },
    )
    return {"link": link}


async def delete_link(
    *, context: OperationContext, project_id: str, link_id: str
) -> Dict[str, Any]:
    """Delete a link from a project."""
    client = await context.client()
    await client.delete_link(project_id, link_id)
    return {"message": f"Link {link_id} deleted successfully"}


async def get_topology(
    *, context: OperationContext, project_id: str
) -> Dict[str, Any]:
    """Return project metadata, nodes, links, and inventory counts."""
    client = await context.client()
    project = await client.get_project(project_id)
    nodes = await client.get_project_nodes(project_id)
    links = await client.get_project_links(project_id)
    return {
        "project": {
            "name": project.get("name"),
            "project_id": project.get("project_id"),
            "status": project.get("status"),
        },
        "nodes": nodes,
        "links": links,
        "summary": {
            "total_nodes": len(nodes),
            "total_links": len(links),
            "running_nodes": sum(
                1 for node in nodes if node.get("status") == "started"
            ),
            "stopped_nodes": sum(
                1 for node in nodes if node.get("status") == "stopped"
            ),
        },
    }


async def validate_topology(
    *, context: OperationContext, project_id: str
) -> Dict[str, Any]:
    """Validate an observed project topology with the shared pure validator."""
    client = await context.client()
    nodes = await client.get_project_nodes(project_id)
    links = await client.get_project_links(project_id)
    return {"validation": validate_topology_snapshot(nodes, links)}


async def start_capture(
    *,
    context: OperationContext,
    project_id: str,
    link_id: str,
    capture_file_name: str,
    data_link_type: str = "DLT_EN10MB",
) -> Dict[str, Any]:
    """Start packet capture on a project link."""
    client = await context.client()
    capture = await client.start_capture(
        project_id, link_id, capture_file_name, data_link_type
    )
    return {"capture": capture, "message": "Packet capture started"}


async def stop_capture(
    *, context: OperationContext, project_id: str, link_id: str
) -> Dict[str, Any]:
    """Stop packet capture on a project link."""
    client = await context.client()
    await client.stop_capture(project_id, link_id)
    return {"message": "Packet capture stopped"}


async def add_text_annotation(
    *,
    context: OperationContext,
    project_id: str,
    text: str,
    x: int,
    y: int,
    rotation: int = 0,
) -> Dict[str, Any]:
    """Add a text annotation to the topology canvas."""
    client = await context.client()
    drawing = await client.create_drawing(
        project_id,
        {
            "svg": (
                '<text font-family="TypeWriter" font-size="10" '
                f'fill="#000000">{text}</text>'
            ),
            "x": x,
            "y": y,
            "rotation": rotation,
        },
    )
    return {"drawing": drawing}


async def add_shape(
    *,
    context: OperationContext,
    project_id: str,
    shape_type: str,
    x: int,
    y: int,
    width: int,
    height: int,
    color: str = "#000000",
    fill_color: str | None = None,
) -> Dict[str, Any]:
    """Add a rectangle or ellipse to the topology canvas."""
    if shape_type == "rectangle":
        svg = (
            f'<rect width="{width}" height="{height}" stroke="{color}" '
            f'fill="{fill_color or "none"}" />'
        )
    elif shape_type == "ellipse":
        rx = width // 2
        ry = height // 2
        svg = (
            f'<ellipse cx="{rx}" cy="{ry}" rx="{rx}" ry="{ry}" '
            f'stroke="{color}" fill="{fill_color or "none"}" />'
        )
    else:
        return {"status": "error", "error": f"Unknown shape type: {shape_type}"}

    client = await context.client()
    drawing = await client.create_drawing(
        project_id, {"svg": svg, "x": x, "y": y}
    )
    return {"drawing": drawing}
