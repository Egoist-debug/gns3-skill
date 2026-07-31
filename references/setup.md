# Setup

This reference covers installation, entrypoints, and GNS3 server configuration. Operation discovery and invocation are in [CLI](cli.md); credential and token policy is in [Safety](safety.md).

## Prerequisites

- Python 3.10 or newer
- A reachable GNS3 server, or a local `gns3server` executable that the skill may start
- Package dependencies installed through the project metadata

## Install

From the workspace root:

```bash
python3 -m venv gns3-skill/.venv
gns3-skill/.venv/bin/pip install -e 'gns3-skill[dev]'
```

For a non-development installation:

```bash
pip install -e gns3-skill/
```

The installed console entrypoint is:

```bash
gns3 list
```

> **PATH collision with the GNS3 GUI.** On a workstation that also has the GNS3
> GUI installed, a bare `gns3` may resolve to the GUI binary (`/usr/bin/gns3`)
> instead of this skill's console entrypoint. The GUI aborts headless with a Qt
> "no display" error. Use one of the venv-qualified forms below (or activate the
> venv and ensure its `bin` directory precedes the GUI in `PATH`). Never rely on
> a bare `gns3` when both installations exist.

The repository wrapper and module entrypoint expose the same three-command contract:

```bash
gns3-skill/.venv/bin/python gns3-skill/scripts/gns3 list
gns3-skill/.venv/bin/python -m gns3_skill list
```

Use [CLI](cli.md) for `list`, `describe`, and `run` syntax.

## Server connection configuration

| Variable | Purpose |
| --- | --- |
| `GNS3_SERVER_URL` | GNS3 REST base URL; default is `http://localhost:3080`. |
| `GNS3_USERNAME` / `GNS3_PASSWORD` | Remote-server authentication override. Local installs normally use the local server config. |
| `GNS3_VERIFY_SSL` | Enable or disable TLS certificate verification. |
| `GNS3_TIMEOUT` | REST request timeout. |
| `GNS3_SERVER_START_CMD` | Optional custom command used to start a local server. |
| `GNS3_SERVER_START_TIMEOUT` | Local server startup wait. |
| `GNS3_SERVER_STOP_TIMEOUT` | Local shutdown wait before forced termination. |
| `GNS3_SERVER_HEALTHY_CACHE_SECONDS` | Healthy-probe cache window. |

Resolution precedence is:

1. explicit operation input;
2. environment variable;
3. local `gns3_server.conf` values where applicable;
4. package default.

Server API credentials are distinct from console and guest SSH credentials. Follow [Safety](safety.md) for credential sourcing and secret handling.

## Local server configuration

For a local GNS3 installation, the skill reads the first existing, readable `gns3_server.conf` candidate:

- Linux: `~/.config/GNS3/2.2/gns3_server.conf`
- macOS: `~/Library/Application Support/GNS3/2.2/gns3_server.conf`
- Windows: `%APPDATA%/GNS3/2.2/gns3_server.conf`
- Bundled installation: `~/Documents/GNS3/embedded/gns3_server.conf`
- Portable/older installation: `~/GNS3/gns3_server.conf`

The `[Server]` keys consumed are `auth`, `user`, `password`, `host`, and `port`. Never print their values while diagnosing setup.

When neither explicit input nor environment specifies a URL, `host` and `port` provide the local URL before the default is used. If authentication is enabled, `user` and `password` provide the local API credentials.

## Remote server configuration

A remote server does not use the workstation's local GNS3 configuration. Set the remote URL and source its API credentials through the explicit or environment inputs above. Use only variable names in shared instructions; provide actual values through the user's approved secret-handling path.

The skill probes but never auto-starts or stops a remote server.

## Authentication diagnosis

If an operation returns an auth error with HTTP status `401` or `403`:

1. for local GNS3, verify that one supported config file is readable and its `[Server]` authentication fields match the running server;
2. for remote GNS3, verify the configured remote credential source;
3. retry once with the corrected source.

Do not guess credentials or cycle through candidate values. See [Safety](safety.md) for the mandatory failure procedure.
