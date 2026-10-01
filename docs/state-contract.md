# Project-Local State Contract (v2)

Canonical control state lives under `.research-os/` inside each research project. Papers, code, data, and evidence live in normal project folders and are referenced by relative path. The plugin never stores project state inside itself.

**Every read and write goes through the state CLI** ([../scripts/research_os.py](../scripts/research_os.py); from inside a skill dir the relative path gains one level; Windows interpreter: `py`, POSIX: `python3`). Hand-editing `state.json` is a contract violation: the CLI validates before every write and writes atomically. Below, `CLI` abbreviates that invocation. The narrative tree and the turn runtime have their own dedicated writers under the same rule — [../scripts/narrative.py](../scripts/narrative.py) and [../scripts/turn_runtime.py](../scripts/turn_runtime.py) ([narrative-contract.md](narrative-contract.md)) — and nothing else, model or hook or subagent, may write those trees.

## Four authority tiers

Since v3 ([v3-contract.md](v3-contract.md)) the control plane is not one undifferentiated pile of files but four tiers with different rules. Knowing which tier a file is in tells you whether it may be deleted, whether it may be appended to, and what happens when it disagrees with something else.

| Tier | What it owns | Written by | If it is lost |
|---|---|---|---|
| **Authoritative narrative** | narrative snapshots, commits, refs, branches, tags | `narrative commit` (single atomic transaction) | the story is lost — restore from a snapshot |
| **Authoritative relations** | `graph/edges.jsonl` (link/retract events), `graph/objects.json` (object index), `graph/current.json` (materialised current relations), `graph/snapshots/` | `graph link\|retract\|derive\|index` — one transaction under the project write lock | events are the truth; `graph rebuild` re-derives the rest |
| **Derived cache** | narrative `card.*`, trajectory, every rendered page, coverage caches | rebuilt from the tier above, always stamped with its source | delete it and re-render; never repair it by hand |
| **Append-only auxiliary** | `annotations/`, `incidents/`, `adjudications/`, `roles/pending_edges/`, `events.jsonl`, `backfill/` | appended, never rewritten | history is lost, current state is not |

Objects stay owned by their own ledgers: the relation tier is authoritative for **how ids relate**, never for what an id means. A claim's verdict belongs to the claim ledger, a figure's status to the figure registry, a node's statement to the narrative tree — the graph only says that this figure expresses that claim.

## Files

