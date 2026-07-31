"""Private console operations for goal tools."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gns3_skill.runtime import OperationContext


async def send_console_commands(
    *,
    context: OperationContext,
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
    """Execute via the invocation-owned REST client and console transport."""
    from gns3_skill.console_core import send_console_commands_impl

    return await send_console_commands_impl(
        client=await context.client(),
        project_id=project_id,
        node_id=node_id,
        commands=commands,
        wait_for_boot=wait_for_boot,
        boot_timeout=boot_timeout,
        enter_config_mode=enter_config_mode,
        save_config=save_config,
        enable_password=enable_password,
        login_username=login_username,
        login_password=login_password,
        ready_timeout=ready_timeout,
    )
