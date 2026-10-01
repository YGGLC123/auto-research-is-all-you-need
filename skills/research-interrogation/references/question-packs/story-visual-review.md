# Story, Pedagogy, Visual, and Reviewer Questions

Load this pack when the research assets are credible enough to present but the paper may be misunderstood, forgettable, overclaimed, or visually inefficient. Do not use story work to conceal unresolved novelty, validity, or evidence failures.

## Q40 — Reader promise and narrative spine

- `Q40.1` What promise do the title, abstract, first page, contribution list, central result, evidence, and conclusion each make?
- `Q40.2` Are those promises the same in object, scope, conditions, verb, and strength?
- `Q40.3` What does a reader understand after 15 seconds, 60 seconds, and 3 minutes?
- `Q40.4` What should they understand at each point, and where is the first divergence?
- `Q40.5` Is the paper competing among multiple center stories? Which one has the highest product of importance, distinctiveness, evidence closure, and memorability?
- `Q40.6` What concrete failure, minimal example, tension, or surprising boundary can establish the problem faster than generic background?
- `Q40.7` What is the paper's single cognitive reversal: “the real bottleneck is not X but Y”?
- `Q40.8` Which result is the climax, and does the preceding structure make the reader want that result?
- `Q40.9` Which side stories, method details, applications, or claims dilute the center and should be demoted or removed?
- `Q40.10` Can a fair reader retell the paper accurately without using the method acronym?

Use one of these story patterns only when it matches the evidence: hidden assumption failure; wrong object; possibility boundary; unverifiable output to checkable certificate; representation/compilation unlocking a capability; exact bridge; negative result becoming a design rule; new empirical fact changing a mechanism or decision.

When story choice is genuinely open, generate a small set of mutually exclusive candidates and compare importance, nearest-prior difference, evidence, audience fit, cost, visualizability, and overclaim risk. Select one for the front end; do not combine incompatible candidates into an abstract.

## Q41 — Truth-constrained positioning

- `Q41.1` What is the strongest honest title-level claim after atomizing object, condition, action, guarantee, scope, evidence, and consequence?
- `Q41.2` Which atom is unsupported and must be weakened?
- `Q41.3` What is the tempting but unjustified version, and what exact evidence would cross that boundary?
- `Q41.4` Does each strong adjective or verb have a visible theorem, experiment, certificate, or dataset that repays the expectation debt?
- `Q41.5` Does deleting fashionable terms leave a technically valuable contribution?
- `Q41.6` Could the title, Figure 1, and contribution bullets be reused unchanged by the closest neighbour? If yes, what is too generic?
- `Q41.7` Can a supporter explain importance, non-triviality, difference, evidence, and boundary without exaggeration?
- `Q41.8` Can the plain-language selling point be mapped back atom by atom to formal objects and verified results?
- `Q41.9` What are the three words a reader should remember the next day, and are they actually unique to this work?

Never translate risk into violation, non-certification into failure, finite checking into proof, feasibility into deployment, a case study into prevalence, worst case into typical behavior, association into causality, or model dependence into causal responsibility.

## Q42 — Title, abstract, first page, and contributions

- `Q42.1` Does the title foreground the important problem, distinctive object, or strongest result rather than only a method name?
- `Q42.2` Does the title's strongest word survive the evidence and nearest-neighbour tests?
- `Q42.3` Does the abstract state a concrete task, exact gap, core insight, strongest result, evidence, and boundary without overloading details?
- `Q42.4` Is every abstract sentence doing a distinct job?
- `Q42.5` Does the first paragraph identify the user/decision and consequence rather than opening with a generic trend?
- `Q42.6` Does the first page expose the hidden failure or minimal example before dense notation?
- `Q42.7` Does each contribution bullet name a research asset, precise guarantee/finding, and why it matters?
- `Q42.8` Are contribution bullets independent, or is one asset split into multiple claims?
- `Q42.9` Is closest-prior difference visible where the reader first forms a novelty judgment?
- `Q42.10` Is a decisive limitation visible early enough to prevent an unfair but predictable overreading?
- `Q42.11` Does the conclusion state the capability change and its boundary rather than merely restating sections?

Treat exact sentence counts and number of contributions as editing choices, not universal rules.

## Q43 — Pedagogy and conceptual language

