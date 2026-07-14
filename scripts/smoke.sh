#!/usr/bin/env bash
# Smoke test for gcgauge: report on the committed sample logs, gate the
# leaky run with `check`, and verify that `diff` catches the regression.
# Self-contained: pure stdlib, no network, idempotent (works from a clean tree).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
if [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON="$ROOT/.venv/bin/python"
fi

# The package has zero runtime dependencies, so running from src/ needs no install.
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/gcgauge-smoke.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT

fail() { echo "SMOKE FAIL: $1" >&2; exit 1; }

echo "[smoke] python: $("$PYTHON" --version 2>&1)"

# 1. Text report on the healthy G1 log: clean verdict, sane percentile table.
steady_out="$("$PYTHON" -m gcgauge report "$ROOT/examples/g1-steady.log")" \
  || fail "report on g1-steady.log exited non-zero"
echo "$steady_out" | sed -n '1,3p' | sed 's/^/[report] /'
echo "$steady_out" | grep -q "collector: G1" || fail "steady report missing collector"
echo "$steady_out" | grep -q "Pause percentiles (ms)" || fail "steady report missing table"
echo "$steady_out" | grep -q "verdict: NONE" || fail "steady log should have leak verdict NONE"

# 2. Text report on the leaking log flags the leak with evidence.
leak_out="$("$PYTHON" -m gcgauge report "$ROOT/examples/g1-leak.log")" \
  || fail "report on g1-leak.log exited non-zero"
echo "$leak_out" | grep -q "verdict: LIKELY" || fail "leak log should have leak verdict LIKELY"
echo "$leak_out" | grep -q "rising post-GC floor" || fail "leak report missing floor indicator"

# 3. The JDK 8 legacy log parses with the right collector.
jdk8_out="$("$PYTHON" -m gcgauge report "$ROOT/examples/jdk8-parallel.log")" \
  || fail "report on jdk8-parallel.log exited non-zero"
echo "$jdk8_out" | grep -q "format: legacy" || fail "jdk8 log not detected as legacy"
echo "$jdk8_out" | grep -q "collector: Parallel" || fail "jdk8 collector not detected"

# 4. JSON report round-trips as a saved baseline (byte-identical on re-run).
"$PYTHON" -m gcgauge report "$ROOT/examples/g1-steady.log" --format json \
  -o "$WORKDIR/baseline.json" || fail "json report failed"
"$PYTHON" -m gcgauge report "$ROOT/examples/g1-steady.log" --format json \
  -o "$WORKDIR/baseline2.json" || fail "second json report failed"
cmp -s "$WORKDIR/baseline.json" "$WORKDIR/baseline2.json" \
  || fail "json report is not deterministic across runs"

# 5. diff of a run against itself: no regressions, exit 0.
"$PYTHON" -m gcgauge diff "$WORKDIR/baseline.json" "$ROOT/examples/g1-steady.log" \
  >/dev/null || fail "diff of identical runs should exit 0"

# 6. diff steady vs leaky: regression table, exit 1.
set +e
diff_out="$("$PYTHON" -m gcgauge diff "$WORKDIR/baseline.json" "$ROOT/examples/g1-leak.log")"
diff_rc=$?
set -e
echo "$diff_out" | tail -n 3 | sed 's/^/[diff] /'
[ "$diff_rc" -eq 1 ] || fail "diff on regression should exit 1, got $diff_rc"
echo "$diff_out" | grep -q "escalated" || fail "diff did not report leak escalation"
echo "$diff_out" | grep -Eq "[0-9]+ regression" || fail "diff did not count regressions"

# 7. check gates: the steady run passes budgets that the leaky run fails.
"$PYTHON" -m gcgauge check "$ROOT/examples/g1-steady.log" \
  --max-p99 50 --min-throughput 99 --fail-on-leak possible >/dev/null \
  || fail "steady run should pass its budgets"
set +e
check_out="$("$PYTHON" -m gcgauge check "$ROOT/examples/g1-leak.log" \
  --max-p99 50 --min-throughput 99 --fail-on-leak possible)"
check_rc=$?
set -e
echo "$check_out" | sed 's/^/[check] /'
[ "$check_rc" -eq 1 ] || fail "leaky run should fail its budgets, got exit $check_rc"
echo "$check_out" | grep -q "FAIL" || fail "check output missing FAIL lines"

# 8. --version agrees with the package version.
version_out="$("$PYTHON" -m gcgauge --version)"
pkg_version="$("$PYTHON" -c 'import gcgauge; print(gcgauge.__version__)')"
[ "$version_out" = "gcgauge $pkg_version" ] \
  || fail "--version mismatch: '$version_out' vs package '$pkg_version'"

echo "SMOKE OK"
