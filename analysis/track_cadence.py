#!/usr/bin/env python3
"""
Recover each card's VISIBLE cadence straight from the pixels — no instrumentation of the page.

WHY THIS EXISTS
    The frame-id stamp is itself one main-thread DOM write per frame, so it cannot be used to
    judge a compositor-eligible animation running alone: it would be measuring its own
    interference.  (It does: with the stamp OFF, 2% of card B's rAF ids are not observed on
    the display when it runs alone; with the stamp ON, 19% are not.)  So those runs are filmed
    with the stamp SWITCHED OFF and the motion is recovered from the footage itself.

    This script therefore carries the report's PRIMARY claim: it instruments the page not at
    all.  decode_frame_ids.py is the secondary, self-interfering measurement.

WHAT IT MEASURES
    For every consecutive pair of camera frames, each card's sub-pixel horizontal displacement
    is obtained by 1-D cross-correlation of its intensity profile (parabolic peak interpolation).

        hold = the number of consecutive camera frames over which a card does not advance.

    On a nominal 120 Hz display sampled at 240 fps, a card whose every visible state reaches the
    display has hold = 2 — it advances on every other camera frame.  A card whose states reach
    the display irregularly sits still for 4, 6, 8+ camera frames and then advances proportionally
    further.

    Both cards are tracked, so every run is CLASSIFIED by which of them actually travelled
    (A only / B only / both).  Results are aggregated per condition — never pooled across them.

    The histogram contains some holds of 1 and 3.  Those are not physical at 240 fps / 120 Hz;
    they come from exposures that straddle a refresh boundary and from tracker noise, and they
    are reported rather than silently redistributed.  This is why "hold=2 %" and "hold>=4 %" do
    not sum to 100.

USAGE
    python3 track_cadence.py FOOTAGE.mov --card-a X Y W H --card-b X Y W H [--csv out.csv]

    Each rectangle must cover that card's FULL travel.  If a card leaves its rectangle
    mid-animation the tracker measures emptiness and reports it as motionless — a mistake that
    is easy to make and produces a very convincing wrong answer.
"""
import argparse, csv, subprocess, tempfile, os, glob
from collections import Counter
import numpy as np
from PIL import Image

INK = 140.0
TRAVELLED = 200.0        # px of accumulated motion that counts as "this card animated"


def extract(video, rect, outdir, tag):
    x, y, w, h = rect
    d = os.path.join(outdir, tag)
    os.makedirs(d)
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', video,
                    '-vf', f'crop={w}:{h}:{x}:{y}', '-fps_mode', 'passthrough',
                    os.path.join(d, 'f-%06d.png')], check=True)
    return sorted(glob.glob(os.path.join(d, 'f-*.png')))


def profile(path):
    a = np.asarray(Image.open(path).convert('L')).astype(float)
    return np.clip(INK - a, 0, None).sum(axis=0)


def shift(p, q, maxlag=22):
    p = p - p.mean(); q = q - q.mean()
    lags = np.arange(-maxlag, maxlag + 1)
    c = np.array([np.corrcoef(p[maxlag:-maxlag], np.roll(q, l)[maxlag:-maxlag])[0, 1] for l in lags])
    k = int(np.argmax(c))
    d = (0.5 * (c[k-1] - c[k+1]) / (c[k-1] - 2*c[k] + c[k+1] + 1e-12)) if 0 < k < len(c) - 1 else 0.0
    return float(lags[k] + d)


