# Analysis

Two independent measurements. Both take the raw camera footage and produce the numbers quoted in the
top-level `README.md`, so every figure there can be re-derived rather than taken on trust.

```
pip install -r requirements.txt      # numpy, pillow  (ffmpeg must be on PATH)
```

Both take the same crop rectangles (the camera was on a tripod; the framing is the same in all three clips):

```bash
CARDS="--card-a 215 428 580 190 --card-b 165 778 560 185"
STRIP="--strip 1450 215 450 100"
```

---

## 0. The sampling-rate gate — read this first

**iPhone slow-motion clips are not uniformly 240 fps.** There is a slow-motion window; outside it the clip
runs at normal speed (**30 fps effective**), with a gradual speed **ramp** between. In a 30 fps region each
stored frame spans 1/30 s of real time, so the camera can film only every 8th rAF id — and a naive analysis
scores the other seven as "never observed", which is a fact about the **camera**, not about Chromium.

The frame-id counter doubles as a **clock** that detects this. rAF issues ids at a steady 120 Hz whether or
not the display presents them, so the mean id-advance **per stored frame** recovers how much real time each
stored frame spans:

| | ids advanced per stored frame |
|---|---|
| 240 fps (slow-motion) | 120 ÷ 240 = **0.50** |
| 30 fps (normal speed) | 120 ÷ 30 = **4.00** |

`decode_frame_ids.py` accepts **0.35–0.65** and **rejects everything else, printing what it rejected**.
`track_cadence.py` gates on the equivalent signal for a clip with no stamp — **run length** (`--min-run 130`):
a 600 ms animation spans ~144 stored frames at 240 fps, but only ~18 at 30 fps.

> ⚠️ A trap worth naming: the mean must include the frames where the id did **not** change. Taking the
> **median of positive advances** instead gives ~1.0 at *any* sampling rate, and looks perfectly plausible.

Three independent signals agree on every run — length, measured rate, and the odd-hold count (holds of 1 or 3
camera frames are not physical at true 2:1 sampling, and the rejected runs are full of them while the accepted
runs have almost none). `footage/segments.csv` records the verdict for **every** detected run:

```bash
python3 build_segments.py ~/Downloads > ../footage/segments.csv
```

---

## 1. `track_cadence.py` — visible cadence, **no instrumentation of the page**

Recovers each card's motion straight from the pixels by sub-pixel cross-correlation of its intensity
profile between consecutive camera frames. The page is filmed with the frame-stamp **off**, so nothing
in the repro is doing per-frame work.

> An **advance** is a per-camera-frame displacement greater than **half of that run's nominal per-refresh
> step**.
>
> A **hold** is the number of consecutive camera frames between two consecutive advances.
>
> **The denominator of every percentage is the number of hold events**, not the number of camera frames:
>
> * `hold = 2` — the card advanced again on the very next display refresh: **one nominal 120 Hz interval**.
> * `hold ≥ 4` — its state was held across **two or more** refresh intervals.
> * `hold = 1 or 3` — not physical at 240 fps against 120 Hz; straddled exposures and tracker noise. They
>   are reported, not redistributed, so the two columns do not always sum to 100.

### Exactly how the numbers are derived

| | |
|---|---|
| **displacement** | per-camera-frame sub-pixel motion from 1-D cross-correlation, **magnitude only**. The cards travel in one direction throughout, so a sign flip is tracker noise and `abs()` treats it as such. |
| **run boundaries** | a run is the stretch over which the card is *actually travelling*, detected from the motion itself (a rolling window of accumulated displacement). The idle head and tail therefore lie outside the run and contribute no holds. |
| **total travel** | the sum of `abs(displacement)` **within that run**. |
| **nominal per-refresh step** | `total travel ÷ refreshes`, with `refreshes = 72` — **theoretical**: 600 ms × 120 Hz. It is deliberately *not* taken from rAF, since the entire point of this report is that rAF's cadence and the display's are not the same thing. |
| **advance threshold** | `--threshold` × the nominal step. Default `0.5`. |

### Threshold sensitivity — the rates are not an artefact of the cut-off

Re-run at `--threshold 0.4`, `0.5` and `0.6`:

| condition | 0.4× | 0.5× | 0.6× |
|---|---|---|---|
| A alone (layout) | **59 %** | **59 %** | **59 %** |
| B alone (compositor-eligible) | **98 %** | **98 %** | **98 %** |
| both — A | **54 %** | **54 %** | **54 %** |
| both — B | **54 %** | **54 %** | **54 %** |

Card A's histogram is byte-identical at all three (`2→122  4→80  6→4`); card B's total hold count moves by ±1
(281 / 280 / 279) without shifting its 98 %. Between 0.4× and 0.6× of the nominal step there is nothing to
reclassify: a camera frame's displacement is either ≈ 0 or ≈ a full step.

```bash
for k in 0.4 0.5 0.6; do
  python3 track_cadence.py ~/Downloads/IMG_3836.mov $CARDS --threshold $k
done
```

Both cards are tracked, so every run is **classified** by which of them actually travelled
(`A-alone` / `B-alone` / `both`), and results are aggregated **per condition — never pooled across them**.

