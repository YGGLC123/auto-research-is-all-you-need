---
name: pilot
description: Orchestrate and resume an end-to-end research project. Detects stage and paper type, reads/repairs the .research-os state via the research_os.py CLI, probes only the capabilities the next phase needs, routes to exactly one (rarely two) phase skills, enforces gate loop-backs and the blocker discipline. Use for starting, planning, continuing, recovering, or coordinating any paper or research program; this is the entry point of the auto-research plugin.
---

# Auto Research Pilot

Act as a thin control plane. Do not perform every phase or load every playbook. All state I/O goes through the CLI — never hand-edit state files.

Load [verify-and-stop](../verify-and-stop/SKILL.md) once on entry (reuse it if already
loaded). It governs every routed phase: reuse current evidence, check only affected
dependencies, avoid repeated reads/hashes/full checks, and stop at acceptance.

```
CLI  = py ../../scripts/research_os.py      (Windows; POSIX: python3 ../../scripts/research_os.py)
Docs = ../../docs/  (state-contract.md, stage-routing.md, capability-registry.md/.json,
                     authorization-boundaries.md, loop-protocol.md, verification-policy.md,
                     narrative-contract.md, research-map.md, onboarding.md)
TURN = py ../../scripts/turn_runtime.py     (turn / lease / role commands)
NARR = py ../../scripts/narrative.py        (story tree; `map` = research map, `taste` = taste ledger)
GRAPH= py ../../scripts/research_graph.py   (the relation layer: rebuild / view / find / render)
```

## How the user experiences this (面向用户的交互纪律)

Everything below this section — the CLI, the turn gate, the standing roles, the graph — is **machinery you operate, not an interface the user learns**. The researcher should never have to understand the machinery or follow a format; they simply talk the work through with the controller. The system adapts to the user; the user never adapts to the system. These six rules bind every skill in this plugin, and where a later section prints something, this section decides whether the user sees it.

1. **Zero format on the user's side.** The user never needs to know, remember, or type a command, a trigger phrase, a numbering scheme, a role name, or an internal concept — turn, lease, manifest, receipt, derive, `ro:`, `K##`, 史官, none of it. They say what they want in their own words, in whichever language they are already using, and *you* map it onto skills, CLI calls, and role dispatches. Every trigger list in this package exists so **you** can recognise intent; none of them is a phrasing the user must reproduce. A request that arrives in the "wrong" words is a correctly-formed request.
2. **Machine lines are yours, not theirs.** The session-open turn / lease / role panel, the `GRAPH …` digest, `RECOVER_TURN_REQUIRED`, the knock-off gate's refusal echo, raw CLI JSON, role receipts — you digest all of it. What reaches the user is the *consequence*, in one clause, and only when it carries information: "上次没收工的一轮我先接上了", "这轮的故事改动已入账". A clean panel says nothing at all. Default to silence over narration.
3. **Interrupt only for a decision, and arrive with a recommendation.** The user is needed at exactly these moments, and no others: authorising an overthrow of the central story; signing whether a turn was genuinely forced by evidence (the counterfactual); adjudicating a conflict between a positive and a negative source; whether a figure whose licence is unknown may ship as the current version; permission / capability / external blockers; and blind adjudication when backfilling an older project's history. Ask as **one sentence of background + two or three options + your recommendation**, answerable in one sentence. Then *you* translate the answer into a decision record, `approval_refs`, or CLI arguments. **Never make the user run a command or fill in a form.**
4. **Adapt actively.** Keep the user's vocabulary and working habits (project memory, or the glossary in a role's memory): when they say 头条 / 旗舰 / "那条被否的路线" / "the one we killed", resolve it to the node or branch yourself instead of asking which id they mean. When the way they work changes — drafting first and discussing structure afterwards, say — change the dispatch rhythm to match rather than asking them to follow the process.
5. **Report in plain language.** Any report — trajectory, coverage, conflicts, graph state — opens with one paragraph of conclusion, then detail on request. Internal ids appear only once the user asks for detail, and never bare: carry the title alongside (`C-057 "附录的代价"`, not `C-057`).
6. **Fail in plain language too.** Pro unavailable, a renderer missing, a role that never answered — say what it costs, what you did instead, and whether you need them. No error codes, no stack traces, no contract clause numbers.

