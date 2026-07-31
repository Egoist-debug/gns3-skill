"""Explicit single-source catalog for all GNS3 skill operations."""
from __future__ import annotations

import inspect
import re
from types import MappingProxyType
from typing import Dict, Optional, Tuple, Union

from .contracts import OperationSpec, OperationTier, assert_json_serializable
from .operations import device_io, nodes, projects, resources, session, snapshots, topology
from .workflow.goals import (
    build_topology_goal, configure_devices_goal, diagnose_connectivity_goal,
    finish_lab_goal, manage_snapshot_goal, prepare_image_goal, prepare_lab_goal,
    run_guest_commands_goal,
)

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _goal(identifier: str, callable, summary: str, *sensitive: str) -> OperationSpec:
    return OperationSpec(identifier, OperationTier.GOAL, callable, summary,
                         frozenset({"password", *sensitive}))


def _expert(identifier: str, callable, summary: str, *sensitive: str) -> OperationSpec:
    return OperationSpec(identifier, OperationTier.EXPERT, callable, summary,
                         frozenset({"password", *sensitive}))


OPERATIONS: Tuple[OperationSpec, ...] = (
    _goal("prepare_lab", prepare_lab_goal, "Ensure the server and resolve, create, and open a lab project."),
    _goal("build_topology", build_topology_goal, "Converge project nodes and links, optionally start and validate them."),
    _goal("configure_devices", configure_devices_goal, "Configure named devices through console commands or templates.", "targets"),
    _goal("diagnose_connectivity", diagnose_connectivity_goal, "Validate topology and collect evidence from suspect devices.", "suspect_nodes"),
    _goal("run_guest_commands", run_guest_commands_goal, "Run ordered shell commands on a guest by host or node identity.", "ssh_password"),
    _goal("prepare_image", prepare_image_goal, "Import an emulator image and optionally compute Dynamips Idle-PC."),
    _goal("manage_snapshot", manage_snapshot_goal, "Create, list, restore, or delete project snapshots safely.", "confirmation_token"),
    _goal("finish_lab", finish_lab_goal, "Preview and perform explicitly requested session cleanup.", "confirmation_token"),
    _expert("ensure_server", session.ensure_server, "Probe a GNS3 server and auto-start it only when local."),
    _expert("stop_server", session.stop_server, "Stop the local GNS3 server process for the selected URL."),
    _expert("cleanup_session", session.cleanup_session, "Run explicitly selected node, project, and server cleanup steps."),
    _expert("get_server_info", session.get_server_info, "Get GNS3 server version and feature information."),
    _expert("list_computes", session.list_computes, "List local and remote GNS3 compute servers."),
    _expert("list_projects", projects.list_projects, "List projects with stable identity and lifecycle fields."),
    _expert("create_project", projects.create_project, "Create a GNS3 project."),
    _expert("get_project", projects.get_project, "Get one project by provenance-supplied ID."),
    _expert("update_project", projects.update_project, "Update selected project settings."),
    _expert("open_project", projects.open_project, "Open a project for editing."),
    _expert("close_project", projects.close_project, "Close a project and stop its nodes."),
    _expert("delete_project", projects.delete_project, "Permanently delete a user-confirmed project."),
    _expert("duplicate_project", projects.duplicate_project, "Duplicate a project under a new name."),
    _expert("save_project", projects.save_project, "Read persisted project state and optionally create a snapshot."),
    _expert("export_project", projects.export_project, "Export a project archive to a local path."),
    _expert("list_nodes", nodes.list_nodes, "List nodes in a project."),
    _expert("add_node", nodes.add_node, "Add a node from a provenance-supplied template."),
    _expert("get_node", nodes.get_node, "Get one project node by ID."),
    _expert("update_node", nodes.update_node, "Update selected node settings and properties."),
    _expert("delete_node", nodes.delete_node, "Delete a user-confirmed node and its connected links."),
    _expert("start_node", nodes.start_node, "Start one node."),
    _expert("stop_node", nodes.stop_node, "Stop one node."),
    _expert("suspend_node", nodes.suspend_node, "Suspend one node."),
    _expert("reload_node", nodes.reload_node, "Reload one node."),
    _expert("duplicate_node", nodes.duplicate_node, "Duplicate a node at a new canvas position."),
    _expert("start_all_nodes", nodes.start_all_nodes, "Attempt to start every node in a project."),
    _expert("stop_all_nodes", nodes.stop_all_nodes, "Attempt to stop every node in a project."),
    _expert("list_links", topology.list_links, "List project links with endpoint and port details."),
    _expert("add_link", topology.add_link, "Create a link between provenance-supplied node ports."),
    _expert("delete_link", topology.delete_link, "Delete a user-confirmed project link."),
    _expert("get_topology", topology.get_topology, "Get project metadata, nodes, links, and counts."),
    _expert("validate_topology", topology.validate_topology, "Validate an observed project topology."),
    _expert("start_capture", topology.start_capture, "Start packet capture on a project link."),
    _expert("stop_capture", topology.stop_capture, "Stop packet capture on a project link."),
    _expert("add_text_annotation", topology.add_text_annotation, "Add a text annotation to the topology canvas."),
    _expert("add_shape", topology.add_shape, "Add a rectangle or ellipse to the topology canvas."),
    _expert("send_console_commands", device_io.send_console_commands, "Send ordered commands through a node console.", "enable_password", "login_password"),
    _expert("get_node_config", device_io.get_node_config, "Read a node's running or startup configuration."),
    _expert("apply_config_template", device_io.apply_config_template, "Render and apply a supported configuration template.", "template_params"),
    _expert("bulk_configure_nodes", device_io.bulk_configure_nodes, "Configure multiple nodes through the shared console transport."),
    _expert("ssh_exec", device_io.ssh_exec, "Run ordered commands through guest SSH.", "ssh_password"),
    _expert("list_templates", resources.list_templates, "List available device templates."),
    _expert("list_appliances", resources.list_appliances, "List available appliance definitions."),
    _expert("list_images", resources.list_images, "List emulator images on a compute."),
    _expert("import_image", resources.import_image, "Upload a local emulator image to a compute."),
    _expert("get_idle_pc_values", resources.get_idle_pc_values, "Compute or list Dynamips Idle-PC values."),
    _expert("list_snapshots", snapshots.list_snapshots, "List project snapshots."),
    _expert("create_snapshot", snapshots.create_snapshot, "Create a project snapshot."),
    _expert("restore_snapshot", snapshots.restore_snapshot, "Restore a user-confirmed project snapshot."),
    _expert("delete_snapshot", snapshots.delete_snapshot, "Permanently delete a user-confirmed project snapshot."),
)


