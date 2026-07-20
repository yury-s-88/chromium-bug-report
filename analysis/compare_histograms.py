#!/usr/bin/env python3
"""Compare chrome://histograms captures across conditions.

Two modes, both used to produce the numbers in `diagnostic-findings.md` §8:

  summary   the presentation-path counters side by side, one column per run:
            the jank mean and its at-zero fraction, and — the useful one —
            `Compositing.Display.SwapStartToSwapEnd` split into its two modes.
            That counter measures swap start -> CALayer commit, so an "immediate"
            share and a "deferred ~1 refresh" share read the two branches of
            ImageTransportSurfaceOverlayMacEGL::Present() directly.

  scan      the survey: given two groups of runs, report every counter whose value
            in one group falls entirely outside the other's range. This is what
            produced "0 of 3772 counters separates a confirmed-smooth run from a
            jerky one" — and the reason that claim needs FULL captures, not the
            extracts in telemetry/ (see extract_histograms.py).

A caution the survey earns: nearly every apparent separator it finds is monotone in
run *length*, because the runs are not equal-duration and many counters converge as
samples accumulate. Always read the per-run values, not just the separation ratio;
if the longest run of one group sits on top of the other group, that is duration,
not signal.

USAGE
-----
    python3 analysis/compare_histograms.py summary LABEL=FILE [LABEL=FILE ...]
    python3 analysis/compare_histograms.py scan --a LABEL=FILE [...] --b LABEL=FILE [...]
"""

import argparse
import re

HDR = re.compile(r"^- Histogram: (\S+) recorded (\d+) samples, mean = ([\d.]+)")
BKT = re.compile(r"^(\d+)\s+\S*\s*\((\d+) = ")

JANK = "Graphics.Smoothness.Jank3.CompositorThread.CompositorAnimation"
SWAP = "Compositing.Display.SwapStartToSwapEnd"
GATE = "GPU.Presentation.FrameHandlesAnimationOrInteraction"
GATE_NAMES = ["kNone", "kInteractionOnly", "kAnimationOnly", "kAnimationAndInteraction"]


def parse(path):
    """{name: {'n': int, 'mean': float, 'b': {bucket_start: count}}} from a dump."""
    out, cur = {}, None
    for line in open(path, errors="replace"):
        m = HDR.match(line)
        if m:
            cur = {"n": int(m.group(2)), "mean": float(m.group(3)), "b": {}}
            out[m.group(1)] = cur
            continue
        if cur is None:
            continue
        b = BKT.match(line)
        if b:
            cur["b"][int(b.group(1))] = int(b.group(2))
        elif not line.strip() and cur["b"]:
            cur = None
    return out


def share(h, lo, hi):
    """Fraction of samples in [lo, hi). Bucket starts, so this is exact at edges."""
    if not h:
        return None
    tot = sum(h["b"].values())
    return sum(v for k, v in h["b"].items() if lo <= k < hi) / tot if tot else None


def summary(runs):
    labels = [l for l, _ in runs]
    data = [parse(p) for _, p in runs]
    w = max(14, max(len(l) for l in labels) + 2)

    def row(title, fn, fmt="{}"):
        cells = []
        for d in data:
            v = fn(d)
            cells.append("—" if v is None else fmt.format(v))
        print(f"{title:<46}" + "".join(f"{c:>{w}}" for c in cells))

    print(f"{'':<46}" + "".join(f"{l:>{w}}" for l in labels))
    print("-" * (46 + w * len(labels)))
    row("Jank3 CompositorAnimation  mean", lambda d: d.get(JANK, {}).get("mean"), "{:.2f}")
    row("  n", lambda d: d.get(JANK, {}).get("n"))
    row("  sequences at zero",
        lambda d: (share(d.get(JANK), 0, 1) or 0) * 100 if JANK in d else None, "{:.1f}%")
    print()
    row("SwapStartToSwapEnd  n", lambda d: d.get(SWAP, {}).get("n"))
    row("  IMMEDIATE commit (<1 ms)",
        lambda d: (share(d.get(SWAP), 0, 1000) or 0) * 100 if SWAP in d else None, "{:.1f}%")
    row("  DEFERRED ~1 refresh (4-11 ms)",
        lambda d: (share(d.get(SWAP), 4000, 11000) or 0) * 100 if SWAP in d else None, "{:.1f}%")
    row("  DEFERRED >=2 refreshes (>=11 ms)",
        lambda d: (share(d.get(SWAP), 11000, 10 ** 9) or 0) * 100 if SWAP in d else None, "{:.1f}%")
    print()
    for i, nm in enumerate(GATE_NAMES):
        row(f"FrameHandles {nm}({i})",
            (lambda idx: lambda d: (share(d.get(GATE), idx, idx + 1) or 0) * 100
             if GATE in d else None)(i), "{:.1f}%")
    print()
    row("Gpu.Mac.BackpressureUs mean us",
        lambda d: d.get("Gpu.Mac.BackpressureUs", {}).get("mean"), "{:.1f}")
    row("Viz.ExternalBeginFrameSource.Interval mean ms",
        lambda d: d.get("Viz.ExternalBeginFrameSource.Interval", {}).get("mean"), "{:.1f}")


def scan(group_a, group_b, min_n):
    A = [(l, parse(p)) for l, p in group_a]
    B = [(l, parse(p)) for l, p in group_b]
    common = set(A[0][1])
    for _, d in A[1:] + B:
        common &= set(d)

    hits = []
    for h in sorted(common):
        ns = [d[h]["n"] for _, d in A + B]
        if min(ns) < min_n:
            continue
        a = [d[h]["mean"] for _, d in A]
        b = [d[h]["mean"] for _, d in B]
        if max(a) < min(b) and min(b) > 0:
            hits.append((min(b) / max(max(a), 1e-9), h, a, b, "A<B"))
        elif max(b) < min(a) and min(a) > 0:
            hits.append((min(a) / max(max(b), 1e-9), h, a, b, "A>B"))
    hits.sort(reverse=True)

    print(f"{len(common)} counters common to all runs with n>={min_n}; "
          f"{len(hits)} separate the groups")
    print(f"A = {[l for l, _ in A]}\nB = {[l for l, _ in B]}\n")
    for sep, h, a, b, d in hits:
        print(f"{sep:6.2f}x {d}  {h[:60]:<60} "
              f"A={[round(x, 2) for x in a]}  B={[round(x, 2) for x in b]}")
    if hits:
        print("\nRead the per-run values before believing any of these: a separator that is "
              "monotone in\nrun length is duration, not signal.")


def kv(s):
    label, _, path = s.partition("=")
    if not path:
        raise argparse.ArgumentTypeError(f"expected LABEL=FILE, got {s!r}")
    return label, path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("summary", help="presentation-path counters, side by side")
    s.add_argument("runs", nargs="+", type=kv, metavar="LABEL=FILE")

    c = sub.add_parser("scan", help="find counters separating two groups of runs")
    c.add_argument("--a", nargs="+", type=kv, required=True, metavar="LABEL=FILE")
    c.add_argument("--b", nargs="+", type=kv, required=True, metavar="LABEL=FILE")
    c.add_argument("--min-n", type=int, default=200,
                   help="ignore counters with fewer samples than this in any run "
                        "(default 200; low-n counters are subsampled and noisy)")

    args = ap.parse_args()
    if args.cmd == "summary":
        summary(args.runs)
    else:
        scan(args.a, args.b, args.min_n)


if __name__ == "__main__":
    main()
