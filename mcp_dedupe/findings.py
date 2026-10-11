"""Finding detection: duplicates, drift, dead links, and unreadable configs."""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .clients import BY_ID, CLIENTS, Client, Servers

SEVERITIES = {"duplicate", "drift", "dead-link", "remote", "unreadable"}


@dataclass
class Finding:
    kind: str  # duplicate | drift | dead-link | remote | unreadable
    server: Optional[str]
    clients: List[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "server": self.server,
            "clients": self.clients,
            "detail": self.detail,
        }


@dataclass
class ClientScan:
    client: Client
    path: Optional[Path]
    servers: Servers
    error: Optional[str] = None


def canonical(server: Dict[str, Any]) -> str:
    """Stable fingerprint of a server config for equality comparison."""
    return json.dumps(server, sort_keys=True, ensure_ascii=False, default=str)


def scan_client(client: Client, home: Path, platform: Optional[str] = None) -> ClientScan:
    path = client.config_path(home, platform)
    if path is None or not path.is_file():
        return ClientScan(client, path, {})
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ClientScan(client, path, {}, error=f"cannot read: {exc}")
    try:
        servers = client.read_servers(text)
    except Exception as exc:  # malformed JSON/TOML
        return ClientScan(client, path, {}, error=f"cannot parse ({client.fmt}): {exc}")
    return ClientScan(client, path, servers)


def _command_dead(server: Dict[str, Any]) -> Optional[str]:
    cmd = server.get("command")
    if not cmd or not isinstance(cmd, str):
        return None
    if os.path.isabs(cmd) or "/" in cmd or "\\" in cmd:
        p = Path(cmd)
        if not p.exists():
            return f"command path does not exist: {cmd}"
        if p.is_dir():
            return f"command path is a directory: {cmd}"
        if not os.access(p, os.X_OK):
            return f"command path is not executable: {cmd}"
        return None
    if shutil.which(cmd) is None:
        return f"command not found on PATH: {cmd}"
    return None


def _check_server_health(name: str, server: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Return (kind, detail) health findings for one server config."""
    out: List[Tuple[str, str]] = []
    if server.get("url"):
        out.append(("remote", f"remote server ({server.get('url')}); not checked offline"))
        return out
    dead = _command_dead(server)
    if dead:
        out.append(("dead-link", dead))
    cwd = server.get("cwd")
    if isinstance(cwd, str) and cwd and not Path(cwd).expanduser().is_dir():
        out.append(("dead-link", f"cwd does not exist: {cwd}"))
    return out


@dataclass
class ScanResult:
    scans: List[ClientScan]
    findings: List[Finding]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clients": [
                {
                    "id": s.client.id,
                    "display": s.client.display,
                    "path": str(s.path) if s.path else None,
                    "servers": sorted(s.servers),
                    "error": s.error,
                }
                for s in self.scans
            ],
            "findings": [f.to_dict() for f in self.findings],
        }


def scan_all(home: Optional[Path] = None, platform: Optional[str] = None) -> ScanResult:
    home = home or Path.home()
    scans = [scan_client(c, home, platform) for c in CLIENTS]
    findings: List[Finding] = []

    for s in scans:
        if s.error:
            findings.append(
                Finding("unreadable", None, [s.client.id], f"{s.path}: {s.error}")
            )

    # Group servers by name across clients.
    by_name: Dict[str, List[Tuple[ClientScan, Dict[str, Any]]]] = {}
    for s in scans:
        for name, cfg in s.servers.items():
            by_name.setdefault(name, []).append((s, cfg))

    for name in sorted(by_name):
        holders = by_name[name]
        if len(holders) > 1:
            fingerprints = {canonical(cfg) for _, cfg in holders}
            client_ids = [s.client.id for s, _ in holders]
            if len(fingerprints) == 1:
                findings.append(
                    Finding(
                        "duplicate",
                        name,
                        client_ids,
                        "identical config in multiple clients; safe to dedupe",
                    )
                )
            else:
                findings.append(
                    Finding(
                        "drift",
                        name,
                        client_ids,
                        "same name but different config across clients",
                    )
                )

    # Health checks per (client, server), grouped per server for readable output.
    health_by_server: Dict[str, List[Tuple[str, str, str]]] = {}
    for s in scans:
        for name, cfg in s.servers.items():
            for kind, detail in _check_server_health(name, cfg):
                bucket = health_by_server.setdefault(name, [])
                entry = (kind, detail, s.client.id)
                if entry not in bucket:
                    bucket.append(entry)
    for name in sorted(health_by_server):
        for kind, detail, client_id in health_by_server[name]:
            # One finding per (server, kind, detail), listing every client.
            clients = [c for k, d, c in health_by_server[name] if k == kind and d == detail]
            findings.append(Finding(kind, name, clients, detail))

    # Stable ordering: duplicates, drift, dead-link, remote, unreadable.
    order = {"duplicate": 0, "drift": 1, "dead-link": 2, "remote": 3, "unreadable": 4}
    findings.sort(key=lambda f: (order.get(f.kind, 9), f.server or "", f.detail))
    return ScanResult(scans, findings)


def actionable(findings: List[Finding]) -> List[Finding]:
    return [f for f in findings if f.kind in ("duplicate", "drift", "dead-link", "unreadable")]
