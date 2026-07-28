"""gns3_manage_snapshot goal implementation."""

from __future__ import annotations

from typing import Any, Dict, Optional

from gns3_skill.gns3_client import GNS3APIClient, GNS3Config
from gns3_skill.server_lifecycle import ensure_gns3_server, normalize_server_url
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


async def manage_snapshot_goal(
    *,
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
    goal = "manage_snapshot"
    op = (operation or "").strip().lower()
    if op not in {"create", "list", "restore", "delete_snapshot", "delete_project"}:
        return error_envelope(
            goal,
            "operation must be create|list|restore|delete_snapshot|delete_project",
        )

    url = normalize_server_url(server_url)
    config = GNS3Config.from_env(
        server_url=url, username=username, password=password
    )
    steps = []
    ensure = await ensure_gns3_server(
        config.server_url,
        username=config.username,
        password=config.password,
    )
    if ensure.get("status") != "success":
        error = ensure.get("error") or "GNS3 server not available"
        steps.append(step_entry("ensure_server", STEP_FAILED, error=error))
        return goal_envelope(goal, "error", steps, error=error)
    steps.append(step_entry("ensure_server", STEP_SUCCESS))
    client = GNS3APIClient(config)

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

    target = {"operation": op, "project_id": pid}
    if resolved_snapshot is not None:
        target["snapshot_id"] = resolved_snapshot.get("snapshot_id")

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
                "restore will be preceded by an automatic safety snapshot when possible"
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
        assert resolved_snapshot is not None
        safety_name = safety_snapshot_name or (
            "safety-before-restore-"
            f"{resolved_snapshot.get('name') or resolved_snapshot.get('snapshot_id')}"
        )
        try:
            safety = await client.create_snapshot(pid, safety_name)
        except Exception as exc:
            return failure("safety_snapshot", f"safety snapshot failed: {exc}")
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
        try:
            restored = await client.restore_snapshot(
                pid, resolved_snapshot["snapshot_id"]
            )
        except Exception as exc:
            return failure(
                "restore_snapshot",
                str(exc),
                next_hint=(
                    f"Safety snapshot {safety_name!r} exists; inspect state and retry restore"
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
        result = {"restored": restored, "from_snapshot": resolved_snapshot}
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
