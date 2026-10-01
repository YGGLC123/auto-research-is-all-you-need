---
name: research-theory-siege
description: Attack a formal research claim until it has a checkable proof, concrete counterexample, conditional boundary, exact barrier, or smaller equivalent open problem. Routed by /auto-research:pilot for mathematical proof, identifiability, stability, complexity, impossibility, convergence, or theorem hardening, optionally consulting an external high-reasoning model via the pro-consult capability. Do not use for general literature search, routine prose editing, or unformalized brainstorming.
---

# Research Theory Siege

Treat a claim as unsettled until independent checks support a precise boundary. The discipline is mandatory; any external model is a high-reasoning participant, never the source of truth.

Apply [verify-and-stop](../verify-and-stop/SKILL.md). Reuse an independent check when
the claim, proof and relevant dependencies are unchanged. A new turn, prose cleanup
or bookkeeping transition alone does not require a new siege, regression or hash.

```
CLI  = py ../../scripts/research_os.py      (POSIX: python3 ../../scripts/research_os.py)
TURN = py ../../scripts/turn_runtime.py     (turn / lease / role commands)
```

If SessionStart printed `RECOVER_TURN_REQUIRED` or `REHYDRATE_REQUIRED`, clear it first: `TURN lease status` → `TURN turn probe`, take or recover the write lease, resume the OPEN turn by replaying its manifest under the same idempotency key. Without the lease your writes are refused.

## Entry

`CLI probe --phase theory --write-state <root>` before promising anything. Obey each verdict's `on_missing` policy per [../../docs/capability-registry.md](../../docs/capability-registry.md): `degrade` → use the registered fallback and record the degradation in state; `blocker` → `CLI blocker <root> add --type capability …` with a `queued_action`, then continue other work. **Silent skipping is a contract violation.**

**Register before running** ([attempt-tree.md](../../docs/attempt-tree.md)): every bounded try this phase makes — a search strategy, a proof route, a protocol variant, a rescue plan — is a coded node in the attempt tree. `CLI tree <root> register --kind attempt --parent <route> --title "…"` before executing, `close --status … --result "…"` at the verdict; run `CLI tree <root> list --under <route>` first so a dead sibling is never re-run.

| capability | claude-code first choice | codex first choice |
|---|---|---|
| pro-consult | any high-reasoning model you can reach (verify the tier in-session) | same |
| python-env | `py` → python3 | same |

## Lock the claim

Create or update `research/claims/<claim-id>/` with: `spec.md` (definitions, quantifiers, domains, input encoding, assumptions, strongest target statement, explicit criteria for proof and refutation), compact `state.json`, append-only `events.jsonl`, `artifacts/`, `counterexamples/`, `regressions/`. Fork a new claim ID for any weakening; never overwrite the original target. Record assumption and lemma dependencies; reject circular support.

Before any destructive rewrite of a spec or proof: `CLI snapshot <root> --label pre-<claim-id>-rewrite`.

**Mechanized claim lifecycle** (`py ../../scripts/procedures.py claim`, POSIX `python3`; the tree `C` node is mirrored automatically): `claim init <root> --id <kebab> --statement "…" [--tree-parent <route>]` opens `research/claims/<id>/spec.md` and registers its `C` node; `claim freeze --id` hashes and locks the statement; then run the siege; `claim verdict --id --status proven|refuted|conditional|barrier|equivalent_core --result "…" [--evidence PATH]` adjudicates and closes the mirrored node (the three non-binary verdicts close it `inconclusive` with a `CONDITIONAL:`/`BARRIER:`/`EQUIVALENT_CORE:` result prefix). **A verdict on an unfrozen spec is refused by the CLI, not by convention** — freeze first or nothing records. To change a frozen statement, `claim revise --id` (bumps `spec_version`, archives the old sha into `sha_history`, clears the freeze) — never hand-edit a frozen spec. Register counterexample regressions with `claim regression <root> add --id … --cmd "…"` and re-check them after any definition or proof change with `claim regression <root> run --id …` — any non-zero exit fails the gate and logs `claim_regressions_fail`.

## Attack state machine

`FALSIFY -> CONSTRUCT -> VERIFY -> RED_TEAM -> ADJUDICATE` — separate cognitive roles, fresh contexts where independence matters. The [claim-siege template](../../workflows/claim-siege.md) is the ready-made fan-out for this machine. Start with counterexamples, hidden assumptions, extreme cases, and quantifier errors. Construct a proof only under the frozen specification. **VERIFY spends one key by default** ([../../docs/verification-policy.md](../../docs/verification-policy.md)): reuse a current independent check when the statement, proof and dependencies match; otherwise recompute the affected enumeration, symbolic check or regression (research-reproducer for a new computational witness); where no mechanical criterion exists, VERIFY is the research-verifier agent with fresh context, given only the frozen statement and the artifact, never the builder's confidence label. **A second independent key is an escalation, not the default**: the user asks for it, or the claim is the paper's central theorem. When you do escalate, buy diversity rather than repetition — research-refuter (adversarial break) on top of a mechanical check, not a second read of the same kind. Close only after adjudication plus feasible mechanical checks (symbolic algebra, finite enumeration, SAT/SMT, proof-assistant skeleton, regression tests — all via python-env).

