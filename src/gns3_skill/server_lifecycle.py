"""GNS3 server probe / auto-start lifecycle helpers."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import signal
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ._env import env_float

import httpx

from .gns3_client import GNS3Config, _env_bool

logger = logging.getLogger(__name__)

# Keep a single default owner: GNS3Config.server_url Field default.
DEFAULT_SERVER_URL = "http://localhost:3080"
DEFAULT_START_CMD = "gns3server"
DEFAULT_START_TIMEOUT = 30.0
DEFAULT_HEALTHY_CACHE_SECONDS = 30.0
DEFAULT_PROBE_TIMEOUT = 3.0
DEFAULT_STOP_TIMEOUT = 10.0

_lock = asyncio.Lock()
# url -> (monotonic_ts, server_info)
_healthy_cache: Dict[str, Tuple[float, Any]] = {}




def normalize_server_url(server_url: Optional[str]) -> str:
    """Normalize server URL; share default/env fallback with GNS3Config."""
    if server_url is not None and str(server_url).strip():
        return str(server_url).strip().rstrip("/")
    # GNS3Config owns GNS3_SERVER_URL + default base URL
    return GNS3Config.from_env().server_url.rstrip("/")


def is_local_server_url(server_url: str) -> bool:
    """Return True if server_url targets this machine's loopback interface."""
    parsed = urlparse(server_url if "://" in server_url else f"http://{server_url}")
    host = (parsed.hostname or "").lower()
    if not host:
        return True
    return host in {"localhost", "127.0.0.1", "::1"}


def _parse_host_port(server_url: str) -> Tuple[str, int]:
    parsed = urlparse(server_url if "://" in server_url else f"http://{server_url}")
    host = parsed.hostname or "127.0.0.1"
    if host == "localhost":
        host = "127.0.0.1"
    port = parsed.port or 3080
    return host, port


def build_start_command(server_url: str) -> list[str]:
    """Build argv for starting gns3server.

    Custom GNS3_SERVER_START_CMD is never rewritten.
    Bare default ``gns3server`` gets --host/--port from server_url.
    """
    raw = os.environ.get("GNS3_SERVER_START_CMD")
    custom = raw is not None and raw.strip() != ""
    if custom:
        return shlex.split(raw.strip())

    argv = shlex.split(DEFAULT_START_CMD)
    host, port = _parse_host_port(server_url)
    if len(argv) == 1 and os.path.basename(argv[0]) == DEFAULT_START_CMD:
        argv.extend(["--host", host, "--port", str(port)])
    return argv