```bash
# the headline clip — stamp OFF, so the page is not instrumented at all
python3 track_cadence.py ~/Downloads/IMG_3836.mov $CARDS --csv ../results/cadence-cards-alone.csv

# the two stamped clips: the concurrent-animation case, and the DevTools-open control
python3 track_cadence.py ~/Downloads/IMG_3832.mov $CARDS --csv ../results/cadence-both-devtools-closed.csv
python3 track_cadence.py ~/Downloads/IMG_3833.mov $CARDS --csv ../results/cadence-both-devtools-open.csv
```

⚠️ Each rectangle must cover that card's **full travel**. If a card leaves its rectangle mid-animation the
tracker measures emptiness and reports it as motionless — a mistake that is easy to make and produces a
very convincing wrong answer. (It did, in an earlier draft of this report: card B was declared to *freeze
completely* beside the layout animation, when in fact it had simply left the tracked rectangle.)

---

## 2. `decode_frame_ids.py` — which rAF states were observed on the display

`index.html` can stamp every `requestAnimationFrame` callback with a unique id, drawn as an **8-bit binary
strip** (and as decimal digits, which serve as an independent check of the decoder). This reads the strip
out of every camera frame and reports, per run, the ids that appeared in **no** camera frame at all.

```bash
python3 decode_frame_ids.py ~/Downloads/IMG_3832.mov $STRIP \
    --csv ../results/session1-incognito-devtools-closed.csv     # DevTools closed
python3 decode_frame_ids.py ~/Downloads/IMG_3833.mov $STRIP \
    --csv ../results/session1-incognito-devtools-open.csv       # DevTools open — the control
```

Runs outside the 240 fps window are **rejected and printed** (see § 0). Rejections are never silent.

### What this does and does not show

An id that never appears **was not observed on the display** by this measurement. Given the ≈2:1
camera-to-display sampling ratio, repeated absence across *all* captured frames is treated as a state that
**did not visibly persist for a full camera exposure** — not as proof that it was never presented for any
interval at all.

It does **not** prove that Chromium completed style → layout → paint → raster → commit for that state and
then discarded a finished frame: several DOM updates can be coalesced into a single paint. The results are
therefore reported as *"rAF states not observed on the display"*, never as *"rendered frames that were
dropped"*.

### Why measurement 1 exists

The stamp is itself one main-thread DOM write per frame, so it **cannot** be used to judge a
compositor-eligible animation running **alone** — it would be measuring its own interference. It does:

| card B, running alone | stamp OFF | stamp ON |
|---|---|---|
| visible cadence (measurement 1) | **98 %** | **80 %** |
| rAF states not observed (measurement 2) | 2 % | **15 %** |

So every *alone* run is filmed with the stamp **off** and measured from the pixels, and measurement 2 is
quoted **only** where a layout animation is already running — where the stamp's own contribution is small
beside it. That last claim is checkable from the published clips: the concurrent (`both`) cadence is **54 %**
with the stamp off and **59 %** with it on, so the stamp barely moves that condition.

> The **80 %** and **15 %** figures come from a clip that is **not published** (poorly framed). They are the
> only numbers in this repository not derivable from the published footage; nothing else depends on them.
>
> An earlier draft quoted a single "19 %" here. That figure was **pooled across two different conditions** —
> the clip contains A-alone runs as well as B-alone runs — which is exactly the mistake this analysis warns
> against everywhere else. Re-derived per condition, and with the § 0 sampling gate applied, card B alone is
> **15 %**, and card A alone is 29 %.

---

## Sampling caveats (both measurements)

* 240 fps against a nominal 120 Hz refresh is ~2 samples per display interval — *likely*, not *guaranteed*,
  to catch every presented state. Exposure phase, exposure duration and rolling shutter can straddle a
  refresh boundary.
* Straddled exposures show up as **holds of 1 or 3 camera frames**, which are not physical at this sampling
  ratio. They are **reported, not redistributed**, which is why the two cadence columns do not always sum to
  100 %.
* The footage was checked for duplicated frames (a 30 fps capture presented as 240 fps by metadata):
  **zero byte-identical consecutive frames** in any clip — every frame carries independent sensor noise, so
  the exposures are genuinely distinct. See `footage/manifest.csv`.

---

## What was measured, and what was only looked at

The distinction matters, so it is drawn explicitly rather than left to the reader.

| | |
|---|---|
| **Camera-measured** | **Chrome only.** Every percentage in this repository — both measurements, all conditions (A alone / B alone / both, DevTools open and closed) — comes from 240 fps footage of **Chrome**. |
| **Checked by eye, not measured** | **Edge, Opera, Vivaldi** — the same *qualitative* behaviour; no cadence figure is claimed for them. **Firefox 152.0.6** — no comparable irregularity was visible; no footage was taken, so no cadence figure is claimed for it either. |
| **Not examined at all** | **Safari.** It forms no part of the evidence. |

Firefox's role in the report is therefore a **qualitative contrast only**: what is immediately obvious in
Chrome on this panel was not apparent in Firefox on the same panel. The **camera-measured** evidence that
the panel can sustain a near-120 Hz visible cadence is the **DevTools-open control** (**97 %**, hold histogram
`2→138 4→2 6→2`) — same panel, same session, same animation — not the Firefox observation.

Reproducing the Firefox comparison is a matter of opening `index.html` in Firefox on a ProMotion Mac and
running steps 2–4 from the top-level README; it needs no tooling from this directory.
