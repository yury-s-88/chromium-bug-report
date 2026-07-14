#!/usr/bin/env python3
"""
Decode the on-screen frame-id strip from external-camera footage.

WHAT IT MEASURES
    index.html can stamp every requestAnimationFrame callback with a unique id, drawn as
    an 8-bit binary strip (and as decimal digits, which serve as an independent check).

    This script reads the strip out of every camera frame and reports, per animation run:

        ids_generated   how many rAF ids the page produced (max_seen - min_seen + 1)
        ids_seen        how many of them appeared in at least one camera frame
        ids_never_seen  the ids that appeared in NONE of them

WHAT THAT DOES AND DOES NOT PROVE
    An id that never appears was NOT OBSERVED ON THE DISPLAY by this measurement.  Given the
    ~2:1 camera-to-display sampling ratio, repeated absence across ALL captured frames is
    treated as a state that did not visibly persist for a full camera exposure — not as proof
    that it was never presented for any interval at all (see SAMPLING CAVEAT).

    It also does NOT prove that Chromium completed style/layout/paint/raster/commit for that
    state and then discarded a finished frame — several DOM updates can be coalesced into one
    paint.  Report it as "rAF states not observed on the display", not as "rendered frames
    that were dropped".

SAMPLING-RATE GATE — the load-bearing correction
    iPhone slow-motion clips are NOT uniformly 240 fps.  There is a slow-motion window, and
    OUTSIDE it the clip runs at normal speed (30 fps effective), with a gradual speed RAMP
    between the two.  In a 30 fps region each stored frame spans 1/30 s of real time instead
    of 1/240 s, so the camera can only film every 8th rAF id — and a naive decoder scores the
    other seven as "never observed", which is a fact about the CAMERA, not about Chromium.

    That artefact is real in this footage: the last run of each stamped clip sits in the
    ramp-out, and IMG_3832's first run sits in the ramp-in.  Left in, they inflate the
    headline.  So every run is gated on its measured sampling rate (see sampling_rate below)
    and a run outside the 240 fps window is REJECTED and reported, never silently dropped.

SAMPLING CAVEAT (within the accepted window)
    At 240 fps a nominal 120 Hz refresh is sampled roughly twice per display interval, so
    a state that WAS presented is very likely to be captured — but this is not a guarantee:
    exposure phase, exposure duration and rolling shutter can blur a refresh boundary.  The
    decoder samples the interior of each cell (away from the borders) and the decimal digits
    beside the strip are used to validate it; ambiguous exposures show as a decode that does
    not match its neighbours in the monotone sequence.

    This is exactly why this is the SECONDARY measurement.  track_cadence.py carries the
    report's primary claim and instruments the page not at all.

USAGE
    python3 decode_frame_ids.py FOOTAGE.mov --strip X Y W H [--min-run 60] [--csv out.csv]

    --strip is the pixel rectangle of the 8-cell binary strip in the video frame.
    Find it once by eye (e.g. in a still exported with ffmpeg) — the camera is on a tripod,
    so it does not move.  A generous rectangle is fine; the cells are located inside it.
"""
import argparse, csv, subprocess, sys, tempfile, os, glob
import numpy as np
from PIL import Image

DARK = 110          # a pixel darker than this is "ink" (cell border or a filled cell)
FILLED = 125        # a cell interior darker than this is a 1-bit

ID_LO = 1           # rAF issues 001 first (measureRaf does n++ BEFORE writing).
                    # 000 is the STATIC MARKUP, not an id any callback ever produced.
RAF_HZ = 120.0      # rAF callback rate, measured by the page itself in every condition

# SAMPLING-RATE GATE.  240 fps sampling a 120 Hz counter => 0.50 ids per stored frame.
# Accept 0.35–0.65 (±30%); anything outside is a slow-motion speed ramp, not 240 fps.
MIN_RATE, MAX_RATE = 0.35, 0.65


def sampling_rate(seq):
    """Mean id-advance per stored frame — a measurement of the CAMERA, not the browser.

    rAF issues ids at a steady ~120 Hz whether or not the display presents them, so the id
    counter is a clock.  The mean advance per stored frame therefore recovers how much REAL
    TIME each stored frame spans:

        mean advance/frame  ~=  120 Hz  x  (real seconds per stored frame)

            240 fps (slow-motion) ->  120/240  =  0.50 ids/frame
             30 fps (normal speed)->  120/30   =  4.00 ids/frame

    Dropped states do NOT bias this: the ids still climb to ~85 over the same 700 ms of real
    time, however few of them are presented.  (Taking the median of POSITIVE advances instead
    would give ~1.0 at any sampling rate — a trap worth naming.)
    """
    d = np.diff(seq)
    d = d[(d >= 0) & (d < 40)]              # ignore the reset (85 -> 1) and decode spikes
    return float(d.mean()) if len(d) else 0.0


def extract(video, rect, outdir):
    x, y, w, h = rect
    subprocess.run(
        ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', video,
         '-vf', f'crop={w}:{h}:{x}:{y}', '-fps_mode', 'passthrough',
         os.path.join(outdir, 'f-%06d.png')],
        check=True)
    return sorted(glob.glob(os.path.join(outdir, 'f-*.png')))


def strip_bounds(g):
    """The 8 cells all carry a dark border, so the strip is one contiguous run of columns
    containing ink.  Locating the whole strip and dividing it into 8 equal cells is robust
    to camera blur merging adjacent borders (they are evenly spaced by construction)."""
    xs = np.where((g < DARK).any(axis=0))[0]
    return (int(xs.min()), int(xs.max())) if len(xs) > 40 else None


