#!/usr/bin/env python3
"""Generate footage/segments.csv — every DETECTED run in every clip, with its condition and
its sampling-rate verdict.

This file used to be hand-maintained and had gone stale (its one filled frame-range matched
no detector).  It is now GENERATED, so it cannot drift from the measurements again.

    python3 build_segments.py ~/Downloads > ../footage/segments.csv

Columns
    video, sha256_prefix          which clip
    run                           run index within that clip, as the detector numbers it
    first_cam_frame,last_cam_frame,cam_frames
                                  where it is in the clip
    devtools, frame_stamp         the condition it was recorded under
    what_animated                 A-alone | B-alone | both   (from the motion, not from notes)
    sampling                      240fps-OK | REJECTED-ramp  (the gate; see decode_frame_ids.py)
    adv_per_cam_frame,est_cam_fps for stamped clips: the measured sampling rate
    notes
"""
import csv, glob, os, subprocess, sys, tempfile
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decode_frame_ids as D
import track_cadence as T

CARD_A = (215, 428, 580, 190)
CARD_B = (165, 778, 560, 185)
STRIP = (1450, 215, 450, 100)
MIN_RUN = 130                     # track_cadence's sampling gate (see its --min-run help)

CLIPS = [
    ('IMG_3832.mov', 'closed', 'on'),
    ('IMG_3833.mov', 'open',   'on'),
    ('IMG_3836.mov', 'closed', 'off'),
]


def sha8(path):
    return subprocess.run(['shasum', '-a', '256', path], capture_output=True, text=True
                          ).stdout.split()[0][:8]


def profiles(video, rect, tag):
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, tag)
        os.makedirs(d)
        x, y, w, h = rect
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', video,
                        '-vf', f'crop={w}:{h}:{x}:{y}', '-fps_mode', 'passthrough',
                        os.path.join(d, 'f-%06d.png')], check=True)
        return [T.profile(f) for f in sorted(glob.glob(os.path.join(d, 'f-*.png')))]


def id_rate(video, lo, hi):
    """measured sampling rate over one run, from the id strip (stamped clips only)"""
    with tempfile.TemporaryDirectory() as tmp:
        files = D.extract(video, STRIP, tmp)
        grays = [np.asarray(Image.open(f).convert('L')).astype(float) for f in files]
        bs = [b for b in (D.strip_bounds(g) for g in grays) if b]
        fixed = (int(np.median([b[0] for b in bs])), int(np.median([b[1] for b in bs])))
        ids = np.array([D.decode(g, fixed) for g in grays]).astype(int)
    return D.sampling_rate(ids[lo - 1:hi])


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser('~/Downloads')
    w = csv.writer(sys.stdout)
    w.writerow(['video', 'sha256_prefix', 'run', 'first_cam_frame', 'last_cam_frame', 'cam_frames',
                'devtools', 'frame_stamp', 'what_animated', 'sampling',
                'adv_per_cam_frame', 'est_cam_fps', 'notes'])

    for name, devtools, stamp in CLIPS:
        video = os.path.join(base, name)
        PA, PB = profiles(video, CARD_A, 'A'), profiles(video, CARD_B, 'B')
        n = len(PA)
        dA = np.array([0.0] + [T.shift(PA[i], PA[i - 1]) for i in range(1, n)])
        dB = np.array([0.0] + [T.shift(PB[i], PB[i - 1]) for i in range(1, n)])

        mag = np.abs(dA) + np.abs(dB)
        sm = np.array([mag[max(0, i - 6):i + 7].sum() for i in range(n)])
        runs, cur = [], []
        for i, on in enumerate(sm > 6.0):
            if on:
                cur.append(i)
            elif cur:
                if len(cur) >= 25:
                    runs.append(cur)          # keep the short ones too — they get REJECTED, not hidden
                cur = []
        if len(cur) >= 25:
            runs.append(cur)

        for k, r in enumerate(runs, 1):
            lo, hi, L = r[0] + 1, r[-1] + 1, len(r)
            ta, tb = float(np.abs(dA[r]).sum()), float(np.abs(dB[r]).sum())
            what = ('both' if ta > T.TRAVELLED and tb > T.TRAVELLED else
                    'A-alone' if ta > T.TRAVELLED else
                    'B-alone' if tb > T.TRAVELLED else 'none')
            ok = L >= MIN_RUN
            rate = fps = ''
            note = ''
            if stamp == 'on':
                rr = id_rate(video, lo, hi)
                rate = round(rr, 3)
                fps = round(D.RAF_HZ / rr) if rr > 0 else ''
            if not ok:
                note = (f'only {L} cam frames; a 600ms animation spans ~144 at 240fps — '
                        f'slow-motion speed ramp, excluded from both measurements')
            w.writerow([name, sha8(video), k, lo, hi, L, devtools, stamp, what,
                        '240fps-OK' if ok else 'REJECTED-ramp', rate, fps, note])


if __name__ == '__main__':
    main()
