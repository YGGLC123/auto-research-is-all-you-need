# Route, Revision, Rebuttal, and Submission Questions

Load this pack after a diagnosis must become a decision, work plan, rebuttal, or final submission check. Do not execute material research or manuscript changes until the route and paper identity are accepted.

## Q60 — Thesis survival and route choice

- `Q60.1` What is the largest current distance to the scientific or venue bar: correctness, novelty, construct validity, evidence, relevance, or communication?
- `Q60.2` Is the core thesis viable if the single largest blocker is tested honestly?
- `Q60.3` Which writing changes cannot compensate for missing research assets?
- `Q60.4` What is the smallest acceptance-blocker repair that preserves the paper identity (`Route A`)?
- `Q60.5` What center claim, contribution hierarchy, evidence order, or structure would a substantive rebuild change (`Route B`)?
- `Q60.6` What new theorem, boundary, dataset, experiment, method, or artifact could change the contribution judgment (`Route C`)?
- `Q60.7` For each route, what is retained, promoted, demoted, deleted, and added?
- `Q60.8` What time, compute, data, domain expertise, engineering, and page cost does each route require?
- `Q60.9` What is the strongest failure mode, and does the paper survive a negative new result?
- `Q60.10` What objective result would kill, pivot, or approve each route?
- `Q60.11` Which one route is recommended, and what evidence makes it preferable?
- `Q60.12` Which material choice truly belongs to the author rather than the reviewer-agent?

Stop at the approval gate before changing the central claim, running material work, or rewriting the manuscript around the selected route.

## Q61 — Freeze the intended paper

- `Q61.1` What one sentence describes `OLD PAPER` from a cold reader's perspective?
- `Q61.2` What one sentence defines `NEW PAPER` without relying on future unverified assets?
- `Q61.3` What is the primary question, audience, decision, and promise?
- `Q61.4` What is the headline research asset, and what only supports it?
- `Q61.5` Which contributions and side stories must be demoted or removed?
- `Q61.6` What are the explicit non-claims and validity boundary?
- `Q61.7` Which definitions, theorem statements, dataset versions, run IDs, and reported numbers are frozen?
- `Q61.8` What open dependency could still force the identity to change?
- `Q61.9` What three words and one evidence-safe sentence should a supporter remember?

## Q62 — Dependency-ordered master plan

- `Q62.1` Which tasks can change a claim and therefore must precede prose or visual work?
- `Q62.2` Which literature search, proof, data audit, protocol, or result gates all downstream work?
- `Q62.3` Which tasks can run safely in parallel without creating conflicting claims or files?
- `Q62.4` Who owns each task and what exact artifact must be returned?
- `Q62.5` What are the input contract, acceptance test, failure fallback, and stop condition?
- `Q62.6` Which task is expensive but low decision value and should be removed?
- `Q62.7` What can be finished with current assets if a blocked task fails?
- `Q62.8` Which three actions most change the decision, in dependency order?

Use actions `KEEP`, `INSERT`, `REPLACE`, `DELETE`, `RENAME`, `MOVE`, `MERGE`, `SPLIT`, `RUN`, and `VERIFY`.

For accepted work, use:

| ID/severity | Exact location | Question/risk closed | Action | Concrete artifact | Evidence | Dependency | Cost | Acceptance test |
|---|---|---|---|---|---|---|---|---|

Do not populate the table with wishes such as “add experiments,” “improve the story,” or “optimize figures.”

## Q63 — Section and page allocation

- `Q63.1` What is the unique cognitive job of each section?
- `Q63.2` Which reader question must be answered before the next section can work?
- `Q63.3` Which content is duplicated, premature, misplaced, or no longer part of the selected story?
- `Q63.4` Which decisive definition, theorem, comparison, experiment, limitation, or example must remain in the main paper?
- `Q63.5` Which proof details, complete tables, robustness checks, or implementation material can move to optional material?
- `Q63.6` What deletion, compression, or move pays for every insertion?
- `Q63.7` Does the verified venue or journal format impose a current constraint that changes the argument order?
- `Q63.8` What is the cut order if the draft exceeds its verified budget?
- `Q63.9` Is readability being sacrificed through font, spacing, figure scale, or dense notation?

Use the current verified venue contract. Never hard-code a page template into the generic suite.

## Q64 — Patch and change safety

- `Q64.1` Has the diagnosed version been snapshotted or committed before edits?
- `Q64.2` Are unrelated user changes preserved and excluded from staging?
- `Q64.3` Does each textual change map to an accepted claim and evidence state?
- `Q64.4` Could a terminology replacement alter a theorem statement, dataset field, code interface, citation meaning, or frozen number?
- `Q64.5` Are source, rendered PDF, supplement, code, figures, and response ledger synchronized after edits?
- `Q64.6` Are new citations verified rather than inferred from metadata?
- `Q64.7` Is proposed prose clearly separated from result-pending prose?
- `Q64.8` Does the rendered output preserve equations, cross-references, figures, tables, accessibility, and anonymity?

