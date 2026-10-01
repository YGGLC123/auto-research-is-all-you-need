# Figure Studio — the generative-element figure pipeline

Durable operating doc for `figure_studio.py`, the skill-local tool that builds a
publication figure by generating **isolated visual elements** and compositing
them with code, instead of asking an image model to draw a whole figure. It is
the production engine behind the `imagegen-conceptual` route in
[../skills/research-artifacts/references/tool-routing.md](../skills/research-artifacts/references/tool-routing.md);
the skill entry point is [../skills/research-artifacts/SKILL.md](../skills/research-artifacts/SKILL.md).

Run it from the skill directory (`skills/research-artifacts/`), mirroring the
`visual_contract.py` convention:

```
FSG = py scripts/figure_studio.py        (Windows; POSIX: python3 scripts/figure_studio.py)
```

Eleven subcommands: `fsg init|validate|freeze`, `compile`, `gen`, `refine`,
`assemble`, `qa`, `key`, `ledger`, `self-test`.

## Why elements, not whole figures

Every design choice below is backed by the eight-track siege study, not taste.
Whole-panel generation reaches publication grade only ~24% of the time versus
~80% for isolated-element compositing (LiveFigure); model-drawn connectors score
45.8/100 for fidelity (PaperBanana). So the model only ever paints one isolated
node on plain white, and code owns everything an image model gets wrong: edges,
exact plots, text, and layout.

### The four iron rules (non-negotiable, all evidence-backed)

1. **Edges are drawn by code, never by the model.** The model paints nodes;
   connectors, arrows, and routing come from the layout solver and the SVG
   assembler. Organic edges are the rare explicit exception (`render:"generative"`).
2. **Exact plots go through matplotlib only.** Any panel carrying numeric
   evidence is an `exact-plot` artifact; it never touches image generation. This
   is the same E2/G0 rule the visual contract already enforces.
3. **Text lives in the vector layer only.** Generated pixels carry no glyphs; the
   only mark a generation may contain is a solid magenta placeholder bar that
   code later replaces with real vector type.
4. **The skeleton is never shown to the image model as an image.** Feeding the
   layout back as a reference picture makes the model slavishly copy it; the
   skeleton is translated into a structured prose prompt instead.

## The six-round pipeline (per figure)

```
R0 planning    paper claim -> figure list -> one fsg.json per figure (frozen)   [no generation]
R1 style anchor one whole-figure reference to set the key; enters the -i chain   [1 generation]
R2 element gen  each node generated textless, isolated (parallel <=3)            [N generations]
R3 edit rounds  per-element delta instructions + -i previous version; dirty only [as needed]
R4 assemble     key out magenta -> embed into SVG skeleton -> vector text -> PDF [no generation]
R5 qa           mechanical checks (font/collision/palette/density) + critic JSON [no generation]
```

### R0 — the 7-step decomposition procedure

The decomposer is the mainline model (research-artifacts Phase 1). Its product is
the `fsg.json` directly. Seven steps, each with a mechanical check downstream:

1. **Start from the claim** — inputs are the paper claim the figure serves plus a
   one-sentence message; the mechanism nouns in that message are the elements
   that must be visible.
2. **Classify first** — pick one of five archetypes
   (`linear-flow` / `cyclic` / `hierarchical` / `dual-stream` / `central-hub` /
   `comparison`); each carries a panel-skeleton prior.
3. **Panel split** — a panel is a stage of the reading path answering one
   sub-question; a hero figure is usually ≤4 panels.
4. **Noun–verb pass** (DiagrammerGPT-style) — mechanism-bearing noun phrases
   become candidate nodes; the verbs between them become edges.
5. **Granularity, three tests** — *atomic* (one nameable semantic, plausibly
   regenerated alone, no text and no arrows inside); *merge* (things that always
   change together and are never labelled apart become one); *reuse* (one
   semantic appearing in many places gets `instance_of` — generated once, placed
   many times by code).
6. **Self-contained description** — the `semantic` field must read standalone
   ("a machine that eats policy text and emits an executable checklist"); it is
   both the requirement and the prompt seed.
7. **Adversarial re-read** (free, pure text, before any quota is spent) — a
   refuter subagent reads only the FSG and answers: can the paper claim be
   reconstructed from this graph? what is missing? what is extra?

