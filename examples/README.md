# Examples

Three committed GC logs exercise every code path, plus the deterministic
generator that produced two of them.

| File | What it is |
|---|---|
| `g1-steady.log` | JDK 17-style unified G1 log, 30 min, healthy: flat post-GC floor, no full GC. |
| `g1-leak.log` | Same JVM shape with a ~2.4 MB/s leak: rising floor, to-space exhaustion, full GCs with poor reclaim. |
| `jdk8-parallel.log` | JDK 8 `-XX:+PrintGCDetails` Parallel log with old-gen growth ending in back-to-back full GCs. |
| `generate_gc_log.py` | The generator for the two G1 logs — same seed, byte-identical output. |

## Try them

```bash
gcgauge report examples/g1-steady.log
gcgauge report examples/g1-leak.log
gcgauge report examples/jdk8-parallel.log --format markdown

# The cross-run gate: exit 1 because the leaky run regresses.
gcgauge diff examples/g1-steady.log examples/g1-leak.log

# The CI budget gate.
gcgauge check examples/g1-leak.log --max-p99 50 --fail-on-leak possible
```

## Regenerating the G1 logs

```bash
python examples/generate_gc_log.py --profile steady --minutes 30 --seed 7 > examples/g1-steady.log
python examples/generate_gc_log.py --profile leak   --minutes 30 --seed 7 > examples/g1-leak.log
```

`tests/test_examples.py` verifies the committed bytes match the generator
output, so the logs, the README numbers, and the analyzer can never drift
apart silently.
