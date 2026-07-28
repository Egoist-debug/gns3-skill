# GNS3 skill playbooks

Canonical tool names: `gns3_*`. Invoke via the skill CLI:
`python3 gns3-skill/scripts/gns3 <tool> [--key=value|--json '{...}']`.

**Prefer the goal tool** for each playbook. Expert tools remain for partial/custom work.
IDs only from prior CLI results or user paste when using expert tools. Goal tools resolve names.

Goal envelope: `status` ∈ `success|error|partial|confirmation_required|conflict`; `steps[]` with `success|skipped|changed|failed`.

---

## 1. Bootstrap lab / project

**Goal tool:** `gns3_prepare_lab`

| Arg | Notes |
|-----|--------|
| `project_name` or `project_id` | At least one for a known target; name used to create when missing |
| `create_if_missing` | default `true` |
| `open_project` | default `true` |
| `force_ensure` | default `false` — bypass healthy cache |

**Expert fallback:**

1. `gns3_ensure_server`
2. `gns3_get_server_info` (optional sanity)
3. `gns3_list_projects`
4. If project exists: `gns3_open_project` with its `project_id`
5. Else: `gns3_create_project` with the agreed name → open if required by host state
6. `gns3_list_templates` (cache names → template_id for later add_node)

**Done when:** Server healthy and a `project_id` is known from a tool result.

---

## 2. Build topology (nodes + links)

**Goal tool:** `gns3_build_topology`

| Arg | Notes |
|-----|--------|
| `project_name` / `project_id` | Required to resolve project |
| `nodes` | `[{name, template_id\|template_name, x?, y?, compute_id?}]` — names unique in request |
| `links` | `[{a, b}]` or `[{node_a, node_b}]`; each end = `{node_name, adapter?, port?}` or bare name string |
| `start` | default `false` |
| `validate` | default `true` |

**Converge rules:**

- Existing node **same name**: reuse; different template → conflict/fail (no replace)
- Missing node: create from template
- Links: adapter+port both set or both omitted (auto free port); replay is stable for identical endpoints
- Fail-stop on first hard error; may return `partial` if earlier creates succeeded

**Expert fallback:**

1. Complete playbook 1 if needed
2. `gns3_list_templates` — pick `template_id` by name/type (never invent)
3. For each device: `gns3_add_node` (`project_id`, `node_name`, `template_id`, position as needed)
4. `gns3_list_nodes` — map name → `node_id`
5. For each link:
   - `gns3_get_node` on endpoints if adapter/port unknown
   - `gns3_add_link` with real node ids and ports
6. `gns3_list_links` or `gns3_get_topology` to verify
7. `gns3_start_node` / `gns3_start_all_nodes` when the user wants power-on
8. `gns3_validate_topology`

**Done when:** Requested nodes and links appear in list/topology results (or goal `result` shows created/skipped).

---

## 3. Configure devices

**Goal tool:** `gns3_configure_devices`

| Arg | Notes |
|-----|--------|
| `project_name` / `project_id` | Required |
| `targets` | Required non-empty list |

Each target:

| Field | Notes |
|-------|--------|
| `node_name` or `node_id` | Resolved via CLI tools |
| `commands` | Raw CLI list **or** |
| `template_name` + `params` | Workflow templates only: `basic_router`, `interface`, `vlan`, `ospf` |
| `enter_config_mode` | default `true` |
| `save_config` | default `false` |
| `verify_commands` | Optional post-check CLI (no config mode) |
| `login_username` / `login_password` / `enable_password` | Console auth (or env defaults) |

Behavior: auto-starts stopped nodes; fail-stop across targets; console bodies pure text + `completed`; incomplete command framing fails the target.

**Expert fallback:**

1. Resolve `node_id` via `gns3_list_nodes` (by name)
2. Ensure node is started if console needs it (`gns3_start_node` / status from list)
3. Choose path:
   - **Template-first:** `gns3_apply_config_template` when a built-in fits (broader set than goal templates)
   - **Pasted CLI:** `gns3_send_console_commands` with the user’s commands (`enter_config_mode` / `save_config` as appropriate; console login via `login_username` / `login_password` or env defaults — do not print secrets)
   - **Bulk (≥3 similar):** `gns3_bulk_configure_nodes`
4. Verify: `gns3_get_node_config` and/or show commands via console

**Done when:** Verification output matches the requested config intent.

---

## 4. Diagnose connectivity

**Goal tool:** `gns3_diagnose_connectivity`

| Arg | Notes |
|-----|--------|
| `project_name` / `project_id` | Required to scope topology |
| `suspect_nodes` | Optional list of name/id dicts to probe |
| `probe_commands` | Optional CLI list for suspects |

Validates topology + optional console probes. **Findings only** — no automatic remediation. Evidence must cite CLI tool outputs.

**Expert fallback:**

1. `gns3_ensure_server` if not yet this session
2. `gns3_validate_topology`
3. `gns3_list_nodes` / `gns3_get_topology` — status, missing links, wrong ports
4. On suspects: `gns3_send_console_commands` (`show ip interface brief`, `show ip route`, `ping`, vendor-appropriate)
5. Optional: `gns3_start_capture` on a link → user/Wireshark analysis → `gns3_stop_capture`
6. Fix with green tools (start interface via console, add missing link, start node, re-apply config)
7. Re-check with the same probes

**Done when:** Root cause is stated with evidence from CLI outputs, and fix (if requested) is applied via the skill CLI.

---

## 5. Guest SSH / host-style ops (e.g. SONiC VS)

**Goal tool:** `gns3_run_guest_commands`

