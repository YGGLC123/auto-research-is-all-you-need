# Problem and Contribution Questions

Load this pack when the center question, contribution, value, scope, or transferability is uncertain. Select only questions that could change the research route or headline claim.

## Q0 — Material and truth boundary

- `Q0.1` What files, versions, pages, figures, tables, theorem statements, code paths, datasets, result manifests, reviews, and related manuscripts were actually read?
- `Q0.2` Which expected materials are missing, unreadable, stale, inconsistent, or not reproducible?
- `Q0.3` Which claims are facts in the current paper, which are author beliefs, which are reviewer interpretations, and which are proposed upgrades?
- `Q0.4` Do source, PDF, supplement, code, and reported numbers agree?
- `Q0.5` Which frozen statements, identifiers, datasets, numbers, anonymity constraints, or legal/ethical boundaries must not be changed?
- `Q0.6` What time, compute, data, annotation, domain-expert, and engineering budget is actually available?
- `Q0.7` Which single missing input would most alter the diagnosis? Can the audit proceed safely with it marked unknown?

## Q1 — The real problem

- `Q1.1` What real scientific, technical, operational, economic, social, or governance failure exists independently of the proposed method?
- `Q1.2` Who encounters the failure, in which workflow, and what decision or outcome is harmed?
- `Q1.3` What is the smallest concrete failure case, counterexample, incident, or pair of indistinguishable worlds that proves the problem is real?
- `Q1.4` Is the paper solving a field problem, a scientific knowledge gap, a technical limitation, or only a convenient benchmark task?
- `Q1.5` What cannot currently be stated, distinguished, proved, computed, checked, certified, predicted, or decided?
- `Q1.6` Is the limitation quantitative (“not accurate enough”) or structural (“the required output is impossible under current information, access, representation, or assumptions”)?
- `Q1.7` What is the root obstacle: information, access, representation, computation, identifiability, measurement, evidence, incentives, or an undefined target?
- `Q1.8` Does the formal or empirical task preserve the real input, output, permissions, timing, threat model, failure condition, and success criterion?
- `Q1.9` Is the paper solving a proxy that has drifted away from the real problem? What evidence links the proxy to the target?
- `Q1.10` If every author-created term is removed, does an important problem remain?
- `Q1.11` Why does this problem matter now? Is “now” supported by changed scale, information structure, deployment, regulation, market design, or other facts rather than trend language?

## Q2 — Gap and closest alternative at the problem level

- `Q2.1` What precisely is not known, not guaranteed, or repeatedly conflated in existing work?
- `Q2.2` Does another field solve the same object under different terminology?
- `Q2.3` What is the strongest existing alternative a rational user would choose instead?
- `Q2.4` Does the introduction describe that alternative fairly, including where it is stronger?
- `Q2.5` Would the paper still be needed if the strongest baseline or closest theorem were implemented correctly?
- `Q2.6` Is the claimed gap a missing capability, missing knowledge, missing representation, missing evidence form, missing workflow, or only missing application?
- `Q2.7` What evidence would falsify the claimed gap?

## Q3 — What the paper actually contributes

- `Q3.1` Is the core output a definition, representation, theorem, algorithm, certificate, estimator, measurement, dataset, empirical fact, system, workflow, or synthesis?
- `Q3.2` Which output is the headline research asset, and which outputs merely support it?
- `Q3.3` What can a user do after this work that they could not reliably do before?
- `Q3.4` Which mechanism or result is indispensable? If removed, which guarantee or decision fails?
- `Q3.5` What is the strongest claim directly supported now, not after proposed work?
- `Q3.6` Which contributions are genuinely independent, and which are one result split into multiple bullets?
- `Q3.7` Is a negative, null, impossibility, necessity, or boundary result more valuable than the currently promoted positive result?
- `Q3.8` What knowledge or capability is new rather than merely renamed?
- `Q3.9` Does the result change a scientific belief, system design, audit decision, policy choice, investment decision, or future research path? Through what exact link?
- `Q3.10` What is the strongest fair “this is just X + Y” reduction? Which specific element prevents that reduction, if any?

## Q4 — Value and consequence

- `Q4.1` Who is the primary beneficiary of the result, and what decision do they make differently?
- `Q4.2` Is the value new knowledge, new expressive power, new computational ability, a checkable witness, improved measurement, a valid prediction, a causal estimate, or a safer workflow?
- `Q4.3` What uncertainty does the central theorem, experiment, certificate, or dataset eliminate?
- `Q4.4` What is the consequence if the result is wrong or unavailable?
- `Q4.5` Is the claimed impact direct, conditional, or speculative? Is it E1, E2, or E3?
- `Q4.6` Does the downstream decision actually use the paper's output, or is impact asserted without a decision rule?
- `Q4.7` What is the economic, operational, scientific, or social magnitude—not merely statistical or benchmark improvement?

## Q5 — Scope, boundaries, and non-claims

- `Q5.1` Under which assumptions, populations, access rights, time periods, data regimes, or threat models does the result hold?
- `Q5.2` Which boundary case breaks the method or theorem?
- `Q5.3` Does the paper confuse absence of evidence, inability to certify, detected risk, observed failure, causal harm, or noncompliance?
- `Q5.4` Does it confuse existence, worst case, average case, common occurrence, or universality?
- `Q5.5` Does a synthetic example support only feasibility while the text claims real-world prevalence or effectiveness?
- `Q5.6` Which three non-claims must be visible in the front end to prevent a reasonable overreading?
- `Q5.7` What would make the main claim false, and is that falsifier observable?
- `Q5.8` Is the limitation a removable engineering deficit, a data limitation, a theorem boundary, or an intrinsic impossibility?

## Q6 — Strongest honest paper identity

- `Q6.1` Complete: “Before this work, one could only ___; with this work, under ___, one can reliably ___; this work still cannot ___.”
- `Q6.2` What is the current paper's one-sentence identity from a cold reader's perspective?
- `Q6.3` What is the strongest evidence-supported one-sentence identity the paper could adopt?
- `Q6.4` Which object, condition, verb, guarantee, scope, or consequence must be weakened to keep that identity honest?
- `Q6.5` Which one result deserves title-level visibility? If none, what minimum new asset would create one?
- `Q6.6` What should a fair supporter be able to tell an editor, AC, or reader in one sentence without exaggeration?

## Q7 — Expansion and transfer

- `Q7.1` Which parts of the problem, formal object, method, and theorem are domain-independent?
- `Q7.2` Which parts are tied to a domain's semantics, data-generation process, incentives, labels, or timing?
- `Q7.3` Does transfer require only re-instantiation, or new definitions, assumptions, proofs, data, baselines, and decision criteria?
- `Q7.4` What is the smallest adjacent domain where the mechanism remains meaningful and testable?
- `Q7.5` What domain would make the method look useful but invalidate its assumptions?
- `Q7.6` Can one unified question cover multiple domains without diluting the strongest provable claim?
- `Q7.7` Which transfer would create new scientific knowledge rather than another application shell?
- `Q7.8` What new failure mode, theorem boundary, or measurement problem appears in the target domain?

## Minimum synthesis

After the selected questions, produce only:

- one-sentence current problem;
- one-sentence current contribution;
- one-sentence best defensible paper identity;
- the problem-to-impact chain with weak or missing links;
- the single decision-changing unknown;
- the next owning skill and objective stop condition.
