# Rebuttal gates — rules for the `rebuttal` mode of research-review

Distilled from ARIS `skills/rebuttal/SKILL.md` (MIT, [wanshuiyin/Auto-claude-code-research-in-sleep](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep); notice in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)). Transport-free: the stress test goes to `review-panel` or, when the user asks, `pro-consult`. Everything below is a rule the mode must satisfy, not a phase script.

## Inputs that must be explicit before drafting

Paper source; the raw reviews verbatim with reviewer ids; venue rules (name, character or word limit, text-only vs revised PDF, rendering mode); the round (initial or follow-up). **Missing venue rules, limit, or rendering mode → stop and ask.** No silent defaults.

Rendering mode changes the artifact shape:
- `single_document` — one shared response: short opener with 2–4 global resolutions, per-reviewer numbered answers, short closing for the meta-reviewer (resolved / remaining / why accept). Budget roughly 10–15 % opener, 75–80 % per-reviewer, 5–10 % closing.
- `per_reviewer_thread` — one self-contained file per reviewer, no global opener, no "see Reviewer X" cross-references, each readable alone. Shared setup or metric definitions go in one canonical block (≤ 150 words) reused verbatim in each file that needs it.

## Three hard gates — fail any one, do not finalize

1. **Provenance.** Every factual statement maps to exactly one of: `paper`, `review`, `user_confirmed_result`, `user_confirmed_derivation`, `future_work`. No source → blocked.
2. **Commitment.** Every promise maps to `already_done`, `approved_for_rebuttal`, or `future_work_only`. Not approved by the user → blocked.
3. **Coverage.** Every reviewer concern ends as `answered`, `deferred_intentionally`, or `needs_user_input`. No concern disappears.

## Issue board (one row per atomic concern)

`issue_id` (R1-C2) · `reviewer` · `round` · `raw_anchor` (short quote) · `issue_type` ∈ {assumptions, theorem_rigor, novelty, empirical_support, baseline_comparison, complexity, practical_significance, clarity, reproducibility, other} · `severity` ∈ {critical, major, minor} · `reviewer_stance` ∈ {positive, swing, negative, unknown} · `reviewer_priority` ∈ {standard, pivotal} · `response_mode` · `status` ∈ {open, answered, deferred, needs_user_input}.

`pivotal` = a reviewer whose rating is low or borderline, whose concerns are addressable, and whose confidence carries weight; there may be more than one. Pivotal rows get disproportionate drafting and stress-test budget.

Response modes: `direct_clarification` · `grounded_evidence` · `nearest_work_delta` · `assumption_hierarchy` · `narrow_concession` · `future_work_boundary` · `structural_distinction`.

`structural_distinction` is for "your method reduces to X / is just generic Y / is subsumed by Z": agree on the local reduction, then show the structural feature the parameterization preserves that X/Y/Z does not, backed by a concrete mechanism (theorem dependency, derivation step, or empirical consequence). Never as rhetoric without the mechanism.

## Drafting rules per answer

Sentence 1 direct answer; sentences 2–4 grounded evidence; last sentence the implication for the paper.

- Evidence over assertion; concrete numbers for counter-intuitive points; name the closest prior work and the exact delta for novelty disputes.
- Minimum sufficient evidence per concern: usually one numerical anchor that maps to *that reviewer's* ask. Cut metrics other reviewers care about.
- If a threshold or hold-out was fixed before any generated sample was inspected, say so in those words — only when true.
- Surface non-obvious design choices up front (compute-matched vs epoch-matched, atypical seed protocol, restricted parameter subset), with numbers where they clarify.
- Concede narrowly when the reviewer is right, and pair the concession with what remains true and why it still supports the contribution.
- For theory, separate core assumptions from technical ones.
- Answer friendly reviewers too; reinforce their framing.
- Answer an unwinnable point once and move on.
- If no strong evidence exists, say less, not more.

Hard rules: never invent experiments, numbers, derivations, citations, or links; never promise what the user has not approved; any new reference goes through DBLP → CrossRef → `[VERIFY]`, never from memory.

## Revision plan (the commitment gate's artifact)

A single flat checklist, one atomic paper edit per line, each line carrying its `issue_id`, its commitment class, owner, and status; regrouped views by section and by severity; a commitment summary (counts per class plus blocking `needs_user_input` items); an out-of-scope log naming every concern that will *not* trigger an edit and why. Every paper-edit promise in the draft must appear here and vice versa — an orphan on either side is a commitment-gate violation. On follow-up rounds update in place; never regenerate.

## Lints before the stress test

1. Coverage — every issue maps to a draft anchor.
2. Provenance — every factual sentence has a source.
3. Commitment — promises approved, and draft ↔ revision plan is a bijection.
4. Tone — flag aggressive, submissive, or evasive phrasing.
5. Consistency — no contradictions across reviewer replies.
6. Limit — exact character count; compress in the order redundancy → friendliness → opener → wording, never by dropping a critical answer.
7. Thread-local context (`per_reviewer_thread` only) — each file intelligible alone.
8. Adversarial design-choice scan — for each experimental claim ask what non-obvious choice a hostile reviewer could reverse-engineer; disclose it in one line.

## Stress test

One external round on the full response set, then focused rounds on each pivotal response until the reviewer returns no new substantive issue; hard cap 5. The reviewer is asked for: unanswered or weakly answered concerns; unsupported factual statements; risky or unapproved promises; tone problems; the paragraph most likely to backfire with the meta-reviewer; minimal grounded fixes only. Save each round's raw output verbatim. Any remaining hard-gate blocker → revise before finalizing.

## Follow-up rounds

Append new reviewer text verbatim; link to existing issues or open new ones; draft the **delta** only; update the revision plan in place; re-run the lints. Escalate technically, not rhetorically; concede if the reviewer is right; stop arguing when the reviewer is immovable and no new evidence exists. Cap 3 follow-up rounds per thread.

## Outputs

`single_document`: a paste-ready plain-text version at the exact limit, plus a rich version with `[OPTIONAL — cut if over limit]` blocks. `per_reviewer_thread`: one file per reviewer plus the shared setup block. Both: the issue board, the revision plan, the verbatim review record, and the list of lines still needing the user's approval.