def _validate_registry(operations: Tuple[OperationSpec, ...]) -> None:
    identifiers = [spec.identifier for spec in operations]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("operation identifiers must be unique")
    for spec in operations:
        if not _ID_PATTERN.fullmatch(spec.identifier) or spec.identifier.startswith("gns3_"):
            raise ValueError(f"invalid operation identifier: {spec.identifier!r}")
        if not inspect.iscoroutinefunction(spec.callable):
            raise ValueError(f"operation {spec.identifier!r} callable must be async")
        if not spec.summary.strip():
            raise ValueError(f"operation {spec.identifier!r} summary is required")
        parameters = spec.parameters()
        unknown_sensitive = spec.sensitive_parameters.difference(parameters)
        if unknown_sensitive:
            raise ValueError(
                f"operation {spec.identifier!r} has unknown sensitive parameters: {sorted(unknown_sensitive)}"
            )
        assert_json_serializable(spec.parameter_schema())
    counts = {tier: sum(spec.tier is tier for spec in operations) for tier in OperationTier}
    if counts != {OperationTier.GOAL: 8, OperationTier.EXPERT: 50}:
        raise ValueError(f"operation tier counts must be 8 goal / 50 expert, got {counts}")


_validate_registry(OPERATIONS)
OPERATION_BY_ID = MappingProxyType({spec.identifier: spec for spec in OPERATIONS})


def get_operation(identifier: str) -> Optional[OperationSpec]:
    return OPERATION_BY_ID.get(identifier)


def list_operations(tier: Union[OperationTier, str] = OperationTier.GOAL) -> Tuple[OperationSpec, ...]:
    if tier == "all":
        return OPERATIONS
    selected = tier if isinstance(tier, OperationTier) else OperationTier(tier)
    return tuple(spec for spec in OPERATIONS if spec.tier is selected)


def describe_operation(identifier: str) -> Optional[Dict[str, object]]:
    spec = get_operation(identifier)
    return spec.to_dict() if spec is not None else None
