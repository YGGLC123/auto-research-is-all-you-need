# Phase 1 — Visual Program Direction

Phase 1 is a planning and prompt-compilation phase. Its output must let a fresh Phase-2 agent design and produce the paper's visuals without access to the earlier conversation.

## Contents

1. Establish ground truth
2. Diagnose the current figure program
3. Plan the portfolio
4. Assign figure types and E/D/G contracts
5. Define the visual grammar
6. Route tools by evidence contract
7. Compile the Stage-2 prompt
8. Validate and freeze
9. Adapt by paper stage

## 1. Establish ground truth

Read only user-authorized artifacts and record exact paths or stable IDs:

- canonical manuscript and supplement;
- claim-evidence map, theorem ledger, experiment runs, datasets, tables, and logs;
- current figures, editable sources, captions, and their manuscript references;
- reviewer complaints or reader-confusion evidence;
- target venue/track and current official figure constraints when they matter;
- existing visual tokens or house style that must be preserved.

Treat the latest user request and verified artifacts as stronger than folder names or stale planning notes. Do not infer data from screenshots or promote a conceptual sketch into evidence.

## 2. Diagnose the current figure program

For every existing or proposed artifact, answer:

1. What exact reader question does it answer?
2. What paper claim or limitation does it support?
3. Is its role `orientation`, `explanation`, `evidence`, `diagnostic`, `comparison`, or `synthesis`?
4. What should a reader learn in three seconds, and what should become clear after thirty seconds?
5. Is the content numeric-exact, structurally exact, interpretive, or merely illustrative?
6. Which source files make every value, node, edge, label, and ordering defensible?
7. Does another figure already perform the same job?
8. Is the figure overloaded with structure, chronology, control flow, and results at once?
9. Would a table, equation, prose sentence, or supplementary artifact communicate it better?
10. What misunderstanding or reviewer objection remains if the figure is removed?

Classify each current figure as `keep`, `revise`, `split`, `merge`, `replace`, `move_to_supplement`, or `remove`. A paper does not improve merely by gaining more figures.

## 3. Plan the portfolio, not isolated canvases

Choose a sequence that follows the paper's reasoning rather than the order in which code happened to be written. A common but non-mandatory progression is:

1. problem or failure mode;
2. method/theory mechanism;
3. protocol or data provenance;
4. primary evidence;
5. mechanism-discriminating ablation/diagnostic;
6. limitations, boundary, or qualitative case.

Assign each artifact one dominant job. Split a figure when it tries to explain architecture, execution order, formal logic, and benchmark results simultaneously. Combine panels only when they share one caption-level argument.

Plan single-column, double-column, full-page, or supplement placement before choosing label density. Define the caption job: context, encoding, result, uncertainty, and limitation that must be understandable without hunting through the main text.

## 4. Select figure type and truth contract

Use `figure-type-playbooks.md`. Record:

- `figure_type` and why it matches the reader question;
- content regime (`exact_numeric`, `exact_structural`, `interpretive`, or `illustrative`) used to assign the E/D/G codes;
- `paper_claim_ids` and source paths;
- panel order and comparison set;
- required uncertainty, baseline, negative result, or boundary information;
- forbidden inference, decorative element, or causal implication;
- editable-source requirement and primary/preview formats.

Use `D3 composition-ready` when the user must ungroup the assembly into meaningful modules, copy/rearrange those modules, or build variants from a component board. Read `composition-ready.md`. `panel_plan` remains the paper's argumentative panel structure; `composition_plan` separately defines reusable visual modules.

If exact and illustrative content share a figure, mark it `mixed_panel`. The figure-level E/D/G fields describe the deterministic final composition; keep each panel's `figure_type`, E/D/G codes, sources, data paths, and route explicit in `panel_plan`.

Use this figure-entry contract in `figure-program.json`:

```json
{
  "figure_id": "F1",
  "title": "Method overview",
  "role": "explanation",
  "reader_question": "Why does the method preserve group structure?",
  "three_second_takeaway": "Group constraints narrow the admissible state before prediction.",
  "paper_claim_ids": ["C-METHOD-1"],
  "figure_type": "method_architecture",
  "exactness": "E1",
  "editability": "D3",
  "generativity": "G0",
  "source_paths": ["research/method-spec.md"],
  "data_paths": [],
  "panel_plan": [{"panel_id": "a", "job": "input to constrained representation"}],
  "preferred_route": "drawio",
  "fallback_route": "figma-design",
  "target_placement": "double_column",
  "primary_formats": ["drawio", "pdf", "svg"],
  "preview_format": "png",
  "editable_source_required": true,
  "composition_plan": {
    "mode": "both",
    "target_editors": ["drawio"],
    "hierarchy": ["assembly", "module", "primitive"],
    "min_top_level_components": 3,
    "max_ungroup_steps_to_primitives": 2,
    "component_board_required": true,
    "top_level_components": [
      {"component_id": "F1.input", "job": "encode inputs", "reusable": true},
      {"component_id": "F1.model", "job": "show the learned mechanism", "reusable": true},
      {"component_id": "F1.output", "job": "show outputs", "reusable": true}
    ],
    "allowed_flattening": [],
    "locked_elements": ["module names", "interface direction"]
  },
  "caption_job": "Define modules, arrow semantics, and the non-causal role of group constraints.",
  "forbidden_inferences": ["The diagram does not establish causal identification."],
  "status": "planned",
  "risks": []
}
```

