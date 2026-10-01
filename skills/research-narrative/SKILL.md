---
name: research-narrative
description: Operate a project's narrative tree as versioned state — inspect the story's history and drift ("看看历史轨迹", narrative trajectory, 叙事树, 叙事轴, "为什么走到今天", "丢过哪些杠杆", "还有哪些赌注"), dispatch the chronicler for a narrative-bearing turn, commit node-level changes, open/promote/kill alternative-story branches, run the overthrow-authorization flow, render the narrative axis, and backfill an older project's history with blind adjudication. Use when routed here by /auto-research:pilot, when a turn changes the story, or when the user asks what the narrative did over time. Do not use to draft prose or to hand-edit anything under .research-os/narrative/.
---

# Research Narrative

The paper's story is a tree with a git-style commit history. Every change is an incremental, node-level commit; alternative stories go to branches instead of vanishing; replacing the central story requires explicit user authorization; and "看看历史轨迹" is answered from the ledger, not by re-reading thirty markdown files.

```
NARR = py ../../scripts/narrative.py           (POSIX: python3 ../../scripts/narrative.py)
TURN = py ../../scripts/turn_runtime.py        (POSIX: python3 ../../scripts/turn_runtime.py)
CLI  = py ../../scripts/research_os.py
GRAPH = py ../../scripts/research_graph.py     (the relation layer: resolve / link / retract / derive /
                                                index / rebuild / snapshot / find / view / render)
```

The binding rules are [narrative-contract.md](../../docs/narrative-contract.md). **The CLI is the only writer.** You never hand-edit `.research-os/narrative/` or `.research-os/runtime/`; the chronicler never writes at all. A decision taken in conversation is not state until a decision record or a CLI receipt exists.

## Mandatory first step

If SessionStart printed `RECOVER_TURN_REQUIRED` or `REHYDRATE_REQUIRED`, **do that before anything else** — hooks only raise the flag; this skill executes it. A turn outlives the session that opened it.

1. `TURN lease status` → `TURN turn probe`. Lease held by a live other session ⇒ read-only; work elsewhere. Holder dead or lease expired ⇒ the CLI performs `recovery_takeover`. State unclear and not expired ⇒ stay read-only and require an explicit `TURN lease take --force --reason "…"` (it writes an incident).
2. Only after the lease is held, resume the OPEN turn: rebuild the chronicler instance and **replay the same `manifest_id` with the same idempotency key**. Never re-derive ops from memory; a commit already on disk is repaired, not repeated (`ALREADY_COMMITTED_RECEIPT_REPAIRED`).
3. Crash between commit and receipt ⇒ `NARR journal replay` first. Never ask a model to regenerate ops the journal already holds.

## Star topology — the only message path

```
UI / user → main controller → SendMessage(chronicler, CHRONICLE_TURN/v1) → chronicler proposal
          → main controller → NARR/TURN CLI → receipt + derived card/render
```

Long-lived role instances never talk to each other; `K##` tasks are the only work ledger. The chronicler never touches a page or a file; a rendered page never touches state.

## Rebuild the chronicler (each session, on demand)

The chronicler is a **durable profile on disk + a per-session instance**. There is no resident process.

1. `TURN role hydrate chronicler` — returns the profile pointer, the ≤12 KB role memory, the hot refs, the active `K##` list, and the current `turn_uuid`.
2. Spawn a `research-chronicler` agent ([profile](../../agents/research-chronicler.md)) injecting exactly that payload. Nothing else — the chronicler must not re-read the project's raw history during execution.
3. Its reply is a proposal in four fields (`proposed_ops` / `commit_meta` / `flags` / `memory_delta`), ≤30 lines. You then call the CLI. If the reply is longer, or contains a `card_delta`, or claims something was written — reject it and re-dispatch.

## The narrative-bearing turn

A turn is narrative-bearing when the input manifest touches `bearing_globs`, or carries a narrative CLI event, a claim state change, a decision record, a branch merge/kill/promote, or a role change. The machine floor is computed by the CLI; you may raise `false → true`, never lower it.

1. `TURN turn probe` → note `turn_uuid` and `manifest_id`.
2. Dispatch `CHRONICLE_TURN/v1` (≤8 KiB, `source_refs` ≤16, `idempotency_key = sha256(project|turn_uuid|manifest_id|chronicler)`). One envelope per manifest; the same envelope twice must return the same proposal.
3. Record the disposition — always through the orchestration entry, never by writing files:

```
TURN turn record-disposition --manifest <im_…> --ops-file <ops.json> --meta-file <meta.json>
TURN turn record-disposition --manifest <im_…> --no-change --reason "cold review filed; no node changed"
```