| Arg | Notes |
|-----|--------|
| `commands` | Required shell list |
| `host` + optional `port` | Prefer when IP known |
| `project_*` + `node_name`/`node_id` | Alternate: resolve guest IP from node metadata |
| `ssh_username` / `ssh_password` | Or `GNS3_SSH_*` env — never echo |
| `stop_on_error` | default `true` |
| `host_key_policy` | default `accept_new` |

Goal never returns passwords in the envelope.

**Expert fallback:**

1. Resolve node; ensure it is started and has management reachability
2. Prefer `gns3_ssh_exec` with explicit `host` when known; else `project_id` + `node_id` for metadata IP discovery
3. Pass `ssh_username` / `ssh_password` or rely on `GNS3_SSH_*` env — never echo passwords
4. Use console (`gns3_send_console_commands`) only if SSH is the wrong plane for that image

**Done when:** Command results from the goal tool or `gns3_ssh_exec` (or justified console path) answer the ask.

---

## 6. Image import + densify + Idle-PC

**Goal tool:** `gns3_prepare_image`

| Arg | Notes |
|-----|--------|
| `source_path` | Local path to upload (idempotent by remote filename) |
| `emulator` | `qemu` \| `dynamips` \| `iou` — **`docker` rejected** |
| `filename` | Optional remote name (default basename) |
| `compute_id` | default `local` |
| `idle_pc_project_*` + `idle_pc_node_*` | Optional Dynamips Idle-PC compute on a node |
| `densify_template` | If true: step is **skipped** with yellow escape note (no green densify) |

**Expert green path:**

1. `gns3_ensure_server`
2. `gns3_import_image` (`source_path`, `emulator`: `qemu` | `dynamips` | `iou`)
3. `gns3_list_images` to confirm
4. `gns3_list_templates` — if a suitable template already exists, use it with `gns3_add_node`
5. Start Dynamips node when needed → `gns3_get_idle_pc_values` (`auto_compute` true)

### Densify policy (always)

- Best practical RAM/NVRAM; sparsemem/mmap on when applicable
- Fill every usable Dynamips-supported slot (switch + routed FE + multi-serial where supported; full PA set on c7200)
- Prefer denser adapters; no empty supported slots
- After import, compute Idle-PC and save on the **template** when possible

### Yellow path (template CRUD / idle-pc on template)

The skill does **not** yet expose create/update template tools (see `capability-matrix.md`). To create a new dense template or write idle-pc onto the template:

1. State the gap (yellow)
2. Ask user allow for this action
3. Minimum non-CLI (or human GUI) only after allow

**Done when:** Image is listed; node can be instantiated from a template; Idle-PC computed; template densify/idle-pc either done green, completed under escape, or explicitly deferred with user agreement.

---

## 7. Snapshot / reset clean state

**Goal tool:** `gns3_manage_snapshot`

| Arg | Notes |
|-----|--------|
| `operation` | `create` \| `list` \| `restore` \| `delete_snapshot` \| `delete_project` |
| `project_name` / `project_id` | Resolve project |
| `snapshot_name` / `snapshot_id` | Required for restore/delete_snapshot; name required for create |
| `confirmation_token` | Required for destructive ops after preview |
| `safety_snapshot_name` | Optional name for pre-restore safety snap |

**Token flow (restore / delete_snapshot / delete_project):**

1. Call **without** token → `status: confirmation_required` + `result.confirmation_token` + impact
2. Show impact to user; get explicit yes
3. Re-call with **same** operation + same resolved target + token
4. Restore automatically creates a safety snapshot when possible before restore

Create/list are not token-gated. Create with an existing name reuses that snapshot.

**Expert fallback:**

1. Resolve `project_id`
2. **Create:** `gns3_create_snapshot` with a clear name (e.g. `Clean_State`, `Before_OSPF`)
3. **List:** `gns3_list_snapshots`
4. **Restore:**
   - Confirm with user
   - Prefer `gns3_create_snapshot` safety checkpoint first (name like `pre_restore_<timestamp>`)
   - `gns3_restore_snapshot`
5. **Delete snapshot:** only when user asked; use `gns3_delete_snapshot` (not project delete)
6. **Delete project:** confirm first → `gns3_delete_project`

**Done when:** Snapshot list reflects the request; restore only after confirm (+ safety snap when possible).

---

## 8. Session cleanup (ask first)

**Goal tool:** `gns3_finish_lab`

| Arg | Notes |
|-----|--------|
| `project_name` / `project_id` | Required when `stop_nodes` or `close_project` |
| `stop_nodes` / `close_project` / `stop_server` | All default **false** |
| `confirmation_token` | Required when any flag is true |

**Flow:**

1. Confirm the lab goal for this turn is done.
2. **Ask the user** which of: stop all nodes / close project / stop GNS3 server.
3. Call with explicit flags **without** token → preview (`confirmation_required`) with impact + token.
4. After user yes, re-call with same flags/project/server_url + token.
5. Order when executing: stop_nodes → close_project → stop_server.
6. All-false call returns success with “nothing requested” (use that as a nudge, not a cleanup).
7. Remote `server_url`: stop_server refused — report that; only project steps may apply.
8. Never delete the project as part of “cleanup” (use manage_snapshot `delete_project` only with separate user confirm).

**Expert fallback:**

1. Ask close project? stop server?
2. Only after explicit answers:
   - Close only: `gns3_close_project` **or** `gns3_cleanup_session(project_id=…, close_project=true)`
   - Stop server only: `gns3_stop_server` **or** `gns3_cleanup_session(stop_server=true)`
   - Combined: `gns3_cleanup_session` with flags matching consent
3. Expert cleanup has **no** confirmation tokens — user chat consent is the only gate.

**Done when:** User was asked; accepted actions ran via the skill CLI; declines are respected with no silent close/stop.
