# macOS ProMotion: a non-composited CSS animation is presented irregularly on a 120 Hz display, and a concurrent transform animation inherits the same cadence

**A compositor-eligible `transform` advances on 98 % of nominal 120 Hz intervals when it runs alone, but on
only 54–59 % when it runs concurrently with a layout animation — matching that layout animation's own
measured cadence within the resolution of the experiment. Meanwhile `requestAnimationFrame` callbacks keep
arriving at a steady ~120 Hz.**

The layout animation itself (a CSS transition on `max-width`, a **non-compositable** property) advances on
**59 %** of intervals when it runs alone: the rest of the time it holds a state for two or more intervals and
then jumps proportionally further.

With DevTools open, the same two animations on the same panel in the same session advance on **97 %**.

**Opening DevTools usually suppresses the issue.** So does running a screen recorder, and so does attaching
CDP. **Every on-machine observation channel tested improves it**, which is why every measured cadence figure
below was obtained with an **external 240 fps camera** filming the screen.

Firefox on the same machine and display does not reproduce it.

---

## Update — root cause localised and a fix confirmed *(follow-up)*

A follow-up diagnostic phase — full methodology, a Chromium source dive, and additional camera
measurements — localised the cause and confirmed a fix. Details and per-claim evidence standards are in
[`diagnostic-findings.md`](diagnostic-findings.md); the essentials:

* **A fix already exists in Chromium, disabled by default.** Launching with
  **`--enable-features=VSyncAlignedPresentation`** makes the animation smooth — **reproducible by anyone on a
  ProMotion Mac, no footage required.** The flag switches the macOS present to defer the CALayer commit to
  the display-link (vsync) callback instead of committing at a free-floating moment. Its scrolling-only
  sibling `kVSyncAlignedPresentationForScrolling` is **on by default**, so Chrome already vsync-aligns the
  present for scrolling — just not for general animation. The bug lives in exactly that gap.

* **That gap is deliberate, and its history is public.** The general vsync-aligned present was written in
  2023 *"in order for CoreAnimation to latch frames in a consistent timing"*, then Finch-tested on Beta in
  2025 with three targeting arms (`AllFrames` / `Animation` / `Interaction`) and **narrowed to the
  scroll-only arm**, because broad alignment *"still has big regressions on guardrail metrics, although it
  improves a lot on smoothness metrics"* — Interaction-to-Next-Paint being the named cost. The flag we use
  here is a **2026 re-introduction** of the rejected general behaviour. Full CL-by-CL timeline, verbatim
  quotes and the resulting (narrower) upstream ask: **§7 of
  [`diagnostic-findings.md`](diagnostic-findings.md)**.

* **A measurement asymmetry worth stating plainly.** On macOS Chrome does not know when a frame was
  actually presented — it **models** it (`PresentationFeedback` is built from the display-link's *predicted*
  display time), and INP, EventLatency and CompositorLatency are all computed on that model, as are the
  smoothness metrics. So **both** sides of the trade-off above are model-mediated — **including this
  report's own Jank3 number**. What differs is ground truth: an external 240 fps camera settles the
  smoothness side (measured below), and **nothing equivalent has been published for the latency side**.
  Chromium engineers raised this about the same feature in 2023 — *"shifting this would directly shift the
  metrics, however that does not mean that the actual presentation for the user changed"* — and the
  control trial they proposed does not appear in any public source. Detail and the careful chronology
  (the timestamp definition was revised in 2024, before the 2025 experiment, so this is **not** a claim that
  the experiment was wrong): **§7h**.

* **Mechanism (localised; source-readable + inferred).** The default macOS present commits the CALayer tree
  synchronously and *not* vsync-aligned; a documented ~1.5 ms latch deadline slips any late commit to the
  next refresh. Concurrent per-frame main-thread work pushes the commit late (via the Viz begin-frame
  deadline), so it crosses the deadline and is presented one refresh later — the irregular cadence measured
  below. The loss is at the **macOS CALayer commit → CoreAnimation present** handoff, not in Chrome's
  compositor scheduling (a Perfetto trace runs clean at ~120 Hz through `SwapBuffers`).

