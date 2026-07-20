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

## `extracts/` — the condition series (`diagnostic-findings.md` §8 and the display series)

Eleven further captures, one condition each. These are **extracts, not full dumps** — see the note below.

| file | condition | what it supports |
|---|---|---|
| `commit-mode-bimodal.txt` | mixed scrolled/unscrolled session | `Compositing.Display.SwapStartToSwapEnd` is **bimodal** — immediate commit ~0.1–0.6 ms vs deferred peaking at 7277 µs against an 8.33 ms interval; `GPU.Presentation.FrameHandlesAnimationOrInteraction` puts **48.3 %** of frames on the interaction gate against **49 %** in the deferred modes |
| `suppressor-baseline.txt` | nothing open, jerky | §8 baseline |
| `suppressor-devtools-window-node-selected.txt` | DevTools undocked, node selected — **jerky** | §8a |
| `suppressor-devtools-smooth.txt` | DevTools, node selected, page reloaded — **visually smooth** | §8b: `Jank3` **12.4**, 0 % of sequences at zero, commit 99.3 % immediate — identical to the jerky runs |
| `suppressor-second-window-static.txt` | static second window — jerky | §8a |
| `suppressor-obs-recording.txt` | screen recorder running | §8a |
| `suppressor-second-window-chrome-scrolled.txt` | scrolling a second **Chrome** window | §8b |
| `suppressor-second-window-finder-scrolled.txt` | scrolling a **Finder** window | §8b — same numbers as the Chrome case, which proves the interaction frames are the **repro's own** Display: Finder is a different process and writes no Chrome UMA |
| `display-external-120hz.txt` | external LG C2, fixed 120 Hz — **jerky** | the bug is not ProMotion-specific |
| `display-external-120hz-vsyncaligned-flag.txt` | same display + the flag — **smooth** | `Jank3` **0.00**, every sequence jank-free (n = 9) |
| `display-external-60hz-REJECTED-cumulative.txt` | same display set to 60 Hz | **REJECTED as a 60 Hz measurement** — monitoring was not reset, so `Viz.ExternalBeginFrameSource.Interval` reads 97.3 % at 8 ms and only 1.8 % at 16 ms: ~98 % of it is the preceding 120 Hz period. Published because this report publishes rejected runs; the 60 Hz result is by-eye only |

**Why these are extracts, and what that costs.** A full `chrome://histograms` capture on a real profile is
~3 MB and holds ~4700 counters, most unrelated and some personal — stored-autofill entity counts including
document types, clipboard word counts, download and navigation statistics. Those are not publishable in a
bug report. `analysis/extract_histograms.py` keeps only `Graphics.Smoothness.*`, `Compositing.Display.*`,
`GPU.Presentation.*`, `Gpu.Mac.*` and `Viz.*`, copying each matching block **verbatim** — so every number
here is byte-identical to what Chrome printed, and 79 counter names survive out of ~4700.

**The cost is stated rather than hidden:** §8b's result — *0 of 3772 counters separates a confirmed-smooth
run from a jerky one* — is a whole-dump survey and **cannot be repeated from these extracts**. The full
captures are not published for the reasons above. `analysis/compare_histograms.py scan` is published so
anyone can repeat the survey on their own captures, which take ~2 minutes to produce.

**Reproduce any row:** `chrome://histograms` → *Switch to Monitoring Mode* → set up the condition → run A+B
on Loop ~30 s → *Refresh* → select all, save → `python3 analysis/extract_histograms.py DUMP.txt`. Reset
monitoring between conditions; the 60 Hz row above is what happens when you do not.

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
