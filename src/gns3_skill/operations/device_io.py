"""Console, configuration, bulk, and guest SSH expert operations."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import ssh_client as ssh_helpers
from ..config_templates import ConfigTemplates
from ..console_core import send_console_commands_impl
from ..runtime import OperationContext
from ..telnet_client import TelnetClient


def _domain_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Remove a lower helper's redundant success status for runtime normalization."""
    if result.get("status") == "success":
        result.pop("status")
    return result

def _template_secret_values(template_params: Dict[str, Any]) -> tuple[str, ...]:
    markers = ("password", "secret", "community", "passphrase", "token")
    values = {
        value
        for key, value in template_params.items()
        if any(marker in key.lower() for marker in markers)
        and isinstance(value, str)
        and value
    }
    return tuple(sorted(values, key=len, reverse=True))


def _redact_template_value(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "<redacted>")
        return value
    if isinstance(value, dict):
        return {
            key: _redact_template_value(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_template_value(item, secrets) for item in value]
    return value


async def send_console_commands(
    *,
    context: OperationContext,
    project_id: str,
    node_id: str = "",
    commands: List[str] = [],
    wait_for_boot: bool = True,
    boot_timeout: int = 120,
    enter_config_mode: bool = False,
    save_config: bool = False,
    enable_password: Optional[str] = None,
    login_username: Optional[str] = None,
    login_password: Optional[str] = None,
    ready_timeout: Optional[float] = None,
    node_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Send commands through the shared console core."""
    if not node_id and not node_name:
        return {"status": "error", "error": "node_id or node_name is required"}

    client = await context.client()
    if not node_id:
        nodes = await client.get_project_nodes(project_id)
        node = next((item for item in nodes if item.get("name") == node_name), None)
        if not node:
            return {
                "status": "error",
                "error": f"Node '{node_name}' not found in project {project_id}",
            }
        resolved_id = node.get("node_id")
        if not resolved_id:
            return {
                "status": "error",
                "error": f"Node '{node_name}' has no node_id",
            }
        node_id = str(resolved_id)

    result = await send_console_commands_impl(
        client=client,
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
    return _domain_result(result)


async def get_node_config(
    *,
    context: OperationContext,
    project_id: str,
    node_id: str,
    config_type: str = "running",
) -> Dict[str, Any]:
    """Read a running or startup configuration through the device console."""
    client = await context.client()
    console_info = await client.get_node_console_info(project_id, node_id)
    host = console_info.get("host")
    port = console_info.get("port")
    if not host or not port:
        return {"status": "error", "error": "Node has no console or is not running"}

    telnet = TelnetClient(host, port, timeout=30.0)
    if not telnet.connect():
        return {"status": "error", "error": "Failed to connect to console"}
    try:
        telnet.wait_for_boot(timeout=10)
        if config_type == "running":
            configuration = telnet.get_running_config()
        else:
            configuration = telnet.send_cmd(
                "show startup-config", wait_for=["#"], wait_time=5.0
            )
        return {
            "node_name": console_info.get("name"),
            "config_type": config_type,
            "configuration": configuration,
        }
    finally:
        telnet.close()


async def apply_config_template(
    *,
    context: OperationContext,
    project_id: str,
    node_id: str,
    template_name: str,
    template_params: Dict[str, Any],
    save_config: bool = True,
) -> Dict[str, Any]:
    """Render and apply one of the supported configuration templates."""
    commands: List[str] = []

    if template_name == "basic_router":
        commands = ConfigTemplates.basic_router_config(
            template_params["hostname"], template_params.get("domain", "local")
        )
    elif template_name == "interface":
        commands = ConfigTemplates.interface_config(
            template_params["interface"],
            template_params["ip_address"],
            template_params["subnet_mask"],
            template_params.get("description"),
        )
    elif template_name == "ospf":
        commands = ConfigTemplates.ospf_config(
            template_params["process_id"],
            template_params["router_id"],
            template_params["networks"],
        )
    elif template_name == "eigrp":
        commands = ConfigTemplates.eigrp_config(
            template_params["as_number"],
            template_params["networks"],
            template_params.get("router_id"),
        )
    elif template_name == "bgp":
        commands = ConfigTemplates.bgp_config(
            template_params["as_number"],
            template_params["router_id"],
            template_params["neighbors"],
        )
    elif template_name == "static_route":
        commands = ConfigTemplates.static_route(
            template_params["network"],
            template_params["mask"],
            template_params["next_hop"],
            template_params.get("admin_distance"),
        )
    elif template_name == "default_route":
        commands = ConfigTemplates.default_route(template_params["next_hop"])
    elif template_name == "vlan":
        commands = ConfigTemplates.vlan_config(
            template_params["vlan_id"], template_params["name"]
        )
    elif template_name == "trunk_port":
        commands = ConfigTemplates.trunk_port_config(
            template_params["interface"], template_params.get("allowed_vlans")
        )
    elif template_name == "access_port":
        commands = ConfigTemplates.access_port_config(
            template_params["interface"],
            template_params["vlan"],
            template_params.get("portfast", True),
            template_params.get("bpduguard", True),
        )
    elif template_name == "dhcp_pool":
        commands = ConfigTemplates.dhcp_pool_config(
            template_params["pool_name"],
            template_params["network"],
            template_params["mask"],
            template_params["default_router"],
            template_params.get("dns_servers"),
            template_params.get("excluded_addresses"),
        )
    elif template_name == "nat_overload":
        commands = ConfigTemplates.nat_overload_config(
            template_params["inside_interfaces"],
            template_params["outside_interface"],
            template_params["acl_number"],
            template_params["allowed_networks"],
        )
    elif template_name == "ssh":
        commands = ConfigTemplates.ssh_config(
            template_params["domain"],
            template_params["username"],
            template_params["password"],
            template_params.get("crypto_key_size", 1024),
            template_params.get("vty_lines", "0 4"),
        )
    elif template_name == "banner":
        commands = ConfigTemplates.banner_config(
            template_params.get("banner_type", "motd"), template_params["message"]
        )
    elif template_name == "ntp":
        commands = ConfigTemplates.ntp_config(template_params["ntp_servers"])
    elif template_name == "logging":
        commands = ConfigTemplates.logging_config(
            template_params["syslog_server"],
            template_params.get("trap_level", "informational"),
        )
    elif template_name == "snmp":
        commands = ConfigTemplates.snmp_config(
            template_params["community"],
            template_params.get("access", "ro"),
            template_params.get("acl"),
        )
    elif template_name == "standard_acl":
        commands = ConfigTemplates.standard_acl(
            template_params["acl_number"], template_params["entries"]
        )
    elif template_name == "extended_acl":
        commands = ConfigTemplates.extended_acl(
            template_params["acl_number"], template_params["entries"]
        )
    elif template_name == "security_hardening":
        commands = ConfigTemplates.security_hardening_basic()
    elif template_name == "qos_marking":
        commands = ConfigTemplates.qos_basic_marking(
            template_params["class_name"],
            template_params["dscp_value"],
            template_params["interfaces"],
        )
    elif template_name == "vpcs_basic":
        result = await send_console_commands_impl(
            client=await context.client(),
            project_id=project_id,
            node_id=node_id,
            commands=[
                ConfigTemplates.vpcs_basic_config(
                    template_params["ip_address"],
                    template_params["subnet_mask"],
                    template_params["gateway"],
                )
            ],
            enter_config_mode=False,
            save_config=False,
        )
        return _domain_result(result)
    elif template_name == "vpcs_dhcp":
        result = await send_console_commands_impl(
            client=await context.client(),
            project_id=project_id,
            node_id=node_id,
            commands=[ConfigTemplates.vpcs_dhcp_config()],
            enter_config_mode=False,
            save_config=False,
        )
        return _domain_result(result)
    else:
        return {"status": "error", "error": f"Unknown template: {template_name}"}

    result = await send_console_commands_impl(
        client=await context.client(),
        project_id=project_id,
        node_id=node_id,
        commands=commands,
        enter_config_mode=True,
        save_config=save_config,
    )
    if result.get("status") == "success":
        result["template_applied"] = template_name
        result["commands_sent"] = commands
    redacted = _redact_template_value(
        result, _template_secret_values(template_params)
    )
    return _domain_result(redacted)


async def bulk_configure_nodes(
    *,
    context: OperationContext,
    project_id: str,
    configurations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Configure multiple nodes sequentially through the shared console core."""
    for index, configuration in enumerate(configurations):
        if not isinstance(configuration, dict):
            return {
                "status": "error",
                "error": f"configuration at index {index} must be an object",
            }
        missing = [
            name for name in ("node_id", "commands") if name not in configuration
        ]
        if missing:
            return {
                "status": "error",
                "error": f"configuration at index {index} missing {', '.join(missing)}",
            }
        if not isinstance(configuration["commands"], list):
            return {
                "status": "error",
                "error": f"configuration commands at index {index} must be a list",
            }

    client = await context.client()
    results = []
    for configuration in configurations:
        try:
            result = await send_console_commands_impl(
                client=client,
                project_id=project_id,
                node_id=configuration["node_id"],
                commands=configuration["commands"],
                enter_config_mode=configuration.get("enter_config_mode", True),
                save_config=configuration.get("save_config", False),
            )
        except Exception as exc:
            result = {"status": "error", "error": str(exc)}
        results.append(
            {
                "node_id": configuration["node_id"],
                "result": result,
            }
        )

    successful_count = sum(
        1 for result in results if result["result"].get("status") == "success"
    )
    failed_count = len(configurations) - successful_count
    if failed_count:
        status = "partial" if successful_count else "error"
        return {
            "status": status,
            "error": (
                "some node configurations failed"
                if successful_count
                else "all node configurations failed"
            ),
            "results": results,
            "total": len(configurations),
            "successful": successful_count,
            "failed": failed_count,
        }
    return {
        "results": results,
        "total": len(configurations),
        "successful": successful_count,
        "failed": 0,
    }


async def ssh_exec(
    *,
    context: OperationContext,
    commands: List[str],
    host: Optional[str] = None,
    port: int = 22,
    project_id: Optional[str] = None,
    node_id: Optional[str] = None,
    ssh_username: Optional[str] = None,
    ssh_password: Optional[str] = None,
    stop_on_error: bool = True,
    host_key_policy: str = "accept_new",
    connect_timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Run shell commands over the shared guest SSH transport."""
    resolved_host = host
    if not resolved_host:
        if not project_id or not node_id:
            return {
                "status": "error",
                "error": (
                    "host is required, or provide project_id and node_id "
                    "for metadata lookup"
                ),
            }
        client = await context.client()
        node = await client.get_node(project_id, node_id)
        ips = ssh_helpers.extract_ips_from_node(
            node if isinstance(node, dict) else {}
        )
        if not ips:
            return {
                "status": "error",
                "error": (
                    "Could not resolve guest IP from node metadata; "
                    "pass host explicitly"
                ),
            }
        resolved_host = ips[0]

    user, passwd = ssh_helpers.resolve_ssh_credentials(
        ssh_username, ssh_password
    )
    result = await ssh_helpers.exec_commands(
        resolved_host,
        commands,
        port=port,
        username=user,
        password=passwd,
        stop_on_error=stop_on_error,
        host_key_policy=host_key_policy,
        connect_timeout=connect_timeout,
    )
    return _domain_result(result)
