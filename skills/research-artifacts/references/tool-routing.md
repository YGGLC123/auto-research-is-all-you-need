# Tool Routing for Research Visuals

Choose routes from the truth contract and required handoff, not from visual fashion or whichever tool is easiest to call.

## Contents

- E/D/G fidelity axes and hard constraints
- Preferred route matrix
- Specialist capability notes
- Route failure and fallback

## Three independent axes

### Exactness

- `E2 exact`: values, coordinates, formulas, nodes, edges, time, topology, counts, and ordering must match a source or deterministic rule.
- `E1 semantic-exact`: named entities and relations must be exact; layout, icons, and visual metaphor may vary.
- `E0 illustrative`: communicates orientation or intuition and is not evidence for exact facts.

### Editability

- `D3 composition-ready`: the assembly, reusable modules, and primitives form named nested groups; a user can ungroup one level, move/copy/replace modules, and reassemble in the declared native editor.
- `D2 native-editable`: text, nodes, edges, and layout can be edited individually in Draw.io, Figma, SVG editor, or equivalent.
- `D1 reproducible`: code/spec/data deterministically rebuild the artifact.
- `D0 raster-acceptable`: a bitmap is acceptable because the content is intrinsically illustrative or photographic.

### Pixel-generative freedom

- `G0 prohibited`: no image model touches the final content.
- `G1 draft-only`: a generated draft may explore composition, but the final artifact is rebuilt through a deterministic route and reverified.
- `G2 final-allowed`: generated pixels may remain only for E0 content.

Hard constraints:

```text
E2 -> G0
G2 -> E0
G1 -> generative draft only, then deterministic final rebuild
any draft-only route (including FigJam) -> deterministic final fallback
D2 -> native-editable SVG/TikZ/Draw.io/Figma source; PDF alone is insufficient
D3 -> D2 plus assembly/component hierarchy, component kit when planned, structure snapshot, composition manifest, and real edit/reassembly QA
mixed figure -> classify and route each panel separately
```

Mermaid, Graphviz, plotting grammars, and deterministic layout algorithms are structural generation, not `G1/G2` pixel generation.

## Preferred routes

| Contract | Preferred route | Required handoff |
|---|---|---|
| `E2-D1-G0` numeric evidence | `scientific-publication-plotter`, `scientific-visualization`, deterministic R/Python | code/spec, data/run pointers, vector PDF/SVG, preview, value QA |
| `E2-D1-G0` formal topology | TikZ, Graphviz, NetworkX/custom SVG | node-edge-formula manifest, source, vector export |
| `E2-D2-G0` exact editable structure | Draw.io, controlled Figma Design, native SVG | editable source, manifest, PDF/SVG export, content QA |
| `E1-D2-G0` architecture/workflow | Draw.io or Figma Design | editable source, semantic legend, PDF/SVG snapshot |
| `E1/E2-D3-G0` composable architecture/theory/workflow | nested-group Draw.io, structured SVG, or native PowerPoint with real OOXML groups | assembly/component kit, native source, source-bound structure snapshot, composition manifest, edit/reassembly QA, preview |
| `E1-D1-G0` programmatic diagram | Graphviz/TikZ/SVG or engineering figure code path | deterministic spec/source and vector export |
| `E1-D2-G1` concept draft then rebuild | image generation or schematic draft, followed by Draw.io/Figma/SVG reconstruction | prompt/version plus rebuilt editable source; discard draft as evidence |
| `E0-D0-G2` illustration/teaser | Figure Studio element pipeline (`figure_studio.py`, codex exec gpt-image-2 backend), fallback authored SVG | prompt, input roles, invariants, bitmap, visual/semantic review |
| table | LaTeX, dataframe, spreadsheet tooling | structured source, generation script/formula, rendered table QA |
| mixed | panel-specific routes plus deterministic composer | frozen panel assets, composer source, delivery/build manifest |

## Capability notes

### Scientific publication plotters

Use for exact result, uncertainty, calibration, heatmap, distribution, and ablation panels. Read data from trusted files or immutable runs; never from screenshots. Prefer the active manuscript's `\columnwidth`/`\textwidth` and font context. The common 85/180 mm sizes are fallbacks, not universal laws. Do not quantile-remap, truncate, smooth, or reorder merely for aesthetics without an analysis decision and caption disclosure.

### Draw.io

