# Figure-Type Playbooks

Choose type from the reader question and evidence contract. Do not make architecture, workflow, theory, network, timeline, and result figures share one generic box-and-arrow grammar.

For any modular type below, upgrade D2 to D3 only when the assembly must decompose into reusable semantic components. D3 changes the handoff and grouping behavior, not the scientific grammar of the figure type.

Program identifiers, in the order documented below, are:

`benchmark_plot`, `ablation_sensitivity`, `calibration_uncertainty`, `heatmap_matrix`, `embedding_projection`, `method_architecture`, `algorithm_workflow`, `theory_proof_map`, `data_provenance_protocol`, `timeline_version_state`, `network_hypergraph`, `mechanism_schematic`, `qualitative_case`, `taxonomy_landscape`, `result_table`, `graphical_abstract`, and `mixed_panel`.

## Contents

- Evidence plots and tables: types 1-5 and 15
- System, formal, and process diagrams: types 6-12
- Cases, synthesis, and composites: types 13-14 and 16-17

## 1. Benchmark and primary-result plot

- Reader question: which method/condition performs better, by how much, and with what uncertainty?
- Grammar: dot/interval, line/band, distribution, calibrated bars, or small multiples; direct labels when they reduce legend travel.
- Style: restrained evidence-first composition, no in-plot marketing title, neutral baseline by default, primary method emphasized once.
- Require: complete comparison set, units, sample/seed information, uncertainty definition, scale/transform disclosure, and source data.
- Avoid: generated pixels, guessed values, truncated-zero bars, unexplained error bars, decorative area/3D encodings, inconsistent axes, and selective panels.
- Route: exact deterministic plot (`E2-D1-G0`).

## 2. Ablation, sensitivity, and robustness

- Reader question: which component or assumption matters, and where is the stable or failing region?
- Grammar: aligned small multiples, factorial comparison, response curves, or matrix; keep the full model and strongest baseline visible.
- Style: consistent axes and ordering across panels; emphasize effect size and uncertainty rather than winner coloring.
- Require: controlled comparison, tuning protocol, seeds/runs, parameter scale, and negative results.
- Avoid: connecting unordered categories into a pseudo-continuous curve, swapping y-ranges silently, and omitting the full system.
- Route: exact deterministic plot (`E2-D1-G0`).

## 3. Calibration, coverage, uncertainty, and risk

- Reader question: are probabilities/sets/intervals calibrated, and what is the coverage-width or risk-utility tradeoff?
- Grammar: reliability plot with counts, coverage-width frontier, interval/distribution panels, or risk curve with reference targets.
- Style: uncertainty is a first-class encoding; reference lines are subordinate, not proof.
- Require: binning rule, denominators, interval definition, target level, finite-sample uncertainty, and empty/failed cases.
- Avoid: calling a credible interval a coverage guarantee, showing a diagonal without bin support, or hiding set width.
- Route: exact deterministic plot (`E2-D1-G0`).

## 4. Heatmap, matrix, and tensor slice

- Reader question: what exact two-dimensional structure or interaction pattern exists?
- Grammar: labeled cell grid, explicit colorbar, meaningful ordering/clustering, annotations only when readable.
- Style: sequential/diverging perceptual map matched to the statistic; neutral midpoint only when mathematically meaningful.
- Require: normalization, missing-value encoding, ordering rule, color scale limits, and whether values are raw or transformed.
- Avoid: rainbow/jet, undisclosed quantile remapping, saturated cells that collapse extremes, and illegible cell text.
- Route: exact deterministic plot (`E2-D1-G0`).

## 5. Embedding or projection view

- Reader question: what exploratory local organization appears after a named projection?
- Grammar: point cloud/density with stable identifiers, selective labels, uncertainty or repeated-seed comparison when relevant.
- Style: low visual overclaim; use facets rather than a crowded rainbow of classes.
- Require: projection method, parameters, seed, preprocessing, and explicit limitation in caption.
- Avoid: using 2D separation as proof of true distance, clustering, causality, or generalization.
- Route: exact deterministic plot from saved embeddings (`E2-D1-G0`).

## 6. Method or system architecture

