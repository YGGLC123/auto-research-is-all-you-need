---
name: research-loop
description: Drive an auto-research project forward autonomously across turns and sessions — one bounded step per tick, state written back every tick, wakeup re-armed, blockers resurfaced instead of skipped. Use when the user asks for autonomous/overnight/continuous progression of a research project, or when pilot hands over multi-tick work. Do not use for one-off questions or when the user is actively steering each step.
---

# Research Loop

You are the tick executor of Layer 2 in [loop-protocol.md](../../docs/loop-protocol.md). One tick = one bounded, verifiable step. The disk is the loop's memory; the wakeup is its heartbeat.

Apply [verify-and-stop](../verify-and-stop/SKILL.md), loaded once and reused across
ticks. Unchanged valid evidence needs no rerun or fresh checksum. Check only affected
dependencies after new changes/failures; never add a full check just to end a tick/phase.

```
CLI  = py ../../scripts/research_os.py      (POSIX: python3 ../../scripts/research_os.py)
TURN = py ../../scripts/turn_runtime.py     (turn / lease / role commands)
ROLE = py ../../scripts/role_runtime.py role  (registry / hydrate / dispatch / receipt / instance)
```

## Entering the loop

1. Confirm the project root and that the user actually asked for autonomous progression (an explicit request, a standing instruction in state, or pilot's handover). If SessionStart printed `RECOVER_TURN_REQUIRED`, `REHYDRATE_REQUIRED`, or `GRAPH_REBUILD_REQUIRED`, clear it first — `TURN lease status` → `TURN turn probe`, take or recover the write lease, resume the OPEN turn by replaying its manifest under the same idempotency key; a loop that ticks without the lease has its writes refused. `GRAPH_REBUILD_REQUIRED` is cleared by `py ../../scripts/research_graph.py rebuild --root <root>` before the first tick: every graph reading and every role envelope's `graph_context` comes out of `graph/current.json`, and a stale projection answers with yesterday's relations instead of failing. Take the badge (`CLI session <root> ensure --intent "loop" `), then set `CLI update <root> --loop-mode autonomous --wakeup 0`.
2. If state is v1-family, `CLI migrate <root>` first.
3. Announce the loop contract to the user once, in two plain sentences: what will advance, what stops it, and that **saying "停" or "先别跑了" pauses it** — the user pauses by asking, and you run `update --loop-mode manual`. Do not teach them the flag, and do not recite the tick mechanics; everything else about entering the loop stays on your side of the line ([pilot](../pilot/SKILL.md), *How the user experiences this*).

## The tick (repeat every wakeup)

1. **Reload truth**: `CLI context <root>`. Never act from conversation memory; after compaction this digest plus files is all there is. Consume logs catalog-first: the digest shows your unread directory; open only what matters via `CLI events read --reader <W##> [--type …]` — full-log re-reads are a hygiene violation. Poll the mailbox: `py ../../scripts/collab.py list <root> --inbox` (claim what is addressed to you; ack what came back).
2. **Blocker pass**: for each unresolved blocker, check whether its condition changed (user replied in this conversation, a capability now probes `available`, an external job finished — reconcile by `run_id`). Resolvable → `CLI blocker resolve --id … --note …`, then promote its `queued_action` into `next_actions`. Still stuck → leave it, but include it in your tick summary. `type=user_decision` blockers older than one tick deserve a push notification where the host supports it.
3. **Pick work**: first non-blocked `next_action`; if none, promote from `backlog`; if still none, derive from the highest-risk open gate ([stage-routing.md](../../docs/stage-routing.md)). Everything blocked → write a blocker digest for the user and lengthen the wakeup.
4. **Execute one bounded step** through the owning phase skill. Bounded = completable within this tick with a verifiable artifact or a clean failure. **Register before running** ([../../docs/attempt-tree.md](../../docs/attempt-tree.md)): `CLI tree <root> register --kind attempt --parent <route> --title …` (or `start` an already-registered node) before executing, `close --status … --result …` or `note` before write-back. A tick that cannot name its node is not a valid tick. Probe capabilities before relying on them; obey `on_missing`.
5. **Write back**: `CLI update <root> --tick --gate … --clear-next --next …` + `CLI event --type loop_tick --summary "<what advanced / what failed / what's next>"`. **Do not run `graph derive` in a tick**: since v3 each host CLI derives its own rule subset the moment its own transaction succeeds — `narrative commit`, `figure_router quick --promote-current`, `CLI tree close`, `CLI asset add`, `procedures claim verdict` / `run finish` — so the edge exists before you would have asked for it, and the commit payload reports `graph{derived, added, retracted}`. A hand `derive` is a no-op that hides the only interesting case, an automatic trigger that stopped firing (which leaves a `GRAPH_DERIVE_FAILED` line in `graph/incidents.log` and never fails the host transaction). Failed gate → loop-back rules apply (stagnation budget → user_decision blocker). Then **dispose of this tick's narrative delta**: `TURN turn probe`, and if the manifest is narrative-bearing, rebuild the chronicler (`TURN role hydrate chronicler` → spawn **research-chronicler**), send one `CHRONICLE_TURN/v1` envelope, and record its four-field proposal with `TURN turn record-disposition --manifest … (--ops-file … --meta-file … | --no-change --reason …)`. The chronicler proposes; the CLI writes and constructs the receipt. A tick that changed the story and left no receipt cannot knock off — `turn finalize` prints `CHRONICLE_REQUIRED` and that is the next tick's first work item ([narrative-contract.md](../../docs/narrative-contract.md), Layer 0 of [loop-protocol.md](../../docs/loop-protocol.md)).
6. **Re-arm**: schedule the next wakeup, then `CLI update <root> --wakeup 1`.
   - claude-code: `ScheduleWakeup` with the full loop prompt (this skill's invocation + project root) so a compacted session still resumes correctly. Delay policy: awaiting an external process → match its real cadence; otherwise idle-tick 1200–1800 s. Never poll harness-tracked background work.
   - codex: schedule via its goals/cron mechanism if present; otherwise end the turn with the exact resume command so the user (or the next session) can re-enter with one line.
7. **Report — in the user's language, not the machine's**: one short user-visible summary per tick — advanced X, blocked on Y, next Z — written the way rule 5 of *How the user experiences this* ([pilot](../pilot/SKILL.md)) fixes it. One sentence of what actually moved, in plain words; **no turn ids, manifest digests, receipt states, `K##` codes, `ro:` addresses, edge counts, or CLI output**, and no internal id without its title beside it. Those live in the event log, which is where the user goes if they ask. A blocker in the summary is "卡在什么上 / 我先做了什么 / 需不需要你", and a `user_decision` blocker is asked as background + two or three options + your recommendation, answerable in one sentence (rule 3) — a tick never hands the user a command to run. No silent ticks, and equally no tick that reports its own bookkeeping as progress. If this tick closed a node `success/proven` with a transferable lesson, sediment it now: `py ../../scripts/growth.py propose …` with provenance ([growth-layer.md](../../docs/growth-layer.md)); at milestones run the Reflector pass (a dedicated subagent auditing what deserves propose/use feedback) instead of sedimenting inline. A generative tick — drafting, figure production, note-taking — closes as **generate → record → continue**; it gets no cold check and no `doctor` run ([verification-policy.md](../../docs/verification-policy.md)). When a tick closes a core object, reuse a current independent key if available; otherwise obtain one affected-object check: research-reproducer for computation, research-verifier for non-mechanical criteria, research-refuter only for a central theorem or fragile object.

## Role fleet discipline (角色分控)

The chronicler is one of **five standing roles** ([role-fleet.md](../../docs/role-fleet.md)); the loop treats them all the same way.

1. **First tick of a session** — `ROLE status`. Rehydrate only the `hot` roles the tick actually needs (`ROLE hydrate <role>` → spawn its agent → `ROLE instance set <role> --agent-id …`, so later ticks in this session continue the instance by `SendMessage` instead of respawning). Any `DISPATCH_UNANSWERED` row is the tick's first work item, ahead of new work.
2. **Every dispatch is booked in the same tick**: `ROLE dispatch <role> --kind …` → the role's ≤30-line proposal → `ROLE receipt <role> --message-id … --file …`. `ANSWERED_NO_CHANGE` is a real receipt; a tick that dispatched and did not book has not written back.
3. **Never two live dispatches to one role**; a tick that wants breadth dispatches *different* roles in one round ([workflows/role-fanout.md](../../workflows/role-fanout.md)), never two lanes of the same one.
4. **Cross-role needs travel as `K##` tasks through you**, never role-to-role; conflicting proposals are adjudicated by the loop with a decision record, not negotiated between agents. Edges a role proposed are applied by you with `graph link` (`role receipt` books what resolves and parks the rest; `graph link --replay-pending` clears the parked ones). You do **not** hand a role its graph context: `ROLE dispatch` fills `inputs.graph_context` from `current.json` itself ([v3-contract.md](../../docs/v3-contract.md) §3) — the refs' node views, the conflicts touching that role's own edge classes, and its pending summary, inside 40 lines.
5. **Roles are not judges.** Rehydrating a role is cheap bookkeeping; spending a verifier, refuter, or reproducer is a gate act ([verification-policy.md](../../docs/verification-policy.md)) and stays one-shot.

## Stopping

Stop (and `CLI update --loop-mode manual --wakeup 0` + `CLI event --type loop_stopped`) when: `next_actions` and `backlog` are empty with no open gates; the user pauses or redirects; a required authorization is absent and everything else is done; or a `stop_conditions` entry in state holds. When stopping on completion, run the exit gate of the current stage first so the stop is a checkpoint, not an abandonment.

## Discipline

- One tick, one step: do not chain phases inside a tick; the write-back between steps is what makes the loop crash-safe.
- Stagnation is information: three failures on one gate is a decision point, not a fourth attempt.
- The loop never relaxes gates to keep moving — honest blockage beats fake progress. The turn gate included: `TURN turn escape --reason "…" --user-approved` is available **only** when the user explicitly authorizes skipping it, writes an incident rather than a receipt, and is never a way for an autonomous tick to unblock itself.
- Verification is a gatekeeper, not a per-tick tax: reuse current evidence. `CLI doctor` is only for a relevant package/host change or suspected installation fault when targeted checks are insufficient, or an explicit request; phase exit, snapshot and submission do not mandate a fresh run. Missing required evidence remains unchecked, never reported as a pass.
- Long/wide computation (>30 min or >8 workers) goes to the compute server under its protocol (register first, tmux, checkpointed) and is reconciled by `run_id` on later ticks, never re-launched blindly.
