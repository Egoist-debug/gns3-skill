---
name: gns3-skill
description: "Skill-first GNS3 lab operations via CLI. Use when the user works with GNS3 projects, topologies, nodes, links, Dynamips/QEMU/SONiC labs, device console, guest SSH, image import, Idle-PC, or when the agent is about to call GNS3 REST, write Python against GNS3, or curl port 3080. Load this skill and drive the lab only through scripts/gns3."
---

# GNS3 Skill (CLI-first)

GNS3 work is executed **only** through this skill’s CLI. Hand-rolled REST, telnet, SSH, or ad-hoc Python clients are not an ops path.

Surface: **58** tools — **8 goal tools** + **50 expert tools**. Prefer goals for playbooks; use expert tools for partial/custom work.

## How to invoke

Single entrypoint (do **not** invent per-tool script files):

```bash
# from workspace root (or any cwd; script adds skill src to path)
python3 gns3-skill/scripts/gns3 list
python3 gns3-skill/scripts/gns3 gns3_prepare_lab --project_name=demo
python3 gns3-skill/scripts/gns3 gns3_list_nodes --json '{"project_id":"<id>"}'
echo '{"project_name":"demo"}' | python3 gns3-skill/scripts/gns3 gns3_prepare_lab
```

Prefer a project venv when available:

```bash
gns3-skill/.venv/bin/python gns3-skill/scripts/gns3 <tool> ...
# or after: pip install -e gns3-skill/
gns3 <tool> ...
```

Args:

| Form | Example |
|------|---------|
| `--key=value` | `--project_name=demo --create_if_missing=true` |
| `--json '{...}'` | complex lists/objects (`nodes`, `targets`, `commands`) |
| stdin JSON object | pipe a JSON object (not combined with `--json`) |

Output is JSON on stdout. Non-success `status` → exit code 1.

Bare names work: `prepare_lab` ≡ `gns3_prepare_lab`.

## When this skill applies

Load and follow this skill for any of:

- GNS3 project / topology / node / link work
- Device console or guest SSH inside GNS3
- Image import, Dynamips densify, Idle-PC
- Any impulse to hit GNS3 via REST, `curl`, `httpx`, `requests`, raw telnet/SSH, or a new Python script

## Hard rules

1. **CLI-first.** Prefer the **8 goal tools** for standard lab playbooks; use expert `gns3_*` tools when goals do not fit. Every GNS3 action goes through `scripts/gns3` (or the installable `gns3` console script).
2. **Forbidden without the escape ritual:**
   - HTTP to GNS3 (`:3080`, `/v2/...`) via curl/httpx/requests/fetch/etc.
   - Raw telnet/console when `gns3_send_console_commands` can do the job
   - Raw guest SSH when `gns3_ssh_exec` / `gns3_run_guest_commands` can do the job
   - Agent-written scripts that reimplement GNS3 REST as a lab driver
   - Running package `tests/` ad-hoc lab scripts as a substitute for the skill CLI
3. **Allowed without escape:**
   - `python3 gns3-skill/scripts/gns3 …` / installable `gns3 …`
   - Reading this skill, `references/*`, `AGENTS.md`
   - Local filesystem for image paths, export destinations, reading capture files
   - Non-GNS3 work (git, unrelated code, general shell)
   - Editing `gns3-skill` (or the linked library package) when the **task is developing the skill**
