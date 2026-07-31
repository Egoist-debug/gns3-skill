# Goal-first playbooks

Use the registry for current operation metadata:

```bash
gns3 list
gns3 describe prepare_lab
gns3 list --tier=expert
```

Prefer a goal operation for each complete job. Use expert operations only for a partial/custom path, and resolve their exact schemas with `gns3 describe <operation>`. IDs must come from the user or prior CLI results. All safety gates in [Safety](safety.md) remain mandatory.

## 1. Bootstrap a lab

**Goal operation:** `prepare_lab`

```bash
gns3 describe prepare_lab
gns3 run prepare_lab --project_name=LAB_NAME
```

Use `project_name` when the goal may create the project; use a provenance-backed `project_id` for a known target. The goal ensures the server, resolves or creates the project as requested, and opens it when configured.

For a custom path, invoke `gns3 run ensure_server`, then discover through `gns3 run list_projects`; invoke `gns3 run open_project` for the returned project ID or `gns3 run create_project` for the agreed name. Invoke `gns3 run list_templates` before any template-backed node creation.

**Done when:** the server is healthy, the intended project is open, and its ID is present in a CLI result.

## 2. Build or converge topology

**Goal operation:** `build_topology`

```bash
gns3 describe build_topology
gns3 run build_topology --json '{"project_name":"LAB_NAME","nodes":[],"links":[]}'
```

Supply desired nodes and links as JSON. Node names must be unique in the request. Link endpoints may use names for goal resolution; explicit adapter and port must be supplied together. Existing matching nodes/links are reused, while a same-name node with a conflicting template is a conflict rather than an implicit replacement.

For a custom path:

1. invoke `gns3 run list_templates` and choose a returned template ID;
2. invoke `gns3 run add_node` for each requested device;
3. invoke `gns3 run list_nodes` to map names to returned node IDs;
4. invoke `gns3 run get_node` when port metadata is needed, then `gns3 run add_link`;
5. invoke `gns3 run get_topology` or `gns3 run list_links`, then `gns3 run validate_topology` to verify;
6. invoke `gns3 run start_node` or `gns3 run start_all_nodes` only when requested.

**Done when:** the observed topology matches the request, or a structured conflict/partial result precisely identifies why it does not.

## 3. Configure devices

**Goal operation:** `configure_devices`

```bash
gns3 describe configure_devices
gns3 run configure_devices --json '{"project_name":"LAB_NAME","targets":[{"node_name":"NODE_NAME","commands":["SHOW_COMMAND"]}]}'
```

Each target selects a node by name or provenance-backed ID and supplies either commands or a supported workflow template with parameters. Keep credentials out of examples and use the sources documented in [Safety](safety.md). Verify command completion metadata; do not treat incomplete console framing as success.

For a custom path, invoke `gns3 run list_nodes` to resolve an ID, start the node if necessary, then choose one registry operation:

- `gns3 run apply_config_template` for a built-in template;
- `gns3 run send_console_commands` for user-provided device CLI;
- `gns3 run bulk_configure_nodes` for repeated configuration across several nodes.

Verify with `gns3 run get_node_config` or read-only commands through `gns3 run send_console_commands`.

**Done when:** observed output or configuration matches the requested intent.

## 4. Diagnose connectivity

**Goal operation:** `diagnose_connectivity`

```bash
gns3 describe diagnose_connectivity
gns3 run diagnose_connectivity --project_name=LAB_NAME
```

The goal validates topology and optionally probes explicitly named suspect nodes. A probe may start a stopped suspect node and reports that mutation; it never applies configuration or topology remediation silently.

For a custom path, invoke `gns3 run validate_topology`, inspect `gns3 run list_nodes` and `gns3 run get_topology`, and use `gns3 run send_console_commands` for vendor-appropriate read-only checks. When packet evidence is required, invoke `gns3 run start_capture` on a provenance-backed link and later `gns3 run stop_capture`. Apply a requested fix through registry operations, then repeat the same probes.

**Done when:** the root cause is supported by CLI evidence and any requested fix has been re-verified.

## 5. Run guest commands

**Goal operation:** `run_guest_commands`

```bash
gns3 describe run_guest_commands
gns3 run run_guest_commands --json '{"host":"HOST_FROM_USER_OR_RESULT","commands":["SHELL_COMMAND"]}'
```

Use an explicit host supplied by the user or resolve it from project/node metadata. Source SSH credentials as described in [Safety](safety.md); results must never echo them.

For a custom path, resolve and start the node, then invoke `gns3 run ssh_exec`. Use `gns3 run send_console_commands` only when the device console is the correct access plane, not as an unannounced SSH fallback.

**Done when:** command results answer the request and each command's completion/error state is accounted for.

## 6. Prepare an image and Idle-PC

**Goal operation:** `prepare_image`

```bash
gns3 describe prepare_image
gns3 run prepare_image --source_path=IMAGE_PATH --emulator=dynamips
```

The green path imports supported QEMU, Dynamips, or IOU images. For Dynamips, it may compute Idle-PC against a resolved node. Docker image pull, template creation/update, template densification, and writing Idle-PC to a template remain yellow gaps; see [Capability matrix](capability-matrix.md).

For a custom green path, invoke `gns3 run import_image`, verify with `gns3 run list_images`, select an existing template through `gns3 run list_templates`, instantiate through `gns3 run add_node`, start it when needed, and invoke `gns3 run get_idle_pc_values`.

For any yellow step, complete the escape ritual before using a non-CLI path.

**Done when:** the image is observed in the store and any requested green Idle-PC work is evidenced; yellow work is approved and minimal or explicitly deferred.

## 7. Manage snapshots or reset state

**Goal operation:** `manage_snapshot`

```bash
gns3 describe manage_snapshot
gns3 run manage_snapshot --operation=list --project_name=LAB_NAME
```

Create and list are not token-gated. Restore, snapshot deletion, and project deletion require the persisted confirmation flow:

1. run the exact request without a token;
2. inspect and show the `confirmation_required` impact;
3. obtain explicit user approval;
4. re-run the unchanged request with its token.

A restore should preserve a safety snapshot when possible. If the target, requested action, or flags change, discard the token and preview again.

For a custom expert path, run the matching snapshot operation only after any required user confirmation. Prefer a safety checkpoint before restore. Project deletion is separate from snapshot deletion and never implied.

**Done when:** a fresh list result reflects the request and every destructive action has approval evidence.

## 8. Finish a lab session

**Goal operation:** `finish_lab`

```bash
gns3 describe finish_lab
gns3 run finish_lab --project_name=LAB_NAME --stop_nodes=true --close_project=true
```

All cleanup flags default false. Before the preview, ask the user separately whether to stop nodes, close the project, and stop the local server. Pass only approved flags. Any true flag returns a confirmation preview first; show the impact, obtain final approval, then repeat the unchanged call with the token.

Execution order is stop nodes, close project, then stop server. Remote server stop is refused. Project deletion is never cleanup.

For a custom expert path, use `gns3 run close_project`, `gns3 run stop_server`, or `gns3 run cleanup_session` only for the exact actions already approved. Expert cleanup has no token handshake; user consent remains the gate.

**Done when:** accepted lifecycle changes are observed and declined actions remain untouched.

## Result handling

Every run uses the unified envelope in [CLI](cli.md). Read `status`, not exit code alone: `confirmation_required` exits 0 but is only a preview. A `partial` result means prior mutation evidence must be reported before attempting anything else. A `conflict` requires resolving observed-versus-requested state rather than forcing replacement.
