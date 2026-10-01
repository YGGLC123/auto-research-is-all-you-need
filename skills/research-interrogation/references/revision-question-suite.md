# Research Revision Interrogation Suite

This is a composable question system, not a fixed review rubric, venue template, or one-shot prompt. Use it to generate author questions, interrogate supplied evidence, cold-review a paper, design a rescue route, or build a revision docket. Its value comes from selecting the right questions for the paper's current uncertainty, not from answering every question in one response.

## Compose the interrogation

Before loading a question pack, set five fields:

1. `operation`: `ask`, `answer_from_evidence`, `cold_audit`, `route_decision`, `revision_plan`, or `portfolio_audit`;
2. `stage`: idea, preliminary study, method formation, active experiments, manuscript, rejected/borderline, or final submission/rebuttal;
3. `paper_type`: theoretical, empirical, domain application, systems/tooling, or survey/position;
4. `primary_uncertainty`: the single question whose answer could most change the project or decision;
5. `budget`: compact, standard, or exhaustive.

Then choose one primary question pack, at most one secondary pack, and zero to two adapters. An adapter may specialize a venue, field, data regime, or paper family; it adds questions and changes priorities but never replaces the universal evidence contract.

For a cross-domain paper, a venue adapter and a field adapter may be combined. For example, a finance application targeting AAAI can load both the AAAI and finance adapters. A finance journal submission normally loads the finance adapter without the AAAI adapter.

## Question-pack registry

| Pack | What it interrogates | Load when |
|---|---|---|
| [problem-contribution.md](question-packs/problem-contribution.md) | Material boundary, real problem, gap, contribution, value, scope, and transfer | The center question or paper identity is uncertain |
| [technical-evidence.md](question-packs/technical-evidence.md) | Closest prior, novelty, formal claims, method integrity, experiments, data, and artifacts | Correctness, novelty, or evidence is the active gate |
| [story-visual-review.md](question-packs/story-visual-review.md) | Narrative, pedagogy, positioning, figures, fast-read behavior, and reviewer stress | The assets exist but readers may misread or undervalue them |
| [revision-execution.md](question-packs/revision-execution.md) | Route selection, dependency planning, patch ledger, page allocation, rebuttal, and final QA | Diagnosis must become an executable plan |

Do not load all four by default. Exhaustive mode means sequential passes with checkpoints, not one enormous answer.

## Stage-aware defaults

| Stage | Primary pack | Optional secondary | Typical output |
|---|---|---|---|
| Idea or preliminary study | problem/contribution | technical/evidence | A falsifiable question, novelty risks, and a kill/continue gate |
| Method taking shape | technical/evidence | problem/contribution | Frozen specification, claim boundaries, and validation obligations |
| Experiments underway | technical/evidence | revision/execution | Protocol risks, decision-critical runs, and stopping rules |
| Manuscript draft | story/visual/review | technical/evidence | Reader misunderstandings, claim debt, and a prioritized repair docket |
| Rejected or borderline | problem/contribution | technical/evidence before route; revision/execution only after diagnosis | Thesis-survival diagnosis, then route comparison and approval gate |
| Final submission | revision/execution | story/visual/review | Compliance, consistency, replay, and fast-read closure |
| Rebuttal | revision/execution | only the pack implicated by actual reviews | Evidence-bounded point-by-point response |

Override these defaults when artifact evidence reveals a higher-risk gate. A polished draft with invalid timing still routes to technical/evidence first.

## Paper-type emphasis

- `theoretical`: definitions, quantifiers, assumptions, counterexamples, necessity, tightness, proof obligations, and the exact relationship between theorem and promise;
- `empirical`: construct validity, information timing, leakage, baselines, uncertainty, multiple testing, failure analysis, and generalization;
- `domain application`: domain-native problem and labels plus methodological novelty, realistic decision consequences, and field-specific alternatives;
- `systems/tooling`: workload realism, interfaces, reliability, efficiency, ablations, deployment boundary, and reproducible artifact behavior;
- `survey/position`: coverage, taxonomy stability, evidence traceability, omitted schools of thought, and a falsifiable synthesis.

## Universal evidence contract

List only materials actually accessible. Record version, page count, compile status, code/data/result availability, previous reviews, and any mismatch between PDF and source. Do not imply that an unread file, image, proof, or artifact was inspected.

Tag answers with their strongest support:

- `[MAIN]`, `[SUPP]`, `[SOURCE]`, `[CODE]`, `[DATA]`, `[EXT]`;
- `[INFERENCE]` for a defensible inference;
- `[PROPOSED]` for a prospective addition;
- `[UNKNOWN]` when the material cannot decide.

