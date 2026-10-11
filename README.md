# mcp-config-dedupe

Detect duplicate, drifted, and dead MCP server configurations across your AI clients — then dedupe them in one command.

If you run Claude Desktop, Cursor, VS Code, and Codex side by side, you have probably pasted the same `mcpServers` block into four different config files. Over time they rot: the same server gets configured twice, one copy gets different args than the other (**drift**), and old entries point at commands you uninstalled months ago (**dead links**). `mcp-dedupe` scans every client, shows you the mess, and cleans it up — with backups and a dry-run first.

Stdlib-only. No dependencies, no network calls, no telemetry.

## Install

```bash
pip install mcp-config-dedupe
```

Requires Python 3.11+.

## Usage

```bash
# Scan all supported clients (read-only) and print findings
mcp-dedupe scan

# Machine-readable report (exit code 1 when findings exist — CI friendly)
mcp-dedupe scan --format json

# Preview what dedupe would change (default; writes nothing)
mcp-dedupe dedupe

# Actually dedupe: backups are taken first, confirmation is explicit
mcp-dedupe dedupe --apply --yes

# Also resolve drifted duplicates, preferring Cursor's copy
mcp-dedupe dedupe --apply --yes --include-drift --prefer cursor

# Also remove servers whose command/cwd no longer exists
mcp-dedupe dedupe --apply --yes --prune-dead
```

What it detects:

| Finding | Meaning |
|---|---|
| `DUPLICATE` | Same server name with an identical config in 2+ clients — safe to dedupe automatically |
| `DRIFT` | Same server name but different command/args/env across clients — reported, and only touched with `--include-drift` |
| `DEAD-LINK` | `command` not on `PATH` / path doesn't exist, or `cwd` is gone — only removed with `--prune-dead` |
| `REMOTE` | URL-based server; can't be verified offline, listed for visibility |
| `UNREADABLE` | Config file exists but can't be parsed |

Supported clients: **Claude Desktop**, **Cursor**, **VS Code** (`mcp.servers` settings), **Codex CLI** (`~/.codex/config.toml`).

Safety model: `scan` and the default `dedupe` (dry-run) never write. `--apply` refuses to run without `--yes`, backs up every touched file to `~/.mcp-dedupe/backups/<timestamp>/` first, and only rewrites configs that parsed cleanly. Drifted duplicates are never auto-merged — you pick the winner with `--prefer`.

## Differentiation

- **vs `mcp-audit-tool` (config security auditing):** auditing tells you a config looks risky; `mcp-dedupe` takes action — it removes the duplicate and dead entries across clients in one command.
- **vs editing JSON by hand:** one scan gives you a unified cross-client view with drift highlighted, instead of diffing four files in four different locations yourself.
- **vs `mcp-config-sync` (merge-everything sync):** syncing unions every server into every client; dedupe does the opposite job — it collapses redundant copies down to one canonical entry and prunes what's dead.

## Exit codes

- `0` — success (scan clean / apply completed)
- `1` — findings present (scan or dry-run)
- `2` — usage error, or `--apply` without `--yes`

## Development

```bash
python3 -m unittest discover -s tests -v   # 14 smoke tests, stdlib only
```

## License

MIT — see [LICENSE](LICENSE).