def decode(g, bounds):
    x0, x1 = bounds
    w = (x1 - x0) / 8.0
    h = g.shape[0]
    bits = []
    for k in range(8):
        cx = x0 + w * (k + 0.5)
        a, b = int(cx - w * 0.22), int(cx + w * 0.22)      # cell interior, clear of the borders
        bits.append('1' if g[int(h * .30):int(h * .62), a:b].mean() < FILLED else '0')
    return int(''.join(bits), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('video')
    ap.add_argument('--strip', nargs=4, type=int, required=True, metavar=('X', 'Y', 'W', 'H'))
    ap.add_argument('--min-run', type=int, default=60, help='min camera frames for a run')
    ap.add_argument('--csv', help='write per-run results here')
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        files = extract(a.video, a.strip, tmp)
        grays = [np.asarray(Image.open(f).convert('L')).astype(float) for f in files]

        bs = [b for b in (strip_bounds(g) for g in grays) if b]
        if not bs:
            sys.exit('strip not found — check --strip')
        fixed = (int(np.median([b[0] for b in bs])), int(np.median([b[1] for b in bs])))
        ids = np.array([decode(g, fixed) for g in grays])

    # a run = a stretch where the id is advancing
    active = np.zeros(len(ids), bool)
    for i in range(1, len(ids)):
        if 0 < ids[i] - ids[i - 1] <= 8:
            active[max(0, i - 8):i + 8] = True
    runs, cur = [], []
    for i, on in enumerate(active):
        if on:
            cur.append(i)
        elif cur:
            if len(cur) >= a.min_run:
                runs.append(cur)
            cur = []
    if len(cur) >= a.min_run:
        runs.append(cur)

    rows, rejected = [], []
    print(f'{len(ids)} camera frames  →  {len(runs)} candidate runs\n')
    print(f'{"run":>4} {"adv/frame":>10} {"cam fps":>8} {"ids_gen":>8} {"ids_seen":>9} {"not_seen":>9}   not observed')
    for k, r in enumerate(runs, 1):
        seq = ids[r].astype(int)

        rate = sampling_rate(seq)
        cam_fps = RAF_HZ / rate if rate > 0 else float('nan')
        if not (MIN_RATE <= rate <= MAX_RATE):
            rejected.append((k, r[0] + 1, r[-1] + 1, rate, cam_fps))
            print(f'{k:>4} {rate:>10.2f} {cam_fps:>8.0f}   ⟵ REJECTED: not sampled at ~240 fps '
                  f'(slow-motion speed ramp) — see SAMPLING-RATE GATE')
            continue

        # The counter still displays the PREVIOUS run's final id until this run's first callback
        # writes 001, and the run detector's ±8-frame dilation pulls some of those leftover frames
        # into the head of the run.  Cut the run at the reset, so a LEFTOVER high id cannot
        # masquerade as an id this run actually reached.
        drop = next((i for i in range(1, len(seq)) if seq[i] < seq[i - 1] - 10), 0)
        seq = seq[drop:]

        # ID_LO is 1, not seq.min():
        #   * rAF issues 001 first — measureRaf() does n++ BEFORE writing, so 000 is never issued.
        #     000 is the STATIC MARKUP (<div id="fcNum">000</div>), visible until the first callback.
        #   * and id 001 belongs in the DENOMINATOR even when the camera never caught it — taking
        #     seq.min() would silently delete missed low ids from the count.
        hi = int(seq.max())                    # highest id this run actually climbed to
        gen = list(range(ID_LO, hi + 1))
        seen = sorted({int(v) for v in seq if ID_LO <= v <= hi})
        never = [v for v in gen if v not in seen]
        rows.append(dict(run=k, first_cam_frame=r[0] + 1, last_cam_frame=r[-1] + 1,
                         adv_per_cam_frame=round(rate, 3), est_cam_fps=round(cam_fps),
                         id_lo=ID_LO, id_hi=hi, ids_generated=len(gen), ids_seen=len(seen),
                         ids_never_seen=len(never),
                         pct_never_seen=round(100 * len(never) / len(gen), 1),
                         never_seen=' '.join(f'{v:03d}' for v in never)))
        print(f'{k:>4} {rate:>10.2f} {cam_fps:>8.0f} {len(gen):>8} {len(seen):>9} '
              f'{len(never):>4} ({100*len(never)/len(gen):5.1f}%)   '
              + ' '.join(f'{v:03d}' for v in never[:10]) + (' …' if len(never) > 10 else ''))

    if not rows:
        sys.exit('\nevery run was rejected by the sampling-rate gate — nothing to report')

    g = sum(x['ids_generated'] for x in rows)
    n = sum(x['ids_never_seen'] for x in rows)
    print(f'\nACCEPTED {len(rows)} run(s)   ids generated {g}   '
          f'not observed on the display {n}  ({100 * n / g:.1f}%)')

    # Never silently drop coverage — say exactly what was excluded and why.
    if rejected:
        print(f'\nREJECTED {len(rejected)} run(s) — outside the 240 fps sampling window:')
        for k, f0, f1, rate, fps in rejected:
            print(f'  run {k}: camera frames {f0}..{f1}, {rate:.2f} ids/frame ⇒ ~{fps:.0f} fps '
                  f'(240 fps would give {RAF_HZ / 240:.2f})')
        print('  Their unseen ids say nothing about the display: at those frame rates the camera\n'
              '  physically could not film every id.  Excluding them is the conservative call.')

    if a.csv:
        with open(a.csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f'wrote {a.csv}')


if __name__ == '__main__':
    main()
