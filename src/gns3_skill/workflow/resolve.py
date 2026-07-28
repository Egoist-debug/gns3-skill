"""Natural-key resource resolution with reuse / conflict / missing."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from gns3_skill.gns3_client import GNS3APIClient


class ResolveConflict(Exception):
    def __init__(self, message: str, existing: Dict[str, Any], expected: Dict[str, Any]):
        super().__init__(message)
        self.existing = existing
        self.expected = expected


class ResolveAmbiguous(Exception):
    def __init__(self, message: str, candidates: List[Dict[str, Any]]):
        super().__init__(message)
        self.candidates = candidates


class ResolveMissing(Exception):
    pass


def _norm(s: Optional[str]) -> str:
    return (s or "").strip()


async def resolve_project(
    client: GNS3APIClient,
    *,
    project_id: Optional[str] = None,
    project_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve project by id or exact name. Raises ResolveMissing if absent."""
    if project_id:
        project = await client.get_project(project_id)
        if not isinstance(project, dict):
            raise ResolveMissing(f"project_id not found: {project_id}")
        return project

    name = _norm(project_name)
    if not name:
        raise ValueError("project_id or project_name is required")

    projects = await client.get_projects()
    matches = [p for p in projects if isinstance(p, dict) and p.get("name") == name]
    if not matches:
        raise ResolveMissing(f"project not found: {name}")
    if len(matches) > 1:
        raise ResolveAmbiguous(
            f"multiple projects named {name!r}",
            candidates=[{"project_id": p.get("project_id"), "name": p.get("name")} for p in matches],
        )
    return matches[0]


