"""CLI for mcp-dedupe: scan and dedupe MCP client configurations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .clients import BY_ID
from .findings import actionable, scan_all
from .sync import apply_plan, build_plan, describe_plan

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2


def _home_arg(args) -> Path:
    return Path(args.home).expanduser() if args.home else Path.home()


def cmd_scan(args) -> int:
    result = scan_all(home=_home_arg(args))
    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        present = [s for s in result.scans if s.path and s.path.is_file()]
        print("MCP config scan")
        print("=" * 60)
        for s in result.scans:
            status = (
                f"{len(s.servers)} server(s)" if s.path and s.path.is_file()
                else "no config file"
            )
            extra = f"  [ERROR: {s.error}]" if s.error else ""
            print(f"- {s.client.id:15} {status}{extra}")
        print("-" * 60)
        if not result.findings:
            print("Clean: no duplicates, drift, or dead links found.")
        for f in result.findings:
            who = f", ".join(f.clients) if f.clients else "-"
            label = {"duplicate": "DUPLICATE", "drift": "DRIFT",
                     "dead-link": "DEAD-LINK", "remote": "REMOTE",
                     "unreadable": "UNREADABLE"}.get(f.kind, f.kind.upper())
            print(f"[{label}] {f.server or '(config)'} ({who}): {f.detail}")
    return EXIT_FINDINGS if actionable(result.findings) else EXIT_OK


def cmd_dedupe(args) -> int:
    if args.prefer and args.prefer not in BY_ID:
        print(f"error: unknown client '{args.prefer}'. Known: {', '.join(sorted(BY_ID))}",
              file=sys.stderr)
        return EXIT_ERROR
    home = _home_arg(args)
    result = scan_all(home=home)
    plan = build_plan(result, include_drift=args.include_drift,
                      prune_dead=args.prune_dead, prefer=args.prefer)

    dry = not args.apply
    print("Dedupe plan (dry-run)" if dry else "Dedupe plan")
    print("=" * 60)
    for line in describe_plan(plan):
        print(line)

    if not plan.actions:
        return EXIT_FINDINGS if actionable(result.findings) else EXIT_OK

    if dry:
        print("-" * 60)
        print("Dry run: no files were changed. Re-run with --apply --yes to apply.")
        return EXIT_FINDINGS

    if not args.yes:
        print("Refusing to apply: pass --yes to confirm you want these files rewritten.",
              file=sys.stderr)
        return EXIT_ERROR

    backup_dir = Path(args.backup_dir).expanduser() if args.backup_dir else \
        Path.home() / ".mcp-dedupe" / "backups"
    removed = apply_plan(plan, result, backup_dir)
    print("-" * 60)
    print(f"Applied. Backups saved under {backup_dir}.")
    for cid in sorted(removed):
        print(f"  {cid}: removed {', '.join(sorted(removed[cid]))}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcp-dedupe",
        description="Detect duplicate / drifted / dead MCP server configs across "
                    "Claude Desktop, Cursor, VS Code and Codex, and dedupe them.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--home", default=None,
                   help="Override the home directory configs are resolved from "
                        "(useful for testing).")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="Scan client configs and report findings (read-only).")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.add_argument("--home", default=None,
                   help="Override the home directory configs are resolved from.")
    s.set_defaults(func=cmd_scan)

    d = sub.add_parser("dedupe",
                       help="Plan or apply dedupe of duplicated / dead servers.")
    d.add_argument("--dry-run", action="store_true",
                   help="Show the plan without changing files (default).")
    d.add_argument("--apply", action="store_true",
                   help="Rewrite config files. Requires --yes.")
    d.add_argument("--yes", action="store_true",
                   help="Explicit confirmation for --apply.")
    d.add_argument("--prefer", default=None, metavar="CLIENT",
                   help="When resolving duplicates/drift, keep this client's copy "
                        f"({', '.join(sorted(BY_ID))}).")
    d.add_argument("--include-drift", action="store_true",
                   help="Also resolve drifted duplicates (same name, different config). "
                        "Without this flag drift is reported but left untouched.")
    d.add_argument("--prune-dead", action="store_true",
                   help="Also remove servers whose command/cwd is a dead link.")
    d.add_argument("--backup-dir", default=None,
                   help="Where to store backups (default ~/.mcp-dedupe/backups).")
    d.add_argument("--home", default=None,
                   help="Override the home directory configs are resolved from.")
    d.set_defaults(func=cmd_dedupe)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        return EXIT_OK
    except Exception as exc:  # never crash with a traceback on bad input
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
