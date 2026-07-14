# Contributing to gcgauge

Thanks for your interest in contributing. Issues, discussions, and pull
requests are all welcome.

## Getting started

You need Python 3.9 or newer — nothing else. The runtime has zero
dependencies; pytest is the only development dependency.

```bash
git clone https://github.com/JaydenCJ/gcgauge
cd gcgauge
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
bash scripts/smoke.sh
```

`scripts/smoke.sh` runs the real CLI end-to-end against the committed
example logs — reports, the diff gate, the check gate, `--version` — and
must print `SMOKE OK`.

## Before you open a pull request

1. `pytest` — all 90 tests must pass, offline, in under a few seconds.
2. `bash scripts/smoke.sh` — must print `SMOKE OK`.
3. Add tests for behavior changes; keep logic in pure, unit-testable modules
   (parsers produce events, analysis produces dicts, renderers only format).
4. If you touch report keys, update `docs/json-output.md` and bump the
   schema version in the same pull request when the change is breaking.
5. If you change the analyzer in a way that shifts the example numbers,
   regenerate the sample logs and the README output blocks together —
   `tests/test_examples.py` is the tripwire.

## Ground rules

- **No runtime dependencies.** The standard library is the whole budget;
  adding a dependency needs a very strong justification in the PR.
- **No network, no telemetry, no clock reads in analysis.** Reports must be
  byte-identical for identical input, on every platform.
- **Never crash on a log.** Torn lines, weird encodings, and unknown line
  shapes are normal input; degrade gracefully and say so in `warnings`.
- Code comments and doc comments are written in English.
- Keep the three READMEs aligned: `README.md`, `README.zh.md`, and
  `README.ja.md` are line-for-line translations (English is authoritative).

## Reporting bugs

Please include `gcgauge --version` output, the exact command, and a minimal
log snippet that reproduces the problem (a dozen lines is usually enough —
GC logs rarely contain secrets, but trim hostnames if yours do).

## Security

Please do not report security issues in public GitHub issues. Use GitHub's
private vulnerability reporting on the repository instead.
