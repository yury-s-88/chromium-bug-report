# Diagnostic phase — localising the cadence loss *below* Chrome

Companion to the top-level `README.md`. The README establishes the bug **at the display**, with an
external 240 fps camera (tripod, sharp). This note records a follow-up diagnostic phase — a Perfetto
trace, behavioural discriminators, a refresh-rate comparison, a flag sweep, and a **source-level read of
the macOS present path** (§5) — that narrows *where* the cadence is lost, *what triggers it*, and *the
code that pins it*.

**As with the README, the standard of evidence is marked explicitly:** *trace-measured*,
*camera-measured*, *visually observed*, or *inferred*. Most follow-up clips here were handheld with
soft focus and yield no reliable rate; the exception is the refresh-rate clip (§3), which is
**self-validating** — its 120 Hz reference reproduces the known bug (see *Method caveats*).

---

## Summary of the refined finding

1. **Not Chrome's compositor/Viz scheduling.** *(trace-measured.)* During the `both` condition the
   whole Chrome pipeline — vsync source → BeginFrame → compositor draw → `SwapBuffers` /
   `ImageTransportSurfaceOverlayMac::Present` — runs at a clean ~120 Hz. The cadence loss the camera
   sees is **below `SwapBuffers`**, in the macOS present path (CoreAnimation / WindowServer).

2. **Trigger is per-frame main-thread work — not compositor concurrency.** *(visually observed.)*
   Card B (compositor `transform`) becomes visibly irregular under **CPU busywork** (no commit) and
   under a **paint-only write** (`color`), but stays **smooth** when a *second compositor animation*
   runs beside it (`two transforms`). So a main-thread frame producer is required; two pure-compositor
   animations do not throttle each other. Even CPU busywork **with no commit at all** is sufficient.

3. **Rate-dependent, and macOS-specific.** *(camera-measured + visually observed.)* On the same panel
   switched to a fixed **60 Hz**, card B is nearly smooth (~8–16 % of refreshes held) versus **52 %**
   held at 120 Hz — an in-clip control that also *validates the method* (the 120 Hz swing reproduces
   the known bug). The numbers fit one interpretation: **card B's present is pinned near ~55 Hz
   regardless of display rate** — catastrophic-looking at 120 Hz (≈half), nearly fine at 60 Hz (≈full).
   On **Windows @ 144 Hz** the animation is smooth *(visual)*, so it is **not** high refresh rate per
   se — it is the macOS present path.

**Refined mechanism statement (inferred from the above):**
> Per-frame main-thread activity — CPU contention *or* a commit — perturbs the **macOS present** of
> a compositor animation. Not Chrome's scheduling; not compositor-layer concurrency; the final present.
>
> Quantitatively (§3), that perturbation **pins card B's present near ~55 Hz** — a macOS-specific value,
> visible as a large drop only when the display runs faster than it.

**Mechanism now localised in source, with a source-predicted one-flag fix that lands by eye (§5).** The
macOS present commits the CALayer tree **synchronously, not vsync-aligned** (`kVSyncAlignedPresentation` is
off by default), so the commit phase free-floats; a documented **~1.5 ms latch deadline** slips any late
commit to the next refresh; the main-thread producer supplies the "late" via the Viz begin-frame deadline.
**Prediction and result:** launching with `--enable-features=VSyncAlignedPresentation` — which only
re-anchors the commit to a fixed pre-vsync phase — makes **every mode smooth by eye** *(visually observed,
§5d)*. The prediction was made from source *before* the test.

> **"Smooth by eye" is this report's suppressor signature** (DevTools/recorder/CDP all made the bug *appear*
> to vanish without fixing it), so a by-eye result cannot by itself distinguish a genuine fix from suppressor
> #4. The **external 240 fps camera** — the one channel proven not to suppress — is what settles it, and now
> has: a fixed-camera **default-vs-flag pair** (`IMG_3848`, §5f) shows **card B in the A+B bug condition go
> from an in-clip-validated bug-level 60 % to a smooth-control-level 87 % hold=2, hold=2-dominant** — a true
> ~120 Hz, not a regularised 60 Hz. **Camera-confirmed for card B in A+B.** (Busywork/color under the flag
> were left under-sampled by a short second cycle — inconclusive, not refuted; card A/layout does *not* reach
> that smoothness. Scope stays on A+B.) **Update:** a later full-travel clip (`IMG_3852`) extends the
confirmation to **card A** too — flag-only card A = card B = 91 %, so the flag fixes both; the apparent
card-A residual above was a crop-clipping artefact, now withdrawn.

**One thing the flag result *does* bank now (free):** it operates entirely in the present/commit path
**below `SwapBuffers`**, so its effect **independently localises the bug below `SwapBuffers`** — that
localisation no longer rests on the possibly-suppressed Perfetto trace (§1). It also hands users a likely
**workaround** and names the **upstream fix target**.

This *refines* the README's framing ("a compositor `transform` inherits the *layout* animation's
cadence"): the trigger is neither specifically `layout` nor even a commit — it is any per-frame
main-thread work. The README's own supplementary note (one fixed-position text update per rAF took
card B from 98 % to 80 %) is the same effect, now isolated.

---

## 1. Trace analysis — `IMG_3841.mov` + `trace_…​.pftrace`

**Setup.** `diagnostic.html`, mode **A + B**, stamp **off**, DevTools closed. Trace recorded via
`chrome://tracing` (manual categories `cc, viz, gpu, blink, toplevel, benchmark`; **no** screenshot /
disabled-by-default categories, to avoid a capture path that could itself suppress). External 240 fps
camera filmed the screen concurrently. Parsed with Perfetto `trace_processor`.

`performance.mark('combined-*')` located **16 `combined` runs**. Within them, `Document::UpdateStyle­AndLayout`
(card A layout) and `AnimationHost::TickAnimations` (card B compositor animation) are both dense —
i.e. the `both` condition was genuinely active.

Per-stage cadence during the `combined` windows *(trace-measured)*:

| stage | event | result |
|---|---|---|
| vsync source | `CVDisplayLinkCallback` | ~120 Hz, regular, **0** intervals in the 60 Hz band |
| BeginFrame | `ExternalBeginFrameSource::OnBeginFrame` | ~120 Hz, regular |
| compositor draw | `DrawLayers.FrameViewerTracing` | ~103 Hz |
| **present (swap)** | `SwapBuffers` / `ImageTransportSurfaceOverlayMac::Present` | ~103–120 Hz, **85 %** of intervals at 8.3 ms, only **2 / ~3000** in the 14–20 ms (60 Hz) band |
| present feedback | `AnimationFrame::Presentation` | ~**17 %** of intervals in the 14–20 ms (60 Hz) band |

**Reading.** BeginFrame is *not* throttled to 60 Hz — this **rules out** the "Viz/cc frame-interval
throttling" hypothesis and bisection points A (BeginFrame) and B (draw scheduling). Chrome's pipeline
*through the CALayer present* is clean 120 Hz. The one signal carrying a 60 Hz component is the
**presentation feedback** (`AnimationFrame::Presentation`, ~17 %), i.e. *when frames actually reached
the display* — pointing below `SwapBuffers`, at the macOS present.

**Caveat (honest).** The camera clip for this trace was too noisy (handheld + soft focus + exposure
straddling) to *independently* confirm whether the bug **survived** tracing or was **suppressed** by
it. So whether Perfetto tracing suppresses (as DevTools / ScreenCaptureKit / CDP do) is **not cleanly
resolved**. Either way, the trace being clean through `SwapBuffers` rules out Chrome-side scheduling.

---

## 2. Behavioural discriminators — `IMG_3842.mov` *(visually observed)*

**Setup.** `diagnostic.html`, external camera, **no** trace / DevTools. Three modes in one clip.
The bug is grossly visible (per the README), so *jerky vs smooth by eye* is the report's own valid
detector — and it is the trustworthy signal here (the camera **held-fraction** for this clip was
unreliable: tracking noise exceeded the signal and mis-ordered the modes; see *Method caveats*).

| mode | per-frame main-thread work | commit | card B |
|---|---|---|---|
| **B + main-thread busywork** (CPU busy-loop) | yes | **no** | **jerky** |
| **B + non-layout write** (`color`) | yes | yes (paint) | **jerky** |
| **two transforms** (B + C, both compositor) | **no** | no | **smooth** |

**Reading.** A main-thread frame producer is **required**. Two compositor animations do **not**
throttle each other. And `busywork` — pure CPU each frame, *no* DOM write, layout or paint — is
enough, so a commit is **not** required; main-thread *occupancy per frame* suffices.

---

## 3. Refresh-rate dependence — `IMG_3844.mov` (camera) + Windows @ 144 Hz (visual)

**This is the one clean, self-validating camera measurement of the diagnostic phase.** The same
ProMotion panel was switched from 120 Hz to a fixed 60 Hz *during* one recording (the page's own rAF
control read `8.3 ms / ~120 Hz` before the switch, `16.7 ms / ~60 Hz` after — the switch landed at
~30–35 s), so both rates are captured in one clip, same method, same card. Card B (mode **A + B**)
animated on both sides of the switch:

| swing | display | card B held | effective present |
|---|---|---|---|
| 1 (27–32 s) | **120 Hz** | **52 %** of 72 refreshes | ~58 Hz |
| 2 (37–42 s) | **60 Hz** | 16 % of 36 refreshes | ~50 Hz |
| 3 (47–50 s) | **60 Hz** | 8 % of 36 refreshes | ~55 Hz |

**Why this clip is trustworthy where the others were not.** The 120 Hz swing reads **52 %** held —
i.e. it *reproduces the known bug* (README: ~46 %). A method that correctly detects the bug at 120 Hz
and reads ~8–16 % at 60 Hz is measuring a real difference, not a noise floor. The 60 Hz step is also
larger (~9 px vs ~4.5 px), improving signal-to-noise.

**Reading — a pin, not a ratio.** The *effective* present rate is ~similar at both display rates
(~50–58 Hz). So the effect is not "B drops to a fraction of the display rate"; it is:

> Card B's present is **pinned near ~55 Hz**. At 120 Hz that is ≈half (52 % held — looks broken);
> at 60 Hz that is ≈full (8 % held — looks smooth).

A single "~55 Hz cap" predicts **54 % / 8 %** held at 120 / 60 Hz; measured **52 % / 8–16 %** — a near
exact fit. This ties together the README's "inherits ~60 Hz cadence" phrasing, the §2 trigger
(per-frame main-thread work), and the §1 localisation (below `SwapBuffers`, macOS present).

**Windows @ 144 Hz** *(visually observed, no capture).* The same repro on Windows at 144 Hz is smooth
(subjectively ~98 %, only rare isolated hitches). 144 Hz > 120 Hz, so **high refresh rate alone is not
the cause** — the pin is specific to the **macOS present path** (Windows uses DWM / DirectComposition,
not CoreAnimation). Same standing as the README's Firefox contrast: a qualitative cross-platform check,
not a camera measurement.

**Caveat.** Small sample (1 × 120 Hz + 2 × 60 Hz swings), but internally consistent and method-validated.
The 60 Hz here is a *stable* 60 Hz (rAF intervals all 16.7 ms), so this is **rate**-dependence, not
variable-vs-fixed refresh; a fixed 120 Hz macOS panel (non-ProMotion) remains untested.

