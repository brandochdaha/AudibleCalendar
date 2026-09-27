#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "No native installation found. Run scripts/install-linux.sh first." >&2
  exit 1
fi

exec "$PYTHON_BIN" "$ROOT_DIR/scripts/run_native.py" "$@"
