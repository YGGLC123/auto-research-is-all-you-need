# Novelty, Technical, and Evidence Questions

Load this pack when novelty, correctness, method integrity, empirical validity, or artifact evidence is the active gate. Route searches, proofs, implementations, and runs to their owning skills rather than answering them speculatively.

## Q20 — Closest-prior and novelty boundary

- `Q20.1` Which papers are direct nearest neighbours in problem, object, inputs/outputs, assumptions, mechanism, and evidence—not merely keyword neighbours?
- `Q20.2` Which classic work defines the problem or baseline that recent papers inherit?
- `Q20.3` What terminology from another field could retrieve equivalent prior work?
- `Q20.4` What does each neighbour enable that this paper does not, and vice versa?
- `Q20.5` Does the paper introduce a new problem, object, theorem, algorithm, certificate, empirical fact, dataset, application mapping, or unifying view?
- `Q20.6` Is the claimed result a special case, corollary, direct composition, parameter change, terminology swap, or domain shell?
- `Q20.7` If the paper's terminology is replaced with the closest neighbour's terminology, what remains novel?
- `Q20.8` What is the strongest plausible “X + Y” attack, and can current evidence answer it in two precise sentences?
- `Q20.9` Does the paper cite and compare against the strongest competing result, including dimensions where that work wins?
- `Q20.10` Which novelty claims are `SAFE_NOW`, `DOWNGRADE`, `REQUIRES_NEW_ASSET`, or `DELETE`?
- `Q20.11` What minimal new asset would create knowledge rather than more implementation effort?
- `Q20.12` Which strong words—first, general, exact, complete, robust, practical, scalable, universal, deployable—lack a verified evidence debt repayment?

Use a contribution-difference matrix with columns chosen for the paper, normally: problem, intended decision/user, formal object, inputs, outputs, information/access, assumptions, mechanism, guarantees, complexity, data, evaluation, artifact, scope, new capability, and neighbour advantage.

## Q21 — Definitions and formal objects

- `Q21.1` Is every central definition necessary, non-circular, internally consistent, and stable under equivalent representations?
- `Q21.2` Was a definition designed to capture the real problem or tuned to make the theorem easy?
- `Q21.3` Are domains, codomains, units, quantifiers, probability spaces, time indices, information sets, and randomness explicit?
- `Q21.4` Do notation and terminology drift across abstract, method, theorem, experiment, and supplement?
- `Q21.5` Which observable object corresponds to each mathematical object?
- `Q21.6` Is the representation identifiable, or can different latent situations yield the same observation and different target answers?
- `Q21.7` Are equivalence classes, invariances, symmetries, missingness, or versioning handled explicitly?
- `Q21.8` What is the smallest valid instance and smallest invalid instance of the definition?

## Q22 — Claims, theorems, and proof obligations

- `Q22.1` Does the main theorem answer the question promised on the first page?
- `Q22.2` Are assumptions necessary, interpretable, testable where appropriate, and weaker than the conclusion?
- `Q22.3` Does an assumption hide the result, rule out the hard case, or contradict the application setting?
- `Q22.4` Are quantifiers, conditioning, probability, approximation, asymptotics, and constants stated precisely?
- `Q22.5` Is a theorem original, imported, adapted, or a corollary? Is ownership visible?
- `Q22.6` What is the exact proof obligation for each new claim?
- `Q22.7` Which intermediate lemma carries the real insight, and should it be promoted?
- `Q22.8` Is necessity, tightness, a matching bound, separation, or impossibility boundary required to make the result informative?
- `Q22.9` What counterexample appears when each major assumption is removed?
- `Q22.10` Does a finite check validate implementation only, or is the text wrongly treating it as a proof?
- `Q22.11` Can a proof sketch communicate the key construction, invariant, or obstruction without hiding the hard step?
- `Q22.12` What is the minimal honest weakening if the strongest theorem target fails?
- `Q22.13` Which formal claim should be `KEEP`, `PROMOTE`, `DEMOTE`, `MERGE`, `RENAME`, `MOVE`, or `DELETE`?

Freeze a theorem claim before routing it to `/auto-research:research-theory-siege`: statement, definitions, assumptions, quantifiers, allowed tools, counterexample target, proof standard, mechanical check, failure fallback, and stop condition.

## Q23 — Method and mechanism integrity

- `Q23.1` What precise objective or decision rule is optimized, and does it align with the claimed goal?
- `Q23.2` Which module supplies the claimed gain or guarantee? What fails when it is removed?
- `Q23.3` Is the method distinguishable from a known architecture plus a new data representation or loss?
- `Q23.4` Are training, inference, update, and deployment information sets identical to those assumed in the story?
- `Q23.5` Are interfaces, dimensions, normalization, initialization, stopping criteria, and computational complexity specified?
- `Q23.6` Which hyperparameters or design choices were selected after seeing test outcomes?
- `Q23.7` Does the method exploit a shortcut, leakage channel, group identity, timestamp, or data artifact?
- `Q23.8` Is claimed interpretability causal, faithful, descriptive, or merely sensitivity of the fitted model?
- `Q23.9` Is robustness measured against the perturbations, missingness, distribution shift, or adversary actually named?
- `Q23.10` What simpler method tests whether the proposed complexity is necessary?
- `Q23.11` What invariance, stability, calibration, or error bound should theory connect to implementation?