**Hard budgets** (`fsg validate` enforces): ≤7 perceptual chunks per panel;
**5–12 generated elements per figure** is the sweet spot (<3 reads as clip-art,
>12 clutters, drifts, and burns quota); exactly one `primary` element per figure;
text with no anchor, an edge with a missing endpoint, or an orphan node is a hard
error. `fsg validate` also **warns** when more than half the counted elements use
`text_strategy=chroma` — chroma is a label-plate placeholder, not artwork, so
icon-scale elements should use `none`. Budget order of magnitude: anchor 1 + elements 5–12 + ~20–30% refine
retries ≈ 8–16 generations for a first hero figure (15–25 min at ≤3 concurrency).

### R1–R5 in brief

- **R1 style anchor** — one whole-figure reference generation (`E00`, the special
  style element) fixes palette, line weight, and flatness; every later element
  cites it through the `-i` reference chain.
- **R2 element generation** — `gen` drives each pending node textless and
  isolated on plain white; concurrency ≤3 to respect the subscription quota.
- **R3 edit rounds** — edit-don't-reroll: for a figure that is ~80% right, issue a
  delta instruction with `-i` the previous version; only dirty nodes are touched.
- **R4 assemble** — code only: chroma-key the magenta out, **trim each asset to
  its content bbox** (the uniform border colour is read from the four corners, and
  everything within tolerance of it is cropped away, keeping a 2% margin) so a
  generation's own margin does not stack with the slot letterbox and stamp-size the
  element, embed the trimmed PNGs into the Graphviz-solved SVG skeleton (**no white
  placeholder rectangle is painted behind an asset** — the slot rects are dropped),
  lay the vector text on top, export PDF. Texts sharing one anchor **stack** (panel
  titles up; then labels and annotations down, labels first) at a 1.35× line
  advance, and `qa`'s collision estimator uses the same stacking model so it agrees
  with what is drawn.
- **R5 qa** — mechanical gate (`qa`) then the critic JSON checklist, then the
  existing research-artifacts cold-review gates, which are unchanged.

## Band-grid presentation template (banded archetypes)

For `hierarchical` / `linear-flow` / `dual-stream` archetypes with ≤4 panels,
`compile` uses a deterministic pure-Python **band-grid** solver instead of
Graphviz (set `layout.prefer:"graphviz"` to force the retreat path). It emits one
open horizontal band per panel — a thin full-width rule plus a typographic header,
no panel boxes — and renders the whole SVG itself, so a missing Graphviz is not a
degrade in this mode. Presentation rules the assembler enforces:

- **Visual dominance and slot-height floors.** Every asset zone is floored by
  saliency in **physical mm** — **primary ≥34 mm, secondary ≥20 mm, ambient ≥12 mm**
  (print-honest at 183 mm double-column) — and ≥65% of each band's content height
  (below the header) stays with the assets, with the label bars compressed to just
  their stack height so the visuals dominate. The primary's ×1.9 area factor is a
  same-layer relative enlargement on top of the floor, so the hero is genuinely
  large. Band heights are **summed by direct arithmetic**: each band needs
  `header + Σ_layer max(slot floor) + measured text-strip heights + inner padding`,
  the inter-band gap is a physical 6 mm, and `canvas_needed = Σ needs + Σ gaps +
  frame margins`. When `height_mm < canvas_needed`, `compile` **hard-fails and prints
  `ceil(canvas_needed)` as the minimum `height_mm`** (the author fixes the fsg — the
  tool never silently starves the figure to postage stamps). Because the needs are
  physical mm and independent of the canvas height, the suggestion is a single
  addition (no binary search) and recompiling at exactly that height always fits;
  any surplus above the needs is distributed to bands in proportion to their needs
  and poured entirely into the asset zones.
- **Asset fill.** Each asset is contain-fit to **88%** of its slot; a wide asset
  expands its width toward the layer's available cell (minus the gutter) rather than
  being crammed into a narrow slot for symmetry.
- **Three connector classes** (stroke weight as a fraction of canvas width): a
  `lane`-colored edge at **0.45%**, a neutral dataflow/stage-flow edge at **0.30%**,
  and a `diagnostic`-typed edge at **0.16%**, dashed at 50% opacity; arrowheads
  scale with the stroke.
- **Port discipline and routing.** Edges exit the source **bottom-center** and
  enter the target **top-center**; multiple edges at one port fan out over ±20% of
  the slot width; a vertical segment never crosses a non-endpoint slot (the router
  jogs through a clear lane), and long multi-band edges (e.g. diagnostics) route
  along the right-margin ambient channel instead of sweeping the main field.

## FSG schema (field-group summary)

One `research/artifacts/visual-program/fsg/F##.fsg.json` per figure; everything
downstream compiles from it, and a tweak is a patch plus a dirty-node recompile.
The schema is Penrose-inspired: content references type names, not pixels. Field
groups (see the tool source for the exact JSON):

