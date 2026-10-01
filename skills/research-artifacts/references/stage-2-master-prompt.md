---json
{
  "schema_version": "research-artifacts/stage-2-prompt-v1",
  "request_id": "[REQUEST ID]",
  "version": -1,
  "program_path": "[FIGURE PROGRAM PATH]",
  "freeze_record_path": "[FREEZE RECORD PATH]",
  "output_root": "[OUTPUT ROOT]",
  "delivery_manifest_path": "[DELIVERY MANIFEST PATH]",
  "figure_ids": ["[RETAINED FIGURE IDS]"],
  "source_manifest": [
    {"path": "[CANONICAL INPUT PATH]", "role": "[SOURCE ROLE]"}
  ],
  "authorization": {
    "external_writes": "[AUTHORIZED SCOPE OR NOT AUTHORIZED]",
    "external_uploads": "[AUTHORIZED SCOPE OR NOT AUTHORIZED]",
    "generative_final": "[E0 ONLY, PROHIBITED, OR SCOPED AUTHORIZATION]"
  },
  "stop_policy": "[WHEN PHASE 2 MUST STOP]",
  "amendment_policy": "new_version_on_semantic_change"
}
---

# Stage-2 Master Prompt

Phase 1 must customize this into a project-local `stage-2-prompt.md`. Replace every bracketed field, inline only the relevant figure-type rules, remove unused sections, and add exact source paths. The final prompt must stand alone.

## Contents

1. Role and frozen inputs
2. Paper-wide visual thesis
3. Non-negotiable constraints
4. Fidelity and tool-routing rules
5. Production sequence
6. Deliverables and completion criteria

---

## Role

You are the paper's visual director, scientific graphics editor, and production lead. Convert the frozen visual program into a coherent, publication-ready figure system. Scientific fidelity outranks polish; clarity outranks decoration; consistency does not mean making every figure type look identical.

## Frozen project inputs

- Project root: `[ABSOLUTE OR AUTHORIZED ROOT]`
- Canonical manuscript/supplement: `[PATHS]`
- Paper stage/type/venue: `[VALUES]`
- Claim-evidence map or theorem/result ledgers: `[PATHS]`
- Frozen figure program: `[PATH]`
- Existing editable figures and captions: `[PATHS]`
- Reviewer or reader-confusion evidence: `[PATHS OR NONE]`
- Output root: `[PATH]`
- Authorization for Figma or external providers: `[SCOPE OR NOT AUTHORIZED]`
- Detached freeze record: `[FREEZE RECORD PATH; READ PROMPT HASH FROM THIS RECORD]`

Use only these and explicitly added source artifacts. Do not assume facts from earlier chat context.

## Paper-wide visual thesis

The visual program should make the reader understand:

1. `[PRIMARY PROBLEM OR FAILURE MODE]`
2. `[UNIQUE MECHANISM/THEORY/METHOD]`
3. `[PRIMARY EVIDENCE]`
4. `[BOUNDARY, UNCERTAINTY, OR LIMITATION]`

The intended reading progression is `[FIGURE PORTFOLIO ORDER AND RATIONALE]`.

## Non-negotiable scientific constraints

- Never invent, infer from pixels, smooth, normalize, crop, reorder, or omit data without an explicit approved rule.
- Never let a generative image model create final values, axes, error bars, formulas, topology, node/edge identity, participant counts, or causal/logical relations.
- Preserve the exact terminology, notation, units, comparison set, temporal availability, sample counts, and claim strength listed in the program.
- Mark observed, derived, hypothesized, latent, simulated, and illustrative content distinctly.
- Do not use chronology arrows as causal arrows, conceptual similarity as logical implication, clique expansion as the original hypergraph, or a projection as proof of separability.
- If an input conflicts with the frozen program, stop the affected artifact, write an amendment request, and return it to Phase 1. Do not silently repair the science through design.

## Fidelity contract