async def probe_server(
    server_url: str,
    *,
    username: Optional[str] = None,
    password: Optional[str] = None,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> Tuple[bool, Any]:
    """Probe GNS3 ``/v2/version``.

    Returns (ok, payload_or_error_str).

    Any HTTP response (including 401/403) means the process is up and listening.
    Connection errors mean the server is down.
    """
    base = normalize_server_url(server_url)
    url = f"{base}/v2/version"
    auth = None
    if username and password:
        auth = (username, password)
    try:
        async with httpx.AsyncClient(
            timeout=timeout, verify=_env_bool("GNS3_VERIFY_SSL", True)
        ) as client:
            resp = await client.get(url, auth=auth)
            if resp.status_code == 200:
                try:
                    return True, resp.json()
                except Exception:
                    return True, {"raw": resp.text}
            # Server is reachable but rejected the request (auth, etc.).
            # Treat 401/403 as a credential problem, not healthy: the healthy cache
            # must not store a 401 body, and the agent must be pointed at the real
            # local credentials rather than loop retries. See references/setup.md
            # "Finding local server credentials".
            if resp.status_code in (401, 403):
                creds_hint = (
                    "GNS3 API authentication required (HTTP "
                    f"{resp.status_code}). For a local GNS3 server the skill already "
                    "reads [Server] user/password from the local gns3_server.conf "
                    "(see references/setup.md \"Finding local server credentials\"; "
                    "Linux: ~/.config/GNS3/2.2/gns3_server.conf under [Server]). This "
                    "401 means that file is missing/unreadable, or its user/password "
                    "is wrong: check the file once and retry the same call. For a "
                    "remote server, pass --username / --password (or GNS3_USERNAME / "
                    "GNS3_PASSWORD). Do not guess passwords."
                )
                return False, {
                    "reachable": True,
                    "http_status": resp.status_code,
                    "detail": (resp.text or "")[:200],
                    "error": creds_hint,
                }
            # Other non-200 from a reachable server (5xx, etc.).
            return True, {
                "reachable": True,
                "http_status": resp.status_code,
                "detail": (resp.text or "")[:200],
            }
    except Exception as e:
        return False, str(e)


def _cache_ttl() -> float:
    return env_float("GNS3_SERVER_HEALTHY_CACHE_SECONDS", DEFAULT_HEALTHY_CACHE_SECONDS)


def _start_timeout() -> float:
    return env_float("GNS3_SERVER_START_TIMEOUT", DEFAULT_START_TIMEOUT)


def _cache_get(server_url: str) -> Optional[Any]:
    key = normalize_server_url(server_url)
    entry = _healthy_cache.get(key)
    if not entry:
        return None
    ts, info = entry
    if time.monotonic() - ts <= _cache_ttl():
        return info
    _healthy_cache.pop(key, None)
    return None


def _cache_set(server_url: str, info: Any) -> None:
    _healthy_cache[normalize_server_url(server_url)] = (time.monotonic(), info)


def clear_healthy_cache(server_url: Optional[str] = None) -> None:
    """Drop healthy cache for one URL, or the whole process cache when None."""
    if server_url is None:
        _healthy_cache.clear()
        return
    _healthy_cache.pop(normalize_server_url(server_url), None)


def _stop_timeout() -> float:
    return env_float("GNS3_SERVER_STOP_TIMEOUT", DEFAULT_STOP_TIMEOUT)


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* exists (and is killable by this user)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we cannot signal it.
        return True
    except OSError:
        return False


def _parse_ss_pids(ss_output: str) -> List[int]:
    """Extract unique PIDs from ``ss -lptn`` users fields (``pid=123,``)."""
    pids: List[int] = []
    seen: set[int] = set()
    for match in re.finditer(r"pid=(\d+)", ss_output or ""):
        pid = int(match.group(1))
        if pid not in seen:
            seen.add(pid)
            pids.append(pid)
    return pids


def _pids_listening_on_port(port: int) -> List[int]:
    """Return PIDs with a TCP LISTEN socket on *port* (local machine).

    Prefers ``ss``; falls back to ``fuser`` then ``lsof``.
    """
    # ss: parse users:(("gns3server",pid=123,fd=...))
    try:
        proc = subprocess.run(
            ["ss", "-lptn", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0 or proc.stdout:
            pids = _parse_ss_pids(proc.stdout)
            if pids:
                return pids
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("ss listen lookup failed: %s", e)

    # fuser -n tcp PORT → "PORT/tcp:  123 456"
    try:
        proc = subprocess.run(
            ["fuser", "-n", "tcp", str(port)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        # fuser writes PIDs to stderr on many distros
        blob = f"{proc.stdout or ''} {proc.stderr or ''}"
        pids = [int(x) for x in re.findall(r"\b(\d+)\b", blob)]
        # Drop the port number itself if present as a token
        pids = [p for p in pids if p != port and p > 0]
        # unique preserve order
        out: List[int] = []
        seen: set[int] = set()
        for p in pids:
            if p not in seen:
                seen.add(p)
                out.append(p)
        if out:
            return out
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("fuser listen lookup failed: %s", e)

    try:
        proc = subprocess.run(
            ["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        out = []
        seen = set()
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line.isdigit():
                continue
            pid = int(line)
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
        return out
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("lsof listen lookup failed: %s", e)

    return []


def _signal_pid(pid: int, sig: signal.Signals) -> str:
    """Send *sig* to *pid*. Returns exited|signaled|gone|error:..."""
    if not _pid_alive(pid):
        return "gone"
    try:
        os.kill(pid, sig)
        return "signaled"
    except ProcessLookupError:
        return "gone"
    except PermissionError as e:
        return f"error:permission:{e}"
    except OSError as e:
        return f"error:{e}"


async def stop_gns3_server(
    server_url: Optional[str] = None,
    *,
    stop_timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Stop a **localhost** GNS3 server listening on *server_url*'s port.

    Strategy: discover TCP LISTEN PIDs on the port → SIGTERM → wait → SIGKILL.
    Remote URLs are refused. Clears the healthy cache for the URL.
    """
    url = normalize_server_url(server_url)
    timeout = float(stop_timeout) if stop_timeout is not None else _stop_timeout()
    t0 = time.monotonic()
    signal_steps: List[Dict[str, Any]] = []

    if not is_local_server_url(url):
        return {
            "status": "error",
            "server_url": url,
            "stopped": False,
            "already_stopped": False,
            "pids": [],
            "signal_steps": [],
            "wait_seconds": round(time.monotonic() - t0, 3),
            "error": (
                f"Refusing to stop remote GNS3 server at {url}. "
                "Only localhost / 127.0.0.1 / ::1 may be stopped via this tool."
            ),
        }

    _, port = _parse_host_port(url)

    async with _lock:
        pids = _pids_listening_on_port(port)
        if not pids:
            clear_healthy_cache(url)
            return {
                "status": "success",
                "server_url": url,
                "stopped": False,
                "already_stopped": True,
                "pids": [],
                "signal_steps": [],
                "wait_seconds": round(time.monotonic() - t0, 3),
            }

        logger.warning(
            "Stopping GNS3 server on port %s (pids=%s) for %s",
            port,
            pids,
            url,
        )

        for pid in pids:
            result = _signal_pid(pid, signal.SIGTERM)
            signal_steps.append(
                {"pid": pid, "signal": "SIGTERM", "result": result}
            )

        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if not any(_pid_alive(pid) for pid in pids):
                break
            await asyncio.sleep(0.2)

        survivors = [pid for pid in pids if _pid_alive(pid)]
        for pid in survivors:
            result = _signal_pid(pid, signal.SIGKILL)
            signal_steps.append(
                {"pid": pid, "signal": "SIGKILL", "result": result}
            )

        # Brief grace after KILL
        if survivors:
            kill_deadline = time.monotonic() + min(2.0, max(0.5, timeout))
            while time.monotonic() < kill_deadline:
                if not any(_pid_alive(pid) for pid in pids):
                    break
                await asyncio.sleep(0.1)

        still_alive = [pid for pid in pids if _pid_alive(pid)]
        remaining_listen = _pids_listening_on_port(port)
        clear_healthy_cache(url)

        if still_alive or remaining_listen:
            return {
                "status": "error",
                "server_url": url,
                "stopped": False,
                "already_stopped": False,
                "pids": pids,
                "signal_steps": signal_steps,
                "wait_seconds": round(time.monotonic() - t0, 3),
                "error": (
                    f"GNS3 server on port {port} still present after TERM/KILL "
                    f"(alive_pids={still_alive}, listen_pids={remaining_listen})."
                ),
            }

        return {
            "status": "success",
            "server_url": url,
            "stopped": True,
            "already_stopped": False,
            "pids": pids,
            "signal_steps": signal_steps,
            "wait_seconds": round(time.monotonic() - t0, 3),
        }


async def _spawn_server(argv: list[str]) -> Tuple[Optional[int], str]:
    """Spawn detached gns3server. Returns (pid_or_none, start_note).

    Stdio is fully detached (DEVNULL) so a chatty child cannot fill a pipe
    buffer and block while we wait for health.
    """
    logger.warning("Starting GNS3 server: %s", " ".join(argv))
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as e:
        return None, str(e)
    except Exception as e:
        return None, str(e)

    # Detached: do not wait on stdio. Caller polls health separately.
    return proc.pid, f"spawned pid={proc.pid}"


async def ensure_gns3_server(
    server_url: Optional[str] = None,
    *,
    username: Optional[str] = None,
    password: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Ensure GNS3 REST API is reachable; auto-start on localhost if needed.

    Returns a structured dict suitable for the ``gns3_ensure_server`` tool.
    """
    url = normalize_server_url(server_url)
    # When credentials aren't passed explicitly, fall back to the local
    # ``gns3_server.conf`` first (default), then ``GNS3_USERNAME`` /
    # ``GNS3_PASSWORD`` env overrides. This makes `gns3_ensure_server`
    # work out of the box for a normal local install with auth enabled — no
    # env ritual, no guessing. See references/setup.md "Finding local server
    # credentials". Reuses GNS3Config.from_env resolution order.
    if username is None or password is None:
        _resolved_cfg = GNS3Config.from_env(server_url=url, username=username, password=password)
        if username is None:
            username = _resolved_cfg.username
        if password is None:
            password = _resolved_cfg.password
    start_timeout = _start_timeout()
    t0 = time.monotonic()

    async with _lock:
        if not force:
            cached = _cache_get(url)
            if cached is not None:
                return {
                    "status": "success",
                    "already_running": True,
                    "started": False,
                    "server_url": url,
                    "server_info": cached,
                    "start_command": None,
                    "wait_seconds": round(time.monotonic() - t0, 3),
                }

        ok, payload = await probe_server(url, username=username, password=password)
        if ok:
            _cache_set(url, payload)
            return {
                "status": "success",
                "already_running": True,
                "started": False,
                "server_url": url,
                "server_info": payload,
                "start_command": None,
                "wait_seconds": round(time.monotonic() - t0, 3),
            }

        if not is_local_server_url(url):
            return {
                "status": "error",
                "already_running": False,
                "started": False,
                "server_url": url,
                "server_info": None,
                "start_command": None,
                "wait_seconds": round(time.monotonic() - t0, 3),
                "error": (
                    f"GNS3 server unreachable at {url} ({payload}). "
                    "Remote URLs are not auto-started; start the server on that host."
                ),
            }

        # If the server is already reachable but rejected auth (401/403), do not
        # auto-start a second copy — point the caller at the real credentials.
        if isinstance(payload, dict) and payload.get("reachable") and payload.get("http_status") in (401, 403):
            return {
                "status": "error",
                "already_running": True,
                "started": False,
                "server_url": url,
                "server_info": None,
                "start_command": None,
                "wait_seconds": round(time.monotonic() - t0, 3),
                "error": payload.get("error") or (
                    f"GNS3 API authentication required (HTTP {payload.get('http_status')}). "
                    "For a local server the skill reads [Server] user/password from "
                    "gns3_server.conf (see references/setup.md \"Finding local server "
                    "credentials\"; Linux: ~/.config/GNS3/2.2/gns3_server.conf). "
                    "For a remote server pass --username / --password "
                    "(or GNS3_USERNAME / GNS3_PASSWORD). Do not guess passwords."
                ),
                "http_status": payload.get("http_status"),
            }
        argv = build_start_command(url)
        start_cmd_str = " ".join(shlex.quote(a) for a in argv)
        pid, stderr_tail = await _spawn_server(argv)
        if pid is None:
            return {
                "status": "error",
                "already_running": False,
                "started": False,
                "server_url": url,
                "server_info": None,
                "start_command": start_cmd_str,
                "wait_seconds": round(time.monotonic() - t0, 3),
                "error": f"Failed to spawn GNS3 server: {stderr_tail}",
            }

        deadline = time.monotonic() + start_timeout
        last_err = payload
        while time.monotonic() < deadline:
            ok, payload = await probe_server(url, username=username, password=password)
            if ok:
                _cache_set(url, payload)
                return {
                    "status": "success",
                    "already_running": False,
                    "started": True,
                    "server_url": url,
                    "server_info": payload,
                    "start_command": start_cmd_str,
                    "wait_seconds": round(time.monotonic() - t0, 3),
                }
            # Auth-rejected reachable server: fail fast instead of looping for
            # start_timeout seconds on the same 401/403.
            if isinstance(payload, dict) and payload.get("reachable") and payload.get("http_status") in (401, 403):
                return {
                    "status": "error",
                    "already_running": True,
                    "started": True,
                    "server_url": url,
                    "server_info": None,
                    "start_command": start_cmd_str,
                    "wait_seconds": round(time.monotonic() - t0, 3),
                    "error": payload.get("error") or (
                        f"GNS3 API authentication required (HTTP {payload.get('http_status')}). "
                        "For a local server the skill reads [Server] user/password from "
                        "gns3_server.conf (see references/setup.md \"Finding local server "
                        "credentials\"; Linux: ~/.config/GNS3/2.2/gns3_server.conf). "
                        "For a remote server pass --username / --password "
                        "(or GNS3_USERNAME / GNS3_PASSWORD). Do not guess passwords."
                    ),
                    "http_status": payload.get("http_status"),
                }
            last_err = payload
            await asyncio.sleep(0.5)

        err_bits = [f"Timed out after {start_timeout}s waiting for GNS3 at {url}: {last_err}"]
        if stderr_tail:
            err_bits.append(f"start stderr: {stderr_tail[:500]}")
        return {
            "status": "error",
            "already_running": False,
            "started": True,
            "server_url": url,
            "server_info": None,
            "start_command": start_cmd_str,
            "wait_seconds": round(time.monotonic() - t0, 3),
            "error": " ".join(err_bits),
        }
