"""Smoke tests for mcp-dedupe. Run with: python3 -m unittest discover -s tests -v"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_dedupe.cli import main as cli_main
from mcp_dedupe.findings import scan_all
from mcp_dedupe.sync import apply_plan, build_plan

FS_SERVER = {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}
BRAVE_A = {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"]}
BRAVE_B = {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search", "--other"]}
FETCH_A = {"command": "uvx", "args": ["mcp-server-fetch"]}
FETCH_B = {"command": "uvx", "args": ["mcp-server-fetch", "--verbose"]}
DEAD_SERVER = {"command": "definitely-not-a-real-command-xyz-123"}
REMOTE_SERVER = {"url": "https://example.com/mcp"}


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def json_config(servers: dict) -> str:
    return json.dumps({"mcpServers": servers}, indent=2)


def make_home(base: Path, files: dict) -> Path:
    home = base / "home"
    for rel, content in files.items():
        write(home / rel, content)
    return home


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_identical_duplicate_detected(self):
        home = make_home(self.tmp, {
            ".config/Claude/claude_desktop_config.json": json_config({"filesystem": FS_SERVER}),
            ".cursor/mcp.json": json_config({"filesystem": FS_SERVER}),
        })
        result = scan_all(home=home)
        dups = [f for f in result.findings if f.kind == "duplicate"]
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0].server, "filesystem")
        self.assertEqual(sorted(dups[0].clients), ["claude-desktop", "cursor"])

    def test_drift_detected(self):
        home = make_home(self.tmp, {
            ".config/Claude/claude_desktop_config.json": json_config({"brave": BRAVE_A}),
            ".cursor/mcp.json": json_config({"brave": BRAVE_B}),
        })
        result = scan_all(home=home)
        drifts = [f for f in result.findings if f.kind == "drift"]
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0].server, "brave")

    def test_dead_link_command_not_found(self):
        home = make_home(self.tmp, {
            ".cursor/mcp.json": json_config({"oldtool": DEAD_SERVER}),
        })
        result = scan_all(home=home)
        dead = [f for f in result.findings if f.kind == "dead-link"]
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0].server, "oldtool")
        self.assertIn("not found", dead[0].detail)

    def test_dead_link_absolute_path(self):
        home = make_home(self.tmp, {
            ".cursor/mcp.json": json_config(
                {"local": {"command": "/nonexistent/bin/server"}}),
        })
        result = scan_all(home=home)
        dead = [f for f in result.findings if f.kind == "dead-link"]
        self.assertEqual(len(dead), 1)
        self.assertIn("does not exist", dead[0].detail)

    def test_remote_url_not_flagged_dead(self):
        home = make_home(self.tmp, {
            ".cursor/mcp.json": json_config({"web": REMOTE_SERVER}),
        })
        result = scan_all(home=home)
        dead = [f for f in result.findings if f.kind == "dead-link"]
        self.assertEqual(dead, [])
        remote = [f for f in result.findings if f.kind == "remote"]
        self.assertEqual(len(remote), 1)

    def test_malformed_config_reported(self):
        home = make_home(self.tmp, {
            ".cursor/mcp.json": "{not valid json",
        })
        result = scan_all(home=home)
        bad = [f for f in result.findings if f.kind == "unreadable"]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0].clients, ["cursor"])

    def test_vscode_nested_layout_parsed(self):
        home = make_home(self.tmp, {
            ".config/Code/User/settings.json": json.dumps(
                {"editor.fontSize": 14, "mcp": {"servers": {"filesystem": FS_SERVER}}}),
        })
        result = scan_all(home=home)
        scan = next(s for s in result.scans if s.client.id == "vscode")
        self.assertEqual(list(scan.servers), ["filesystem"])

    def test_codex_toml_parsed(self):
        home = make_home(self.tmp, {
            ".codex/config.toml": (
                'model = "gpt-5"\n\n[mcp_servers.filesystem]\n'
                'command = "npx"\nargs = ["-y", "srv"]\n'
            ),
        })
        result = scan_all(home=home)
        scan = next(s for s in result.scans if s.client.id == "codex")
        self.assertEqual(list(scan.servers), ["filesystem"])
        self.assertEqual(scan.servers["filesystem"]["command"], "npx")

    def test_scan_json_output(self):
        home = make_home(self.tmp, {
            ".cursor/mcp.json": json_config({"oldtool": DEAD_SERVER}),
        })
        proc = subprocess.run(
            [sys.executable, "-m", "mcp_dedupe", "scan", "--format", "json",
             "--home", str(home)],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
        )
        self.assertEqual(proc.returncode, 1)  # findings present
        data = json.loads(proc.stdout)
        kinds = {f["kind"] for f in data["findings"]}
        self.assertIn("dead-link", kinds)


class DedupeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.backup = self.tmp / "backups"

    def _home(self):
        return make_home(self.tmp, {
            # "filesystem": identical in 3 clients -> duplicate
            ".config/Claude/claude_desktop_config.json":
                json_config({"filesystem": FS_SERVER, "brave": BRAVE_A}),
            ".cursor/mcp.json":
                json_config({"filesystem": FS_SERVER, "oldtool": DEAD_SERVER}),
            # "fetch": same name, different config -> drift
            ".codex/config.toml":
                '[mcp_servers.filesystem]\ncommand = "npx"\n'
                'args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]\n'
                '\n[mcp_servers.fetch]\ncommand = "uvx"\nargs = ["mcp-server-fetch"]\n',
            ".config/Code/User/settings.json": json.dumps(
                {"mcp": {"servers": {"fetch": FETCH_B}}}),
        })

    def test_dry_run_writes_nothing(self):
        home = self._home()
        before = {str(p): p.read_bytes() for p in home.rglob("*") if p.is_file()}
        result = scan_all(home=home)
        plan = build_plan(result)
        self.assertTrue(plan.actions)  # there is work to do...
        # ...but a dry run must not touch anything. Just don't call apply_plan.
        after = {str(p): p.read_bytes() for p in home.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_apply_removes_duplicates_and_backs_up(self):
        home = self._home()
        result = scan_all(home=home)
        plan = build_plan(result)  # drift + dead-link excluded by default
        removed = apply_plan(plan, result, self.backup)
        # identical duplicate: claude-desktop keeps (priority), cursor+codex lose it
        self.assertEqual(removed.get("cursor"), ["filesystem"])
        self.assertEqual(removed.get("codex"), ["filesystem"])
        self.assertNotIn("claude-desktop", removed)
        cursor_cfg = json.loads((home / ".cursor/mcp.json").read_text())
        self.assertNotIn("filesystem", cursor_cfg["mcpServers"])
        self.assertIn("oldtool", cursor_cfg["mcpServers"])  # dead kept: no --prune-dead
        # backup exists
        backups = list(self.backup.rglob("*.bak"))
        self.assertTrue(backups)
        # drift untouched by default
        codex_text = (home / ".codex/config.toml").read_text()
        self.assertIn("mcp-server-fetch", codex_text)

    def test_apply_requires_explicit_yes(self):
        home = self._home()
        proc = subprocess.run(
            [sys.executable, "-m", "mcp_dedupe", "dedupe", "--apply",
             "--home", str(home)],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--yes", proc.stderr + proc.stdout)
        # nothing changed
        cursor_cfg = json.loads((home / ".cursor/mcp.json").read_text())
        self.assertIn("filesystem", cursor_cfg["mcpServers"])

    def test_drift_resolved_with_include_drift_and_prefer(self):
        home = self._home()
        result = scan_all(home=home)
        plain = build_plan(result)
        self.assertTrue(any("drift" in s for s in plain.skipped))
        plan = build_plan(result, include_drift=True, prefer="vscode")
        removed = apply_plan(plan, result, self.backup)
        # vscode preferred: its "fetch" survives, codex's copy removed
        self.assertIn("fetch", removed.get("codex", []))
        vscode_cfg = json.loads((home / ".config/Code/User/settings.json").read_text())
        self.assertIn("fetch", vscode_cfg["mcp"]["servers"])
        codex_text = (home / ".codex/config.toml").read_text()
        self.assertNotIn("[mcp_servers.fetch]", codex_text)

    def test_prune_dead_removes_dead_servers(self):
        home = self._home()
        result = scan_all(home=home)
        plan = build_plan(result, prune_dead=True)
        removed = apply_plan(plan, result, self.backup)
        self.assertIn("oldtool", removed.get("cursor", []))
        cursor_cfg = json.loads((home / ".cursor/mcp.json").read_text())
        self.assertNotIn("oldtool", cursor_cfg["mcpServers"])


if __name__ == "__main__":
    unittest.main()
