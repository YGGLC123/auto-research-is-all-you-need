---
name: research-interrogation
description: Compose adaptive, evidence-bound research question suites across research stages, paper types, venues, and fields. Use when the auto-research pilot or another phase skill routes here to generate an author questionnaire, interrogate a project, expose decision-changing unknowns, compare research routes, design a cross-stage audit, or apply a venue/domain question adapter. Do not use as a fixed checklist or to replace the phase skill that owns the resulting work.
---

# Research Interrogation

Turn a large question bank into the smallest useful interrogation for the current uncertainty.

```
CLI = py ../../scripts/research_os.py      (Windows; POSIX: python3 ../../scripts/research_os.py)
```

## Entry

Before composing: `CLI probe --phase review --write-state <root>`. Obey each verdict's `on_missing` policy from [../../docs/capability-registry.md](../../docs/capability-registry.md): degrade loudly and record it, raise a blocker, or stop that route — never silently skip. Interrogation itself is local text work; the probe matters for the routes questions will trigger (web verification of venue rules, review panels, Pro consults).

**Register before running** ([attempt-tree.md](../../docs/attempt-tree.md)): every bounded try this phase makes is a coded node in the attempt tree. `CLI tree <root> register --kind attempt --parent <route> --title "…"` before executing, `close --status … --result "…"` at the verdict; run `CLI tree <root> list --under <route>` first so a dead sibling is never re-run.

## Compose the run

Read [revision-question-suite.md](references/revision-question-suite.md), then:

1. choose one operation, stage, paper type, primary uncertainty, and output budget;
2. load one primary question pack and at most one secondary pack (from `references/question-packs/`);
3. load zero to two additive adapters (from `references/adapters/`) only when the venue or field genuinely adds questions;
4. ask or answer the selected questions with evidence anchors and decision impact;
5. return a compact questionnaire, evidence audit, decision docket, portfolio audit, or revision specification.

Do not read every pack by default. Exhaustive work is sequential: finish one decision gate, update the docket, then select the next pack.

## Preserve truth and ownership

List only materials actually available. Never invent answers, citations, venue rules, theorems, results, or domain facts. Mark missing evidence explicitly and distinguish observed facts, verified results, inferences, proposals, and unknowns.

This skill owns question composition and synthesis, not the work implied by the answers. Route literature, discovery, theory, methods, experiments, prose, figures, and reproducibility to their owning skills (dispatch table in [revision-question-suite.md](references/revision-question-suite.md); fully qualified names, e.g. `/auto-research:research-evidence`) with the originating question ID, frozen claim, expected artifact, falsifier, fallback, and stop condition.

Do not invoke an external model or transmit materials merely because authenticated access exists; external-model assistance requires the user's request or acceptance and a `pro-consult` route verified on the current host.

## Subagent playbook

- **Adversarial question set** — an independent subagent drafts the hardest decision-changing questions from the same frozen materials, without seeing your candidate suite; merge by decision impact, not volume.
- **Cold lens** — an independent subagent (the `research-verifier` agent in [../../agents/research-verifier.md](../../agents/research-verifier.md) fits) answers the selected questions from artifacts only, with no session memory; disagreements between its answers and the author channel are findings, not noise.
- Hand each subagent only: the frozen question selection, the named project artifacts, and the evidence contract above. Verify returned answers cite real anchors before merging — an answer without a checkable anchor is marked missing, not accepted.

## Adapt without hard-coding

Adapters add field- or venue-specific questions while preserving the universal evidence contract. A venue adapter governs audience, live submission rules, and review presentation. A field adapter governs scientific validity in that domain. Cross-domain work may use both, but neither can erase the other.

When current venue, law, policy, data, or software facts matter, verify them from authoritative sources at run time (probe `web-evidence`; on missing, raise a blocker rather than guessing). Never turn a current page limit, review process, empirical convention, or fashionable application into a permanent universal rule.

## Exit gate

Finish when the selected questions expose the decision-changing unknowns, unsupported claims, and next owner; each critical answer has an evidence anchor or explicit missing status; and the next gate has an objective stop condition. A long checklist without a decision consequence is not complete.

The docket's key conclusions must land in state, not only in the document ([../../docs/state-contract.md](../../docs/state-contract.md)):

- gate-changing verdicts → `CLI update <root> --gate "<gate_id>=<status>:<evidence>@<owner_skill>"` and refreshed `--next` actions (≤3, overflow to backlog);
- **every `user_decision` item in the docket → `CLI blocker <root> add --type user_decision --description … --queued-action …`** — a decision left only in a markdown docket is a silent skip and a contract violation;
- one `CLI event` recording the interrogation run and the docket path.

Exit only to a named owning skill or a blocker.
