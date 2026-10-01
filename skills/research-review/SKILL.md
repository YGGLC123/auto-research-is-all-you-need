---
name: research-review
description: Independently cold-review, triage, rescue, revise, finalize, or rebut a research paper against its target venue and evidence. Use when routed by /auto-research:pilot for manuscript draft review, rejected or borderline-paper rescue, reviewer-risk excavation, contribution re-positioning, pre-submission audit, response-to-reviewers, rebuttal, or revision planning. Do not use for routine drafting or silently editing before the review diagnosis is accepted.
---

# Research Review and Revision

Separate diagnosis from repair. A cold reviewer must not inherit the authors' confidence.

```
CLI = py ../../scripts/research_os.py      (POSIX: python3 ../../scripts/research_os.py)
```

## Entry

`CLI probe --phase review --write-state <root>`, then obey each verdict's `on_missing` policy ([capability-registry.md](../../docs/capability-registry.md)): `degrade` → registered fallback, recorded in state; `blocker` → `CLI blocker add` with a `queued_action`, continue reviewable work; `stop` → route elsewhere. Never assume a reviewer tool exists because prose mentions it.

**Register before running** ([attempt-tree.md](../../docs/attempt-tree.md)): every bounded try this phase makes — a search strategy, a proof route, a protocol variant, a rescue plan — is a coded node in the attempt tree. `CLI tree <root> register --kind attempt --parent <route> --title "…"` before executing, `close --status … --result "…"` at the verdict; run `CLI tree <root> list --under <route>` first so a dead sibling is never re-run.

| capability | claude-code first choice | codex first choice |
|---|---|---|
| review-panel | review-paper / review-paper-light skills | local subagents |
| pro-consult | external high-reasoning model (user-requested/accepted; verify the tier every run) | same |
| web-evidence | WebSearch/WebFetch (venue rules, prior art) | web |
| pdf-tools | Read tool (native) | pdf → pdftotext |

## Select the review mode

- `cold_review`: judge the paper from the submitted artifact and target venue alone.
- `borderline_rescue`: find the smallest changes that can move the decision boundary.
- `rejection_rebuild`: decide whether the core thesis survives and what must be rebuilt.
- `pre_submission`: attack correctness, novelty, evidence, presentation, and compliance.
- `rebuttal`: answer actual reviewer claims within the allowed evidence and word limit.
- `revision`: convert reviews into verified changes and a response ledger.

**Cold review is stage-level** ([../../docs/verification-policy.md](../../docs/verification-policy.md)): one panel per manuscript version at a gate — not per section, not after every repair. A repair pass re-opens the panel only when it changed a claim, an evidence binding, or venue compliance. Cold review defaults to independent local subagents via the `review-panel` capability. Do not invoke Pro merely because it exists: a Pro-assisted review only when the user requests or accepts it, via the registry's current authenticated route — never an assumed extension or fixed Playwright pool. Snapshot before starting a rescue or rebuild: `CLI snapshot <root> --label pre-<mode>`.

For deep review, rescue, or revision planning, invoke `/auto-research:research-interrogation` with operation `cold_audit`, `route_decision`, or `revision_plan`, passing stage, paper type, primary uncertainty, and applicable venue/field adapters. Interrogation composes the questions; this skill owns the decision-style synthesis and the accepted repair route. Keep the strategic mode separate from the immediate operation: `rejection_rebuild` normally begins with a blind `cold_audit`, advances to `route_decision` only after the thesis-survival docket, and uses `revision_plan` only after the route is accepted.

## Review from evidence

Inspect the manuscript or submission PDF, supplement, verified evidence ledger, claim map, protocols, and results manifests. Verify current venue rules from official sources (`web-evidence`) when deadlines, formats, anonymity, or rebuttal policies matter. Produce a decision-style review with summary, strengths, weaknesses, questions, confidence, and likely rating. Then create a risk ledger and register it (`CLI asset add <root> --path <ledger> --role ledger --tag risk-ledger`):

| Risk | Severity | Evidence | Decision impact | Owning phase | Minimal repair | Verification |
|---|---|---|---|---|---|---|

