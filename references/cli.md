# CLI contract

The live operation registry is the only catalog of operation IDs, tiers, summaries, parameter schemas, and sensitive fields. Do not maintain or infer an operation inventory from prose.

## Commands

```text
gns3 list [--tier=goal|expert|all]
gns3 describe <operation>
gns3 run <operation> [--key=value ...] [--json '{...}']
```

All commands emit JSON on stdout. Logs and diagnostics belong on stderr. The repository wrapper, installed console command, and module entrypoint expose the same interface; see [Setup](setup.md).

## Registry discovery

```bash
gns3 list
gns3 list --tier=expert
gns3 list --tier=all
gns3 describe prepare_lab
gns3 describe send_console_commands
```

- `list` defaults to `--tier=goal` and returns exactly 8 goal operations.
- `--tier=expert` returns exactly 50 expert operations.
- `--tier=all` returns all 58 operations.
- Canonical identifiers are snake_case and do not carry a redundant prefix.

A successful list result has this registry-derived shape:

```json
{
  "tier": "goal",
  "operations": [
    {"identifier": "prepare_lab", "tier": "goal", "summary": "..."}
  ],
  "total": 8
}
```

A successful describe result has this registry-derived shape:

```json
{
  "identifier": "prepare_lab",
  "tier": "goal",
  "summary": "...",
  "schema": {}
}
```

The machine-readable `schema` identifies required fields, defaults, accepted types, and sensitive fields. Sensitive values are never included. Use `describe` immediately before building non-trivial inputs. An identifier not returned by `list` is not callable.

## Run inputs

### Key/value form

```bash
gns3 run prepare_lab --project_name=LAB_NAME --create_if_missing=true
gns3 run get_project --project_id=PROJECT_ID_FROM_RESULT
```

Each key/value argument is `--key=value`. The CLI coerces scalar strings according to the registry schema, including booleans and numbers. Unknown keys, invalid scalar values, and missing required keys are usage errors.

### JSON option

Use one JSON object for arrays, nested objects, or typed values:

```bash
gns3 run build_topology --json '{"project_name":"LAB_NAME","nodes":[],"links":[]}'
```

`--json` must decode to an object. It may be combined with key/value fields only when their keys do not overlap.

### Standard-input JSON

Standard input may supply one JSON object instead of `--json`:

```bash
printf '%s\n' '{"project_name":"LAB_NAME"}' | gns3 run prepare_lab
```

Input rules are strict:

1. `--json` and standard-input JSON are mutually exclusive.
2. A key present in both JSON and key/value input is rejected; no source silently wins.
3. Repeated key/value keys are rejected.
4. Unknown parameters, missing required parameters, malformed JSON, and schema/type failures are rejected before operation execution.
5. `server_url`, `username`, and `password` are shared runtime inputs owned by the invocation context. Other device/guest credentials remain operation-specific.
6. Sensitive values must come from the sources in [Safety](safety.md); never expose them in examples, logs, or captured output.

## Unified run envelope

Every `run` emits one envelope containing:

- `status`;
- `operation`, the requested canonical registry ID;
- `tier`, either `goal` or `expert` after registry resolution;
- exactly one of `result` or structured `error`.

Successful form:

```json
{
  "status": "success",
  "operation": "prepare_lab",
  "tier": "goal",
  "result": {}
}
```

Failure form:

```json
{
  "status": "error",
  "operation": "prepare_lab",
  "tier": "goal",
  "error": {
    "type": "auth",
    "message": "Authentication failed",
    "details": {"http_status": 401}
  }
}
```

Supported top-level statuses are:

| Status | Meaning |
| --- | --- |
| `success` | Operation completed successfully. |
| `confirmation_required` | Valid preview/confirmation handshake; inspect `result` and follow [Safety](safety.md). |
| `partial` | Some mutation occurred before a later failure. Inspect the result evidence. |
| `conflict` | Observed state conflicts with the requested convergence. |
| `error` | Operation or CLI validation failed. |

Operation-specific data, goal steps, confirmation tokens, impact, and next actions live under `result`. Errors contain `type` and `message`; structured evidence belongs under `error.details`. Top-level `failed` is not valid. Goal step records may still use `failed` to describe an individual step.

CLI usage failures use the same structured error form and exit 2. Known-operation parse/schema errors retain the operation's tier. For an unknown operation, `operation` is the attempted identifier and `tier` is `null` because registry resolution failed. `list` and `describe` usage failures use structured CLI errors without pretending an operation was resolved.

## Exit codes

| Exit | Meaning | Statuses/cases |
| --- | --- | --- |
| `0` | Valid success or expected confirmation handshake | `success`, `confirmation_required`; successful `list`/`describe` |
| `1` | Operation-level non-success | `partial`, `conflict`, `error` |
| `2` | CLI usage or schema failure | Unknown command/operation, malformed or conflicting inputs, missing/unknown parameters |

A confirmation preview deliberately exits 0: it is expected control flow, not completed destructive work. Inspect `status` rather than treating exit 0 alone as proof that a mutation ran.

## Rejected forms

Only `list`, `describe`, and `run` are public commands. Direct operation dispatch, aliases, per-operation script files, and identifiers not returned by the registry are usage errors with exit 2.