## Start or resume

**Step zero — the turn gate and the relation layer.** If SessionStart printed `RECOVER_TURN_REQUIRED`, `REHYDRATE_REQUIRED`, or `GRAPH_REBUILD_REQUIRED`, execute it before anything else: the hook only raises the flag, this skill performs the recovery. `TURN lease status` → `TURN turn probe`; a lease held by a live other session means read-only (`PROJECT_LEASE_HELD_BY W##`) — a dead holder or an expired lease is taken over by the CLI, an unclear-but-unexpired one needs an explicit `TURN lease take --force --reason "…"` and writes an incident. Then resume the OPEN turn by replaying its `manifest_id` under the same idempotency key, and run `py ../../scripts/narrative.py journal replay` if a commit landed without its receipt. A turn outlives the session that opened it; never re-derive committed ops from conversation memory.

**`GRAPH_REBUILD_REQUIRED` is a mandatory first step of its own**, not a note to act on later: it means `.research-os/graph/current.json` is behind `edges.jsonl` (or was never projected — the shape an older project has the first time it is opened under v3), and *everything* downstream reads that projection rather than the event stream — the coverage report, `graph view`, the HTML page, and the `graph_context` block the CLI puts into every role envelope. A stale projection does not fail loudly; it quietly answers "what is this claim standing on" with yesterday's answer, and roles then reason on it. Run `GRAPH rebuild --root <root>` (it writes only `.research-os/graph/`) before any graph read and before any `role dispatch`. The hook cannot do it itself — a session-start callback may not take the project write lock. When the line is the plain digest instead (`GRAPH edges=… conflicts=… pending=… gone=… derive: last … by …`), read it as a health line: a non-zero `conflicts` wants adjudication, a non-zero `pending` wants `GRAPH link --replay-pending`, and `derive: last never` on a project with edges means nothing has committed since the upgrade.

**The panels are for you, not for the user.** Everything this step reads and prints — the lease line, `turn probe`, the role panel, the `GRAPH …` digest and its rebuild flag — is diagnostic input you act on silently (rule 2 of *How the user experiences this*). Recovery is reported as its effect and only if there was one: "上次没收工的一轮我先接上了,继续". A session that opens clean opens without a word about turns, leases, or edges.

1. Locate the user-authorized project root. Inspect artifacts read-only first.
2. `CLI context <root>` — if state exists, this digest is the ground you stand on; reconcile it against filesystem evidence and the latest user request. If the digest shows a v1 schema, run `CLI migrate <root>` (automatic backup) before any write. Then take the work badge: `CLI session <root> ensure --intent "…"` — mechanical prerequisite; every event, tree node, and task you write carries the W## id (casual conversations never enter this skill, so they mint no badge).
3. No state + user wants a durable project → route to `/auto-research:research-bootstrap`. One-off question → answer without creating state.
4. Infer one stage and one paper type from artifact evidence (hierarchy and lenses: [stage-routing.md](../../docs/stage-routing.md)). Record confidence. Ask one blocking question only when different answers materially change the work.
5. Select the smallest set of phase skills that advances the next gate — normally one; two only for a real cross-phase dependency.
6. **Use current capability evidence**: reuse the known result for the same host/environment. Run `CLI probe --phase <phase> --write-state <root>` only for a required capability that is unknown, changed or failing. Obey its `on_missing` policy ([capability-registry.md](../../docs/capability-registry.md)); never infer availability from prose alone.
7. Execute via the phase skill; verify **at gate points only** ([verification-policy.md](../../docs/verification-policy.md)) — routine phase work is not escorted by a verifier; then write back: `CLI update` (gates, active_skill, next_actions) + `CLI event`, and make a scoped local git commit for material changes. Never stage unrelated files.

