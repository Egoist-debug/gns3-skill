#!/usr/bin/env python3
"""
GNS3 API Client - Comprehensive HTTP client for GNS3 REST API v2.
Handles all GNS3 server operations with robust error handling.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}

# Candidate locations for the GNS3 local server config. The first readable file
# wins; we only ever read credentials + host/port from this — never write it,
# never log its contents. See references/setup.md "Finding local server
# credentials". Module-level (not a class attr) so pydantic v2 does not treat
# it as a private model attribute.
_GNS3_SERVER_CONF_CANDIDATES = (
    "~/.config/GNS3/2.2/gns3_server.conf",                       # Linux
    "~/Library/Application Support/GNS3/2.2/gns3_server.conf",    # macOS
    "~/Documents/GNS3/embedded/gns3_server.conf",                 # bundled server / older Linux
    "~/GNS3/gns3_server.conf",                                    # portable
)


class GNS3Config(BaseModel):
    """Configuration for GNS3 server connection."""
    server_url: str = Field(default="http://localhost:3080", description="GNS3 server URL")
    username: Optional[str] = Field(default=None, description="Username for authentication")
    password: Optional[str] = Field(default=None, description="Password for authentication")
    verify_ssl: bool = Field(default=True, description="Verify SSL certificates")
    timeout: float = Field(default=30.0, description="Request timeout in seconds")

    @staticmethod
    def _resolve_conf_path() -> Optional[Path]:
        """Return the first existing, readable ``gns3_server.conf`` candidate, else None."""
        for raw in _GNS3_SERVER_CONF_CANDIDATES:
            p = Path(raw).expanduser()
            try:
                if p.is_file():
                    return p
            except OSError:
                continue
        # Windows: %APPDATA%\GNS3\2.2\gns3_server.conf (resolved per-user)
        appdata = os.environ.get("APPDATA")
        if appdata:
            p = Path(appdata) / "GNS3" / "2.2" / "gns3_server.conf"
            try:
                if p.is_file():
                    return p
            except OSError:
                pass
        return None

    @staticmethod
    def _read_local_server_conf(path: Path) -> Dict[str, Optional[str]]:
        """Parse the ``[Server]`` section of a local ``gns3_server.conf``.

        Returns a dict with keys ``auth``, ``user``, ``password``, ``host``,
        ``port`` (values None when absent / unparseable). Never raises — a
        malformed/missing file simply yields no credentials, and the caller
        surfaces a normal 401 error rather than a parse traceback.
        """
        import configparser

        out: Dict[str, Optional[str]] = {k: None for k in ("auth", "user", "password", "host", "port")}
        try:
            cp = configparser.ConfigParser(strict=False, interpolation=None)
            if not cp.read(str(path)):
                return out
            if not cp.has_section("Server"):
                return out
            for key in out:
                if cp.has_option("Server", key):
                    out[key] = (cp.get("Server", key) or "").strip() or None
        except Exception:
            return out
        return out

    @classmethod
    def from_local_server_conf(cls) -> Dict[str, Optional[str]]:
        """Read the local ``gns3_server.conf`` ``[Server]`` section as a flat dict.

        Always returns a dict (possibly all-None). Used as the **default**
        credential source for local GNS3 servers so the skill works out of the
        box when the user installed GNS3 normally, with no env ritual. See
        ``references/setup.md`` ("Finding local server credentials").
        """
        path = cls._resolve_conf_path()
        if path is None:
            return {k: None for k in ("auth", "user", "password", "host", "port")}
        return cls._read_local_server_conf(path)

    @classmethod
    def from_env(
        cls,
        server_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        verify_ssl: Optional[bool] = None,
        timeout: Optional[float] = None,
    ) -> "GNS3Config":
        """Build config from explicit args with **local-config-file first** defaults.

        Resolution order for credentials (highest-precedence first):
          1. Explicit ``username`` / ``password`` kwargs.
          2. ``GNS3_USERNAME`` / ``GNS3_PASSWORD`` environment overrides.
          3. ``[Server]`` section of the local ``gns3_server.conf``
             (``~/.config/GNS3/2.2/gns3_server.conf`` etc.) — the default the
             skill loads without any env setup. Only attempted when missing.

        ``server_url`` falls back to the same config file's ``host`` / ``port``
        when not provided via arg or env. Environment variables:
          ``GNS3_SERVER_URL``, ``GNS3_USERNAME``, ``GNS3_PASSWORD``,
          ``GNS3_VERIFY_SSL``, ``GNS3_TIMEOUT``.
        """
        local = cls.from_local_server_conf()
        # Server URL: explicit > env > local conf host:port > default.
        if server_url is None or str(server_url).strip() == "":
            if os.environ.get("GNS3_SERVER_URL"):
                server_url = os.environ.get("GNS3_SERVER_URL")
            elif local.get("host"):
                port = local.get("port") or "3080"
                server_url = f"http://{local['host']}:{port}"
            else:
                server_url = "http://localhost:3080"
        # Credentials: explicit > env > local conf (only when auth=True).
        if username is None and password is None:
            env_u = os.environ.get("GNS3_USERNAME")
            env_p = os.environ.get("GNS3_PASSWORD")
            if env_u and env_p:
                username, password = env_u, env_p
            elif str(local.get("auth", "")).lower() == "true" and (local.get("user") or local.get("password")):
                username, password = local.get("user"), local.get("password")
            elif env_u:
                username = env_u
            elif env_p:
                password = env_p
        return cls(
            server_url=server_url,
            username=username,
            password=password,
            verify_ssl=_env_bool("GNS3_VERIFY_SSL", True) if verify_ssl is None else verify_ssl,
            timeout=float(os.environ["GNS3_TIMEOUT"]) if timeout is None and os.environ.get("GNS3_TIMEOUT") else (timeout if timeout is not None else 30.0),
        )


class GNS3APIClient:
    """Comprehensive HTTP client for GNS3 REST API v2."""

    def __init__(self, config: GNS3Config):
        self.config = config
        self.base_url = config.server_url.rstrip('/')
        self.auth = None
        if config.username and config.password:
            self.auth = (config.username, config.password)

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        content: Optional[bytes] = None,
        content_type: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Union[Dict[str, Any], List[Dict[str, Any]], bytes, None]:
        """Make HTTP request to GNS3 API with comprehensive error handling."""
        url = f"{self.base_url}/v2{endpoint}"
        headers: Dict[str, str] = {}
        if content is not None:
            headers["Content-Type"] = content_type or "application/octet-stream"
        elif data is not None:
            headers["Content-Type"] = "application/json"

        request_timeout = timeout if timeout is not None else self.config.timeout

        try:
            async with httpx.AsyncClient(verify=self.config.verify_ssl, timeout=request_timeout) as client:
                kwargs: Dict[str, Any] = {"headers": headers, "auth": self.auth, "params": params}
                m = method.upper()
                if content is not None:
                    kwargs["content"] = content
                elif data is not None and m in {"POST", "PUT", "PATCH"}:
                    kwargs["json"] = data

                if m == "GET":
                    response = await client.get(url, **kwargs)
                elif m == "POST":
                    response = await client.post(url, **kwargs)
                elif m == "PUT":
                    response = await client.put(url, **kwargs)
                elif m == "DELETE":
                    response = await client.delete(url, **kwargs)
                elif m == "PATCH":
                    response = await client.patch(url, **kwargs)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                response.raise_for_status()

                if response.status_code == 204 or not response.content:
                    return None

                ctype = response.headers.get("content-type", "")
                if ctype.startswith("application/json"):
                    return response.json()
                return response.content

        except httpx.RequestError as e:
            logger.error(f"Request error: {e}")
            raise Exception(f"Failed to connect to GNS3 server at {self.base_url}: {str(e)}")
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error: {e.response.status_code} - {e.response.text}")
            raise Exception(f"GNS3 API error [{e.response.status_code}]: {e.response.text}")
    # ==================== SERVER OPERATIONS ====================
    
    async def get_server_info(self) -> Dict[str, Any]:
        """Get GNS3 server version and information."""
        return await self._request("GET", "/version")
    
    async def get_compute_list(self) -> List[Dict[str, Any]]:
        """List all compute servers."""
        return await self._request("GET", "/computes")
    
    async def get_compute(self, compute_id: str) -> Dict[str, Any]:
        """Get details of a specific compute."""
        return await self._request("GET", f"/computes/{compute_id}")
    
    # ==================== PROJECT OPERATIONS ====================
    
    async def get_projects(self) -> List[Dict[str, Any]]:
        """List all projects."""
        return await self._request("GET", "/projects")
    
    async def create_project(self, name: str, auto_close: bool = False, 
                           auto_open: bool = False, auto_start: bool = False,
                           path: Optional[str] = None) -> Dict[str, Any]:
        """Create a new project."""
        data = {
            "name": name,
            "auto_close": auto_close,
            "auto_open": auto_open,
            "auto_start": auto_start
        }
        if path:
            data["path"] = path
        return await self._request("POST", "/projects", data)
    
    async def get_project(self, project_id: str) -> Dict[str, Any]:
        """Get project details."""
        return await self._request("GET", f"/projects/{project_id}")
    
    async def update_project(self, project_id: str, **kwargs) -> Dict[str, Any]:
        """Update project settings."""
        return await self._request("PUT", f"/projects/{project_id}", kwargs)
    
    async def open_project(self, project_id: str) -> Dict[str, Any]:
        """Open a project."""
        return await self._request("POST", f"/projects/{project_id}/open")
    
    async def close_project(self, project_id: str) -> Dict[str, Any]:
        """Close a project."""
        return await self._request("POST", f"/projects/{project_id}/close")
    
    async def delete_project(self, project_id: str) -> None:
        """Delete a project permanently."""
        await self._request("DELETE", f"/projects/{project_id}")
    
    async def duplicate_project(self, project_id: str, name: str, path: Optional[str] = None) -> Dict[str, Any]:
        """Duplicate a project."""
        data = {"name": name}
        if path:
            data["path"] = path
        return await self._request("POST", f"/projects/{project_id}/duplicate", data)
    
    # ==================== NODE OPERATIONS ====================
    
    async def get_project_nodes(self, project_id: str) -> List[Dict[str, Any]]:
        """List all nodes in a project."""
        return await self._request("GET", f"/projects/{project_id}/nodes")
    
    async def create_node(self, project_id: str, node_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a node in a project."""
        return await self._request("POST", f"/projects/{project_id}/nodes", node_data)
    
    async def create_node_from_template(self, project_id: str, template_id: str, 
                                       x: int = 0, y: int = 0, 
                                       compute_id: Optional[str] = None, 
                                       name: Optional[str] = None) -> Dict[str, Any]:
        """Create a node from a template."""
        data = {"x": x, "y": y}
        if compute_id:
            data["compute_id"] = compute_id
        if name:
            data["name"] = name
        return await self._request("POST", f"/projects/{project_id}/templates/{template_id}", data)
    
    async def get_node(self, project_id: str, node_id: str) -> Dict[str, Any]:
        """Get node details."""
        return await self._request("GET", f"/projects/{project_id}/nodes/{node_id}")
    
    async def update_node(self, project_id: str, node_id: str, node_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update node settings."""
        return await self._request("PUT", f"/projects/{project_id}/nodes/{node_id}", node_data)
    
    async def delete_node(self, project_id: str, node_id: str) -> None:
        """Delete a node."""
        await self._request("DELETE", f"/projects/{project_id}/nodes/{node_id}")
    
    async def start_node(self, project_id: str, node_id: str) -> Dict[str, Any]:
        """Start a node."""
        return await self._request("POST", f"/projects/{project_id}/nodes/{node_id}/start")
    
    async def stop_node(self, project_id: str, node_id: str) -> Dict[str, Any]:
        """Stop a node."""
        return await self._request("POST", f"/projects/{project_id}/nodes/{node_id}/stop")
    
    async def suspend_node(self, project_id: str, node_id: str) -> Dict[str, Any]:
        """Suspend a node."""
        return await self._request("POST", f"/projects/{project_id}/nodes/{node_id}/suspend")
    
    async def reload_node(self, project_id: str, node_id: str) -> Dict[str, Any]:
        """Reload a node."""
        return await self._request("POST", f"/projects/{project_id}/nodes/{node_id}/reload")
    
    async def duplicate_node(self, project_id: str, node_id: str, x: int = 0, y: int = 0) -> Dict[str, Any]:
        """Duplicate a node."""
        data = {"x": x, "y": y}
        return await self._request("POST", f"/projects/{project_id}/nodes/{node_id}/duplicate", data)
    
    async def get_node_console_info(self, project_id: str, node_id: str) -> Dict[str, Any]:
        """Get console host and port for a node."""
        node = await self.get_node(project_id, node_id)
        return {
            "host": node.get("console_host"),
            "port": node.get("console"),
            "type": node.get("console_type"),
            "name": node.get("name"),
            "status": node.get("status")
        }
    
    async def get_node_dynamips_auto_idlepc(self, project_id: str, node_id: str) -> Dict[str, Any]:
        """Get auto idle-pc value for Dynamips router."""
        return await self._request("GET", f"/projects/{project_id}/nodes/{node_id}/dynamips/auto_idlepc")
    
    async def get_node_dynamips_idlepc_proposals(self, project_id: str, node_id: str) -> List[str]:
        """Get idle-pc proposals for Dynamips router."""
        result = await self._request("GET", f"/projects/{project_id}/nodes/{node_id}/dynamips/idlepc_proposals")
        return result.get("idlepc", [])
    
    # ==================== LINK OPERATIONS ====================
    
    async def get_project_links(self, project_id: str) -> List[Dict[str, Any]]:
        """List all links in a project."""
        return await self._request("GET", f"/projects/{project_id}/links")
    
    async def create_link(self, project_id: str, link_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a link between nodes."""
        return await self._request("POST", f"/projects/{project_id}/links", link_data)
    
    async def get_link(self, project_id: str, link_id: str) -> Dict[str, Any]:
        """Get link details."""
        return await self._request("GET", f"/projects/{project_id}/links/{link_id}")
    
    async def update_link(self, project_id: str, link_id: str, link_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update link settings."""
        return await self._request("PUT", f"/projects/{project_id}/links/{link_id}", link_data)
    
    async def delete_link(self, project_id: str, link_id: str) -> None:
        """Delete a link."""
        await self._request("DELETE", f"/projects/{project_id}/links/{link_id}")
    
    async def get_available_link_filters(self, project_id: str, link_id: str) -> List[Dict[str, Any]]:
        """Get available filters for a link."""
        return await self._request("GET", f"/projects/{project_id}/links/{link_id}/available_filters")
    
    # ==================== CAPTURE OPERATIONS ====================
    
    async def start_capture(self, project_id: str, link_id: str, 
                          capture_file_name: str, data_link_type: str = "DLT_EN10MB") -> Dict[str, Any]:
        """Start packet capture on a link."""
        data = {
            "capture_file_name": capture_file_name,
            "data_link_type": data_link_type
        }
        return await self._request("POST", f"/projects/{project_id}/links/{link_id}/start_capture", data)
    
    async def stop_capture(self, project_id: str, link_id: str) -> Dict[str, Any]:
        """Stop packet capture on a link."""
        return await self._request("POST", f"/projects/{project_id}/links/{link_id}/stop_capture")
    
    async def get_capture_stream(self, project_id: str, link_id: str) -> bytes:
        """Get capture stream (pcap data)."""
        return await self._request("GET", f"/projects/{project_id}/links/{link_id}/pcap")
    
    # ==================== SNAPSHOT OPERATIONS ====================
    
    async def get_snapshots(self, project_id: str) -> List[Dict[str, Any]]:
        """List all snapshots for a project."""
        return await self._request("GET", f"/projects/{project_id}/snapshots")
    
    async def create_snapshot(self, project_id: str, name: str) -> Dict[str, Any]:
        """Create a snapshot of a project."""
        data = {"name": name}
        return await self._request("POST", f"/projects/{project_id}/snapshots", data)
    
    async def delete_snapshot(self, project_id: str, snapshot_id: str) -> None:
        """Delete a snapshot."""
        await self._request("DELETE", f"/projects/{project_id}/snapshots/{snapshot_id}")
    
    async def restore_snapshot(self, project_id: str, snapshot_id: str) -> Dict[str, Any]:
        """Restore a project from a snapshot."""
        return await self._request("POST", f"/projects/{project_id}/snapshots/{snapshot_id}/restore")
    
    # ==================== TEMPLATE OPERATIONS ====================
    
    async def get_templates(self) -> List[Dict[str, Any]]:
        """List all templates."""
        return await self._request("GET", "/templates")
    
    async def create_template(self, template_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new template."""
        return await self._request("POST", "/templates", template_data)
    
    async def get_template(self, template_id: str) -> Dict[str, Any]:
        """Get template details."""
        return await self._request("GET", f"/templates/{template_id}")
    
    async def update_template(self, template_id: str, template_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update a template."""
        return await self._request("PUT", f"/templates/{template_id}", template_data)
    
    async def delete_template(self, template_id: str) -> None:
        """Delete a template."""
        await self._request("DELETE", f"/templates/{template_id}")
    
    async def duplicate_template(self, template_id: str) -> Dict[str, Any]:
        """Duplicate a template."""
        return await self._request("POST", f"/templates/{template_id}/duplicate")
    
    # ==================== APPLIANCE OPERATIONS ====================
    
    async def get_appliances(self) -> List[Dict[str, Any]]:
        """List all available appliances."""
        return await self._request("GET", "/appliances")
    
    async def get_appliance(self, appliance_id: str) -> Dict[str, Any]:
        """Get appliance details."""
        return await self._request("GET", f"/appliances/{appliance_id}")
    
    async def install_appliance(self, appliance_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        """Install an appliance."""
        data = {}
        if version:
            data["version"] = version
        return await self._request("POST", f"/appliances/{appliance_id}/install", data)
    
    # ==================== DRAWING OPERATIONS ====================
    
    async def get_project_drawings(self, project_id: str) -> List[Dict[str, Any]]:
        """List all drawings in a project."""
        return await self._request("GET", f"/projects/{project_id}/drawings")
    
    async def create_drawing(self, project_id: str, drawing_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a drawing (text, rectangle, ellipse, line, image)."""
        return await self._request("POST", f"/projects/{project_id}/drawings", drawing_data)
    
    async def get_drawing(self, project_id: str, drawing_id: str) -> Dict[str, Any]:
        """Get drawing details."""
        return await self._request("GET", f"/projects/{project_id}/drawings/{drawing_id}")
    
    async def update_drawing(self, project_id: str, drawing_id: str, drawing_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update a drawing."""
        return await self._request("PUT", f"/projects/{project_id}/drawings/{drawing_id}", drawing_data)
    
    async def delete_drawing(self, project_id: str, drawing_id: str) -> None:
        """Delete a drawing."""
        await self._request("DELETE", f"/projects/{project_id}/drawings/{drawing_id}")
    
    # ==================== SYMBOL OPERATIONS ====================

    async def get_symbols(self) -> List[Dict[str, Any]]:
        """List all available symbols."""
        return await self._request("GET", "/symbols")

    async def get_symbol(self, symbol_id: str) -> bytes:
        """Get symbol image data."""
        result = await self._request("GET", f"/symbols/{symbol_id}/raw")
        return result if isinstance(result, (bytes, bytearray)) else b""

    async def upload_symbol(self, symbol_id: str, symbol_data: bytes) -> None:
        """Upload a custom symbol (raw body)."""
        await self._request(
            "POST",
            f"/symbols/{symbol_id}/raw",
            content=symbol_data,
            content_type="application/octet-stream",
        )

    # ==================== IMAGE OPERATIONS ====================

    async def list_images(self, compute_id: str, emulator: str) -> List[Dict[str, Any]]:
        """List images for an emulator on a compute.

        emulator: qemu | dynamips | iou | docker
        """
        result = await self._request("GET", f"/computes/{compute_id}/{emulator}/images")
        if result is None:
            return []
        if isinstance(result, list):
            return result
        return [result]  # type: ignore[list-item]

    async def upload_image(
        self,
        compute_id: str,
        emulator: str,
        filename: str,
        source_path: Optional[str] = None,
        content: Optional[bytes] = None,
        timeout: float = 600.0,
    ) -> Dict[str, Any]:
        """Upload a device image to a compute (qemu/dynamips/iou).

        Uses controller forward API:
          POST /v2/computes/{compute_id}/{emulator}/images/{filename}
        """
        if content is None:
            if not source_path:
                raise ValueError("Either source_path or content is required")
            path = Path(source_path)
            if not path.is_file():
                raise FileNotFoundError(f"Image file not found: {source_path}")
            content = path.read_bytes()

        safe_name = filename.lstrip("/")
        endpoint = f"/computes/{compute_id}/{emulator}/images/{safe_name}"
        await self._request(
            "POST",
            endpoint,
            content=content,
            content_type="application/octet-stream",
            timeout=timeout,
        )
        return {
            "compute_id": compute_id,
            "emulator": emulator,
            "filename": safe_name,
            "size_bytes": len(content),
        }

    async def download_image(
        self,
        compute_id: str,
        emulator: str,
        filename: str,
        output_path: str,
        timeout: float = 600.0,
    ) -> Dict[str, Any]:
        """Download an image from compute to a local path."""
        safe_name = filename.lstrip("/")
        data = await self._request(
            "GET",
            f"/computes/{compute_id}/{emulator}/images/{safe_name}",
            timeout=timeout,
        )
        if not isinstance(data, (bytes, bytearray)):
            raise Exception("Unexpected non-binary response when downloading image")
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return {"path": str(out), "size_bytes": len(data), "filename": safe_name}

    # ==================== PROJECT EXPORT ====================

    async def export_project(
        self,
        project_id: str,
        output_path: str,
        include_images: bool = False,
        include_snapshots: bool = False,
        reset_mac_addresses: bool = False,
        keep_compute_ids: bool = False,
        compression: str = "zip",
        timeout: float = 600.0,
    ) -> Dict[str, Any]:
        """Export a project archive to a local file path."""
        params = {
            "include_images": "yes" if include_images else "no",
            "include_snapshots": "yes" if include_snapshots else "no",
            "reset_mac_addresses": "yes" if reset_mac_addresses else "no",
            "keep_compute_ids": "yes" if keep_compute_ids else "no",
            "compression": compression,
        }
        data = await self._request(
            "GET",
            f"/projects/{project_id}/export",
            params=params,
            timeout=timeout,
        )
        if not isinstance(data, (bytes, bytearray)):
            raise Exception("Unexpected non-binary response when exporting project")
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return {"path": str(out), "size_bytes": len(data), "project_id": project_id}
