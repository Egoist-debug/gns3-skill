"""Prepare-lab goal implementation."""

from __future__ import annotations

from typing import Any, Dict, Optional

from gns3_skill.gns3_client import GNS3APIClient
from gns3_skill.runtime import OperationContext
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
from gns3_skill.workflow.resolve import resolve_or_missing_project
from gns3_skill.workflow.runner import Step, run_steps


async def prepare_lab_goal(
    *,
    context: OperationContext,
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    create_if_missing: bool = True,
    open_project: bool = True,
    force_ensure: bool = False,
) -> Dict[str, Any]:
    goal = "prepare_lab"
    url = context.server_url
    ctx: Dict[str, Any] = {"client": None, "project": None}

    async def ensure_step() -> Dict[str, Any]:
        result = await context.ensure(force=force_ensure)
        if result.get("status") != "success":
            return step_entry(
                "ensure_server",
                STEP_FAILED,
                detail=result,
                error=result.get("error") or "GNS3 server not available",
            )
        ctx["client"] = await context.client()
        status = STEP_CHANGED if result.get("started") else STEP_SUCCESS
        return step_entry("ensure_server", status, detail={
            "server_url": result.get("server_url") or url,
            "already_running": result.get("already_running"),
            "started": result.get("started"),
        })

    async def resolve_step() -> Dict[str, Any]:
        client: GNS3APIClient = ctx["client"]
        existing = await resolve_or_missing_project(
            client, project_id=project_id, project_name=project_name
        )
        if existing is not None:
            ctx["project"] = existing
            return step_entry(
                "resolve_project",
                STEP_SKIPPED,
                detail={
                    "action": "reuse",
                    "project_id": existing.get("project_id"),
                    "name": existing.get("name"),
                },
            )
        if project_id and not project_name:
            return step_entry(
                "resolve_project",
                STEP_FAILED,
                error=f"project_id not found: {project_id}",
            )
        if not create_if_missing:
            return step_entry(
                "resolve_project",
                STEP_FAILED,
                error="project missing and create_if_missing=false",
            )
        name = (project_name or "").strip()
        if not name:
            return step_entry(
                "resolve_project",
                STEP_FAILED,
                error="project_name required to create project",
            )
        created = await client.create_project(name)
        ctx["project"] = created
        return step_entry(
            "resolve_project",
            STEP_CHANGED,
            detail={
                "action": "create",
                "project_id": created.get("project_id"),
                "name": created.get("name"),
            },
        )

    async def open_step() -> Dict[str, Any]:
        if not open_project:
            return step_entry("open_project", STEP_SKIPPED, detail={"reason": "open_project=false"})
        project = ctx.get("project") or {}
        pid = project.get("project_id")
        if not pid:
            return step_entry("open_project", STEP_FAILED, error="no project to open")
        client: GNS3APIClient = ctx["client"]
        # Always open explicitly — even if the cached project status says "opened",
        # the GNS3 server may have restarted, losing all open states.  An open
        # call on an already-opened project is a cheap no-op, so we always do it.
        opened = await client.open_project(pid)
        if isinstance(opened, dict) and opened.get("status") == "opened":
            ctx["project"] = opened
            return step_entry(
                "open_project",
                STEP_CHANGED,
                detail={"project_id": pid},
            )
        # open_project may return the project dict or raise; if we get here
        # the project is open but we may not have a clean status.
        ctx["project"] = opened if isinstance(opened, dict) else project
        return step_entry(
            "open_project",
            STEP_CHANGED,
            detail={"project_id": pid, "note": "open issued but status unclear"},
        )

    if not project_id and not (project_name and project_name.strip()):
        return error_envelope(goal, "project_id or project_name is required")

    result = await run_steps(
        [
            Step("ensure_server", ensure_step),
            Step("resolve_project", resolve_step),
            Step("open_project", open_step),
        ]
    )
    project = ctx.get("project") or {}
    if result.status == STATUS_SUCCESS:
        return goal_envelope(
            goal,
            STATUS_SUCCESS,
            result.steps,
            result={
                "project_id": project.get("project_id"),
                "name": project.get("name"),
                "project_status": project.get("status"),
                "server_url": url,
            },
        )
    return goal_envelope(
        goal,
        result.status,
        result.steps,
        error=result.error,
        result={
            "project_id": project.get("project_id"),
            "name": project.get("name"),
            "server_url": url,
        },
        next_hint="Fix ensure/server or project identity and retry",
    )
