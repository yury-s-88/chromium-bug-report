#!/usr/bin/env python3
"""Extract the presentation-path counters from a full chrome://histograms dump.

WHY THIS EXISTS
---------------
A full `chrome://histograms` capture on a real profile is ~3 MB and contains ~4700
counters, the overwhelming majority of them unrelated to this report and many of them
personal: stored-autofill entity counts (including document types), clipboard word
counts, download statistics, language detection, navigation counts. Those must not be
published in a bug report. This script keeps only the families the report actually
cites and drops everything else, so the committed evidence is small, auditable, and
free of profile data.

The transformation is deliberately trivial and lossless within the families it keeps:
every matching histogram block is copied verbatim, header and buckets, so the numbers
in `telemetry/` are byte-identical to what Chrome printed.

WHAT IT KEEPS  (prefix match)
-----------------------------
  Graphics.Smoothness.*   jank / dropped-frame / checkerboarding, per sequence type
  Compositing.Display.*   the Viz draw->swap->commit stage timings, incl.
                          SwapStartToSwapEnd == swap start -> CALayer commit
  GPU.Presentation.*      incl. FrameHandlesAnimationOrInteraction, the gating predicate
  Gpu.Mac.*               incl. BackpressureUs, the Metal backpressure wait
  Viz.*                   BeginFrameSource / DisplayLink accuracy and interval

WHAT IS LOST
------------
Everything else — which means a whole-dump survey (e.g. "does ANY counter separate
these two runs?") cannot be repeated from the extracts. That survey needs the full
captures, which are not published for the reasons above; `compare_histograms.py`
is published so anyone can repeat it on their own captures in ~2 minutes.

USAGE
-----
    python3 analysis/extract_histograms.py DUMP.txt > telemetry/extracts/NAME.txt
    python3 analysis/extract_histograms.py DUMP.txt --list   # names only, no buckets
"""

import argparse
import re
import sys

KEEP_PREFIXES = (
    "Graphics.Smoothness.",
    "Compositing.Display.",
    "GPU.Presentation.",
    "Gpu.Mac.",
    "Viz.",
)

HEADER = re.compile(r"^- Histogram: (\S+) recorded ")


def blocks(lines):
    """Yield (name, [lines]) for each histogram block in a chrome://histograms dump.

    A block starts at its '- Histogram: NAME recorded N samples' header and runs until
    the next header. Trailing blank lines are trimmed so output spacing is uniform
    regardless of how the dump was copied out of the page.
    """
    name, buf = None, []
    for line in lines:
        m = HEADER.match(line)
        if m:
            if name is not None:
                yield name, buf
            name, buf = m.group(1), [line]
        elif name is not None:
            buf.append(line)
    if name is not None:
        yield name, buf


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", help="a full chrome://histograms capture (plain text)")
    ap.add_argument("--list", action="store_true",
                    help="print matching histogram names only")
    args = ap.parse_args()

    with open(args.dump, errors="replace") as f:
        lines = f.readlines()

    kept = total = 0
    out = []
    for name, buf in blocks(lines):
        total += 1
        if not name.startswith(KEEP_PREFIXES):
            continue
        kept += 1
        if args.list:
            out.append(name + "\n")
            continue
        while buf and not buf[-1].strip():
            buf.pop()
        out.extend(buf)
        out.append("\n")

    sys.stdout.writelines(out)
    print(f"# extracted {kept} of {total} histograms from {args.dump}", file=sys.stderr)


if __name__ == "__main__":
    main()
