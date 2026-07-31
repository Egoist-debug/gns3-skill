---
name: gns3-skill
description: "CLI-first GNS3 lab operations. Use for any GNS3 project, topology, node, link, image, snapshot, device console, guest SSH, Dynamips, QEMU, or SONiC task, and before attempting GNS3 REST, raw console/SSH, or an ad-hoc lab driver."
---

# GNS3 Skill

Operate GNS3 only through this skill's registry-backed CLI. Do not replace it with hand-written REST, Telnet, SSH, or Python automation.

## Interface

```bash
gns3 list
gns3 describe prepare_lab
gns3 run prepare_lab --project_name=LAB_NAME
```

The only public commands are:

```text
gns3 list [--tier=goal|expert|all]
gns3 describe <operation>
gns3 run <operation> [--key=value ...] [--json '{...}']
```

`list` defaults to goal operations. Operation IDs never have a redundant prefix. Use [CLI reference](references/cli.md) for discovery, schemas, input rules, envelopes, and exits.

## Hard safety invariants

1. **CLI only.** Every GNS3 control-plane, device-console, or guest-access action goes through `gns3 run`. Raw REST, Telnet, SSH, per-operation scripts, and ad-hoc lab drivers are forbidden unless the escape ritual is completed.
2. **Goal first.** Prefer goal operations for standard lab work. Discover expert operations only when a goal does not fit.
3. **Provenance-only IDs.** IDs and adapter/port coordinates must come from the user or a prior CLI result. Never invent, remember, or infer a UUID.
4. **Protect secrets.** Use documented credential sources; never guess, print, log, return, or place secret values in examples. After one authentication failure, find the configured source once and retry only with the real value.
5. **Confirm destructive actions.** Show the preview/impact, obtain explicit user approval, then use the persisted, one-shot, scoped confirmation token with the unchanged target.
6. **Ask before cleanup.** Never stop nodes, close a project, or stop the server without explicit user intent. Cleanup defaults remain inert.
7. **No silent fallback.** A missing/broken operation or documented yellow gap requires the escape ritual: name the gap and error, ask permission for that exact action, then do only the minimum approved non-CLI work.

The authoritative session, credential, confirmation, cleanup, ID, and escape rules are in [Safety](references/safety.md).

## Goal-first loop

1. Open the session: `gns3 run prepare_lab ...`
2. Build or converge topology: `gns3 run build_topology ...`
3. Configure devices or guests: `gns3 run configure_devices ...` or `gns3 run run_guest_commands ...`
4. Verify or diagnose: `gns3 run diagnose_connectivity ...`
5. Checkpoint when needed: `gns3 run manage_snapshot ...`
6. When the work is complete, ask what cleanup is wanted; preview and confirm through `gns3 run finish_lab ...`

Use `gns3 describe <operation>` before composing non-trivial input. See [Playbooks](references/playbooks.md) for the full goal-first procedures.

## Progressive disclosure

| Need | Load |
| --- | --- |
| Discover operations, inspect schemas, invoke, parse results | [CLI](references/cli.md) |
| Sessions, IDs, credentials, secrets, confirmation, cleanup, escape | [Safety](references/safety.md) |
| Install entrypoints or configure a local/remote server | [Setup](references/setup.md) |
| Follow a common goal-first lab recipe | [Playbooks](references/playbooks.md) |
| Check supported capability or a yellow/red gap | [Capability matrix](references/capability-matrix.md) |
