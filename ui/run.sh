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

# Load Gemini key from repo .env, ui/.env, or Desktop checkout (legacy path).
_load_env() {
  local f="$1"
  if [[ -f "$f" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$f"
    set +a
    return 0
  fi
  return 1
}
_load_env .env \
  || _load_env "$REPO_ROOT/.env" \
  || _load_env "$HOME/Desktop/vdj-automatic-cuer/.env" \
  || _load_env "$HOME/Desktop/vdj-automatic-cuer/ui/.env" \
  || true

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "WARNING: GEMINI_API_KEY is not set. AutoCue will fail until you add it to" >&2
  echo "  $REPO_ROOT/.env  or  $HOME/Desktop/vdj-automatic-cuer/.env" >&2
fi

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
export MUSIC_SORTER_AUTOCUE_CONCURRENCY="${MUSIC_SORTER_AUTOCUE_CONCURRENCY:-1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
export VECLIB_MAXIMUM_THREADS="${VECLIB_MAXIMUM_THREADS:-1}"
export PYTHONUNBUFFERED=1

PORT="${MUSIC_SORTER_PORT:-8787}"
echo "Music Sorter UI → http://127.0.0.1:${PORT}"
echo "  (AutoCue repo: $REPO_ROOT)"
exec "$PY" -m uvicorn app:app --host 127.0.0.1 --port "$PORT"