Distinguish fatal correctness or novelty failures from clarity issues. Test whether the paper is an application-only variant, whether baselines are strong, whether timing or leakage invalidates results, whether theorem assumptions match the method, and whether claims exceed evidence. For the numbers themselves, run the zero-context claim audit in [../../docs/claim-audit-protocol.md](../../docs/claim-audit-protocol.md) once per manuscript version at the pre-submission gate: the auditor sees only the manuscript source and the raw result files, never a ledger or a summary.

## Plan and execute revision

Diagnosis precedes execution. Recommend a route, then obtain user acceptance before changing the central claim, adding research assets, spending material compute, or rewriting the manuscript around a new identity. **Every repair item raised by a review must land in state — `CLI update --next "…@<owner-skill>"` (overflow → backlog) or `CLI blocker add` — never prose only.** Order by decision impact and dependency; route each item to its owning phase skill; do not paper over missing evidence with prose. For direct manuscript changes: snapshot or commit the diagnosed version first, make scoped edits, compile, verify references and claims, and bump `--version manuscript=vN` on the revised draft.

A review may open a visual blocker or request a new reader question, comparison, panel, or caption role. It must not silently edit a frozen Stage-2 visual prompt or use aesthetic repair to change scientific content. After the user accepts a repair that changes claims, sources, fidelity, or figure role, send it to the owning phase and refreeze through `/auto-research:research-artifacts`.

For rebuttal: quote or paraphrase each reviewer concern accurately, answer directly, cite only existing or clearly labeled new evidence, avoid adversarial tone, never promise infeasible work. Maintain a point-by-point response ledger linking each answer to a manuscript change or evidence artifact. The three hard gates (provenance, commitment, coverage), the issue-board schema, the drafting lints, and the revision checklist are in [../../docs/rebuttal-gates.md](../../docs/rebuttal-gates.md); a rebuttal that fails any gate is not finalized.

## Subagent playbook

Panel reviewers run as independent subagents, each with fresh context: prompt = frozen inputs (exact PDF/supplement paths, target venue, review mode), no author rationale, expected return format (decision-style review + rated risk rows), and a stop condition ("if you cannot open the artifact, report and stop — do not review from memory"). Merge their evidence into one ledger but never average their confidence into false consensus — disagreement between reviewers is itself a finding. Independent final verification (claims vs. evidence, compile, compliance) goes to the `research-verifier` agent, blind to this session's repairs. Independent final verification is **one** key spent at the submission gate, not a companion to each repair. The adversarial refuter is spot-check tier: send it the findings that would change the decision (fatal correctness, novelty, evidence-gap rows), and let clarity-grade rows go straight to the ledger. For the standard fan-out shape — score each dimension in parallel, then send the decision-changing findings to an adversarial refuter so only weaknesses that survive attack return — start from the [review-fanout template](../../workflows/review-fanout.md).

## Exit

Finish when every critical risk is fixed, explicitly accepted, or shown out of scope; the paper compiles and complies; claims match evidence; and an independent final pass finds no unresolved submission blocker. This exit — not the drafting in between — is where the full `CLI doctor` and the independent pass belong.

```
CLI update <root> --gate "risk_ledger=passed:<ledger path>@research-review" \
    --gate "pre_submission_review=passed:<review artifact>@research-review" \
    --clear-next --next "Package milestone@research-reproducibility"
CLI event <root> --type phase_advanced --summary "review mode <mode> closed: N risks fixed, M waived"
```

Failed gates loop back to their owner with evidence (stagnation budget → `user_decision` blocker). Route final packaging to `/auto-research:research-reproducibility`; new drafting to `/auto-research:research-manuscript`.


## Prose drafting

When the deliverable **is** the writing -- a manuscript section, a report, a handoff document, a talk
script, a cover letter -- it goes through the `prose-drafting` capability: the running model by default,
or the writer model the researcher has registered there. The division of labour holds either way: the
running model keeps every decision, every number, every verification, and the discipline scan. Give a
delegated writer the frozen facts rather than a summary of them -- numbers with their sources, scope
limits, banned words, audience, length -- and check what comes back for facts and discipline instead of
rewriting it into your own voice.