## Q65 — Rebuttal and revision-response logic

- `Q65.1` What did each reviewer actually claim, and what is the fairest precise paraphrase?
- `Q65.2` Is the concern about a misunderstanding, missing evidence, incorrect claim, missing citation, preference, or fatal flaw?
- `Q65.3` What existing evidence answers it directly?
- `Q65.4` What evidence is new, and is new evidence allowed in this response process?
- `Q65.5` What exact paper change accompanies the answer?
- `Q65.6` What limitation should be conceded rather than argued away?
- `Q65.7` Can the response be shorter and more direct without losing evidence?
- `Q65.8` Is the tone factual and non-adversarial?
- `Q65.9` Does the response promise work that is infeasible or outside policy?
- `Q65.10` Which concern cannot be repaired in rebuttal and must shape the next submission instead?

Maintain a point-by-point ledger with reviewer text, answer, evidence, manuscript change, residual risk, and word cost.

## Q66 — Truth firewall

For every strong expression in the title, abstract, contributions, figures, and conclusion ask:

- `Q66.1` What evidence level supports it?
- `Q66.2` What assumptions and quantifiers are suppressed by the short phrasing?
- `Q66.3` Does the evidence type support causality, universality, robustness, deployment, or practical impact?
- `Q66.4` Has the nearest-prior search qualified “first,” “only,” or “new”?
- `Q66.5` Where in the main paper is the expectation debt repaid?
- `Q66.6` What is the strongest safe version if a dependency remains open?
- `Q66.7` Would a hostile but fair paraphrase expose a real overclaim?

## Q67 — Final scientific and submission closure

- `Q67.1` Are all fatal and blocker risks resolved, explicitly accepted, or route-ending?
- `Q67.2` Are all `VERIFY_REQUIRED`, `SEARCH_REQUIRED`, and `RESULT_PENDING` tokens closed, or are dependent claims removed?
- `Q67.3` Do title, abstract, contributions, definitions, theorem statements, figures, tables, reported numbers, experiments, limitations, and conclusion agree?
- `Q67.4` Are decision-critical facts and evidence visible without optional material?
- `Q67.5` Does the manuscript compile cleanly and satisfy the current official venue/journal contract?
- `Q67.6` Are anonymity, conflicts, ethics, data use, generative-AI use, overlap, citations, and reproducibility handled?
- `Q67.7` Can the main artifact replay from a clean environment, or are claims narrowed to what can be verified?
- `Q67.8` Are figures and tables linked to sources, data, scripts, and self-contained captions?
- `Q67.9` Has an independent reader performed the fast-read and evidence-consistency pass?
- `Q67.10` Can that reader state the problem, nearest difference, strongest result, evidence, and boundary correctly?
- `Q67.11` What objective condition must be true before declaring the submission build final?

## Q68 — Related-paper and contribution ownership audit

Run this module only when multiple related manuscripts, overlapping author groups, companion papers, or shared research assets are actually in scope.

- `Q68.1` What center problem, formal object, headline result, evidence, and audience makes each paper independently valuable?
- `Q68.2` Which definitions, theorems, proofs, algorithms, datasets, experiments, figures, tables, and text passages are shared?
- `Q68.3` Which manuscript owns each shared asset, and how do the others cite or depend on it without duplicate claiming?
- `Q68.4` Are papers separated by a genuine scientific question and evidence loop, or only by domain nouns, parameter settings, datasets, or cosmetic framing?
- `Q68.5` Would combining the work create one stronger cohesive paper, or destroy independently important questions?
- `Q68.6` Does a companion paper become an unpublished dependency for a central proof, method, or empirical claim?
- `Q68.7` Are parallel-submission, prior-publication, text-reuse, anonymity, and disclosure obligations verified for the actual venue or journal?
- `Q68.8` Can a blind reader distinguish the papers from title, abstract, contributions, central result, and evidence alone?
- `Q68.9` Which side story or shared asset should be reassigned, merged, or removed to make ownership clear?
- `Q68.10` What contribution-ownership matrix and cross-reference plan closes the overlap risk?

## Minimum synthesis

Return only:

- viable routes and one recommendation;
- the frozen intended paper identity;
- the approval boundary;
- the three highest-impact tasks in dependency order;
- an executable patch/run ledger only for accepted work;
- objective final-entry and stop conditions.
