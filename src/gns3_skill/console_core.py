"""Console command execution core — shared by server.py and workflow goal tools."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import ssh_client as ssh_helpers
from .gns3_client import GNS3APIClient, GNS3Config
from .server_lifecycle import ensure_gns3_server, normalize_server_url
from .telnet_client import TelnetClient

logger = logging.getLogger(__name__)


async def _create_client_ready(
    server_url: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> GNS3APIClient:
    """Ensure GNS3 is reachable (auto-start if local) then create API client."""
    config = GNS3Config.from_env(
        server_url=server_url,
        username=username,
        password=password,
    )
    result = await ensure_gns3_server(
        config.server_url,
        username=config.username,
        password=config.password,
    )
    if result.get("status") != "success":
        raise RuntimeError(result.get("error") or f"GNS3 server not available at {config.server_url}")
    return GNS3APIClient(config)


async def send_console_commands_impl(
    project_id: str,
    node_id: str,
    commands: List[str],
    server_url: str = "http://localhost:3080",
    username: Optional[str] = None,
    password: Optional[str] = None,
    wait_for_boot: bool = True,
    boot_timeout: int = 120,
    enter_config_mode: bool = False,
    save_config: bool = False,
    enable_password: Optional[str] = None,
    login_username: Optional[str] = None,
    login_password: Optional[str] = None,
    ready_timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Console command sender (shared by public tool and goal workflows).

    Returns pure-body command responses; completion tracked via ``completed`` bool.
    """
    try:
        client = await _create_client_ready(server_url, username, password)

        console_info = await client.get_node_console_info(project_id, node_id)
        host = console_info.get("host")
        port = console_info.get("port")

        if not host or not port:
            return {
                "status": "error",
                "error": "Node has no console or is not running; start it with gns3_start_node first",
            }

        # Resolve console login credentials (args > env). Never log secrets.
        resolved_login_user, resolved_login_pass = ssh_helpers.resolve_console_credentials(
            login_username, login_password
        )
        need_login = resolved_login_user is not None or resolved_login_pass is not None

        telnet = TelnetClient(host, port, timeout=30.0)
        if not telnet.connect():
            return {"status": "error", "error": f"Failed to connect to console {host}:{port}"}

        try:
            if wait_for_boot:
                if not telnet.wait_for_boot(
                    timeout=boot_timeout,
                    accept_login_prompts=need_login,
                ):
                    return {"status": "error", "error": "Timeout waiting for device boot"}
                # Discard any residual output from boot phase (login prompts,
                # banner text) so it doesn't pollute the first command response.
                telnet._rx_buf = ""

            authenticated = False
            if need_login:
                if not telnet.login(
                    resolved_login_user,
                    resolved_login_pass,
                    ready_timeout=ready_timeout,
                ):
                    return {"status": "error", "error": "Console authentication failed"}
                authenticated = True
                # Discard any residual output from login phase (banner text,
                # stale command echoes) so it doesn't pollute the first
                # command response.
                telnet._rx_buf = ""
            else:
                # Even without login, drain any stale output from the console
                # buffer (cross-call pollution — a previous session may have
                # left data in the device's console output buffer).
                telnet._drain_idle_prompts()

            def _result_entry(cmd: str, output: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
                entry: Dict[str, Any] = {"command": cmd, "response": output}
                if meta and meta.get("truncated"):
                    entry["truncated"] = True
                    entry["response_bytes"] = meta.get("response_bytes")
                    entry["response_bytes_raw"] = meta.get("response_bytes_raw")
                if meta and "completed" in meta:
                    entry["completed"] = bool(meta["completed"])
                return entry

            if enter_config_mode:
                outputs, metas = telnet.send_config_commands(
                    commands,
                    enter_config=True,
                    save_config=save_config,
                    enable_password=enable_password,
                    return_meta=True,
                )
                results = [
                    _result_entry(cmd, output, meta)
                    for cmd, output, meta in zip(commands, outputs, metas)
                ]
            else:
                results = []
                prompts = [">", "#", "$", "%"]
                for cmd in commands:
                    output, meta = telnet.send_cmd(
                        cmd, wait_for=prompts, wait_time=1.0, return_meta=True
                    )
                    results.append(_result_entry(cmd, output, meta))

            payload: Dict[str, Any] = {
                "status": "success",
                "node_name": console_info.get("name"),
                "results": results,
            }
            if need_login:
                payload["authenticated"] = authenticated
                if resolved_login_user:
                    payload["login_username"] = resolved_login_user
            return payload
        finally:
            telnet.close()

    except Exception as e:
        logger.error(f"Failed to send console commands: {e}")
        return {"status": "error", "error": str(e)}