- `state.json` — current materialized checkpoint (schema below).
- `events.jsonl` — append-only transition log. Never rewritten.
- `decisions.md` — short, dated decisions that would otherwise be rediscovered.
- `tree.json` + `ATTEMPTS.md` — the structured attempt tree and its auto-rendered master table: every route/attempt/claim/verification as a coded node, registered before it runs ([attempt-tree.md](attempt-tree.md)).
- `sessions.json` — numbered work badges (W##) per host session; allocated by `session ensure` under the write lease ([collab-protocol.md](collab-protocol.md)). Events, tree nodes, and tasks carry the badge automatically.
- `cursors.json` — per-reader event cursors: logs are consumed catalog-first (`events status`) then incrementally (`events read`); nobody re-reads the whole log.
- `collab/tasks/K##.json` — the cross-host task mailbox (`collab.py`), digests hard-capped at 30 lines.
- `learned/` — the project-tier growth library ([growth-layer.md](growth-layer.md)); `promote` lifts entries to the plugin's global library.
- `narrative/` — the versioned narrative tree ([narrative-contract.md](narrative-contract.md)), written **only** by `narrative.py`. Three layers with different rules: the **authoritative** layer (`objects/` snapshots, `commits/`, `refs/heads/*`, `branches/registry.json` with append-only `disposition_history`, `tags/`) where tree snapshots, commits, and refs move through the single atomic `narrative commit` transaction; the **derived cache** layer (`card.json`/`card.md`, trajectory, rendered views, `index/`) which is deletable, rebuildable, always stamped with its `source_commit_id`, and never edited independently; and the **append-only auxiliary** layer (`annotations/`, `incidents/`, `backfill/`, `working/<ref>.json` previews, `config.json`). Node ids match `^[A-Z]{1,2}-[0-9]+(\.[0-9]+)*$` and are never reused.
- `runtime/` — the turn gate's state, written **only** by `turn_runtime.py`: `turn.json` (one OPEN turn at a time, surviving the session that opened it), `lease.json` (single-writer lease, 15-minute expiry, heartbeat on every write command), `manifests/` (CLI-generated InputManifests — the model never supplies a digest), `receipts/<turn_uuid>.<manifest_id>.json` (exactly four states: `NO_INPUT_CHANGE`, `ACK_NON_NARRATIVE`, `NARRATIVE_COMMITTED`, `NARRATIVE_REVIEWED_NO_CHANGE`), `journal/` (replayed on restart; ops are never regenerated by a model), and `base_input_snapshot.json`.
- `roles/` — the long-lived role fleet ([role-fleet.md](role-fleet.md)), written **only** by `role_runtime.py`: `registry.json` (schema `auto-research/role-registry-v1`: per-role profile pointer, `enabled`/`hot`, memory path, cursor, session-scoped `instance`, `last_dispatch`, open `K##` tasks, status); durable role memory `<role>.memory.json` (`chronicler.memory.json`, `figure-engineer.memory.json`, …; ≤12 KB each, **merged** — never overwritten — by the CLI from the role's returned `memory_delta` at receipt time); and `receipts/<message_id>.json`, one per dispatch, carrying `ANSWERED | ANSWERED_NO_CHANGE | REJECTED(reason)` plus the idempotency key that makes a second receipt for the same `message_id` return `ALREADY_ANSWERED`. A role instance never writes its own memory, its registry entry, or its receipt; a dispatch with no receipt is listed as `DISPATCH_UNANSWERED` at the next session start. Judge agents (verifier, refuter, reproducer) are one-shot and are never registered here ([verification-policy.md](verification-policy.md)).
- `figures/registry.json` — the v2.9 figure-instance ledger ([figure-engineering-contract.md](figure-engineering-contract.md)), written **only** by [../scripts/figure_router.py](../scripts/figure_router.py). Schema `auto-research/figure-registry-v1`: `{"figures": {"F-1NN": {figure_id, intent, status: draft|current|superseded, route: A|B|C, template: "lib:figure:forest-ci@1", claim_ids[], section, venue, constraints, semantic_objects[], data_refs[], params, artifact_dir, route_decision, outputs{}, provenance{}, qa_summary{}, edges[], pending_edges[], handoff?, history[]}}}`. Every figure is **registered before it is drawn** (`quick --register`), and the id resolves as `ro:<project>:fig:F-1NN` ahead of the FSG programs ([graph-contract.md](graph-contract.md) §1). Promoting to `current` books the `expressed_as` edge through `research_graph.py`; an edge whose narrative end does not exist yet is parked in `pending_edges` and replayed by `graph link --replay-pending`, never dropped. The rendered payload lives outside the control plane, in the project's own `research/artifacts/figures/<figure_id>/` (`request.json`, `route.json`, the pdf/svg/png, `qa.json`, `provenance.json`, optional `spec.vl.json` / `attribution.md` / `seed.fsg.json`) — state stores pointers and hashes, never payloads.
- `taste/` — the researcher's taste ledger ([research-map.md](research-map.md) §3), written **only** by [../scripts/taste_ledger.py](../scripts/taste_ledger.py): `rules.jsonl` (append-only `add` / `retire` events; every rule carries its source — the researcher's own words, a decision block, or a signed commit) and `candidates.json` (derived cache of verdicts on record that are not yet rules; a candidate is never applied until the researcher accepts it). Hand edits are blocked by `hook_guard`.
- `map/` — the research map's side files ([research-map.md](research-map.md) §1): `views.jsonl` (append-only; when the researcher last looked, so the next map opens on "since your last view"), the rendered `research-map.html`, and any exports. Derived and auxiliary; the map reads the narrative, graph and taste ledgers and writes none of them.
- `graph/` — the **authoritative relation layer** ([v3-contract.md](v3-contract.md), superseding the auxiliary-layer wording of [graph-contract.md](graph-contract.md)), written **only** by `research_graph.py`. The graph owns how ids in different stores *relate*; every object itself stays owned by its own ledger — the narrative tree, attempt tree, claims, runs, evidence ledgers, figure registry, asset index — and no id is ever migrated. Four files with different rules: `edges.jsonl` is the **append-only event stream** (`link` / `retract` events, `{edge_id, from, to, kind: depends_on|supported_by(+|-)|expressed_as|evolved_from, basis, by(role|main|user), at, source_commit?}` over `ro:<project>:<space>:<id>` addresses, lines never rewritten — a retraction is an event); `objects.json` is the **object index** over all eight spaces (`{space, id, title, status, fingerprint, source_path, updated_at}`; a vanished object is marked `gone`, never deleted, and derivation takes its edges back); `current.json` is the **materialised projection** of active logical edges (merged provenance, `EDGE_CONFLICT` marks, counts) that every view, the role graph context, and the SessionStart line read *instead of* folding the event stream; `snapshots/<ts>_<label>/` freezes all three (`--keep N` rotated, `--milestone` never). `link | retract | derive | index` are **one transaction** — journal → stamped events → temp file → fsync → atomic rename → `current.json` re-projected → journal DONE — inside the project write lock, so an interrupted write is finished by `graph rebuild` and never leaves the projection disagreeing with the stream. `derive` is deterministic, idempotent, reconciling (it retracts what its source stopped saying), and since v3 it is **triggered by the host CLIs themselves** after their own transactions succeed, not run by hand. Roles only *propose* edges (`proposed_edges[]` in their reply); the controller runs `graph link`.
- optional `runs/` (immutable external-run metadata), `scratch/` (disposable), `snapshots/` (labeled checkpoints, `--keep` rotated; milestone-labeled ones never auto-deleted).
- `.lock` — transient write lease; every mutating CLI command holds it, so concurrent windows/hosts queue instead of corrupting.
- `state.v1.bak.json` — automatic backup created by `migrate`.

## Schema `auto-research/v2`

```json
{
  "schema_version": "auto-research/v2",
  "project_id": "stable-kebab-id",
  "title": "Project title",
  "status": "active",
  "stage": "preliminary_study",
  "stage_detail": "optional free text preserved from migration or nuance",
  "stage_confidence": 0.8,
  "paper_type": "domain_application",
  "target_venue": "",
  "objective": "One current objective",
  "active_workstream": "novelty-and-data",
  "active_skill": "research-evidence",
  "gates": {
    "novelty_map": {
      "status": "open",
      "owner_skill": "research-evidence",
      "evidence": "closest prior unresolved",
      "updated_at": "2026-07-17T00:00:00Z",
      "blocker_id": "blk-001 (required when status=blocked)"
    }
  },
  "blockers": [
    {
      "id": "blk-001",
      "type": "permission",
      "description": "git push requires user authorization",
      "queued_action": "push branch after user approves",
      "raised_at": "2026-07-17T00:00:00Z",
      "resolved_at": null
    }
  ],
  "capability_status": {
    "latex-toolchain": {"status": "available", "provider": "latexmk", "host": "claude-code", "probed_at": "2026-07-17T00:00:00Z"}
  },
  "loop": {
    "mode": "manual",
    "wakeup_pending": false,
    "last_tick": null,
    "stagnation_count": 0,
    "stagnation_budget": 3,
    "stop_conditions": []
  },
  "host_log": [{"host": "claude-code", "last_active": "2026-07-17T00:00:00Z"}],
  "risks": [],
  "versions": {"manuscript": "v12", "protocol": "v3"},
  "artifacts": [
    {"id": "fig-history", "path": "paper/figures/history.pdf", "role": "evidence",
     "tags": ["figure", "headline"], "version": "v2", "sha256": "…",
     "produced_by": "research-artifacts", "status": "current", "added_at": "…"}
  ],
  "capability_needs": [],
  "authorization": {
    "github_scope": null,
    "external_library_scope": null,
    "drive_upload": "confirm_each_action"
  },
  "next_actions": [{"action": "Verify nearest prior", "owner_skill": "research-evidence"}],
  "backlog": [],
  "visual_plan": {},
  "visual_delivery": {},
  "visual_integration": {},
  "updated_at": "2026-07-17T00:00:00Z"
}
```

Enums: `stage` ∈ idea_only | preliminary_study | method_taking_shape | experiments_underway | manuscript_draft | rejected_or_borderline | final_submission_or_rebuttal. `paper_type` ∈ theoretical_ml | empirical_ml | domain_application | systems_tooling | survey_position. Gate `status` ∈ open | passed | failed | blocked | waived. Blocker `type` ∈ permission | capability | user_decision | external | technical. `loop.mode` ∈ manual | assisted | autonomous.

## Hard rules (enforced by the validator)

1. **Read wide, write strict.** v1-family states (`research-pipeline-os/v1`, `auto-research-v1/v1`) validate leniently and migrate losslessly (free-form stages land in `stage_detail`, overflow next_actions land in `backlog`, missing fields get defaults). v2 writes reject any drift.
2. **Blockers are first-class and loud.** Anything that needs user permission, a user decision, or a missing capability MUST become a blocker (`research_os.py blocker add`), never a silent skip. A gate may be `blocked` only with a `blocker_id`. The `context` digest surfaces unresolved blockers on every resume.
3. **`next_actions` ≤ 3.** Long queues go to `backlog`; the pilot promotes at most three at a time.
4. **No secrets.** Keys matching token/cookie/password/credential/secret/private_key/api_key anywhere in state fail validation.
5. **Events are append-only** with `ts` + `event`; retries of external work get a new immutable `run_id` plus `parent_run_id`.
6. **Visual gate objects** (`visual_plan`, `visual_delivery`, `visual_integration`) belong to `research-artifacts` and follow its `research-artifacts/*-v1` schemas; state stores pointers and hashes, not payloads.
7. **`narrative/`, `runtime/`, `roles/`, `graph/`, `figures/`, and `taste/` are CLI-only, without exception.** No hand edit, no subagent write, no hook write, no "just this once". `graph/` is enforced, not merely stated: `hooks/` runs `scripts/hook_guard.py`, which blocks Edit/Write/MultiEdit/NotebookEdit on any `.research-os/**/graph/**/*.json|.jsonl|.log` and names the `research_graph.py` subcommand to use instead — because a hand edit there does not lose one entry, it desynchronises `current.json` from the event stream that `current.json` exists to project. A decision reached in conversation is not state until a decision record or a CLI receipt exists; every role — chronicler, figure engineer, evidence steward, experiment runner, theory operator — proposes and the CLI writes. Derived files are rebuilt, never repaired by hand, and the narrative card derives from committed `refs/heads/main` — never from a preview.

## The anti-amnesia layer (versions, ammo depot, snapshots)

Long contexts forget exactly three things: version numbers, asset inventories, and which state was current. All three are therefore indexed on disk and printed by every `context` digest — **the digest is authoritative; conversation memory is not.**

- **`versions`** — a flat track→label map (`manuscript=v12`, `protocol=v3`, `frozen-plan=2026-07-13`). Update with `research_os.py update <root> --version manuscript=v13` at the moment the version changes, in the same breath as the change itself.
- **`artifacts` as ammo depot** — every load-bearing output (script, table, figure, ledger, proof pack) gets an indexed entry: `research_os.py asset <root> add --path … --role … --tag … --version-label … [--hash]`. `--hash` records sha256 for bit-exactness claims. Same id again requires `--supersede` (old entry flips to `superseded`, stays in the index — provenance is never deleted). Query: `asset list [--tag …]`. Statuses: current | superseded | quarantined | archived.
- **`snapshot`** — `research_os.py snapshot <root> --label pre-model-switch` copies state/events/decisions into `.research-os/snapshots/<ts>_<label>/`. Take one before: model/agent switches, destructive rewrites, route pivots, and harvest points. Replaces the ad-hoc `_codex_revision_snapshots` convention.

## Update protocol (every meaningful transition)

```bash
CLI update <root> --set active_skill=research-method \
    --gate "method_spec=passed:frozen v3@research-method" \
    --clear-next --next "Implement vertical slice@research-experiments"
CLI event <root> --type phase_advanced --summary "..." --artifact <path>
```

Then make a scoped local git commit for material project changes. Update state after: verified progress, failed gates, authorization changes, blocker transitions, pauses, and completion.

## Resume protocol

`research_os.py context <root>` prints the digest: identity, open/failed/blocked gates, **unresolved blockers**, loop status, next_actions, last five events. The SessionStart hook runs it automatically. Reconcile state against filesystem evidence — state is a checkpoint, not ground truth. Reconcile in-flight external jobs by `run_id` before retrying.
