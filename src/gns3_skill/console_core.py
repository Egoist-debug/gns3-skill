"""Console command execution core shared by expert and goal operations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import ssh_client as ssh_helpers
from .gns3_client import GNS3APIClient
from .telnet_client import TelnetClient


async def send_console_commands_impl(
    *,
    client: GNS3APIClient,
    project_id: str,
    node_id: str,
    commands: List[str],
    wait_for_boot: bool = True,
    boot_timeout: int = 120,
    enter_config_mode: bool = False,
    save_config: bool = False,
    enable_password: Optional[str] = None,
    login_username: Optional[str] = None,
    login_password: Optional[str] = None,
    ready_timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Execute console commands with an invocation-owned REST client."""
    console_info = await client.get_node_console_info(project_id, node_id)
    host = console_info.get("host")
    port = console_info.get("port")
    if not host or not port:
        return {"status": "error", "error": "Node has no console or is not running; start it first"}
    resolved_login_user, resolved_login_pass = ssh_helpers.resolve_console_credentials(
        login_username, login_password
    )
    need_login = resolved_login_user is not None or resolved_login_pass is not None
    telnet = TelnetClient(host, port, timeout=30.0)
    if not telnet.connect():
        return {"status": "error", "error": f"Failed to connect to console {host}:{port}"}
    try:
        if wait_for_boot:
            if not telnet.wait_for_boot(timeout=boot_timeout, accept_login_prompts=need_login):
                return {"status": "error", "error": "Timeout waiting for device boot"}
            telnet._rx_buf = ""
        authenticated = False
        if need_login:
            if not telnet.login(resolved_login_user, resolved_login_pass, ready_timeout=ready_timeout):
                return {"status": "error", "error": "Console authentication failed"}
            authenticated = True
            telnet._rx_buf = ""
        else:
            telnet._drain_idle_prompts()

        def result_entry(command: str, output: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            entry: Dict[str, Any] = {"command": command, "response": output}
            if meta and meta.get("truncated"):
                entry["truncated"] = True
                entry["response_bytes"] = meta.get("response_bytes")
                entry["response_bytes_raw"] = meta.get("response_bytes_raw")
            if meta and "completed" in meta:
                entry["completed"] = bool(meta["completed"])
            return entry

        if enter_config_mode:
            outputs, metas = telnet.send_config_commands(
                commands, enter_config=True, save_config=save_config,
                enable_password=enable_password, return_meta=True,
            )
            results = [result_entry(command, output, meta)
                       for command, output, meta in zip(commands, outputs, metas)]
        else:
            results = []
            for command in commands:
                output, meta = telnet.send_cmd(
                    command, wait_for=[">", "#", "$", "%"], wait_time=1.0, return_meta=True
                )
                results.append(result_entry(command, output, meta))
        payload: Dict[str, Any] = {
            "status": "success", "node_name": console_info.get("name"), "results": results,
        }
        if need_login:
            payload["authenticated"] = authenticated
        return payload
    finally:
        telnet.close()
