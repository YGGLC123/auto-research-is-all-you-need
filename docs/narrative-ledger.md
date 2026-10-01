# Narrative Ledger — paired reader/internal/TODO documents for a paper's argument

Origin: the narrative tree v1.0 of a real paper, 2026-08-11. Community mechanics
absorbed from fact-check v0.2 (evidence-ledger schema, load classes, 7-verdict
set, cross-claim flags) and claude-scholar (Evidence Gate).

## The three-file pattern

| File | Audience | Authority |
|---|---|---|
| Reader version (正式版) | referee / stranger | wording |
| Internal version (内部版) | the agent, every cycle | claim strength (state words) |
| TODO (待办) | execution | open obligations |

Conflict rule: if a reader-version sentence is stronger than its internal
state word, fix the reader version. TODO rows and internal cards cross-link
both ways; a red TODO blocks its node from being finalized.

## ID system (grep-stable, shared across all three files)

N-<sec> node · C-<sec><i> load-bearing claim · D-## number · R-## citation ·
F-## formula/theorem · S-## pending-reading slot · G-## banned phrase ·
Q-## cold-review question · T-## todo.

## Internal card schema (one per reader-version section)

Claim line: `C-xxx [load|verdict] statement → bindings`.
- load: critical (argument dies with it) / supporting / passing.
- verdict for claims: CLOSED / OPEN / pilot / PENDING_READING / in-flight / awaiting-review.
- verdict for citations (7-set): SUPPORTED / SUPPORTED_W_CAVEAT / PARTIAL /
  MISREPRESENTED / UNRETRIEVABLE / OUTDATED / NEEDS_EXPERT.
- citation depth is a separate column: full-text / abstract / unverified.
- priority triage: red (before finalization) / yellow (before submission) / green.

## Cross-cutting registries (part B)

R citations · D numbers (value | layer: DERIVED/CALIBRATED/pilot/measured/PENDING
| artifact path | sections) · F formulas (status words quoted verbatim, gloss may
never exceed registry) · artifact map · S slot table (fill slots BEFORE editing
the reader version) · G banned phrases · Q cold-review question↔card map.

## Hard rules

1. **Evidence Gate**: nothing enters the reader version unless its internal
   binding row is complete, or it is explicitly PENDING_READING/speculative.
   A number without an artifact may not enter the D table; a number outside
   the D table may not enter prose.
2. **Slot discipline**: when readings land, update the S table first, then the
   reader version. Cold-review verdicts land in Q table first.
3. **Consistency script** (run on every edit): bidirectional grep — every
   number/author in the reader version must appear in D/R tables and vice versa.
4. **Three-flag self-audit** after every major edit:
   - Citation Padding: "independent" corroborations tracing to one source
     (canonical failure: three 45.0 readings = one prior-mean rule).
   - Hotspot Cluster: >=3 audit failures in one section = structural weakness.
   - Authority Mask: source prestige standing in for verification.
5. **Independent restatement loop**: fresh-context reviewers (codex CLI + a
   verifier subagent that recomputes) restate the whole argument in their own
   words; patch every comprehension drift; converged when a fresh restatement
   reproduces all load-bearing numbers and their provenance with zero drift.
   The recomputing reviewer is the one that catches missing load-bearing
   details (e.g. an unstated weighting that makes baselines unreproducible).
