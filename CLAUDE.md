# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Not a software product — a **bug report**. It documents a Chromium/macOS ProMotion rendering
issue (a non-composited CSS animation is presented irregularly at 120 Hz; a concurrent
compositor-eligible `transform` inherits its cadence). The deliverable is `README.md`, and its
entire value is that **every number in it is re-derivable** from raw footage by the scripts in
`analysis/`. Treat the prose and the code as one artifact: an edit that makes a claim the code
can't reproduce, or code whose output no longer matches the prose, is a regression.

- `index.html` — the repro. Single file, no dependencies, no build step. Open it directly.
- `analysis/` — three Python measurement scripts + their own `README.md` (exact invocations).
- `results/` — committed per-run CSV output of the two measurements.
- `footage/` — `SHA256SUMS`, `manifest.csv`, and the generated `segments.csv`. **The raw video
  clips are not in the repo** (they live on Google Drive, linked and SHA-pinned from `README.md`).

## Commands

```bash
# Run the repro
open index.html                              # macOS

# Analysis environment (ffmpeg must also be on PATH — the scripts shell out to it)
pip install -r analysis/requirements.txt     # numpy, pillow

# The scripts take the clip path as the first positional arg; download the footage first.
# All three share ONE set of crop rectangles (tripod-fixed framing, identical across clips):
CARDS="--card-a 215 428 580 190 --card-b 165 778 560 185"
STRIP="--strip 1450 215 450 100"

# Measurement 1 — visible cadence, page NOT instrumented (the primary claim)
python3 analysis/track_cadence.py ~/Downloads/IMG_3836.mov $CARDS --csv results/cadence-cards-alone.csv

# Measurement 2 — which rAF states were observed on the display (uses the on-screen id stamp)
python3 analysis/decode_frame_ids.py ~/Downloads/IMG_3832.mov $STRIP --csv results/session1-incognito-devtools-closed.csv

# Regenerate footage/segments.csv (expects the clips in the given dir; imports the other two scripts)
python3 analysis/build_segments.py ~/Downloads > footage/segments.csv
```

There is no test suite, lint, or CI. "Testing" a change means re-running the relevant script on
the footage and confirming the printed numbers still match `README.md` and `results/*.csv`.
Threshold-sensitivity re-runs (`--threshold 0.4 0.5 0.6`) are documented in `analysis/README.md`.

## Architecture — the cross-file invariants

The three scripts are a system, not independents. Points that require reading several files at once:

- **Shared crop geometry.** `CARD_A`/`CARD_B`/`STRIP` are hard-coded in `build_segments.py` and
  passed on the CLI (`$CARDS`/`$STRIP`) to the other two. They describe one fixed camera framing
  used for all three clips. Change a rectangle → change it in all three places, or results diverge
  silently. A rectangle that fails to cover a card's *full* travel makes the tracker report motion
  as stillness — a convincing wrong answer that has bitten this report before.
- **`build_segments.py` imports `track_cadence` and `decode_frame_ids` as modules** (reuses their
  `profile`/`shift`/`decode`/`sampling_rate`). That's why `__pycache__/` is gitignored.
- **The two measurements are deliberately redundant, not interchangeable.** `track_cadence.py` is
  *primary* — it instruments the page at all (pure pixel cross-correlation). `decode_frame_ids.py`
  is *secondary* — it reads the rAF id stamp, which is itself one main-thread DOM write per frame,
  so it perturbs what it measures and is quoted **only** where a layout animation already dominates.
- **The three clips and their conditions** (see `build_segments.py` `CLIPS` / `footage/manifest.csv`):
  `IMG_3836` = DevTools closed, stamp OFF (the headline, uninstrumented); `IMG_3832` = closed, stamp
  ON; `IMG_3833` = DevTools OPEN, stamp ON (the control).

## Conventions that keep the report trustworthy — do not break these

These are the load-bearing methodology rules. They read as pedantry until you realize the whole
report exists to be un-refutable; preserve them in any edit to code or prose.

- **The sampling-rate gate is non-negotiable.** iPhone slow-motion clips are *not* uniformly
  240 fps; frames in the speed ramp sample every 8th rAF id and would fake "dropped" states. Both
  scripts gate on it (length `--min-run 130` for the unstamped clip; `0.35–0.65` ids/frame for the
  stamped ones) and **print every rejected run — never drop coverage silently.** Any new bounding
  (top-N, sampling, no-retry) must likewise be logged.
- **Never pool across conditions.** Every run is classified `A-alone`/`B-alone`/`both` from the
  motion itself; percentages are aggregated per condition. Pooling conditions is the specific error
  the report was rebuilt to eliminate.
- **Word claims precisely.** Say "rAF states *not observed on the display*", never "dropped/rendered
  frames" — the data cannot prove a finished frame was discarded. Keep the *measured* (Chrome, camera)
  vs *looked-at* (Edge/Opera/Vivaldi/Firefox by eye) distinction intact.
- **Holds of 1 or 3 camera frames are reported, not redistributed** (they're non-physical at 240 fps
  vs 120 Hz), which is why the two percentage columns intentionally don't sum to 100.
- **Every published number must be derivable from the three published clips.** The only exceptions
  (the 80 %/15 % from an unpublished, poorly-framed clip) are explicitly flagged as such wherever
  they appear; keep them flagged.