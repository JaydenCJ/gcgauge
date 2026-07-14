"""The committed example logs and the README's claims about them stay true.

These tests pin the exact behavior the documentation shows: the generator is
deterministic, the steady log is clean, the leak log is flagged, and the diff
between them gates. If a change to the analyzer shifts any of this, the docs
must be regenerated in the same commit — this file is the tripwire.
"""

import io
import subprocess
import sys
from pathlib import Path

from gcgauge import analyze, parse_log
from tests.conftest import EXAMPLES

ROOT = Path(__file__).resolve().parents[1]


def test_generator_is_deterministic():
    spec = ROOT / "examples" / "generate_gc_log.py"
    ns = {"__name__": "generate_gc_log"}
    exec(compile(spec.read_text(encoding="utf-8"), str(spec), "exec"), ns)
    a, b = io.StringIO(), io.StringIO()
    ns["generate"]("leak", 5.0, 7, a)
    ns["generate"]("leak", 5.0, 7, b)
    assert a.getvalue() == b.getvalue()
    assert a.getvalue().splitlines()[0] == "[0.005s][info][gc] Using G1"


def test_committed_logs_match_generator_output():
    # The committed samples must be regenerable byte-for-byte: same script,
    # same seed, same bytes. Anything else means docs and data have diverged.
    for profile, name in [("steady", "g1-steady.log"), ("leak", "g1-leak.log")]:
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "examples" / "generate_gc_log.py"),
                "--profile", profile, "--minutes", "30", "--seed", "7",
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        committed = (EXAMPLES / name).read_text(encoding="utf-8")
        assert proc.stdout == committed, "%s diverged from its generator" % name


def test_steady_log_is_clean_and_leak_log_is_flagged():
    steady = analyze(parse_log(EXAMPLES / "g1-steady.log"))
    assert steady["leak"]["verdict"] == "none"
    assert steady["full_gc"]["count"] == 0
    assert steady["throughput_pct"] > 99.0

    leak = analyze(parse_log(EXAMPLES / "g1-leak.log"))
    assert leak["leak"]["verdict"] == "likely"
    assert leak["full_gc"]["count"] > 0
    rising = [i for i in leak["leak"]["indicators"] if i["id"] == "rising_floor"]
    assert rising[0]["triggered"]


def test_jdk8_log_shows_leak_pattern():
    report = analyze(parse_log(EXAMPLES / "jdk8-parallel.log"))
    assert report["collector"] == "Parallel"
    assert report["format"] == "legacy"
    assert report["leak"]["verdict"] == "likely"
    assert report["full_gc"]["count"] == 4
