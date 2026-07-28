"""Pure topology inventory and validation helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

PortKey = Tuple[int, int]


def node_port_keys(node: Dict[str, Any]) -> List[PortKey]:
    """Return the node's advertised adapter/port pairs in stable order."""
    ports = node.get("ports")
    if not isinstance(ports, list):
        return []
    result: Set[PortKey] = set()
    for port in ports:
        if not isinstance(port, dict):
            continue
        adapter = port.get("adapter_number")
        port_number = port.get("port_number")
        if adapter is None or port_number is None:
            continue
        result.add((int(adapter), int(port_number)))
    return sorted(result)


def validate_topology_snapshot(
    nodes: List[Dict[str, Any]],
    links: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Validate an observed topology without performing network I/O."""
    connected_nodes: Set[str] = set()
    for link in links:
        ends = link.get("nodes") if isinstance(link, dict) else None
        if not isinstance(ends, list) or len(ends) < 2:
            continue
        for end in ends[:2]:
            if isinstance(end, dict) and end.get("node_id"):
                connected_nodes.add(str(end["node_id"]))

    issues: List[str] = []
    warnings: List[str] = []
    for node in nodes:
        node_id = node.get("node_id")
        if node_id not in connected_nodes:
            warnings.append(f"Node '{node.get('name')}' has no connections")
        if (
            node.get("node_type") in ("dynamips", "iou", "qemu")
            and node.get("status") != "started"
        ):
            warnings.append(f"Critical node '{node.get('name')}' is not running")

    positions: Dict[Tuple[Any, Any], str] = {}
    for node in nodes:
        position = (node.get("x"), node.get("y"))
        if position in positions:
            issues.append(
                f"Nodes '{node.get('name')}' and '{positions[position]}' "
                f"overlap at position {position}"
            )
        positions[position] = str(node.get("name"))

    return {
        "total_nodes": len(nodes),
        "total_links": len(links),
        "connected_nodes": len(connected_nodes),
        "disconnected_nodes": len(nodes) - len(connected_nodes),
        "issues": issues,
        "warnings": warnings,
        "is_valid": not issues,
    }
