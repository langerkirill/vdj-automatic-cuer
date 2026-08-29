# Product

<!-- impeccable:product-schema 1 -->

<!-- Inferred from the repo README, ui/README.md, and the running sorter
     at 127.0.0.1:8787. Not a live init interview. Edit anything that is
     wrong; later Impeccable commands read this file. -->

## Platform

web

## Users

Kirill (and any operator of this local booth) preparing a live DJ set. Typical scene: VirtualDJ is open or just closed, headphones on, a dark room, Pajamathon / House / Zouk crates on disk. The job is to hear a track, trust or fix its cues and beatgrid, pick a color lane, and place the file without breaking VirtualDJ FilePath-linked markers.

## Product Purpose

**VDJ Station** is the local VirtualDJ booth. Music Sorter is the console: listen to AutoCue markers, retry cues or loops, align the grid, recs and assemble a set, then sort cued tracks into House / Zouk emotion folders while preserving VirtualDJ database links.

Success is a crate that is cued on the 1s, colored, and filed, without a second pass in VirtualDJ to repair lost cues.

## Positioning

The mechanism a generic library tagger cannot copy: surgical VirtualDJ `database.xml` writes that keep FilePath-linked cue points attached when a file is relocated, copied onto an existing library/Pajamathon sibling, or promoted from Add Cues to Ready for Sort.

## Operating Context

- Local Flask app at `ui/`, default [http://127.0.0.1:8787](http://127.0.0.1:8787).
- VirtualDJ database at `~/Library/Application Support/VirtualDJ/database.xml`.
- Crates on disk: Add Cues, Ready For Sort, Cues Sorted, House, Zouk, Sets/Pajamathon.
- Modes in the top bar: Add Cues, Set Overview, Practice, Best for set, Recs, Assemble.
- Gemini is used for lane recommendations, AutoCue, recs, and assemble. Sorter default model: `gemini-3.7-flash`.
- Keyboard-first review: Space, J/K, 1–9 cues, C/O place, Z/H speed, G ones.

## Capabilities and Constraints

- Do not reimplement VirtualDJ XML safety; reuse `vdj_database_safety.py` and `vdj_cuer/`.
- Relocate must preserve FilePath-linked cues. Close VirtualDJ before batch grid writes.
- Add Cues "ready" requires at least two cues, two loops, and a beatgrid.
- White / unsorted VDJ color is not a sortable lane.
- Cue colors are semantic (melodic / drums / vocals), not theme decoration.
- Quiet session (`?quiet=1`, `?mute=1`, or WebDriver) must stay silent.

## Brand Commitments

- Product name: **VDJ Station**. UI console: **Music Sorter**.
- Voice: operator, not marketing. Name the action. No SaaS slogans.
- Visual world (user-pinned 2026-08-27): 80s tape deck × night pool × chic VHS. Walnut cheeks, amber VFD, pool-teal water, peach dusk. Cue colors stay semantic (melodic / drums / vocals). Accent swatches: Amber, Pool, Dusk, Peach.

## Evidence on Hand

- Live UI: `ui/static/index.html`, `styles.css`, `app.js`.
- Docs screenshots in `docs/screenshots/`.
- Asset tests in `ui/tests/test_ui_clarity_assets.py` and `test_ux_review_assets.py` encode shipped structure.
- No customer logos, testimonials, or public metrics. Do not invent any.

## Product Principles

- VirtualDJ-safe beats pretty. A beautiful sort that drops cues is a failure.
- The waveform is the work. Chrome exists to get the operator onto the 1 and off again.
- One primary action per mode, on the right rail.
- Counts and state (cued / not cued, VDJ open / closed, AutoCue jobs) stay visible.
- Density over explanation. The operator already knows the crates.

## Accessibility & Inclusion

Keyboard is a first-class input. Visible focus, 12px minimum on functional text, and contrast that holds on the dark booth surface. Not a public WCAG product; do not regress those floors.