Configure effort to claim risk: one local pass can settle a simple lemma; a central theorem warrants independent subagents, external-model consults, and mechanical channels. Set a stagnation budget per claim wired to `loop.stagnation_budget` ([../../docs/loop-protocol.md](../../docs/loop-protocol.md)): budget exhausted on one claim → raise a `user_decision` blocker carrying the two best options and move to other claims — never a silent stall, never an infinite retry.

## External-model consult (capability `pro-consult`)

A consult puts a second, independent model family on the frozen claim: the strongest reasoning model you can reach, by API or through a chat UI the researcher drives. Whatever the transport, the discipline is the same:

- Verify the model tier on every run and quarantine any answer that came from a lower tier; a transport is not a tier.
- One new conversation per independent role; immutable `run_id` per run; send the exact frozen claim; save before retrying and reconcile the conversation by `run_id`.
- Ask it to break the claim, not to endorse it: every run ends in a proof, a counterexample, or a precisely stated gap.

**Transport broken mid-siege** (v1's real failure mode): `CLI blocker <root> add --type capability …` with the pending consult as `queued_action`, then degrade to local multi-channel self-attack (independent fresh-context subagents) and mark the route `degraded` in `capability_status` and in the claim's events. Never silently skip the consult; never present degraded consensus as externally verified.

A consult cannot replace source verification. Feed only necessary, clearly delimited excerpts from /auto-research:research-evidence; independently verify every citation or borrowed theorem.

## Adjudicate outcomes

Accept only one of:

- `PROVED`: checkable derivation under the exact spec;
- `REFUTED`: concrete counterexample with verified applicability;
- `CONDITIONAL`: precise additional hypothesis and why it is needed;
- `BARRIER`: exact reduction to a known hard or open obstruction;
- `EQUIVALENT_CORE`: a strictly smaller equivalent unresolved problem.

**A verdict that moves the story is a narrative event.** `REFUTED` on a load-bearing claim, a `theorem-failure`, and a `CONDITIONAL` that narrows what the paper may assert all change the narrative tree, so the turn that records them is narrative-bearing: `TURN turn probe`, rebuild the chronicler (`TURN role hydrate chronicler` → spawn **research-chronicler**), send one `CHRONICLE_TURN/v1`, and record its four-field proposal with `TURN turn record-disposition`. These are also the strongest `trigger_class` values the ledger has — a story change these force is genuinely `forced`, provided the counterfactual sentence is written and the user signs `approval_refs.forced_cause`; without that signature it stands at `unknown/candidate`, which is the honest reading. `KILLED` requires a qualifying basis; a claim that merely stopped being interesting is `DROPPED`, never `KILLED`. Detail: `/auto-research:research-narrative`.

RED_TEAM is the spot-check tier: run research-refuter against the central theorem and against fragile objects (long case analyses, hand-checked arithmetic, borrowed lemmas), not against every settled lemma. **Multiple agreeing model responses are correlated evidence, not proof.** Mechanically rerun historical counterexamples affected by a definition or proof change; reuse unaffected verified cases. The orchestrator writes `CLOSED`; models may recommend only `CANDIDATE_CLOSED`.

## Subagent playbook

Fan out when independence matters: parallel FALSIFY channels on one claim, VERIFY of a finished construction, or sieges on non-interacting claims. Every subagent prompt must carry: the frozen `claim-id` and spec version, the exact path to `spec.md` (verbatim spec, not a paraphrase), the expected artifact and return format (verdict + evidence paths), and an explicit falsification/stop condition. On return, merge evidence, never confidence — an unverified "I believe it holds" changes nothing. Independent verification of a **closing central claim** goes through the research-verifier agent, fresh context, without the builder's reasoning attached; a routine lemma closes on its own mechanical check.

## Exit gate

Register load-bearing outputs: `CLI asset <root> add --path research/claims/<claim-id>/… --role evidence --tag proof-pack [--hash]`; bump the claim's version track with `CLI update <root> --version claims=<label>` when the spec set changes. Then write the structured gate and hand off:

```
CLI update <root> --gate "theory_claims=passed:<verdict per claim>@research-theory-siege" --clear-next --next "…"
CLI event <root> --type phase_advanced --summary "claims adjudicated: <ids+verdicts>"
```

Finish only when the strongest defensible claim, assumptions, proof or counterexample, failed routes, regression checks, and remaining obligations are externally recorded. Route method consequences to /auto-research:research-method and empirical theorem checks to /auto-research:research-experiments.