4. **Receipt discipline.** Exactly four receipt states exist: `NO_INPUT_CHANGE` and `ACK_NON_NARRATIVE` (the CLI writes them itself), `NARRATIVE_COMMITTED{commit_ids[]}`, and `NARRATIVE_REVIEWED_NO_CHANGE{reason}`. You never author receipt content — the CLI constructs it. `proposed_kind` is advisory; a `KIND_CORRECTED` flag on the receipt means the CLI's mechanical judgement stood, and that is correct.
5. **Knock-off gate.** `TURN turn finalize` closes the turn only when every manifest has exactly one valid receipt. Refusal prints `CHRONICLE_REQUIRED turn=… manifest=…` — that is a work item, not an error to route around. New input arriving after a manifest froze needs a *new* manifest and its own disposition. Diagnose without closing via `--check-only`.
6. `TURN turn escape --reason "…" --user-approved` exists **only** when the user explicitly authorizes skipping the gate. It writes an `incidents/` record; it is never a receipt and never a way to make a red gate green. Do not offer it as a convenience.

## Answer "看看历史轨迹" — the user asks in passing; no wording is required

**The phrases in this skill's description are for you to recognise, never for the user to reproduce.** There is no trigger word, no command, no format. Any offhand question about how the story got here routes to the readings below, in whatever language and whatever wording it arrives — including a vague one you resolve yourself rather than asking which id they mean ([pilot](../pilot/SKILL.md), *How the user experiences this*, rules 1 and 4).

All of these are the same request:

> 看看历史轨迹 · 这篇是怎么走到今天的 · 我们中间是不是拐过弯 · 之前那个更强的说法哪去了 · 当初为什么放弃 X · 现在的头条是什么时候定的 · 还有哪些赌注没了结 · 丢过哪些杠杆 · 叙事树 / 叙事轴
> what did the story do over the last month · why are we telling it this way now · when did the headline change · was that turn forced by the data or by a referee · what bets are still open · which lever did we drop · show me the trajectory

```
NARR trajectory [--window 30]     # ≤40 lines, fixed budget; overflow prints "… +N; FULL:<pointer>"
NARR card --md                    # current narrative card (≤120 lines; give the main line ≤40)
NARR log --graph | NARR blame <node> | NARR diff <a> <b>
```

**What you hand back is a paragraph, not the readout.** Lead with the answer in plain words — what the story did, where it turned, what it cost — then offer the detail. The five readings below and the node ids stay in your hands until the user asks to see them, and an id never travels bare: `N-014 "阅读量取决于审稿流程"`, not `N-014` (rule 5).

Five raw readings, reported and never scored into a composite: `structural_commits_N`, `unforced_structural_N` (with `cause=unknown` shown separately), `headline_identity_changes_N`, `structural_stability_days` (refine does not reset it), `open_branch_count` with its unresolved count. Report them flat. `LEVER_DEBT` and `LOST_LEVER` are surfaced, not resolved by the system; `UNRESOLVED_BRANCH` is never auto-dropped — "nobody worked on it lately" is not "the researcher decided to abandon it".

## Read the argument as a graph (`graph view`)

The narrative tree says what the story claims; the **research graph** says what holds it up. Since v3 it is the **authoritative relation layer** ([v3-contract.md](../../docs/v3-contract.md)), not a side-car: it owns how ids in different stores relate, while `narrative/` stays authoritative for every node and no id is ever migrated.

What you read is always `.research-os/graph/current.json` — the materialised set of currently-holding relations, re-projected inside the same locked transaction that appended the event. So:

- **`GRAPH rebuild` first if SessionStart said so.** `GRAPH_REBUILD_REQUIRED` means the projection is behind `edges.jsonl`; every reading below would then be yesterday's answer, silently. The hook cannot fix it (it may not take the write lock) — you run it, before anything else graph-shaped.
- **Never edit anything under `graph/` by hand.** `hook_guard` blocks it, and it would desynchronise the projection from the event stream it projects.

```
GRAPH rebuild                        # replay an interrupted transaction + re-project current.json
GRAPH view argument [--node <ro>]    # 主张 → 证据 → 运行 → 数据: the support chain, ≤40 lines, conflicts marked (!)
GRAPH view coverage                  # five lists A–E (below)
GRAPH view conflicts                 # every EDGE_CONFLICT with both sides' sources
GRAPH view objects [--space narr|fig|claim|…] [--status present|gone]
GRAPH view role-activity | node <ro>
GRAPH index build | update [--space …] # the object index; a vanished object becomes [GONE], never deleted
GRAPH snapshot --label … [--keep N] [--milestone]     # freeze edges + objects + current
GRAPH resolve ro:<project>:<space>:<id>               # read-only address → file + object
GRAPH render --html --view coverage --out <file>      # the four-tab page (below)
GRAPH --all-projects view coverage | find --kind expressed_as --to-space narr --title-like "…"
```

