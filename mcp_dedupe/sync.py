"""Dedupe planning and applying: compute what changes, back up, then write."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import clients as clients_mod
from .clients import Client, Servers
from .findings import Finding, ScanResult, canonical


@dataclass
class Action:
    client: Client
    path: Path
    server: str
    action: str  # "remove"
    reason: str


@dataclass
class Plan:
    actions: List[Action] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    def removals_by_client(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for a in self.actions:
            out.setdefault(a.client.id, []).append(a.server)
        return out


def _keeper_first(client_ids: List[str], prefer: Optional[str]) -> List[str]:
    ordered = list(client_ids)
    if prefer and prefer in ordered:
        ordered.remove(prefer)
        ordered.insert(0, prefer)
        return ordered
    prio = clients_mod.PRIORITY
    return sorted(ordered, key=lambda c: prio.index(c) if c in prio else 99)


def build_plan(
    result: ScanResult,
    include_drift: bool = False,
    prune_dead: bool = False,
    prefer: Optional[str] = None,
) -> Plan:
    plan = Plan()

    # Map (client_id, server) -> (scan, config) for quick lookup.
    configs: Dict[tuple, Dict] = {}
    for s in result.scans:
        for name, cfg in s.servers.items():
            configs[(s.client.id, name)] = cfg

    for f in result.findings:
        if f.kind == "duplicate":
            keepers = _keeper_first(f.clients, prefer)
            keep = keepers[0]
            for cid in keepers[1:]:
                scan = next(s for s in result.scans if s.client.id == cid)
                plan.actions.append(
                    Action(scan.client, scan.path, f.server, "remove",
                           f"identical duplicate; keeping copy in {keep}")
                )
        elif f.kind == "drift":
            if include_drift:
                keepers = _keeper_first(f.clients, prefer)
                keep = keepers[0]
                for cid in keepers[1:]:
                    scan = next(s for s in result.scans if s.client.id == cid)
                    plan.actions.append(
                        Action(scan.client, scan.path, f.server, "remove",
                               f"drifted duplicate; keeping {keep}'s config")
                    )
            else:
                plan.skipped.append(
                    f"drift '{f.server}' in {', '.join(f.clients)}: "
                    "skipped (re-run with --include-drift to resolve)"
                )
        elif f.kind == "dead-link" and prune_dead:
            for cid in f.clients:
                # Don't double-plan a removal for the same (client, server).
                if any(a.client.id == cid and a.server == f.server for a in plan.actions):
                    continue
                scan = next(s for s in result.scans if s.client.id == cid)
                plan.actions.append(
                    Action(scan.client, scan.path, f.server, "remove",
                           f"dead link: {f.detail}")
                )
    return plan


def backup_configs(paths: List[Path], backup_dir: Path) -> Path:
    """Copy each config file into a timestamped backup directory."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / stamp
    dest.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if p and p.is_file():
            target = dest / (p.name + ".bak")
            shutil.copy2(p, target)
    return dest


def apply_plan(plan: Plan, result: ScanResult, backup_dir: Path) -> Dict[str, List[str]]:
    """Apply removals. Returns {client_id: [removed server names]}.

    Backs up every touched config file first. Only JSON/TOML clients that
    parsed cleanly are rewritten; anything else is left untouched.
    """
    touched = sorted({a.path for a in plan.actions if a.path})
    backup_configs(touched, backup_dir)

    # Reload each touched file fresh so we never write stale data.
    removed: Dict[str, List[str]] = {}
    by_path: Dict[Path, List[Action]] = {}
    for a in plan.actions:
        by_path.setdefault(a.path, []).append(a)

    for path, actions in by_path.items():
        client = actions[0].client
        original = path.read_text(encoding="utf-8")
        servers = client.read_servers(original)
        for a in actions:
            if a.server in servers:
                del servers[a.server]
                removed.setdefault(client.id, []).append(a.server)
        path.write_text(client.write_servers(servers, original), encoding="utf-8")
    return removed


def describe_plan(plan: Plan) -> List[str]:
    lines: List[str] = []
    if not plan.actions and not plan.skipped:
        lines.append("Nothing to dedupe: no duplicates or dead links found.")
        return lines
    for client_id in sorted(plan.removals_by_client()):
        names = sorted(plan.removals_by_client()[client_id])
        lines.append(f"  {client_id}: remove {', '.join(names)}")
        for a in plan.actions:
            if a.client.id == client_id:
                lines.append(f"    - {a.server}: {a.reason}")
    for s in plan.skipped:
        lines.append(f"  SKIP: {s}")
    return lines
