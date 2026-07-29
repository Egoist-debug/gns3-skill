#!/usr/bin/env python3
"""GNS3 skill tool surface — async functions for CLI dispatch.

58 tools (8 goal + 50 expert). Invoke via ``gns3_skill.cli`` / ``scripts/gns3``.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from .gns3_client import GNS3APIClient, GNS3Config
from .config_templates import ConfigTemplates, TopologyTemplates
from .console_core import send_console_commands_impl as _send_console_commands_impl
from .server_lifecycle import ensure_gns3_server, normalize_server_url, stop_gns3_server
from . import ssh_client as ssh_helpers
from .workflow.topology import validate_topology_snapshot

# Default WARNING unless GNS3_DEBUG is set.
_log_level = logging.DEBUG if os.environ.get("GNS3_DEBUG") else logging.WARNING
logging.basicConfig(level=_log_level, stream=sys.stderr)
logger = logging.getLogger(__name__)


# ==================== HELPER FUNCTIONS ====================

def create_client(
    server_url: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> GNS3APIClient:
    """Create a GNS3 API client.

    Explicit args win; otherwise fall back to GNS3_* env, then to the local
    ``gns3_server.conf`` ``[Server]`` section (for local GNS3 installs with
    auth enabled). See GNS3Config.from_env resolution order.
    """
    config = GNS3Config.from_env(
        server_url=server_url,
        username=username,
        password=password,
    )
    return GNS3APIClient(config)

async def create_client_ready(
    server_url: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> GNS3APIClient:
    """Ensure GNS3 is reachable (auto-start if local) then create API client."""
    config = GNS3Config.from_env(
        server_url=server_url,
        username=username,
        password=password,
    )
    result = await ensure_gns3_server(
        config.server_url,
        username=config.username,
        password=config.password,
    )
    if result.get("status") != "success":
        raise Exception(result.get("error") or f"GNS3 server not available at {config.server_url}")
    return GNS3APIClient(config)


async def get_node_by_name(client: GNS3APIClient, project_id: str, node_name: str) -> Optional[Dict[str, Any]]:
    """Find a node by name in a project."""
    nodes = await client.get_project_nodes(project_id)
    for node in nodes:
        if node.get("name") == node_name:
            return node
    return None


# ==================== SERVER & COMPUTE TOOLS ====================
async def gns3_ensure_server(
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Probe the GNS3 server and auto-start it when the target is localhost.

    Remote server URLs are probed only (never auto-started).
    When started by this tool, the process is left running after the CLI exits.

    Args:
        server_url: GNS3 REST base URL
        username: Optional GNS3 API username
        password: Optional GNS3 API password
        force: Bypass healthy cache and re-probe
    """
    try:
        return await ensure_gns3_server(
            server_url,
            username=username,
            password=password,
            force=force,
        )
    except Exception as e:
        logger.error(f"Failed to ensure GNS3 server: {e}")
        return {
            "status": "error",
            "already_running": False,
            "started": False,
            "server_url": normalize_server_url(server_url),
            "server_info": None,
            "start_command": None,
            "wait_seconds": 0,
            "error": str(e),
        }