---

## 4. Flag sweep — the bug lives in the Metal present path *(visually observed)*

**Method.** `diagnostic.html`, mode **A + B**, display at **120 Hz** (confirmed each time via the page's
rAF control ≈ 8.3 ms), **one flag changed at a time**, restart between each, card B watched by eye
(jerky = bug present). Every flag confirmed applied via `chrome://gpu` / `chrome://version`.
Chrome 150.0.7871.125, macOS 15.7.3, M4 Pro.

| change (A+B on Metal, 120 Hz) | card B | note |
|---|---|---|
| **Metal** (default, ANGLE-Metal) | **jerky** (bug) | baseline |
| **Hardware acceleration OFF** (software compositing) | coupling **gone** | but slow-raster jank in single-card modes |
| **ANGLE → OpenGL** (`--use-angle=gl`, rAF still 120 Hz) | coupling **gone** | but single-card judder — an OpenGL-path quirk, unexplained |
| `--disable-gpu-vsync` | **jerky** | not a vsync-lock |
| Skia Graphite (all variants) | **jerky** | not the raster backend (Ganesh & Graphite both present via Metal) |
| `--disable-frame-rate-limit` | — | **black window**; breaks present entirely — inconclusive (but itself shows the action is in present) |
| `--disable-remote-core-animation` | **jerky** | inconclusive (flag likely ignored in 150) |

**Reading.** The coupling is present **only** on the default Metal present path; swapping the *entire*
present backend (software compositing or ANGLE-OpenGL) removes it. **No knob *within* Metal** — GPU
vsync, Skia raster backend, remote CoreAnimation — turns it off. So it is not a configurable
sub-behaviour: it is inherent to how the **Metal → CoreAnimation present** handles the concurrent-animation
case. (The alternative backends each bring their own perf issues — software slow-raster, OpenGL
single-card judder — so they are *layer-confirmation*, not usable workarounds.)

Consistent with §1 (clean through `SwapBuffers`) and §2 (per-frame main-thread trigger): the ~55 Hz pin
lives in the **Metal CALayer commit / CoreAnimation present**, where the compositor frame is handed to
the OS. *(Flag results are by-eye, now partly camera-corroborated: the software clip `IMG_3845` reads
card B **~23 % held** in software A+B — its one cleanly-sampled 240 fps swing — vs **~52 %** on Metal.
Coupling clearly reduced, though not down to fully smooth (~2 %), consistent with software's own raster
overhead. A Metal-vs-OpenGL camera pair remains the cleaner rigor step.)*

---

## 5. Source dive — the macOS present path in code *(source-readable + inferred + one falsifiable prediction)*

Read against the exact reproduced build (**Chrome 150.0.7871.125**, tag re-fetched — the hot present
path is byte-identical to `main`; the only 150-vs-`main` deltas are a `PowerMonitor` observer and a
`RefreshRateChangedOnSameDisplay`/`OnResume` DisplayLink refresh, none on the per-frame path). Standard
of evidence is marked per claim: **source-readable** (the code says so), **inferred** (follows from the
code but not directly shown), **predicted** (a falsifiable test is named).

### 5a. What the default macOS present actually does *(source-readable)*

`gpu/ipc/service/image_transport_surface_overlay_mac.mm` — `Present()` → `CommitPresentedFrameToCA()`:

- **The commit is *not* vsync-aligned by default.** `delay_presentation_until_next_vsync` is gated on
  `features::IsVSyncAligned()`, and `kVSyncAlignedPresentation` is **`FEATURE_DISABLED_BY_DEFAULT`**
  (`components/viz/common/features.cc`). The scrolling variant `kVSyncAlignedPresentationForScrolling`
  *is* on by default, but only applies `if (data.is_handling_interaction)` — this repro doesn't scroll,
  so it never triggers. **Net: every `Present()` calls `CommitPresentedFrameToCA()` synchronously, at
  whatever wall-clock moment the GPU thread finishes the frame** — the CALayer/CATransaction commit
  phase relative to the WindowServer refresh is *free-floating*, not latched to vsync.
- **Max pending swaps = 2 on macOS**, not 1 and not the Android high-rate 4:
  `skia_output_device_buffer_queue.cc` sets `number_of_buffers = 3` → `max_pending_swaps = 2`; the
  per-rate `_120hz = 4` override is `#if BUILDFLAG(IS_ANDROID)` only. In the immediate-commit mode the
  cap barely binds (each frame commits in its own `Present()`), so this is *not* a 1-in-flight
  throughput cap — an earlier framing I checked and discarded.
- **A latch deadline exists and is documented in-code.** `GetDisplaytime()` comment, verbatim from
  Chrome's own experiments: *"frames committed before (current_display_time − 1.5 ms) will be displayed
  at the next display time … The result is inconsistent … if commit is too close to the display_time."*
  So a commit that lands within ~1.5 ms of a refresh (or after it) **slips a full refresh**. This is the
  concrete, readable mechanism by which a "held" frame is produced — no opaque WindowServer coalescing
  needed to explain it.
- **`CommitScheduledCALayers` sets no explicit CATransaction timing** (`ca_renderer_layer_tree.mm`) —
  it updates CALayer properties under `ScopedCAActionDisabler`; `display_time`/`frame_interval` are used
  only to fill the *reported* `PresentationFeedback`, never to schedule the transaction. The transaction
  floats at commit wall-time.

### 5b. The one Metal-vs-GL divergence in the present path *(source-readable)*

The CALayer commit path is **identical** for ANGLE-Metal and ANGLE-GL (both hand an IOSurface to the
same `CommitScheduledCALayers`). The single place the two backends genuinely diverge is the back-pressure
wait in `CALayerTreeCoordinator::ApplyBackpressure()` (`ca_layer_tree_coordinator.mm`), run at the top of
every `CommitPresentedFrameToCA()`:

- **Metal:** polls the previous frame's `MTLSharedEvent` in a `while (!signaled) { check; Sleep(1 ms); }`
  loop — a **1 ms-quantized busy-poll**.
- **GL / software:** `backpressure_metal_fences` is **empty**, so that loop is a no-op; instead a
  `GLFence::ClientWait()` (created in `Present()` only `if (ANGLEImplementation != kMetal)`, guarded by
  `CHECK_NE(..., kMetal)`) blocks and wakes **exactly** on GPU completion.

