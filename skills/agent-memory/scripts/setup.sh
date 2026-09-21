#!/usr/bin/env bash
set -euo pipefail

if (( $# != 0 )); then
  echo "usage: setup.sh" >&2
  exit 2
fi

# Resolve the runtime from this script, never from the caller's working directory.
script_source=${BASH_SOURCE[0]}
case "$script_source" in
  */*) script_parent=${script_source%/*} ;;
  *) script_parent=. ;;
esac
script_dir=$(cd -- "$script_parent" && pwd -P)
cli="$script_dir/agent_memory.py"
repo_root=$(cd -- "$script_dir/../../.." && pwd -P)

if ! command -v python3 >/dev/null 2>&1; then
  echo "agent-memory setup: Python 3.10 or newer is required" >&2
  exit 1
fi
python_cmd=$(command -v python3)

# The complete scripts directory is the supported Python runtime. Check its
# version, sqlite3 module, and FTS5 support before making any user-state change.
if ! "$python_cmd" - "$cli" <<'PY'
import sqlite3
import sys
from pathlib import Path

cli = Path(sys.argv[1])
if sys.version_info < (3, 10):
    raise SystemExit("agent-memory setup: Python 3.10 or newer is required")
if not cli.is_file():
    raise SystemExit(f"agent-memory setup: Python entry is missing: {cli}")
try:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE VIRTUAL TABLE probe USING fts5(content)")
    connection.close()
except sqlite3.Error as exc:
    raise SystemExit(f"agent-memory setup: SQLite with FTS5 is required: {exc}")
PY
then
  exit 1
fi

# Reuse memory_config for the canonical settings precedence and database path.
# lstat distinguishes a genuinely absent file from malformed files, dangling
# symlinks, directories, and other failures that setup must not overwrite.
if state=$("$python_cmd" - "$script_dir" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from memory_config import MemoryError, database_path, default_settings_path, load_settings

settings_path = default_settings_path()
try:
    settings_path.lstat()
except FileNotFoundError:
    print("settings_missing")
    raise SystemExit(0)
except OSError as exc:
    print(f"agent-memory setup: settings I/O error at {settings_path}: {exc}", file=sys.stderr)
    raise SystemExit(2)

try:
    settings = load_settings(settings_path)
    db_path = database_path(settings, settings_path)
except (MemoryError, OSError) as exc:
    print(f"agent-memory setup: invalid settings at {settings_path}: {exc}", file=sys.stderr)
    raise SystemExit(2)

try:
    db_path.lstat()
except FileNotFoundError:
    print("database_missing")
except OSError as exc:
    print(f"agent-memory setup: database I/O error at {db_path}: {exc}", file=sys.stderr)
    raise SystemExit(2)
else:
    print("ready")
PY
); then
  :
else
  rc=$?
  exit "$rc"
fi

case "$state" in
  settings_missing)
    "$python_cmd" "$cli" --json init >/dev/null
    "$python_cmd" "$cli" --json sync >/dev/null
    ;;
  database_missing)
    "$python_cmd" "$cli" --json sync >/dev/null
    ;;
  ready)
    # A present index is never synced implicitly. Status classifies corruption,
    # incomplete schema, permissions, and other SQLite failures.
    ;;
  *)
    echo "agent-memory setup: internal state probe returned: $state" >&2
    exit 2
    ;;
esac

set +e
"$python_cmd" "$cli" --json status
rc=$?
set -e
if (( rc != 0 )); then
  echo "agent-memory setup: existing index is unhealthy; settings and database were left unchanged" >&2
  exit "$rc"
fi

# Installation is optional and happens only after the registry is known healthy.
# Validate this exact checkout and package-wide bin surface before invoking pnpm.
linked=false
manifest="$repo_root/package.json"
if "$python_cmd" - "$manifest" "$cli" <<'PY' >/dev/null 2>&1
import json
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
cli = Path(sys.argv[2]).resolve()
try:
    package = json.loads(manifest.read_text(encoding="utf-8"))
    bins = package.get("bin")
    mapped = (manifest.parent / bins["agent-memory"]).resolve()
except (OSError, ValueError, TypeError, KeyError):
    raise SystemExit(1)
expected_bins = {"arcp", "agent-memory", "sandbox-ctl"}
if (
    package.get("name") != "my-claude-plugins"
    or set(bins) != expected_bins
    or mapped != cli
):
    raise SystemExit(1)
PY
then
  if command -v node >/dev/null 2>&1 && command -v pnpm >/dev/null 2>&1; then
    pnpm_cmd=$(command -v pnpm)
    if "$pnpm_cmd" --version >/dev/null 2>&1; then
      echo "agent-memory setup: linking repository package from $repo_root" >&2
      echo "agent-memory setup: this global link exposes three bins: arcp, agent-memory, sandbox-ctl" >&2
      if ! (cd -- "$repo_root" && "$pnpm_cmd" link --global >/dev/null); then
        echo "agent-memory setup: pnpm global link failed; no CLI installation is being reported" >&2
        exit 1
      fi
      linked=true
    fi
  fi
fi

if [[ "$linked" == true ]]; then
  if command -v agent-memory >/dev/null 2>&1; then
    echo "agent-memory setup: repository global link complete; bare command is on PATH" >&2
  else
    echo "agent-memory setup: repository global link complete, but its global bin directory is not on PATH" >&2
    echo "agent-memory setup: Python entry remains available at: $python_cmd $cli" >&2
  fi
else
  echo "agent-memory setup: Python entry ready: $python_cmd $cli" >&2
  echo "agent-memory setup: bare command not installed; keep the complete scripts directory" >&2
fi