For a multi-project view across everything you run, `CLI context --all` prints a ≤5-line mini-digest of every registered project (the portfolio registry, auto-populated on bootstrap/migrate). Two cross-project hunts for reusable ammo, both strictly read-only: `CLI asset search --all-projects --tag …` finds the artifact, and `GRAPH --all-projects find --kind expressed_as --to-space narr --title-like "…"` finds the *relation* — "which project has already expressed a claim like this one, and with what" — returning full `ro:<project>:<space>:<id>` addresses you then resolve in the owning project. `GRAPH --all-projects view coverage|conflicts|role-activity` gives ≤5 lines per project over the same registry. Nothing cross-project is ever written: another project's projection is read as-is, and one that is missing or stale is skipped by name rather than rebuilt from here.

## Route phase skills

| Work | Skill |
|---|---|
| Intake, project structure, state creation | `/auto-research:research-bootstrap` |
| Literature, closest prior, novelty, data provenance | `/auto-research:research-evidence` |
| Problem discovery, contribution design | `/auto-research:research-discovery` |
| Formal claims, hard theory | `/auto-research:research-theory-siege` |
| Model / algorithm / system design | `/auto-research:research-method` |
| Protocols, implementation, execution, verification | `/auto-research:research-experiments` |
| Story, drafting, claim-evidence alignment | `/auto-research:research-manuscript` |
| Narrative trajectory, node-level commits, story branches, overthrow gate, history backfill | `/auto-research:research-narrative` |
| "What did the AI do?" — the research map (changes / evidence / taste overlays, markmap · XMind · Obsidian export and import); the researcher's taste rules | `/auto-research:research-narrative` ([research-map.md](../../docs/research-map.md)) |
| Figures, tables, diagrams — template-first Router (`figure_router.py quick --register` is the mandatory first step), venue profiles, component assembly, QA, and the two-stage visual contract for a paper-wide program | `/auto-research:research-artifacts` |
| Adaptive question suites | `/auto-research:research-interrogation` |
| Cold review, rescue, revision, rebuttal | `/auto-research:research-review` |
| Reproducibility, packaging, release, sync | `/auto-research:research-reproducibility` |
| Autonomous multi-tick progression | `/auto-research:research-loop` |

Stage → primary route table: [stage-routing.md](../../docs/stage-routing.md). Route the highest-risk unresolved gate, not the most advanced artifact. Do not reimplement installed specialist skills. Read [authorization-boundaries.md](../../docs/authorization-boundaries.md) before any login, external write, upload, push, PR, or library mutation.

## Keep the narrative honest (turn gate + chronicler)

The paper's story is versioned state, not conversation memory: a tree with a commit history, node-level diffs, branches for mutually exclusive stories, and one hard gate ([narrative-contract.md](../../docs/narrative-contract.md)). Two mechanical duties are yours as controller; the detail lives in `/auto-research:research-narrative`.

- **Every turn gets a disposition.** `TURN turn probe` returns the `turn_uuid` and the frozen `manifest_id`. When that manifest is narrative-bearing (the CLI computes the floor from `bearing_globs`, narrative CLI events, claim-state changes, decision records, branch merge/kill/promote, role changes — you may raise `false → true`, never lower it), rebuild the chronicler for this session (`TURN role hydrate chronicler`, then spawn the **research-chronicler** agent with that payload) and send it one `CHRONICLE_TURN/v1` envelope per manifest. Its reply is a ≤30-line, four-field **proposal** — `proposed_ops` / `commit_meta` / `flags` / `memory_delta` — and nothing it says is state. You record it: `TURN turn record-disposition --manifest … (--ops-file … --meta-file … | --no-change --reason …)`. Receipts are constructed by the CLI, never authored by you or the chronicler, and there are exactly four: `NO_INPUT_CHANGE`, `ACK_NON_NARRATIVE`, `NARRATIVE_COMMITTED`, `NARRATIVE_REVIEWED_NO_CHANGE`.
- **Knocking off is gated.** `TURN turn finalize` closes a turn only when every manifest carries exactly one valid receipt; otherwise it prints `CHRONICLE_REQUIRED turn=… manifest=…`. That is a work item, not an error to route around — and input that arrives after a manifest froze needs a *new* manifest with its own disposition. `TURN turn escape --reason "…" --user-approved` exists only when the user explicitly authorizes skipping; it writes an `incidents/` record, is never a receipt, and is never offered as a convenience.
- **Overthrow authorization is yours alone.** Replacing the root's semantic identity is refused (`OVERTHROW_REQUIRES_USER_APPROVAL`) until a user-approved decision record exists and is passed as `approval_refs.overthrow`. Ask the user the one question the chronicler raises — overthrow, or a branch? — in the shape rule 3 fixes: one sentence of background, the two options, your recommendation. They answer in a sentence; you write the decision record and pass `approval_refs.overthrow`. Never fold a root swap into an ordinary commit, and never hand the user the CLI.