async def resolve_or_missing_project(
    client: GNS3APIClient,
    *,
    project_id: Optional[str] = None,
    project_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    try:
        return await resolve_project(
            client, project_id=project_id, project_name=project_name
        )
    except ResolveMissing:
        return None


async def resolve_node(
    client: GNS3APIClient,
    project_id: str,
    *,
    node_id: Optional[str] = None,
    node_name: Optional[str] = None,
) -> Dict[str, Any]:
    if node_id:
        node = await client.get_node(project_id, node_id)
        if not isinstance(node, dict):
            raise ResolveMissing(f"node_id not found: {node_id}")
        return node
    name = _norm(node_name)
    if not name:
        raise ValueError("node_id or node_name is required")
    nodes = await client.get_project_nodes(project_id)
    matches = [n for n in nodes if isinstance(n, dict) and n.get("name") == name]
    if not matches:
        raise ResolveMissing(f"node not found: {name}")
    if len(matches) > 1:
        raise ResolveAmbiguous(
            f"multiple nodes named {name!r}",
            candidates=[{"node_id": n.get("node_id"), "name": n.get("name")} for n in matches],
        )
    return matches[0]


async def resolve_template(
    client: GNS3APIClient,
    *,
    template_id: Optional[str] = None,
    template_name: Optional[str] = None,
) -> Dict[str, Any]:
    if template_id:
        tpl = await client.get_template(template_id)
        if not isinstance(tpl, dict):
            raise ResolveMissing(f"template_id not found: {template_id}")
        return tpl
    name = _norm(template_name)
    if not name:
        raise ValueError("template_id or template_name is required")
    templates = await client.get_templates()
    matches = [t for t in templates if isinstance(t, dict) and t.get("name") == name]
    if not matches:
        raise ResolveMissing(f"template not found: {name}")
    if len(matches) > 1:
        raise ResolveAmbiguous(
            f"multiple templates named {name!r}",
            candidates=[
                {"template_id": t.get("template_id"), "name": t.get("name")}
                for t in matches
            ],
        )
    return matches[0]


def link_endpoint_key(
    node_name: str,
    adapter: int,
    port: int,
) -> Tuple[str, int, int]:
    return (_norm(node_name), int(adapter), int(port))


def unordered_link_key(
    a: Tuple[str, int, int],
    b: Tuple[str, int, int],
) -> Tuple[Tuple[str, int, int], Tuple[str, int, int]]:
    return (a, b) if a <= b else (b, a)


def link_natural_key_from_nodes(
    link: Dict[str, Any],
    node_id_to_name: Dict[str, str],
) -> Optional[Tuple[Tuple[str, int, int], Tuple[str, int, int]]]:
    nodes = link.get("nodes") or []
    if len(nodes) < 2:
        return None
    ends: List[Tuple[str, int, int]] = []
    for end in nodes[:2]:
        nid = end.get("node_id")
        name = node_id_to_name.get(nid or "", "")
        if not name:
            return None
        ends.append(
            link_endpoint_key(
                name,
                int(end.get("adapter_number", 0)),
                int(end.get("port_number", 0)),
            )
        )
    return unordered_link_key(ends[0], ends[1])


async def find_link_by_endpoints(
    client: GNS3APIClient,
    project_id: str,
    a: Tuple[str, int, int],
    b: Tuple[str, int, int],
) -> Optional[Dict[str, Any]]:
    nodes = await client.get_project_nodes(project_id)
    id_to_name = {
        n.get("node_id"): n.get("name", "")
        for n in nodes
        if isinstance(n, dict) and n.get("node_id")
    }
    want = unordered_link_key(a, b)
    links = await client.get_project_links(project_id)
    for link in links:
        if not isinstance(link, dict):
            continue
        key = link_natural_key_from_nodes(link, id_to_name)
        if key == want:
            return link
    return None


async def resolve_snapshot(
    client: GNS3APIClient,
    project_id: str,
    *,
    snapshot_id: Optional[str] = None,
    snapshot_name: Optional[str] = None,
) -> Dict[str, Any]:
    snaps = await client.get_snapshots(project_id)
    if snapshot_id:
        for s in snaps:
            if isinstance(s, dict) and s.get("snapshot_id") == snapshot_id:
                return s
        raise ResolveMissing(f"snapshot_id not found: {snapshot_id}")
    name = _norm(snapshot_name)
    if not name:
        raise ValueError("snapshot_id or snapshot_name is required")
    matches = [s for s in snaps if isinstance(s, dict) and s.get("name") == name]
    if not matches:
        raise ResolveMissing(f"snapshot not found: {name}")
    if len(matches) > 1:
        raise ResolveAmbiguous(
            f"multiple snapshots named {name!r}",
            candidates=[
                {"snapshot_id": s.get("snapshot_id"), "name": s.get("name")}
                for s in matches
            ],
        )
    return matches[0]


def image_filename(entry: Dict[str, Any]) -> str:
    return _norm(
        entry.get("filename")
        or entry.get("path")
        or entry.get("name")
        or ""
    )


async def find_image(
    client: GNS3APIClient,
    *,
    compute_id: str,
    emulator: str,
    filename: str,
) -> Optional[Dict[str, Any]]:
    want = _norm(filename)
    images = await client.list_images(compute_id=compute_id, emulator=emulator)
    for img in images if isinstance(images, list) else []:
        if isinstance(img, dict) and image_filename(img) == want:
            return img
        if isinstance(img, str) and _norm(img) == want:
            return {"filename": img}
    return None


def node_template_id(node: Dict[str, Any]) -> Optional[str]:
    props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    return node.get("template_id") or props.get("template_id")


def check_node_template_conflict(
    existing: Dict[str, Any],
    *,
    expected_template_id: Optional[str],
    expected_template_name: Optional[str] = None,
) -> None:
    """Raise ResolveConflict if existing node disagrees with expected template."""
    if not expected_template_id and not expected_template_name:
        return
    have_id = node_template_id(existing)
    if expected_template_id and have_id and have_id != expected_template_id:
        raise ResolveConflict(
            f"node {existing.get('name')!r} exists with different template",
            existing={
                "node_id": existing.get("node_id"),
                "name": existing.get("name"),
                "template_id": have_id,
            },
            expected={
                "name": existing.get("name"),
                "template_id": expected_template_id,
                "template_name": expected_template_name,
            },
        )