- Reader question: what modules exist, what boundaries separate them, and how does information move?
- Grammar: grouped subsystems, ports/interfaces, data stores, model stages, inputs/outputs; orthogonal connectors are a default for structured flows.
- Style: clean editable vector, short noun labels, visible hierarchy, one dominant direction, semantic grouping bands.
- Require: exact module names and interfaces; distinguish training/inference, online/offline, public/private, or local/external boundaries when relevant.
- Avoid: code-directory dumps, procedural chronology disguised as architecture, invented modules, paragraph boxes, and unlabeled arrow semantics.
- Route: `E1-D2-G0` Draw.io/Figma Design or `E1-D1-G0` SVG/TikZ/Graphviz. Use `E1-D3-G0` Draw.io/structured SVG/native PowerPoint for an assembly plus reusable subsystem kit; Figma may author it only if a local D3 master is produced. Generative draft only if rebuilt.

## 7. Algorithm, workflow, and control flow

- Reader question: in what order do steps execute, where do decisions branch, and what loops/fallbacks exist?
- Grammar: numbered process nodes, explicit decision conditions, branch labels, termination and failure states.
- Style: directional rhythm; decisions and processes have distinct shapes; loops stay outside the main spine.
- Require: actual conditions, triggers, inputs, outputs, and stopping rules.
- Avoid: architecture boundaries, research roadmap, and runtime control flow collapsed together; unlabeled yes/no branches; tangled back edges.
- Route: `E1-D2-G0` editable vector or `E2-D1-G0` deterministic state/flow graph when structure itself is evidence. Use D3 when step blocks, decisions, or fallback modules must be rearranged as intact components.

## 8. Theory, proof, set, lattice, or complexity map

- Reader question: how do definitions, assumptions, results, counterexamples, inclusions, and boundaries relate?
- Grammar: dependency DAG, nested/overlapping regions, lattice/order diagram, quantifier stack, boundary map, or theorem-counterexample pair.
- Style: sparse formal composition; math font matches the manuscript; color encodes epistemic role, not decoration; line types distinguish implication, dependence, translation, and analogy.
- Require: exact formulas/labels and a legend for every relation; state when a spatial area has no quantitative meaning.
- Avoid: generated formulas, analogy arrows presented as proofs, non-rigorous area sizes, hidden assumptions, and cyclic dependency caused by layout.
- Route: `E2-D1-G0` TikZ/Graphviz/native SVG or `E2-D2-G0` controlled Draw.io/Figma with a verified node-edge-formula manifest. D3 is valid only when proof/theory modules remain exact after decomposition and recomposition.

## 9. Data provenance, evaluation, or experimental protocol

- Reader question: where did data/labels/evidence come from, how were splits or filters applied, and where could leakage occur?
- Grammar: source-to-snapshot-to-split-to-model-to-evaluation lanes; explicit clocks, versions, exclusions, and immutable run IDs.
- Style: audit-like, exact counts and barriers, observed vs unavailable/future information visually distinct.
- Require: point-in-time availability, split identity, exclusions, revision/backfill status, and count conservation.
- Avoid: future leakage, mixing event time with observation time, inconsistent counts, and hiding manual adjudication.
- Route: `E2-D1/D2-G0`; exact manifest first, editable vector or deterministic flow second.

## 10. Timeline, version, disclosure, or state-transition figure

- Reader question: what happened when, what was knowable then, and how did state/version change?
- Grammar: aligned time lanes, event markers, observation/reveal windows, version branches, or explicit state transitions.
- Style: time flows in one direction; chronology, availability, and revision use distinct encodings.
- Require: clock definition, timezone/period boundaries, version identity, and missing/unknown intervals.
- Avoid: causal arrows for mere temporal order, retroactive information appearing early, and collapsing event/filing/effective dates.
- Route: exact vector or code (`E2-D1/D2-G0`). FigJam Gantt is a planning draft only when styling or exact semantics matter.

## 11. Network or hypergraph

