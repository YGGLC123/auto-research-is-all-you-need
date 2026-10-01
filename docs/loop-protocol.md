# Loop Protocol — how this system keeps moving

v1 had no loop: one route, one execution, then silence; work died at permission gates. v2 makes progression a mechanism at four nested scopes — a knock-off gate inside each turn, gate loop-back inside a session, autonomous ticks across turns, and hooks at the session boundary. `state.loop` is the single source of loop truth; the turn gate has its own runtime under `.research-os/runtime/`.

Verification follows [verify-and-stop](../skills/verify-and-stop/SKILL.md): reuse
current results and read only relevant deltas. A tick or phase boundary alone does
not invalidate evidence or require fresh hashes, a new verifier, or a full doctor.

**All four layers are invisible on the user's side.** Turns, manifests, receipts, leases, stagnation counts, wakeups, the SessionStart panel and the `GRAPH …` digest are the controller's instruments; the user never sees, learns, or types any of them, and never pauses the loop with a flag — they say so, and the controller runs the command. What surfaces is the consequence in plain words, and only when it carries information: what advanced, what is stuck, what is needed from them. The binding rules are the six in *How the user experiences this* ([skills/pilot/SKILL.md](../skills/pilot/SKILL.md)); a decision this protocol routes to the user is asked as background + two or three options + a recommendation, never as a queue dump or a command to run.

## Layer 0 — Knock-off gate (within a turn)

Owned by the turn runtime (`turn_runtime.py`), specified by §6 of [narrative-contract.md](narrative-contract.md). It converts "remember to capture what changed" from a habit into a mechanical invariant: **a turn closes only when every one of its input manifests carries exactly one valid receipt.**

1. The CLI freezes an `InputManifest` per turn — it computes the digest itself; a model never supplies one. Whether the manifest is *narrative-bearing* has a machine floor (`bearing_globs`, narrative CLI events, claim-state changes, decision records, branch merge/kill/promote, role changes); a model may raise `false → true` and never the reverse.
2. A bearing manifest is dispatched to the chronicler as one `CHRONICLE_TURN/v1` envelope, whose reply is a **proposal** in four fields. The main controller records it via `turn record-disposition` (with ops, or `--no-change --reason …`). The chronicler never writes; the CLI writes and constructs the receipt. **Dispatch and receipt are paired for every standing role, not only the chronicler** ([role-fleet.md](role-fleet.md)): `role dispatch` mints the `ROLE_TASK/v1` envelope and registers it, the role replies with a ≤30-line proposal, and `role receipt` validates it, merges the memory delta, and advances the cursor — a dispatch left un-receipted is an incident (`DISPATCH_UNANSWERED`), never a silent drop.
3. Four receipt states exist and no others: `NO_INPUT_CHANGE` and `ACK_NON_NARRATIVE` (written by the CLI unprompted), `NARRATIVE_COMMITTED{commit_ids[]}`, `NARRATIVE_REVIEWED_NO_CHANGE{reason}`. A bearing turn that genuinely changed no node closes honestly on the fourth — the invariant demands a *disposition*, never a manufactured node.
4. `turn finalize` (Stop hook, no arguments) rescans inputs: new delta after the freeze ⇒ a fresh manifest needing its own disposition; a manifest missing its receipt ⇒ exit 1 with `CHRONICLE_REQUIRED turn=… manifest=…`. `--check-only` diagnoses without closing.
5. **Single writer.** `runtime/lease.json` holds a 15-minute lease with a heartbeat on every write command; a second session's writes are refused (`PROJECT_LEASE_HELD_BY W##`). A dead holder or expired lease is a `recovery_takeover`; an unclear-but-unexpired one stays read-only until an explicit `lease take --force --reason …`, which writes an incident.
6. **Host reality.** Stop can be bypassed (Ctrl+C, `--no-hooks`, a killed process), so `turn finalize` is best-effort and an unclosed turn is not a contradiction — it is an incident the next SessionStart must repair. Sessions die; turns do not. `turn escape --reason … --user-approved` is the only sanctioned bypass, it writes to `incidents/`, and it is never a receipt.

## Layer 1 — Gate loop-back (within a session)

Owned by `/auto-research:pilot`. After any phase skill exits:

1. Read the gate it wrote. `passed` → route the next open gate per [stage-routing.md](stage-routing.md). `failed` → route **back to the owning skill** with the failure evidence, append event `loop_back`, and increment `loop.stagnation_count`.
2. When the same gate fails `loop.stagnation_budget` times (default 3), stop retrying: raise a blocker (`type=user_decision`) carrying the best two options and the evidence, and move to other open work. Stagnation must convert to a decision request, never to an infinite retry or a silent stall.
3. Any progress on a different gate resets `stagnation_count` to 0.

## Layer 2 — Autonomous ticks (across turns): `/auto-research:research-loop`

One tick =

