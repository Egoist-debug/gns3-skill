"""GNS3 skill library — CLI-first GNS3 lab operations."""

from __future__ import annotations

__version__ = "2.0.0"
__author__ = "GNS3 Skill Contributors"
__license__ = "MIT"

__all__ = [
    "GNS3APIClient",
    "GNS3Config",
    "TelnetClient",
    "ConfigTemplates",
    "TopologyTemplates",
]


def __getattr__(name: str):
    if name in {"GNS3APIClient", "GNS3Config"}:
        from . import gns3_client

        return getattr(gns3_client, name)
    if name == "TelnetClient":
        from .telnet_client import TelnetClient

        return TelnetClient
    if name in {"ConfigTemplates", "TopologyTemplates"}:
        from . import config_templates

        return getattr(config_templates, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