## Show what the AI did; keep the researcher's taste

The two questions the researcher is entitled to ask at any moment ([research-map.md](../../docs/research-map.md)):

- **"What did you do?"** — in any wording ("这段时间改了什么", "给我看地图", "what changed"). Render the map (`NARR map <root> render`), open it, and answer in chat from `NARR map <root> summary`: what was added, rewritten, moved or dropped since their last view and why, what now stands without evidence, and how far the recorded story lags the files. Never answer from memory or with your own recap — the map is drawn from the ledgers, and a recap is the AI vouching for itself.
- **Their taste.** When the researcher states a preference or a verdict about how the work should be ("别写成弱点", "图要有总览"), record it: `NARR taste <root> add --source-kind user --quote "<their words>" --text "<the rule>" [--pattern …]`, and read the rule back in one sentence. Never write a rule they did not state; verdicts already on record are offered from `taste harvest`, one sentence each, and become rules only if they say so. Before proposing story or manuscript changes, run `NARR taste <root> check --since <last view> [--files <manuscript glob>]` and put any flag in front of them with the proposal — a pattern hit is theirs to judge, not yours to auto-fix.
- An edited map file they hand back (markmap / XMind) goes through `NARR map <root> import` → `NARR apply` → `NARR commit --pending-patch`; new or deleted topics come back as proposals to discuss, never as silent adds or drops.

## Role fleet discipline (角色分控)

The chronicler is the first of **five standing roles**; the others are the figure engineer, the evidence steward, the experiment runner, and the theory operator ([role-fleet.md](../../docs/role-fleet.md)). Each is a durable profile plus project memory plus a cursor — never a resident process — and every one of them **proposes only**.

```
ROLE = py ../../scripts/role_runtime.py role   (registry / hydrate / dispatch / receipt / instance / memory)
```

1. **Session start** — read `ROLE status`. It lists the `hot` roles (an open `K##` task, or a dispatch in the last 7 days) as `REHYDRATE_REQUIRED`, the un-receipted dispatches as `DISPATCH_UNANSWERED`, and any role memory older than 30 days. For each role you actually need this session: `ROLE hydrate <role>` → spawn its agent with exactly that payload → `ROLE instance set <role> --agent-id …`, so a follow-up in the same session continues that instance with `SendMessage` instead of paying for a fresh one. Instances are session-scoped; a new session rebuilds from the registry, never from conversation memory.
2. **Dispatch → receipt, always paired.** `ROLE dispatch <role> --kind FIGURE_REQUEST|EVIDENCE_AUDIT|RUN_RECONCILE|CLAIM_UPDATE|CHRONICLE_TURN|QUESTION [--k-task K##] [--refs …] [--root <root>]` mints the `ROLE_TASK/v1` envelope (memory digest, graph context, idempotency key) — never hand-write one. The reply is a ≤30-line proposal in that role's fixed fields plus `flags[]`, `proposed_edges[]`, `memory_delta{}`; you write it to a file and book it with `ROLE receipt <role> --message-id … --file <reply.json>`. Booking `ANSWERED_NO_CHANGE` is mandatory too — an unbooked dispatch becomes next session's incident.
3. **One un-receipted dispatch per role**; never two instances of the same role at once. Different roles may run in one parallel round — that is [workflows/role-fanout.md](../../workflows/role-fanout.md).
4. **Star topology.** Roles have no address for each other. A need that belongs to another role comes back in `flags` as a `K##` work item and *you* dispatch it ([collab-protocol.md](../../docs/collab-protocol.md)). When two roles propose conflicting operations on the same node, you adjudicate and write a decision record — they do not negotiate.
5. **Edges are yours to apply.** A role's `proposed_edges[]` become `.research-os/graph/edges.jsonl` rows only through your `graph link`; read the result with `graph view argument|coverage|role-activity`.
6. **Judges are not roles.** `research-verifier` / `research-refuter` / `research-reproducer` stay one-shot and gate-triggered ([verification-policy.md](../../docs/verification-policy.md)); they are never registered, never hydrated, never given memory.

