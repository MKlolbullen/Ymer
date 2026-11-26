#!/usr/bin/env bash
# Install and run the local development stack. Run from any directory.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
root=$(pwd -P)

usage() {
  cat <<'HELP'
Usage: ./install.sh [--install-only | --help]

Install Ymer's Python and Node dependencies, start Compose infrastructure,
migrate the database, then run the API, collaboration server, UI and configured
workers in this terminal. Ctrl+C stops the host processes. To stop the containers
and keep their volumes, run "docker compose stop" from the repository root.

--install-only  Prepare .env and install project packages without starting services.
Prerequisites: Bash, Python 3.12+ with venv, Node.js 22.18+ and npm.
Starting services also requires curl and a running Docker engine with Compose v2.
Factory Docker builds also use make. Set PYTHON to select a Python interpreter.
HELP
}

fail() { printf 'Ymer: %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null || fail "Missing command: $1. See ./install.sh --help."; }

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  ''|--install-only) ;;
  *) usage >&2; exit 2 ;;
esac
(( $# <= 1 )) || { usage >&2; exit 2; }
python=${PYTHON:-python3}
require "$python"
"$python" -c 'import sys; sys.exit(sys.version_info < (3, 12))' || fail "Select Python 3.12+ with PYTHON=/path/to/python."
require node
require npm
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major < 22 || (major === 22 && minor < 18) ? 1 : 0)' || fail "Node.js 22.18+ is required."
if [[ ${1:-} != --install-only ]]; then
  require curl
  require docker
  docker compose version >/dev/null || fail "Install the Docker Compose v2 plugin."
  docker info >/dev/null || fail "Start Docker and check that your user can reach its daemon."
fi

# Keep the lock on the shell's open descriptor across setup and the foreground run.
exec 9>>.ymer-install.lock
"$python" -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null ||
  fail "Another installer or development stack is already running in this checkout."

if [[ ! -e .env ]]; then
  "$python" - <<'PY'
from pathlib import Path
import os
import secrets
import tempfile

text = Path(".env.example").read_text()
for key in ("YMER_SECRET_KEY", "YMER_COLLAB_SECRET"):
    lines = text.splitlines(keepends=True)
    text = "".join(f"{key}={secrets.token_hex(32)}\n" if line.startswith(f"{key}=") else line for line in lines)
with tempfile.NamedTemporaryFile(mode="w", dir=".", prefix=".env.") as fresh:
    fresh.write(text)
    fresh.flush()
    try:
        os.link(fresh.name, ".env")
    except FileExistsError:
        pass
PY
fi

venv="$root/backend/.venv"
if [[ ! -x "$venv/bin/python" ]]; then "$python" -m venv "$venv"; fi
"$venv/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 12))' ||
  fail "backend/.venv uses an older Python. Move it aside, then rerun with Python 3.12+."
echo "Installing backend, collaboration and frontend dependencies..."
backend_install() {
  if command -v uv >/dev/null; then
    uv pip install --python "$venv/bin/python" -e "./backend[$1]"
  else
    "$venv/bin/python" -m pip --version >/dev/null 2>&1 || "$venv/bin/python" -m ensurepip
    "$venv/bin/python" -m pip install -e "./backend[$1]"
  fi
}
backend_install dev
(cd collab && npm ci --include=dev)
(cd frontend && npm ci --include=dev)
if [[ ${1:-} == --install-only ]]; then echo "Ymer dependencies installed."; exit 0; fi

echo "Starting PostgreSQL, Redis and MinIO..."
docker compose up -d --wait --wait-timeout 120 postgres redis minio
docker compose run --rm --no-deps minio-init
(cd backend && "$venv/bin/alembic" upgrade head)

# Read settings through Ymer itself, with the same .env precedence as Flask.
features=$(cd backend && "$venv/bin/python" - <<'PY'
from ymer.config import get_settings

s = get_settings()
if not s.ai_index_inline and s.ai_embedding_provider != "off":
    print("ai-worker")
if not s.factory_run_inline and s.factory_sandbox != "off":
    print("factory-worker")
if s.factory_sandbox == "docker":
    print("factory-images")
if s.factory_sandbox == "modal":
    print("modal-sandbox")
PY
)
if [[ $features == *modal-sandbox* ]]; then
  echo "Installing the Modal client for the hosted build sandbox..."
  backend_install dev,modal
fi
if [[ $features == *factory-images* ]]; then
  require make
  make factory-images
fi
collab_port=$(node --env-file=.env -p 'process.env.YMER_COLLAB_PORT || 1234')
export VITE_COLLAB_PROXY_TARGET="${VITE_COLLAB_PROXY_TARGET:-ws://localhost:$collab_port}"

# Each host service gets its own process group so tools it spawns stop with it.
set -m
pids=()
services=()
start() {
  local name=$1 dir=$2
  shift 2
  (cd "$dir" && exec "$@") &
  pids+=("$!")
  services+=("$name")
}
stop_host_services() {
  local pid
  trap - EXIT
  for pid in "${pids[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || true; done
  wait || true
}
trap stop_host_services EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
check_processes() {
  local i
  for i in "${!pids[@]}"; do
    kill -0 "${pids[i]}" 2>/dev/null || fail "${services[i]} stopped. Check its output above."
  done
}
await_url() {
  local name=$1 url=$2 deadline=$((SECONDS + 60))
  until curl --noproxy '*' -fs --max-time 3 "$url" >/dev/null; do
    check_processes
    (( SECONDS < deadline )) || fail "$name did not become ready at $url."
    sleep 1
  done
  check_processes
}

start API backend "$venv/bin/flask" --app wsgi run --no-reload --port 5000
await_url API http://localhost:5000/api/health/ready
start Collaboration collab node src/main.ts
await_url Collaboration "http://localhost:$collab_port/health"
for worker in ai-worker factory-worker; do
  if [[ $features == *"$worker"* ]]; then start "$worker" backend "$venv/bin/flask" --app wsgi "$worker"; fi
done
start Frontend frontend node node_modules/vite/bin/vite.js --port 5173 --strictPort
await_url Frontend http://localhost:5173/api/health/ready
echo "Ymer is ready at http://localhost:5173. Press Ctrl+C to stop the host services."
while :; do check_processes; sleep 1; done
