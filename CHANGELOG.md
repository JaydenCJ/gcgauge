# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-13

### Added

- Parser for JDK 9+ unified GC logging (`-Xlog:gc`, JEP 158): G1, Parallel,
  Serial, ZGC (single-gen and generational), and Shenandoah line shapes;
  uptime, wall-clock, and undecorated logs; `To-space exhausted` markers
  matched to their pause by GC id.
- Parser for JDK 8 `-XX:+PrintGCDetails` logs: Parallel/Serial minor and
  full collections, G1-on-8 pause lines, CMS stop-the-world phases and
  concurrent progress lines, promotion-failure (`--`) markers; nested
  generation segments stripped structurally so the whole-heap transition is
  never confused with a generation's.
- Normalized event model with format sniffing, collector detection, and a
  three-way clock (`uptime` / `absolute` / `index`) so wall-clock-only and
  timestamp-free logs still produce trend analysis.
- Deterministic statistics: nearest-rank pause percentiles per collection
  class (p50/p90/p95/p99/max), throughput and GC overhead, allocation-rate
  estimation from heap growth between collections.
- Heap trend analysis: least-squares post-GC floor slope with r², with a
  full-GC basis only when full collections actually span the run.
- Six weighted leak indicators (rising floor, full-GC frequency growth, low
  full-GC reclaim, evacuation failures, sustained high occupancy,
  OutOfMemoryError) rolled into a `none` / `possible` / `likely` verdict,
  each reporting evidence whether triggered or not.
- `gcgauge report` with text, markdown, and JSON renderers; JSON has sorted
  keys and fixed rounding, byte-identical across runs and platforms
  (documented in `docs/json-output.md`).
- `gcgauge diff` cross-run comparison — both sides accept a raw log or a
  saved JSON report — with a regression threshold, leak-verdict escalation
  rules, and exit code 1 on regression.
- `gcgauge check` CI budget gate: `--max-p99`, `--max-pause`,
  `--min-throughput`, `--fail-on-leak`; exit code 1 on violation.
- Committed example logs (healthy G1, leaking G1, JDK 8 Parallel) plus a
  deterministic generator; tests pin the committed bytes to the generator.
- 90 pytest tests and `scripts/smoke.sh`, all offline and deterministic.

### Notes

- The repository ships no CI workflow; verification is local —
  `pip install -e '.[dev]' && pytest && bash scripts/smoke.sh`.

[0.1.0]: https://github.com/JaydenCJ/gcgauge/releases/tag/v0.1.0