def holds_of(d, refreshes, k=0.5):
    """Hold lengths, in camera frames, for one card over one run.

    displacement  per-camera-frame sub-pixel motion, MAGNITUDE only (the cards travel in one
                  direction; a sign flip would be tracker noise, and |d| treats it as such).
    step          the run's nominal per-refresh advance = total travel / `refreshes`.
                  `refreshes` is THEORETICAL (600 ms x 120 Hz = 72), not taken from rAF — the
                  whole point is that rAF's cadence and the display's are not the same thing.
    advance       |displacement| > k * step.   k = 0.5 by default; see --threshold.
    hold          consecutive camera frames between two advances.

    Run boundaries: a run is the stretch over which the card is actually travelling (detected
    from the motion itself), so the idle head and tail are outside it and contribute no holds.
    """
    dd = np.abs(d)
    thr = (dd.sum() / refreshes) * k
    out, rl = [], 0
    for v in dd:
        if v > thr:
            if rl:
                out.append(rl)
            rl = 1
        else:
            rl += 1
    return out, float(dd.sum() / refreshes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('video')
    ap.add_argument('--card-a', nargs=4, type=int, required=True, metavar=('X', 'Y', 'W', 'H'))
    ap.add_argument('--card-b', nargs=4, type=int, required=True, metavar=('X', 'Y', 'W', 'H'))
    ap.add_argument('--refreshes', type=int, default=72,
                    help='THEORETICAL refreshes the animation spans: 600 ms x 120 Hz = 72 (not from rAF)')
    ap.add_argument('--threshold', type=float, default=0.5,
                    help='an advance is |displacement| > THRESHOLD x nominal per-refresh step (default 0.5)')
    ap.add_argument('--min-run', type=int, default=130,
                    help='SAMPLING-RATE GATE: a 600ms animation spans ~144 stored frames at 240fps '
                         '(and the +/-6 detection window widens that to ~150-156), but only ~18 at '
                         '30fps. A materially shorter run was not sampled at 240fps — it straddles an '
                         'iPhone slow-motion speed ramp — and is REJECTED. Default 130.')
    ap.add_argument('--csv')
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        PA = [profile(f) for f in extract(a.video, a.card_a, tmp, 'A')]
        PB = [profile(f) for f in extract(a.video, a.card_b, tmp, 'B')]

    n = len(PA)
    dA = np.array([0.0] + [shift(PA[i], PA[i-1]) for i in range(1, n)])
    dB = np.array([0.0] + [shift(PB[i], PB[i-1]) for i in range(1, n)])

    mag = np.abs(dA) + np.abs(dB)
    sm = np.array([mag[max(0, i-6):i+7].sum() for i in range(n)])
    runs, rejected, cur = [], [], []
    for i, on in enumerate(sm > 6.0):
        if on:
            cur.append(i)
        elif cur:
            (runs if len(cur) >= a.min_run else rejected).append(cur)
            cur = []
    if cur:
        (runs if len(cur) >= a.min_run else rejected).append(cur)
    rejected = [r for r in rejected if len(r) >= 25]      # below this it is not a run at all, just noise

    rows, agg = [], {}
    print(f'{n} camera frames  →  {len(runs)} animation runs\n')
    print(f'{"run":>4} {"what ran":>12} {"card":>4} {"travel":>7} {"step":>7} {"hold=2":>7} {"hold>=4":>8} {"max":>4}')
    for k, r in enumerate(runs, 1):
        ta, tb = float(np.abs(dA[r]).sum()), float(np.abs(dB[r]).sum())
        cond = ('both' if ta > TRAVELLED and tb > TRAVELLED else
                'A-alone' if ta > TRAVELLED else
                'B-alone' if tb > TRAVELLED else 'none')
        if cond == 'none':
            continue
        for card, d, trav in (('A', dA[r], ta), ('B', dB[r], tb)):
            if trav < TRAVELLED:
                continue                       # this card did not animate in this run
            h, step = holds_of(d, a.refreshes, a.threshold)
            H = np.array(h)
            key = (cond, card)
            agg.setdefault(key, []).extend(h)
            rows.append(dict(run=k, condition=cond, card=card,
                             first_cam_frame=r[0]+1, last_cam_frame=r[-1]+1,
                             travel_px=round(trav, 1), step_per_refresh_px=round(step, 2),
                             holds=len(H),
                             pct_hold_1_refresh=round(100*float((H == 2).mean()), 1),
                             pct_hold_2plus_refresh=round(100*float((H >= 4).mean()), 1),
                             max_hold_cam_frames=int(H.max())))
            print(f'{k:>4} {cond:>12} {card:>4} {trav:7.0f} {step:6.2f}px '
                  f'{100*(H==2).mean():6.0f}% {100*(H>=4).mean():7.0f}% {H.max():4d}')

    print(f'\n{"="*76}\nPER CONDITION (never pooled across conditions)\n{"="*76}')
    print(f'{"condition":>10} {"card":>5} {"holds":>6} {"hold=2 (1 refresh)":>19} {"hold>=4 (2+)":>13}   histogram')
    for (cond, card), h in sorted(agg.items()):
        H = np.array(h)
        hist = '  '.join(f'{k}→{v}' for k, v in sorted(Counter(H.tolist()).items()))
        print(f'{cond:>10} {card:>5} {len(H):6d} {100*(H==2).mean():18.0f}% {100*(H>=4).mean():12.0f}%   {hist}')
    print('\n  holds of 1 and 3 are not physical at 240 fps / 120 Hz — straddled exposures and')
    print('  tracker noise. They are reported, not redistributed, so the two columns do not sum to 100.')
    print('  Their SCARCITY is also the sampling check: a clip genuinely sampled at 2x the refresh')
    print('  rate produces almost none. A run full of them was not sampled at 240 fps.')

    # Never silently drop coverage — name what the sampling gate excluded, and why.
    if rejected:
        print(f'\n  REJECTED {len(rejected)} run(s) by the sampling-rate gate (--min-run {a.min_run}):')
        for r in rejected:
            print(f'    camera frames {r[0]+1}..{r[-1]+1} — only {len(r)} frames, but a 600ms animation')
            print(f'      spans ~144 at 240 fps. Too short ⇒ this run straddles an iPhone slow-motion')
            print(f'      speed ramp, where hold-lengths no longer count refresh intervals.')

    if a.csv and rows:
        with open(a.csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f'\nwrote {a.csv}')


if __name__ == '__main__':
    main()
