#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.13 or 3.14 is required. Install it with your distribution package manager, then rerun this script." >&2
  exit 1
fi

exec "$PYTHON_BIN" "$ROOT_DIR/scripts/install_native.py" "$@"