* **Metal-specific.** The coupling reproduces only on the default **ANGLE-Metal** present. On **ANGLE-OpenGL**
  (`--use-angle=gl`) and **software compositing** it is absent (camera-measured follow-up: OpenGL keeps the
  concurrent `transform` smooth in A+B where Metal degrades it). Neither is a usable *workaround*, though —
  software raster is slow and OpenGL showed its own single-card judder by eye — so the Metal-side flag above,
  not a backend switch, is the fix. This points at the **Metal → CoreAnimation** present.

* **Camera confirmation.** A fixed-camera *default-vs-flag* pair confirmed the flag restores the near-120 Hz
  cadence for **both** the compositor (`transform`) and the layout (`max-width`) animation.

* **Confirmable with no camera at all — via Chrome's own telemetry — and it says "jank, not dropped
  frames".** `chrome://histograms/Graphics.Smoothness.Jank3.CompositorThread.CompositorAnimation` is a
  **passive internal metric that does *not* suppress** (unlike DevTools / a recorder / CDP, which capture
  the screen). Running the A+B repro (DevTools closed): mean **Jank3 ≈ 11.5 on default → ≈ 0.1 with the
  flag** (90 % of sequences at zero), while `Graphics.Smoothness.PercentDroppedFrames3` reads **0
  throughout**. So **Chrome's own data confirms both the bug and the fix**, and confirms the precise
  framing below: the states are **janky (held, uneven cadence), not dropped** — which is exactly why
  standard *dropped-frame* monitoring (and the DevTools FPS meter) shows "0, looks fine" and misses it. A
  reviewer can reproduce this in **~2 minutes with no footage**: `chrome://histograms` → run A+B → note
  Jank3; relaunch `--enable-features=VSyncAlignedPresentation` → note it again. *(Caveat: Jank3 is built
  from Chrome's present **estimate**, so it under-reports magnitude vs the external camera's ~50 % held —
  but the default→flag direction is unambiguous.)*

> The follow-up **camera** clips are **not published** — they are trivially reproducible (an iPhone in
> slow-motion plus the flag). Their numbers are follow-up detail, **not** the headline re-derivable
> measurements below, which remain those from the three published clips. But note the two claims that need
> **no footage at all**: the flag test itself (**enable the flag, and the bug disappears**), and the
> passive `Jank3` histogram above (**11.5 → 0.1**).

