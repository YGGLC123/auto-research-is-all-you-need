# Stage and Paper-Type Routing (v2)

Used by `/auto-research:pilot`. Read when stage selection is ambiguous, the project spans workstreams, or a gate fails.

## Stage evidence hierarchy

Prefer evidence in this order: current user intent, reviewer decision or deadline, runnable artifacts, manuscript artifacts, recorded state, folder names. A manuscript draft does not imply experiments or novelty are ready. For mixed projects route the highest-risk unresolved gate, not the most advanced artifact.

## Stages: exit gates and loop-backs

Gate ids are conventions, not an enum — keep them stable per project. `owner` = the skill that must fix a failure.

| Stage | Minimum exit gate (typical gate ids) | Loop back to owner when |
|---|---|---|
| `idea_only` | falsifiable question + kill criteria (`problem_frozen`) | closest prior already answers it; no measurable target → `research-discovery` |
| `preliminary_study` | verified nearest-prior map, feasible data path, scoped novelty thesis (`novelty_map`, `data_path`) | critical full text or provenance missing → `research-evidence` |
| `method_taking_shape` | frozen interfaces, mechanism, assumptions, complexity (`method_spec`) | a component lacks necessity or identifiability → `research-method`; formal gap → `research-theory-siege` |
| `experiments_underway` | leakage-safe protocol, strong baselines, reproducible primary results (`protocol_frozen`, `primary_results`) | split/label/metric/implementation invalid → `research-experiments` |
| `manuscript_draft` | claim-evidence map, coherent story, verified citations (`claim_evidence_map`, `manuscript_compile`) | a claim lacks evidence → owner of the missing evidence, not prose |
| `rejected_or_borderline` | reviewer-risk ledger, fatal-flaw decision, revised contribution (`risk_ledger`, `route_decision`) | rescue needs new theory/data/experiments → that owner |
| `final_submission_or_rebuttal` | venue compliance, cold review, repro audit, package or response (`pre_submission_review`, `delivery_lock`) | a critical claim unsupported → its owner; figure not embedded → `research-artifacts` |

Failure handling is Layer 1 of [loop-protocol.md](loop-protocol.md): route back with evidence, budgeted by `loop.stagnation_budget`, then escalate to a `user_decision` blocker.

These exit gates are also the verification doors: the full `doctor` run, the stage-level cold review, and the one key that closes a core scientific object are spent **here**, not inside the stage ([verification-policy.md](verification-policy.md)).

## Primary route by stage

`idea_only` → research-discovery · `preliminary_study` → research-evidence then research-discovery · `method_taking_shape` → research-method (+ research-theory-siege for formal claims) · `experiments_underway` → research-experiments · `manuscript_draft` → research-manuscript · `rejected_or_borderline` → research-review · `final_submission_or_rebuttal` → research-review then research-reproducibility.

## Paper-type lenses

- **theoretical_ml** — definitions, quantifiers, encoding, assumptions, necessity, theorem ancestry explicit; fresh-context verification and mechanical checks where feasible.
- **empirical_ml** — freeze the primary endpoint and protocol before broad search; audit leakage, multiplicity, tuning budget, compute parity, seeds, uncertainty, effect size.
- **domain_application** — domain timing, label validity, missingness, identity mapping, deployment realism are part of the method; compare domain-native and generic baselines; no causal language from predictive association.
- **systems_tooling** — workloads, SLOs, failure injection, cost, throughput, latency, reliability, operational baselines.
- **survey_position** — inclusion criteria, coverage dates, search trace, taxonomy rules, counterpositions, and a falsifiable central thesis.

## Multi-workstream routing

Keep one `active_workstream`; queue the rest in `backlog`. Choose the unresolved gate with the highest product of severity × uncertainty × downstream dependency. Parallelize only independent work (e.g. evidence collection vs implementation) and merge through state artifact pointers, never through shared prose context.