**`derive` is no longer yours to run.** Since v3 every host CLI derives its own rule subset the moment its own
transaction succeeds: `narrative commit` runs narr-basis + lineage (+ a fig-registry re-check), `figure_router
quick --promote-current|--rebase|--reroute` runs fig-registry, `research_os tree close` / `asset add` and
`procedures claim verdict` / `run finish` run theirs, and `narrative backfill` runs all eight once at the end.
The commit payload carries `graph{derived, added, retracted}`, so a commit that added a `basis_ref` already has the
edge before you look. Running `GRAPH derive` by hand is at best a no-op — and it hides the one thing worth noticing,
which is an automatic trigger that stopped firing (it would have left a `GRAPH_DERIVE_FAILED` line in
`graph/incidents.log` rather than failing the commit).

Coverage is five lists, not three: **A** load-bearing claims no figure expresses · **B** figures expressing no claim ·
**C** `HELD` with no `supported_by(+)` · **D** carries a `(−)` edge yet is still ACTIVE · **E** `EDGE_CONFLICT`
(two sources of opposite sign on the same relation — counted as neither support nor refutation until somebody
adjudicates).

`GRAPH render --html` writes one self-contained page (no network, no external asset) with all four views —
argument / coverage / conflicts / objects — in the narrative-axis visual language. It reads `current.json` and
**writes no state**: the conflicts tab's 提议裁决 button exports a `decision-draft-v1` JSON for a human to land
through the decision CLI. Hand it to the user when the answer is a shape rather than a sentence.

Coverage list A — load-bearing claims no figure expresses — is fed by the figure registry: a Route A/B/C instance registered with `figure_router.py quick --register --claim <id>` and promoted with `--promote-current` is written to the figure registry, after which `graph derive` (fig-registry rule) books the `expressed_as` edge, so the claim leaves list A the moment the figure that argues it ships ([figure-engineering-contract.md](../../docs/figure-engineering-contract.md) §7). A claim still sitting in list A after its figure exists usually means the figure was never promoted, or its edge is parked — `GRAPH link --replay-pending` clears the parked ones.

Use `argument` when the question is "what is this claim standing on"; use `coverage` before a submission or a cold review, because its five lists are exactly the ones a referee finds first. The readings are flat facts, not a score: a node with no supporting edge may be an honest assumption, and a figure expressing no node may be a diagnostic that never belonged in the paper — the view surfaces, you decide.

Across projects the graph is **read-only, always**: `GRAPH --all-projects view coverage` gives ≤5 lines per project, and `GRAPH --all-projects find --kind expressed_as --to-space narr --title-like "…"` answers "who has already expressed a claim like this, with what" — hits point at the source project's full `ro:` address, which you then open there. An edge is never written into another project from here.

Edges are **proposed by roles and written by you**: the figure engineer proposes `fig → narr expressed_as`, the evidence steward `ledger → narr supported_by`, the experiment runner `run → narr|claim supported_by(±)`, the theory operator `claim → narr supported_by` and `claim → claim evolved_from`, the chronicler the narrative-internal and `narr → claim` edges. Each arrives as `proposed_edges[]` in a ≤30-line reply and becomes state only through `role receipt` (the CLI reads the role from the dispatch registry, authorises on (role, kind, from-space, to-space) before resolving endpoints, and books or parks the edge); a controller-authored edge is `GRAPH link --from … --to … --kind … [--polarity +|-] --basis …` with no `--by` (self-asserted roles are refused). A retracted edge is `GRAPH retract <edge_id> --reason …`, which appends a retract event — the ledger is never rewritten, so a support that was later withdrawn stays visible.

## Branches — alternative stories that cannot coexist with main

```
NARR branch create <name> --from main       # opens with an OPEN disposition event
NARR branch promote <name> [--into main]    # fast-forward, or a two-parent merge with a conflict list
NARR branch kill <name> --basis <ref>       # requires a qualifying basis
NARR branch drop <name> --reason "…"        # explicit abandonment only — the system never does this for you
```

Refine and restructure both commit straight to `main`. A branch is for a *mutually exclusive* story hypothesis. Auto-merge covers only disjoint expression fields (title/summary/statement); structure, state, identity, roles, detach, split/merge all go to the conflict list for the user.

