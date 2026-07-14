# JSON output (schema version 1)

`gcgauge report --format json` emits a single JSON object with sorted keys,
fixed rounding, and a trailing newline. Identical logs produce byte-identical
output on any supported Python — that property is load-bearing: saved JSON
is accepted back as an input to `gcgauge diff` and `gcgauge check`.

A saved run is recognized by its `gcgauge_report` key. The schema version
is bumped on any breaking key change; additive keys do not bump it.

## Top-level keys

| Key | Type | Meaning |
|---|---|---|
| `gcgauge_report` | int | Schema version, currently `1`. |
| `source` | string | The path given on the command line. |
| `format` | string | `unified` (JDK 9+) or `legacy` (JDK 8). |
| `collector` | string | `G1`, `Parallel`, `Serial`, `CMS`, `ZGC`, `Shenandoah`, or `unknown`. |
| `clock` | string | `uptime`, `absolute` (wall-clock, normalized so t0 = first event), or `index` (no timestamps; ordinals). |
| `events` | object | `total`, `pauses`, `concurrent` counts. |
| `window` | object | `start_s`, `end_s`, `duration_s`; the end includes the final pause's duration. |
| `pauses` | object | `classes` (per pause class, see below) and `all` (every stop-the-world pause). |
| `throughput_pct` | number | Percent of wall time the application was running, `100 * (1 - pause/duration)`. |
| `gc_overhead_pct` | number | `100 - throughput_pct`; the metric `diff` gates on. |
| `total_pause_s` | number | Sum of all stop-the-world pause durations. |
| `allocation_rate_mb_s` | number\|null | Mean allocation rate from heap growth between collections. |
| `heap` | object | `total_mb` (max observed capacity) and `post_gc_floor` (see below). |
| `full_gc` | object | `count`, `first_half`, `second_half`, `per_min`, `avg_reclaim_pct`. |
| `leak` | object | `verdict` (`none`/`possible`/`likely`), `score`, `max_score`, `indicators[]`. |
| `warnings` | array | Analysis caveats, e.g. a synthetic time axis. |

## `pauses.classes.<kind>` / `pauses.all`

Percentiles are **nearest-rank**: every reported value is a pause that
actually happened, never an interpolation. Concurrent phases and ZGC cycles
are excluded — they have durations but do not stall the application.

| Key | Meaning |
|---|---|
| `count` | Number of pauses in the class. |
| `mean_ms`, `p50_ms`, `p90_ms`, `p95_ms`, `p99_ms`, `max_ms` | Milliseconds, rounded to 3 decimals. |
| `total_ms` | Sum of the class's pause time. |

Kinds: `young`, `mixed`, `full` first, then any dialect-specific pause names
(`remark`, `cleanup`, `initial-mark`, `init-mark`, `final-mark`, ...)
alphabetically.

## `heap.post_gc_floor`

A least-squares fit over post-collection heap occupancy — the live-set floor.

| Key | Meaning |
|---|---|
| `basis` | `full GC` when at least 3 fulls span at least half the window; otherwise `all collections`. |
| `samples` | Points in the fitted series. |
| `first_mb`, `last_mb` | Observed floor at the ends of the series. |
| `slope_mb_per_min` | Fitted growth rate. |
| `r2` | Fit quality (0..1). |
| `last_quarter_occupancy_pct` | Mean post-GC occupancy over the final quarter of samples, as % of capacity. |

## `leak.indicators[]`

Each indicator carries `id`, `label`, `weight`, `severity` (`ok` when not
triggered, else `warn`/`critical`), `triggered`, and a human-readable
`detail` — evidence is emitted either way, so a clean log yields an
auditable "checked and not found" list.

| id | Weight | Trigger |
|---|---|---|
| `rising_floor` | 4 | Floor slope > 0, r² >= 0.6, total rise >= 10% of heap (>= 25% relative when capacity is unknown), >= 4 samples. |
| `full_gc_growth` | 2 | >= 2 full GCs in the second half of the run and at least double the first half. |
| `low_full_reclaim` | 2 | The most recent half of full GCs reclaim < 10% of heap on average. |
| `evacuation_failure` | 1 | Any to-space exhausted / promotion failed event. |
| `high_occupancy` | 1 | Mean post-GC occupancy >= 85% of heap over the last quarter. |
| `oom` | 4 | `java.lang.OutOfMemoryError` appears anywhere in the log. |

Verdict: score 0 -> `none`; 1..3 -> `possible`; >= 4 -> `likely`.

## Diff output (`gcgauge diff --format json`)

A `gcgauge_diff: 1` object with `baseline`, `current`, `threshold_pct`,
`regressions`, `improvements`, and a `metrics[]` array. Each metric row has
`id`, `label`, `baseline`, `current`, `delta_pct`, and a `verdict`:
`ok` / `regression` / `improved` / `info` (never gates) / `n/a` (missing on
either side). Two rules are threshold-independent by design: a leak-verdict
escalation always regresses, and a lower-is-better metric going from exactly
0 to non-zero regresses even though no finite percentage exists.
