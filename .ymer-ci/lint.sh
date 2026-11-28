#!/bin/sh
# Written by Ymer Factory. The same script runs in Ymer and in your CI.
set -eu
here=$(pwd)
cd "${1:-.}"
if command -v ruff >/dev/null 2>&1; then
  ruff check --no-cache --select E9,F63,F7,F82 --output-format concise .
else
  echo "ruff is not installed here; checking syntax only."
  python "$here/.ymer-ci/pycheck.py" .
fi
