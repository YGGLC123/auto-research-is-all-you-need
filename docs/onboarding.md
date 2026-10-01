# Onboarding — install and run on both hosts

Requirements: Python 3.10 or newer (the control plane is stdlib-only), git, and Claude Code or
Codex. Optional tools (matplotlib, LaTeX, Graphviz, …) are probed at runtime; whatever is
missing degrades loudly instead of failing silently. `pip install -r requirements.txt` adds
pypdf, which the PDF delivery checks use; without it, that self-test suite reports itself as
skipped.

## Claude Code (primary host)

**From the marketplace (recommended).** Inside Claude Code:

```
/plugin marketplace add https://github.com/YGGLC123/auto-research-is-all-you-need
/plugin install auto-research@auto-research-is-all-you-need
```

The short form `YGGLC123/auto-research-is-all-you-need` clones over SSH, so it needs a
GitHub SSH key; the HTTPS URL works for everyone. Start a new session so the skills, hooks
and agents load.

**From a clone.** `.\install.ps1` (POSIX `./install.sh`) copies a frozen, doctor-verified
snapshot into `~/.claude/skills/auto-research`, which auto-loads as `auto-research@skills-dir`.
`status` compares versions, `uninstall` removes cleanly, `release` builds a portable zip
(`claude --plugin-dir auto-research-<version>.zip` on any machine). Edits in the clone never
affect the live install until you rerun `install`. To try it without installing:
`claude --plugin-dir <clone-or-zip>`.

What you get: skills under `/auto-research:*` (entry point `/auto-research:pilot`), nine
research agents, and SessionStart/PreCompact hooks that inject the project digest whenever the
working directory (or a parent) contains `.research-os/`. The hooks find a Python 3 by
themselves (`python3`, then `python`, then `py -3`).

## Codex (compatibility host)

The repository also ships `.codex-plugin/plugin.json` and per-skill `agents/openai.yaml`.

```
codex plugin marketplace add YGGLC123/auto-research-is-all-you-need
codex plugin add auto-research@auto-research-is-all-you-need
```

Codex has no hooks: run the digest yourself at the start of a thread —
`python <plugin>/scripts/research_os.py context <project-root>`.

## First run on a new project

Run `/auto-research:pilot` and say what you are working on. It inspects the root and, if you
want durable state, routes to `research-bootstrap`, which runs
`research_os.py bootstrap <root> --title "…" [--stage … --paper-type … --layout standard --init-git]`.

## A project with older state (v1 → v2)

1. `python scripts/research_os.py context <root>` — the digest flags a v1 schema.
2. `python scripts/research_os.py migrate <root>` — lossless: free-form stages land in
   `stage_detail`, overflow next_actions in `backlog`, missing fields get defaults, gates
   normalize to structured objects. The original is kept as `state.v1.bak.json`, and an event
   records the migration.
3. Old snapshot folders stay untouched; from now on use `snapshot --label`.

## Self-check anytime

```bash
python scripts/run_selftests.py                    # every self-test suite, one verdict
python scripts/research_os.py doctor               # manifests, registry, hooks, skills, scripts, links, host probes
python scripts/lint_package.py                     # link lint only
python scripts/research_os.py probe --phase evidence   # per-phase capability verdicts
```

On Windows, `py` works wherever these examples say `python`.
