"""Manage-snapshot goal implementation."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from gns3_skill.gns3_client import GNS3APIClient
from gns3_skill.runtime import OperationContext
from gns3_skill.workflow.confirm import consume_token, issue_token
from gns3_skill.workflow.envelopes import (
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    STEP_CHANGED,
    STEP_FAILED,
    STEP_SKIPPED,
    STEP_SUCCESS,
    confirmation_required_envelope,
    error_envelope,
    goal_envelope,
    step_entry,
)
from gns3_skill.workflow.resolve import (
    ResolveAmbiguous,
    ResolveMissing,
    resolve_project,
    resolve_snapshot,
)

_DESTRUCTIVE = {"restore", "delete_snapshot", "delete_project"}

SnapshotOperation = Literal[
    "create", "list", "restore", "delete_snapshot", "delete_project"
]


def _unused_snapshot_name(base: str, snapshots: List[Dict[str, Any]]) -> str:
    names = {
        str(snapshot.get("name"))
        for snapshot in snapshots
        if isinstance(snapshot, dict) and snapshot.get("name")
    }
    if base not in names:
        return base
    suffix = 2
    while f"{base}-{suffix}" in names:
        suffix += 1
    return f"{base}-{suffix}"


async def manage_snapshot_goal(
    *,
    context: OperationContext,
    operation: SnapshotOperation,
    project_name: Optional[str] = None,
    project_id: Optional[str] = None,
    snapshot_name: Optional[str] = None,
    snapshot_id: Optional[str] = None,
    confirmation_token: Optional[str] = None,
    safety_snapshot_name: Optional[str] = None,
) -> Dict[str, Any]:
    goal = "manage_snapshot"
    op = (operation or "").strip().lower()
    if op not in {"create", "list", "restore", "delete_snapshot", "delete_project"}:
        return error_envelope(
            goal,
            "operation must be create|list|restore|delete_snapshot|delete_project",
        )

    steps = []
    ensure = await context.ensure()
    if ensure.get("status") != "success":
        error = ensure.get("error") or "GNS3 server not available"
        steps.append(step_entry("ensure_server", STEP_FAILED, error=error))
        return goal_envelope(goal, "error", steps, error=error)
    steps.append(step_entry("ensure_server", STEP_SUCCESS))
    client = await context.client()

    try:
        project = await resolve_project(
            client, project_id=project_id, project_name=project_name
        )
    except (ResolveMissing, ResolveAmbiguous, ValueError) as exc:
        steps.append(step_entry("resolve_project", STEP_FAILED, error=str(exc)))
        return goal_envelope(goal, "error", steps, error=str(exc))
    pid = project["project_id"]
    steps.append(
        step_entry(
            "resolve_project",
            STEP_SUCCESS,
            detail={"project_id": pid, "name": project.get("name")},
        )
    )

    resolved_snapshot: Optional[Dict[str, Any]] = None
    if op in {"restore", "delete_snapshot"}:
        try:
            resolved_snapshot = await resolve_snapshot(
                client,
                pid,
                snapshot_id=snapshot_id,
                snapshot_name=snapshot_name,
            )
        except (ResolveMissing, ResolveAmbiguous, ValueError) as exc:
            steps.append(step_entry("resolve_snapshot", STEP_FAILED, error=str(exc)))
            return goal_envelope(goal, "error", steps, error=str(exc))
        steps.append(
            step_entry(
                "resolve_snapshot",
                STEP_SUCCESS,
                detail={
                    "snapshot_id": resolved_snapshot.get("snapshot_id"),
                    "name": resolved_snapshot.get("name"),
                },
            )
        )

    def failure(
        step: str,
        error: str,
        *,
        next_hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        steps.append(step_entry(step, STEP_FAILED, error=error))
        had_change = any(entry.get("status") == STEP_CHANGED for entry in steps)
        return goal_envelope(
            goal,
            STATUS_PARTIAL if had_change else "error",
            steps,
            error=error,
            next_hint=next_hint,
        )

    target = {"operation": op, "project_id": pid}
    if resolved_snapshot is not None:
        target["snapshot_id"] = resolved_snapshot.get("snapshot_id")

    safety_base: Optional[str] = None
    if op == "restore":
        assert resolved_snapshot is not None
        default_safety_name = (
            "safety-before-restore-"
            f"{resolved_snapshot.get('name') or resolved_snapshot.get('snapshot_id')}"
        )
        safety_base = (safety_snapshot_name or default_safety_name).strip()
        if not safety_base:
            return failure("restore_preflight", "safety_snapshot_name may not be empty")
        target["safety_snapshot_name"] = safety_base

    if op in _DESTRUCTIVE and not confirmation_token:
        token, expires = issue_token(op, target)
        impact = {
            **target,
            "project_name": project.get("name"),
            "snapshot_name": None
            if resolved_snapshot is None
            else resolved_snapshot.get("name"),
        }
        if op == "restore":
            impact["note"] = (
                "restore opens the project, stops non-stopped nodes, creates a "
                "collision-free safety snapshot, restores the requested snapshot, "
                "and leaves the project open"
            )
        steps.append(
            step_entry(
                "authorization",
                STEP_SUCCESS,
                detail={"phase": "preview", "action": op},
            )
        )
        return confirmation_required_envelope(
            goal,
            steps,
            action=op,
            target=target,
            impact=impact,
            confirmation_token=token,
            expires_at=expires,
        )

    restore_nodes: List[Dict[str, Any]] = []
    safety_name = safety_base
    if op == "restore":
        try:
            restore_nodes = await client.get_project_nodes(pid)
            observed_snapshots = await client.get_snapshots(pid)
        except Exception as exc:
            return failure(
                "restore_preflight",
                str(exc),
                next_hint="The confirmation token was not consumed; retry after the project is readable",
            )
        assert safety_base is not None
        safety_name = _unused_snapshot_name(safety_base, observed_snapshots)
        steps.append(
            step_entry(
                "restore_preflight",
                STEP_SUCCESS,
                detail={
                    "project_status": project.get("status"),
                    "nodes_to_stop": [
                        node.get("name")
                        for node in restore_nodes
                        if node.get("status") != "stopped"
                    ],
                    "safety_snapshot_name": safety_name,
                },
            )
        )

    if op in _DESTRUCTIVE:
        consumed = consume_token(confirmation_token, op, target)
        if not consumed.get("ok"):
            error = consumed.get("error") or "token rejected"
            steps.append(step_entry("authorization", STEP_FAILED, error=error))
            return goal_envelope(goal, "error", steps, error=error)
        steps.append(
            step_entry(
                "authorization",
                STEP_SUCCESS,
                detail={"phase": "consumed", "action": op},
            )
        )

    result: Dict[str, Any]
    if op == "list":
        try:
            snapshots = await client.get_snapshots(pid)
        except Exception as exc:
            return failure("list_snapshots", str(exc))
        steps.append(
            step_entry(
                "list_snapshots", STEP_SUCCESS, detail={"count": len(snapshots)}
            )
        )
        result = {"snapshots": snapshots, "total": len(snapshots)}
    elif op == "create":
        name = (snapshot_name or "").strip()
        if not name:
            return failure("create_snapshot", "snapshot_name required")
        try:
            existing = await resolve_snapshot(client, pid, snapshot_name=name)
        except ResolveMissing:
            try:
                created = await client.create_snapshot(pid, name)
            except Exception as exc:
                return failure("create_snapshot", str(exc))
            steps.append(
                step_entry(
                    "create_snapshot",
                    STEP_CHANGED,
                    detail={
                        "snapshot_id": created.get("snapshot_id")
                        if isinstance(created, dict)
                        else None,
                        "name": name,
                    },
                )
            )
            result = {"snapshot": created, "action": "create"}
        except (ResolveAmbiguous, ValueError) as exc:
            return failure("create_snapshot", str(exc))
        else:
            steps.append(
                step_entry(
                    "create_snapshot",
                    STEP_SKIPPED,
                    detail={
                        "action": "reuse",
                        "snapshot_id": existing.get("snapshot_id"),
                        "name": existing.get("name"),
                    },
                )
            )
            result = {"snapshot": existing, "action": "reuse"}
    elif op == "restore":
        assert resolved_snapshot is not None and safety_name is not None

        if project.get("status") != "opened":
            try:
                await client.open_project(pid)
            except Exception as exc:
                return failure("open_project", str(exc))
            steps.append(
                step_entry(
                    "open_project", STEP_CHANGED, detail={"project_id": pid}
                )
            )
        else:
            steps.append(
                step_entry(
                    "open_project",
                    STEP_SKIPPED,
                    detail={"reason": "project already opened"},
                )
            )

        nodes_to_stop = [
            node for node in restore_nodes if node.get("status") != "stopped"
        ]
        stopped: List[Any] = []
        stop_failures: List[Dict[str, Any]] = []
        for node in nodes_to_stop:
            try:
                await client.stop_node(pid, node["node_id"])
                stopped.append(node.get("name"))
            except Exception as exc:
                stop_failures.append(
                    {"name": node.get("name"), "error": str(exc)}
                )
        if stop_failures:
            return failure(
                "stop_nodes",
                "some nodes failed to stop"
                if stopped
                else "all node stop attempts failed",
                next_hint="Stop every node, then request a new restore preview if the token was consumed",
            )
        steps.append(
            step_entry(
                "stop_nodes",
                STEP_CHANGED if stopped else STEP_SKIPPED,
                detail={
                    "stopped": stopped,
                    "reason": None if stopped else "all nodes already stopped",
                },
            )
        )

        try:
            safety = await client.create_snapshot(pid, safety_name)
        except Exception as exc:
            return failure(
                "safety_snapshot",
                f"safety snapshot failed: {exc}",
                next_hint="Inspect current project/node state, then request a new restore preview",
            )
        steps.append(
            step_entry(
                "safety_snapshot",
                STEP_CHANGED,
                detail={
                    "name": safety_name,
                    "snapshot_id": safety.get("snapshot_id")
                    if isinstance(safety, dict)
                    else None,
                },
            )
        )

        async def ensure_open_after_restore() -> Optional[str]:
            try:
                current_project = await client.get_project(pid)
                if current_project.get("status") == "opened":
                    steps.append(
                        step_entry(
                            "reopen_project",
                            STEP_SKIPPED,
                            detail={"reason": "project already opened"},
                        )
                    )
                    return None
                await client.open_project(pid)
            except Exception as exc:
                return str(exc)
            steps.append(
                step_entry(
                    "reopen_project", STEP_CHANGED, detail={"project_id": pid}
                )
            )
            return None

        try:
            restored = await client.restore_snapshot(
                pid, resolved_snapshot["snapshot_id"]
            )
        except Exception as exc:
            restore_error = str(exc)
            steps.append(
                step_entry("restore_snapshot", STEP_FAILED, error=restore_error)
            )
            reopen_error = await ensure_open_after_restore()
            if reopen_error:
                steps.append(
                    step_entry("reopen_project", STEP_FAILED, error=reopen_error)
                )
                restore_error = (
                    f"{restore_error}; project reopen failed: {reopen_error}"
                )
            return goal_envelope(
                goal,
                STATUS_PARTIAL,
                steps,
                error=restore_error,
                next_hint=(
                    f"Safety snapshot {safety_name!r} exists; inspect state and request a new restore preview"
                ),
            )
        steps.append(
            step_entry(
                "restore_snapshot",
                STEP_CHANGED,
                detail={
                    "snapshot_id": resolved_snapshot.get("snapshot_id"),
                    "name": resolved_snapshot.get("name"),
                },
            )
        )
        reopen_error = await ensure_open_after_restore()
        if reopen_error:
            return failure(
                "reopen_project",
                reopen_error,
                next_hint="The snapshot was restored, but the project could not be reopened",
            )
        result = {
            "restored": restored,
            "from_snapshot": resolved_snapshot,
            "safety_snapshot": {
                "name": safety_name,
                "snapshot_id": safety.get("snapshot_id")
                if isinstance(safety, dict)
                else None,
            },
        }
    elif op == "delete_snapshot":
        assert resolved_snapshot is not None
        try:
            await client.delete_snapshot(pid, resolved_snapshot["snapshot_id"])
        except Exception as exc:
            return failure("delete_snapshot", str(exc))
        steps.append(
            step_entry(
                "delete_snapshot",
                STEP_CHANGED,
                detail={
                    "snapshot_id": resolved_snapshot.get("snapshot_id"),
                    "name": resolved_snapshot.get("name"),
                },
            )
        )
        result = {"deleted_snapshot_id": resolved_snapshot.get("snapshot_id")}
    else:
        try:
            await client.delete_project(pid)
        except Exception as exc:
            return failure("delete_project", str(exc))
        steps.append(
            step_entry(
                "delete_project",
                STEP_CHANGED,
                detail={"project_id": pid, "name": project.get("name")},
            )
        )
        result = {"deleted_project_id": pid}

    return goal_envelope(
        goal,
        STATUS_SUCCESS,
        steps,
        result={
            "operation": op,
            "project_id": pid,
            "project_name": project.get("name"),
            **result,
        },
    )