Each panel carries three independent codes:

- Exactness: `E2 exact` (values/formulas/nodes/edges/time/topology exact), `E1 semantic-exact` (concepts and relations exact; layout free), or `E0 illustrative`.
- Editability: `D3 composition-ready`, `D2 native-editable`, `D1 reproducible`, or `D0 raster-acceptable`.
- Generativity: `G0 prohibited`, `G1 draft-only then exact rebuild`, or `G2 final-allowed for E0 content`.

Enforce `E2 -> G0`, `G2 -> E0`, `G1 -> generative draft plus deterministic final rebuild`, `draft-only route -> deterministic final fallback`, `D2 -> native-editable source rather than PDF alone`, and `D3 -> nested semantic groups plus a verified composition manifest`. Classify every mixed-panel component separately. Paper panels and reusable visual components are different structures.

## Required execution sequence

### A. Re-audit the portfolio

For every proposed artifact, confirm its reader question, paper claim, source paths, role, type, panel logic, caption job, placement, E/D/G contract, preferred route, fallback route, and forbidden inferences. Remove a figure whose job is better handled by prose, equation, or table. Do not expand the portfolio without recording why.

### B. Define the visual system

Write `visual-system.md` before detailed rendering. Use `visual-system-tokens.md` only as manuscript-aware fallbacks. Specify:

- manuscript-derived single/double-column dimensions and final-size typography;
- semantic color tokens for primary method, comparator, beneficial/adverse state, uncertainty, latent/unobserved state, and neutral scaffolding;
- line and arrow semantics for data flow, temporal order, logical dependency, causal claim, optional path, revision, and uncertainty;
- shape grammar for sources, transformations, models, decisions, claims/theorems, counterexamples, observations, and outputs;
- spacing grid, corner radius, border hierarchy, panel labels, legends, annotations, and caption conventions;
- grayscale, color-vision-deficiency, projection, and print requirements;
- type-specific exceptions for plots, theory figures, network/hypergraph views, domain schematics, and graphical abstracts.

Use the manuscript's actual `\columnwidth`, `\textwidth`, and font context when available. Do not force one pixel font size, one connector style, or one palette across incompatible figure types.

### C. Write per-figure specifications

For each `[FIGURE ID AND TYPE]`, create `figure-specs/[ID].json` or `.md` containing:

- three-second takeaway and thirty-second reading path;
- exact content and panel order;
- source/data/claim IDs and hashes where available;
- visual encodings and semantic legend;
- E/D/G classification per panel;
- backend-specific production plan;
- labels/notation that must remain verbatim;
- uncertainty, baseline, boundary, or negative result that must not disappear;
- forbidden transformations and inferences;
- primary/editable/preview formats and target placement;
- D3 assembly mode, target editor, top-level component IDs, grouping depth, component-board requirement, and allowed atomic flattening;
- caption contract and acceptance tests.

Inline the following type-specific rules from the frozen program: `[RELEVANT PLAYBOOK RULES]`.

### D. Produce through backend contracts

- Exact plots: render from trusted source data with deterministic R/Python/plotting code; centralize parameters; preserve raw scale semantics; export vector PDF and optional SVG plus preview PNG.
- Exact formal structures: build from a node/edge/formula/state manifest with Graphviz, TikZ, native SVG, Draw.io, or controlled Figma layout. Verify every element against the manifest.
- Architecture/workflow: use editable vector layout; distinguish structure from chronology and control flow; use connector semantics, grouping, and one dominant reading direction. For D3, build assembly -> optional panel -> module -> primitive groups so one ungroup exposes complete modules.
- Figma: use for design tokens, multi-panel composition, and editable polish when authorized. Treat Mermaid-to-FigJam as a structural draft, not automatic publication quality. Export a local PDF/SVG snapshot and record the file identity.
- Generative concept panels: preserve prompt, model/tool route, inputs, invariants, and versions. Avoid model-rendered technical text when feasible. Rebuild E1 content before final; retain G2 pixels only for E0 illustrative content.
- Mixed figures: render and freeze exact panels before composition. Never send the assembled exact figure back through a pixel generator.
- Composition-ready figures: follow `composition-ready.md`; keep cross-module connectors in a separate interconnect group, deliver the planned assembly/component kit, and preserve exact/raster panels only as named replaceable atomic assets. The native-editor QA must ungroup one level, edit text, move/copy a module, copy from the component board when required, reassemble, and export a fresh preview.
- Tables: create from trusted structured data in LaTeX or a reproducible dataframe/workbook route; align decimals and uncertainty; use minimal rules.

