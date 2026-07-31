# Safety contract

These rules are mandatory for every GNS3 lab operation. The CLI interface is defined in [CLI](cli.md); supported and missing capabilities are classified in [Capability matrix](capability-matrix.md).

## CLI-only boundary

Every GNS3 control-plane request, device-console action, and guest-access action must use `gns3 run <operation>`.

Without the escape ritual, do not:

- call the GNS3 REST API directly;
- open a raw Telnet console when a console operation exists;
- open raw guest SSH when a guest operation exists;
- write a Python or shell lab driver that bypasses the CLI;
- invent per-operation scripts or use package tests as an operations path.

Reading files, handling local image/export/capture paths, unrelated shell work, and developing this package are outside that lab-operations boundary.

## Session open

Prefer the goal operation:

```bash
gns3 run prepare_lab --project_name=LAB_NAME
```

It ensures the server, resolves or creates the project as requested, and opens it when configured to do so. For a custom expert path, begin with `gns3 run ensure_server`, then discover and open/create the project through registry operations. Use only IDs returned by those calls.

## ID provenance

`project_id`, `node_id`, `link_id`, `template_id`, `snapshot_id`, and adapter/port coordinates must come from:

1. a result from this CLI in the current work; or
2. an explicit value supplied by the user.

Never fabricate a UUID, recover one from memory, or guess an adapter/port. Resolve names through `list`, `get`, or topology operations before mutation. If the observed name-to-ID mapping is ambiguous, stop and report the conflict.

## Credential sourcing

Server API credentials and device/guest credentials are separate trust planes.

### Local GNS3 server

For a normal local installation, the skill reads the first supported local `gns3_server.conf` and uses the `[Server]` configuration. No environment ritual is required. File locations and connection precedence are documented in [Setup](setup.md).

### Remote GNS3 server

A remote server has no local configuration file to read. Supply its URL and credentials through explicit runtime input or the documented `GNS3_SERVER_URL`, `GNS3_USERNAME`, and `GNS3_PASSWORD` sources. Do not reuse device login credentials as server API credentials.

### Console and guest SSH

Console login may use explicit sensitive fields or `GNS3_CONSOLE_USER` / `GNS3_CONSOLE_PASSWORD`. Guest SSH may use explicit sensitive fields or `GNS3_SSH_USER` / `GNS3_SSH_PASSWORD`. Use `gns3 describe <operation>` to identify the exact sensitive field names; descriptions mark sensitivity but never return values.

### Authentication failures

A server `401` or `403` must produce an auth-classified error with the observed HTTP status. It is not a healthy result.

After the first authentication failure:

1. stop retrying;
2. check the configured local source once, or ask once for the remote credential source;
3. retry the same operation once with the real value.

Never iterate candidate usernames or passwords.

## Secret handling

- Document source and variable names only; never record credential values.
- Never echo secrets to stdout, stderr, logs, examples, transcripts, result envelopes, or registry descriptions.
- Never paste secrets into diagnostic commands or source-controlled files.
- Operation results must not echo server, console, or SSH credential fields.
- Console response bodies contain device command output, not credentials; inspect completion metadata before trusting a response.

## Persisted confirmation tokens

Destructive goal actions use a two-phase handshake. The preview returns `status: confirmation_required` with impact and a token under `result`. This valid preview exits 0 but performs no gated action.

Tokens are:

- persisted in a local, permission-restricted store so a preview token survives into a later CLI process;
- one-shot;
- bound to the exact action and resolved target;
- expiry-limited (default TTL 600 seconds);
- invalid after use, expiry, or any action/target mismatch.

`GNS3_CONFIRM_TOKEN_STORE` selects the persisted store path. `GNS3_CONFIRM_TOKEN_TTL_SECONDS` configures expiry. The default store uses the runtime directory when available, then a user cache location, with a temporary-directory fallback.

For `manage_snapshot`, `restore`, `delete_snapshot`, and `delete_project` are token-gated. Create and list are not. For `finish_lab`, any true cleanup flag is token-gated; an all-false request is inert.

Required flow:

1. call the goal without a token to obtain preview and impact;
2. show the impact to the user and obtain an explicit yes;
3. re-call with the same operation, resolved target, flags, and token;
4. if any target or flag changes, discard the token and preview again.

Expert delete, restore, export, close, and stop operations do not use the goal token handshake. They still require explicit user approval before invocation.

## Cleanup consent and session close

Finishing the task does not imply permission to change lab lifecycle state.

1. Ask separately whether to stop nodes, close the project, and stop the local server.
2. Pass only the flags the user approved to `gns3 run finish_lab`.
3. Show its preview, then obtain final approval before re-calling with the token.
4. Respect a decline by performing no cleanup.
5. Never delete a project as an implied part of cleanup.
6. Never attempt to stop a remote server; remote stop is refused.

## Destructive operations

Explicit approval is required before deleting a project/node/link/snapshot, restoring a snapshot, exporting a project, stopping nodes/server, or closing a project unless the user's current request already unambiguously authorizes that exact change.

Prefer the goal confirmation handshake where available. Preserve evidence of partial mutation, fail-stop on hard errors, and never label partial work successful.

## Escape ritual

The only path outside the CLI is a required capability that is missing, returns a hard failure, or is classified yellow.

1. Name the missing/broken operation or yellow gap and include the relevant error.
2. Ask the user for explicit permission for that exact non-CLI action.
3. After approval, perform only the minimum action needed.
4. State what escaped the CLI in the result.

There is no silent fallback. A green operation failure does not automatically become a yellow path, and yellow classification is not permission by itself.
