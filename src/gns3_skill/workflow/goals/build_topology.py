"""gns3_build_topology goal implementation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from gns3_skill.gns3_client import GNS3APIClient, GNS3Config
from gns3_skill.server_lifecycle import ensure_gns3_server, normalize_server_url
from gns3_skill.workflow.envelopes import (
    STATUS_SUCCESS,
    STEP_CHANGED,
    STEP_FAILED,
    STEP_SKIPPED,
    STEP_SUCCESS,
    conflict_envelope,
    error_envelope,
    goal_envelope,
    step_entry,
)
from gns3_skill.workflow.resolve import (
    ResolveAmbiguous,
    ResolveConflict,
    ResolveMissing,
    check_node_template_conflict,
    link_endpoint_key,
    resolve_project,
    resolve_template,
)
from gns3_skill.workflow.runner import Step, run_steps
from gns3_skill.workflow.topology import node_port_keys, validate_topology_snapshot


def _endpoint_spec(end: Dict[str, Any]) -> Tuple[str, Optional[int], Optional[int]]:
    name = (end.get("node_name") or end.get("name") or "").strip()
    adapter = end.get("adapter")
    port = end.get("port")
    if (adapter is None) != (port is None):
        raise ValueError("link endpoint adapter and port must be provided together")
    return (
        name,
        int(adapter) if adapter is not None else None,
        int(port) if port is not None else None,
    )


async def build_topology_goal(
    *,
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    nodes: Optional[List[Dict[str, Any]]] = None,
    links: Optional[List[Dict[str, Any]]] = None,
    start: bool = False,
    validate: bool = True,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    goal = "build_topology"
    url = normalize_server_url(server_url)
    nodes = nodes or []
    links = links or []
    node_names = [(spec.get("name") or "").strip() for spec in nodes]
    if any(not name for name in node_names):
        return error_envelope(goal, "node entry missing name")
    if len(set(node_names)) != len(node_names):
        return error_envelope(goal, "node names must be unique within one request")
    for spec in links:
        endpoint_a = spec.get("a") or spec.get("node_a") or {}
        endpoint_b = spec.get("b") or spec.get("node_b") or {}
        if not isinstance(endpoint_a, dict):
            endpoint_a = {"node_name": endpoint_a}
        if not isinstance(endpoint_b, dict):
            endpoint_b = {"node_name": endpoint_b}
        try:
            name_a, _, _ = _endpoint_spec(endpoint_a)
            name_b, _, _ = _endpoint_spec(endpoint_b)
        except (TypeError, ValueError) as exc:
            return error_envelope(goal, str(exc))
        if not name_a or not name_b:
            return error_envelope(goal, "link endpoints require node_name")
    ctx: Dict[str, Any] = {
        "client": None,
        "project": None,
        "node_by_name": {},
        "created_nodes": [],
        "skipped_nodes": [],
        "created_links": [],
        "skipped_links": [],
        "validation": None,
    }

    async def ensure_step() -> Dict[str, Any]:
        config = GNS3Config.from_env(
            server_url=url, username=username, password=password
        )
        result = await ensure_gns3_server(
            config.server_url,
            username=config.username,
            password=config.password,
        )
        if result.get("status") != "success":
            return step_entry(
                "ensure_server",
                STEP_FAILED,
                detail=result,
                error=result.get("error") or "GNS3 server not available",
            )
        ctx["client"] = GNS3APIClient(config)
        return step_entry("ensure_server", STEP_SUCCESS, detail={"server_url": url})

    async def resolve_project_step() -> Dict[str, Any]:
        client: GNS3APIClient = ctx["client"]
        try:
            project = await resolve_project(
                client, project_id=project_id, project_name=project_name
            )
        except ResolveMissing as exc:
            return step_entry("resolve_project", STEP_FAILED, error=str(exc))
        except ResolveAmbiguous as exc:
            return step_entry(
                "resolve_project",
                STEP_FAILED,
                error=str(exc),
                detail={"candidates": exc.candidates},
            )
        ctx["project"] = project
        opened = project.get("status") != "opened"
        if opened:
            await client.open_project(project["project_id"])
        return step_entry(
            "resolve_project",
            STEP_CHANGED if opened else STEP_SUCCESS,
            detail={"project_id": project.get("project_id"), "name": project.get("name")},
        )

    async def converge_nodes_step() -> Dict[str, Any]:
        client: GNS3APIClient = ctx["client"]
        project_id = ctx["project"]["project_id"]
        existing = await client.get_project_nodes(project_id)
        by_name = {
            node.get("name"): node
            for node in existing
            if isinstance(node, dict) and node.get("name")
        }
        create_plan: List[Tuple[Dict[str, Any], str, str]] = []

        for spec in nodes:
            name = (spec.get("name") or "").strip()
            template_id = spec.get("template_id")
            template_name = spec.get("template_name")
            try:
                if not template_id and template_name:
                    template = await resolve_template(
                        client, template_name=template_name
                    )
                    template_id = template.get("template_id")
                elif template_id:
                    await resolve_template(client, template_id=template_id)
            except (ResolveMissing, ResolveAmbiguous, ValueError) as exc:
                return step_entry(
                    "converge_nodes",
                    STEP_FAILED,
                    error=f"template resolve failed for node {name}: {exc}",
                )

            if name in by_name:
                node = by_name[name]
                try:
                    check_node_template_conflict(
                        node,
                        expected_template_id=template_id,
                        expected_template_name=template_name,
                    )
                except ResolveConflict as exc:
                    return step_entry(
                        "converge_nodes",
                        STEP_FAILED,
                        error=str(exc),
                        detail={
                            "existing": exc.existing,
                            "expected": exc.expected,
                        },
                    )
                ctx["skipped_nodes"].append(
                    {"name": name, "node_id": node.get("node_id")}
                )
                ctx["node_by_name"][name] = node
                continue
            if not template_id:
                return step_entry(
                    "converge_nodes",
                    STEP_FAILED,
                    error=f"template_id or template_name is required for node {name}",
                )
            create_plan.append((spec, name, template_id))

        for spec, name, template_id in create_plan:
            try:
                created = await client.create_node_from_template(
                    project_id,
                    template_id,
                    x=int(spec.get("x") or 0),
                    y=int(spec.get("y") or 0),
                    name=name,
                    compute_id=spec.get("compute_id"),
                )
            except Exception as exc:
                return step_entry(
                    "converge_nodes",
                    STEP_FAILED,
                    error=f"failed to create node {name}: {exc}",
                    mutated=bool(ctx["created_nodes"]),
                )
            by_name[name] = created
            ctx["node_by_name"][name] = created
            ctx["created_nodes"].append(
                {
                    "name": name,
                    "node_id": created.get("node_id"),
                    "template_id": template_id,
                }
            )

        for node in by_name.values():
            if node.get("name"):
                ctx["node_by_name"][node["name"]] = node
        status = STEP_CHANGED if ctx["created_nodes"] else STEP_SUCCESS
        if not nodes:
            status = STEP_SKIPPED
        return step_entry(
            "converge_nodes",
            status,
            detail={
                "created": ctx["created_nodes"],
                "skipped": ctx["skipped_nodes"],
            },
        )

    async def converge_links_step() -> Dict[str, Any]:
        if not links:
            return step_entry(
                "converge_links", STEP_SKIPPED, detail={"reason": "no links"}
            )
        client: GNS3APIClient = ctx["client"]
        pid = ctx["project"]["project_id"]
        all_nodes = await client.get_project_nodes(pid)
        name_to_node = {
            node.get("name"): node
            for node in all_nodes
            if isinstance(node, dict) and node.get("name")
        }
        ctx["node_by_name"].update(name_to_node)

        used_ports: Dict[str, set] = {
            str(node.get("node_id")): set()
            for node in all_nodes
            if node.get("node_id")
        }
        existing_links = [
            link
            for link in await client.get_project_links(pid)
            if isinstance(link, dict)
        ]
        for link in existing_links:
            for end in link.get("nodes") or []:
                node_id = str(end.get("node_id") or "")
                if node_id in used_ports:
                    used_ports[node_id].add(
                        (
                            int(end.get("adapter_number", 0)),
                            int(end.get("port_number", 0)),
                        )
                    )

        def link_sort_key(link: Dict[str, Any]) -> Tuple[Any, ...]:
            endpoints = sorted(
                (
                    str(end.get("node_id") or ""),
                    int(end.get("adapter_number", 0)),
                    int(end.get("port_number", 0)),
                )
                for end in (link.get("nodes") or [])[:2]
            )
            return (*endpoints, str(link.get("link_id") or ""))

        existing_links.sort(key=link_sort_key)
        claimed_links = set()

        def link_end_for_node(
            link: Dict[str, Any], node_id: str
        ) -> Optional[Dict[str, Any]]:
            for end in link.get("nodes") or []:
                if str(end.get("node_id")) == node_id:
                    return end
            return None

        def matching_existing_link(
            a_node: Dict[str, Any],
            b_node: Dict[str, Any],
            requested_a: Optional[Tuple[int, int]],
            requested_b: Optional[Tuple[int, int]],
        ) -> Optional[Dict[str, Any]]:
            a_id = str(a_node["node_id"])
            b_id = str(b_node["node_id"])
            for index, link in enumerate(existing_links):
                if index in claimed_links:
                    continue
                a_end = link_end_for_node(link, a_id)
                b_end = link_end_for_node(link, b_id)
                if a_end is None or b_end is None:
                    continue
                actual_a = (
                    int(a_end.get("adapter_number", 0)),
                    int(a_end.get("port_number", 0)),
                )
                actual_b = (
                    int(b_end.get("adapter_number", 0)),
                    int(b_end.get("port_number", 0)),
                )
                if requested_a is not None and requested_a != actual_a:
                    continue
                if requested_b is not None and requested_b != actual_b:
                    continue
                claimed_links.add(index)
                return link
            return None

        def choose_port(
            node: Dict[str, Any], requested: Optional[Tuple[int, int]]
        ) -> Tuple[int, int]:
            node_id = str(node.get("node_id") or "")
            used = used_ports.setdefault(node_id, set())
            advertised = node_port_keys(node)
            if requested is not None:
                if advertised and requested not in advertised:
                    raise ValueError(
                        f"port {requested[0]}/{requested[1]} is not advertised "
                        f"by node {node.get('name')}"
                    )
                if requested in used:
                    raise ValueError(
                        f"port {requested[0]}/{requested[1]} is already in use "
                        f"on node {node.get('name')}"
                    )
                used.add(requested)
                return requested
            if not advertised:
                raise ValueError(
                    f"node {node.get('name')} has no advertised ports; "
                    "specify a valid adapter and port"
                )
            for candidate in advertised:
                if candidate not in used:
                    used.add(candidate)
                    return candidate
            raise ValueError(f"no free advertised ports on node {node.get('name')}")

        link_plan: List[
            Tuple[Dict[str, Any], str, Tuple[int, int], str, Tuple[int, int]]
        ] = []
        for spec in links:
            endpoint_a = spec.get("a") or spec.get("node_a") or {}
            endpoint_b = spec.get("b") or spec.get("node_b") or {}
            if not isinstance(endpoint_a, dict):
                endpoint_a = {"node_name": endpoint_a}
            if not isinstance(endpoint_b, dict):
                endpoint_b = {"node_name": endpoint_b}
            name_a, adapter_a, port_a = _endpoint_spec(endpoint_a)
            name_b, adapter_b, port_b = _endpoint_spec(endpoint_b)
            node_a = name_to_node.get(name_a) or ctx["node_by_name"].get(name_a)
            node_b = name_to_node.get(name_b) or ctx["node_by_name"].get(name_b)
            if not node_a or not node_b:
                return step_entry(
                    "converge_links",
                    STEP_FAILED,
                    error=f"link endpoint node missing: {name_a!r} or {name_b!r}",
                )
            requested_a = (
                None if adapter_a is None else (int(adapter_a), int(port_a))
            )
            requested_b = (
                None if adapter_b is None else (int(adapter_b), int(port_b))
            )
            existing = matching_existing_link(
                node_a, node_b, requested_a, requested_b
            )
            if existing is not None:
                existing_a = (
                    link_end_for_node(existing, str(node_a["node_id"])) or {}
                )
                existing_b = (
                    link_end_for_node(existing, str(node_b["node_id"])) or {}
                )
                ctx["skipped_links"].append(
                    {
                        "link_id": existing.get("link_id"),
                        "a": link_endpoint_key(
                            name_a,
                            int(existing_a.get("adapter_number", 0)),
                            int(existing_a.get("port_number", 0)),
                        ),
                        "b": link_endpoint_key(
                            name_b,
                            int(existing_b.get("adapter_number", 0)),
                            int(existing_b.get("port_number", 0)),
                        ),
                    }
                )
                continue
            try:
                selected_a = choose_port(node_a, requested_a)
                selected_b = choose_port(node_b, requested_b)
            except ValueError as exc:
                return step_entry("converge_links", STEP_FAILED, error=str(exc))
            link_plan.append(
                (
                    {
                        "nodes": [
                            {
                                "node_id": node_a["node_id"],
                                "adapter_number": selected_a[0],
                                "port_number": selected_a[1],
                            },
                            {
                                "node_id": node_b["node_id"],
                                "adapter_number": selected_b[0],
                                "port_number": selected_b[1],
                            },
                        ]
                    },
                    name_a,
                    selected_a,
                    name_b,
                    selected_b,
                )
            )

        for link_data, name_a, selected_a, name_b, selected_b in link_plan:
            try:
                created = await client.create_link(pid, link_data)
            except Exception as exc:
                return step_entry(
                    "converge_links",
                    STEP_FAILED,
                    error=f"failed to create link: {exc}",
                    mutated=bool(ctx["created_links"]),
                )
            ctx["created_links"].append(
                {
                    "link_id": created.get("link_id")
                    if isinstance(created, dict)
                    else None,
                    "a": link_endpoint_key(name_a, *selected_a),
                    "b": link_endpoint_key(name_b, *selected_b),
                }
            )

        return step_entry(
            "converge_links",
            STEP_CHANGED if ctx["created_links"] else STEP_SUCCESS,
            detail={
                "created": ctx["created_links"],
                "skipped": ctx["skipped_links"],
            },
        )

    async def start_step() -> Dict[str, Any]:
        if not start:
            return step_entry(
                "start_nodes", STEP_SKIPPED, detail={"reason": "start=false"}
            )
        client: GNS3APIClient = ctx["client"]
        pid = ctx["project"]["project_id"]
        all_nodes = await client.get_project_nodes(pid)
        started, failed = [], []
        for node in all_nodes:
            if node.get("status") == "started":
                continue
            try:
                await client.start_node(pid, node["node_id"])
                started.append(node.get("name"))
            except Exception as exc:
                failed.append({"name": node.get("name"), "error": str(exc)})
        if failed:
            return step_entry(
                "start_nodes",
                STEP_FAILED,
                error="some nodes failed to start" if started else "all start attempts failed",
                detail={"started": started, "failed": failed},
                mutated=bool(started),
            )
        return step_entry(
            "start_nodes",
            STEP_CHANGED if started else STEP_SUCCESS,
            detail={"started": started},
        )

    async def validate_step() -> Dict[str, Any]:
        if not validate:
            return step_entry(
                "validate", STEP_SKIPPED, detail={"reason": "validate=false"}
            )
        client: GNS3APIClient = ctx["client"]
        pid = ctx["project"]["project_id"]
        all_nodes = await client.get_project_nodes(pid)
        all_links = await client.get_project_links(pid)
        ctx["validation"] = validate_topology_snapshot(all_nodes, all_links)
        return step_entry("validate", STEP_SUCCESS, detail=ctx["validation"])

    if not project_id and not (project_name and str(project_name).strip()):
        return error_envelope(goal, "project_id or project_name is required")

    result = await run_steps(
        [
            Step("ensure_server", ensure_step),
            Step("resolve_project", resolve_project_step),
            Step("converge_nodes", converge_nodes_step),
            Step("converge_links", converge_links_step),
            Step("start_nodes", start_step),
            Step("validate", validate_step),
        ]
    )

    for step in result.steps:
        if step.get("status") == STEP_FAILED and step.get("detail", {}).get(
            "existing"
        ):
            return conflict_envelope(
                goal,
                result.steps,
                existing=step["detail"]["existing"],
                expected=step["detail"]["expected"],
                message=step.get("error") or "resource conflict",
            )

    project = ctx.get("project") or {}
    return goal_envelope(
        goal,
        result.status if result.status != STATUS_SUCCESS else STATUS_SUCCESS,
        result.steps,
        result={
            "project_id": project.get("project_id"),
            "created_nodes": ctx["created_nodes"],
            "skipped_nodes": ctx["skipped_nodes"],
            "created_links": ctx["created_links"],
            "skipped_links": ctx["skipped_links"],
            "validation": ctx["validation"],
        },
        error=result.error,
        next_hint=None
        if result.status == STATUS_SUCCESS
        else "Inspect steps and retry safely",
    )