- `Q43.1` Which three to five mechanisms or formal objects impose the highest cognitive load?
- `Q43.2` Can each be explained first without symbols, then with a minimal example, formal definition, result meaning, proof or mechanism intuition, consequence, and non-implication?
- `Q43.3` Is there a running example that remains valid from motivation through theorem and evidence?
- `Q43.4` Are terms short, accurate, memorable, and consistent with existing field language?
- `Q43.5` Do invented terms create the right intuition or conceal a known object?
- `Q43.6` Are acronyms, symbol load, synonyms, and notation introduced only when they pay for themselves?
- `Q43.7` Does each theorem answer a question naturally raised by the preceding text?
- `Q43.8` Is the proof intuition specific enough to reveal the obstruction, construction, invariant, or decomposition?
- `Q43.9` Can a broad reviewer understand the non-technical center claim without losing the formal boundary?
- `Q43.10` If an analogy is used, which mapping is exact, where does it fail, and what sentence prevents the wrong inference?

## Q44 — Visual coverage and figure function

- `Q44.1` Which core claims have a visual carrier—a figure, table, example, theorem box, or result panel—and which do not?
- `Q44.2` Which existing visuals consume space without carrying a decision-critical claim?
- `Q44.3` What single question must each figure or table answer?
- `Q44.4` What sentence should a reader remember after viewing it?
- `Q44.5` Does it show mechanism, comparison, evidence, boundary, or only decoration?
- `Q44.6` Are reading order, hierarchy, labels, arrows, scales, legends, units, and uncertainty unambiguous?
- `Q44.7` Does visual encoding imply causality, universality, precision, or scope beyond the evidence?
- `Q44.8` Is the visual readable at final column width, in grayscale, and under common color-vision differences?
- `Q44.9` Is the caption self-contained and claim-faithful?
- `Q44.10` Is the source editable and the figure reproducibly generated when data-driven?

## Q45 — Whether a hero Figure 1 is justified

- `Q45.1` What decisive reader misunderstanding would a new Figure 1 remove?
- `Q45.2` Is a visual actually more efficient than a minimal example, theorem box, or comparison table?
- `Q45.3` Should it show problem -> failure -> insight -> output/boundary; old versus new view; minimal counterexample -> general result; representation -> new capability; or unverifiable output -> checkable witness?
- `Q45.4` What is the cognitive job of every panel, node, arrow, label, color, and line style?
- `Q45.5` Which claim and evidence source authorizes each visual assertion?
- `Q45.6` What page content must be deleted or moved to pay for the figure?
- `Q45.7` Can a reader reconstruct the problem, mechanism, result, and limit in 5, 20, and 60 seconds?

Only after these answers route to `/auto-research:research-artifacts`. Treat Q44–Q45 as Phase-1 inputs, not permission to draw immediately: the artifact skill must map claims/sources, classify fidelity and type, write and validate the paper-specific Stage-2 prompt, then freeze it before production.

## Q46 — Reviewer stress lenses

Choose reviewer lenses from the actual venue, field, and paper type rather than using a permanent cast. Useful lenses include:

- broad fast reader;
- closest technical expert;
- construct/identification specialist;
- experiments/statistics/reproducibility reviewer;
- systems/deployment reviewer;
- domain expert;
- sceptic of fashionable framing;
- editor, AC, SPC, or meta-review synthesizer.

For each selected lens ask:

- `Q46.1` What are the three strongest fair rejection reasons?
- `Q46.2` What is the one value this reader may recognize?
- `Q46.3` What minimum change or evidence could move their judgment?
- `Q46.4` Which misunderstanding cannot be repaired in rebuttal and must be prevented in the paper?
- `Q46.5` Which content is hidden in optional material but needed for their decision?
- `Q46.6` What one sentence would they use to summarize the paper unfavorably but fairly?
- `Q46.7` What would a supporter say, and is that statement evidence-safe?

Synthesize the blocker shared across lenses. Do not return independent reviews without a decision implication.

## Q47 — Modern-AI or trend mapping, only when relevant

- `Q47.1` Which actual lifecycle object or workflow corresponds to each formal object?
- `Q47.2` Are inputs, outputs, access, assumptions, failure conditions, and semantics preserved by the mapping?
- `Q47.3` What new difficulty is introduced or amplified in the target modern-AI setting?
- `Q47.4` Is the connection a keyword, plausible use case, faithful instantiation, verified case, or systematic validation?
- `Q47.5` What minimum evidence would upgrade the mapping without distorting the paper?
- `Q47.6` Does the connection strengthen the contribution or blur a stronger independent identity?

Use the optional ladder L0 keyword/analogy, L1 use case, L2 faithful formal mapping, L3 verified case/artifact, L4 systematic validation. L0 does not enter headline claims; L1 remains a limited implication; L2 is a scoped instantiation; L3 can support front-end positioning; L4 supports stronger operational claims.

## Minimum synthesis

Return only:

- the actual versus intended fast-read takeaway;
- the winning evidence-safe story spine;
- the strongest overclaim or generic-positioning risk;
- front-end repairs ranked by decision impact;
- visual action only if it resolves a named misunderstanding;
- the shared reviewer blocker and its minimum repair.
