"""Per-playbook goal implementations (called from thin MCP tools)."""

from .build_topology import build_topology_goal
from .configure_devices import configure_devices_goal
from .diagnose_connectivity import diagnose_connectivity_goal
from .finish_lab import finish_lab_goal
from .manage_snapshot import manage_snapshot_goal
from .prepare_image import prepare_image_goal
from .prepare_lab import prepare_lab_goal
from .run_guest_commands import run_guest_commands_goal

__all__ = [
    "build_topology_goal",
    "configure_devices_goal",
    "diagnose_connectivity_goal",
    "finish_lab_goal",
    "manage_snapshot_goal",
    "prepare_image_goal",
    "prepare_lab_goal",
    "run_guest_commands_goal",
]
