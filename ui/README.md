# Music Sorter UI

Booth UI for **VDJ Station**: review AutoCue markers, re-run cues/loops, recs, assemble, then sort cued tracks into House / Zouk emotion folders while **preserving VirtualDJ FilePath-linked cues**.

This package lives at `ui/` inside the [vdj-automatic-cuer](https://github.com/langerkirill/vdj-automatic-cuer) monorepo. It reuses `vdj_database_safety.py` and `vdj_cuer/` from the repo root — it does **not** reimplement VirtualDJ XML safety.

## Quick start

From the repo root (after `./setup.sh`):

```bash
./ui/run.sh
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787).

Uses `GEMINI_API_KEY` from the repo `.env` (or `ui/.env`). Sorter defaults to `gemini-3.7-flash`. Optional: `MUSIC_SORTER_GEMINI_MODEL` / `GEMINI_MODEL`. AutoCue defaults to `gemini-3.7-flash`.

## Modes

| Mode | Purpose |
|------|---------|
| **Add Cues** | Review queue under `Cues/Add Cues`. Beatgrid preflight, AutoCue Both/Cues/Loops, confirm a color lane, **Sort** into House/Zouk + Cues Sorted |
| **Set Overview** | Pajamathon / set copies: copy cues, approve, must-play, send back |
| **Recs** | Live-from-VirtualDJ next-track suggestions |
| **Assemble** | Build a Pajamathon set folder |
| **Practice / Best for set** | Mix scoring and keepers |

## Paths (defaults)

| Role | Path |
|------|------|
| Add Cues | `~/Music/DJ/Music/Cues/Add Cues` |
| Ready for Sort | `~/Music/DJ/Music/Cues/Ready For Sort` |
| Cues Sorted | `~/Music/DJ/Music/Cues/Cues Sorted` |
| House | `~/Music/DJ/Music/House` |
| Zouk | `~/Music/DJ/Music/Zouk` |
| VDJ database | `~/Library/Application Support/VirtualDJ/database.xml` |

## Tests

```bash
cd ui
PYTHONPATH=..:../.  ../venv/bin/python -m pytest tests/ -q
# or:
PYTHONPATH=..:$(pwd) ../venv/bin/python -m pytest tests/ -q
```

Also run AutoCue relocate safety tests from the repo root:

```bash
./venv/bin/python -m pytest tests/test_filepath_relocate.py -q
```