async def gns3_stop_server(
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Stop a **localhost** GNS3 server by terminating the process listening on
    the URL port (SIGTERM, then SIGKILL after timeout).

    Remote server URLs are refused (never killed over the network).
    Clears the healthy cache so a later ``gns3_ensure_server`` re-probes.

    Args:
        server_url: GNS3 REST base URL (port used for PID discovery)
        username: Unused; kept for signature consistency with other tools
        password: Unused; kept for signature consistency with other tools
    """
    del username, password  # API auth not used for local process stop
    try:
        return await stop_gns3_server(server_url)
    except Exception as e:
        logger.error(f"Failed to stop GNS3 server: {e}")
        return {
            "status": "error",
            "server_url": normalize_server_url(server_url),
            "stopped": False,
            "already_stopped": False,
            "pids": [],
            "signal_steps": [],
            "wait_seconds": 0,
            "error": str(e),
        }


async def gns3_cleanup_session(
    project_id: Optional[str] = None,
    stop_nodes: bool = False,
    close_project: bool = False,
    stop_server: bool = False,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Optional multi-step lab session cleanup (defaults all false — safe/inert).

    Fixed order when flags are true:
      1. stop_nodes  — stop all nodes in project_id
      2. close_project — close project (stops nodes)
      3. stop_server — stop localhost gns3server on server_url port

    Missing project_id with stop_nodes/close_project true → that step skipped.
    A step failure is recorded and later steps still run.
    Does **not** delete projects. Prefer asking the user before enabling flags.

    Args:
        project_id: Project for node/project steps (optional if only stop_server)
        stop_nodes: Stop all nodes in the project
        close_project: Close the project
        stop_server: Stop the local GNS3 server process
        server_url: GNS3 REST base URL
        username: Optional GNS3 API username
        password: Optional GNS3 API password
    """
    url = normalize_server_url(server_url)
    steps: List[Dict[str, Any]] = []

    def _append(step: str, status: str, **detail: Any) -> None:
        entry: Dict[str, Any] = {"step": step, "status": status}
        entry.update(detail)
        steps.append(entry)

    # --- stop_nodes ---
    if not stop_nodes:
        _append("stop_nodes", "skipped", reason="stop_nodes=false")
    elif not project_id:
        _append("stop_nodes", "skipped", reason="project_id required")
    else:
        try:
            client = await create_client_ready(server_url, username, password)
            nodes = await client.get_project_nodes(project_id)
            stopped: List[Dict[str, Any]] = []
            failed: List[Dict[str, Any]] = []
            for node in nodes:
                nid = node.get("node_id")
                name = node.get("name")
                try:
                    await client.stop_node(project_id, nid)
                    stopped.append({"node_id": nid, "name": name})
                except Exception as e:
                    failed.append({"node_id": nid, "name": name, "error": str(e)})
            if failed and not stopped:
                _append(
                    "stop_nodes",
                    "error",
                    stopped_nodes=stopped,
                    failed_nodes=failed,
                    error="all node stop attempts failed",
                )
            elif failed:
                _append(
                    "stop_nodes",
                    "error",
                    stopped_nodes=stopped,
                    failed_nodes=failed,
                    error="some nodes failed to stop",
                )
            else:
                _append(
                    "stop_nodes",
                    "success",
                    stopped_nodes=stopped,
                    failed_nodes=failed,
                    total=len(nodes),
                )
        except Exception as e:
            logger.error(f"cleanup stop_nodes failed: {e}")
            _append("stop_nodes", "error", error=str(e))

    # --- close_project ---
    if not close_project:
        _append("close_project", "skipped", reason="close_project=false")
    elif not project_id:
        _append("close_project", "skipped", reason="project_id required")
    else:
        try:
            client = await create_client_ready(server_url, username, password)
            closed = await client.close_project(project_id)
            _append("close_project", "success", project=closed)
        except Exception as e:
            logger.error(f"cleanup close_project failed: {e}")
            _append("close_project", "error", error=str(e))

    # --- stop_server (never via create_client_ready — would re-start) ---
    if not stop_server:
        _append("stop_server", "skipped", reason="stop_server=false")
    else:
        try:
            stop_result = await stop_gns3_server(server_url)
            step_status = "success" if stop_result.get("status") == "success" else "error"
            _append("stop_server", step_status, result=stop_result)
            if step_status == "error" and stop_result.get("error"):
                steps[-1]["error"] = stop_result["error"]
        except Exception as e:
            logger.error(f"cleanup stop_server failed: {e}")
            _append("stop_server", "error", error=str(e))

    requested = []
    if stop_nodes:
        requested.append("stop_nodes")
    if close_project:
        requested.append("close_project")
    if stop_server:
        requested.append("stop_server")

    # Overall status from steps that were not skipped
    active = [s for s in steps if s.get("status") != "skipped"]
    if not requested:
        overall = "success"
    elif not active:
        # all requested became skipped (e.g. missing project_id only)
        overall = "success"
    else:
        errors = [s for s in active if s.get("status") == "error"]
        successes = [s for s in active if s.get("status") == "success"]
        if errors and successes:
            overall = "partial"
        elif errors and not successes:
            overall = "error"
        else:
            overall = "success"

    return {
        "status": overall,
        "server_url": url,
        "project_id": project_id,
        "steps": steps,
    }



async def gns3_get_server_info(
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get GNS3 server version and information.
    Returns server version, supported features, and system information.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        info = await client.get_server_info()
        return {"status": "success", "server_info": info}
    except Exception as e:
        logger.error(f"Failed to get server info: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_list_computes(
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    List all available compute servers (local, VMs, remote).
    Shows compute ID, name, protocol, host, port, and status.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        computes = await client.get_compute_list()
        return {"status": "success", "computes": computes, "total": len(computes)}
    except Exception as e:
        logger.error(f"Failed to list computes: {e}")
        return {"status": "error", "error": str(e)}


# ==================== PROJECT MANAGEMENT TOOLS ====================

async def gns3_list_projects(
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    List all projects on the GNS3 server with detailed status.
    Shows project name, ID, node/link counts, and status.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        projects = await client.get_projects()
        
        projects_summary = []
        for project in projects:
            projects_summary.append({
                "name": project.get("name", "Unnamed"),
                "project_id": project.get("project_id", ""),
                "status": project.get("status", "unknown"),
                "path": project.get("path", ""),
                "filename": project.get("filename", ""),
                "auto_close": project.get("auto_close", False),
                "auto_open": project.get("auto_open", False),
                "auto_start": project.get("auto_start", False),
            })
        
        return {
            "status": "success",
            "projects": projects_summary,
            "total_projects": len(projects_summary)
        }
    except Exception as e:
        logger.error(f"Failed to list projects: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_create_project(
    name: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    auto_close: bool = False,
    auto_open: bool = False,
    auto_start: bool = False,
    path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new GNS3 project.
    
    Args:
        name: Project name
        auto_close: Automatically close when server stops
        auto_open: Automatically open when server starts
        auto_start: Automatically start all nodes when opened
        path: Custom path for project files
    """
    try:
        client = await create_client_ready(server_url, username, password)
        project = await client.create_project(name, auto_close, auto_open, auto_start, path)
        return {"status": "success", "project": project}
    except Exception as e:
        logger.error(f"Failed to create project: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_get_project(
    project_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get detailed information about a specific project.
    Returns complete project configuration and statistics.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        project = await client.get_project(project_id)
        return {"status": "success", "project": project}
    except Exception as e:
        logger.error(f"Failed to get project: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_update_project(
    project_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    name: Optional[str] = None,
    auto_close: Optional[bool] = None,
    auto_open: Optional[bool] = None,
    auto_start: Optional[bool] = None
) -> Dict[str, Any]:
    """
    Update project settings.
    Only specified parameters will be updated.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        update_data = {}
        if name is not None:
            update_data["name"] = name
        if auto_close is not None:
            update_data["auto_close"] = auto_close
        if auto_open is not None:
            update_data["auto_open"] = auto_open
        if auto_start is not None:
            update_data["auto_start"] = auto_start
        
        project = await client.update_project(project_id, **update_data)
        return {"status": "success", "project": project}
    except Exception as e:
        logger.error(f"Failed to update project: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_open_project(
    project_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """Open an existing GNS3 project for editing."""
    try:
        client = await create_client_ready(server_url, username, password)
        opened_project = await client.open_project(project_id)
        return {"status": "success", "project": opened_project}
    except Exception as e:
        logger.error(f"Failed to open project: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_close_project(
    project_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """Close an open project. All nodes will be stopped."""
    try:
        client = await create_client_ready(server_url, username, password)
        closed_project = await client.close_project(project_id)
        return {"status": "success", "project": closed_project, "message": "Project closed successfully"}
    except Exception as e:
        logger.error(f"Failed to close project: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_delete_project(
    project_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    Permanently delete a project and all its files.
    WARNING: This action cannot be undone!
    """
    try:
        client = await create_client_ready(server_url, username, password)
        await client.delete_project(project_id)
        return {"status": "success", "message": f"Project {project_id} deleted permanently"}
    except Exception as e:
        logger.error(f"Failed to delete project: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_duplicate_project(
    project_id: str,
    new_name: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Duplicate an existing project with a new name.
    Creates an exact copy of the project including all nodes and configurations.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        duplicated = await client.duplicate_project(project_id, new_name, path)
        return {"status": "success", "project": duplicated, "message": "Project duplicated successfully"}
    except Exception as e:
        logger.error(f"Failed to duplicate project: {e}")
        return {"status": "error", "error": str(e)}


# ==================== NODE MANAGEMENT TOOLS ====================

async def gns3_list_nodes(
    project_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    List all nodes (devices) in a project.
    Shows node name, type, status, console port, and position.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        nodes = await client.get_project_nodes(project_id)
        
        nodes_summary = []
        for node in nodes:
            nodes_summary.append({
                "name": node.get("name"),
                "node_id": node.get("node_id"),
                "node_type": node.get("node_type"),
                "status": node.get("status"),
                "console": node.get("console"),
                "console_type": node.get("console_type"),
                "console_host": node.get("console_host"),
                "x": node.get("x"),
                "y": node.get("y"),
                "ports": len(node.get("ports", []))
            })
        
        return {"status": "success", "nodes": nodes_summary, "total_nodes": len(nodes_summary)}
    except Exception as e:
        logger.error(f"Failed to list nodes: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_add_node(
    project_id: str,
    node_name: str,
    template_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    x: int = 0,
    y: int = 0,
    compute_id: str = "local"
) -> Dict[str, Any]:
    """
    Add a network device/node to a project using a template.
    
    Args:
        project_id: ID of the project
        node_name: Name for the new node
        template_id: Template ID (use gns3_list_templates to get available templates)
        x, y: Position coordinates on the canvas
        compute_id: Compute server ID (default: "local")
    """
    try:
        client = await create_client_ready(server_url, username, password)
        node = await client.create_node_from_template(
            project_id=project_id,
            template_id=template_id,
            x=x,
            y=y,
            compute_id=compute_id,
            name=node_name
        )
        return {"status": "success", "node": node}
    except Exception as e:
        logger.error(f"Failed to add node: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_get_node(
    project_id: str,
    node_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get detailed information about a specific node.
    Returns complete node configuration including ports and properties.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        node = await client.get_node(project_id, node_id)
        return {"status": "success", "node": node}
    except Exception as e:
        logger.error(f"Failed to get node: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_update_node(
    project_id: str,
    node_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    name: Optional[str] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
    properties: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Update node settings and properties.
    
    Args:
        name: New node name
        x, y: New position coordinates
        properties: Device-specific properties (RAM, CPU, interfaces, etc.)
    """
    try:
        client = await create_client_ready(server_url, username, password)
        update_data = {}
        if name is not None:
            update_data["name"] = name
        if x is not None:
            update_data["x"] = x
        if y is not None:
            update_data["y"] = y
        if properties is not None:
            update_data["properties"] = properties
        
        node = await client.update_node(project_id, node_id, update_data)
        return {"status": "success", "node": node}
    except Exception as e:
        logger.error(f"Failed to update node: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_delete_node(
    project_id: str,
    node_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    Delete a node from the project.
    All links connected to this node will also be deleted.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        await client.delete_node(project_id, node_id)
        return {"status": "success", "message": f"Node {node_id} deleted successfully"}
    except Exception as e:
        logger.error(f"Failed to delete node: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_start_node(
    project_id: str,
    node_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """Start a specific node."""
    try:
        client = await create_client_ready(server_url, username, password)
        node = await client.start_node(project_id, node_id)
        return {"status": "success", "node": node, "message": "Node started"}
    except Exception as e:
        logger.error(f"Failed to start node: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_stop_node(
    project_id: str,
    node_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """Stop a specific node."""
    try:
        client = await create_client_ready(server_url, username, password)
        node = await client.stop_node(project_id, node_id)
        return {"status": "success", "node": node, "message": "Node stopped"}
    except Exception as e:
        logger.error(f"Failed to stop node: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_suspend_node(
    project_id: str,
    node_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """Suspend a node (pause execution, save state)."""
    try:
        client = await create_client_ready(server_url, username, password)
        node = await client.suspend_node(project_id, node_id)
        return {"status": "success", "node": node, "message": "Node suspended"}
    except Exception as e:
        logger.error(f"Failed to suspend node: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_reload_node(
    project_id: str,
    node_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """Reload a node (restart without stopping)."""
    try:
        client = await create_client_ready(server_url, username, password)
        node = await client.reload_node(project_id, node_id)
        return {"status": "success", "node": node, "message": "Node reloaded"}
    except Exception as e:
        logger.error(f"Failed to reload node: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_duplicate_node(
    project_id: str,
    node_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    x: int = 50,
    y: int = 50
) -> Dict[str, Any]:
    """
    Duplicate a node with the same configuration.
    The duplicate will be placed at the specified offset from the original.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        node = await client.duplicate_node(project_id, node_id, x, y)
        return {"status": "success", "node": node, "message": "Node duplicated"}
    except Exception as e:
        logger.error(f"Failed to duplicate node: {e}")
        return {"status": "error", "error": str(e)}


# ==================== BULK NODE OPERATIONS ====================

async def gns3_start_all_nodes(
    project_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """Start all nodes in a project."""
    try:
        client = await create_client_ready(server_url, username, password)
        nodes = await client.get_project_nodes(project_id)
        
        started = []
        failed = []
        
        for node in nodes:
            try:
                await client.start_node(project_id, node["node_id"])
                started.append({"node_id": node["node_id"], "name": node["name"]})
            except Exception as e:
                failed.append({"node_id": node["node_id"], "name": node["name"], "error": str(e)})
        
        return {
            "status": "success",
            "started_nodes": started,
            "failed_nodes": failed,
            "total": len(nodes),
            "successful": len(started)
        }
    except Exception as e:
        logger.error(f"Failed to start all nodes: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_stop_all_nodes(
    project_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """Stop all nodes in a project."""
    try:
        client = await create_client_ready(server_url, username, password)
        nodes = await client.get_project_nodes(project_id)
        
        stopped = []
        failed = []
        
        for node in nodes:
            try:
                await client.stop_node(project_id, node["node_id"])
                stopped.append({"node_id": node["node_id"], "name": node["name"]})
            except Exception as e:
                failed.append({"node_id": node["node_id"], "name": node["name"], "error": str(e)})
        
        return {
            "status": "success",
            "stopped_nodes": stopped,
            "failed_nodes": failed,
            "total": len(nodes),
            "successful": len(stopped)
        }
    except Exception as e:
        logger.error(f"Failed to stop all nodes: {e}")
        return {"status": "error", "error": str(e)}


# ==================== LINK MANAGEMENT TOOLS ====================

async def gns3_list_links(
    project_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    List all links (connections) in a project.
    Shows link endpoints, ports, and status.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        links = await client.get_project_links(project_id)
        nodes = await client.get_project_nodes(project_id)
        
        # Create node lookup
        node_lookup = {n["node_id"]: n["name"] for n in nodes}
        
        links_summary = []
        for link in links:
            node_a = link["nodes"][0]
            node_b = link["nodes"][1]
            links_summary.append({
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
                "capturing": link.get("capturing", False)
            })
        
        return {"status": "success", "links": links_summary, "total_links": len(links_summary)}
    except Exception as e:
        logger.error(f"Failed to list links: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_add_link(
    project_id: str,
    node_a_id: str,
    node_b_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    adapter_a: int = 0,
    port_a: int = 0,
    adapter_b: int = 0,
    port_b: int = 0
) -> Dict[str, Any]:
    """
    Create a link between two nodes.
    
    Args:
        node_a_id, node_b_id: Node IDs to connect
        adapter_a, port_a: Adapter and port number on node A
        adapter_b, port_b: Adapter and port number on node B
    """
    try:
        client = await create_client_ready(server_url, username, password)
        link_data = {
            "nodes": [
                {
                    "node_id": node_a_id,
                    "adapter_number": adapter_a,
                    "port_number": port_a
                },
                {
                    "node_id": node_b_id,
                    "adapter_number": adapter_b,
                    "port_number": port_b
                }
            ]
        }
        link = await client.create_link(project_id, link_data)
        return {"status": "success", "link": link}
    except Exception as e:
        logger.error(f"Failed to add link: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_delete_link(
    project_id: str,
    link_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """Delete a link between nodes."""
    try:
        client = await create_client_ready(server_url, username, password)
        await client.delete_link(project_id, link_id)
        return {"status": "success", "message": f"Link {link_id} deleted successfully"}
    except Exception as e:
        logger.error(f"Failed to delete link: {e}")
        return {"status": "error", "error": str(e)}


# ==================== TOPOLOGY TOOLS ====================

async def gns3_get_topology(
    project_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get complete network topology for a project.
    Returns all nodes, links, and project information in one call.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        
        project = await client.get_project(project_id)
        nodes = await client.get_project_nodes(project_id)
        links = await client.get_project_links(project_id)
        
        return {
            "status": "success",
            "project": {
                "name": project.get("name"),
                "project_id": project.get("project_id"),
                "status": project.get("status")
            },
            "nodes": nodes,
            "links": links,
            "summary": {
                "total_nodes": len(nodes),
                "total_links": len(links),
                "running_nodes": sum(1 for n in nodes if n.get("status") == "started"),
                "stopped_nodes": sum(1 for n in nodes if n.get("status") == "stopped")
            }
        }
    except Exception as e:
        logger.error(f"Failed to get topology: {e}")
        return {"status": "error", "error": str(e)}



# ==================== CONSOLE & CONFIGURATION TOOLS ====================


async def gns3_send_console_commands(
    project_id: str,
    node_id: str,
    commands: List[str],
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    wait_for_boot: bool = True,
    boot_timeout: int = 120,
    enter_config_mode: bool = False,
    save_config: bool = False,
    enable_password: Optional[str] = None,
    login_username: Optional[str] = None,
    login_password: Optional[str] = None,
    ready_timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Send commands to a node's console via Telnet.

    Args:
        commands: List of commands to execute
        wait_for_boot: Wait for device boot before sending commands
        boot_timeout: Maximum time to wait for boot (seconds)
        enter_config_mode: Automatically enter config mode (Cisco)
        save_config: Save configuration after commands (Cisco)
        enable_password: Enable password if required
        login_username: Console login username (or GNS3_CONSOLE_USER)
        login_password: Console login password (or GNS3_CONSOLE_PASSWORD)
        ready_timeout: Post-connect login readiness budget seconds
            (default 30 / GNS3_CONSOLE_READY_TIMEOUT)
    """
    return await _send_console_commands_impl(
        project_id=project_id,
        node_id=node_id,
        commands=commands,
        server_url=server_url,
        username=username,
        password=password,
        wait_for_boot=wait_for_boot,
        boot_timeout=boot_timeout,
        enter_config_mode=enter_config_mode,
        save_config=save_config,
        enable_password=enable_password,
        login_username=login_username,
        login_password=login_password,
        ready_timeout=ready_timeout,
    )

async def gns3_get_node_config(
    project_id: str,
    node_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    config_type: str = "running"
) -> Dict[str, Any]:
    """
    Get device configuration via console.
    
    Args:
        config_type: "running" or "startup" (Cisco-style devices)
    """
    try:
        client = await create_client_ready(server_url, username, password)
        console_info = await client.get_node_console_info(project_id, node_id)
        host = console_info.get("host")
        port = console_info.get("port")
        
        if not host or not port:
            return {"status": "error", "error": "Node has no console or is not running"}
        
        telnet = TelnetClient(host, port, timeout=30.0)
        if not telnet.connect():
            return {"status": "error", "error": f"Failed to connect to console"}
        
        try:
            telnet.wait_for_boot(timeout=10)
            
            if config_type == "running":
                config = telnet.get_running_config()
            else:
                config = telnet.send_cmd("show startup-config", wait_for=["#"], wait_time=5.0)
            
            return {
                "status": "success",
                "node_name": console_info.get("name"),
                "config_type": config_type,
                "configuration": config
            }
        finally:
            telnet.close()
            
    except Exception as e:
        logger.error(f"Failed to get node config: {e}")
        return {"status": "error", "error": str(e)}


# ==================== CONFIGURATION TEMPLATE TOOLS ====================

async def gns3_apply_config_template(
    project_id: str,
    node_id: str,
    template_name: str,
    template_params: Dict[str, Any],
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    save_config: bool = True
) -> Dict[str, Any]:
    """
    Apply a pre-built configuration template to a device.

    Supported templates:
    - basic_router, interface, ospf, eigrp, bgp, static_route, default_route
    - vlan, trunk_port, access_port
    - dhcp_pool, nat_overload, ssh
    - banner, ntp, logging, snmp
    - standard_acl, extended_acl, security_hardening, qos_marking
    - vpcs_basic, vpcs_dhcp
    """
    try:
        commands: List[str] = []

        if template_name == "basic_router":
            commands = ConfigTemplates.basic_router_config(
                template_params["hostname"],
                template_params.get("domain", "local"),
            )
        elif template_name == "interface":
            commands = ConfigTemplates.interface_config(
                template_params["interface"],
                template_params["ip_address"],
                template_params["subnet_mask"],
                template_params.get("description"),
            )
        elif template_name == "ospf":
            commands = ConfigTemplates.ospf_config(
                template_params["process_id"],
                template_params["router_id"],
                template_params["networks"],
            )
        elif template_name == "eigrp":
            commands = ConfigTemplates.eigrp_config(
                template_params["as_number"],
                template_params["networks"],
                template_params.get("router_id"),
            )
        elif template_name == "bgp":
            commands = ConfigTemplates.bgp_config(
                template_params["as_number"],
                template_params["router_id"],
                template_params["neighbors"],
            )
        elif template_name == "static_route":
            commands = ConfigTemplates.static_route(
                template_params["network"],
                template_params["mask"],
                template_params["next_hop"],
                template_params.get("admin_distance"),
            )
        elif template_name == "default_route":
            commands = ConfigTemplates.default_route(template_params["next_hop"])
        elif template_name == "vlan":
            commands = ConfigTemplates.vlan_config(
                template_params["vlan_id"],
                template_params["name"],
            )
        elif template_name == "trunk_port":
            commands = ConfigTemplates.trunk_port_config(
                template_params["interface"],
                template_params.get("allowed_vlans"),
            )
        elif template_name == "access_port":
            commands = ConfigTemplates.access_port_config(
                template_params["interface"],
                template_params["vlan"],
                template_params.get("portfast", True),
                template_params.get("bpduguard", True),
            )
        elif template_name == "dhcp_pool":
            commands = ConfigTemplates.dhcp_pool_config(
                template_params["pool_name"],
                template_params["network"],
                template_params["mask"],
                template_params["default_router"],
                template_params.get("dns_servers"),
                template_params.get("excluded_addresses"),
            )
        elif template_name == "nat_overload":
            commands = ConfigTemplates.nat_overload_config(
                template_params["inside_interfaces"],
                template_params["outside_interface"],
                template_params["acl_number"],
                template_params["allowed_networks"],
            )
        elif template_name == "ssh":
            commands = ConfigTemplates.ssh_config(
                template_params["domain"],
                template_params["username"],
                template_params["password"],
                template_params.get("crypto_key_size", 1024),
                template_params.get("vty_lines", "0 4"),
            )
        elif template_name == "banner":
            commands = ConfigTemplates.banner_config(
                template_params.get("banner_type", "motd"),
                template_params["message"],
            )
        elif template_name == "ntp":
            commands = ConfigTemplates.ntp_config(template_params["ntp_servers"])
        elif template_name == "logging":
            commands = ConfigTemplates.logging_config(
                template_params["syslog_server"],
                template_params.get("trap_level", "informational"),
            )
        elif template_name == "snmp":
            commands = ConfigTemplates.snmp_config(
                template_params["community"],
                template_params.get("access", "ro"),
                template_params.get("acl"),
            )
        elif template_name == "standard_acl":
            commands = ConfigTemplates.standard_acl(
                template_params["acl_number"],
                template_params["entries"],
            )
        elif template_name == "extended_acl":
            commands = ConfigTemplates.extended_acl(
                template_params["acl_number"],
                template_params["entries"],
            )
        elif template_name == "security_hardening":
            commands = ConfigTemplates.security_hardening_basic()
        elif template_name == "qos_marking":
            commands = ConfigTemplates.qos_basic_marking(
                template_params["class_name"],
                template_params["dscp_value"],
                template_params["interfaces"],
            )
        elif template_name == "vpcs_basic":
            # VPCS uses a single command string, not config mode
            cmd = ConfigTemplates.vpcs_basic_config(
                template_params["ip_address"],
                template_params["subnet_mask"],
                template_params["gateway"],
            )
            return await _send_console_commands_impl(
                project_id=project_id,
                node_id=node_id,
                commands=[cmd],
                server_url=server_url,
                username=username,
                password=password,
                enter_config_mode=False,
                save_config=False,
            )
        elif template_name == "vpcs_dhcp":
            cmd = ConfigTemplates.vpcs_dhcp_config()
            return await _send_console_commands_impl(
                project_id=project_id,
                node_id=node_id,
                commands=[cmd],
                server_url=server_url,
                username=username,
                password=password,
                enter_config_mode=False,
                save_config=False,
            )
        else:
            return {"status": "error", "error": f"Unknown template: {template_name}"}

        result = await _send_console_commands_impl(
            project_id=project_id,
            node_id=node_id,
            commands=commands,
            server_url=server_url,
            username=username,
            password=password,
            enter_config_mode=True,
            save_config=save_config,
        )

        if result.get("status") == "success":
            result["template_applied"] = template_name
            result["commands_sent"] = commands

        return result

    except Exception as e:
        logger.error(f"Failed to apply config template: {e}")
        return {"status": "error", "error": str(e)}


# ==================== TEMPLATE & APPLIANCE TOOLS ====================

async def gns3_list_templates(
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    List all available device templates.
    Templates are used to create new nodes quickly.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        templates = await client.get_templates()
        
        templates_summary = []
        for template in templates:
            templates_summary.append({
                "name": template.get("name"),
                "template_id": template.get("template_id"),
                "template_type": template.get("template_type"),
                "category": template.get("category"),
                "builtin": template.get("builtin", False),
                "symbol": template.get("symbol")
            })
        
        return {"status": "success", "templates": templates_summary, "total": len(templates_summary)}
    except Exception as e:
        logger.error(f"Failed to list templates: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_list_appliances(
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    List all available appliances.
    Appliances are pre-configured device definitions that can be installed.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        appliances = await client.get_appliances()
        
        appliances_summary = []
        for appliance in appliances:
            appliances_summary.append({
                "name": appliance.get("name"),
                "appliance_id": appliance.get("appliance_id"),
                "category": appliance.get("category"),
                "vendor": appliance.get("vendor"),
                "product_name": appliance.get("product_name"),
                "status": appliance.get("status")
            })
        
        return {"status": "success", "appliances": appliances_summary, "total": len(appliances_summary)}
    except Exception as e:
        logger.error(f"Failed to list appliances: {e}")
        return {"status": "error", "error": str(e)}


# ==================== SNAPSHOT TOOLS ====================

async def gns3_list_snapshots(
    project_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """List all snapshots for a project."""
    try:
        client = await create_client_ready(server_url, username, password)
        snapshots = await client.get_snapshots(project_id)
        return {"status": "success", "snapshots": snapshots, "total": len(snapshots)}
    except Exception as e:
        logger.error(f"Failed to list snapshots: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_create_snapshot(
    project_id: str,
    snapshot_name: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a snapshot (backup) of a project.
    Captures current state of all nodes and configuration.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        snapshot = await client.create_snapshot(project_id, snapshot_name)
        return {"status": "success", "snapshot": snapshot}
    except Exception as e:
        logger.error(f"Failed to create snapshot: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_restore_snapshot(
    project_id: str,
    snapshot_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    Restore a project from a snapshot.
    WARNING: Current project state will be lost!
    """
    try:
        client = await create_client_ready(server_url, username, password)
        result = await client.restore_snapshot(project_id, snapshot_id)
        return {"status": "success", "result": result, "message": "Snapshot restored successfully"}
    except Exception as e:
        logger.error(f"Failed to restore snapshot: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_delete_snapshot(
    project_id: str,
    snapshot_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """Delete a snapshot permanently."""
    try:
        client = await create_client_ready(server_url, username, password)
        await client.delete_snapshot(project_id, snapshot_id)
        return {"status": "success", "message": f"Snapshot {snapshot_id} deleted"}
    except Exception as e:
        logger.error(f"Failed to delete snapshot: {e}")
        return {"status": "error", "error": str(e)}


# ==================== PACKET CAPTURE TOOLS ====================

async def gns3_start_capture(
    project_id: str,
    link_id: str,
    capture_file_name: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    data_link_type: str = "DLT_EN10MB"
) -> Dict[str, Any]:
    """
    Start packet capture on a link.
    Captured packets can be analyzed with Wireshark.
    
    Args:
        capture_file_name: Name for the capture file (without .pcap extension)
        data_link_type: Data link layer type (default: Ethernet)
    """
    try:
        client = await create_client_ready(server_url, username, password)
        result = await client.start_capture(project_id, link_id, capture_file_name, data_link_type)
        return {"status": "success", "capture": result, "message": "Packet capture started"}
    except Exception as e:
        logger.error(f"Failed to start capture: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_stop_capture(
    project_id: str,
    link_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """Stop packet capture on a link."""
    try:
        client = await create_client_ready(server_url, username, password)
        result = await client.stop_capture(project_id, link_id)
        return {"status": "success", "message": "Packet capture stopped"}
    except Exception as e:
        logger.error(f"Failed to stop capture: {e}")
        return {"status": "error", "error": str(e)}


# ==================== DRAWING & ANNOTATION TOOLS ====================

async def gns3_add_text_annotation(
    project_id: str,
    text: str,
    x: int,
    y: int,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    rotation: int = 0
) -> Dict[str, Any]:
    """
    Add text annotation to the topology.
    Useful for documenting networks and adding labels.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        drawing_data = {
            "svg": f'<text font-family="TypeWriter" font-size="10" fill="#000000">{text}</text>',
            "x": x,
            "y": y,
            "rotation": rotation
        }
        drawing = await client.create_drawing(project_id, drawing_data)
        return {"status": "success", "drawing": drawing}
    except Exception as e:
        logger.error(f"Failed to add text annotation: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_add_shape(
    project_id: str,
    shape_type: str,
    x: int,
    y: int,
    width: int,
    height: int,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    color: str = "#000000",
    fill_color: Optional[str] = None
) -> Dict[str, Any]:
    """
    Add a shape (rectangle or ellipse) to the topology.
    
    Args:
        shape_type: "rectangle" or "ellipse"
        color: Border color (hex format)
        fill_color: Fill color (hex format, optional)
    """
    try:
        client = await create_client_ready(server_url, username, password)
        
        if shape_type == "rectangle":
            svg = f'<rect width="{width}" height="{height}" stroke="{color}" fill="{fill_color or "none"}" />'
        elif shape_type == "ellipse":
            rx = width // 2
            ry = height // 2
            svg = f'<ellipse cx="{rx}" cy="{ry}" rx="{rx}" ry="{ry}" stroke="{color}" fill="{fill_color or "none"}" />'
        else:
            return {"status": "error", "error": f"Unknown shape type: {shape_type}"}
        
        drawing_data = {
            "svg": svg,
            "x": x,
            "y": y
        }
        drawing = await client.create_drawing(project_id, drawing_data)
        return {"status": "success", "drawing": drawing}
    except Exception as e:
        logger.error(f"Failed to add shape: {e}")
        return {"status": "error", "error": str(e)}


# ==================== ADVANCED & UTILITY TOOLS ====================

async def gns3_get_idle_pc_values(
    project_id: str,
    node_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    auto_compute: bool = True
) -> Dict[str, Any]:
    """
    Get idle-pc values for Dynamips routers to reduce CPU usage.
    Only works with Dynamips/IOS routers.
    
    Args:
        auto_compute: Automatically compute best idle-pc value
    """
    try:
        client = await create_client_ready(server_url, username, password)
        
        if auto_compute:
            result = await client.get_node_dynamips_auto_idlepc(project_id, node_id)
            return {"status": "success", "idlepc": result}
        else:
            proposals = await client.get_node_dynamips_idlepc_proposals(project_id, node_id)
            return {"status": "success", "idlepc_proposals": proposals}
    except Exception as e:
        logger.error(f"Failed to get idle-pc values: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_bulk_configure_nodes(
    project_id: str,
    configurations: List[Dict[str, Any]],
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    Configure multiple nodes in one operation.
    
    Args:
        configurations: List of dicts with keys:
            - node_id: Node to configure
            - commands: List of commands to send
            - save_config: Whether to save (optional, default False)
    """
    try:
        results = []
        
        for config in configurations:
            result = await _send_console_commands_impl(
                project_id=project_id,
                node_id=config["node_id"],
                commands=config["commands"],
                server_url=server_url,
                username=username,
                password=password,
                enter_config_mode=config.get("enter_config_mode", True),
                save_config=config.get("save_config", False),
            )
            results.append({
                "node_id": config["node_id"],
                "result": result
            })
        
        successful = sum(1 for r in results if r["result"]["status"] == "success")
        
        return {
            "status": "success",
            "results": results,
            "total": len(configurations),
            "successful": successful,
            "failed": len(configurations) - successful
        }
    except Exception as e:
        logger.error(f"Failed bulk configuration: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_validate_topology(
    project_id: str,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> Dict[str, Any]:
    """
    Validate network topology for common issues.
    Checks for disconnected nodes, missing links, and configuration problems.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        
        nodes = await client.get_project_nodes(project_id)
        links = await client.get_project_links(project_id)
        
        return {
            "status": "success",
            "validation": validate_topology_snapshot(nodes, links),
        }
    except Exception as e:
        logger.error(f"Failed to validate topology: {e}")
        return {"status": "error", "error": str(e)}



# ==================== PROJECT SAVE / EXPORT ====================

async def gns3_save_project(
    project_id: str,
    snapshot_name: Optional[str] = None,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Checkpoint a project. GNS3 has no discrete 'save' RPC; this fetches current
    project status and optionally creates a named snapshot.
    """
    try:
        client = await create_client_ready(server_url, username, password)
        project = await client.get_project(project_id)
        snapshot = None
        if snapshot_name:
            snapshot = await client.create_snapshot(project_id, snapshot_name)
        return {
            "status": "success",
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
    except Exception as e:
        logger.error(f"Failed to save project: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_export_project(
    project_id: str,
    output_path: str,
    include_images: bool = False,
    include_snapshots: bool = False,
    reset_mac_addresses: bool = False,
    keep_compute_ids: bool = False,
    compression: str = "zip",
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Export a project as a portable .gns3project archive to a local path.

    Args:
        output_path: Destination file path (e.g. /tmp/lab.gns3project)
        include_images: Bundle device images into the archive
        include_snapshots: Include snapshots
        compression: zip | none | bzip2 | lzma
    """
    try:
        client = await create_client_ready(server_url, username, password)
        result = await client.export_project(
            project_id=project_id,
            output_path=output_path,
            include_images=include_images,
            include_snapshots=include_snapshots,
            reset_mac_addresses=reset_mac_addresses,
            keep_compute_ids=keep_compute_ids,
            compression=compression,
        )
        return {"status": "success", "export": result}
    except Exception as e:
        logger.error(f"Failed to export project: {e}")
        return {"status": "error", "error": str(e)}


# ==================== IMAGE MANAGEMENT ====================

async def gns3_list_images(
    emulator: str = "qemu",
    compute_id: str = "local",
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """
    List device images available on a GNS3 compute for an emulator type.

    Args:
        emulator: qemu | dynamips | iou | docker
        compute_id: Compute id (default 'local')
    """
    try:
        client = await create_client_ready(server_url, username, password)
        images = await client.list_images(compute_id=compute_id, emulator=emulator)
        return {
            "status": "success",
            "compute_id": compute_id,
            "emulator": emulator,
            "images": images,
            "total": len(images) if isinstance(images, list) else 0,
        }
    except Exception as e:
        logger.error(f"Failed to list images: {e}")
        return {"status": "error", "error": str(e)}


async def gns3_import_image(
    source_path: str,
    emulator: str = "qemu",
    filename: Optional[str] = None,
    compute_id: str = "local",
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Import (upload) a local device image file into the GNS3 compute image store.

    Args:
        source_path: Local filesystem path to the image (.qcow2, .img, .bin, .image, ...)
        emulator: Target emulator store: qemu | dynamips | iou
        filename: Remote filename (defaults to basename of source_path)
        compute_id: Compute id (default 'local')

    Note: docker images are managed by Docker itself; use qemu/dynamips/iou here.
    """
    try:
        if emulator.lower() == "docker":
            return {
                "status": "error",
                "error": "Docker images are managed by Docker (pull/load), not GNS3 image upload. Use emulator=qemu|dynamips|iou.",
            }
        from pathlib import Path

        path = Path(source_path)
        if not path.is_file():
            return {"status": "error", "error": f"Image file not found: {source_path}"}

        remote_name = filename or path.name
        client = await create_client_ready(server_url, username, password)
        result = await client.upload_image(
            compute_id=compute_id,
            emulator=emulator,
            filename=remote_name,
            source_path=str(path),
        )
        return {"status": "success", "import": result}
    except Exception as e:
        logger.error(f"Failed to import image: {e}")
        return {"status": "error", "error": str(e)}


# ==================== SSH GUEST ACCESS ====================

async def gns3_ssh_exec(
    commands: List[str],
    host: Optional[str] = None,
    port: int = 22,
    project_id: Optional[str] = None,
    node_id: Optional[str] = None,
    ssh_username: Optional[str] = None,
    ssh_password: Optional[str] = None,
    stop_on_error: bool = True,
    host_key_policy: str = "accept_new",
    connect_timeout: Optional[float] = None,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run shell commands on a guest over SSH (password auth).

    Provide ``host`` explicitly, or omit it and pass ``project_id`` + ``node_id``
    to best-effort resolve an IP from GNS3 node metadata.

    Args:
        commands: Ordered shell commands to run on one SSH connection
        host: Guest IP/hostname (preferred)
        port: SSH port (default 22)
        project_id: Optional project for node metadata lookup
        node_id: Optional node for metadata IP discovery
        ssh_username: Guest SSH user (or GNS3_SSH_USER)
        ssh_password: Guest SSH password (or GNS3_SSH_PASSWORD)
        stop_on_error: Stop after first non-zero exit (default True)
        host_key_policy: accept_new | strict | warn (default accept_new)
        connect_timeout: Total SSH connect readiness budget in seconds
            (default 30 / GNS3_SSH_CONNECT_TIMEOUT); retries transient failures
        username/password: GNS3 API auth (not guest credentials)
    """
    try:
        resolved_host = host
        if not resolved_host:
            if not project_id or not node_id:
                return {
                    "status": "error",
                    "error": "host is required, or provide project_id and node_id for metadata lookup",
                }
            client = await create_client_ready(server_url, username, password)
            node = await client.get_node(project_id, node_id)
            ips = ssh_helpers.extract_ips_from_node(node if isinstance(node, dict) else {})
            if not ips:
                return {
                    "status": "error",
                    "error": "Could not resolve guest IP from node metadata; pass host explicitly",
                }
            resolved_host = ips[0]

        user, passwd = ssh_helpers.resolve_ssh_credentials(ssh_username, ssh_password)
        return await ssh_helpers.exec_commands(
            resolved_host,
            commands,
            port=port,
            username=user,
            password=passwd,
            stop_on_error=stop_on_error,
            host_key_policy=host_key_policy,
            connect_timeout=connect_timeout,
        )
    except Exception as e:
        logger.error(f"Failed SSH exec: {e}")
        return {"status": "error", "error": str(e)}


# ==================== GOAL WORKFLOW TOOLS ====================


async def gns3_prepare_lab(
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    create_if_missing: bool = True,
    open_project: bool = True,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    force_ensure: bool = False,
) -> Dict[str, Any]:
    """Ensure server, resolve/create project by name, open it, return project_id + step trace.

    Preferred entry for bootstrap lab / project playbook.
    """
    from .workflow.goals.prepare_lab import prepare_lab_goal

    return await prepare_lab_goal(
        project_name=project_name,
        project_id=project_id,
        create_if_missing=create_if_missing,
        open_project=open_project,
        server_url=server_url,
        username=username,
        password=password,
        force_ensure=force_ensure,
    )


async def gns3_build_topology(
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
    """Converge nodes/links by natural keys; optional start + validate. Observe-converge fail-stop.

    Preferred entry for build topology playbook.
    """
    from .workflow.goals.build_topology import build_topology_goal

    return await build_topology_goal(
        project_name=project_name,
        project_id=project_id,
        nodes=nodes,
        links=links,
        start=start,
        validate=validate,
        server_url=server_url,
        username=username,
        password=password,
    )


async def gns3_configure_devices(
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    targets: Optional[List[Dict[str, Any]]] = None,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """Configure devices by name via console (pure-body responses) or config templates.

    Preferred entry for configure devices playbook. Fail-stop across targets.
    """
    from .workflow.goals.configure_devices import configure_devices_goal

    return await configure_devices_goal(
        project_name=project_name,
        project_id=project_id,
        targets=targets,
        server_url=server_url,
        username=username,
        password=password,
    )


async def gns3_diagnose_connectivity(
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    suspect_nodes: Optional[List[Dict[str, Any]]] = None,
    probe_commands: Optional[List[str]] = None,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate topology and optionally probe suspects; findings cite tool evidence only.

    Preferred entry for diagnose connectivity playbook. No automatic remediation.
    """
    from .workflow.goals.diagnose_connectivity import diagnose_connectivity_goal

    return await diagnose_connectivity_goal(
        project_name=project_name,
        project_id=project_id,
        suspect_nodes=suspect_nodes,
        probe_commands=probe_commands,
        server_url=server_url,
        username=username,
        password=password,
    )


async def gns3_run_guest_commands(
    commands: Optional[List[str]] = None,
    host: Optional[str] = None,
    port: int = 22,
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    node_name: Optional[str] = None,
    node_id: Optional[str] = None,
    ssh_username: Optional[str] = None,
    ssh_password: Optional[str] = None,
    stop_on_error: bool = True,
    host_key_policy: str = "accept_new",
    connect_timeout: Optional[float] = None,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """Run guest SSH commands via host or project+node IP resolution. Never returns passwords.

    Preferred entry for guest SSH / host-style ops playbook.
    """
    from .workflow.goals.run_guest_commands import run_guest_commands_goal

    return await run_guest_commands_goal(
        commands=commands,
        host=host,
        port=port,
        project_name=project_name,
        project_id=project_id,
        node_name=node_name,
        node_id=node_id,
        ssh_username=ssh_username,
        ssh_password=ssh_password,
        stop_on_error=stop_on_error,
        host_key_policy=host_key_policy,
        connect_timeout=connect_timeout,
        server_url=server_url,
        username=username,
        password=password,
    )


async def gns3_prepare_image(
    source_path: Optional[str] = None,
    emulator: str = "qemu",
    filename: Optional[str] = None,
    compute_id: str = "local",
    idle_pc_project_name: Optional[str] = None,
    idle_pc_project_id: Optional[str] = None,
    idle_pc_node_name: Optional[str] = None,
    idle_pc_node_id: Optional[str] = None,
    densify_template: bool = False,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """Idempotent image import; optional Dynamips Idle-PC; densify stays yellow with escape note.

    Preferred entry for image import + densify + Idle-PC playbook.
    """
    from .workflow.goals.prepare_image import prepare_image_goal

    return await prepare_image_goal(
        source_path=source_path,
        emulator=emulator,
        filename=filename,
        compute_id=compute_id,
        idle_pc_project_name=idle_pc_project_name,
        idle_pc_project_id=idle_pc_project_id,
        idle_pc_node_name=idle_pc_node_name,
        idle_pc_node_id=idle_pc_node_id,
        densify_template=densify_template,
        server_url=server_url,
        username=username,
        password=password,
    )


async def gns3_manage_snapshot(
    operation: str,
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    snapshot_name: Optional[str] = None,
    snapshot_id: Optional[str] = None,
    confirmation_token: Optional[str] = None,
    safety_snapshot_name: Optional[str] = None,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """Snapshot create/list or restore/delete with one-time confirmation tokens.

    Preferred entry for snapshot / reset playbook. Destructive ops need token.
    """
    from .workflow.goals.manage_snapshot import manage_snapshot_goal

    return await manage_snapshot_goal(
        operation=operation,
        project_name=project_name,
        project_id=project_id,
        snapshot_name=snapshot_name,
        snapshot_id=snapshot_id,
        confirmation_token=confirmation_token,
        safety_snapshot_name=safety_snapshot_name,
        server_url=server_url,
        username=username,
        password=password,
    )


async def gns3_finish_lab(
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    stop_nodes: bool = False,
    close_project: bool = False,
    stop_server: bool = False,
    confirmation_token: Optional[str] = None,
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    """Session cleanup with defaults all false; destructive flags require confirmation token.

    Preferred entry for finish lab / cleanup playbook. Never auto-stops without flags+token.
    """
    from .workflow.goals.finish_lab import finish_lab_goal

    return await finish_lab_goal(
        project_name=project_name,
        project_id=project_id,
        stop_nodes=stop_nodes,
        close_project=close_project,
        stop_server=stop_server,
        confirmation_token=confirmation_token,
        server_url=server_url,
        username=username,
        password=password,
    )

