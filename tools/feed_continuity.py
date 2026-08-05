#!/usr/bin/env python3
"""Record port-8083 stream continuity across a controller operation.

Answers one question the cumulative counters cannot: does the feed keep
*delivering* through an FT_FindSurface, or does it stall with the socket still
open? `connects` and `generation` only move on a reconnect, so a stream that
goes quiet for two seconds and then resumes leaves every counter looking
perfect. The only way to see it is to sample the frame count against a clock.

**This performs no robot I/O.** It polls the local Flask app's
`/ui/diagnostics/feed` panel, which renders the feed's cached frame and never
touches the controller. Running it is safe at any time; it adds nothing to the
XML-RPC worker's queue and opens no socket to the robot.

    python tools/feed_continuity.py --duration 90 --out run.csv

Start it, run the operation from the UI, then read the summary. A healthy
stream shows a max gap near the sample interval. A stall shows up as a gap of
hundreds of milliseconds or more with `connects` unchanged.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:5000/ui/diagnostics/feed"

# The panel is HTML; these pull the few numbers that matter out of its text.
_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_WS = re.compile(r"\s+")

_PATTERNS = {
    "frames": re.compile(r"([\d,]+) frames"),
    "fps": re.compile(r"([\d.]+) fps"),
    "age_s": re.compile(r"([\d.]+)s ago"),
    "checksum_fail": re.compile(r"Checksum fails (\d+)"),
    "resync_bytes": re.compile(r"Resync bytes (\d+)"),
    "connects": re.compile(r"Reconnects (\d+)"),
    "generation": re.compile(r"Generation (\d+)"),
    "data_len": re.compile(r"DATA length (\d+)"),
    "fz": re.compile(r"Fz \(native\) (-?[\d.]+) N"),
    "tcp_z": re.compile(r"TCP Z (-?[\d.]+) mm"),
    "ft_active": re.compile(r"F/T sensor (\w+)"),
}

# Health label is the first bold word in the banner.
_HEALTH = re.compile(r"^\s*(Streaming|Connecting|Stalled|No stream|Bad frames|Stopped|Unknown)")

# The comparison rows render as "<label> [=|≠] <feed> / <rpc>", with a missing
# value shown as an em dash. Capturing BOTH columns is the point: when the
# XML-RPC link dies the rpc column falls to "—" while the feed column keeps
# reporting, and that divergence lands on a single timestamped row.
_COMPARE = {
    "state": "Program state",
    "line": "Current line",
    "fault_main": "Fault main",
}
_COMPARE_RE = {
    key: re.compile(rf"{label} (?:[=≠] )?(\S+) / (\S+)")
    for key, label in _COMPARE.items()
}

# Derived from _COMPARE so the column names can never drift from the keys the
# sampler actually writes.
FIELDS = [
    "t", "elapsed", "health", "frames", "d_frames", "gap_s", "fps", "age_s",
    *[f"{side}_{key}" for key in _COMPARE for side in ("feed", "rpc")],
    "rpc_alive", "fz", "tcp_z", "ft_active",
    "checksum_fail", "resync_bytes", "connects", "generation", "data_len",
]


def scrape(url: str, timeout: float) -> dict[str, str]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", "replace")
    text = _WS.sub(" ", _TAG.sub(" ", _COMMENT.sub("", html))).strip()

    out: dict[str, str] = {}
    health = _HEALTH.match(text)
    out["health"] = health.group(1) if health else "?"
    for name, pattern in _PATTERNS.items():
        found = pattern.search(text)
        out[name] = found.group(1).replace(",", "") if found else ""

    for key, pattern in _COMPARE_RE.items():
        found = pattern.search(text)
        out[f"feed_{key}"] = found.group(1) if found else ""
        out[f"rpc_{key}"] = found.group(2) if found else ""

    # Both compared signals blank on the RPC side means the link is no longer
    # reporting. Fault is excluded: "—" there is the normal no-fault reading.
    out["rpc_alive"] = "0" if all(
        out.get(f"rpc_{k}", "") in ("", "—") for k in ("state", "line")
    ) else "1"
    return out


def as_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--hz", type=float, default=8.0, help="samples per second")
    ap.add_argument("--duration", type=float, default=120.0, help="seconds")
    ap.add_argument("--out", default="feed_continuity.csv")
    ap.add_argument("--timeout", type=float, default=2.0)
    args = ap.parse_args()

    interval = 1.0 / args.hz
    start = time.monotonic()
    prev_frames: int | None = None
    last_advance = start
    worst_gap = 0.0
    worst_gap_at = 0.0
    errors = 0
    first: dict[str, str] | None = None
    last: dict[str, str] = {}
    rows: list[dict[str, object]] = []

    print(f"Sampling {args.url} at {args.hz:g} Hz for {args.duration:g}s.")
    print("Run the operation now. Ctrl-C to stop early.\n")
    print(f"{'t':>7}  {'health':<10} {'frames':>8} {'gap':>6}  {'feed':<13} {'rpc':<12} {'fz':>7}")

    # Written and flushed row-by-row rather than in one block at the end, so a
    # long unattended recording can be read while it is still running.
    fh = open(args.out, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=FIELDS)
    writer.writeheader()
    fh.flush()

    try:
        while True:
            now = time.monotonic()
            elapsed = now - start
            if elapsed >= args.duration:
                break

            try:
                sample = scrape(args.url, args.timeout)
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                errors += 1
                print(f"{elapsed:7.2f}  sample failed: {exc}")
                time.sleep(interval)
                continue

            frames = as_int(sample["frames"])
            delta = None if frames is None or prev_frames is None else frames - prev_frames
            if frames is not None and (prev_frames is None or frames > prev_frames):
                last_advance = now
            gap = now - last_advance
            if gap > worst_gap:
                worst_gap, worst_gap_at = gap, elapsed

            if first is None:
                first = sample
            last = sample

            row = {
                "t": f"{time.time():.3f}", "elapsed": f"{elapsed:.3f}",
                "health": sample["health"], "frames": frames,
                "d_frames": delta, "gap_s": f"{gap:.3f}", "fps": sample["fps"],
                "age_s": sample["age_s"],
                "fz": sample["fz"], "tcp_z": sample["tcp_z"],
                "ft_active": sample["ft_active"], "checksum_fail": sample["checksum_fail"],
                "resync_bytes": sample["resync_bytes"], "connects": sample["connects"],
                "generation": sample["generation"], "data_len": sample["data_len"],
            }
            for key in _COMPARE:
                row[f"feed_{key}"] = sample.get(f"feed_{key}", "")
                row[f"rpc_{key}"] = sample.get(f"rpc_{key}", "")
            row["rpc_alive"] = sample["rpc_alive"]
            rows.append(row)
            writer.writerow(row)
            fh.flush()

            # Print on a gap, on an RPC transition, or periodically — so the
            # console stays readable but never hides a stall or a link drop.
            rpc_changed = len(rows) > 1 and rows[-2]["rpc_alive"] != row["rpc_alive"]
            if gap > interval * 2.5 or rpc_changed or len(rows) % 8 == 1:
                flag = "  <-- RPC LINK CHANGED" if rpc_changed else ""
                print(f"{elapsed:7.2f}  {sample['health']:<10} {frames if frames is not None else '-':>8} "
                      f"{gap:6.2f}  feed={sample.get('feed_state', '?'):<8} "
                      f"rpc={sample.get('rpc_state', '?'):<8} {sample['fz']:>7}{flag}")

            prev_frames = frames if frames is not None else prev_frames
            time.sleep(max(0.0, interval - (time.monotonic() - now)))
    except KeyboardInterrupt:
        print("\nstopped early")

    if not rows:
        print("No samples collected — is the app running?")
        return 1

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    span = float(rows[-1]["elapsed"]) - float(rows[0]["elapsed"])
    f0, f1 = as_int(str(rows[0]["frames"])), as_int(str(rows[-1]["frames"]))
    delivered = (f1 - f0) if (f0 is not None and f1 is not None) else None
    c0 = as_int(first["connects"]) if first else None
    c1 = as_int(last.get("connects", ""))
    x0 = as_int(first["checksum_fail"]) if first else None
    x1 = as_int(last.get("checksum_fail", ""))

    print(f"\n{'-' * 58}\nSummary  ({len(rows)} samples over {span:.1f}s -> {args.out})")
    if delivered is not None and span > 0:
        print(f"  frames delivered : {delivered}  ({delivered / span:.2f}/s average)")
    print(f"  longest gap      : {worst_gap:.3f}s at t={worst_gap_at:.1f}s")
    if c0 is not None and c1 is not None:
        verdict = "NO reconnect" if c1 == c0 else f"RECONNECTED {c1 - c0}x"
        print(f"  connects         : {c0} -> {c1}   [{verdict}]")
    if x0 is not None and x1 is not None:
        print(f"  checksum fails   : {x0} -> {x1}")
    if errors:
        print(f"  sampler errors   : {errors} (local HTTP, not the feed)")

    # Contiguous stretches where the XML-RPC cache reported nothing. The feed's
    # delivery rate across the longest one is the entire point of the exercise.
    outages: list[tuple[int, int]] = []
    open_at: int | None = None
    for i, r in enumerate(rows):
        if r["rpc_alive"] == "0" and open_at is None:
            open_at = i
        elif r["rpc_alive"] != "0" and open_at is not None:
            outages.append((open_at, i - 1))
            open_at = None
    if open_at is not None:
        outages.append((open_at, len(rows) - 1))

    if outages:
        def span_of(pair: tuple[int, int]) -> float:
            return float(rows[pair[1]]["elapsed"]) - float(rows[pair[0]]["elapsed"])

        a, b = max(outages, key=span_of)
        dur = span_of((a, b))
        fa, fb = rows[a]["frames"], rows[b]["frames"]
        print(f"  XML-RPC outages  : {len(outages)}  "
              f"(longest {dur:.1f}s at t={float(rows[a]['elapsed']):.1f}s)")
        if isinstance(fa, int) and isinstance(fb, int) and dur > 0:
            print(f"    feed delivered : {fb - fa} frames during it "
                  f"({(fb - fa) / dur:.2f}/s)  <-- the question")
    else:
        print("  XML-RPC outages  : none observed")

    no_reconnect = c1 == c0 if (c0 is not None and c1 is not None) else False
    healthy = worst_gap < 1.0 and no_reconnect
    print(f"\n  {'FEED CONTINUOUS — no stall, no reconnect.' if healthy else 'FEED GAP DETECTED — inspect the CSV around the longest gap.'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
