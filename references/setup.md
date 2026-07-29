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
| `GNS3_USERNAME` / `GNS3_PASSWORD` | **Remote-server** override (or one-off kwarg). For a local GNS3 install the skill reads `[Server]` user/password from the local `gns3_server.conf` automatically — do **not** set these for local servers (see “Finding local server credentials” below). Resolution order: `--username`/`--password` kwargs > `GNS3_USERNAME`/`GNS3_PASSWORD` env > local `gns3_server.conf`. |
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
| `GNS3_CONFIRM_TOKEN_STORE` | Override path for the persisted confirm-token store (default: `$XDG_RUNTIME_DIR/gns3-skill/confirm-tokens.json`, fallback `~/.cache/gns3-skill/confirm-tokens.json`, mode 0600) |

API `username`/`password` tool fields / `GNS3_USERNAME` / `GNS3_PASSWORD` are **not** guest console/SSH credentials — they authenticate the GNS3 **server** and, for a local install, are normally sourced automatically from `gns3_server.conf` (don’t set them for local servers). Guest SSH / console credentials are separate; see `GNS3_SSH_*` / `GNS3_CONSOLE_*` above.
Confirmation tokens (for destructive goal ops — `gns3_manage_snapshot` restore/delete, `gns3_finish_lab` with true flags) are **persisted to a local file** so a token issued in one CLI call’s `confirmation_required` preview survives into the follow-up execute call in a separate CLI process. The store is `gns3-skill/confirm-tokens.json` under `XDG_RUNTIME_DIR` (fallback `~/.cache`, mode 0600) and can be overridden with `GNS3_CONFIRM_TOKEN_STORE=<path>`. Tokens are single-use, action+target bound, and TTL-limited (`GNS3_CONFIRM_TOKEN_TTL_SECONDS`, default 600s). Don’t reuse a token after showing impact to the user — re-preview if the target or flags changed.

## Finding local server credentials

When the local GNS3 server has **auth enabled** (`auth = True` under `[Server]` in its config), every REST call — including the `gns3_ensure_server` health probe — must carry `user` / `password`. **The skill reads those automatically from the local `gns3_server.conf`** — no `GNS3_*` env or shell ritual is required or expected for a normal local install. This section documents the file the skill reads, and what to do when the file’s credentials don’t satisfy the probe (missing/unreadable, wrong value, or a remote server with no local file).

### 1. The file the skill reads (no manual step needed)

GNS3 writes the local server’s credentials into `gns3_server.conf` under `[Server]`. The skill reads this file itself at probe time (first readable candidate wins), so for a normal local install you don’t have to do anything — `gns3_ensure_server` / `gns3_prepare_lab` just work.

Config locations the skill searches, in order:

- Linux: `~/.config/GNS3/2.2/gns3_server.conf` (most common)
- macOS: `~/Library/Application Support/GNS3/2.2/gns3_server.conf`
- Windows: `%APPDATA%\GNS3\2.2\gns3_server.conf`
- Bundled/packaged server: `~/Documents/GNS3/embedded/gns3_server.conf`
- Portable/older Linux: `~/GNS3/gns3_server.conf`

Fields the skill consumes from `[Server]`:

| Key | Meaning |
|-----|---------|
| `auth` | `True` → credentials required; the skill uses `user` / `password` from the same file |
| `user` | API username |
| `password` | API password |
| `host` / `port` | used to construct `server_url` when neither `--server_url` nor `GNS3_SERVER_URL` is given |

`server_url` resolution (highest precedence first): `--server_url` kwarg > `GNS3_SERVER_URL` env > `[Server] host:port` from the local conf > default `http://localhost:3080`.

### 2. Diagnostic: confirm what the skill will read (names only, value never echoed)

If a probe returns `401`, verify the file is present and `auth = True`:

```bash
# Print key NAMES only — values are redacted so the secret never lands in transcript history.
conf="$HOME/.config/GNS3/2.2/gns3_server.conf"
awk -F' *= *' '/^\[Server\]/{s=1;next} s && /^(user|password|auth|host|port)[ \t]*=/{print "* "$1": <redacted>"}' "$conf"
# e.g. * auth: <redacted>  * user: <redacted>  * password: <redacted>  * host: <redacted>  * port: <redacted>
```

If the file is missing or its `user`/`password` differ from the server’s actual auth, that is the 401’s root cause — see step 4.

### 3. Remote GNS3 server (no local conf to read): use env / kwargs

For a **remote** server (a host on another machine, no local `gns3_server.conf`), the env vars / kwargs are the supported override:

```bash
# One-off via the CLI (credentials never logged):
CMP=gns3-skill/.venv/bin/python
$CMP gns3-skill/scripts/gns3 gns3_ensure_server \
    --server_url=http://10.0.0.5:3080 --username=ops --password=**** --force=true

# Or export for the whole shell:
export GNS3_SERVER_URL=http://10.0.0.5:3080
export GNS3_USERNAME=ops
export GNS3_PASSWORD=****        # never `echo` this; the value lives only in the process env
$CMP gns3-skill/scripts/gns3 gns3_ensure_server --force=true
```

### 4. If the server has auth off but you still see 401

That is not a credential problem — clear the cached unreachable result and reprobe:

```bash
gns3-skill/.venv/bin/python gns3-skill/scripts/gns3 gns3_ensure_server --force=true
```

(The healthy cache lasts `GNS3_SERVER_HEALTHY_CACHE_SECONDS`, default 30s; `--force=true` bypasses it now.)

### 5. Ask, don’t brute-force

If the local config file is missing/empty, or `auth = True` but its `user`/`password` are blank or rejected by the server, **ask the user once** for the credentials (do not iterate candidate passwords). One lookup (config file) → one ask (if config unavailable) → one retry with the real value. Pass the answer via `--username` / `--password` (or `GNS3_USERNAME` / `GNS3_PASSWORD` for a remote server) and never print the value into the transcript.

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

## Device default credentials

Common GNS3 appliance defaults for console login (pass via `--login_username` / `--login_password` to `gns3_send_console_commands`):

| Appliance | Username | Password | Notes |
|-----------|----------|----------|-------|
| SONiC VS | `admin` | `YourPaSsWoRd` | Default for the official SONiC VS qcow2 image |
| Cisco IOS | — | — | No login by default; optional `enable` secret |
| Cisco IOSv | — | — | Same as IOS |

When in doubt, consult the appliance documentation. Do not guess passwords.

## Skill core

- Rules: `../SKILL.md`
- Playbooks: `playbooks.md`
- Capability matrix: `capability-matrix.md`
