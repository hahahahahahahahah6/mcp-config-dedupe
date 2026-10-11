"""Client adapters: where each MCP client keeps its server configuration.

Every adapter knows how to locate its config file(s) relative to a home
directory, and how to extract / replace the ``{name: server-config}`` mapping.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

Servers = Dict[str, Dict[str, Any]]


def _json_servers(doc: Any, *path: str) -> Servers:
    """Walk a parsed JSON document to the servers mapping."""
    node = doc
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return {}
        node = node[key]
    if not isinstance(node, dict):
        return {}
    return {k: v for k, v in node.items() if isinstance(v, dict)}


def _read_json(text: str) -> Servers:
    return _json_servers(json.loads(text), "mcpServers")


def _write_json(servers: Servers, original_text: Optional[str]) -> str:
    """Replace the mcpServers block, preserving sibling keys."""
    try:
        doc = json.loads(original_text) if original_text else {}
    except (json.JSONDecodeError, TypeError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc["mcpServers"] = servers
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _read_vscode(text: str) -> Servers:
    doc = json.loads(text)
    # Official VS Code MCP layout: "mcp": {"servers": {...}}.
    servers = _json_servers(doc, "mcp", "servers")
    if servers:
        return servers
    # Legacy/alternate layout with a dotted key.
    return _json_servers(doc, "mcp.servers")


def _write_vscode(servers: Servers, original_text: Optional[str]) -> str:
    try:
        doc = json.loads(original_text) if original_text else {}
    except (json.JSONDecodeError, TypeError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    if isinstance(doc.get("mcp"), dict):
        doc["mcp"]["servers"] = servers
    elif "mcp.servers" in doc:
        doc["mcp.servers"] = servers
    else:
        doc.setdefault("mcp", {})["servers"] = servers
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _read_codex(text: str) -> Servers:
    doc = tomllib.loads(text)
    node = doc.get("mcp_servers", {})
    if not isinstance(node, dict):
        return {}
    return {k: v for k, v in node.items() if isinstance(v, dict)}


def _write_codex(servers: Servers, original_text: Optional[str]) -> str:
    from . import toml_util

    try:
        doc = tomllib.loads(original_text) if original_text else {}
    except tomllib.TOMLDecodeError:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc["mcp_servers"] = servers
    return toml_util.dumps(doc)


class Client:
    def __init__(
        self,
        client_id: str,
        display: str,
        relpaths: Dict[str, List[str]],
        read_servers: Callable[[str], Servers],
        write_servers: Callable[[Servers, Optional[str]], str],
        fmt: str,
    ) -> None:
        self.id = client_id
        self.display = display
        self.relpaths = relpaths
        self.read_servers = read_servers
        self.write_servers = write_servers
        self.fmt = fmt

    def config_path(self, home: Path, platform: Optional[str] = None) -> Optional[Path]:
        plat = platform or sys.platform
        for rel in self.relpaths.get(plat, []):
            p = home / rel
            if p.is_file():
                return p
        # Fall back to the first candidate so callers can report "missing".
        cands = self.relpaths.get(plat, [])
        return home / cands[0] if cands else None


CLIENTS: List[Client] = [
    Client(
        "claude-desktop",
        "Claude Desktop",
        {
            "darwin": ["Library/Application Support/Claude/claude_desktop_config.json"],
            "win32": ["AppData/Roaming/Claude/claude_desktop_config.json"],
            "linux": [".config/Claude/claude_desktop_config.json"],
        },
        _read_json,
        _write_json,
        "json",
    ),
    Client(
        "cursor",
        "Cursor",
        {
            "darwin": [".cursor/mcp.json"],
            "win32": [".cursor/mcp.json"],
            "linux": [".cursor/mcp.json"],
        },
        _read_json,
        _write_json,
        "json",
    ),
    Client(
        "vscode",
        "VS Code",
        {
            "darwin": ["Library/Application Support/Code/User/settings.json"],
            "win32": ["AppData/Roaming/Code/User/settings.json"],
            "linux": [".config/Code/User/settings.json"],
        },
        _read_vscode,
        _write_vscode,
        "json",
    ),
    Client(
        "codex",
        "Codex CLI",
        {
            "darwin": [".codex/config.toml"],
            "win32": [".codex/config.toml"],
            "linux": [".codex/config.toml"],
        },
        _read_codex,
        _write_codex,
        "toml",
    ),
]

BY_ID = {c.id: c for c in CLIENTS}

# Priority order: the first client in this list that holds a duplicated server
# keeps the canonical copy unless --prefer overrides it.
PRIORITY = ["claude-desktop", "cursor", "vscode", "codex"]