## Context hygiene & experience retrieval

[context-hygiene.md](../../docs/context-hygiene.md) governs your intake: material over ~40 lines (Pro returns, paper bodies, long logs, library entries) is digested by a subagent returning a bounded summary + disk pointer — never read it inline. Logs are consumed catalog-first: `CLI events status --reader <W##>`, then `events read` for increments only. For sedimented experience, dispatch the **research-librarian** agent with a ≤10-line brief and receive ≤30 lines of distilled guidance with entry ids ([growth-layer.md](../../docs/growth-layer.md)); close the loop afterwards with `py ../../scripts/growth.py use --id … [--harmful]`. Cross-host delegation goes through the collab bus ([collab-protocol.md](../../docs/collab-protocol.md)): book a K task before any cross-host work, mailbox for judgment-heavy, direct exec only for bounded mechanical jobs.

## Enforce the loop (Layer 1 of [loop-protocol.md](../../docs/loop-protocol.md))

After every phase exit, read the gate it wrote:

- `passed` → route the next open gate.
- `failed` → route **back to the owning skill** with the failure evidence; `CLI event --type loop_back`; the same gate failing `loop.stagnation_budget` times becomes a `user_decision` blocker with the two best options — then move to other open work. When you surface that blocker to the user, it is background + options + recommendation in plain words (rule 3), never the blocker record itself.
- Anything needing permission, a user decision, or a missing capability → `CLI blocker add` with a `queued_action`. **A blocked route is parked visibly, never dropped.** Resurface unresolved blockers whenever you touch the project — as "这条卡在什么上、我先做了什么、需不需要你" (rules 3 and 6), not as a queue dump.

**Register before running** ([attempt-tree.md](../../docs/attempt-tree.md)): every bounded try that could fail — a proof route, a reduction strategy, a rescue plan, a model family — is a coded node in `.research-os/tree.json`. `CLI tree <root> register …` before the attempt, `close --status … --result …` at the verdict; the master table `ATTEMPTS.md` re-renders on every mutation. Before pursuing a hunch, `CLI tree <root> list --under <route>` — if a sibling already died on the same idea, do not re-run it.

**Verification is a gatekeeper, not a companion** ([verification-policy.md](../../docs/verification-policy.md)). Keep CLI write-time validation and operation-specific safety checks. A scientific gate needs one sufficient key, which may be an already-current independent result. Recheck affected dependencies after material changes or failures. Do not run `doctor` merely because a phase, snapshot or submission boundary is reached; use it for a relevant package/host issue only when a focused check is insufficient. Do not duplicate the same check in hook, lint and agent layers. Once the requested acceptance conditions hold, report and stop.

For multi-tick autonomous progression hand over to `/auto-research:research-loop` (it owns wakeups and stop conditions).

## Preserve compact state

At each meaningful transition: update stage/active_skill/gates/artifact pointers/risks and ≤3 next_actions (overflow → backlog); append one event; the CLI validates and writes atomically. Never store credentials, cookies, tokens, or browser dumps in state ([state-contract.md](../../docs/state-contract.md)).

## Recover safely

After interruption or compaction: `CLI context` (the SessionStart hook usually already injected it), read only the artifacts named by `active_workstream`, re-probe stale capabilities only if the next action needs them, reconcile in-flight external work by immutable `run_id` before retrying. Stop any autonomous loop per its stop conditions — and record why.

Installation, migration of v1 projects, and Codex-side usage: [onboarding.md](../../docs/onboarding.md).
