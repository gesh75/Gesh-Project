#!/bin/bash
# GESH Main Server — port 9000
#
# RULE #57: never `source venv/bin/activate` in a launcher. A venv copied from
# another repo keeps the ORIGINAL absolute path inside `activate`, which then puts
# the WRONG venv first on PATH. That took :9003 down on 2026-08-04 (wrong
# sys.path, anyio backend imported lazily → 500 on every route). Invoke the
# interpreter by ABSOLUTE PATH and assert sys.prefix.
#
# RULE #55: launchd hands us a minimal PATH with no Homebrew, so a bare `python3`
# here cannot be relied on to resolve to anything useful.
set -euo pipefail
ulimit -n 65536
cd "$(dirname "$0")"

# Rule #55: launchd PATH has no Homebrew and no ~/.local/bin. The Grok CLI
# used by llm_failover lives in ~/.local/bin; without this export,
# grok_available() is False for every dashboard LLM call and the Anthropic
# credit-exhaustion path 500s instead of failing over.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

REPO="$(pwd)"
PY="$REPO/venv/bin/python3"

# Tracker DB (wash_sale_log etc.) lives in the engine repo. gesh_core.tax_harvester
# resolves it via GESH_TRACKER_DB; default to the sibling engine repo's db so the
# /api/tax-harvest endpoint reads the same db as before the gesh-core extraction.
export GESH_TRACKER_DB="${GESH_TRACKER_DB:-$(dirname "$(pwd)")/gesh-project-trading/gesh_financial.db}"

if [ ! -x "$PY" ]; then
    echo "FATAL: $PY missing or not executable — repair with: python3 -m venv $REPO/venv" >&2
    exit 1
fi

# Fail loudly if the venv is mispointed or incomplete, rather than half-running
# with the wrong interpreter. .resolve() follows the repo's venv symlink to the
# shared tree under ~/Projects/gesh-venvs/.
"$PY" - <<'PYCHECK' || exit 1
import importlib, importlib.util, os, pathlib, sys

want = (pathlib.Path(os.getcwd()) / "venv").resolve()
have = pathlib.Path(sys.prefix).resolve()
if have != want:
    sys.stderr.write(
        f"FATAL: venv mispointed — sys.prefix={have} expected={want}\n"
        f"       repair: python3 -m venv {want}\n")
    sys.exit(1)

missing = [m for m in ("fastapi", "uvicorn", "starlette", "anyio")
           if importlib.util.find_spec(m) is None]
if missing:
    sys.stderr.write(f"FATAL: venv missing modules: {', '.join(missing)}\n")
    sys.exit(1)

try:
    importlib.import_module("anyio._backends._asyncio")
except Exception as exc:  # noqa: BLE001 — any failure here must abort the launch
    sys.stderr.write(
        f"FATAL: anyio asyncio backend unusable ({exc!r}) — every HTTP request "
        f"would return 500.\n"
        f"       repair: {sys.executable} -m pip install --force-reinstall anyio\n")
    sys.exit(1)

print(f"[start_gesh_main] venv OK: {have} (python {sys.version.split()[0]})")
PYCHECK

exec "$PY" server.py