Use when a local, native-editable architecture, workflow, theory, or provenance diagram is valuable. Deliver the `.drawio` source and a paper-suitable PDF/SVG when supported, plus PNG preview. For D3, use explicit nested group cells with stable IDs; place the finished assembly and component board on named pages/root groups, and prefer uncompressed XML for structural verification. Override fixed pixel typography with final physical-size readability. Orthogonal connectors are appropriate for structured architecture/workflow, not a universal rule for networks, hypergraphs, set maps, or curved scientific pathways.

### Engineering Figure Agent

Use after the visual contract is clear. Reuse its brief, exact-plot, mixed-panel, provider-safety, and prompt/version conventions. Its coarse `image/plot/mixed` mode does not replace E/D/G classification. Do not route a semantic-exact architecture to raster generation merely because it is called a conceptual figure.

### Figma Design

Use when design tokens, reusable components, multi-panel composition, collaborative editing, or fine visual polish materially help. Probe the installed Figma plugin at execution time. Inspect an existing file before writing, work incrementally, and verify screenshots/metadata after meaningful changes. Save a local PDF/SVG snapshot and source identity; do not leave a paper dependent on an unrecorded cloud-only object.

Figma may author a D3 candidate, but the verified D3 master must be rebuilt/exported to locally inspectable structured SVG or Draw.io. A Figma URL, file key, or self-attested node tree alone proves only D2 editability.

Figma is an external write surface. Record the authorized file/project scope before creation or mutation.

### Figma Mermaid-to-FigJam

Use `figma-generate-diagram` only after loading its skill. It supports flowchart, architecture flowchart, sequence, state, Gantt, and ERD drafts. It does not support arbitrary publication diagrams, fine font changes, or node-by-node automated refinement. Use it for collaborative structure exploration or a modest editable draft; move to Figma Design/Draw.io/SVG for publication micro-layout. Reuse `fileKey` during iteration to avoid draft-file sprawl.

### Conceptual image generation — Figure Studio

Use for E0 graphical abstracts, contextual illustrations, visual metaphors, or G1 composition exploration. The claude-code first choice is the **Figure Studio element pipeline** (`figure_studio.py`, codex exec gpt-image-2 backend): generate isolated textless elements and composite them with code, rather than emitting a whole figure. Fallback when the codex backend is unreachable is hand-authored SVG illustration. Text and formulas from an image model are never trusted — text lives in the vector layer as a separate overlay, and image-model glyphs are keyed out. Never generate final numeric charts, theorem statements, topology, participant counts, or system interfaces. For project-bound work, keep the selected asset, prompt, input roles, and invariants in the project. See [figure-studio.md](../../../docs/figure-studio.md).

### Native PowerPoint component kit

Use `presentations:Presentations` only when `.pptx` is a requested native handoff. Build with plain `.mjs` and `@oai/artifact-tool`; preserve the builder as reproducible source. Use named native objects and real nested groups, keep cross-module connectors behind modules, put the component board on a separate slide/canvas, render every slide, and run overflow/overlap plus manual ungroup/edit/copy/reassembly QA. Never flatten the complete figure into one slide image and never use `python-pptx` for this route. If the current backend cannot preserve actual OOXML group shapes, downgrade that PPTX to D2 and choose Draw.io/Figma/structured SVG for the D3 master.

### Scientific Schematics or third-party image providers

Treat as optional generative draft/illustration routes, not automatic scientific authorities. External API keys and uploads require explicit provider authorization. Model self-review scores are advisory only; regeneration may change semantics and therefore requires source comparison. Claims that AI "automatically handles" accuracy, labels, overlaps, or publication quality do not satisfy the quality gates.

### Formal graph and network routes

Use a deterministic node/edge/hyperedge/state manifest. Record layout algorithm and seed when spatial arrangement is non-canonical. For hypergraphs, render actual hyperedges/incidence structure rather than silently substituting a clique expansion. When a force layout is used, treat position as layout unless the analysis explicitly gives it meaning.

### Tables

Keep tables as text/vector artifacts. Use LaTeX, a dataframe renderer, or spreadsheet tooling with explicit formulas and structured source. Do not rasterize or generate a table with an image model.

## Route failure and fallback

Record why a preferred capability failed before falling back. A fallback must preserve the same E/D/G contract. If no available route can preserve exactness or editability, mark the artifact blocked; do not silently relax the contract.
