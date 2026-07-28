"""Private console operations for goal tools."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Imported lazily inside functions to avoid circular imports with server.py.


async def send_console_commands(
    *,
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
    """Delegate to server private impl — pure-body responses included."""
    from gns3_skill.server import _send_console_commands_impl

    return await _send_console_commands_impl(
        project_id=project_id,
        node_id=node_id,
        commands=commands,
        server_url=server_url,
        username=username,
        password=password,
        wait_for_boot=wait_for_boot,
        boot_timeout=boot_timeout,
        enter_config_mode=enter_config_mode,
        save_config=save_config,
        enable_password=enable_password,
        login_username=login_username,
        login_password=login_password,
        ready_timeout=ready_timeout,
    )
