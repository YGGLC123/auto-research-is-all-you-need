# Collab Protocol — Codex ↔ Claude on one project

Both hosts share one filesystem, one `.research-os/` state, and one accounting system. Collaboration is structural: **badges (W##), booked tasks (K##), bounded returns** — never shared conversation memory.

## Badges: numbered sessions

Real work sessions register on entry (`research_os.py session <root> ensure --intent "…"` — pilot/loop/phase entries do this; casual chats never touch those entries, so they get no badge). Headless executors register with `--headless`. The CLI is the single allocator (write-lease protected): W numbers never collide across hosts. Every event, tree node, and task carries the badge — cross-host reconciliation reads the ledger, not memories.

## The mailbox: K tasks

`.research-os/collab/tasks/K##.json`, state machine `open → claimed → done|failed → returned` (poster acks). Booked via `collab.py post` with: `--to codex|claude-code|any`, `--lane mailbox|direct`, `--title`, `--spec` (**self-contained — the executor has no conversational context; inputs must exist on disk or post is refused**), `--input` (repeatable), `--expect`, `--node` (attempt-tree link).

Guards, all CLI-enforced: claims only by the addressed host; returns only by the claimer; a successful return names ≥1 existing artifact path; **digest hard-capped at 30 lines**; every transition appends an event that lands in other readers' unread catalogs.

## Two lanes

- **mailbox (default)** — durable, asynchronous. The Claude loop polls `collab.py list --inbox` each tick; the Codex side polls via its scheduled run (or a Windows Task Scheduler entry running `codex exec` periodically). Judgment-heavy work goes here so the interactive 主控 handles it inside its own rich context.
- **direct (fast lane)** — bounded, mechanical, single-agent jobs only (a plot, a conversion, a script run). The poster invokes the other host headlessly, e.g.:

```bash
env -u CLAUDECODE -u CLAUDE_CODE -u CLAUDE_PROJECT_DIR -u CLAUDE_SESSION_ID AR_HOST=codex \
  codex exec --skip-git-repo-check --sandbox workspace-write -C <project> "<executor brief>" < /dev/null
```

  (Strip the caller's host env and set `AR_HOST`; close stdin; `--skip-git-repo-check` outside trusted repos. Reverse direction: `claude -p`.) Verified live 2026-07-19: `codex exec` runs a full tool-using, plugin-loaded agent; treat it as **single-loop** — no subagent fan-out. **Still books a K task first** (`--lane direct`) and goes through claim/return: the fast lane changes the transport, never the accounting. Executor brief = the four steps: `session ensure --headless` → read the task JSON → `claim` (with `AR_SESSION=<W##> AR_HOST=<host>`) → execute → `return --digest … --artifact …`.

## Lane selection

| Work | Lane |
|---|---|
| Judgment-heavy (claims, adjudication, rewrites) | mailbox → the other host's interactive 主控 |
| Mechanical, bounded, single-agent | direct |
| Needs subagent fan-out | keep on the Claude side (its headless mode can spawn agents; Codex exec cannot) |

## Red lines

1. **No unbooked cross-host work** — direct included. No K number, no execution.
2. **No re-entrant direct calls**: an executor that needs the caller posts to the mailbox; it never headless-execs back (deadlock/nesting).
3. **Digest cap is absolute** — full material to disk, pointer in the task.
4. Direct calls spend the other account's quota: small bounded jobs only; batch the rest through the mailbox.