## Q24 — Construct and label validity

- `Q24.1` What exact construct is the target: prediction, causal effect, latent state, certification, risk, event, preference, or mechanism?
- `Q24.2` Does the label measure the construct or a proxy? What are its false positives and false negatives?
- `Q24.3` Could the label be mechanically derived from an input feature or post-outcome information?
- `Q24.4` Are detection, prediction, explanation, attribution, and causal responsibility kept distinct?
- `Q24.5` Is “risk” being confused with realized failure, “uncertifiable” with negative, or “association” with transmission?
- `Q24.6` Is the evaluation unit independent enough for the stated statistical analysis?
- `Q24.7` Are label timing, censoring, truncation, delayed observation, and revision modeled?
- `Q24.8` What independent measurement or domain evidence would validate the construct?

## Q25 — Data provenance and information timing

- `Q25.1` Where did every dataset originate, under what license, with what retrieval date and hash?
- `Q25.2` What information was genuinely available at each prediction, treatment, or decision time?
- `Q25.3` Are event time, observation time, publication/acceptance time, revision time, and usable decision time distinct?
- `Q25.4` Does preprocessing backfill future revisions, final classifications, current membership, or retrospectively resolved identifiers?
- `Q25.5` Are survivorship, attrition, missing-not-at-random, selection, duplicate entities, and identity changes handled?
- `Q25.6` Were normalization, vocabulary, graph construction, imputation, feature selection, and thresholds estimated only from the allowed split?
- `Q25.7` Can a point-in-time dataset be reconstructed, or is the core claim infeasible with available data?
- `Q25.8` What negative control deliberately introduces the suspected leakage and measures its magnitude?
- `Q25.9` Which data transformations are irreversible, undocumented, or impossible for another team to replay?

## Q26 — Experimental protocol

- `Q26.1` Which research question does each experiment answer, and which claim depends on it?
- `Q26.2` What result would falsify the paper's preferred explanation?
- `Q26.3` Are baselines the strongest realistic alternatives, including simple and domain-native methods?
- `Q26.4` Do controls and ablations isolate the mechanism rather than only reduce model size?
- `Q26.5` Are train, validation, test, temporal, entity, and group boundaries leakage-safe?
- `Q26.6` Are repeated seeds, sampling variability, confidence intervals, effect sizes, and uncertainty appropriate to the unit of analysis?
- `Q26.7` Is model and hyperparameter search accounted for, or is the best of many trials reported as confirmatory?
- `Q26.8` Are metrics aligned with prevalence, calibration, operational costs, ranking, probability quality, or causal estimand?
- `Q26.9` Do typical, boundary, subgroup, distribution-shift, and failure cases appear?
- `Q26.10` Which result is practically or scientifically meaningful rather than merely statistically significant?
- `Q26.11` Would the conclusion survive a negative or null result on the proposed new experiment?
- `Q26.12` Which experiments are expensive but cannot change the acceptance or scientific decision?
- `Q26.13` What predeclared stopping rule prevents endless benchmark chasing?

For each proposed run, specify the originating question, dependent claim, falsifiable hypothesis, data, baselines, controls, metrics, split, seeds/statistics, output artifact, interpretation of positive/negative/inconclusive results, cost, fallback, and stop condition.

## Q27 — Claim-evidence ledger

For every abstract, contribution, theoretical, experimental, and impact claim, ask:

- `Q27.1` What is the exact claim and evidence level E0–E3?
- `Q27.2` Which theorem, table, figure, code output, data record, citation, or artifact supports it?
- `Q27.3` What uncertainty does that evidence remove?
- `Q27.4` Is the evidence type qualified to support the verb and quantifier used?
- `Q27.5` What evidence is missing, and what is the smallest informative addition?
- `Q27.6` If the addition is not made, what is the strongest safe downgrade?
- `Q27.7` Must the evidence be visible in the main paper, or can it live in supplement/artifact?
- `Q27.8` Does another claim contradict it elsewhere in the paper?

## Q28 — Artifact and reproducibility

- `Q28.1` Can the main reported result be recreated from a clean environment with one documented entry point?
- `Q28.2` Are environment, dependency, hardware, seed, data version, and runtime recorded?
- `Q28.3` Does the repository contain the exact configuration corresponding to each reported result?
- `Q28.4` Are generated tables and figures traceable to data and scripts rather than manual edits?
- `Q28.5` Are proprietary or restricted inputs clearly separated from releasable code and synthetic smoke tests?
- `Q28.6` Can certificates, witnesses, proofs, or system traces be replayed and independently checked?
- `Q28.7` Do documentation and code implement the same method described in the manuscript?
- `Q28.8` What result would a reproducer be unable to verify, and must the corresponding claim be narrowed?

## Minimum synthesis

Return only the decision-relevant items:

- closest-prior difference and strongest novelty attack;
- fatal correctness or validity risk;
- claim-evidence gaps ranked by decision impact;
- one frozen proof or experiment obligation if needed;
- claims safe now, claims to downgrade, claims requiring assets, and claims to delete;
- owning skill, expected artifact, and stop condition for the next gate.
