# review-fanout — dimension review → adversarial verification

**目的** Score a manuscript/artifact on several independent dimensions in parallel, then send every
raised finding to an adversarial refuter. Only findings the refuter *cannot* break come back — the
mainline never sees a reviewer's unverified worry as if it were a fact.

**何时用** Cold review, panel review, or pre-submission audit where you want breadth (many dimensions
at once) without averaging reviewers into a false consensus. Pairs with `research-review`'s Subagent
playbook: this is the mechanized version of "merge evidence, never confidence".

**args**
- `DIMENSIONS` — the review axes for this venue/artifact (`// EDIT ME`).
- Prompt slots `<artifact>` and `<venue>` — the exact PDF/section path and the target venue.

## 脚本

```js
export const meta = {
  name: "review-fanout",
  description: "Review N dimensions in parallel, then adversarially verify every finding.",
  phases: ["Review", "Verify"],
};

// EDIT ME — the review dimensions for this artifact / venue.
const DIMENSIONS = ["novelty", "correctness", "evidence", "clarity", "compliance"];

const FINDINGS = {/* schema: rows of {dimension, claim, severity, evidence_path} */};
const VERDICT  = {/* schema: {verdict: "REFUTED|UNREFUTED|INCONCLUSIVE", attacks, note} */};

export default pipeline(
  DIMENSIONS,                                 // seed: one reviewer per dimension (stage 1 fans out)
  (d) => agent(                               // Review — inherit no author rationale
    `Cold-review ONLY the "${d}" dimension of <artifact> for <venue>. ` +
    `Return findings as rows (claim, severity, evidence_path). <=30 lines.`,
    { schema: FINDINGS },
  ),
  (findings) => parallel(                     // Verify — one refuter per raised finding
    findings.flat().map((f) => () => agent(
      `Adopt the research-refuter posture. Try to REFUTE this review finding: ${JSON.stringify(f)}. ` +
      `Counterexample / hidden assumption / quantifier slip / boundary break. VERDICT first. <=40 lines.`,
      { schema: VERDICT, subagent_type: "research-refuter" },
    )),
  ),
  (verdicts) => verdicts.filter((v) => v.verdict === "UNREFUTED"),  // confirmed = survived the attack
);
```

## 产物落盘

Reviewers write each dimension's full notes under `research/review/<run>/<dimension>.md`; refuters write
their attack ledgers under `research/review/<run>/verify/`. The workflow returns only the confirmed
rows — feed them straight into the risk ledger (`CLI asset add … --tag risk-ledger`) and into
`CLI update --next "<repair>@<owner-skill>"`, so every surviving weakness lands in state, not prose.

## 裁剪指引

- **Semantics of `confirmed`:** a finding stands when the refuter returns `UNREFUTED`. A `REFUTED`
  finding was a bad worry — it is dropped, not repaired. `INCONCLUSIVE` stays on the list, tagged for a
  human look; do not silently promote or drop it.
- **The refuter stage is spot-check tier** ([../docs/verification-policy.md](../docs/verification-policy.md)):
  filter findings to the decision-changing ones (fatal correctness, novelty, evidence gaps) before the attack
  stage, and send clarity-grade rows straight to the ledger. Attacking every raised row spends the budget the
  real weaknesses needed.
- **One dimension per reviewer.** Do not collapse dimensions to save agents — the independence is the
  point, and a single overloaded reviewer re-imports the correlation you are trying to break.
- Scale `DIMENSIONS` to the artifact: 3 for a light check, the full venue rubric for a submission audit.
- For a rebuttal pass, swap the refuter prompt for one that attacks *the reviewer's* claim instead of
  the paper's, keeping the same UNREFUTED-survives filter.
