"""CLI behavior: subcommands, exit codes, file output, and error paths."""

import json
import os
import subprocess
import sys
from pathlib import Path

import gcgauge
from gcgauge.cli import main
from tests.conftest import EXAMPLES

STEADY = str(EXAMPLES / "g1-steady.log")
LEAK = str(EXAMPLES / "g1-leak.log")
JDK8 = str(EXAMPLES / "jdk8-parallel.log")


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _src_env():
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return env


def test_report_text_default(capsys):
    code, out, err = run(capsys, "report", STEADY)
    assert code == 0
    assert "gcgauge report" in out
    assert "collector: G1" in out
    assert err == ""


def test_report_json_is_valid_tagged_and_deterministic(capsys):
    code_a, out_a, _ = run(capsys, "report", LEAK, "--format", "json")
    code_b, out_b, _ = run(capsys, "report", LEAK, "--format", "json")
    assert code_a == code_b == 0
    assert out_a == out_b
    data = json.loads(out_a)
    assert data["gcgauge_report"] == 1
    assert data["leak"]["verdict"] == "likely"


def test_every_format_renders_every_example(capsys):
    for fmt in ("text", "json", "markdown"):
        for log in (STEADY, LEAK, JDK8):
            code, out, err = run(capsys, "report", log, "--format", fmt)
            assert code == 0, (log, fmt, err)
            assert out


def test_input_errors_are_exit_2_without_tracebacks(capsys, tmp_path):
    code, _, err = run(capsys, "report", "no-such-file.log")
    assert code == 2
    assert "gcgauge: error:" in err
    assert "Traceback" not in err

    bogus = tmp_path / "app.log"
    bogus.write_text("INFO starting up\nINFO ready\n", encoding="utf-8")
    code, _, err = run(capsys, "report", str(bogus))
    assert code == 2
    assert "-Xlog:gc" in err

    code, _, err = run(capsys, "check", STEADY)  # no budget flags at all
    assert code == 2
    assert "no budgets given" in err


def test_diff_exit_codes_and_json_format(capsys):
    code, out, _ = run(capsys, "diff", STEADY, LEAK)
    assert code == 1
    assert "regression" in out

    code, out, _ = run(capsys, "diff", STEADY, STEADY)
    assert code == 0
    assert "no regressions" in out

    code, out, _ = run(capsys, "diff", STEADY, LEAK, "--format", "json")
    assert code == 1
    data = json.loads(out)
    assert data["gcgauge_diff"] == 1
    assert data["regressions"] >= 3


def test_saved_json_report_is_a_first_class_input(capsys, tmp_path):
    # `report -o` writes the file (stdout stays quiet), and both diff and
    # check accept that JSON in place of the original log.
    baseline = tmp_path / "baseline.json"
    code, out, _ = run(capsys, "report", STEADY, "--format", "json",
                       "-o", str(baseline))
    assert code == 0
    assert out == ""
    assert json.loads(baseline.read_text(encoding="utf-8"))["gcgauge_report"] == 1

    code, out, _ = run(capsys, "diff", str(baseline), LEAK)
    assert code == 1
    assert "leak verdict" in out


def test_diff_rejects_foreign_json(capsys, tmp_path):
    impostor = tmp_path / "other.json"
    impostor.write_text('{"unrelated": true}', encoding="utf-8")
    code, _, err = run(capsys, "diff", str(impostor), STEADY)
    assert code == 2
    assert "gcgauge_report" in err


def test_check_passing_budgets(capsys):
    code, out, _ = run(
        capsys, "check", STEADY,
        "--max-p99", "50", "--min-throughput", "99", "--fail-on-leak", "possible",
    )
    assert code == 0
    assert "all 3 check(s) passed" in out
    assert out.count("PASS") == 3

    code, out, _ = run(capsys, "check", LEAK, "--max-pause", "10000")
    assert code == 0
    assert "max pause" in out


def test_check_failing_budgets(capsys):
    code, out, _ = run(
        capsys, "check", LEAK,
        "--max-p99", "50", "--min-throughput", "99", "--fail-on-leak", "possible",
    )
    assert code == 1
    assert out.count("FAIL") == 3


def test_check_accepts_saved_json_report(capsys, tmp_path):
    saved = tmp_path / "run.json"
    assert main(["report", LEAK, "--format", "json", "-o", str(saved)]) == 0
    capsys.readouterr()
    code, out, _ = run(capsys, "check", str(saved), "--fail-on-leak", "likely")
    assert code == 1
    assert "leak verdict likely" in out


def test_no_command_prints_help_and_exits_2(capsys):
    code, out, _ = run(capsys)
    assert code == 2
    assert "report" in out and "diff" in out and "check" in out


def test_module_invocation_and_version_match_package(tmp_path):
    version = subprocess.run(
        [sys.executable, "-m", "gcgauge", "--version"],
        capture_output=True, text=True, cwd=str(tmp_path), env=_src_env(),
    )
    assert version.returncode == 0
    assert version.stdout.strip() == "gcgauge %s" % gcgauge.__version__

    # A real end-to-end run from an unrelated working directory.
    proc = subprocess.run(
        [sys.executable, "-m", "gcgauge", "report", STEADY],
        capture_output=True, text=True, cwd=str(tmp_path), env=_src_env(),
    )
    assert proc.returncode == 0
    assert "gcgauge report" in proc.stdout
