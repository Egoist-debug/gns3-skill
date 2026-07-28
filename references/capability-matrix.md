# GNS3 skill capability matrix

Legend:

| Color | Meaning |
|-------|---------|
| **Green** | Fully doable via skill CLI tools. No escape. |
| **Yellow** | Partial CLI path. Finish only via escape ritual (name gap → user allow → minimum non-CLI). |
| **Red** | Not an agent ops path via this skill CLI. Human / external process. |

Tool names below are canonical `gns3_*`.

**Inventory:** 58 tools = **8 goal** + **50 expert** (see `SKILL.md` expert table). Prefer goals for playbooks.

## Green — CLI-only

| Job | Primary tools |
|-----|----------------|
| **Playbook goal tools (preferred)** | `gns3_prepare_lab`, `gns3_build_topology`, `gns3_configure_devices`, `gns3_diagnose_connectivity`, `gns3_run_guest_commands`, `gns3_prepare_image`, `gns3_manage_snapshot`, `gns3_finish_lab` (tokens for destructive snapshot ops + any finish flags; densify still yellow inside prepare_image) |
| Probe / auto-start local GNS3 | `gns3_ensure_server`, `gns3_get_server_info`, `gns3_list_computes` |
| Stop local GNS3 server / session cleanup | `gns3_stop_server` (localhost port → SIGTERM→SIGKILL), `gns3_cleanup_session` / `gns3_finish_lab` (optional stop_nodes / close_project / stop_server; **ask user first**; finish_lab needs token when flags true) |
| Project lifecycle | `gns3_list_projects`, `gns3_create_project`, `gns3_get_project`, `gns3_update_project`, `gns3_open_project`, `gns3_close_project`, `gns3_duplicate_project`, `gns3_delete_project`, `gns3_save_project`, `gns3_export_project` |
| List / add / control nodes | `gns3_list_nodes`, `gns3_add_node`, `gns3_get_node`, `gns3_update_node`, `gns3_delete_node`, `gns3_start_node`, `gns3_stop_node`, `gns3_suspend_node`, `gns3_reload_node`, `gns3_duplicate_node`, `gns3_start_all_nodes`, `gns3_stop_all_nodes` |
| Links | `gns3_list_links`, `gns3_add_link`, `gns3_delete_link` |
| Topology overview / validation | `gns3_get_topology`, `gns3_validate_topology` |
| Device CLI | `gns3_send_console_commands`, `gns3_get_node_config` |
| Built-in config templates (expert) | `gns3_apply_config_template` (ospf, eigrp, bgp, vlan, dhcp, ssh, …) |
| Goal workflow config templates | `gns3_configure_devices` with `template_name` ∈ `basic_router` \| `interface` \| `vlan` \| `ospf` |
| Bulk CLI push | `gns3_bulk_configure_nodes` |
| Guest shell over SSH | `gns3_ssh_exec`, `gns3_run_guest_commands` |
| List device templates / appliances | `gns3_list_templates`, `gns3_list_appliances` |
| Image store list / upload | `gns3_list_images`, `gns3_import_image`, `gns3_prepare_image` (import path) |
| Compute Idle-PC on a node | `gns3_get_idle_pc_values`, optional via `gns3_prepare_image` idle-pc fields |
| Snapshots | `gns3_list_snapshots`, `gns3_create_snapshot`, `gns3_restore_snapshot`, `gns3_delete_snapshot`, `gns3_manage_snapshot` |
| Packet capture | `gns3_start_capture`, `gns3_stop_capture` |
| Canvas annotations | `gns3_add_text_annotation`, `gns3_add_shape` |
| Export project archive | `gns3_export_project` (confirm with user first) |

## Yellow — CLI + possible escape

| Job | CLI can | Gap | Escape only after ritual |
|-----|---------|-----|---------------------------|
| Dynamips / IOS **template create** | Upload image (`gns3_import_image` / `gns3_prepare_image`); list images/templates | No tool for `create_template` (client has REST helper) | Create template via allowed non-CLI **after user allow**, or human uses GNS3 GUI |
| **Densify** template slots (fill WIC/NM/PA) | `gns3_update_node` for **instances**; list templates; `densify_template=true` on prepare_image returns yellow note only | No tool to update **template** properties / slot maps | Persist densify on template only with allow; prefer densifying documented defaults in GUI/template JSON with allow |
| **Idle-PC on template** | `gns3_get_idle_pc_values` on a running Dynamips **node** | No tool to write idle-pc onto the **template** record | Apply computed value to template with allow, or leave on node only |
| Appliance install from `.gns3a` | List appliances | No dedicated “install appliance file” tool | Install via GNS3 UI/CLI with allow if required for the task |
| Docker image pull | N/A | `gns3_import_image` / prepare_image reject `docker` | Use Docker tooling with allow if the lab needs it |

Policy for yellow: still **ask once** (escape ritual). Do not treat yellow as silent permission.

## Red — not skill agent ops

| Job | Notes |
|-----|--------|
| Drive lab by running ad-hoc REST scripts or raw curl | Forbidden as ops path (see skill hard rules) |
| Reverse-engineer GNS3 GUI automation | Out of scope |
| Change GNS3 server source / reinstall GNS3 OS packages | Human / infra |
| Non-GNS3 networks (physical lab, cloud VPC) | Different tools |

## Authorization notes (green tools, still gated)

| Action | Gate |
|--------|------|
| Goal destructive snapshot ops (`restore`, `delete_snapshot`, `delete_project`) | `confirmation_required` + one-time token after user yes |
| Goal finish_lab with any true flag | Same token gate; all-false is inert |
| Expert `gns3_delete_project`, `gns3_restore_snapshot`, `gns3_export_project`, stop/close | User confirm in chat (no token field) |
| Expert node/link delete | OK when user asked for that topology change |

## Densify / Idle-PC (agent-facing)

From workspace policy (also in root `AGENTS.md`):

- Prefer best practical RAM/NVRAM; sparsemem/mmap on where applicable.
- Fill every usable Dynamips-supported slot with high-density lab modules.
- After import, compute Idle-PC and **save it on the template** when possible.

**Green portion:** `gns3_import_image` / `gns3_prepare_image` → add node from an **existing** template → `gns3_get_idle_pc_values`.  
**Yellow portion:** creating/updating the template document itself (slots + idle-pc field) until those tools exist.

## When a green tool fails

That is not “yellow.” Run the escape ritual with the **error text**, or fix preconditions via other green tools (ensure server, open project, start node, correct ports). Do not invent a Python client because one call failed.
