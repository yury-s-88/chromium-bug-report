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
