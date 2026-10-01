# Claim audit protocol — every number in the paper against the raw files

Distilled from ARIS `skills/paper-claim-audit/SKILL.md` and the deterministic pre-check of `skills/result-to-claim/SKILL.md` (MIT, [wanshuiyin/Auto-claude-code-research-in-sleep](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep); notice in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)). Fits gate 4 of [verification-policy.md](verification-policy.md): run **once per manuscript version** at the pre-submission gate, and again only if a number or a result file changed. Never on a timer.

## Why a separate protocol

The executor ran the analysis and wrote the paper, so it "knows" what the numbers should be. That is where 84.7 becomes 85.3, the best seed replaces the mean, the delta is computed against the wrong denominator, and "consistently" covers two datasets. The auditor must have **no expectations**: it receives the claims and the evidence, nothing else.

## Stage 1 — deterministic existence check (no model, no cost)

Before any judging read, list every cited number with its cited source file and run `scripts/evidence_check.py <root> --batch claims.json` (claims are `[{"id", "value", "source"}]`, `source` relative to the root, globs allowed). Statuses:

- `verified` — the cited value occurs in the cited file (existence only, **not** support);
- `value_not_found` / `path_missing` — hallucinated evidence: mark the claim unsupported immediately, do not spend a judging read defending it;
- `unparseable` — no usable value or source; goes to stage 2 as is.

The matcher is deliberately conservative (allow-list boundaries; dates, versions, fractions, Unicode dashes fail closed), so a false negative is cheap and a false `verified` is the bug it must never have. The script exits 1 when it finds hallucinated evidence — that is the signal, not a failure; judge success by valid JSON output.

## Stage 2 — zero-context audit

**What the auditor receives**: the manuscript source (`.tex`, tables, captions) and the raw result files (`.json`, `.csv`, `.tsv`, run configs, regression outputs) plus the stage-1 status table. **What it must not receive**: run ledgers, experiment logs, narrative reports, prior audits, executor summaries, conversation history. Paths only; no interpretation. A fresh context every run — never a continued thread.

The auditor is dispatched as `research-verifier` (or, for a central result, a cross-family key: `codex:codex-rescue` with the inputs inlined, or `pro-consult` when the user asks). Its brief:

**A. Extract** every quantitative claim: location (section, table, caption, inline), exact text, the number or comparison.
**B. Trace** each to evidence: which file, the exact value there, match status.
**C. Check** the seven failure modes:

1. Number inflation — only standard rounding to the displayed precision is allowed (84.7 → 85 is fine; 84.7 → 85.3 is not).
2. Best-seed / best-slice cherry-pick — does the paper say average, median, or best, and does the file agree?
3. Config mismatch — compared methods run under the same splits, samples, hyperparameters, denominators?
4. Aggregation mismatch — claimed count of runs, seeds, or observations vs the actual count in the files.
5. Delta arithmetic — every relative improvement recomputed from the two absolute numbers.
6. Caption–content mismatch — every caption checked against what the table or figure actually shows.
7. Scope overclaim — "consistently", "in all settings", "robust to" checked against the evaluated scope.

## Per-claim record

`claim_id` · `location` · `paper_text` (verbatim) · `paper_value` · `evidence_file` · `evidence_value` · `status` ∈ {exact_match, rounding_ok, ambiguous_mapping, missing_evidence, config_mismatch, aggregation_mismatch, number_mismatch, scope_overclaim, unsupported_claim} · `details` (only when not exact).

## Verdict table

| Input state | Verdict |
|---|---|
| no numeric claims in the manuscript | `NOT_APPLICABLE` |
| numeric claims, no raw result files reachable | `BLOCKED` |
| all claims reconcile | `PASS` |
| rounding drift only, nothing material | `WARN` |
| any material mismatch | `FAIL` |
| auditor could not run or returned malformed output | `ERROR` |

The audit **emits, it does not block**: the verdict, the per-claim table, and the list of files consulted are written (Markdown for people, JSON for the verifier) and registered as an asset; the pre-submission gate decides. `FAIL` means the draft is not submission-ready until every mismatch is fixed **from the result files** (fix the number, not the sentence) and the audit is re-run on the changed spans. Record the exact input set (manuscript files and result files read) so a later re-run can tell whether anything it audited has changed.

## Relation to existing roles

- `research-experiment-runner` (polishing phase) already traces every table cell to a run id and file and raises `NUMBER_NOT_IN_ANY_RUN`; this protocol is the **independent** pass over the same question, run by an agent that never saw the ledger.
- `research-verifier` remains the one key at the gate; this document is the brief it is handed for numbers.
- For finance tables (coefficients, t-statistics, p-values, sample counts), failure modes 3–5 and 7 are the ones that bite; a statcheck-style recomputation of t/p/df consistency is a natural stage-1 extension (see the Anti-Autoresearch note in the merge notes).