Provenance traced end-to-end: the fences come from `access->GetBackpressureFences()`
(`output_presenter_gl.cc`), which returns `IOSurfaceImageBacking::exclusive_shared_events_`
(`iosurface_image_backing.mm`). That set is populated **only** by Metal `EndAccess`
(`AddSharedEventForEndAccess`, from Dawn/Ganesh-Metal) — empty on the GL path. This is the one code-level
Metal/GL split in the present path, and it aligns with the §4 flag sweep (Metal reproduces, GL & software
don't) — **but whether it actually operates on this light workload is the open question §5c takes up.**

### 5c. Trigger vs pin — the two-part mechanism *(inferred, each arrow tied to code)*

**Trigger (cross-platform — explains why even CPU busywork with *no* commit is enough):**
`requestAnimationFrame` itself requests a `BeginMainFrame` every vsync. `DisplayScheduler`
(`FrameDeadlineDecider` / `GetOSPreferredDeadline`, "use 75 % of the deadline for CPU work") waits for
the renderer's CompositorFrame up to a deadline before `AttemptDrawAndSwap`. A busy/producing main thread
delays that CompositorFrame → `DrawAndSwap` runs later in the interval → the GPU-thread `Present()` /
`CommitPresentedFrameToCA()` lands **later in the vsync interval**. Two pure-compositor transforms request
*no* main frame → no delay → smooth. This is the same `cc`/Viz scheduler on GL and Windows, so it is the
**trigger, not the pin**.

**Pin (Metal-specific) — the honest limit of what the code shows.** With the commit free-floating (5a)
and pushed late by the trigger, a commit that lands near/after the ~1.5 ms latch deadline slips to the
next refresh; if that happens on ~every other 120 Hz refresh, card B advances ~55–58×/s, and at 60 Hz the
16.7 ms interval absorbs the same lateness → nearly smooth — a **rate-independent ~55 Hz pin** that fits
the measured **52 % / 8–16 %** (§3). *That* half is readable and cross-platform. **What is *not* settled
in readable code is why this is Metal-only.** Both readable Metal-vs-GL candidates come up short:

- **The free-floating commit (5a) is not Metal-specific** — `kVSyncAlignedPresentation` is off for the GL
  path too, so GL commits just as un-aligned, yet GL is smooth (§4). So 5a alone cannot be the pin.
- **The `ApplyBackpressure` poll (5b) probably does not even fire here.** It waits on the *previous*
  committed frame's fence; that frame was drawn ~8.3 ms earlier and two small cards render in well under a
  millisecond, so the fence is almost certainly **already signaled on entry** — the loop runs once, no
  `Sleep`, no quantization, and returns at the same instant GL's `ClientWait` would. The 1 ms poll only
  bites if GPU render exceeds the frame interval, which this light workload does not. So on this repro the
  Metal/GL divergence in `ApplyBackpressure` likely **does not operate** — it cannot be assumed to be the
  pin. (`Gpu.Mac.BackpressureUs`, §5e-2, measures whether it ever sleeps — that is the arbiter, not code
  reading.)

So the source dive **localises the trigger and the entire commit-timing machinery**, but *from code alone*
does **not** conclusively identify the Metal-specific pin. **→ The §5d fix experiment points at it and the
§5f camera pair confirms it (for card B / A+B):** forcing vsync-aligned commit takes card B from an
in-clip-validated bug-level 60 % to smooth-control 87 % hold=2, so the pin **is** the commit phase (the
free-floating commit crossing the latch deadline). What remains unread is only the narrower question of *why the default Metal path
enters that bad phase while default GL does not* — most likely a per-frame commit-timing-profile difference
(GL's present may carry implicit vsync pacing that ANGLE-Metal's async commit lacks), closable by the §5e
`now_to_display` / histogram reads rather than an ANGLE dive.

### 5d. Falsifiable prediction + the fix target *(predicted — testable in minutes)*

The code already contains the cure and it is **off by default**: enabling `kVSyncAlignedPresentation`
makes `Present()` defer the commit and lets `OnVSyncPresentation()` (the CVDisplayLink callback, which
fires at a fixed ~1.5-interval offset *before* the target display time) do it — anchoring the commit to a
fixed pre-vsync phase and bypassing the variable draw-completion / 1 ms-poll phase entirely.

> **Prediction:** launching Chrome with `--enable-features=VSyncAlignedPresentation` (display at 120 Hz,
> mode A + B, DevTools closed) should make card B **smooth**. It anchors the commit to a fixed pre-vsync
> phase, so it targets the "commit lands late past the latch deadline" family *regardless of which
> sub-mechanism dominates*. A by-eye pass makes it a **candidate** fix; the external camera is what would
> confirm it (see the suppression caveat below). If it does **not** even look smooth, the pin is upstream of
> the commit phase (GPU-completion timing / ANGLE-Metal present itself).

> **RESULT — PREDICTION LANDS BY EYE** *(visually observed; since **camera-confirmed for A+B in §5f**).* Run
> with `--enable-features=VSyncAlignedPresentation` (Metal default, 120 Hz, DevTools closed): **every mode
> looks smooth** — including the ones jerky on stock Chrome (A + B, CPU busywork, `color`). The prediction was
> made from the source read *before* the test, so it is a genuine predictive hit; §5f then measured it with an
> external camera.

> ⚠️ **Fix vs. suppression — the open question this project exists to police.** Every on-machine
> intervention this report tried — DevTools, screen recorder, CDP — made the bug *look* fixed without fixing
> it. A by-eye "smooth" is therefore the **suppressor signature**, and cannot by itself tell a real fix from
> suppressor #4. What separates this case: mechanistically the flag alters **rendering-schedule code below
> `SwapBuffers`**, not an observation channel, and was predicted from source; and evidentially, the **external
> 240 fps camera** (the one channel proven not to suppress) has now **confirmed it for card B in A+B** — see
> **§5f**, the fixed-camera default-vs-flag pair. This was recorded here as a candidate; §5f promotes it to
> confirmed (scoped to A+B).

**What the flag result establishes — and its evidentiary limit.** `VSyncAlignedPresentation` changes exactly
one thing — *when* the otherwise-identical CALayer commit is issued (synchronous end-of-`Present()`, variable/
late phase → deferred to the `OnVSyncPresentation()` CVDisplayLink callback, fixed pre-vsync phase). Render
backend, backpressure, CALayer tree and IOSurfaces are untouched — a near-single-variable experiment. But the
readout so far is by-eye, so:

- **Cause: the commit's phase relative to the display refresh** — the free-floating commit crossing the
  ~1.5 ms latch deadline (§5a); re-anchoring the phase removes the jerk. By-eye here, then **camera-confirmed
  for card B / A+B in §5f** (default 60 % → flag 87 % hold=2, self-validated in-clip).
- **If real, commit-phase alone is sufficient to cure it:** the flag touches only *when* the commit issues,
  not the ANGLE-Metal render path, raster, or the *amount* of backpressure work. (Sufficiency, not exclusion:
  correcting the phase is enough; this does not prove render/GPU-completion timing plays no *upstream* role in
  producing the bad phase.) Consistent with the `ApplyBackpressure` poll being a non-factor (§5c).
- **The camera pair is the confirmation, and must read the *rate*, not just "smooth."** Shoot the same clip
  Metal-default vs Metal+flag (so the default swing self-validates at ~52 % held, as `IMG_3844` did in §3).
  The flag run must show a true **~120 Hz advance (held ≈ 2 %)** — *not* a regularised ~60 Hz that also looks
  smooth by eye but would mean the flag traded jerk for half-rate. `VSyncAlignedPresentation` gates commits to
  the CVDisplayLink tick, so this is a real thing to check, not a formality.
- **Still one narrowed open link:** *why the default Metal path lands in the bad phase while the default GL
  path does not* — most likely a per-frame commit-timing-profile difference (see §5e `now_to_display`). Minor
  and well-scoped, not the crux.

**Fix target for the upstream bug report:** primarily (1) default the macOS present to vsync-aligned for
the non-scrolling case, or otherwise pace the CALayer commit to clear the ~1.5 ms latch deadline — this is
the fix §5d directly tests. Secondarily, **only if `Gpu.Mac.BackpressureUs` (§5e-2) shows the poll actually
sleeps**, (2) replace the Metal `ApplyBackpressure` 1 ms-`Sleep` poll with an exact-wake primitive
(`MTLSharedEvent`'s `notifyListener` / `waitUntilSignaledValue`), matching the GL `ClientWait` path; if the
histogram is near-zero, (2) changes nothing and is not the fix.

### 5e. Two non-invasive reads to close the *narrow* remaining questions *(should not suppress like DevTools/CDP)*

*(The camera pair in §5d is what confirms the fix; these two reads are secondary — they separate which
sub-mechanism produced the bad phase and would corroborate the phase story from GPU-process instrumentation.
Note read #1 shares §1's tracing-suppression caveat, so it is not a substitute for the camera.)*

1. **Re-query the trace you already have** (`IMG_3841`'s `trace_….pftrace`). `CommitPresentedFrameToCA`
   is traced with arg `now_to_display = (display_time − Now()) µs` (source line in
   `image_transport_surface_overlay_mac.mm`). In the `combined` windows, histogram `now_to_display`: if a
   large fraction sits **below ~1500 µs** (inside the latch buffer), the latch-slip pin (5c) is shown
   *directly* from data already captured. Also check `OnVSyncPresentation`'s `callback_delay`.
   Perfetto `trace_processor` query:
   `SELECT ... FROM slice WHERE name='CommitPresentedFrameToCA'` joined to its `now_to_display` arg.
   **Caveat — this discriminator is contingent on tracing *not* suppressing the bug** (the unresolved §1
   question): if Perfetto suppresses, the captured present is clean regardless and `now_to_display` shows
   nothing, so a *negative* result here is uninformative. Lean on the camera pair (§5d) and the histogram
   (§5e-2), which don't depend on it.
2. **`chrome://histograms/Gpu.Mac.BackpressureUs`** — recorded around *exactly* the `ApplyBackpressure`
   wait. Compare a buggy run (DevTools closed, card B visibly jerky) against the DevTools-open control
   (fresh launch each; confirm card B is still jerky while you read, since it's a passive GPU-process
   counter, not a capture channel). A **multi-ms tail present only in the buggy run** ⇒ the poll is the
   amplifier; **both near-zero** ⇒ the backpressure wait is not where the time goes and the bad phase is the
   free-floating commit itself (the expected result, given §5d already fixed it by re-anchoring the phase).

### 5f. Camera test of the fix — CONFIRMED for card B in A+B *(camera-measured, self-validating pair)*

**`IMG_3848.mov` is the decisive clip.** A single **fixed-camera** (phone on a desk) recording carries
**both arms**: cycle 1 on **stock Chrome (no flag)**, then Chrome quit and **relaunched with
`--enable-features=VSyncAlignedPresentation`** for cycle 2 (the relaunch shows in the footage as a ~90 s
terminal/desk gap, excluded from analysis). Same framing for both arms, so **method error is common-mode and
cancels in the default→flag difference.** Both cadence signals are 240 fps (median step ~4.8 px/frame
throughout); analysed with the report's own `holds_of`, **segmented per mode** (transition frames excluded;
rejected short/ramp fires reported).

**Self-validation lives *inside the default arm* — no cross-clip assumption.** Same clip, same (default)
flag-state, same camera, the method reads bug-from-smooth directly:

| default-arm condition | card | runs | holds | hold=2 (adv/refresh) | hold≥4 (held) | odd |
|---|---|---:|---:|---:|---:|---:|
| **A + B** (bug condition) | **B** | 4 | 203 | **60 %** | 37 % | 2 % |
| **two transforms** (smooth control) | B | 3 | 131 | **86 %** | 8 % | 5 % |
| color (bug condition) | B | 2 | 97 | 49 % | 42 % | 8 % |

A+B reads **60 %** and two-transforms **86 %** *in the same breath* — a 26-point split that proves the method
resolves **bug from smooth on this exact framing** (and retires the `IMG_3847` worry that it might "read
everything ~88 %"). A+B's 60 % also matches the known bug (README "both" 54–59 %; §3 52 %).

**The flag lifts A+B / card B to that smooth-control level:**

| A + B, card B | runs | holds | hold=2 | hold≥4 | odd |
|---|---:|---:|---:|---:|---:|
| **default** (bug) | 4 | 203 | **60 %** | 37 % | 2 % |
| **+ `VSyncAlignedPresentation`** | 3 | 187 | **87 %** | 12 % | 1 % |

87 % sits at this clip's smooth ceiling (two-transf-default 86 %, B-only-flag 90 %) and is **hold=2-dominant**
(162 of 187 holds), so the flag restored a **true ~120 Hz advance, not a regularised ~60 Hz** (which reads
hold=4-dominant).

> **Camera-confirmed (scoped):** for **card B in the A+B bug condition**,
> `--enable-features=VSyncAlignedPresentation` restores the present cadence from **bug-level 60 %** (matching
> the in-clip default bug) to **smooth-control-level 87 %** hold=2 — a genuine fix, not a 60 Hz regularisation,
> and not observer suppression (the external camera is the one channel proven not to suppress). Fixed camera,
> odd-holds 1–2 %.

**Scope — what is NOT (yet) confirmed:**
- **busywork / color under the flag are inconclusive**, not confirmed. Cycle 2 was cut short, leaving the
  flag arm with **busywork = 1 valid run (65 %, + 3 rejected short fires)** and **color = absent**. One
  under-sampled run can neither confirm nor refute those modes; a completed cycle-2 re-run would settle them.
  The confirmation is scoped to **A+B / card B**. (Default-arm busywork read 69 %, color 49 % — both show the
  bug on default; only their flag arms are missing.)
- **Card A (layout) is indicative only and does *not* reach card B's smoothness:** A+B default 34 % → flag
  57 % hold=2. But card A's travel (248–276 px vs card B's ~316–345) shows the **card-A crop clips**, so those
  numbers are soft — read only the direction: **the flag does not make the layout animation smooth**,
  consistent with card A carrying its own residual (main-thread layout) irregularity, not only a present-phase
  loss. This **overturns `IMG_3847`'s handheld reading** that card A looked "also fixed" at 88 % — a flagged
  artifact, now corrected by the fixed-camera clip.

**Validity checks (both pass):** the crop **survived the relaunch** — a cycle-2 A+B frame shows card B inside
its box mid-travel; **both arms are fresh launches**, so the flag is the only systematic difference; and the
conclusion is **non-circular** because the default arm *independently reproduces the bug* (the flag-state is
the user's, but the 60 % is measured, not assumed).

**Method validated against published ground truth (no new footage) — and it re-reads the 87 %.** Running the
report's own `track_cadence.py` on the README's local clips reproduces every published number *exactly*: the
**DevTools-open control (`IMG_3833`) reads 97 %** (histogram `2→138 4→2 6→2`), and the headline (`IMG_3836`)
reads **B-alone 98 %, A-alone 59 %, both 54 %** — down to the histograms. Two consequences: (1) the **counting
is ground-truth-correct** — the exact concern behind "is the method or the count wrong?" is answered by
reproducing the published range 54 %→98 %; (2) since the same pipeline reads a *genuinely* smooth condition
at **97 %**, it is **not capped at ~87 %** — so `IMG_3848`'s ~87 % ceiling is **footage** (desk vs
tripod-sharp), not method. This re-reads the fix result: flag-A+B's **87 % sits at *this clip's* smooth
ceiling** (two-transf-default 86 %, B-only-flag 90 %), so the flag restores card B to the smooth level of this
footage — a **floor→ceiling** move (60→87), not a partial fix; on tripod-sharp footage the same method would
score it ~97 %. (This also validates the `IMG_3848` per-mode pipeline, which reuses the same `holds_of`.)

**`IMG_3847.mov`** (the earlier **handheld, flag-only** clip) remains the **by-eye first look**: all modes
looked smooth and card B was directionally hold=2-dominant. But its handheld cadence numbers (odd-holds
7–10 %, card-B B-only misread at 64 %, card-A at 88 %) are **superseded for measurement by `IMG_3848`**
(odd-holds 1–2 %). Keep it as the qualitative first pass; the numbers come from the fixed-camera pair.

**Cross-check — DevTools-open suppression on the new harness (`IMG_3850.mov`).** A third clip, **DevTools open
throughout** (intentional), single pass through the six modes, reads A+B **card B = 93 %** and **card A =
94 %** — so *opening DevTools alone* takes A+B card B from the bug's 60 % to 93 %, reproducing the README's
DevTools-suppression control (97 %, `IMG_3833`) on `diagnostic.html` with a different camera. The four
conditions line up coherently:

| A + B, card B | hold=2 | source |
|---|---:|---|
| default (bug) | **60 %** | IMG_3848 |
| + flag | **87 %** | IMG_3848 |
| DevTools open | **93 %** | IMG_3850 |
| DevTools open, index.html (README control) | 97 % | IMG_3833 ✓ |

Two consequences: **(1) footage sets the *absolute ceiling*** (handheld/desk ~86–93 %, tripod-sharp ~97 %) —
poor footage *compresses* the ceiling but **preserves the bug-vs-smooth contrast** (the method still reads bug
60 % and smooth 86–93 % in the same clip), so the within-clip 60→87 confirmation is robust; the absolute "12 %
gap from 100" at flag is footage, not residual bug (this clip's genuinely-smooth two-transforms reads the same
86 %). **(2) card A (layout)** looked left behind (flag 57 % vs DevTools 94 %) — but that 57 % was **crop
clipping** (see next), not a real residual.

**Card A resolved — the flag fixes the layout animation too (`IMG_3852.mov`).** The `IMG_3848` card-A crop
clipped the travel (248 px vs the true ~475), which inflated card A's held fraction — a textbook
"rectangle doesn't cover full travel → motion read as stillness" error. A closer clip with a **full-travel
card-A crop** (travel 471 px, verified) measures, at A+B:

| A + B | card B | card A |
|---|---:|---:|
| **flag only** | **91 %** | **91 %** |
| flag + DevTools | 88 % | 88 % |

**Card A tracks card B exactly** — 91 % = 91 % under flag-only, 88 % = 88 % under flag+DevTools. So the flag
brings the **layout** animation to the *same* smooth level as the compositor one; it does **not** leave card A
behind. (Default card A is ~55 %, README both-A 54 % / A-alone 59 %, so the flag lifts it ~55 %→91 %, same as
card B.) And **flag-only ≈ flag+DevTools**, so **DevTools adds nothing beyond the flag** — the flag alone
fully fixes the present. Mechanistically consistent: both animations present through the same CALayer commit,
so re-aligning that commit fixes both. The earlier "flag fixes only the compositor animation" reading is
**withdrawn** — it was the clipped crop.

---

## 6. Camera-free confirmation via Chrome's own telemetry — "jank, not dropped frames" *(passive built-in metric)*

The bug and the fix are both confirmable **without a camera**, using Chrome's own smoothness telemetry —
which, being a **passive counter read** (not a screen capture), does **not** suppress the way DevTools /
ScreenCaptureKit / CDP do. `chrome://histograms`, A+B repro, DevTools closed, on the reproduced build:

| `Graphics.Smoothness.*.CompositorThread.CompositorAnimation` (card B) | default | + `VSyncAlignedPresentation` |
|---|---:|---:|
| **`Jank3`** (mean) | **11.5** (spread 0–14+, long tail) | **0.1** (90.6 % at zero) |
| **`PercentDroppedFrames3`** (mean) | **0.0** (100 % of samples at zero) | 0.0 |
| `Checkerboarding3/4` (mean) | 0.0 | 0.0 |

**Three things this establishes:**

1. **Camera-free confirmation of the fix.** Chrome's own jank metric collapses **11.5 → 0.1** with the flag
   — independent corroboration of the camera pair (§5f: card-B hold=2 60 % → 87 %). No camera, no build, no
   screen capture.
2. **The bug is *jank* (uneven cadence), not *dropped frames*** — from Chrome's own accounting:
   `PercentDroppedFrames = 0` throughout (the states *do* reach the display, just held/late, then jump
   proportionally), while `Jank3` is high on default. This is the report's careful wording ("rAF states not
   observed", never "*rendered frames that were dropped*") confirmed by Chromium's internal counters.
3. **Why it hid in plain sight.** Standard smoothness monitoring — the DevTools FPS meter, "dropped frames"
   dashboards — watches `PercentDroppedFrames`, which reads **0** here. Only the *jank* metric catches it, so
   even Chrome's own default-facing telemetry reports "no dropped frames" while the animation is visibly janky.

**Reproduce in ~2 minutes (a reviewer needs no footage):** `chrome://histograms` → *Switch to Monitoring
Mode* → run A+B (Loop, DevTools closed) ~20 s → *Refresh* → read
`Graphics.Smoothness.Jank3.CompositorThread.CompositorAnimation` (≈ 11). Relaunch with
`--user-data-dir=/tmp/x --enable-features=VSyncAlignedPresentation`, repeat (≈ 0).

**Raw dumps** of these three reads (verbatim `chrome://histograms`) are committed under
[`telemetry/`](telemetry/) — the default-vs-flag `Jank3` pair plus the full `Graphics.Smoothness` default
capture that shows `PercentDroppedFrames = 0` alongside the jank.

**Caveat (evidence standard).** `Jank3` is computed from Chrome's **present estimate** (the feedback
timestamp is `GetDisplaytime`, not the real WindowServer present), so it **under-reports magnitude** vs the
external camera (11.5 vs the camera's ~50 % held) — it captures the commit-phase unevenness Chrome *models*,
which is exactly why the flag (which regularises that phase) drives it to ~0. The **direction** (default ≫
flag) is unambiguous and agrees with every other channel; the **absolute** ground truth stays the camera.
This also partly answers §1's open "does tracing suppress?": this passive counter does **not**, and it shows
the bug — so the loss is real and on-machine-measurable, just not through a *capture* channel.

---

## 7. Why the fix is off by default — the upstream history *(primary-source: public Gerrit CLs)*

§5–§6 establish *that* `--enable-features=VSyncAlignedPresentation` fixes the bug. The remaining
conceptual gap was *why Chromium ships it disabled*. It is answerable from public primary sources, and
the answer changes what this report should ask for upstream.

**Provenance.** Every quote below is **verbatim from a merged CL description** on
`chromium-review.googlesource.com` (public Gerrit REST API, no sign-in; `crrev.com/c/<n>` for each).
All CLs are in `chromium/src`, branch `main`, owner **Maggie Chen** (`magchen@chromium.org`); workstream
bugs **330771325** and **1404797**. The end state was verified byte-for-byte against the reproduced build
(tag `150.0.7871.125`) — `kVSyncAlignedPresentationForScrolling` = `FEATURE_ENABLED_BY_DEFAULT`,
`kVSyncAlignedPresentation` = `FEATURE_DISABLED_BY_DEFAULT`. *(Standard of evidence: the timeline and
quotes are **source-readable**; the causal link from the 2025 experiment to the 2026 default is marked
**inferred** below, because no CL states it.)*

### 7a. Short answer — the question splits in two

**(i) Why is broad vsync-aligned present not the default behaviour?** *(Answered, firmly.)* Because it
**was** the default behaviour in the experiment and lost. It was implemented, put behind Finch,
A/B-tested on Beta with three targeting arms, and **rejected in favour of the narrow scroll-only slice** —
it regressed **guardrail metrics** (Interaction-to-Next-Paint is named explicitly) even though it improved
smoothness a lot. **→ Hypothesis 1 (latency), confirmed.**

**(ii) Why is *this* flag — `kVSyncAlignedPresentation`, created 2026-03-23 — off?** *(Answered less
firmly.)* Because it is a **fresh re-introduction of that general capability that has not been through an
experiment of its own.** New Chromium features land disabled; no CL states a reason for this one, and the
entire code review is a single comment — *"lgtm, thanks."* **→ Hypothesis 2 (still under development)** —
and for *this* sub-question it is **co-primary with (i), not a footnote**: the workstream is visibly live
around it (present-path latency trimmed three days later in CL 7701873; CL 5911632 still open 2026-07-16).

**Two readings of (ii), both consistent with the public record.** *"Parked off after the 2025 verdict"*
and *"staged for a second attempt now that the present path is cheaper"* both fit. The timeline leans
towards the second — but CL 7701873's latency trim is gated on **other** flags
(`kAllowCallbackWithoutPostTask`, `kEnableDrDc`, `CADisplayLinkInBrowser`), so the "trim → re-attempt"
link is **circumstantial, not proven**. **The workstream bug 330771325 has since been read (§7h) and does
not settle it** — all 16 of its comments are bot-posted CL notifications, with no human discussion. Both
readings stand.

**What is firm either way, and is what this report needs:** the gap the bug lives in is a **known, measured
trade-off, not an oversight** — so the upstream ask must be a *scoped re-evaluation*, not "flip the flag"
(§7f).

### 7b. Timeline

| date | CL | what happened |
|---|---|---|
| 2023-08-25 | [4710566](https://crrev.com/c/4710566) | **Implemented.** "Delay frame presentation until next VCDisplayLink callback on Mac" — reviewed by ccameron. Bug 1404797 |
| 2023-11-17 | [5037740](https://crrev.com/c/5037740) | Put behind a flag (`DelayOnFramePresent`) |
| 2024-03-27 | [5399694](https://crrev.com/c/5399694), [5399901](https://crrev.com/c/5399901) | Finch prep: "In preparation for the finch kVSyncAlignedPresent"; added to `fieldtrial_testing_config` |
| 2025-01-17 | [6143459](https://crrev.com/c/6143459) | **First named cost: INP.** Mitigation added so non-animating/non-interacting frames commit immediately |
| 2025-01-17 | [6182347](https://crrev.com/c/6182347) | Dropped the `kNumPendingFrames` variant — "does not show any benefit on stable 1%" |
| 2025-03-10 | [6334447](https://crrev.com/c/6334447) | UMA to measure what fraction of frames must wait |
| 2025-04-24 | [6482387](https://crrev.com/c/6482387) | **Three Finch arms created**: `AllFrames`, **`Animation`**, `Interaction` (param `Target`) |
| 2025-05-20 | [6558510](https://crrev.com/c/6558510) | **Experiment verdict**: `Interaction` best on Beta → made the launch target |
| 2025-06-25 | [6674714](https://crrev.com/c/6674714) | **Shipped interaction-only**, enabled by default; the `AllFrames`/`Animation` targeting code **deleted** |
| 2026-03-23 | [7690172](https://crrev.com/c/7690172) | **Our flag added**: new `kVSyncAlignedPresentation`, **disabled by default**; old one renamed `…ForScrolling` |
| 2026-03-26 | [7701873](https://crrev.com/c/7701873) | Present-path latency trimmed: callbacks run directly instead of via posted tasks |
| open, 2026-07-16 | [5911632](https://crrev.com/c/5911632) | Workstream still active |

### 7c. The four quotes that carry it *(verbatim)*

1. **The original motivation is this report's own mechanism** — CL 4710566:
   > "When CVDisplayLinkBeginFrameSource is enabled, **in order for CoreAnimation to latch frames in a
   > consistent timing**, only present a frame in CVDisplayLink callback."

   That is §5a's latch deadline, named by the author in 2023. The code was written to cure exactly the
   inconsistency we measure.

2. **The cost is latency** — CL 6143459:
   > "With kVSyncAlignedPresent enabled, **all frames are delayed** and committed to CoreAnimation in the
   > next VSync to improve smoothness. This CL allows frames that don't handle interactions or animations
   > to commit immediately without waiting. **The goal is to prevent INP (Interaction to Next Paint)
   > regression** when a frame doesn't have a smoothness issue."

3. **The trade-off, stated flatly** — CL 6482387:
   > "**This group still has big regressions on guardrail metrics, although it improves a lot on
   > smoothness metrics.**"

   *(Interpretation, not source: the antecedent of "This group" is ambiguous in context. The most natural
   reading is the experiment group carrying the CL-6143459 INP mitigation — i.e. **even with** the
   mitigation, broad alignment regressed guardrails, which is why explicit target arms were introduced.
   Read it as ambiguous; the sentence's plain content — smoothness up, guardrails down — is not.)*

4. **The verdict** — CL 6558510:
   > "There are three arms in VSyncAlignedPresent finch experiment: "AllFrames", "Animation", and
   > "Interaction". Among these three arms, **"Interaction" shows the best result in Beta channel.** Now
   > make the fieldtrial default from "AllFrames" to "Interaction" which will be the finch launch target
   > on stable channel."

   And the launch, CL 6674714: *"Enable VSyncAlignedPresent by Default on Mac. **This feature improves
   scrolling smoothness significantly.**"*

### 7d. The hypotheses, scored

| hypothesis | verdict |
|---|---|
| **1. Latency** | **Confirmed, and specific — this is why the *behaviour* is not the default (§7a-i).** Not a guess about latency in the abstract: **INP** is named in CL 6143459, "guardrail metrics" in CL 6482387, and a real three-arm Beta experiment picked the arm with the least latency exposure. |
| **2. Unfinished / still under development** | **Co-primary — this is why *this flag* is off (§7a-ii).** `kVSyncAlignedPresentation` is a fresh (2026-03) re-introduction that has had no experiment of its own, is absent from `fieldtrial_testing_config.json` in `main`, and sits in a visibly live workstream. Not "unfinished code", though: the behaviour itself shipped, for scrolling. |
| **3. Perf / power regressions** | **No specific evidence found.** Possibly subsumed by the unspecified "guardrail metrics"; nothing in any CL names power or GPU cost. |

### 7e. What the sources do **not** say — the limits of this finding

- **The `Animation` arm's standalone result is unknown.** CL 6558510 says only that `Interaction` was
  *best of three*. It does **not** say `Animation` was acceptable-but-second, and it does not say it was
  harmful. Treat its individual numbers as **unread** — and now as **checked-and-absent**: bug 330771325
  was read (§7h) and its 16 comments are all bot-posted CL notifications, with no per-arm data.
- **CL 7690172 gives no reason for the disabled default.** Its entire review is one comment — *"lgtm,
  thanks."* So "off by default **because** `AllFrames` lost in 2025" is **inferred** from the chain
  (new flags start off; the same behaviour had already lost), not stated by anyone.
- **The experiment's arms were compared on aggregate Beta metrics**, whose composition is not public.

### 7f. Consequence — the upstream ask must change

**Do not ask upstream to flip `kVSyncAlignedPresentation` on.** That is the `AllFrames` behaviour, which
has already been measured and rejected once. Asking for it re-opens a settled question and invites the
answer "we tried; it regressed guardrails."

**And the obvious-looking alternative has a trap worth naming.** "Just restore the `Animation` target —
animation frames aren't interactions, so there's no INP cost" is **wrong**, and an upstream reviewer will
see why immediately. `is_handling_animation` is set in `cc/trees/layer_tree_host_impl.cc`
(`CompositorFrameMetadata` population) as

```
metadata.is_handling_animation = HasMainThreadAnimation(active_types) ||
                                 HasCompositorThreadAnimation(active_types);
```

and `HasMainThreadAnimation` (`cc/metrics/frame_sequence_metrics.h`) includes **`kRAF`** alongside
`kMainThreadAnimation`, `kCanvasAnimation` and `kJSAnimation`. **Any page running a `requestAnimationFrame`
loop therefore has `is_handling_animation == true` more or less permanently** — including while it paints
the response to a click or keypress. An `Animation` target degenerates toward `AllFrames` on exactly the
pages INP is measured on, so it inherits the regression that killed `AllFrames`. *(This is also why our own
repro is squarely inside `is_handling_animation`: card A is `kMainThreadAnimation`, card B is
`kCompositorAnimation` — literally the tracker named in the §6 telemetry,
`Graphics.Smoothness.Jank3.CompositorThread.CompositorAnimation` — and the page's own rAF control adds
`kRAF`.)*

**What *is* defensible to ask** — three points, each grounded in something this report measured or read:

1. **The shipped gate is narrower than "interaction" sounds, and it is INP-shaped.**
   `LayerTreeHostImpl::IsHandlingInteraction()` returns true only for
   `GetActivelyScrollingType() != kNone || input_delegate_->IsHandlingTouchSequence()` — **active scroll
   or an in-progress touch sequence**, nothing else. And INP by definition measures only **click, tap and
   keypress**; [scrolling is explicitly excluded](https://web.dev/articles/inp). So the shipped design
   aligns precisely the frames INP cannot charge it for. That is coherent engineering — and it explains
   the shape of the gap rather than treating it as an oversight. **But it has a user-visible cost the
   experiment's metrics would not have named** (§7g, confirmed by eye on stock Chrome): the *same*
   animation presents at a true ~120 Hz while a scroll is in progress and drops back to the ~55 Hz pin the
   instant the gesture ends. The gate does not just withhold a fix — it hands it out and takes it back
   mid-animation.

2. **The benefit is display-rate-dependent, so a Mac-wide aggregate dilutes it.** §3 measured the same
   animation at ~52 % held on a 120 Hz ProMotion panel versus 8–16 % at a fixed 60 Hz — i.e. the loss this
   flag cures is **severe at 120 Hz and near-invisible at 60 Hz**, while the latency cost of alignment
   applies at every rate. A Beta population dominated by 60 Hz Macs would therefore show the smoothness
   win diluted and the guardrail cost undiluted. **The ask: re-evaluate the general path segmented by
   display refresh rate (or gate it on high-refresh / variable-refresh panels), rather than as one Mac-wide
   arm.** *(Inference from our §3 measurement plus the public arm structure — the Beta population's panel
   mix is not public.)*

3. **The latency budget has changed since the arms were compared.** CL 7701873 (2026-03) removed posted-task
   hops from the commit/callback path "to avoid unnecessary delays" — after the 2025 experiment. So the
   measured cost of alignment today is not necessarily the cost that lost in 2025. *(Inferred.)*

### 7g. A free falsifiable prediction — **HOLDS** *(visually observed, then corroborated on the passive counter; ground truth inherited from §5f)*

`kVSyncAlignedPresentationForScrolling` is **on by default** and gates on `data.is_handling_interaction`,
which is true while the page is **actively being scrolled**. The commit path taken during a scroll is
therefore *the same vsync-aligned path* the flag forces all the time.

> **Prediction:** on **stock Chrome, no flags**, with the A+B repro running on a page tall enough to
> scroll — card B should become **smooth while a scroll gesture is actively in progress** and revert to
> jerky the moment the scroll ends.

**The harness is built — it is the bottom-right box in `diagnostic.html`.** Design constraints, each
load-bearing: the cards must **stay still while something scrolls** (otherwise they translate with the
viewport and neither the eye nor `track_cadence.py` can read their cadence), so the scroller is a small
`position: fixed`, `overflow-y: scroll` box with the cards left **outside** it and untouched. `position:
fixed` is deliberately on the **scroller, never on the cards** — promoting the cards to their own
compositing layer would change their present setup, and a null result could then be the harness rather
than the mechanism. And it is **pure HTML/CSS, zero JavaScript**: the scrolling text is its own
"gesture is live" indicator, so the test adds no per-frame main-thread work — which matters, since that
is the bug's own trigger (§2).

**Procedure.** Stock Chrome, **no flags**, DevTools closed, 120 Hz. Mode **A + B** → **Loop** → confirm
card B is jerky → two-finger scroll **inside the box**, fingers down, slow and continuous (not a flick —
a flick ends the gesture and momentum may not keep `is_handling_interaction` set) → watch card B *during*
the gesture, then release and watch again.

**Why a positive result would be strong.** The obvious confound runs the *wrong way*: scrolling **adds**
main-thread and compositor work, which by §2's trigger should make card B **worse**, not better. So
"smooth while scrolling" cannot be explained by reduced load — it points at the one thing scrolling changes
in the present path, `data.is_handling_interaction`. *(Remaining caveat: a scroll also changes what the
compositor is doing more broadly; this is a strong hint, not a single-variable experiment like §5f.)*

> **RESULT — PREDICTION HOLDS** *(visually observed, 2026-07-19).* Stock Chrome, no flags, A+B on Loop:
> **card B is smooth while the scroll gesture is in progress.** Predicted from source *before* the test,
> as in §5d.

**What this buys.** The by-eye pass alone would have sat on this report's **suppressor signature** tier
(§5d) — but it has since been **measured on a passive counter** (below), which is the same channel §6 used
to promote the flag. Three things separate it from suppressors #1–#3 (DevTools / recorder / CDP), and they
are the same three that separated the flag:

- **It is not an observation channel.** Nothing is capturing the screen. Scrolling is an *input to the
  rendering path*, and there is a **source-readable branch** that predicts this exact result:
  `delay_presentation_until_next_vsync = … || (IsVSyncAlignedForScrolling() && data.is_handling_interaction)`
  with the feature `ENABLED_BY_DEFAULT`. The prediction was made from that line before the test.
- **The confound runs the wrong way.** Scrolling *adds* compositor and main-thread work, which by §2's
  trigger should make card B **worse**. "Smooth while scrolling" cannot be explained by reduced load.
- **It reaches the same code as the flag.** Both set the same boolean in the same `Present()`; the flag
  sets it unconditionally, the scroll sets it for the duration of the gesture. So this is not a new
  mechanism — it is the §5d/§5f mechanism, reached through Chrome's own shipped gate.

**Residual caveat, stated plainly:** a scroll changes more than one thing about what the compositor is
doing, so this is a **strong corroboration, not a single-variable experiment** like §5f. And macOS itself
may behave differently during a scroll (the standing worry from the ScreenCaptureKit suppressor, which
touches zero Chrome code yet suppresses). The difference here is that we have a **readable branch that
predicts it**, and the recorder case did not — plus a passive-counter measurement that lands on the flag's
own published number.

> **MEASURED on the passive counter** *(2026-07-19; raw dumps in [`telemetry/`](telemetry/)).* Stock Chrome,
> **no flag**, A+B on Loop, one monitoring window, two sequential phases — **scrolling throughout, then not
> scrolling at all**. *(Read with the two-part framing below: this shows the aligned path is **engaged**;
> §5f's camera is what shows that path is a **real** fix.)*
>
> | `Jank3.CompositorThread.CompositorAnimation` (card B) | n | mean | at zero |
> |---|---:|---:|---:|
> | **scrolling** — stock Chrome, no flag | 18 | **0.11** | **94.4 %** |
> | **not scrolling** — same launch, derived (see below) | 16 | **12.44** | **0 %** |
> | *published reference: default (§6)* | 75 | *11.5* | *1.3 %* |
> | *published reference: `--enable-features=VSyncAlignedPresentation` (§6)* | 32 | *0.1* | *90.6 %* |
>
> **Scrolling lands on the flag's number; not scrolling lands on the default's.** Not merely "better" —
> **0.11 vs the flag's 0.1**, and **12.44 vs the default's 11.5**. The two distributions are **completely
> disjoint**: every scrolled sequence scores ≤ 2, every unscrolled one ≥ 8.

**Not a second witness — one witness reported three times.** `CompositorAnimation`,
`NativePropertyAnimation` and `MainThread.MainThreadAnimation` are **byte-identical** in both dumps (n = 34,
same buckets). That is not card A independently confirming card B: in mode A+B both animations are active
across the *same* frames, so all three frame-sequence trackers cover the same windows and report the same
jank. Card A's independent witness is the **camera** (§5f / `IMG_3852`), not this histogram. *(Earlier
wording here claimed card A "behaves identically" as corroboration — withdrawn.)*

**How the unscrolled arm was obtained, and why the subtraction is sound.** The second dump is **cumulative**
— monitoring was not reset between phases — so the unscrolled arm is `dump2 − dump1`, bucket by bucket. Three
things make that exact rather than approximate: (1) every bucket of dump 1 appears in dump 2 with a count
≥ its own (checked); (2) the residual is cleanly separated (buckets 8–18, nothing below 8), so no sample is
ambiguously assigned; (3) **`Jank3.MainThread.WheelScroll` is byte-identical in both dumps** (n = 9, same
buckets) — proving no further scrolling occurred in phase 2 *and* that dump 2 genuinely contains dump 1.
Being one monitoring window also makes this a **paired within-session measurement** — same launch, same
process, same page instance — so launch-to-launch variance cancels, the same way §5f's single-clip pair
cancels camera error.

**The `Jank3` discipline of §7h applies here too — applied, and then discharged.** §7h establishes that on
macOS the presentation timestamp is a **model**, so a favourable `Jank3` delta deserves the same discount as
an unfavourable one. Refusing to apply it only when the number is a win would be exactly the bias §7h warns
about. So the discriminating question was checked in source rather than assumed: **is `feedback.timestamp`
computed differently on the aligned path than on the immediate one?**

**No — there is no branch.** `CommitPresentedFrameToCA()` is a *single* function, reached either
synchronously at the end of `Present()` or deferred from `OnVSyncPresentation()`, and on both paths it
evaluates the identical expression:

```
display_time = GetDisplaytime(base::TimeTicks::Now());
```

So the timestamp *source* does not shift between the arms; only **when that line runs** does — which is
precisely the physical variable under test.

**But one honest residual remains, and it is why this section does not rest on `Jank3`.** `GetDisplaytime()`
is evaluated **at commit time**, with `Now()` as its input. Aligning the commit to the DisplayLink callback
makes that input near-constant *by construction* — so the **modelled** presentation times become regular
almost tautologically, and the metric would report low jank for an aligned commit whether or not the real
display outcome improved.

**→ The ground truth is §5f, and it transfers by code-path identity.** Scrolling sets the *same* boolean and
runs the *same* deferred `CommitPresentedFrameToCA()` from `OnVSyncPresentation()` that
`--enable-features=VSyncAlignedPresentation` forces permanently — and **§5f already measured that path on an
external 240 fps camera**, finding a true ~120 Hz advance (hold=2-dominant, 87 %), *not* a regularised
60 Hz. So the correct reading is two-part, and neither half carries it alone:

> The `Jank3` pair shows that **scrolling engages the aligned commit path**, cleanly and with disjoint
> distributions. **§5f's camera** shows that **that path is a real fix**, not a metric artefact and not a
> half-rate regularisation. This is the identical arrangement §6 already has — its flag `Jank3` numbers are
> backed by §5f's flag camera; §7g inherits the same backing because it is the same code path.

**Caveats, in order of seriousness:**

- **Which of two code branches produced the alignment is not isolated.** `Present()` sets
  `delay_presentation_until_next_vsync` on default Chrome via *either*
  `IsVSyncAlignedForScrolling() && data.is_handling_interaction` (the predicted path) *or* the separate
  `… && NumPendingSwaps() > 1` clause, which is also live by default and could fire more often under scroll
  load. **Both are the same vsync-aligned commit**, so the *mechanism* conclusion is unaffected — but this
  measurement does not prove *which predicate* opened the door. Stated rather than glossed.
- **Order not counterbalanced** (scrolled first, then unscrolled) and **n is small** (18 / 16). The complete
  separation of the distributions makes drift an implausible explanation, but a reversed-order repeat would
  close it.
- **The gesture registered as `MainThread.WheelScroll`** (n = 9, mean 2.3), not `CompositorThread` — a
  main-thread-repainted scroll. `IsHandlingInteraction()` reads `GetActivelyScrollingType()`, which is set
  for main-repainted scrolls too (`IsCurrentScrollMainRepainted()` is a *separate* query), so this does not
  undercut the reading — but it is why the scroll's own jank is 2.3 rather than ~0.
- Standing `Jank3` caveat (§6): it is computed from Chrome's present **estimate**, so it under-reports
  magnitude against the camera. Direction and the two-sided landing on published references are what carry
  here, not absolute magnitude.

**Hardening attempt #1 — REJECTED as pooled** *(logged, not dropped — per this report's own rule that every
rejected run is reported)*. A first `chrome://histograms` read (2026-07-19) ran **unscrolled first, then
scrolled, in one monitoring window**, so the dump is a **mixture of two conditions** and cannot be read as a
scroll measurement — pooling conditions is the specific error this report was rebuilt to eliminate. For the
record: `Jank3.CompositorThread.CompositorAnimation` n = 171, mean **6.4**, with **24.0 % of sequences at
zero** — against the published default reference (n = 75, mean 11.5, **1.3 %** at zero). So the pooled run
contains **41 zero-jank sequences where a pure-default run predicts ~2**. That excess is a **hint in the
predicted direction, explicitly not a result**: the split between the two phases is unknown, and one
self-consistent decomposition (~25 % of sequences flag-like at ~0.1, the remainder at ~8.5) is a *fit*, not
a measurement. Two side results from the same dump are usable, though, because they are not condition-split:
`Jank3.CompositorThread.WheelScroll` (n = 102, mean 2.0) **confirms the gesture registered as a real
compositor scroll** — i.e. the §7g harness does enter `is_handling_interaction` territory — and
`PercentDroppedFrames3.CompositorThread.CompositorAnimation` = **0.5**, again "jank, not dropped frames" (§6).

> **Correct protocol for attempt #2** (the pooling is the only thing that went wrong): **two fresh launches**
> (`--user-data-dir=/tmp/x`), each ~30 s, **one scrolled from the first frame to the last and one never
> scrolled** — an in-session pair rather than a comparison against an older published number, matching §5f's
> within-clip self-validation. Read via the filtered page `chrome://histograms/Graphics.Smoothness.Jank3`
> (Monitoring Mode), keeping the repro and the histogram page in **separate visible windows** so neither is
> backgrounded and throttled. The scrolled arm must contain **no unscrolled animation time at all**.

**Three things it already establishes:**

1. **A fourth confirmation path that needs nothing but stock Chrome** — no flag, no camera, no build, no
   footage. A reviewer with a ProMotion Mac can see the bug *and* Chrome's own fix for it in one minute.
2. **The sharpest possible upstream framing:** *Chrome's shipped code already fixes this animation — but
   only while the user happens to be scrolling.* Stated as a defect in its own right: the **same
   animation presents at a true ~120 Hz during a scroll and falls back to the ~55 Hz pin the instant the
   gesture ends** — a user-visible cadence discontinuity produced by the feature gate itself, not by
   anything the page did.
3. **Part of why this went unreported.** Scrolling is the most common thing a user does on a page, and it
   silently repairs the cadence for its duration — so the degradation is easiest to see exactly where
   people look least: a static page left alone to animate.

### 7h. What the issue trackers add *(primary-source: sign-in-gated tracker, snapshots 2026-07-19)*

**Provenance.** `issues.chromium.org` requires sign-in; these are the reporter's own authenticated page
snapshots, taken **2026-07-19**. All status claims are as-of that date.

**Bug status — a correction to this report's earlier framing.** This report described
[40202100](https://issues.chromium.org/issues/40202100) as Chromium's *in-progress* ProMotion work. It is
not:

| issue | title | status (2026-07-19) |
|---|---|---|
| 40202100 | ☂️ New Mac: Support for ProMotion display ☂️ | **Fixed**, closed 2023-12-19 |
| 40062488 | Reliably VSync Aligned Rendering on macOS | **Fixed**, closed 2025-03-07 |
| 345275139 | Support CADisplayLink on macOS | open, Assigned, P2 |
| **330771325** | **VSync aligned frame presentation on Mac** | **open, Assigned, P2** — the flag's home |

*(The breadcrumb on each child page shows 40062488's title above 330771325 and 345275139, so 40062488
**appears** to be their parent. That is read off the page layout, not a stated relationship — and nothing
here depends on it.)*

The umbrella was closed by its reporter (#66, 2023-12-19) with: *"This bug was about ensuring we supported
ProMotion. If there are instances where Chrome does not correctly support it, **it's best to file them as
new bugs**."* — the maintainer explicitly inviting a report shaped like this one. **The live bug to
reference is 330771325**, not the umbrella.

**Issue 40820525 — read, and it is a *different* bug.** *("Promotion (high refresh rate) support is
broken"; snapshot 2026-07-19.)* Filed **2022-02-05** against **Chrome 98**, component **Blink > Scroll**,
**P3 / S4, status New**, no assignee, no linked code changes; 19 comments, nothing substantive since
2022-03-07. Reported symptom: *"Scrolling is less fluid than expected, and **locked to 60 Hz. UFO Test
reports a 60 Hz display.** Manually **resizing the window** makes scrolling smoother again."*

| | **40820525** (2022) | **this report** |
|---|---|---|
| what is at 60 Hz | **the display, as Chrome sees it** — `testufo.com` reports a 60 Hz panel | **nothing is**: rAF holds a measured ~120 Hz (README), and vsync source → BeginFrame → `SwapBuffers` are clean ~120 Hz in the trace (§1) |
| nature of the failure | **rate selection** — Chrome chose the wrong refresh interval | **commit phase** — correct rate, wrong moment relative to the ~1.5 ms latch deadline (§5a) |
| workaround | **resize the window** (temporarily) | untested for this bug — see next steps |
| status | stale since 2022, P3, wrong component | — |

**Its era is also gone.** The Chrome 98 regression was tracked as crbug **1274172** with a fix landing in
Canary (CL 3488608, see 40202100 #49/#51), and #16 confirms *"the canary version fix this bug"* (2022-03-07).
The `DelayBasedBeginFrameSource` machinery it lived in was then replaced by `CVDisplayLinkBeginFrameSource`
(2023-11), whose flag was deleted as permanently-on in 2025 (CL 6192599). **That machinery does not exist in
the reproduced build.**

*(One shared observation, quoted only to be discounted: commenters #12/#14 report that disabling hardware
acceleration fixes theirs, superficially echoing §4. It is weak evidence of anything — disabling HW
acceleration replaces the *entire* present path and would "fix" almost any GPU-present problem. **Not**
treated as corroboration.)*

**So it is neither a duplicate nor corroboration — but it is an actionable triage risk.** A triager
pattern-matching on "ProMotion broken on macOS" could fold this report into a **stale P3 sitting in a
component that owns none of the code involved**, where it has gone untouched for four years. The upstream
comment should pre-empt that in as many words: *this is not 40820525 — rAF and the vsync source are measured
at ~120 Hz here; the loss is in the present **commit phase**, below `SwapBuffers`.*

**And it hands us the component argument.** 40820525 stalled partly because **Blink > Scroll** owns none of
this code. The live workstream sits in **Internals > GPU > Internals** (40062488, 345275139), and the flag's
own bug 330771325 in **Internals**; the fix target here is
`gpu/ipc/service/image_transport_surface_overlay_mac.mm` + `components/viz/common/features.cc`. **Requesting
`Internals > GPU > Internals` on 534417001 puts it in front of the people who wrote
`kVSyncAlignedPresentation`** — a concrete answer to the standing "propose a component to speed up triage"
item.

**§5a was stated by a Chromium graphics lead in 2021.** ccameron, 40202100 #12 (2021-10-20):

> "with CADisplayLink we would [have] the ability to say 'draw this at the next vsync' whereas
> CVDisplayLink gives us the ability to say 'give me a callback at the next vsync'. **Our compositor ends
> up hoping that it commits its CATransaction at the right time** (the commit happens when the frame's
> drawing is complete — we only have the ability to say when it starts)."

That is §5a's free-floating commit, described five years before this report and independently of it.

**The ~1.5 ms latch deadline has a provenance.** CL [5345141](https://crrev.com/c/5345141) (2024-03):
*"From the experiment I did in my local machine … 1. Subtract 1.5 ms from the latch deadline. 2. Add one
frame interval to PresentationFeedback.timestamp. Frames committed before the latch deadline is displayed
at the next display time which is one frame interval further."* And CL
[5051449](https://crrev.com/c/5051449) (2024-01) defines `feedback.latch_timestamp` as *"the time when
CATransaction Commits and CoreAnimation latches the frame."*

**The per-arm experiment numbers are not in the tracker.** 330771325's 16 comments are **all bot-posted CL
notifications — zero human discussion**. So §7e's "the `Animation` arm's standalone result is unknown" is
now **checked, not merely unread**, and §7a-ii's two readings of the 2026 default stay unresolved for the
same reason.

#### The one thing the trackers do change: what the guardrail metrics are made of

**On macOS, Chrome does not know when a frame was actually presented — it models it.** jonross, 40062488
#55 (2023-11-17):

> "Historically macOS never provided the timestamp of when the GPU actually presented a frame. So, while
> most platforms have that, on macOS we just used `Now` as the best-effort estimate … **A lot of metrics
> are built on-top of this PresentationFeedback concept: INP, FCP, EventLatency, CompositorLatency**, etc.
> The feature `kDelayOnFramePresent` changes when `PopulateCALayerParameters` is called … **So shifting
> this would directly shift the metrics, however that does not mean that the actual presentation for the
> user changed.** … To help confirm if the regression seen is **solely due to a metrics issue**, we could
> run a trial where we do not shift the call … there is precedent for a metrics 'regression' where we fix
> the accuracy."

And the feature was switched off for exactly that reason — CL [5050323](https://crrev.com/c/5050323)
(2023-11): *"the PresentationFeedback timestamps will be restored to the original one … Now
CompositorLatency.TotalLatency and WebVitals.InteractionToNextPaint2 should not be regressed. … **We will
try to enable kDelayOnFramePresent and communicate with the metrics team later on the PresentationFeedback
timestamp change.**"*

**Chronology — the over-read this subsection exists to prevent.** It is **wrong** to conclude "the 2025
verdict was a metrics artifact":

- jonross's concern is **2023-11**, when `feedback.timestamp` was `Now()` (render completion).
- The timestamp was then **redefined** to the DisplayLink `display_time` (CL 5051449, 2024-01) and retuned
  (CL 5345141, 2024-03).
- The three-arm experiment and its *"big regressions on guardrail metrics"* verdict are **2025 — after
  those revisions.**
- jonross only **proposed** the no-shift control trial. Whether it ran, and what it showed, is in no public
  source read here.

What survives is narrower and still substantial: even after the revisions, the macOS presentation timestamp
is a **prediction of when the frame will be shown** (`GetDisplaytime`), not a measurement of when it was —
precisely the caveat this report already attaches to its own `Jank3` reading (§6).

**And it cuts both ways — that is the point, not a hedge.** If the presentation timestamp is a model, then
on macOS **both** sides of the trade-off are model-mediated:

| side of the trade-off | measured by | estimate-independent ground truth? |
|---|---|---|
| smoothness **gain** (`Jank3`, `PercentDroppedFrames`) | Chrome's present model | **yes — this report's external 240 fps camera** (§5f: 60 % → 87 % hold=2) |
| latency **cost** (INP, EventLatency, CompositorLatency) | the same present model | **none produced publicly by anyone** |

This report's own `Jank3` **11.5 → 0.1** (§6) is **the same kind of number** as the guardrail regressions,
and is already quoted with that caveat. What is asymmetric is not the metrics — it is the **ground truth**:
an external camera exists for the smoothness side and settles it; nothing equivalent has been produced for
the latency side.

**What is *not* being claimed — and why §7f still stands.** The added latency is **real by construction**:
deferring an already-ready commit to the next DisplayLink callback costs up to one refresh, and no
measurement dispute changes that. So §7f's reading — that gating on interaction is *coherent* engineering,
because it buys smoothness precisely where INP cannot charge for it — is unaffected. What is
model-mediated is only the **measured magnitude** of that cost in INP / guardrail units, and therefore
whether it outweighs a smoothness gain that *is* now camera-confirmed. This section argues for measuring
the cost better, **not** for arguing it away.

**Sharpened ask** (refining §7f):

> Before the general vsync-aligned path is judged again, the **latency** side deserves the same
> estimate-independent measurement the smoothness side now has. A 240 fps camera measures input-to-photon
> directly and does not depend on `PresentationFeedback` at all — the same method this report used, pointed
> at the other half of the trade-off. Failing that, the **no-shift control trial jonross proposed in 2023**
> would separate model shift from real change.

**One precedent, because it is the same judgement call this report is making.** magchen, 40062488 #28
(2023-07-26), on the very CL that introduced the vsync-aligned commit:

> "CL 4710566 that presents frames during VSync callback **actually regresses a bit more**, and there are
> still drop frames. **Anyway, I would think this CL is needed. It reduces the flickering in**
> https://www.vsynctester.com/"

The author kept a change the metrics disliked because it visibly fixed something. And etienne (#24,
2023-07-25) on what the regression looked like: *"There's no clear difference in Vsync precision, **except
maybe its phase**."* — phase being exactly what §5f identifies and what the flag corrects.

---

## What is resolved / still open

**Resolved**
- Not Chrome's compositor scheduling or Viz frame-interval selection *(trace-measured)*.
- Below `SwapBuffers`, in the macOS present path *(trace-measured + inferred)*.
- Trigger = per-frame main-thread work (contention *or* commit); **not** compositor-layer concurrency
  *(visually observed)*.
- **Rate-dependent**: nearly smooth at 60 Hz (~8–16 % held) vs ~52 % held at 120 Hz; card B's present
  pinned near ~55 Hz *(camera-measured, self-validating clip)*.
- **macOS-specific**: Firefox does not reproduce (README); **Windows @ 144 Hz is smooth** *(visual)* —
  so it is the macOS present path, not high rate per se.
- **Metal present path specifically** *(visual, flag sweep §4)*: reproduces only on the default
  ANGLE-Metal present; software compositing and ANGLE-OpenGL both remove it, and no Metal-internal knob
  (gpu-vsync, Skia raster, remote-CoreAnimation) toggles it off. → fix target = the Metal CALayer
  commit / CoreAnimation present.
- **Trigger + commit machinery localised in code** *(source-readable, §5)*: the default macOS present
  commits the CALayer tree **synchronously and non-vsync-aligned** (`kVSyncAlignedPresentation` off by
  default), so the commit phase free-floats; a documented **~1.5 ms latch deadline** (`GetDisplaytime`)
  slips any late commit to the next refresh. The trigger (§5c) is the Viz begin-frame deadline pushing
  `DrawAndSwap` late under a main-thread producer (cross-platform).
- **Localised below `SwapBuffers` a second, independent way** *(source-readable + visually observed, §5d)*:
  `--enable-features=VSyncAlignedPresentation` acts *entirely* in the present/commit path below `SwapBuffers`,
  and it removes the visible jerk — so the localisation below `SwapBuffers` **no longer rests on the
  possibly-suppressed Perfetto trace** (§1). This much holds regardless of the fix-vs-suppression question.
- **Pin = the commit's phase vs the display refresh — source-predicted AND camera-confirmed for card B/A+B**
  *(§5d source; §5f camera)*: the flag changes only *when* the identical commit issues. The fixed-camera
  default-vs-flag pair (`IMG_3848`) shows card B in A+B going from an in-clip-validated **bug-level 60 %** to
  **smooth-control 87 %** hold=2 (hold=2-dominant → true ~120 Hz). So the free-floating commit crossing the
  ~1.5 ms latch deadline is the pin, and commit-phase alone cures it — a **user workaround** and the
  **upstream fix target**.
- **Why the fix is off by default — ANSWERED from primary sources** *(source-readable, §7)*. The general
  vsync-aligned present was implemented in 2023 ("in order for CoreAnimation to latch frames in a
  consistent timing"), Finch-tested on Beta in 2025 with three targeting arms (`AllFrames` / `Animation` /
  `Interaction`), and **rejected in favour of the scroll-only arm** because broad alignment "**still has big
  regressions on guardrail metrics, although it improves a lot on smoothness metrics**" (CL 6482387); INP
  is the named cost (CL 6143459). `kVSyncAlignedPresentation` (2026-03, CL 7690172) is a **re-introduction**
  of that general behaviour which has had **no experiment of its own yet** — so the flag's own off-state is
  "new feature, not yet run" as much as "parked after the 2025 verdict" (§7a-ii; not disambiguated by any
  public CL). Either way the gap is a **deliberate, measured trade-off**, not an oversight — which is why the
  upstream ask must be a *scoped re-evaluation*, not "flip the flag" (§7f).
- **Chrome's own shipped gate reaches the fix — but only during a scroll** *(source-readable + visually
  observed, §7g)*. `kVSyncAlignedPresentationForScrolling` is `ENABLED_BY_DEFAULT` and sets the *same*
  `delay_presentation_until_next_vsync` boolean when `data.is_handling_interaction`
  (= active scroll or touch sequence, `LayerTreeHostImpl::IsHandlingInteraction()`). Predicted from that
  line, then confirmed by eye on **stock Chrome with no flags**: card B is **smooth while a scroll gesture
  is in progress**. So the same animation presents at a true ~120 Hz during a scroll and falls back to the
  ~55 Hz pin the moment it ends. **Now measured on the passive `Jank3` counter, not just seen:** in one
  monitoring window, scrolling reads **0.11** (94.4 % of sequences at zero) and not-scrolling **12.44**
  (0 % at zero) — landing on the *published* flag (0.1 / 90.6 %) and default (11.5 / 1.3 %) references
  respectively, with completely disjoint distributions. Card A behaves identically.

**Open**
- **Fix vs suppressor #4 — RESOLVED for A+B (card B *and* card A)** *(§5f)*. The camera pair confirms the fix
  for **card B in A+B** (default 60 % → flag 87 % hold=2, self-validated in-clip against two-transf 86 %); the
  full-travel clip `IMG_3852` extends it to **card A** (flag-only 91 % = card B 91 %) — the flag fixes the
  layout animation too; the earlier card-A residual was crop-clipping. **Still open:** the flag arms for
  **busywork and color** were left under-sampled/absent by short cycles (busywork-flag confirmed at 90 % in
  `IMG_3852` though; only color under flag stays thin) — minor, a completed cycle would close them.
- **GL doesn't reproduce the A+B coupling — now MEASURED (`IMG_3855`), and the *why* is inside ANGLE**
  *(camera-measured + source-readable)*. A `chrome://gpu`-verified Metal-vs-GL pair (cycle 1
  `ANGLE_METAL`, cycle 2 `ANGLE_OPENGL` — both confirmed on-screen), same fixed camera, DevTools closed, no
  flag:

  | mode, card B | Metal | GL |
  |---|---:|---:|
  | **A+B** | **75 %** (23 % held) | **93 %** (4 % held) |
  | busywork | 77 % | 90 % |

  So **GL avoids the compositor-inheritance coupling** (card B stays smooth in A+B *and* busywork),
  confirming it is **Metal-specific** (Metal-bug self-validated in-cycle by `color/Metal` = 58 % held-heavy
  and the smooth control `two-transf/Metal` = 91 %). **Correction to an earlier over-read:** the §4 by-eye
  "GL single-card judder" was **not** re-confirmed here — GL `B-only` fell in a slow-mo *ramp* (medstep
  13.8 px, sub-240 fps → rejected), and GL `A-only` card A = 58 % is **indistinguishable from the normal
  layout irregularity** (README A-alone 59 %), *not* a GL quirk. So the measured story is simply: **GL
  removes the coupling.** Whether GL is a clean general workaround is unresolved by this clip (the §4 judder
  stays by-eye, and §4's software A+B ≈ 23 % held shows software isn't clean either) — which is why the
  Metal-side `VSyncAlignedPresentation` flag remains the fix, not a backend switch. The
  **presenter is the same for both backends** (`CreatePresenter()`, no ANGLE branch), so the GL-vs-Metal
  divergence is **inside ANGLE's rendering backend** (deep, separate repo) — not worth reading; the *what*
  is now measured, the *why* is ANGLE-internal. *(Caveat: Metal A+B here was a single clean run at 75 %,
  milder than `IMG_3848`'s 60 %; the stronger in-clip Metal-bug evidence is `color` = 58 %.)*
- **Why DevTools / recorder / CDP suppress — source dive done: no single Chrome realignment; it operates
  through macOS presentation behaviour** *(source-readable + inferred)*. **In the inspected present-path
  files, found no capture-specific branch:** no capture/CDP/screencast conditional in the macOS
  present/commit path; no cadence change in `DisplayScheduler`; the resize → `CATransactionV2 createFencePort`
  path is **one-time** (reset after one commit, feature-gated) so static docking can't suppress through it;
  the only capture hook is `display.cc` `should_draw = have_copy_requests || …` — which forces drawing
  *undamaged* frames under a `CopyOutputRequest` (**throughput, not commit phase**), and is capture-specific
  (the Elements panel issues no CopyOutputRequest, yet "any panel" suppresses). *(Absence of evidence in the
  files read, not proof no branch exists — a whole-tree grep needs the checkout.)* **Strong constraint:** a
  **ScreenCaptureKit recorder touches zero Chrome code** yet suppresses — which **strongly suggests the
  suppressor operates through macOS display/presentation behaviour** (WindowServer / CoreAnimation /
  DisplayLink cadence) rather than a DevTools-specific Chromium code path. Note it need not be *beneath*
  Chrome: macOS may simply feed Chromium's present **different inputs** (e.g. a different DisplayLink callback
  cadence/phase), changing behaviour with the code unchanged. → The suppressors are **not** "accidental copies
  of the flag's code path"; they **share the *locus*, not the implementation** — the **macOS CALayer commit →
  CoreAnimation present handoff**, where the flag and the bug also live. Direct readable test (needs a trace):
  whether DevTools shifts `CommitPresentedFrameToCA`'s `now_to_display` past the ~1.5 ms latch (§5e-1),
  standing tracing caveat.
- **Backpressure poll: factor or not** — expected non-factor (§5c). One passive read settles it:
  `Gpu.Mac.BackpressureUs` ≈ 0 on the buggy default run ⇒ excluded.
- **Variable vs fixed refresh**: the macOS 120 Hz tested is ProMotion (variable); the 60 Hz was stable.
  A *fixed* 120 Hz macOS panel (non-ProMotion) is untested, so "variable-refresh" is not isolated from
  "high rate" on macOS.
- **Does Perfetto tracing suppress?** Not cleanly resolved (camera for the trace clip was too noisy).

**Next candidate steps**
- **Report upstream now — the core claim is camera-confirmed.** File: repro + camera cadence (README) +
  source localisation to the non-vsync-aligned macOS CALayer commit (§5) + the **`IMG_3848` default-vs-flag
  pair confirming `--enable-features=VSyncAlignedPresentation` restores card B's A+B present from 60 % to
  87 % hold=2** (§5f). Frame the flag as **both the confirmation and the shipping mitigation/fix target**, and
  flag the DevTools/recorder/CDP suppression up front — it is why this went unreported.
  - **Prior-art context (strengthens the report).** The fix flag is **not a bespoke workaround** — it is an
    existing, implemented, **disabled-by-default** feature (`kVSyncAlignedPresentation`, `IsVSyncAligned()`)
    that defers the CALayer commit to the display-link callback. It sits in an **active, defaults-off macOS
    high-refresh/ProMotion present overhaul**: the CADisplayLink migration ("Support CADisplayLink on Mac",
    commit `c920f54`, disabled-by-default, macOS 14+, 2024 — its own bug is **345275139**, open). **Corrected
    in §7h:** the right bug to cite is **330771325** *(VSync aligned frame presentation on Mac — open,
    Assigned, P2; the flag's home)*, **not** the ProMotion umbrella 40202100, which was **closed as Fixed on
    2023-12-19**, nor 40062488, closed 2025-03-07. So the bug is best framed as *"the default (non-vsync-aligned, CVDisplayLink) present
    path drops compositor-animation cadence on ProMotion under concurrent per-frame main-thread work; the
    in-development vsync-aligned path already fixes it — here is a minimal camera-measured repro, the exact
    trigger, and the exact toggle."* Note that the scrolling-only variant `kVSyncAlignedPresentationForScrolling`
    is **on by default** — the team already ships vsync-aligned present for the scroll case, just not generally.
    *(Verified public: CADisplayLink feature status + umbrella issue.* **Now also verified — see §7:** *the flag's
    own CL is [7690172](https://crrev.com/c/7690172) (2026-03-23, bug 330771325), and the whole Finch history of
    the feature is public. The earlier "not readable, tracker requires sign-in" caveat applied to the **issue
    tracker**; the **Gerrit CLs are public** and carry the rationale.)* Separately,
    input issue **40375001** ("vsync-aligned input") was checked and is **unrelated** — Internals→Input→Touch
    pipeline (MotionEventBuffer, input resampling), *before* the renderer; zero presentation-path terms. Only
    the name "vsync-aligned" overlaps.
  - **Ask precisely (§7f).** Do **not** request "enable `kVSyncAlignedPresentation`" — that is the `AllFrames`
    behaviour already measured and rejected. Do **not** propose "gate on `is_handling_animation`" without
    naming the `kRAF` trap (that predicate is ~always true on rAF pages, so it degenerates to `AllFrames`).
    Ask instead for a **display-refresh-rate-segmented re-evaluation** of the general path — the loss is
    severe at 120 Hz and near-absent at 60 Hz (§3), so a Mac-wide arm dilutes the benefit while the latency
    cost applies uniformly.
- **Two actions that came out of reading 40820525 (§7h), both cheap:**
  1. **Ask for component `Internals > GPU > Internals` on 534417001.** It is where the live workstream sits
     (40062488, 345275139) and where the flag's own bug 330771325 lives; 40820525 has sat in **Blink >
     Scroll** — which owns none of this code — untouched since 2022. Concrete answer to the standing
     "propose a component to speed triage" item.
  2. **State the non-duplication up front in the upstream comment.** *"Not 40820525: rAF and the vsync
     source are measured at ~120 Hz here; the loss is present-**phase**, below `SwapBuffers`."* Without it,
     a triager pattern-matching on "ProMotion broken on macOS" may dedupe this into that stale P3.
- **Cheap discriminator, ~1 minute, not yet run: does *resizing the window* suppress this bug?** It is
  40820525's workaround, so a **negative** result is a clean extra separation from it; a **positive** one
  would be a new suppressor (#5) and would need explaining. Either outcome is worth having before filing the
  comparison. *(The source dive on the DevTools-suppression question already found the resize →
  `CATransactionV2 createFencePort` path is one-time and feature-gated, so a positive result would* not
  *have an obvious readable mechanism.)*
- **§7g is done** — hardened on the passive `Jank3` counter (scrolling 0.11 / 94.4 % at zero vs
  not-scrolling 12.44 / 0 %, disjoint distributions, both landing on published references). Two optional
  tighteners remain, neither load-bearing: a **reversed-order repeat** (unscrolled first) to exclude drift,
  and a **camera clip structured like §3** — jerky / scroll / jerky in one continuous take, which would be
  self-validating and would give the absolute rate rather than the modelled one.
- **Optional: complete cycle 2** (busywork + color under the flag, a few clean fires each) to extend the
  confirmation beyond A+B to the other bug conditions. Not required for the core claim.
- **Passive histogram** `Gpu.Mac.BackpressureUs` on the buggy default run — excludes the backpressure poll.
- `rAF`-synced vs constant background main-thread load — sharpens "busy per frame" vs "busy near vsync".

---

## Method caveats

- The trace and discriminator clips (`IMG_3841`, `IMG_3842`) were **handheld with soft focus**. Camera
  shake in the card region was validated as negligible, but **exposure straddling + soft focus** made
  per-frame sub-pixel cadence tracking unreliable (odd-hold fraction far above the README's clean-run
  standard). No cadence **number** comes from those two clips.
- The refresh-rate clip (`IMG_3844`, §3) was also handheld, but its numbers are trusted because it is
  **self-validating** — its 120 Hz swing reproduces the known ~46 % bug — and the 60 Hz step (~9 px) is
  large enough to track cleanly.
- The reliable signals in this phase are therefore **(a)** the trace (Chrome-internal, microsecond
  timestamps, immune to camera focus), **(b)** direct **visual** observation of jerkiness (gross and
  eye-visible — the same basis the README uses for the Firefox and Windows contrasts), and **(c)** the
  self-validating §3 rate comparison.
- The clean, sampling-gated **absolute** percentages remain those in the top-level `README.md` (tripod,
  sharp); §3's figures are quoted as a *within-clip, method-validated comparison*, not new headline rates.
