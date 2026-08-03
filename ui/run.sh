#!/usr/bin/env bash
# Launch the Music Sorter UI (Add Cues + Ready for Sort).
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(cd .. && pwd)"

# Prefer the shared AutoCue venv; fall back to a local ui/.venv.
if [[ -x "$REPO_ROOT/venv/bin/python" ]]; then
  PY="$REPO_ROOT/venv/bin/python"
  PIP="$REPO_ROOT/venv/bin/pip"
elif [[ -x .venv/bin/python ]]; then
  PY=".venv/bin/python"
  PIP=".venv/bin/pip"
else
  echo "Creating ui/.venv and installing UI dependencies…"
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
  # Repo root must be importable for vdj_database_safety / vdj_cuer.
  .venv/bin/pip install -r "$REPO_ROOT/requirements.txt" || true
  PY=".venv/bin/python"
  PIP=".venv/bin/pip"
fi

# Ensure UI deps (FastAPI / uvicorn) are present in the chosen env.
if ! "$PY" -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "Installing UI dependencies into $(dirname "$PY")…"
  "$PIP" install -r requirements.txt
fi

# Load Gemini key from repo .env (or local ui/.env).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
elif [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

PORT="${MUSIC_SORTER_PORT:-8787}"
echo "Music Sorter UI → http://127.0.0.1:${PORT}"
echo "  (AutoCue repo: $REPO_ROOT)"
exec "$PY" -m uvicorn app:app --host 127.0.0.1 --port "$PORT"