1. `research_os.py context <root>` — reload truth; never work from conversation memory.
2. Resurface unresolved blockers: for each, check whether its condition changed (user replied, capability appeared via `probe`, external job finished). Resolvable → `blocker resolve` and promote its `queued_action` into `next_actions`.
3. Pick the first non-blocked `next_action` (promote from `backlog` if empty). If every action is blocked, emit a user-facing blocker summary and lengthen the wakeup interval.
4. Execute exactly one bounded step via the owning phase skill — **inside an attempt-tree node** ([attempt-tree.md](attempt-tree.md)): `tree register`/`start` before executing, `close`/`note` before write-back. A tick that cannot name its node is not a valid tick.
5. Write back: `update --tick` + gates + events; refresh `next_actions`. **Book every role dispatched this tick before the write-back**: each reply becomes a `role receipt` (`ANSWERED | ANSWERED_NO_CHANGE | REJECTED`), and any edge a role proposed is applied by the controller with `graph link` — a tick that dispatched a role and booked no receipt has not closed ([role-fleet.md](role-fleet.md)). **No `graph derive` in a tick either**: since v3 each host CLI derives its own rule subset after its own transaction succeeds ([v3-contract.md](v3-contract.md) §2), so a tick that commits a narrative node or promotes a figure already has the edge — running `derive` by hand is at best a no-op and at worst hides the fact that the automatic trigger stopped firing. **No `doctor` per tick** — reuse current checks at phase exit, snapshot and submission; a full self-check needs a concrete package/host reason ([verification-policy.md](verification-policy.md)); a generative tick closes as generate → record → continue.
6. Re-arm the wakeup (below) and set `wakeup_pending=1`.

**Stop conditions** (any → `update --loop-mode manual --wakeup 0`, final event `loop_stopped`): `next_actions` and `backlog` both empty with no open gates; user pause; required authorization absent; `stop_conditions` entries in state evaluate true.

## Host bindings

| Concern | claude-code | codex |
|---|---|---|
| Re-arm wakeup | `ScheduleWakeup` (delay per what you await: external state → match its cadence; fallback heartbeat 1200s+). Prompt must carry the loop instruction verbatim, incl. project root. | Codex goals/scheduled runs; if unavailable, the loop is user-cadenced: end each turn with the exact resume command. |
| Survive compaction | `SessionStart(compact)` hook auto-injects the `context` digest; wakeup re-enters the loop. | Re-read state at thread start (the digest is one command). |
| Notify on user-decision blockers | `PushNotification` when available; always also state + digest. | End-of-turn summary. |

Claude-side discipline matches the host repo's own rule: state on disk + one pending wakeup at all times while `loop.mode=autonomous`; compaction is then harmless. Before a deliberate `/compact`, update state first and confirm `wakeup_pending=true`.

## Layer 3 — Session boundary (hooks)

`hooks/hooks.json` runs `research_os.py context --quiet-if-absent` on `SessionStart` (startup | resume | compact). A project in cwd (or above) always greets the session with its digest — stage, open gates, **unresolved blockers**, next actions — so no host or window switch loses the thread. Non-project directories stay silent.

After the digest, the turn runtime checks for an OPEN turn and a stale lease and prints `RECOVER_TURN_REQUIRED` / `REHYDRATE_REQUIRED` when either needs attention. **Hooks only raise flags** — they never spawn an agent, send a message, or write authoritative state. Acting on the flag is the skill's mandatory first step: take or recover the lease, then resume the OPEN turn by replaying the same manifest under the same idempotency key (a commit already on disk is repaired, not repeated), running `narrative journal replay` first if a commit landed without its receipt.

The same hook prints the research graph's one-line digest ([v3-contract.md](v3-contract.md) §6), read straight out of `graph/current.json`:

```
GRAPH edges=44 conflicts=0 pending=0 gone=0 derive: last 2026-08-25T15:36 by narrative:commit
GRAPH_REBUILD_REQUIRED current.json is behind the event stream (41 of 44 events) -- first step: `graph rebuild`
```

The first line is silent in a project that never linked anything. **The second line, when it appears, is a mandatory first step, not a note**: run `py <plugin>/scripts/research_graph.py rebuild --root <root>` before any other graph read, and before dispatching any role. The reason it must be first is that everything downstream reads the projection rather than the event stream — the views, the coverage report, the role envelopes' `graph_context`, the HTML page — so working on a stale `current.json` does not fail loudly, it quietly answers "what is this claim standing on" with yesterday's answer. The hook cannot rebuild it itself: a session-start callback may not take the project write lock, and hooks never write authoritative state. `GRAPH_REBUILD_REQUIRED` also appears with no counts when an edge ledger exists but was never projected at all — the shape an older project has the first time it is opened under v3.

## Anti-patterns (all observed in v1's real traces)

- Dying at a permission gate (one real run simply ended at "git push needs user approval"). → Raise blocker, keep working elsewhere, resurface each tick.
- Retrying a failed gate forever or abandoning it silently. → Stagnation budget converts to a user decision.
- Polling wakeups every 60s "to stay warm". → Wakeup cadence matches what is actually awaited.
- Escorting every tick with a verifier, a refuter, and a `doctor` run. → Verification is a gatekeeper at four doors, not a chaperone ([verification-policy.md](verification-policy.md)).
- Trusting conversation memory after compaction. → Only the digest + files are truth.
- Letting the story drift with no ledger entry, then reconstructing it months later from thirty dated markdown files (a real project's history). → Layer 0: every turn gets a disposition, and "nothing changed" is a receipt, not a silence.
- Manufacturing a node so a bearing turn can knock off. → `NARRATIVE_REVIEWED_NO_CHANGE` exists precisely so honesty is the cheap path.
