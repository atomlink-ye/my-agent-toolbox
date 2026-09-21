# my-claude-plugins

Personal Claude Code plugin marketplace.

## Skills

The main bundled skills are shipped under a single plugin (`my-skills`).

| Skill | Description |
|-------|-------------|
| [agent-memory](skills/agent-memory/) | Local file-first Agent Memory with cross-project routing, stable IDs, lifecycle, duplicate-aware capture, Doctor, tags, FTS, and links/backlinks |
| [mve-first-development](skills/mve-first-development/) | Shape/Probe/Prove/Protect/Harden stage router for early product delivery |
| [opencode-companion](skills/opencode-companion/) | OpenCode serve/session/job/review runtime via direct companion scripts |
| [paseo-companion](skills/paseo-companion/) | Paseo CLI runtime: agents, terminals, schedules, worktrees, host/port targeting |
| [agent-runtime-control-panel](skills/agent-runtime-control-panel/) | Durable local control plane for ARCP Actors, Goals, live-validated Paseo sessions, and safe-point deliveries |
| [google-workspace](skills/google-workspace/) | Google Docs/Drive/Sheets via the `gws` CLI |
| [mcp-skill](skills/mcp-skill/) | On-demand MCP server invocation via MCPorter |
| [figma-console](skills/figma-console/) | Schema-first local Figma Desktop Bridge workflow |

### Optional standalone plugins

`skill-creator` remains available as a standalone optional plugin.

### Upgrade notes

- `opencode-orchestrator` was folded into runtime-specific companion skills.
- `task-iteration`, `agentic-orchestration`, and `team-lead-orchestration` were removed from the bundle.
- `daytona-companion` was removed; sandbox lifecycle is superseded by `sandbox-ctl`.
- `paseo-reminder` and its loopback port 8787 are retired. `agent-runtime-control-panel` is the only
  cooperation path; its Channel, Delivery, Knowledge and Result concepts replace the reminder,
  message, child-watch and correction-gate surfaces.

## Agent Memory quick start

Resolve the directory of the `agent-memory` skill that your runtime actually loaded; do
not guess it from CWD. A discovered standalone skill does not necessarily install a bare
command:

```bash
AM_SKILL_DIR=/absolute/path/to/loaded/agent-memory
if ! test -f "$AM_SKILL_DIR/SKILL.md" || \
   ! test -f "$AM_SKILL_DIR/scripts/agent_memory.py"; then
  printf '%s\n' "invalid agent-memory skill directory: $AM_SKILL_DIR" >&2
  exit 2
fi
if command -v agent-memory >/dev/null 2>&1; then
  AM=(agent-memory)
else
  AM=(python3 "$AM_SKILL_DIR/scripts/agent_memory.py")
fi
"${AM[@]}" --json status
```

Status exits `0` when ready. If and only if it reports `settings not found`, complete a
fresh bootstrap:

```bash
"${AM[@]}" --json init
"${AM[@]}" --json sync
"${AM[@]}" --json status
"${AM[@]}" --json search "learnings agent server" --path /abs/path/to/project
```

Requirements: Python 3.10+, standard-library SQLite with FTS5, and the complete skill
`scripts/` directory. `init` creates settings and `sync` creates the index. On an existing
registry, classify with `status`: run only `sync` for a missing index, and do not overwrite
malformed settings or a corrupt database with `init --force`.

Claude Code plugin content may set
`AM_SKILL_DIR="${CLAUDE_PLUGIN_ROOT}/skills/agent-memory"`; this repository has not
end-to-end verified that runtime substitution, and ordinary Bash, Codex, and standalone
skill copies must use the actual absolute skill directory instead.

Stable cross-project references use `memory://mem_xxx`. Legacy Markdown remains valid; explicit ID migration is available through `skills/agent-memory/scripts/migrate_ids.py`.

See [docs/agent-memory.md](docs/agent-memory.md) and [docs/agent-memory-doctor.md](docs/agent-memory-doctor.md).

## Installation

Configure this repository as a local directory or GitHub Claude Code marketplace, then enable `my-skills@my-claude-plugins`.

## Development

```bash
pnpm install
pnpm test
python3 -m unittest discover -s eval/agent-memory/tests -p 'test_*.py'
```