- **`figure`** — id, three-second `message`, `archetype`, `canvas`
  (width/height mm, column), `version`, `frozen_sha`.
- **`ontology`** — declared `node_types` and `edge_types` the content may cite.
- **`panels`** — id, role, reading order.
- **`nodes`** — `E##` id, panel, type, self-contained `semantic`, `saliency`
  (primary/secondary/ambient → compiled into composition instructions),
  `rhythm_group`, `representation` (`physical-schematic` | `math-viz`) plus a
  `viz_idiom` for math-viz nodes, `instance_of` (reuse pointer), and a `gen`
  block (strategy raster/vector/plot/reuse, `text_strategy` none/chroma/plate,
  `spec_version`, `generation`, `asset` path).
- **`edges`** — `L##` id, type, from/to, scope, routing, `render` (`code` default,
  `generative` only by explicit exemption), `label_ref`.
- **`constraints`** — the scientific layer, hard (`ensure`) or soft (`encourage`).
- **`layout`** — solver, seed, and solver-emitted `bboxes` (0–100 normalized,
  never hand-written).
- **`texts`** — `T##` id, anchor element, role, content, size, weight, max chars.
- **`style`** — named style `language`, spoken-word `palette_words` (never hex —
  hex/pt tokens induce garbled output), code-layer `palette_hex`, venue guide,
  anchor asset.
- **`compile`** — dirty list and per-node hashes.

## Coding grammar

Structural IDs inside an FSG: `F##` figure / `P##` panel / `E##` element
(`E00` = style anchor) / `L##` edge / `T##` text. The iteration chain in the
ledger and asset filenames is `F02.E03.S04.G01` = figure 2 · element 3 · prompt
spec version 4 · generation 1. `S` (spec version) avoids colliding with `P`
(panel); `G` is the generation counter. Each generated image carries a JSON
sidecar: full prompt, FSG hash, latency, driver params.

## Representation typing — content decides the vocabulary

A node's ontological type decides how it may be drawn (user directive, 2026-07-19):

- **Physical things** (instruments, servers, documents-as-objects) →
  `representation: "physical-schematic"` — the illustration template with
  metaphor latitude.
- **Mathematical/statistical concepts** (a power computation, a budget
  threshold, a positive-part function, pooled sampling) →
  `representation: "math-viz"` with a `viz_idiom` — the prompt switches to
  algorithm-visualization vocabulary (thin axes, curves, threshold lines, point
  clouds, grids/matrices) and **bans machines, devices, gadgets and 3D objects
  outright**. Creative latitude moves from metaphor to composition and curve
  shaping; every mark must carry meaning.

The R0 noun–verb pass must therefore also ask, per element: *is this noun a
THING or a CONCEPT?* Decorative machinery wrapped around a concept is a defect,
not a style. When most elements are math-viz the style anchor (E00)
automatically inherits the math-viz voice; `fsg validate` warns on raster nodes
with no declared representation.

**Render grade is the orthogonal axis** (user directive, 2026-07-20):
representation decides *what* is drawn, `style.render_grade` decides *how it is
rendered*. `"premium"` (the default) buys what the image model is actually for —
high-end editorial science illustration (Quanta/Nature-cover polish: soft studio
light, real depth, grounded shadows, ceramic/glass/metal materials, restrained
in-palette gradients) while the underlying geometry stays exactly the declared
diagram. `"flat"` keeps the old skeletal minimalism (drafts, or venues that
demand it). Never conflate the axes: over-constraining the render to flat
produces elements code could have drawn, wasting the generative model.

## Two driver modes, and why

`model_reasoning_effort` tunes the **driver agent** (the Codex model), not the image
model. The intelligence is front-loaded into `compile`, so by the time the prompt
reaches the driver it is a finished product; a higher-effort courier does not buy
a better picture — it buys opinions.

- **`gen` (default, low effort)** — a mechanical courier: generate once, zero
  post-processing, deterministic bookkeeping. ~77 s per image. Low effort is
  chosen precisely because there is nothing left for the courier to decide;
  higher effort was measured self-regenerating to "fix" magenta and overwriting a
  688 KB asset down to 22 KB, 3–4× slower.
  - **`--ref-anchor` / `--no-ref-anchor`** — the E00 style anchor is attached to
    every element generation via `-i` to hold palette and line-weight steady. It
    is **on by default whenever E00 already has an asset on disk** (and never
    attaches E00 to itself); `--no-ref-anchor` opts out. The anchor used is
    recorded in each generation's sidecar (`driver.ref_anchor`).
  - **`--reconcile`** — no generation: it repairs every raster node's
    `gen.generation`/`gen.asset` pointer from disk truth, scanning the asset pool
    for `<FID>.<EID>.<S##>.<G##>.png` at the node's current spec version (highest
    existing `G` wins) and printing each repair. Use it after a crash or a manual
    asset shuffle to re-sync the FSG to what is actually on disk. Runs standalone.
  - **Concurrency-safe writes** — every FSG mutation (gen/refine/reconcile/
    compile/freeze) now takes a short `<fsg>.lock` lease (pid+timestamp, 120 s
    stale takeover) and re-reads the freshest copy before writing back, so two
    parallel `gen` runs on different elements of one figure no longer clobber each
    other's asset pointers.
