# Research Visual Quality Gates

Run gates in order. A later aesthetic pass cannot cure an earlier scientific failure.

## Contents

- Gates 0-2: necessity, provenance, and scientific content
- Gates 3-5: visual semantics, legibility, and accessibility
- Gates 6-8: reproducibility, manuscript integration, and cold review
- Gate 9 and exit ledger: delivery closure

## Gate 0 — Necessary role

- The artifact answers one explicit reader question and supports named claim(s), limitation(s), or navigation need.
- Removing it would create a real comprehension or verification gap.
- It does not duplicate another figure/table or merely restate nearby prose.
- Its dominant role and caption job are explicit.

Fail or remove artifacts with no necessary job.

## Gate 1 — Source and contract integrity

- Every value, count, formula, label, node, edge, state, version, and ordering has a trusted source or declared deterministic rule.
- E/D/G codes and figure type match the content.
- Exact numeric panels have data/run pointers and uncertainty rules.
- Exact structural panels have a manifest.
- Observed, derived, hypothesized, latent, simulated, and illustrative content are distinguishable.
- Forbidden inferences are absent.

For frozen programs, run `scripts/visual_contract.py validate-request`. Any prompt/program/input hash mismatch makes the request stale and blocks production.

## Gate 2 — Content QA

Compare the rendered draft against the source, not against memory:

- values, units, rounding, signs, missing values, denominators, counts, and comparison sets;
- formulas, notation, theorem/section labels, quantifiers, and assumptions;
- nodes, edges, arrow directions, hyperedges, state transitions, time windows, and version identities;
- baseline identity, uncertainty definition, scale, transform, normalization, and sample/seed information;
- captions, legends, panel references, and terminology.

Programmatic comparison is preferred for tables and plots. Use a node/edge manifest for exact diagrams. Generated imagery requires item-by-item semantic review; a model quality score is not evidence.

## Gate 3 — Visual hierarchy and type fitness

At final placement size:

- the three-second takeaway is visible without reading every label;
- the thirty-second reading path is unambiguous;
- the selected type expresses structure, progression, execution, evidence, topology, or chronology appropriately;
- panel order supports one caption-level argument;
- whitespace, alignment, grouping, and emphasis create hierarchy;
- legends and annotations do not require excessive eye travel;
- labels are concise and consistent;
- connectors avoid text, clipping, ambiguous endpoints, and unjustified crossings;
- no decorative texture, shadow, gradient, 3D, icon, or color competes with evidence.

Do not force all figure types into the same box-and-arrow appearance.

## Gate 4 — Typography, dimensions, and technical export

- Derive target width from the active manuscript template or venue specification; use generic single/double-column defaults only when no template exists.
- Evaluate text in physical units at final size. Use the venue minimum; absent one, keep ordinary labels roughly 7–9 pt and panel labels about 9–11 pt, with controlled hierarchy.
- Use manuscript-compatible math and text fonts; embed fonts in vector outputs.
- Keep ordinary strokes at or above roughly 0.5 pt at final size unless the venue requires more.
- Prefer PDF as the publication vector, SVG for editing/interchange, and PNG as preview; use TIFF/EPS only when the venue requires them.
- Native raster panels meet the venue's effective DPI, commonly at least 300 dpi for color/continuous tone and higher for line art.
- Nothing is clipped, blurred, accidentally transparent, or outside the export bounds.

Compile and inspect the manuscript page. Standalone-canvas success is insufficient.

## Gate 5 — Accessibility and print robustness

- Color never carries meaning alone; use direct labels, line style, marker shape, hatching, or position redundantly.
- Test grayscale and common color-vision-deficiency conditions.
- Avoid red-versus-green-only contrasts and low-luminance yellow on white.
- Continuous maps are perceptually ordered; diverging maps have a meaningful midpoint.
- Contrast, line weights, markers, and text survive print and projection.
- Alt-text or a concise accessibility description can be derived from the figure spec and caption.

