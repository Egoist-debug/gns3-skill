"""SSH client helpers for guest VM command execution."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import asyncssh

logger = logging.getLogger(__name__)

_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)


def resolve_ssh_credentials(
    ssh_username: Optional[str] = None,
    ssh_password: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve SSH credentials: explicit args override env."""
    user = ssh_username if ssh_username is not None else os.environ.get("GNS3_SSH_USER")
    password = ssh_password if ssh_password is not None else os.environ.get("GNS3_SSH_PASSWORD")
    return user, password


def resolve_console_credentials(
    login_username: Optional[str] = None,
    login_password: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve console login credentials: explicit args override env."""
    user = (
        login_username
        if login_username is not None
        else os.environ.get("GNS3_CONSOLE_USER")
    )
    password = (
        login_password
        if login_password is not None
        else os.environ.get("GNS3_CONSOLE_PASSWORD")
    )
    return user, password


def resolve_host_key_policy(policy: Optional[str] = None) -> str:
    raw = policy if policy is not None else os.environ.get("GNS3_SSH_HOST_KEY_POLICY")
    value = (raw or "accept_new").strip().lower()
    if value not in {"accept_new", "strict", "warn"}:
        return "accept_new"
    return value

DEFAULT_CONNECT_TIMEOUT = 30.0


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def default_connect_timeout() -> float:
    """Total SSH connect readiness budget in seconds."""
    return _env_float("GNS3_SSH_CONNECT_TIMEOUT", DEFAULT_CONNECT_TIMEOUT)


def _is_transient_ssh_error(exc: BaseException) -> bool:
    """True when the failure looks like guest not ready yet (not auth)."""
    if isinstance(exc, asyncssh.PermissionDenied):
        return False
    # asyncssh uses several connection error types; treat most connect-time
    # failures as transient readiness issues.
    name = type(exc).__name__
    if name in {
        "ConnectionLost",
        "ConnectionRefused",
        "ConnectError",
        "TimeoutError",
        "DisconnectError",
    }:
        return True
    if isinstance(exc, (TimeoutError, ConnectionError, OSError, asyncio.TimeoutError)):
        return True
    msg = str(exc).lower()
    transient_bits = (
        "connection refused",
        "timed out",
        "timeout",
        "connection reset",
        "network is unreachable",
        "no route to host",
        "temporarily unavailable",
        "connection lost",
        "connect call failed",
    )
    return any(b in msg for b in transient_bits)



class _WarnHostKeyClient(asyncssh.SSHClient):
    """Warn policy: accept any key but log a warning with fingerprint."""

    def validate_host_public_key(self, host: str, addr: str, port: int, key) -> bool:
        try:
            fp = key.get_fingerprint()
        except Exception:
            fp = "unknown"
        logger.warning(
            "SSH host key not in known_hosts; accepting with warn policy host=%s addr=%s port=%s fingerprint=%s",
            host,
            addr,
            port,
            fp,
        )
        return True

    def validate_host_ca_key(self, host: str, addr: str, port: int, key) -> bool:
        try:
            fp = key.get_fingerprint()
        except Exception:
            fp = "unknown"
        logger.warning(
            "SSH host CA key not in known_hosts; accepting with warn policy host=%s fingerprint=%s",
            host,
            fp,
        )
        return True


def _connect_kwargs_for_policy(policy: str) -> Dict[str, Any]:
    """Build asyncssh.connect kwargs for a named host-key policy.

    asyncssh host-key gates:
    - ``known_hosts is None`` → ``_trusted_host_keys is None`` → accept any key,
      and ``validate_host_public_key`` is **not** called.
    - ``known_hosts`` omitted / ``()`` → load default ``~/.ssh/known_hosts``.
    - ``known_hosts=b''`` → empty trusted set → unknown keys call the client
      validator (used for ``warn`` so we can log fingerprints).

    Policies:
    - ``strict``: default known_hosts files; unknown keys fail.
    - ``accept_new``: no known_hosts check; accept any key (lab default).
    - ``warn``: accept any key, but log host/fingerprint via client validator.
    """
    if policy == "strict":
        # Load default known_hosts; do not override validator
        return {}
    if policy == "warn":
        return {
            "known_hosts": b"",
            "client_factory": _WarnHostKeyClient,
        }
    # accept_new (default): skip known_hosts entirely
    return {
        "known_hosts": None,
    }


def extract_ips_from_node(node: Dict[str, Any]) -> List[str]:
    """Best-effort IPv4 harvest from a GNS3 node document."""
    found: List[str] = []
    seen = set()

    def _add(value: Any) -> None:
        if value is None:
            return
        text = str(value)
        for m in _IPV4_RE.findall(text):
            if m.startswith("127.") or m == "0.0.0.0":
                continue
            if m not in seen:
                seen.add(m)
                found.append(m)

    if not isinstance(node, dict):
        return found

    props = node.get("properties") or {}
    if isinstance(props, dict):
        for key in (
            "ip_address",
            "ip",
            "ipv4",
            "management_ip",
            "host",
            "hostname",
        ):
            _add(props.get(key))
        for key, val in props.items():
            if "ip" in str(key).lower():
                _add(val)

    for key in ("name", "console_host", "host", "hostname"):
        # console_host is usually the GNS3 host, not the guest — skip pure console_host
        if key == "console_host":
            continue
        _add(node.get(key))

    # Nested scrapes
    for val in node.values():
        if isinstance(val, (str, int)):
            _add(val)
        elif isinstance(val, dict):
            for v in val.values():
                _add(v)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    for v in item.values():
                        _add(v)
                else:
                    _add(item)

    return found


async def exec_commands(
    host: str,
    commands: List[str],
    *,
    port: int = 22,
    username: Optional[str] = None,
    password: Optional[str] = None,
    stop_on_error: bool = True,
    host_key_policy: str = "accept_new",
    connect_timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Run commands over SSH on one connection.

    ``connect_timeout`` is the **total readiness budget** (default ~30s via
    code default or ``GNS3_SSH_CONNECT_TIMEOUT``), not a single-attempt only
    login timeout. Transient connect failures are retried until the budget is
    exhausted. ``PermissionDenied`` is never retried.

    Passwords are never included in the returned structure.
    """
    if not host:
        return {"status": "error", "error": "host is required"}
    if not username:
        return {"status": "error", "error": "ssh_username is required (or set GNS3_SSH_USER)"}
    if password is None:
        return {"status": "error", "error": "ssh_password is required (or set GNS3_SSH_PASSWORD)"}
    if not commands:
        return {"status": "error", "error": "commands must be a non-empty list"}

    policy = resolve_host_key_policy(host_key_policy)
    results: List[Dict[str, Any]] = []
    budget = (
        float(connect_timeout)
        if connect_timeout is not None
        else default_connect_timeout()
    )
    budget = max(0.5, budget)
    deadline = time.monotonic() + budget
    backoff = 0.5
    last_err: Optional[BaseException] = None
    attempts = 0

    while True:
        attempts += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0 and attempts > 1:
            break
        per_attempt = min(10.0, max(0.5, remaining if remaining > 0 else budget))
        conn_kwargs: Dict[str, Any] = {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "login_timeout": per_attempt,
        }
        conn_kwargs.update(_connect_kwargs_for_policy(policy))

        try:
            async with asyncssh.connect(**conn_kwargs) as conn:
                for cmd in commands:
                    try:
                        result = await conn.run(cmd, check=False)
                        exit_code = int(
                            result.exit_status if result.exit_status is not None else -1
                        )
                        entry = {
                            "command": cmd,
                            "stdout": result.stdout or "",
                            "stderr": result.stderr or "",
                            "exit_code": exit_code,
                        }
                        results.append(entry)
                        if stop_on_error and exit_code != 0:
                            return {
                                "status": "error",
                                "host": host,
                                "authenticated": True,
                                "username": username,
                                "results": results,
                                "connect_attempts": attempts,
                                "error": f"Command failed with exit_code={exit_code}: {cmd}",
                            }
                    except Exception as e:
                        results.append(
                            {
                                "command": cmd,
                                "stdout": "",
                                "stderr": str(e),
                                "exit_code": -1,
                            }
                        )
                        if stop_on_error:
                            return {
                                "status": "error",
                                "host": host,
                                "authenticated": True,
                                "username": username,
                                "results": results,
                                "connect_attempts": attempts,
                                "error": f"Command execution failed: {e}",
                            }
            return {
                "status": "success",
                "host": host,
                "authenticated": True,
                "username": username,
                "results": results,
                "connect_attempts": attempts,
            }
        except asyncssh.PermissionDenied:
            logger.error("SSH authentication failed for user %s@%s", username, host)
            return {
                "status": "error",
                "host": host,
                "authenticated": False,
                "username": username,
                "results": results,
                "connect_attempts": attempts,
                "error": "SSH authentication failed",
            }
        except Exception as e:
            last_err = e
            if not _is_transient_ssh_error(e):
                logger.error("SSH connection failed to %s: %s", host, e)
                return {
                    "status": "error",
                    "host": host,
                    "authenticated": False,
                    "username": username,
                    "results": results,
                    "connect_attempts": attempts,
                    "error": f"SSH connection failed: {e}",
                }
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sleep_for = min(backoff, remaining)
            logger.info(
                "SSH not ready on %s (attempt %s): %s; retry in %.1fs",
                host,
                attempts,
                e,
                sleep_for,
            )
            await asyncio.sleep(sleep_for)
            backoff = min(backoff * 2, 4.0)

    logger.error("SSH connection failed to %s after %s attempts: %s", host, attempts, last_err)
    return {
        "status": "error",
        "host": host,
        "authenticated": False,
        "username": username,
        "results": results,
        "connect_attempts": attempts,
        "error": f"SSH connection failed after {attempts} attempt(s): {last_err}",
    }