- Reader question: what relational or higher-order structure matters, and how does it differ across conditions/scales?
- Grammar: exact nodes/edges/hyperedges, incidence view, layered/small-multiple views, hyperedge regions/hulls, or summary statistics paired with topology.
- Style: topology legible before decoration; curves/regions may outperform orthogonal connectors; use direct group labels and restrained opacity.
- Require: graph construction, node/edge/hyperedge meaning, thresholding, layout rule/seed, scale, and whether topology is exact or schematic.
- Avoid: clique expansion masquerading as a hypergraph, force layout treated as evidence, hairballs, arbitrary node sizes, and unverifiable generated topology.
- Route: deterministic NetworkX/Graphviz/custom SVG for `E2`; editable vector for `E1`; generation only for an explicitly E0 metaphor.

## 12. Mechanism or domain schematic

- Reader question: what scientific/financial/physical mechanism is proposed or observed?
- Grammar: entities, compartments, transformations, activation/inhibition, flows, and evidence status.
- Style: domain-standard symbols with a clear focal pathway; richer illustration is allowed only outside exact relations.
- Require: every component and relation checked against source evidence; observed, assumed, and hypothesized edges distinguished.
- Avoid: letting a model invent molecule paths, circuit values, causal edges, quantities, or labels.
- Route: editable vector for E1/E2; generative `G1` concept draft followed by rebuild; `G2` only for E0 contextual illustration.

## 13. Qualitative example or case study

- Reader question: what does the method do on a representative fixed case, including failure modes?
- Grammar: aligned input/intermediate/output/comparator panels, identical crop/scale, direct annotations tied to evidence.
- Style: minimal frames and labels; keep source content visually dominant.
- Require: case-selection rule, provenance, prediction/label, and whether the case is typical, best, random, or failure.
- Avoid: cherry-picked success without disclosure, inconsistent crops, post-hoc annotations, and privacy/license violations.
- Route: exact source assets plus deterministic composition; image editing only with invariants and explicit disclosure.

## 14. Taxonomy, literature landscape, or knowledge map

- Reader question: how are categories defined and how do works/claims occupy them?
- Grammar: hierarchy, matrix, typed graph, or timeline; inclusion rules and evidence status are visible.
- Style: category structure dominates; citations stay readable; avoid dense hairball maps.
- Require: taxonomy rule, coverage date, item identity, and uncertainty/ambiguous membership.
- Avoid: proximity implying intellectual descent, arbitrary category colors, and embedding similarity presented as identity.
- Route: structured data plus deterministic/editable layout (`E2/E1-D1/D2-G0`).

## 15. Table or visual matrix

- Reader question: which exact values or attributes must be scanned and compared precisely?
- Grammar: aligned decimals, grouped columns, sparse rules, controlled emphasis, footnotes for definitions and missingness.
- Style: typographic rather than decorative; use booktabs-like hierarchy and consistent precision.
- Require: structured source, units, uncertainty, denominator, missing-value rule, and complete row/column set.
- Avoid: screenshot tables, raster generation, excessive vertical rules, color-only meaning, and bolding every winner.
- Route: LaTeX/dataframe/spreadsheet (`E2-D1-G0`).

## 16. Graphical abstract or teaser

- Reader question: what is the motivation-method-value story at a glance?
- Grammar: three-part or focal mechanism composition, minimal text, one visual hook, no dense evidence panels.
- Style: strongest aesthetic freedom in the paper; restrained illustration, limited gradients, or icons are allowed if venue-appropriate.
- Require: no unsupported claim, clear separation between concept and measured result, and a plain-language takeaway.
- Avoid: using the teaser as quantitative evidence, generated formulas, dense labels, and marketing superlatives.
- Route: Figma/vector or `E0-D0-G2` image generation; preserve prompt/source and verify all text.

## 17. Mixed multi-panel synthesis

- Reader question: what single caption-level argument requires different visual forms?
- Grammar: panels in argumentative order, shared alignment/tokens, exact panels frozen before assembly, one dedicated legend zone if helpful.
- Style: coherent but not forcibly identical; panel-specific encodings remain valid.
- Require: E/D/G contract per panel and a composer-linked delivery/build manifest.
- Avoid: collage logic, repeated legends, inconsistent units, and passing the assembled figure through a generative model.
- Route: panel-specific production followed by deterministic vector composition. Use D3 for a rearrangeable synthesis: freeze exact panels as replaceable vector components, then group panel/module/primitive levels without redrawing their evidence.
