# Setup: GNS3 skill (CLI-first)

Primary surface is the **skill CLI** (`scripts/gns3`).

Secrets: set via environment. Document **names only**. Never commit real passwords.

## Prerequisites

- GNS3 server reachable (default `http://127.0.0.1:3080`)
- Python 3.10+
- Deps: `httpx`, `pydantic`, `asyncssh`

```bash
# from workspace root — install skill editable (or use a venv that has deps)
python3 -m venv gns3-skill/.venv && gns3-skill/.venv/bin/pip install -e 'gns3-skill[dev]'
# or
pip install -e gns3-skill/
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `GNS3_SERVER_URL` | REST base URL (default `http://localhost:3080`) |
| `GNS3_USERNAME` / `GNS3_PASSWORD` | GNS3 API auth |
| `GNS3_VERIFY_SSL` | TLS verify (`true`/`false`) |
| `GNS3_SERVER_START_CMD` | Custom localhost start command (optional) |
| `GNS3_SERVER_START_TIMEOUT` | Wait for auto-start (default `30`) |
| `GNS3_SERVER_STOP_TIMEOUT` | Wait after SIGTERM before SIGKILL (default `10`) |
| `GNS3_SERVER_HEALTHY_CACHE_SECONDS` | Skip re-probe window (default `30`) |
| `GNS3_CONSOLE_USER` / `GNS3_CONSOLE_PASSWORD` | Default device console login |
| `GNS3_CONSOLE_READY_TIMEOUT` | Console login readiness budget seconds (default `30`) |
| `GNS3_CONSOLE_MAX_RESPONSE_BYTES` | Per-command console output cap (default `524288`) |
| `GNS3_SSH_USER` / `GNS3_SSH_PASSWORD` | Default guest SSH |
| `GNS3_SSH_HOST_KEY_POLICY` | `accept_new` (default) / `strict` / `warn` |
| `GNS3_SSH_CONNECT_TIMEOUT` | SSH connect readiness budget with retries (default `30`) |
| `GNS3_CONFIRM_TOKEN_TTL_SECONDS` | One-time destructive goal token TTL (default `600`) |

API `username`/`password` tool fields are **not** guest console/SSH credentials.
Confirmation tokens are process-local to the CLI process (not shared across restarts).

## Source layout

Source of truth: `gns3-skill/src/gns3_skill/` — standalone async Python package dispatched via CLI.
CLI lives at `gns3_skill.cli` / `scripts/gns3`.

## Install skill (this monorepo layout)

Canonical skill monofolder:

```text
gns3-skill/
  SKILL.md
  scripts/gns3
  src/gns3_skill/   # library package (source of truth)
  references/
```

Workspace hosts should symlink (not copy):

```bash
# from gns3-test workspace root
ln -sfn ../../gns3-skill .agents/skills/gns3-skill
ln -sfn ../../gns3-skill .omp/skills/gns3-skill
```

Confirm:

```bash
ls -la .agents/skills/gns3-skill .omp/skills/gns3-skill
readlink -f .agents/skills/gns3-skill
```

## CLI usage

```bash
PY=gns3-skill/.venv/bin/python   # or python3 after pip install -e gns3-skill/

$PY gns3-skill/scripts/gns3 list
$PY gns3-skill/scripts/gns3 gns3_prepare_lab --project_name=demo
$PY gns3-skill/scripts/gns3 gns3_list_projects
$PY gns3-skill/scripts/gns3 gns3_list_nodes --json '{"project_id":"<uuid>"}'
```

There is **one** script: `scripts/gns3`. Tool name is the first argument. Do not invent `scripts/gns3_prepare_lab`.

After `pip install -e gns3-skill/`, the console script `gns3` is also available.

## Smoke check

```bash
$PY gns3-skill/scripts/gns3 list | tail -5          # expect Total: 58
$PY gns3-skill/scripts/gns3 gns3_list_projects      # real project list or ensure+list
```

## Skill core

- Rules: `../SKILL.md`
- Playbooks: `playbooks.md`
- Capability matrix: `capability-matrix.md`