## Overthrow — the one hard gate

Replacing the root's semantic identity (root replaced, removed, merged into a new semantic node, or a branch with a different root promoted to main) is refused with `OVERTHROW_REQUIRES_USER_APPROVAL` and leaves refs, snapshots, and preview untouched. The flow:

1. Chronicler flags `OVERTHROW_SUSPECTED`; **you** put the one question to the user: **overthrow, or a branch?** One sentence of background (what the current root claims, what the new one would claim, what forced it), the two options, and your recommendation — answerable in a sentence ([pilot](../pilot/SKILL.md), rule 3). The words `overthrow`, `approval_refs`, and `OVERTHROW_REQUIRES_USER_APPROVAL` are yours, not theirs; steps 2 and 3 are your work, not a form they fill in.
2. If overthrow: write a dated decision record (`CLI` decisions) stating the old root, the proposed root, the evidence, and the user's explicit approval.
3. Commit with `approval_refs.overthrow` pointing at that record. `kind=overthrow` is then assigned mechanically.

A forced `cause` needs its own signature: the counterfactual sentence plus `approval_refs.forced_cause`. Until both exist the commit stands at `unknown/candidate`, and that is the honest reading.

## The research map — "what did the AI do?" on one page

`NARR map <root> render` writes `.research-os/map/research-map.html`: the story tree as a mind map with three overlays — **changes** since the researcher's last view (✚ ✎ ⇄, the dropped list, each with commit, author and cause), **evidence** (⚡ refuted, ? held without evidence, ▢ no figure, ⚠ benched — the ledger's `LEVER_DEBT`, ◷ live bet, ✔ signed), and **taste** (✦ a touched node trips one of their rules). `NARR map <root> summary` gives the ≤40-line plain-text version for the chat reply. When you talk the researcher through the page, use the words it shows them, so the chat and the page agree: 被雪藏 / Benched, 被打脸 / Called out, 踩你雷区 / Not your style, 昨夜小票 / Overnight receipt, 要你拍板 / Needs your call. The header says how stale the recorded story is; if many story-bearing files changed after the last commit, say so and offer to bring the tree up to date — do not present a lagging map as current.

Exports for the tools the researcher already uses: `NARR map <root> export --format markmap|canvas|xmind --out <file>`. An edited markmap or XMind file comes back with `NARR map <root> import --file <f> --out <patch.json>`: renames and moves become ops by stable node id; new and deleted topics become `todo_proposals`. Land it with `NARR apply --patch` → `NARR commit --pending-patch`, as any NarrativePatch. Contract: [research-map.md](../../docs/research-map.md).

Taste rules live beside it (`NARR taste <root> add | harvest | accept | check | list | retire`). Every rule quotes its source; the chronicler flags `TASTE_CONFLICT: <rule id>` when a proposed op would trip a rule, and the controller shows the flag to the researcher rather than resolving it.

## Render the narrative axis

```
NARR render --view axis        # horizontal axis of root's ordered children, 4 levels expanded by default
NARR render --view reader|internal|todo|tiers
NARR render --view axis --html --out <file>     # single file, no external references
NARR serve [--ref main] [--port 0] [--model …]  # live page: same tree, questions answered in place
```

```
NARR audit [--checkers claude,codex] [--emit-patch <file>]   # is every node part of the story? two independent checkers
```

**Only the story gets a node — and a second model family checks it.** A narrative node is something the reader meets as part of the argument. Wording decisions, to-dos, status notes, remarks about the tree, unreadable fragments and duplicates are noise the drift readings would count as story. After a backfill, after a large restructure, and whenever the user says a node "doesn't look like narrative", run `NARR audit`: it classifies every node with the Claude CLI *and* the Codex CLI, writes `narrative/audits/<stamp>.{json,md}`, and lists what both agree is not story. Read the ≤40-line report, tell the user in one sentence what will leave the story (titles, not ids), apply the emitted patch (`NARR apply --patch … → commit --pending-patch …`) for the agreed leaves, and bring the disagreements to the user only if they matter — a disagreement is never applied. Nodes both checkers cannot read are the chronicler's transcription debt (a prose pass, not a drop). The audit needs both CLIs on the machine; with one missing, say so and run the one you have as a single-key *report*, never as a patch.

```
NARR literature build | link | report | status     # the studies each paragraph rests on (树形 only)
```