Use `VERIFY_REQUIRED`, `SEARCH_REQUIRED`, and `RESULT_PENDING` for open dependencies. Separate:

- `E0`: directly observed fact;
- `E1`: capability established directly by a verified theorem, experiment, certificate, or artifact;
- `E2`: conditional consequence supported by E1;
- `E3`: unvalidated impact, deployment, or future use.

Every answered question should preserve this ledger shape when the answer affects a decision:

| Question ID | Status | Answer or unresolved fork | Evidence and anchor | Confidence | Decision impact | Next test | Owner |
|---|---|---|---|---|---|---|---|

Use `ANSWERED`, `PARTIAL`, `MISSING`, `CONTESTED`, or `NOT_APPLICABLE` for status. Explain every `NOT_APPLICABLE`; never use it to avoid a hostile question.

Use `FATAL`, `BLOCKER`, `HIGH`, `MEDIUM`, and `POLISH` for decision risk. Do not average away a fatal correctness, construct-validity, ethics, or novelty failure.

## Interrogation modes

- `ask`: return a prioritized questionnaire for authors or collaborators; do not pretend to answer missing facts.
- `answer_from_evidence`: answer only what the supplied material supports and expose unknowns.
- `cold_audit`: use an independent reviewer lens and state likely rejection logic before repairs.
- `route_decision`: compare conservative repair, substantive rebuild, and research upgrade; recommend one route but gate material execution.
- `revision_plan`: after diagnosis is accepted, translate selected answers into owned, dependency-ordered work packages.
- `portfolio_audit`: compare related manuscripts, assign contribution ownership, expose overlap, and test whether each paper retains independent value.

Within any mode, vary the stance deliberately:

- `forensic`: What exactly is supported, where, and by which evidence type?
- `adversarial`: What is the strongest fair counterclaim, nearest-neighbour substitution, or alternative explanation?
- `constructive`: What is the smallest new asset that would change the answer?
- `boundary-seeking`: Where does the theorem, measurement, causal interpretation, or practical claim stop?
- `transfer`: Which parts are domain-independent, and what must be redefined or revalidated elsewhere?

## Output shapes

Choose the shape that matches the operation:

- `questionnaire`: ranked questions, why each matters, required input, and what different answers imply;
- `evidence audit`: answered ledger plus unknowns and unsafe claims;
- `decision docket`: strongest thesis, fatal break, route options, recommendation, and approval boundary;
- `revision blueprint`: frozen identity, claim/evidence plan, dependency order, owners, artifacts, and acceptance tests;
- `portfolio audit`: contribution ownership, shared assets, text/result overlap, and independent-value tests across related manuscripts;
- `submission check`: current venue contract, consistency, reproducibility, anonymity, ethics, and unresolved-status closure.

Do not force a fixed count of questions, contributions, stories, figures, papers, pages, or experiments. Use counts only as local compression devices and explain exceptions.

## Dispatch answers to owning skills

The suite diagnoses and specifies work; it does not silently take ownership of all work:

| Answer implies work on | Route to |
|---|---|
| Closest prior, citation verification, data provenance | `/auto-research:research-evidence` |
| Research question, contribution redesign, cross-domain transfer | `/auto-research:research-discovery` |
| Formal statement, proof, boundary, counterexample | `/auto-research:research-theory-siege` |
| Objective, architecture, algorithm, or interface | `/auto-research:research-method` |
| Protocol, baseline, ablation, statistics, implementation, run | `/auto-research:research-experiments` |
| Title, abstract, introduction, structure, precise prose | `/auto-research:research-manuscript` |
| Figure, table, diagram, exact scientific plot | `/auto-research:research-artifacts` |
| Clean replay, checklist, package, archive, release | `/auto-research:research-reproducibility` |

Each dispatched item carries the originating question ID, frozen claim, evidence gap, expected artifact, stop condition, and return contract. Route only the one or two items needed to close the active gate.

## Adapter registry

- [aaai.md](adapters/aaai.md): a thin, current-cycle AAAI venue lens; never the core question system.
- [finance-top-journals.md](adapters/finance-top-journals.md): a finance field and top-journal-quality lens for economics, identification, point-in-time data, market mechanisms, and domain-native evidence.

Add future adapters for other venues or fields without editing the universal packs unless the underlying scientific question is genuinely universal.

## Stop rules

Stop and ask for input only when different answers would materially change the selected pack, route, central claim, or authorized resource use. Stop execution at an approval gate before adding research assets, changing paper identity, spending material compute, transmitting confidential material, or editing a manuscript around a new story.

The suite succeeds when it exposes the decision-changing unknowns and turns the accepted answers into verifiable work. It fails when it produces a long checklist that neither changes a judgment nor owns a next test.