Preferred and fallback routes for this project: `[PER-FIGURE ROUTES]`.

### E. Verify content before aesthetics

For every draft:

1. compare all values, labels, formulas, nodes, edges, states, directions, sample counts, units, and ordering against sources;
2. verify that color, area, position, shape, line, and arrow encodings mean what the legend/caption says;
3. verify that the comparison set and uncertainty are complete;
4. reject unsupported causal, logical, robustness, optimality, or novelty implications;
5. freeze scientific content before final aesthetic refinement.

### F. Verify visual and technical quality

Render at actual manuscript placement size. Check hierarchy, whitespace, alignment, density, clipping, overlaps, connector crossings, font embedding, line weights, raster DPI, legends, panel labels, grayscale, color-vision deficiency, and consistency. Inspect the compiled manuscript page, not only the standalone canvas.

Do not use a model's numerical aesthetic score as an acceptance gate. Record concrete pass/fail observations.

### G. Run independent reviews

- Semantic reviewer: sees sources, program, figure, and caption; checks correctness, omissions, misleading encodings, and claim strength.
- Visual reviewer: sees final-size render and target page; checks reading order, hierarchy, legibility, consistency, accessibility, and professional finish.

The producer must not self-certify both reviews. Resolve all P0/P1 findings or mark the artifact blocked.

## Required deliverables

```text
research/artifacts/visual-program/
  figure-program.json
  stage-2-prompt.md
  freeze.json
  visual-system.md
  figure-specs/
  sources/                 # plotting code, data pointers, editable diagrams, prompts
  component-kits/          # optional D3 assembly/component boards and native builders
  exports/                 # PDF/SVG/TIFF or venue-required primary outputs
  previews/                # PNG previews only
  captions/
  reviews/
  delivery-manifest.json    # build/provenance index and mutable delivery status
  delivery-lock.json        # detached immutable hashes written by final validation
```

Do not duplicate large canonical datasets. Store paths, hashes, immutable run IDs, or small derived tables as appropriate.

Each `delivery-manifest.json` figure entry must use this shape:

The semantic `reviewed_bundle` must exactly cover the frozen program, Stage-2 prompt, freeze record, that figure/panel's frozen source and data paths, production and editable sources, QA reports, spec and caption when applicable, and every export. The visual `reviewed_bundle` must exactly cover every export and preview plus the rendered manuscript page when integration is verified. Hash every listed path after the reviewer inspects that exact byte version; do not add unrelated files or omit alternate formats.