4. **IDs are provenance-only.** Goal tools resolve names (`project_name`, `node_name`, `template_name`, `snapshot_name`) internally. For expert tools, `project_id` / `node_id` / `link_id` / `template_id` / `snapshot_id` / adapter+port must come from a prior CLI result or the user. Never invent UUIDs.
5. **Session open.** Prefer `gns3_prepare_lab` (ensure + resolve/create + open). Expert-only path: first GNS3 action is `gns3_ensure_server`. **Local server credentials come from the config file, not env.** For a normally-installed local GNS3 (auth enabled), the CLI reads `[Server]` `user` / `password` straight from `~/.config/GNS3/2.2/gns3_server.conf` (see `references/setup.md` → “Finding local server credentials”) — no `GNS3_*` env ritual is required or expected. Resolution order is kwarg > env > local conf. A `401`/`403` from the ensure probe is the skill telling you the local conf couldn’t supply credentials — re-read the conf path, never guess; clear the cached unhealthy probe with `gns3_ensure_server --force=true` (or goal `gns3_prepare_lab --force_ensure=true`) after fixing. Use `GNS3_USERNAME` / `GNS3_PASSWORD` only for **remote** GNS3 servers that have no local conf to read.
6. **Session close (ask first).** Prefer `gns3_finish_lab` after user intent. Defaults are all false (inert). Any true `stop_nodes` / `close_project` / `stop_server` needs a preview → user yes → `confirmation_token` re-call. Expert path: ask close/stop, then `gns3_cleanup_session` or discrete tools. Never auto-close or auto-stop without explicit yes.
7. **Secrets.** For the **local** GNS3 server the skill reads `[Server]` `user` / `password` from the local `gns3_server.conf` automatically (see `references/setup.md` → “Finding local server credentials”); you do not need to set anything in the shell. `GNS3_USERNAME` / `GNS3_PASSWORD` are **remote-server** overrides only (or explicit `--username` / `--password`). Document variable **names** only. Never print passwords. Console `results[].response` is **pure command body**; completion is `completed` bool when first-prompt framing succeeded.
   - **Find credentials, don’t brute-force.** On a `401` / `403` (or a probe reporting `http_status: 401` / `403`), **stop retrying**. Do not loop on different guessed passwords. The 401 means the local `gns3_server.conf` could not supply credentials (missing/unreadable, `auth` off but server still rejects, or wrong value). Check the conf path/file once (`references/setup.md` → “Finding local server credentials”), confirm `auth = True` and the `user`/`password` it contains, then retry the same call. For a **remote** server, ask the user once and pass via `--username` / `--password`. One auth failure → one lookup → one retry with the real value.
8. **Destructive ops + tokens.** Goal tools only:
   - `gns3_manage_snapshot`: destructive ops = `restore` | `delete_snapshot` | `delete_project` (create/list are not token-gated)
   - `gns3_finish_lab`: any true cleanup flag
   - Response: `status: confirmation_required` with `result.confirmation_token` bound to **action + target**. Tokens are one-shot, process-local, default TTL 600s (`GNS3_CONFIRM_TOKEN_TTL_SECONDS`).
   - After **user** confirms, re-call with the **same** resolved target fields + token.
   - Expert delete/restore/export still needs user confirm in chat (no token system on expert tools).

## Escape ritual (only path around the CLI)

Use only when a required `gns3_*` tool is missing or returns a hard failure, or the path is **yellow** in the capability matrix:

1. Name the missing/broken tool and the error (or the matrix gap).
2. Ask the user for explicit allow for **this** action.
3. After allow, do the **minimum** non-CLI work and state that in the reply.

No silent fallback. Yellow ≠ auto-escape.

## Preferred: goal tools

| Goal tool | Playbook | Key inputs |
|-----------|----------|------------|
| `gns3_prepare_lab` | Bootstrap lab / project | `project_name` or `project_id`; `create_if_missing` / `open_project` (default true); `force_ensure` |
| `gns3_build_topology` | Build topology | `project_*`; `nodes[]`; `links[]`; `start`; `validate` (default true) |
| `gns3_configure_devices` | Configure devices | `project_*`; `targets[]` with commands or template |
| `gns3_diagnose_connectivity` | Diagnose connectivity | `project_*`; optional `suspect_nodes[]`, `probe_commands` |
| `gns3_run_guest_commands` | Guest SSH / host-style ops | `commands`; `host` **or** project+node |
| `gns3_prepare_image` | Image import + Idle-PC | `source_path` + `emulator` |
| `gns3_manage_snapshot` | Snapshot / reset | `operation`: create/list/restore/delete_* |
| `gns3_finish_lab` | Session cleanup | flags default false; any true flag → token after user yes |

