# Telemetry evidence — Chrome's own smoothness histograms

Camera-free corroboration of the bug and the fix (see `diagnostic-findings.md` §6). These are **verbatim
`chrome://histograms` dumps**. Being a *passive counter read* (not a screen capture), this channel does
**not** suppress the bug — unlike DevTools / a screen recorder / CDP, which the top-level `README.md` shows
each alter what they observe.

**Environment:** Chrome 150.0.7871.125, macOS 15.7.3 (M4 Pro), 120 Hz ProMotion. **Repro:** `diagnostic.html`,
mode **A + B**, **DevTools closed**.

| file | condition | key figure |
|---|---|---|
| `graphics-smoothness.default.txt` | default (no flag) — the whole `Graphics.Smoothness` family | **`PercentDroppedFrames3` = 0** while `Jank3` is high (compositor/card B 10.5, main-thread/card A 13.7) and `Checkerboarding = 0` → the bug is **jank, not dropped frames** |
| `jank3-compositor-animation.default.txt` | default (no flag) | `Jank3.CompositorThread.CompositorAnimation` mean = **11.5** (long tail) |
| `jank3-compositor-animation.vsyncaligned-flag.txt` | `--enable-features=VSyncAlignedPresentation` | same metric mean = **0.1** (90.6 % of sequences at zero) → the flag fixes it |
| `jank3-scrolltest.phase1-scrolled.txt` | **stock Chrome, no flag**, scrolling throughout | same metric mean = **0.11**, 94.4 % at zero — **the flag's number, without the flag** |
| `jank3-scrolltest.cumulative-after-unscrolled.txt` | same monitoring window, after a second phase with **no** scrolling | cumulative; `dump2 − dump1` = the unscrolled arm: mean **12.44**, **0 %** at zero |
| `jank3-resizetest.txt` | stock Chrome, no flag, **after resizing the window** (40820525's workaround) | mean **12.96**, **0 %** at zero — squarely the bug population: **resizing does not suppress this bug** |

## The scroll test (`diagnostic-findings.md` §7g)

Chrome ships `kVSyncAlignedPresentationForScrolling` **enabled by default**, gated on
`data.is_handling_interaction` — true only while a scroll or touch sequence is active. So **scrolling should
hand the animation the same vsync-aligned commit the flag forces permanently**. Predicted from source, then
measured:

| `Jank3.CompositorThread.CompositorAnimation` (card B) | n | mean | at zero |
|---|---:|---:|---:|
| **scrolling** — stock Chrome, no flag | 18 | **0.11** | **94.4 %** |
| **not scrolling** — same launch, derived by subtraction | 16 | **12.44** | **0 %** |
| *reference: default* | 75 | *11.5* | *1.3 %* |
| *reference: `VSyncAlignedPresentation` flag* | 32 | *0.1* | *90.6 %* |

Scrolling lands on the **flag's** number; not scrolling lands on the **default's**. The distributions are
**completely disjoint** (scrolled ≤ 2, unscrolled ≥ 8).

**What this does and does not establish.** `CommitPresentedFrameToCA()` evaluates the *same*
`GetDisplaytime(Now())` on both the immediate and the deferred path — there is no branch, so the timestamp
source does not shift between the arms. But it is evaluated **at commit time**, so aligning the commit makes
its input near-constant by construction and the *modelled* presentation times regular almost tautologically.
So this pair shows that **scrolling engages the aligned commit path**; what shows that path is a *real*
~120 Hz fix is the **external camera** (`diagnostic-findings.md` §5f) — the same backing the flag's own
`Jank3` numbers above rely on.

**Not a second witness:** `CompositorAnimation`, `NativePropertyAnimation` and `MainThreadAnimation` are
byte-identical in these dumps because in mode A+B all three trackers span the same frames. Card A's
independent witness is the camera, not this histogram.

**Why the two files are a pair, not two runs.** Both phases share **one monitoring window**, so the second
dump is *cumulative* and the unscrolled arm is `dump2 − dump1`, bucket by bucket. That subtraction is exact,
not approximate: every bucket of dump 1 appears in dump 2 with a count ≥ its own; the residual sits in
buckets 8–18 with nothing below 8, so no sample is ambiguously assigned; and
`Jank3.MainThread.WheelScroll` is **identical in both dumps** (n = 9, same buckets), proving no further
scrolling happened in phase 2. Sharing one launch also makes it a **paired within-session** measurement —
launch-to-launch variance cancels.

## All five conditions on one axis

| condition | n | mean `Jank3` | at zero | population |
|---|---:|---:|---:|---|
| default | 75 | 11.45 | 1.3 % | **bug** |
| unscrolled arm (derived) | 16 | 12.44 | 0 % | **bug** |
| after a window resize | 81 | 12.96 | 0 % | **bug** |
| **scrolling** | 18 | **0.11** | **94.4 %** | **fixed** |
| **`VSyncAlignedPresentation`** | 32 | **0.09** | **90.6 %** | **fixed** |

Two clean populations, no intermediate cases. The bug baseline is replicated **three** times (11.45 / 12.44 /
12.96, all ≤ 1.3 % at zero), which is what makes the two fixed readings unambiguous rather than single-run.
The resize row is a deliberate **negative control**: it is issue 40820525's workaround, and it does nothing
here — one more separation between that bug and this one. Its protocol was **near-continuous resizing
throughout the run** (several drags per 600 ms animation cycle; not counted), which rules out a fix that
appears on resize and then decays — and is stricter than 40820525 needs, where one resize held for minutes.

**Caveat:** this does not isolate *which* branch of `Present()` aligned the commit — the predicted
`IsVSyncAlignedForScrolling() && is_handling_interaction`, or the separate `NumPendingSwaps() > 1` clause
that is also live by default. Both are the same vsync-aligned commit, so the mechanism holds; the predicate
does not. Order was also not counterbalanced (scrolled first). See §7g for the full caveat list.

**Headline:** for the compositor `transform` animation (card B), `Jank3` collapses **11.5 → 0.1** with the
flag, while `PercentDroppedFrames` stays **0** throughout — Chrome's own telemetry confirms both the bug and
the fix, and confirms the report's "*not* dropped frames" wording. This is *why* the issue hid: standard
dropped-frame monitoring (and the DevTools FPS meter) watches `PercentDroppedFrames`, which reads 0.

**Reproduce in ~2 minutes, no camera:** `chrome://histograms` → *Switch to Monitoring Mode* → run A+B
(`Loop`, DevTools closed) ~20 s → *Refresh* → read `Graphics.Smoothness.Jank3.CompositorThread.CompositorAnimation`
(≈ 11). Relaunch `--user-data-dir=/tmp/x --enable-features=VSyncAlignedPresentation`, repeat (≈ 0).

**Caveat (evidence standard):** `Jank3` is computed from Chrome's present *estimate* (`GetDisplaytime`, not
the real WindowServer present), so it **under-reports magnitude** vs the external camera (~50 % held) — but
the default→flag **direction** is unambiguous and agrees with the camera. The absolute ground truth remains
the camera (top-level `README.md`).