## Gate 6 — Paper-wide visual system

- A semantic token keeps the same meaning across figures.
- Baselines, proposed method, unavailable/latent information, uncertainty, and adverse states are encoded consistently.
- Typography, panel labels, captions, spacing rhythm, and export naming form one family.
- Type-specific exceptions are intentional: network/hypergraph curves need not mimic architecture connectors; theory math may use manuscript math fonts; graphical abstracts may carry restrained illustration.
- The paper avoids both template monotony and stylistic collage.

## Gate 7 — Editability, reproducibility, and provenance

- D3 artifacts include a nested semantic assembly, composition manifest, structure snapshot, and component board when planned. One ungroup exposes the frozen top-level modules; the next one or two expose primitives without meaningless wrapper groups.
- D3 manual QA edits text, moves and copies a module, copies a reusable module from the component board when required, reassembles it, and exports a fresh preview in the declared editor. Moving one module must not corrupt siblings; cross-module connectors stay in the interconnect layer or follow the declared port rule.
- D3 sources are not whole-canvas images. Any exact plot, photo, scan, or E0 illustration retained as an atomic asset is named, independently replaceable, and justified in the composition manifest.
- D2 artifacts include the native editable source; D1 artifacts include code/spec and source pointers.
- Prompts, model/provider route, reference-image roles, and invariants are saved for generative work.
- Exact inputs are identified by path plus hash or immutable run ID when feasible.
- The delivery manifest serves as the build/provenance index linking request, prompt hash, specs, sources, exports, captions, reviews, and manuscript render.
- The manifest records a non-empty producer identity and independent semantic/visual reviewer identities. Semantic review binds spec, caption, and every primary export; visual review binds every primary export and final-size preview/page, with a SHA-256 for each inspected file.
- Final `validate-delivery` writes `delivery-lock.json`, whose hashes cover the frozen program/prompt/freeze record, manifest, sources, exports, captions, QA, reviews, and any verified manuscript render.
- Publication formats live in exports, native-editable formats in editable sources, and spec/caption/QA/review/composition-manifest/structure-snapshot roles use distinct files. Existing locks are immutable; drift cannot be silently re-baselined.
- Failed drafts and scratch files stay outside the permanent delivery set.
- External/cloud artifacts have a local export snapshot and recorded identity.

Run `scripts/visual_contract.py validate-delivery` before declaring the bundle verified.

## Gate 8 — Independent cold review

**Scope:** this gate fires when the artifact is promoted to `current` (the version bound for the manuscript), not on every draft render or refine iteration. Intermediate versions close on the producer's own gate 0-7 pass.

Use two review roles:

### Semantic reviewer

Receives the frozen program/prompt, sources, figure, and caption. Checks factual fidelity, omissions, misleading encodings, claim strength, uncertainty, and forbidden inference. This reviewer need not optimize aesthetics.

### Visual reviewer

Receives final-size exports and rendered manuscript pages. Checks type fitness, reading order, hierarchy, typography, density, consistency, accessibility, and professional finish. This reviewer must not assume scientific correctness from polish.

The producer must not self-certify both. Resolve all P0/P1 findings; retain P2 only with an explicit decision. Aesthetic model scores and same-provider self-critiques are advisory, not independent review.

## Gate 9 — Manuscript integration

- The manuscript cites the correct figure/table number and every panel referenced in text exists.
- Caption defines encodings, abbreviations, uncertainty, and limitations needed for standalone reading.
- Claims in surrounding prose match what the artifact actually supports.
- Float placement, width, page breaks, and caption spacing are acceptable in the compiled PDF.
- Anonymous-submission, accessibility, copyright/license, and venue format rules are satisfied.
- Recompilation from clean sources reproduces the placed artifact.

## Exit ledger

Record each artifact as `pass`, `blocked`, `removed`, or `amended`, with failing gate and next owner. File existence alone never counts as `pass`.
