# Changelog

## 3.2.0 — 2026-10-02 — the map, rebuilt

- **A new research map page.** It is rebuilt with React, React Flow, Radix, Tailwind and lucide
  icons (source in `web/research-map`) and still compiles to one offline HTML file. New in the
  page:
  - *the overnight receipt*: every commit since your last view, with the benching commit printed
    in red and a *Needs your call* list. Clicking a line flies the map to that card;
  - a red-pen redline of each rewritten statement;
  - rubber stamps on the results that need a decision;
  - a one-click language switch, a dark mode and adjustable text size.

  `render --classic` keeps the old page as a fallback.
- **Plain words.** The page, the READMEs and the classic labels use one vocabulary: *Benched*
  (被雪藏) replaces "lever debt"; the others are *Called out*, *No receipts*, *Needs a figure*,
  *Open bet*, *Your call* and *Not your style*. The ledger's event name stays `LEVER_DEBT`.
- **A promo film** rendered with Remotion from the page's own components, in Chinese and
  English, 16:9 and 9:16 (`web/research-map/README.md`).
- The map model now carries the night's commits (`log`), the builder's time zone and each
  taste rule's quote.
- Taste-rule excerpts end on whole English words.
- The page's data script escapes every `<`, so markup inside a claim cannot close or reopen it.

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
