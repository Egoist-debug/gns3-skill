"""gns3_prepare_image goal implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from gns3_skill.gns3_client import GNS3APIClient, GNS3Config
from gns3_skill.server_lifecycle import ensure_gns3_server, normalize_server_url
from gns3_skill.workflow.envelopes import (
    STATUS_SUCCESS,
    STEP_CHANGED,
    STEP_FAILED,
    STEP_SKIPPED,
    STEP_SUCCESS,
    error_envelope,
    goal_envelope,
    step_entry,
)
from gns3_skill.workflow.resolve import ResolveAmbiguous, ResolveMissing, find_image, resolve_node, resolve_project
from gns3_skill.workflow.runner import Step, run_steps


async def prepare_image_goal(
    *,
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
    goal = "prepare_image"
    url = normalize_server_url(server_url)
    emu = (emulator or "").lower()
    if emu == "docker":
        return error_envelope(
            goal,
            "Docker images are managed by Docker (pull/load), not GNS3 image upload. Use emulator=qemu|dynamips|iou.",
        )
    if not source_path and not densify_template and not (
        idle_pc_node_id or idle_pc_node_name
    ):
        return error_envelope(
            goal,
            "source_path required for import (or request idle-pc / densify flags)",
        )

    remote_name = filename
    if source_path and not remote_name:
        remote_name = Path(source_path).name

    ctx: Dict[str, Any] = {
        "client": None,
        "import": None,
        "idle_pc": None,
        "yellow": None,
    }

    async def ensure_step() -> Dict[str, Any]:
        config = GNS3Config.from_env(server_url=url, username=username, password=password)
        result = await ensure_gns3_server(
            config.server_url, username=config.username, password=config.password
        )
        if result.get("status") != "success":
            return step_entry(
                "ensure_server",
                STEP_FAILED,
                error=result.get("error") or "GNS3 server not available",
            )
        ctx["client"] = GNS3APIClient(config)
        return step_entry("ensure_server", STEP_SUCCESS)

    async def import_step() -> Dict[str, Any]:
        if not source_path:
            return step_entry(
                "import_image",
                STEP_SKIPPED,
                detail={"reason": "no source_path"},
            )
        path = Path(source_path)
        if not path.is_file():
            return step_entry(
                "import_image",
                STEP_FAILED,
                error=f"Image file not found: {source_path}",
            )
        client: GNS3APIClient = ctx["client"]
        existing = await find_image(
            client, compute_id=compute_id, emulator=emu, filename=remote_name or path.name
        )
        if existing is not None:
            ctx["import"] = {
                "action": "skip",
                "compute_id": compute_id,
                "emulator": emu,
                "filename": remote_name or path.name,
                "existing": existing,
            }
            return step_entry("import_image", STEP_SKIPPED, detail=ctx["import"])
        uploaded = await client.upload_image(
            compute_id=compute_id,
            emulator=emu,
            filename=remote_name or path.name,
            source_path=str(path),
        )
        ctx["import"] = {"action": "upload", **uploaded}
        return step_entry("import_image", STEP_CHANGED, detail=ctx["import"])

    async def verify_import_step() -> Dict[str, Any]:
        if not source_path:
            return step_entry(
                "verify_image",
                STEP_SKIPPED,
                detail={"reason": "no source_path"},
            )
        if (ctx.get("import") or {}).get("action") == "skip":
            return step_entry(
                "verify_image",
                STEP_SKIPPED,
                detail={"reason": "image already existed"},
            )
        client: GNS3APIClient = ctx["client"]
        expected_name = remote_name or Path(source_path).name
        observed = await find_image(
            client,
            compute_id=compute_id,
            emulator=emu,
            filename=expected_name,
        )
        if observed is None:
            return step_entry(
                "verify_image",
                STEP_FAILED,
                error=f"uploaded image not visible after import: {expected_name}",
            )
        ctx["import"]["observed"] = observed
        return step_entry(
            "verify_image",
            STEP_SUCCESS,
            detail={"filename": expected_name, "observed": observed},
        )
    async def idle_pc_step() -> Dict[str, Any]:
        if not (idle_pc_node_id or idle_pc_node_name):
            return step_entry(
                "idle_pc",
                STEP_SKIPPED,
                detail={"reason": "no idle-pc node requested"},
            )
        if emu != "dynamips" and not idle_pc_node_id:
            # still allow if node is dynamips
            pass
        client: GNS3APIClient = ctx["client"]
        try:
            project = await resolve_project(
                client,
                project_id=idle_pc_project_id,
                project_name=idle_pc_project_name,
            )
            node = await resolve_node(
                client,
                project["project_id"],
                node_id=idle_pc_node_id,
                node_name=idle_pc_node_name,
            )
        except (ResolveMissing, ResolveAmbiguous, ValueError) as exc:
            return step_entry("idle_pc", STEP_FAILED, error=str(exc))
        try:
            auto = await client.get_node_dynamips_auto_idlepc(
                project["project_id"], node["node_id"]
            )
            ctx["idle_pc"] = {
                "node_name": node.get("name"),
                "node_id": node.get("node_id"),
                "auto_idlepc": auto,
            }
            return step_entry("idle_pc", STEP_SUCCESS, detail=ctx["idle_pc"])
        except Exception as exc:
            return step_entry(
                "idle_pc",
                STEP_FAILED,
                error=f"Idle-PC green path failed: {exc}",
                detail={"node": node.get("name")},
            )

    async def densify_step() -> Dict[str, Any]:
        if not densify_template:
            return step_entry(
                "densify_template",
                STEP_SKIPPED,
                detail={"reason": "densify_template=false"},
            )
        ctx["yellow"] = {
            "capability": "yellow",
            "action": "template densify / template CRUD",
            "message": (
                "Template densify is not a green MCP path. Escape: name the missing "
                "tool, get explicit user allow, then use minimum non-MCP REST if permitted."
            ),
        }
        return step_entry(
            "densify_template",
            STEP_SKIPPED,
            detail=ctx["yellow"],
        )

    result = await run_steps(
        [
            Step("ensure_server", ensure_step),
            Step("import_image", import_step),
            Step("verify_image", verify_import_step),
            Step("idle_pc", idle_pc_step),
            Step("densify_template", densify_step),
        ]
    )
    return goal_envelope(
        goal,
        result.status,
        result.steps,
        result={
            "import": ctx.get("import"),
            "idle_pc": ctx.get("idle_pc"),
            "yellow": ctx.get("yellow"),
            "compute_id": compute_id,
            "emulator": emu,
            "filename": remote_name,
        },
        error=result.error,
        next_hint=None
        if result.status == STATUS_SUCCESS
        else (ctx.get("yellow") or "Retry import or Idle-PC after fixing path/node"),
    )
