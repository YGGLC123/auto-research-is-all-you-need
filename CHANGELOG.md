# Changelog

## 3.1.0 — 2026-10-02 — first public release

Versions 1.x through 3.0 were developed privately on real research projects. This release
is the first public one.

- **Research map** (`scripts/research_map.py`, `narrative map`). A single-file page that shows
  what changed since your last view, what each claim stands on, and where it trips your
  taste rules, with a staleness alarm and deep links. It exports to markmap, JSON Canvas and
  XMind, and imports edited mind maps back as proposed patches.
- **Taste ledger** (`scripts/taste_ledger.py`, `narrative taste`). Sourced, append-only taste
  rules: harvest candidates from decisions, accept them, and check story nodes and manuscript
  files against them. A hook blocks direct writes.
- **Demo project** (`examples/build_demo.py`). A fictional paper and one night of AI edits,
  built entirely through the plugin's own CLIs.
- **One-command self-tests** (`scripts/run_selftests.py`).
- **Claim-audit and rebuttal rules** adapted from ARIS, with the deterministic
  evidence pre-check (`scripts/evidence_check.py`).
- **Portability.**
  - Hooks find a working Python 3 on any OS (`hooks/run-python.sh`), and their timeouts are
    now given in seconds.
  - Capability probes resolve `{plugin}` paths and import Python packages directly
    (`python-import`).
  - Marketplace manifests for Claude Code and Codex.
