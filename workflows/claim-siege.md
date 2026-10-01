# claim-siege — FALSIFY → CONSTRUCT → VERIFY → RED_TEAM

**目的** Drive one frozen formal claim through the attack state machine: fan out refuters to falsify it,
build a proof only if it survives, verify that proof in a fresh context, then red-team the verified
construction. Survivors advance; a break at any stage ends the siege with a recorded counterexample.

**何时用** Hardening a central theorem, an identifiability/complexity placement, or any claim that must
reach a checkable boundary. This is the mechanized skeleton of `research-theory-siege`'s state machine;
keep the discipline (fresh contexts, orchestrator-writes-`CLOSED`) that skill defines.

**args**
- `CLAIM` — the frozen claim id; its verbatim spec lives at `research/claims/<id>/spec.md` (`// EDIT ME`).
- `N` — parallel falsifiers (scale to claim risk: 1 for a lemma, 4+ for a central theorem).

## 脚本

```js
export const meta = {
  name: "claim-siege",
  description: "FALSIFY -> CONSTRUCT -> VERIFY -> RED_TEAM for one frozen claim; only survivors advance.",
  phases: ["Falsify", "Construct", "Verify", "RedTeam"],
};

const CLAIM = "C01";                                   // EDIT ME — frozen claim id
const SPEC  = `research/claims/${CLAIM}/spec.md`;      // verbatim spec (not a paraphrase), root-relative
const DIR   = `research/claims/${CLAIM}`;
const N     = 4;
const VERDICT = {/* schema: {verdict, evidence_path, note} */};

export default async function () {
  // FALSIFY — N independent refuters attack the frozen spec; any REFUTED ends the siege
  const falsify = await parallel(range(N).map((i) => () => agent(
    `research-refuter posture. Attack the claim frozen at ${SPEC} (channel ${i}): counterexample, ` +
    `hidden assumption, quantifier slip, boundary break. Write ${DIR}/attacks/falsify-${i}.md; ` +
    `VERDICT first line. <=40 lines.`,
    { schema: VERDICT, subagent_type: "research-refuter" },
  )));
  if (falsify.some((v) => v.verdict === "REFUTED"))
    return { claim: CLAIM, status: "REFUTED", attacks: falsify };

  // CONSTRUCT — only a survivor earns a proof, built under the EXACT spec
  const proof = await agent(
    `Construct a proof of the claim under the exact spec at ${SPEC}. ` +
    `Write ${DIR}/artifacts/proof.md; return the load-bearing steps only. <=40 lines.`,
  );

  // VERIFY — research-verifier semantics: fresh context, spec + artifact, no builder confidence
  const verify = await agent(
    `Independently verify the proof at ${DIR}/artifacts/proof.md against the frozen spec at ${SPEC}. ` +
    `You did not build it; do not trust it. VERIFIED / REFUTED / INCONCLUSIVE first. <=40 lines.`,
    { schema: VERDICT, subagent_type: "research-verifier" },
  );
  if (verify.verdict !== "VERIFIED")
    return { claim: CLAIM, status: verify.verdict, proof, verify };

  // RED_TEAM — attack the *verified* construction once more before recommending closure
  const red = await parallel(range(2).map((i) => () => agent(
    `research-refuter posture. The proof at ${DIR}/artifacts/proof.md passed verification. Break it anyway ` +
    `(channel ${i}); write ${DIR}/attacks/redteam-${i}.md; VERDICT first. <=40 lines.`,
    { schema: VERDICT, subagent_type: "research-refuter" },
  )));
  const status = red.some((v) => v.verdict === "REFUTED") ? "REFUTED" : "CANDIDATE_CLOSED";
  return { claim: CLAIM, status, proof, verify, red };  // models recommend CANDIDATE_CLOSED; you write CLOSED
}
```

## 工序衔接 (`procedures.py claim`)

Run from the project root, mirror each transition into the claim's five-state machine so the attempt-tree
C-node and the counterexample regression set stay in sync with the siege:
`py <plugin>/scripts/procedures.py claim set --id C01 --status <verdict>` after FALSIFY/VERIFY/RED_TEAM,
and `py <plugin>/scripts/procedures.py claim add-counterexample …` whenever a refuter returns `REFUTED`.
The workflow *decides*; the CLI *records*. Never let the workflow's return be the only trace — and never
let a model write `CLOSED`; the orchestrator does that after adjudication.

## 裁剪指引

- **`range` / `${...}` are yours** — `range(N)` is any `[...Array(N).keys()]` helper; the `attacks/` and
  `artifacts/` subdirs must exist (create them at claim-lock time).
- **Early-exit is the point.** Do not "keep going to be thorough" past a `REFUTED` — a live counterexample
  is the result; construct nothing on a broken claim.
- **This is the escalated shape, not the default.** A routine lemma closes on one key — a recomputation where
  a mechanical criterion exists, one `research-verifier` pass where it does not
  ([../docs/verification-policy.md](../docs/verification-policy.md)). Run the full FALSIFY×N + VERIFY + RED_TEAM
  machine for the central theorem, a fragile object, or when the user asks for it; otherwise drop `N` to 1 and
  skip RED_TEAM.
- Diversity beats redundancy: for a hard claim, mix a `research-reproducer` pass into RED_TEAM (re-run any
  computational witness) rather than adding a fifth identical refuter.
- Fork a new `CLAIM` id for any weakening; never overwrite the original target's spec.