For `mixed_panel`, use `preferred_route: "deterministic-composite"`, set the figure-level exactness to the strictest panel, keep the final composition at `D1`, `D2`, or `D3` and `G0`, and expand each `panel_plan` item. Use D3 only when the composed panels/modules must remain independently rearrangeable; exact plot panels may stay frozen replaceable SVG assets inside the D3 assembly.

```json
{
  "panel_id": "b",
  "job": "show the exact calibrated risk frontier",
  "figure_type": "calibration_uncertainty",
  "exactness": "E2",
  "editability": "D1",
  "generativity": "G0",
  "source_paths": ["research/claims.md"],
  "data_paths": ["results/risk-frontier.csv"],
  "preferred_route": "exact-plot",
  "fallback_route": "",
  "primary_formats": ["pdf", "svg"],
  "preview_format": "png",
  "editable_source_required": false
}
```

Allowed types and routes are enforced by `scripts/visual_contract.py`; use the exact identifiers from `figure-type-playbooks.md` and `tool-routing.md`.

## 5. Define a preliminary visual grammar

Give Phase 2 constraints, not a finished pixel layout. Use `visual-system-tokens.md` as a fallback vocabulary while deriving actual dimensions and typography from the manuscript:

- semantic color roles that remain stable across the paper;
- typography relationship to the manuscript and minimum final-size readability;
- shape families for data, model, latent state, observation, decision, theorem, counterexample, and output;
- arrow/line semantics for data flow, temporal order, logical dependence, causal claim, uncertainty, and optional paths;
- panel-label and legend conventions;
- grayscale and color-vision-deficiency requirements;
- whether icons, textures, illustrations, or domain imagery are allowed.

Avoid one universal appearance for every figure. Data evidence should be restrained; theory diagrams should be semantically formal; architecture should emphasize boundaries; workflows should emphasize order; graphical abstracts may carry more visual character.

## 6. Route tools before writing the production prompt

Use `tool-routing.md` to choose a preferred and fallback route for every artifact. Decide separately:

- how truth is encoded;
- how the source stays editable or reproducible;
- where aesthetic composition happens;
- how the final export is verified.

Do not choose Python merely because it is available, or choose a generative model merely because it looks polished. Use code for exact geometry and values, editable design tools for controlled composition, and generation only for content whose truth contract permits interpretation.

## 7. Compile the Stage-2 prompt

Customize `stage-2-master-prompt.md`. The resulting `stage-2-prompt.md` must:

- begin with the required `---json` front matter bound to request/version, program/freeze/output/delivery paths, retained figure IDs, the complete source manifest, authorization, and stop/amendment policies;
- name canonical inputs and forbid hidden-chat assumptions;
- restate the paper's visual thesis and figure portfolio;
- inline the relevant type-specific rules and tool routes;
- preserve exact labels, notation, units, comparison sets, and claim strength;
- specify output folders, editable sources, exports, captions, and the delivery/build manifest;
- for D3, specify the assembly mode, component hierarchy, component board, native target editor, structure snapshot, composition manifest, and manual edit/reassembly test;
- define per-figure tests and paper-wide quality gates;
- identify unresolved choices and stop conditions;
- require independent semantic and visual reviews;
- require an amendment instead of silently changing a frozen claim, source, role, or fidelity class.

Remove unused template sections and all placeholders. A prompt that merely says "make the figures publication quality" is not frozen.

## 8. Freeze and hand off

Validate `figure-program.json`. Freeze Phase 1 only when:

- every retained figure has a necessary role and traceable sources;
- the portfolio has no unexplained duplication or missing headline evidence;
- type, fidelity, route, placement, formats, caption job, and risks are explicit;
- the Stage-2 prompt is self-contained and names its output contract;
- unresolved scientific questions are routed back to evidence, method, theory, or experiments rather than hidden inside art direction.

The prompt must not contain its own SHA-256. `freeze.json` records that detached hash after the prompt is final. The frozen program remains byte-immutable; later `stale` or `superseded` labels belong in Auto Research state while the replacement request carries `supersedes` metadata. Record the hash and `visual_plan.status=frozen`; Phase 2 must start from this handoff.

For a semantic revision, initialize a new request directory or higher version and set `supersedes` to the prior request ID, version, project-local freeze-record path, and that freeze file's SHA-256. Never change the old program, prompt, or freeze record in place.

## Stage adaptation

- `idea_only` / `preliminary_study`: produce an opportunity map or provisional explanatory sketch; do not create result-like visuals.
- `method_taking_shape`: use diagrams to test interfaces and assumptions, and mark unstable components visibly.
- `experiments_underway`: freeze plot specifications and comparison logic; only render results from immutable runs.
- `manuscript_draft`: run the full paper-wide program and caption alignment.
- `rejected_or_borderline`: begin with reviewer-confusion and figure-role diagnosis; do not cosmetically rescue unsupported claims.
- `final_submission_or_rebuttal`: prioritize compliance, legibility, consistency, and compiled-placement QA; reopen structure only for a fatal issue.