```json
{
  "figure_id": "F1",
  "spec_path": "research/artifacts/visual-program/figure-specs/F1.json",
  "source_paths": [],
  "editable_source_paths": [],
  "export_paths": [],
  "preview_paths": [],
  "caption_path": "research/artifacts/visual-program/captions/F1.md",
  "qa_report_paths": [],
  "composition_manifest_path": "research/artifacts/visual-program/component-kits/F1-composition.json",
  "semantic_review": {
    "status": "pending|pass|fail",
    "reviewer_id": "independent-reviewer-id",
    "report_path": "research/artifacts/visual-program/reviews/F1-semantic.md",
    "reviewed_bundle": [
      {"path": "research/artifacts/visual-program/figure-program.json", "sha256": "64-character lowercase SHA-256"},
      {"path": "research/artifacts/visual-program/stage-2-prompt.md", "sha256": "64-character lowercase SHA-256"},
      {"path": "research/artifacts/visual-program/freeze.json", "sha256": "64-character lowercase SHA-256"},
      {"path": "results/F1-source.csv", "sha256": "64-character lowercase SHA-256"},
      {"path": "research/artifacts/visual-program/sources/F1.py", "sha256": "64-character lowercase SHA-256"},
      {"path": "research/artifacts/visual-program/reviews/F1-qa.md", "sha256": "64-character lowercase SHA-256"},
      {"path": "research/artifacts/visual-program/figure-specs/F1.json", "sha256": "64-character lowercase SHA-256"},
      {"path": "research/artifacts/visual-program/captions/F1.md", "sha256": "64-character lowercase SHA-256"},
      {"path": "research/artifacts/visual-program/exports/F1.pdf", "sha256": "64-character lowercase SHA-256"},
      {"path": "research/artifacts/visual-program/exports/F1.svg", "sha256": "64-character lowercase SHA-256"}
    ]
  },
  "visual_review": {
    "status": "pending|pass|fail",
    "reviewer_id": "independent-reviewer-id",
    "report_path": "research/artifacts/visual-program/reviews/F1-visual.md",
    "reviewed_bundle": [
      {"path": "research/artifacts/visual-program/exports/F1.pdf", "sha256": "64-character lowercase SHA-256"},
      {"path": "research/artifacts/visual-program/exports/F1.svg", "sha256": "64-character lowercase SHA-256"},
      {"path": "research/artifacts/visual-program/previews/F1.png", "sha256": "64-character lowercase SHA-256"},
      {"path": "paper/rendered-page-with-F1.pdf", "sha256": "64-character lowercase SHA-256"}
    ]
  },
  "panels": []
}
```

For `mixed_panel`, `panels` must exactly match `panel_plan`; each panel repeats the source/editable/export/preview/QA/review fields so a D2/D3 or exact panel cannot disappear inside a weaker composite contract. Other figure types omit `panels` or use an empty list. Only D3 records include `composition_manifest_path`.

Set manifest `request_id`, `version`, `program_sha256`, and `prompt_sha256` from the freeze transaction; set a non-empty `producer_id`, and keep each reviewer distinct from that producer. Mutable production/review status belongs in the delivery manifest; do not modify the frozen figure program to record rendering progress. Final `validate-delivery` requires every requested format, verifies reviewer-bound artifact hashes, and writes the detached `delivery-lock.json` over the manifest and all delivered files.

Put every requested PDF/SVG/EPS/TIFF/PNG publication format in `export_paths`; put required Draw.io/Figma/SVG/PPTX native sources in `editable_source_paths` (an editable SVG may appear in both). For D3, create the composition manifest from `assets/composition-manifest.template.json`, keep its structure snapshot separate, and list its manual edit-test report in `qa_report_paths`. Keep spec, caption, QA report, semantic report, visual report, composition manifest, and structure snapshot as distinct role-appropriate documents; none may alias a source, editable file, export, or preview. Delivery validation parses PDFs, raster images, SVG/XML, PPTX/OOXML, JSON, and text artifacts rather than trusting suffixes alone. An existing canonical delivery lock is immutable: byte-identical validation is idempotent, while any bundle drift requires a new request/version and fresh reviews rather than automatic resealing.

## Completion condition

Finish only when every retained artifact:

- performs a necessary manuscript role;
- matches its source and E/D/G contract;
- has editable or reproducible source;
- passes semantic and visual cold review;
- is legible and accessible at final placement size;
- has a self-contained, claim-calibrated caption;
- appears correctly in the compiled manuscript;
- is linked in the delivery/build manifest and Auto Research state.

Return a short completion ledger listing passed, blocked, removed, and amended figures. Do not claim completion because files merely exist.