**The 树形 structure carries the literature; the other two do not.** When the user wants to see, beside a paragraph, which studies it rests on — or asks what a cited paper contributes and where it stops — run `NARR literature build` (refs.bib + the manuscript's `\cite`s + a per-chapter priority table + a literature map, if the project has them), `link` (chapter / mention / basis), and `report` (the evidence steward's three lines per study, from local materials only; "材料未记载" is an honest line). The page then shows a purple count beside each paragraph in 树形 and opens each study to its report, verification status, DOI/link and the paragraphs that rest on it. Rules and responsibilities: [docs/literature-ledger.md](../../docs/literature-ledger.md). Never hand-edit `.research-os/literature/`; a study the text cites but the bib lacks appears as `unmatched_mentions` — that is the evidence steward's queue, not something to paper over.

**The user reads and asks; you open the right page.** When the user wants to *look* at the story ("打开叙事树 / 我看看 / 给我页面"), prefer `NARR serve` — it opens the browser on a loopback page that re-renders the head snapshot on every load and answers a question typed beside any node right there (streamed, then filed as that node's `qa` annotation by `chronicler-live`, so the next render shows it and the chronicler sees it as prior Q&A). The static `--html --out` file is for sending the page somewhere the server is not running; on that page a question is only recorded as 待回答 and copied to the clipboard — nothing downloads on its own. The page shows only titles, briefs and plain-language state words; if a project's nodes still read like a ledger (pointers, status codes, workbench shorthand in `statement`), that is the chronicler's transcription debt (contract §1): dispatch a prose pass that returns `patch_fields` moving the marks into `basis_refs` as `ledger:…` strings — never edit the page or the store by hand. Keep the server running only while the user is reading; it is a reader's tool, not a daemon.

The old "formal / internal / todo" markdown trio and the four summary tiers are **views of one tree**, so tier-to-tier consistency holds by construction. Round trips from a page come back as a `NarrativePatch` (`NARR apply --patch …` → preview + `pending_patch_id`, consumed once by `NARR commit --pending-patch <id>`). A patch is not a commit; a mismatched base is refused, and there is no three-way merge.

## Backfill an older project's history

Deterministic descent — never "read the old files and write history freehand".

1. `NARR backfill --sources <dir>` — explicitly numbered versions only; each snapshot yields a Historical Frame recording the target, venue, evidence standard, and open questions **as of then**. Processing S_t → S_t+1 may read only material timestamped no later than S_t+1; the access log is auditable.
2. `NARR backfill adjudicate --blind` — the first pass shows the user the before/after pair **without** revealing what the story later became, and asks only the ambiguous calls: load-bearing node mapping, split/merge, forced vs unforced, `KILLED` basis, whether a `DROPPED` was really abandoned.
3. The user adjudicates — one ambiguous call at a time, each posed as background + options + your reading, in the wording of the paper rather than of the schema; they answer in a sentence and you enter it. Adjudications are append-only; an old one is never overwritten, only superseded. Unnumbered load-bearing passages go to `backfill/unmapped.json` — never guessed onto an existing id.
4. Submit the chain, then `NARR trajectory` and `NARR validate`. `kind` stays mechanically judged; `author.role=backfill`, `origin=backfill`, and absent a contemporaneous trigger `cause=unknown`. Acceptance is human: the user recognizes the reconstruction as the history they lived.

## Discipline

- Node budget: a normal writing round produces **0–8 nodes**. Node only what has a persistent semantic identity and gets referenced across phases; explanatory prose belongs inside a node's payload, not in a new node.
- Same proposition, better wording ⇒ same id. Changed subject/target/predicate/quantifier/domain/conditions/polarity ⇒ new id plus lineage. Never let semantics drift under a stable id.
- Derived artifacts (`card`, trajectory, renders, `index/`) are rebuildable and are never edited directly; they always carry their `source_commit_id`, and the card derives from committed `refs/heads/main`, never from a preview.
- Verification follows [verification-policy.md](../../docs/verification-policy.md): keep CLI write-time validation and the specific check before an `overthrow`; reuse valid narrative checks until affected nodes/rules change. A phase exit, snapshot or submission alone does not require `NARR validate` or `CLI doctor`; target changed dependencies and stop when sufficient.

## Exit

```
CLI update <root> --gate "narrative_state=passed:<ref>@<commit>@research-narrative" --clear-next --next "…"
CLI event <root> --type narrative_committed --summary "<kind>/<cause>: <what moved>"
```

Route story drafting to `/auto-research:research-manuscript`, figures to `/auto-research:research-artifacts`, formal claims to `/auto-research:research-theory-siege`, and coordination back to `/auto-research:pilot`.