Same area as Chromium's open macOS present work —
[issue 330771325](https://issues.chromium.org/issues/330771325), *"VSync aligned frame presentation on
Mac"*, which is where `kVSyncAlignedPresentation` was added. *(The ProMotion umbrella
[40202100](https://issues.chromium.org/issues/40202100) is **closed as Fixed** since 2023-12-19, with its
reporter noting that specific failures should "be filed as new bugs"; its parent
[40062488](https://issues.chromium.org/issues/40062488) closed 2025-03-07.)*

---

## Observation

With DevTools closed, on a 120 Hz ProMotion Mac:

1. **Card A alone** (`max-width`, a layout animation) — visibly irregular.
2. **Card B alone** (`transform`, compositor-eligible) — visually smooth.
3. **Both together** — B now looks as irregular as A.
4. **Open DevTools and repeat** — both look smooth.
5. Throughout all of the above, the page's own `requestAnimationFrame` counter reads a steady ~120 Hz with
   zero callbacks longer than 20 ms.
6. **Firefox**, same machine and display — no comparable irregularity was visible. *(An unaided visual
   check; Firefox was not camera-measured. See **Environment**.)*

## What the evidence establishes

* The **visible cadence** of a non-composited animation is irregular on the tested system, while
  `requestAnimationFrame` callbacks continue to arrive regularly at ~120 Hz.
* A **concurrently running compositor-eligible `transform` animation** — which advances on 98 % of intervals
  on its own — falls to **54–59 %**, matching the layout animation's own measured cadence within the
  resolution of the experiment.
* **DevTools, a screen recorder, and CDP each usually suppress it**, so each on-machine diagnostic tested
  here alters the behaviour it is intended to observe.
* The degradation **becomes substantially worse when additional main-thread work is introduced**. In one
  *supplementary, unpublished* run, a single fixed-position text update per rAF callback was enough to take
  the transform animation from 98 % down to **80 %**. *(This one figure is not derivable from the published
  footage — see the note under **Footage**. Nothing above depends on it.)*

## What the evidence does **not** establish

* **Where** in the pipeline the visual states are lost — *the published measurements below* do not
  distinguish between states never being painted, being coalesced before paint, being lost during
  raster/commit, being dropped in compositor submission, or being discarded at final presentation. *(The
  follow-up phase — see the **Update** above and `diagnostic-findings.md` — narrows this to the macOS CALayer
  commit / CoreAnimation present, corroborated by the fix flag.)*
* That Chromium **completed and then discarded finished frames**. A `requestAnimationFrame` callback and a
  DOM write do not imply that style → layout → paint → raster → commit ran for that state; several DOM
  updates can be coalesced into one paint. Everything below is therefore reported as
  *"rAF states not observed on the display"*, never as *"rendered frames that were dropped"*.
* Whether the same cadence occurs on a fixed-refresh (non-ProMotion) display — untested.

---

## Environment

| | |
|---|---|
| Machine | MacBook Pro — **Apple M4 Pro**, 48 GB |
| Display | 3456 × 2234 native, **ProMotion — variable refresh, up to 120 Hz** |
| Display scaling | **1728 × 1117 @2x** |
| macOS | **Sequoia 15.7.3** (build 24G419), arm64 |
| **Power** | **AC (mains)** |
| **Low Power Mode** | **OFF** |
| Profile | **incognito, no extensions** |
| Browser zoom | 100 % |
| Viewport during filming | **~1380 CSS px** (card A reaches its full 1000 px — verified below) |

**The same qualitative behaviour was reproduced in every Chromium-based browser tested.** The camera
measurements in this report were taken in **Chrome**; the others were checked by eye, so no claim of
numeric equivalence is made.

| browser | version | Chromium |
|---|---|---|
| **Chrome** | 150.0.7871.115 (Official Build, arm64) | 150.0.7871.115 |
| **Edge** | 150.0.4078.65 (Official Build, arm64) | 150 |
| **Vivaldi** | 8.1.4087.48 (Official Build, arm64) | 150.0.7871.120 |
| **Opera One** | 133.0.5932.34 (arm64) | 149.0.7827.201 |

**Does not reproduce:** **Firefox 152.0.6** (aarch64), same machine, same panel, same page — **no comparable
irregularity was visible.**

Stated exactly: this was an **unaided visual check, not a camera measurement.** No footage was taken in
Firefox and **no cadence figure is claimed for it** — the same standing as the Edge / Opera / Vivaldi checks
above. What it contributes is a qualitative contrast: the irregularity that is immediately obvious in Chrome
on this panel was not apparent in Firefox on the same panel, which points at the Chromium/macOS rendering
path rather than at the display.

**The camera-measured evidence that this panel can sustain a near-120 Hz visible cadence does not rest on
Firefox** — it is the DevTools-open control described in the next paragraph.

**Safari** — not measured, and forms no part of the evidence here.

Recorded on **AC power with Low Power Mode off**, which removes the obvious power-saving constraint.
ProMotion is a *variable*-refresh panel regardless, so that alone would not prove the display was running at
120 Hz. The stronger evidence is in the data: **the DevTools-open control runs show the same panel, in the
same session, presenting the same animation at a near-perfect 120 Hz cadence — 97 %, with a hold histogram of
`2→138 4→2 6→2`.** The same panel and session can sustain a near-120 Hz visible cadence; the DevTools-closed
runs do not.

## Footage

The raw 240 fps recordings every number was decoded from:
**https://drive.google.com/drive/folders/1kwcsK5xhGeF9WlCHI-Gg6KFpOUiFgopO?usp=sharing**

`footage/SHA256SUMS` pins them; `footage/manifest.csv` records the frame counts; `footage/segments.csv`
(generated by `analysis/build_segments.py`) maps **every detected run** to its condition, its camera-frame
range, and its measured sampling rate. Checked for metadata-only rate conversion: **zero byte-identical
consecutive frames** in any clip.

They are iPhone slow-motion clips. **They are not uniformly 240 fps** — see
[Sampling-rate validation](#sampling-rate-validation--and-what-it-removed) below, which is load-bearing.

> One further clip was recorded with the frame stamp on — but is **not published**; it is poorly framed. It
> is the source of exactly one figure in this document: the stamp's own interference (card B alone drops from
> a **98 %** visible cadence with the stamp off to **80 %** with it on; **15 %** of its rAF states go
> unobserved, against 2 % with the stamp off). **Every other number here is derivable from the three
> published clips**, and none of the conclusions depend on the unpublished one.

---

## The repro — `index.html`

Two cards. **Identical motion, different pipeline.**

| | animation | pipeline | content motion |
|---|---|---|---|
| **A** | `max-width: 400px → 1000px`, 600 ms linear | `max-width` is **not compositable** → requires **main-thread style/layout updates** | glides **300 px left** |
| **B** | `transform: translateX(0 → -300px)`, 600 ms linear | `transform` **is compositor-eligible** † | glides **300 px left** |

The card is centred, so widening it moves its content horizontally. Both cards move their content by
**exactly 300 px over exactly 600 ms with the same easing**. Only the pipeline differs. The list items are
short and `white-space: nowrap`. That does **not** avoid reflow — animating card A's width is precisely a
layout operation. It keeps the text's **line wrapping and line-box structure constant** during the resize,
so what moves on screen is the content sliding, not lines re-flowing into different shapes.

† *Stated as **eligibility**, not as a verified fact about layerisation. Whether card B is in fact promoted to
its own layer can only be confirmed in DevTools — and opening DevTools suppresses the very behaviour being
measured. What **is** measured is that it advances on 98 % of intervals when it runs alone.*

### The window must be wide enough — and it was

Card A only travels 300 px if it can actually reach `max-width: 1000px`. In a narrow window it is clipped:
it travels less, card B still travels exactly 300 px (a fixed `translateX`), and the comparison is void.

The repro tests that condition directly — it measures how wide card A is *allowed* to get and, below
1000 px, replaces the caption with a red warning naming the exact distances that would no longer match.

**And the filming window was verified from the footage itself, not assumed.** Card B translates by exactly
300 CSS px; card A's content travels `(A_final − 400) / 2`. Their measured travels are
**464.2 vs 454.5 camera px — a ratio of 1.02**, which pins card A's rendered width at ≈ 1000 px. Had the
window clipped it, that ratio would have been well below 1. Confirmed independently in the browser at the
filming viewport (1380 px): card A ends at exactly **1000 px**, card B at **400 px**, and both contents
travel exactly **300 px**.

---

## Sampling-rate validation — and what it removed

**iPhone slow-motion clips are not uniformly 240 fps.** There is a slow-motion window, and outside it the
clip runs at **normal speed (30 fps effective)**, with a gradual speed **ramp** between the two. In a 30 fps
region each stored frame spans 1/30 s of real time instead of 1/240 s, so the camera can only film every 8th
rAF id — and a naive analysis scores the other seven as "never observed", which is a fact about the **camera**,
not about Chromium.

That artefact is real in this footage, and it was inflating an earlier draft of these numbers. It is now
detected and excluded, because the frame-id counter doubles as a **clock**: rAF issues ids at a steady 120 Hz
whether or not the display presents them, so the mean id-advance **per stored frame** measures the camera —

| | ids advanced per stored frame |
|---|---|
| 240 fps (slow-motion) | 120 ÷ 240 = **0.50** |
| 30 fps (normal speed) | 120 ÷ 30 = **4.00** |

Every run is gated on that measurement (accept 0.35–0.65) and **rejected runs are reported, never silently
dropped** — `analysis/decode_frame_ids.py` prints them, and `footage/segments.csv` records the verdict and
measured rate for **every** detected run in all three clips. Every accepted run measures **220–257 fps**;
every rejected one measures **42–160 fps**.

Three independent checks agree, which is why this is trustworthy rather than a convenient filter:

1. **Run length.** A 600 ms animation spans ~144 stored frames at 240 fps, but only ~18 at 30 fps. Every
   accepted run is 151–155 frames; every rejected one is 31–110.
2. **Measured sampling rate** (above) — an entirely separate signal, same verdict on every run.
3. **Odd holds.** A hold of 1 or 3 camera frames is not physical at 240 fps against 120 Hz. The accepted runs
   contain **almost none**; the rejected runs are full of them. Removing the ramp made the odd holds vanish
   exactly where predicted.

**What it changed.** The headline (Measurement 1 on `IMG_3836`) is **untouched** — that clip's nine runs are
all inside the slow-motion window. What moved was the two *stamped* clips: the DevTools-open control improved
from 92 % to **97 %**, and the closed session's unobserved-state count fell from 33.2 % to **24.8 %**. An
earlier draft also carried a mysterious "outlier" run whose missing ids all fell in one contiguous tail; it
was not mysterious, it was the ramp, and it is now simply excluded.

---

## Measurement 1 — visible cadence (no instrumentation of the page)

Each card's motion is recovered from the pixels by sub-pixel cross-correlation — the page is not instrumented
at all. **hold = 2 camera frames** means the card advanced on that refresh interval; **hold ≥ 4** means its
state was held for two or more intervals.

Runs are classified by which card actually travelled, and **never pooled across conditions**.

| condition | card | holds | **advanced on 1 refresh** | held ≥ 2 refreshes | hold histogram (camera frames) |
|---|---|---:|---:|---:|---|
| **B alone** *(stamp off · 4 runs)* | B | 280 | **98 %** | 2 % | `2→274  4→1  6→5` |
| **A alone** *(stamp off · 4 runs)* | A | 206 | **59 %** | 41 % | `2→122  4→80  6→4` |
| **both** *(stamp off · 1 run)* | A | 50 | 54 % | 46 % | `2→27  4→22  6→1` |
| | **B** | 48 | **54 %** | 46 % | `2→26  4→21  12→1` |
| **both** *(stamp on · 4 runs · DevTools closed)* | A | 206 | 58 % | 42 % | `2→120  4→82  6→4` |
| | **B** | 199 | **59 %** | 41 % | `2→117  4→73  6→4  12→1  15→1  (+3 odd)` |
| **both, DevTools OPEN** *(stamp on · 2 runs — control)* | A | 142 | **97 %** | 3 % | `2→138  4→2  6→2` |
| | **B** | 142 | **97 %** | 3 % | `2→138  4→2  6→2` |

Read down the card-B column: **98 % alone → 54–59 % beside the layout animation → 97 % again once DevTools is
open.** Card A's own cadence in those same runs is 54–58 %. The two **match within the resolution of the
experiment.**

The stamp-on and stamp-off "both" rows agree (54 % vs 59 %), which is the published evidence that the frame
stamp adds little **when a layout animation is already running** — the condition, and the only condition, in
which Measurement 2 below is quoted.

### How the percentages are computed

Stated exactly, so that `59 %` can be re-derived and not merely taken from the histogram:

* Each card's per-camera-frame displacement is obtained by cross-correlation. An **advance** is a
  displacement greater than **half of that run's nominal per-refresh step** (the step is the run's total
  travel divided by 72, the number of 120 Hz refreshes in 600 ms).
* A **hold** is the number of consecutive camera frames between two consecutive advances.
* **The denominator is the number of hold events**, not the number of camera frames.
  * `hold = 2` — the card advanced again on the very next display refresh: **one nominal 120 Hz interval**.
  * `hold ≥ 4` — its state was held across **two or more** refresh intervals.
  * `hold = 1 or 3` — **not physical** at 240 fps against 120 Hz. These come from exposures that straddle a
    refresh boundary and from tracker noise. They are **reported, not redistributed**, which is why the two
    percentage columns do not always sum to 100. In the uninstrumented runs there are **none at all** — the
    histograms contain only 2, 4 and 6 — which is itself the confirmation that the camera really was sampling
    at twice the refresh rate there. (A run *full* of odd holds is a run outside the 240 fps window; that is
    the third check in [Sampling-rate validation](#sampling-rate-validation--and-what-it-removed).)

Example — card A alone: holds `2→122, 4→80, 6→4`, total **206** events. `122 / 206 = 59 %` advanced on one
refresh interval; `(80 + 4) / 206 = 41 %` were held across two or more.

**The rates are not an artefact of that threshold.** Re-run at `0.4×`, `0.5×` and `0.6×` of the nominal step,
every figure is **identical, down to the histogram**:

| condition | 0.4× | 0.5× | 0.6× |
|---|---|---|---|
| A alone | **59 %** | **59 %** | **59 %** |
| B alone | **98 %** | **98 %** | **98 %** |
| both — A | **54 %** | **54 %** | **54 %** |
| both — B | **54 %** | **54 %** | **54 %** |

There is nothing in that range to reclassify: a camera frame's displacement is either ≈ 0 or ≈ a full step.
The full derivation — how travel, run boundaries and the nominal step are computed, and why `refreshes = 72`
is theoretical rather than taken from rAF — is in [`analysis/README.md`](analysis/README.md).

Raw per-run output: `results/cadence-cards-alone.csv` (the headline clip), plus
`results/cadence-both-devtools-closed.csv` and `results/cadence-both-devtools-open.csv` (the two stamped
clips, including the control).

## Measurement 2 — rAF states not observed on the display

A second, independent measurement. The page stamps each `requestAnimationFrame` callback with a unique id;
the footage is decoded, and any id that appears in **no** camera frame **was not observed on the display by
the external-camera measurement**.

Given the ≈2:1 camera-to-display sampling ratio and the independently legible binary strip, repeated absence
across *all* captured frames is treated as a state that **did not visibly persist for a full camera
exposure** — not as proof that it was never presented for any interval at all.

**Caveat, stated up front:** the stamp is itself one main-thread DOM write per frame, so it perturbs what it
measures — which is precisely why measurement 1 exists. It is quoted here only for the conditions where a
layout animation is already running, i.e. where the stamp's own contribution is small next to it. **The
primary evidence in this report is measurement 1**, which instruments nothing.

Only runs inside the 240 fps sampling window are counted — see
[Sampling-rate validation](#sampling-rate-validation--and-what-it-removed).

| session (incognito, no extensions) | runs | rAF ids generated | observed on the display | **not observed** |
|---|---:|---:|---:|---:|
| **DevTools CLOSED** | 4 | 339 | 255 | **84 — 24.8 %** |
| **DevTools OPEN** *(control)* | 2 | 171 | 169 | **2 — 1.2 %** |

One DevTools-open run had **every issued id observed — 85 of 85, none missing**; the other missed two. No
DevTools-closed run missed fewer than 19.

The denominator is `1 … (highest id the run reached)`. It starts at **1, not 0**: `measureRaf()` does `n++`
*before* writing, so `001` is the first id any callback issues — the `000` on screen beforehand is the static
markup (`<div id="fcNum">000</div>`), never a state rAF produced. Counting it would have inflated the
denominator by one per run.

Raw per-run output, including the measured sampling rate and the exact list of ids that were never observed:
`results/session1-incognito-devtools-closed.csv`, `results/session1-incognito-devtools-open.csv`.

---

## The callback rate is not the problem

Throughout every measurement above, the page's own counter reads:

```
rAF callback rate ~120 Hz · avg 8.3 ms · callbacks longer than 20 ms: 0
```

Chromium keeps issuing callbacks on time. What differs is which of the resulting visual states reach the
display — and nothing in `requestAnimationFrame`, in DevTools, or in any performance counter surfaces that.

The next visible state is **time-correct**: after a longer hold, the animation advances proportionally further
and lands where its timeline predicts. Intermediate visual states are simply not observed on the display.

---

## Method — and why the tested on-machine methods fail

**Every on-machine capture method tested suppressed the issue.**

* opening **DevTools** (any panel, any selection) → suppressed
* running a **screen recorder** (ScreenCaptureKit / OBS) → suppressed
* attaching **CDP** → suppressed

So a screen recording of the problem shows no problem, and a CDP-driven capture shows no problem. This is
worth stating plainly, because it likely explains why something this large has gone unreported.

The measurements were therefore taken with an **external 240 fps camera on a tripod**, filming the screen —
an observation channel **external to the Mac's rendering and capture pipeline**.

At 240 fps a nominal 120 Hz refresh is sampled **roughly twice per display interval**. That makes a presented
state very likely to be captured, but it is not a guarantee: exposure phase, exposure duration and rolling
shutter can straddle a refresh boundary. Straddled exposures are visible in the data as holds of 1 or 3
camera frames, and are reported rather than smoothed away.

The external camera brings its own artefact, and it is not a small one: **the clips are not uniformly
240 fps.** Frames outside the slow-motion window are detected and excluded, by two independent signals, before
anything is counted — [Sampling-rate validation](#sampling-rate-validation--and-what-it-removed).

## Reproducing the analysis

Everything in this document is re-derivable from the raw footage:

```
analysis/track_cadence.py       measurement 1  — visible cadence, no instrumentation
analysis/decode_frame_ids.py    measurement 2  — which rAF states were observed on the display
analysis/build_segments.py      regenerates footage/segments.csv from the clips
analysis/README.md              exact invocations, caveats, and what each does NOT show
results/*.csv                   per-run output of both measurements
footage/SHA256SUMS              pins the raw clips
footage/segments.csv            every detected run: frame range, condition, sampling verdict
```

---

## Steps to reproduce

With **DevTools closed**, on a 120 Hz ProMotion Mac, viewport ≥ ~1100 CSS px:

1. Open `index.html`.
2. Click **card A** a few times → the list glides irregularly.
3. Click **card B** → the identical 300 px glide is smooth.
4. Click **“Run both together”** → **both** are now irregular. B, smooth a moment ago, has taken on A's cadence.
5. Open DevTools (any panel), repeat 2 and 4 → both are smooth. Close DevTools → the irregularity returns.
6. Note the counter reads ~120 Hz with zero long callbacks in every case above.

## Expected

Card A should advance on the display's every refresh interval, as card B does when it runs alone — **98 %,
camera-measured**.

A **compositor-eligible** `transform` animation that advances smoothly when it runs alone is expected to stay
smooth under moderate concurrent main-thread activity. Here it takes on the layout animation's cadence
instead.

---

## Known variables not isolated

Stated so that a reviewer does not have to find them:

* The cards carry a `box-shadow` and a `border-radius`, which add paint/raster cost. Whether the effect
  survives with them removed was **not measured**.
* The cards contain text. Whether a plain rectangle behaves the same was **not measured**.
* Only a ProMotion (variable-refresh) display was tested. A fixed 60 Hz display was **not tested**.
* Every run was filmed on the same machine; no second Mac was available.

## Files

```
index.html      the repro — single file, no dependencies
analysis/       both measurement scripts, the segments generator, and their caveats
results/        per-run CSV output of both measurements
footage/        SHA-256, manifest, and the generated run→condition→sampling map
```