- **`refine` (explicit, high effort)** — a bounded in-loop critic. Input = the
  current asset + the node's FSG spec + the compiled JSON checklist; action =
  judge, then at most **N edit iterations (default 2, `--max-iters`)** with `-i`
  the previous version. **Every iteration lands as a new `G` asset and never
  overwrites**; the run returns the verdict plus all versions. It judges **only**
  the compiled checklist — inventing its own criteria is forbidden — and it is
  triggered by a mechanical QA flag or an explicit call, never by default.

## Magenta chroma-key (parameters written into the code)

- The model never emits exact `#FF00FF` — measured fills land around (253,1,251)
  with thousands of pixels of anti-alias halo. So the key is a **wide pink-family
  band**, not an equality test: `R>G+4 && B>G+4 && R≥170 && B≥160 && |R−B|≤90`,
  with a **2 px dilation, 8-connectivity**; residual after cleanup was 183 px
  (98%+ of the halo removed).
- Connected components cluster by row into label-zone bounding boxes (20 measured
  bars collapsed to exactly 4 zones, all positions correct); vector text is then
  pasted back per zone.
- The placeholder form is a **solid magenta bar** — models draw bars more
  reliably than glyphs and they key cleaner. `key --expect N` merges widely-spaced
  same-row zones down to exactly N when the zone count is known.
- The empty-plate strategy survives only for manual / vector-placed labels: a
  plate interior is background white, not keyable, and is found by shape instead.

## Typography and palette floors (mechanical QA)

Body text 6–8 pt at final width; panel labels 8 pt bold; line weight ≥0.5 pt;
single column 89 mm / double column 183 mm; Okabe-Ito palette with dual encoding;
≤7 perceptual chunks per panel. These are the same physical-size floors the
production gates already apply, checked by `qa`.

## Capabilities

- **Graphviz `dot`** is required for layout solving. `compile` finds it on PATH
  first, then the Windows default `C:\Program Files\Graphviz\bin`. When it is
  missing, `compile` still emits `skeleton.dot`, the element prompts, and the QA
  checklist — only `skeleton.svg` and the solver bboxes are skipped (degrade, not
  crash). Registered as capability `figure-layout`.
- **Inkscape CLI** is optional, used only for the final SVG→PDF export (embeds or
  outlines fonts; CairoSVG is not used for finals because it drops fonts). PATH
  first, then `C:\Program Files\Inkscape\bin`. Missing Inkscape degrades to an
  SVG deliverable, which is a valid handoff. Registered as capability
  `figure-pdf-export`.
- **codex CLI** is required for `gen` and `refine` (the `codex exec` gpt-image-2
  backend); the `CLAUDE*` environment variables are stripped before the call so
  headless invocation does not hang.

## Bookkeeping behavior

When a `.research-os/` project control directory is present, `gen` and `refine`
book a `K`-task for the generation batch and, on return, emit a `figure_gen`
event through `research_os.py` — the driver does the register-before-running
bookkeeping itself, so the model does not have to remember. Each batch is also one
attempt-tree node (A-class, under the figure's route). When `.research-os/` is
absent the tool runs in **standalone mode** and says so loudly (`bookkeeping
skipped`); assets are never lost, and a degraded return prints a warning rather
than failing. When a booked generation **fails** (nonzero exit, no PNG, timeout)
the `K`-task is closed as `failed` with a short error digest instead of hanging
claimed, and the failure is written to a `<would-be-asset>.png.failed.json`
sidecar; a bookkeeping hiccup during that close is itself only a loud warning.

## Relationship to `academic-figure-image-prompt`

For project figure work this doc and `figure_studio.py` **supersede** the
user-level `academic-figure-image-prompt` skill: that skill is a single-shot
prompt recipe, whereas this is the FSG-driven, compiled, keyed, and assembled
pipeline with bookkeeping. The older skill remains fine for ad-hoc single images
outside a research project.