### Goal envelope contract

- `status`: `success` \| `error` \| `partial` \| `confirmation_required` \| `conflict`
- `goal`, `steps[]`, `result` / `error` / `next` as applicable
- **Observe-converge** + **fail-stop**

## Standard lab loop

1. `gns3_prepare_lab`
2. `gns3_build_topology`
3. `gns3_configure_devices` / `gns3_run_guest_commands`
4. `gns3_diagnose_connectivity` when verifying
5. `gns3_manage_snapshot` for checkpoints
6. On completion: ask cleanup flags → `gns3_finish_lab` preview → user yes → re-call with token

## Expert tool inventory (50)

| Area | Tools |
|------|--------|
| Server / session | `gns3_ensure_server`, `gns3_stop_server`, `gns3_cleanup_session`, `gns3_get_server_info`, `gns3_list_computes` |
| Projects | `gns3_list_projects`, `gns3_create_project`, `gns3_get_project`, `gns3_update_project`, `gns3_open_project`, `gns3_close_project`, `gns3_delete_project`, `gns3_duplicate_project`, `gns3_save_project`, `gns3_export_project` |
| Nodes | `gns3_list_nodes`, `gns3_add_node`, `gns3_get_node`, `gns3_update_node`, `gns3_delete_node`, `gns3_start_node`, `gns3_stop_node`, `gns3_suspend_node`, `gns3_reload_node`, `gns3_duplicate_node`, `gns3_start_all_nodes`, `gns3_stop_all_nodes` |
| Links | `gns3_list_links`, `gns3_add_link`, `gns3_delete_link` |
| Topology | `gns3_get_topology`, `gns3_validate_topology` |
| Console / config | `gns3_send_console_commands`, `gns3_get_node_config`, `gns3_apply_config_template`, `gns3_bulk_configure_nodes` |
| Templates / appliances / images | `gns3_list_templates`, `gns3_list_appliances`, `gns3_list_images`, `gns3_import_image`, `gns3_get_idle_pc_values` |
| Snapshots | `gns3_list_snapshots`, `gns3_create_snapshot`, `gns3_restore_snapshot`, `gns3_delete_snapshot` |
| Capture / canvas | `gns3_start_capture`, `gns3_stop_capture`, `gns3_add_text_annotation`, `gns3_add_shape` |
| Guest SSH | `gns3_ssh_exec` |

Yellow gaps: template create/update, densify-on-template, idle-pc write to template, appliance file install, docker image pull. See `references/capability-matrix.md`.

## Progressive disclosure

| Need | Load |
|------|------|
| Install / env / host wiring | `references/setup.md` |
| What CLI can vs cannot do | `references/capability-matrix.md` |
| Recipe for a common lab job | `references/playbooks.md` |

## Anti-patterns

- Writing `scripts/fix_lab.py` that talks to GNS3 because “it’s faster”
- Curl-ing `http://127.0.0.1:3080/v2/projects` after a tool error instead of the escape ritual
- Guessing a `project_id` from memory
- Calling non-existent `scripts/gns3_prepare_lab` — use `scripts/gns3 gns3_prepare_lab`
- Printing API passwords into the transcript
- Stopping gns3server or closing a project without asking
- Treating `gns3_finish_lab` preview as “already done”
- Retrying `gns3_ensure_server` / `gns3_prepare_lab` with guessed username/password after a `401` — the skill already reads the local `gns3_server.conf` automatically; on a `401` you check that file once (or ask for remote creds) and retry the **same** call, never iterate candidate passwords (`references/setup.md` → “Finding local server credentials”)
- Reusing a confirmation token after changing project, snapshot, or flags
