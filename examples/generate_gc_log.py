#!/usr/bin/env python3
"""Generate a synthetic JDK 17-style unified G1 GC log — deterministically.

The committed sample logs in this directory were produced by this script:

    python examples/generate_gc_log.py --profile steady --minutes 30 --seed 7 \
        > examples/g1-steady.log
    python examples/generate_gc_log.py --profile leak --minutes 30 --seed 7 \
        > examples/g1-leak.log

Same seed, same flags -> byte-identical log, which keeps every number in the
README reproducible. The simulation is simple but honest about shape: young
collections arrive as allocation fills eden, the ``leak`` profile grows the
live set every collection until mixed collections stop keeping up and full
GCs appear with poor reclaim — exactly the pattern gcgauge is built to flag.
"""

import argparse
import random
import sys

HEAP_MB = 4096
EDEN_MB = 400  # young collections trigger roughly every EDEN_MB of allocation


def _emit(out, t, gc_id, body):
    out.write("[%.3fs][info][gc] GC(%d) %s\n" % (t, gc_id, body))


def generate(profile: str, minutes: float, seed: int, out) -> None:
    rng = random.Random(seed)
    out.write("[0.005s][info][gc] Using G1\n")

    duration_s = minutes * 60.0
    t = rng.uniform(0.2, 0.6)
    gc_id = 0
    floor_mb = 700.0  # live set right after a collection
    leak_mb_per_s = 2.40 if profile == "leak" else 0.0
    since_mixed = 0

    while t < duration_s:
        alloc_rate = rng.uniform(30.0, 46.0)  # MB/s of fresh allocation
        interval = EDEN_MB / alloc_rate
        t += interval
        if t >= duration_s:
            break
        floor_mb += leak_mb_per_s * interval

        occupancy_pct = floor_mb / HEAP_MB
        if occupancy_pct > 0.90 and profile == "leak":
            # Nothing left to evacuate into: degenerate to a full GC that can
            # only reclaim the young garbage, not the leaked live set.
            _emit(out, t, gc_id, "To-space exhausted")
            before = min(HEAP_MB - 16, floor_mb + rng.uniform(120, 260))
            reclaimed = rng.uniform(90, 170)
            floor_mb = max(floor_mb - reclaimed * 0.3, 0)
            after = floor_mb
            pause = rng.uniform(380.0, 900.0)
            _emit(
                out, t, gc_id,
                "Pause Full (G1 Compaction Pause) %dM->%dM(%dM) %.3fms"
                % (before, after, HEAP_MB, pause),
            )
            t += pause / 1000.0
            gc_id += 1
            since_mixed = 0
            continue

        before = min(HEAP_MB, floor_mb + EDEN_MB + rng.uniform(-25, 25))
        since_mixed += 1
        if occupancy_pct > 0.45 and since_mixed >= 6:
            # Concurrent cycle then a mixed collection, as G1 actually does.
            cycle_ms = rng.uniform(140.0, 420.0)
            _emit(out, t, gc_id, "Concurrent Mark Cycle %.3fms" % cycle_ms)
            gc_id += 1
            t += rng.uniform(1.5, 4.0)
            after = floor_mb * rng.uniform(0.97, 1.0)
            pause = rng.uniform(14.0, 34.0)
            _emit(
                out, t, gc_id,
                "Pause Young (Mixed) (G1 Evacuation Pause) %dM->%dM(%dM) %.3fms"
                % (before, after, HEAP_MB, pause),
            )
            since_mixed = 0
        else:
            after = floor_mb + rng.uniform(0, 20)
            pause = rng.uniform(4.0, 16.0) * (1.0 + occupancy_pct)
            _emit(
                out, t, gc_id,
                "Pause Young (Normal) (G1 Evacuation Pause) %dM->%dM(%dM) %.3fms"
                % (before, after, HEAP_MB, pause),
            )
        gc_id += 1
        t += pause / 1000.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", choices=["steady", "leak"], default="steady")
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    generate(args.profile, args.minutes, args.seed, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
