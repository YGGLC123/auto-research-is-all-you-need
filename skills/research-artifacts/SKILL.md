---
name: research-artifacts
description: Plan, design, produce, and cold-review publication visuals through a template-first figure Router (register every figure before drawing it; Route A data templates, Route B component assembly, Route C generative elements) on top of a two-stage visual-program workflow. Use when the auto-research pilot routes a paper, revision, or research program to figure/table/diagram work; when a manuscript needs a coherent paper-wide visual system; when an architecture or multi-part figure must be delivered as layered reusable components for human recomposition; or when an individual plot, theory figure, workflow, hypergraph, timeline, qualitative panel, graphical abstract, or table must be redesigned. Phase 1 writes and freezes a source-grounded Stage-2 prompt; Phase 2 executes it with type-specific aesthetics, exact/editable/composition-ready/generative tool routing, rendered QA, and manuscript integration.
---

# Research Visual Director

Treat figures as a claim-evidence interface, not decoration. Use the smallest amount of visual complexity that makes the paper easier to understand or verify.

```
CLI = py ../../scripts/research_os.py      (Windows; POSIX: python3 ../../scripts/research_os.py)
FIG = py ../../scripts/figure_router.py    (the Router + Quick Figure; v2.9)
CMP = py ../../scripts/component_index.py  (Route B component index and SVG assembly)
VC  = py scripts/visual_contract.py        (skill-local script; POSIX: python3 scripts/visual_contract.py)
```

## Router first — register before you draw

**`FIG quick --register` is the mandatory first step for every figure**: before a plot
script, before a diagram editor, before any generation. **The registry mints the `F-1NN` id
— you never propose one** (`--figure-id` is accepted only with `--import`, for adopting a
figure that already exists outside the registry). Registration decides the route by rule and
books the instance in `.research-os/figures/registry.json`. A figure that was never
registered has no provenance, no QA record, and no way to reach the argument graph.
Contract: [figure-engineering-contract.md](../../docs/figure-engineering-contract.md).

**"我要一张图" is a whole request — the four steps are yours.** The user says "这里要张图" ·
"把这个结果画出来" · "架构图重做一版,现在看不懂" · "make this a figure instead of a table",
and that is complete. They are never asked for a route, a figure id, a claim id, a template, a
venue, or a fidelity class; you fill those from the manuscript, the claim-evidence map and
state, then run **register → produce → read the QA → promote** yourself ([pilot](../pilot/SKILL.md),
*How the user experiences this*, rule 1). Ask back only where the answer would actually change
the figure — what the reader should take from it, or which of two framings they want — as one
question with a recommendation, never as a form. What returns to the user is the render and one
line about what it shows and whether it is the version that ships; `F-1NN`, `render_id`, the
route verdict, `qa.json`, and the `expressed_as` edge stay on your side (rules 2 and 5). A
degraded route, a missing renderer or a licence that blocks promotion is reported as its
consequence and your workaround — "这张暂时只能出草图,矢量那条链缺件,要不要我先用别的方式出" —
not as a probe verdict or an error code (rule 6).

```bash
FIG quick --register --intent "<the reader question this figure answers>" \
    --claim C-057 --section 4.3 \
    --data research/results/thresholds.csv --columns "x=k_eff,y=t,lo=t_lo,hi=t_hi" \
    --semantic "threshold-curve" --semantic-optional "reference-line" \
    --venue rfs --width single
FIG quick --render          F-101 [--emit-vegalite]          # Route A -> renders/<R>/
FIG quick --assemble        F-101 [--slots A=bioicons:<id> B=…]   # Route B
FIG quick --promote-current F-101 [--render-id R-…]          # full QA, then the graph derives
FIG quick --promote-to-fsg  F-101                            # Route C: seed figure-studio
FIG quick --reroute         F-101 [--template lib:figure:event-study@1]   # re-pin the selection
FIG quick --rebase          F-101 --to lib:figure:event-study@1           # explicit upgrade
FIG route --request <request.json> --verbose                  # the verdict and its reasons
FIG list | FIG venue list|show <id> | FIG library items|show <id>|validate
```

**Three routes, decided by rule, never by taste.** `FIG route` prints the reasons and the
scored candidates; `--route data|components|concept` overrides the verdict and the override
is recorded with it.

| Route | Test | Backend | Delivers |
|---|---|---|---|
| **A — data** | `data_refs` present **and** some chart-template's required roles all match by name and type | matplotlib templates (`figure_templates.py`) | pdf + svg + png, `qa.json`, `provenance.json`, optional `spec.vl.json` |
| **B — components** | the semantic objects are a subset of a diagram-template's tags, or ≥60% covered by the component index | `CMP assemble` into the template slots | svg with code-drawn connectors, live vector text, `attribution.md` |
| **C — concept** | neither holds | Figure Studio (FSG), below | FSG program, generated elements, assembled figure |

**Only Route C ever reaches an image model.** Exact numeric evidence is Route A by
construction, and a Route A figure that cannot render is a blocker, not a fallback to
generation.

**Venue profiles live in the library, not in prose.** `FIG venue list` — nature, science,
ieee, rfs, jf, aaai, neurips, generic — fixes column widths, font size, palette, panel-label
style, dpi and export formats. Re-render the same data for a different venue and the
`data_hash` holds while the `render_hash` moves; that pair is the audit trail.

**Search the library before building.** `FIG library items` lists the registered
chart/diagram templates, palettes and style profiles with their semantic tags. For sedimented
experience about *how* a figure went — not what exists — dispatch the **research-librarian**
agent with a ≤10-line brief and take its ≤30-line digest; never read library bodies inline
([context-hygiene.md](../../docs/context-hygiene.md)). Templates are pinned by version **and
by content hash** (`selected_item = lib:figure:forest-ci@1`, `item_hash`); a library update
never re-decides a registered request. Moving the selection is an explicit `--reroute` (re-run
the Router) or `--rebase` (name the target), never a silent upgrade, and either one demands a
re-render.

**Renders are immutable.** Each render lands in `renders/<render_id>/`, where `render_id` is
the hash of `item_hash | venue_hash | params_hash | data_hash | capability_state` — the same
inputs return to the same directory, different inputs get a new one, and an old render is
never overwritten. `current_render` in the registry is only a pointer. All three products
(pdf, svg, png) are always written; `venue.export[]` decides only what is *published*.

**QA is mechanical and reports, never blocks — with one exception.** Every render writes
`qa.json`: artifacts exist and are non-empty, width matches the venue, **PDF fonts embedded**,
**SVG text preserved** (still `<text>` with a declared font, or deliberately converted to
paths) and inside the canvas, missing data cells, component licences. `--render` runs the
light pass; `--promote-current` runs the full pass and adds claim-binding and caption-intent.
You read the findings and decide. The exception is licensing: **a component whose licence
nobody transcribed blocks `--promote-current`** (a draft may still render with it).

**Promotion does not book the edge — it makes the fact the graph derives from.**
`--promote-current` flips the instance to `current`, supersedes the previous `current` for the
same claims, and then — in-process, after its own transaction has succeeded — calls
`research_graph.derive(rules=["fig-registry"])`. The `expressed_as` edge (figure → narrative
claim) is produced by the graph's own rule, `by=derive`, and `graph/current.json` is
re-projected in the same locked transaction. So by the time the command returns,
`graph view coverage` already no longer lists that claim as unexpressed, and the edge has
exactly one owner and one place it can be retracted. The figure track never writes an edge
itself.

**Nothing is left for you to run afterwards.** `--rebase` and `--reroute` trigger the same rule;
so do the other tracks' CLIs for their own rules. The `graph{derived, added, retracted}` block in
the command's receipt is the report — read it instead of re-running `derive`. A derivation that
failed does **not** fail the promotion: it lands one `GRAPH_DERIVE_FAILED` line in
`.research-os/graph/incidents.log`, which is the only case worth acting on (and the only reason
to touch `derive` by hand). If a promoted claim is still in coverage list A, the usual cause is a
parked edge whose narrative node did not exist yet — `graph link --replay-pending`, not a
re-promotion.

**The figure engineer proposes; you register.** Its `proposed_figure_requests[]` land in one
call: `FIG quick --register --from-reply <reply.json>` ([role-fleet.md](../../docs/role-fleet.md) §2).
The two-stage visual contract below still governs a paper-wide visual program; the Router
governs each individual figure inside it.

## Entry

Before any visual work: `CLI probe --phase artifacts --write-state <root>`. Read the verdicts in `state.capability_status` and obey each capability's `on_missing` policy from [../../docs/capability-registry.md](../../docs/capability-registry.md) — degrade loudly and record it, raise a blocker (`CLI blocker <root> add`) and continue other figures, or stop that route. Silent skipping is a contract violation. **When `exact-plot` is missing the route is `stop`: exact numeric panels must never fall through to image generation, whatever is missing.** Park the affected figures visibly and surface the stop; conceptual-only work may continue on degraded routes.

**Register before running** ([attempt-tree.md](../../docs/attempt-tree.md)): every bounded try this phase makes is a coded node in the attempt tree. `CLI tree <root> register --kind attempt --parent <route> --title "…"` before executing, `close --status … --result "…"` at the verdict; run `CLI tree <root> list --under <route>` first so a dead sibling is never re-run.

Dependency note: `pypdf` (declared in [../../requirements.txt](../../requirements.txt)) is needed only by `VC validate-delivery` (PDF payload checks) and `VC self-test`. When it is absent those commands report an explicit degraded mode; `init`, `validate-request`, and `freeze` run without it.

## Choose the scope

- For a paper-wide redesign, rejected-paper rescue, new visual language, or several heterogeneous figures, run both phases. Never start rendering before Phase 1 freezes the visual program.
- For one artifact, Phase 2 may start only when a compact Phase 1 has already produced a valid frozen request with matching hashes. If the brief is explicit but not frozen, compile, validate, and freeze it first.
- For final-submission formatting only, preserve scientific content and run the applicable production and quality gates without reopening the program unless a fatal visual contradiction appears.

## Phase 1 — direct the visual program

Read [phase-1-visual-program.md](references/phase-1-visual-program.md). Inspect the manuscript, claim-evidence map, method/results artifacts, current figures and captions, reviewer feedback, venue constraints, and existing visual conventions. Diagnose missing, redundant, overloaded, or scientifically misleading figures.

Read [figure-type-playbooks.md](references/figure-type-playbooks.md) and [tool-routing.md](references/tool-routing.md) only for types and routes present in the paper. Use [stage-2-master-prompt.md](references/stage-2-master-prompt.md) as the prompt compiler contract; customize it into a self-contained project-local prompt rather than forwarding a generic template.

When the user needs nested groups, reusable modules, one-level ungrouping, a component board, or manual recomposition, read [composition-ready.md](references/composition-ready.md) and assign `D3 composition-ready`. Do not confuse paper panels with reusable components.

Write under `research/artifacts/visual-program/` unless the project already has a documented artifact home:

- `figure-program.json`: claim, role, type, fidelity, sources, route, placement, formats, risks, and status for every planned artifact;
- `stage-2-prompt.md`: the frozen, self-contained instruction for Phase 2;
- optional `visual-gap-audit.md`: keep only when the diagnosis itself is a durable research decision.

Use `VC init` to scaffold these files when useful, then fill them from evidence. Run `validate-request`, then `freeze`; the freeze command binds the prompt and input hashes. Phase 1 may create rough wireframes only when needed to test information architecture; it must not smuggle an unreviewed draft into the final paper.

Run from this skill's directory (or give the full path to `scripts/visual_contract.py`); on POSIX replace `py` with `python3`:

```bash
py scripts/visual_contract.py init <project-root> --request-id <kebab-id> --paper-title "<title>" --stage <stage> --paper-type <type> --venue "<venue>"
py scripts/visual_contract.py validate-request <figure-program.json> --check-paths
py scripts/visual_contract.py freeze <figure-program.json>
py scripts/visual_contract.py validate-delivery <figure-program.json> <delivery-manifest.json> --require-integration
py scripts/visual_contract.py self-test
```

## Phase 2 — answer the frozen prompt

Run Phase 2 in a fresh agent (see the Subagent playbook). Read the frozen prompt, `figure-program.json`, and only the source artifacts named there. Do not rely on hidden conversation memory.

1. Recheck claim/source consistency. If the prompt requests unsupported content, stop that figure and return a decision record to Phase 1; do not silently beautify the error.
2. Define `visual-system.md`: semantic colors, typography, spacing, shape grammar, line/arrow semantics, panel labels, legend behavior, and cross-figure consistency. Use [visual-system-tokens.md](references/visual-system-tokens.md) as fallbacks, not a fixed skin.
3. Write one spec per artifact under `figure-specs/`. Make the reader question, three-second takeaway, thirty-second reading path, panel logic, caption job, route, and verification tests explicit. For D3, also freeze the assembly/component/primitive hierarchy and top-level reusable modules.
4. Produce through the selected capability route. Keep exact data panels deterministic, editable diagrams editable, and generative work confined to illustrative or conceptual content.
5. Save editable/reproducible sources, primary vector or venue-required exports, lightweight previews, captions, prompts when applicable, and a delivery manifest that serves as the build/provenance index linking inputs to outputs. D3 additionally requires a component board when planned, a structure snapshot, composition manifest, and a real edit/reassembly QA report.
6. When the artifact is promoted to `current` — the version headed for the manuscript — run the semantic and visual cold reviews in [quality-gates.md](references/quality-gates.md), then render the manuscript at final placement size and verify integration. Intermediate iterations stop at the producer's own gate 0-7 pass; the full QA sweep is spent once, on the version that ships ([../../docs/verification-policy.md](../../docs/verification-policy.md)).

Any Phase-2 change to a frozen claim, figure role, fidelity class, or source path requires an amendment note and a new prompt hash. Pixel-level layout iteration does not.

## Route by fidelity, not convenience

Routes are chosen by the E/D/G contract in [tool-routing.md](references/tool-routing.md) and produced through **capability IDs**, never prose-assumed tools. Probe verdicts decide the provider on the current host ([../../docs/capability-registry.md](../../docs/capability-registry.md)):

| Capability | claude-code first choice | codex first choice | on_missing |
|---|---|---|---|
| `exact-plot` | matplotlib scripts + dataviz tokens | scientific-publication-plotter | **stop** |
| `diagram-editable` | code-authored SVG/TikZ | drawio → engineering-figure-agent | degrade |
| `imagegen-conceptual` | authored SVG (no imagegen) | visualize/imagegen | degrade |
| `latex-toolchain` | latexmk → pdflatex | same | blocker |

- Exact numeric evidence (E2/G0): `exact-plot` only; never image generation.
- Exact formal topology, dependency, or state: deterministic graph/TikZ/SVG code when feasible; controlled diagram editors for layout, not invented structure.
- Architecture, workflow, and method diagrams: `diagram-editable`; require editable vector handoff, and use D3 when modules must be freely recomposed.
- Conceptual mechanism or graphical abstract (E0 only): `imagegen-conceptual`; reconstruct technical labels and geometry when raster generation is used. When this route is live, produce it through the Figure Studio element pipeline (below), not a single whole-figure generation.
- Mixed panels: render exact panels first, assemble without redrawing their geometry, then add conceptual panels.
- Tables: LaTeX or spreadsheet/dataframe tooling; never raster generation.

Route selection now *starts* at `FIG route`: the E/D/G fidelity contract decides what a
figure is allowed to be, and the Router decides which backend may build it. When the two
disagree, the stricter one wins — a fidelity class is never relaxed to reach an easier route.

[tool-routing.md](references/tool-routing.md) remains the detailed route matrix (E/D/G axes, hard constraints, per-tool capability notes, fallback rules). The specific specialist skill names it mentions (scientific-publication-plotter, engineering-figure-agent, Figma routes, presentations:Presentations) are Codex-side expert detail; whether any of them actually exists is decided by the capability-registry probe verdict on the current host, not by their appearance in prose. A fallback must preserve the same E/D/G contract; if no available route can, mark the artifact blocked — do not silently relax the contract.

## Figure Studio — the Route C backend (generative element pipeline)

Figure Studio is reached **only through Route C**: the Router has already established
that no chart-template can derive the geometry from data and no registered component or
diagram-template covers the semantics. Seed it from the registered instance
(`FIG quick --promote-to-fsg F-1NN` writes `seed.fsg.json` into the artifact directory
and records `handoff.to = figure-studio` in the registry) so the figure keeps one id, one
claim binding and one provenance trail across the handoff. Inside FSG nothing changes.

When a planned artifact routes to `imagegen-conceptual` — or a D-class diagram
that wants generative elements — do **not** ask an image model for a whole figure.
Production goes through the skill-local Figure Studio tool, run like `VC`:

```
FSG = py scripts/figure_studio.py     (Windows; POSIX: python3 scripts/figure_studio.py)
```

It builds the figure from **isolated, textless elements composited by code** —
whole-panel generation reaches publication grade ~24% of the time vs ~80% for
element compositing. The order is: `fsg init` (author the Figure Scene Graph) →
`validate` → `freeze` → `compile` (FSG → SVG skeleton + per-element prompts + QA
checklist) → `gen` (low-effort courier driver) → `refine` **only when QA flags**
(bounded high-effort critic, ≤2 edit iterations, each a new generation, never
overwrite) → `assemble` → `qa`. Full operating doc:
[figure-studio.md](../../docs/figure-studio.md).

The **four iron rules** are non-negotiable: (1) edges are drawn by code, the model
paints only nodes; (2) exact numeric panels go through matplotlib `exact-plot`,
never image generation; (3) text lives in the vector layer only — a generation may
carry a magenta placeholder bar and nothing else; (4) the skeleton is never shown
to the image model as an image (it is compiled to a prose prompt).

`F##`-code discipline and register-before-running still apply: every generation
batch is one attempt-tree node under the figure's route, and the CLI books the
`K`-task and emits the `figure_gen` event itself when `.research-os/` is present —
you author the FSG and read the verdicts.

## Subagent playbook

- **Phase 2 executor — fresh agent, always.** Hand it only: the frozen `stage-2-prompt.md`, `figure-program.json`, and the exact source files named there. It must not use session memory or unfrozen conversation context; anything not in the frozen inputs is out of bounds. It returns produced sources/exports, the delivery manifest, and decision records for any stopped figure.
- **Semantic cold review** and **visual cold review** — spent **only on the version promoted to `current`**, one independent subagent each (the `research-verifier` agent in [../../agents/research-verifier.md](../../agents/research-verifier.md) fits), per [quality-gates.md](references/quality-gates.md). Neither reviewer may be the producer, and the two reviews stay separate. Draft renders, refine loops, and rejected candidates are not cold-reviewed — generate, record, continue.
- **Recycle discipline**: after any subagent returns, verify file existence and content pairing yourself (spec ↔ source ↔ export ↔ caption ↔ review) before trusting the summary; a clean report over missing files is a known failure mode.

## Maintain auto-research state

The visual gate objects `visual_plan`, `visual_delivery`, and `visual_integration` are part of the v2 state schema ([../../docs/state-contract.md](../../docs/state-contract.md)); the validator enforces their shape. They store pointers and hashes, not payloads:

- `visual_plan`: request id/version, owner phase, program/prompt/freeze paths and hashes, and `draft | frozen | stale | superseded | waived`;
- `visual_delivery`: the same request id/version/prompt hash, manifest and delivery-lock paths/hashes, and `blocked | in_progress | verified | failed | superseded`;
- `visual_integration`: manuscript/render paths and `pending | verified | failed`.

Write them through the dedicated setter — never by hand:
`CLI update <root> --visual 'visual_delivery={"request_id":"…","status":"verified","manifest":"…","manifest_sha256":"…"}'` — then record the transition with `CLI event`.

Freeze planned/removed scope in `figure-program.json`; keep mutable production and semantic/visual review status in `delivery-manifest.json`, not as dozens of state gates. Keep at most three immediate next actions. Append one event after a phase freeze, amendment/stale decision, verified delivery, or integration pass.

Once `VC validate-delivery … --require-integration` passes, index the manifest in the ammo depot:

```bash
py ../../scripts/research_os.py asset <root> add --path research/artifacts/visual-program/delivery-manifest.json \
   --role delivery-index --tag figure --tag delivery --produced-by research-artifacts --hash
```

## Exit gate

Read [quality-gates.md](references/quality-gates.md). Finish only when every retained artifact has a necessary manuscript role, traceable inputs, correct type and fidelity class, editable or reproducible source, verified final-size export, accessible encoding, self-contained caption, independent semantic and visual review, and correct placement in the rendered manuscript.

Do not accept an image-model quality score, a visually attractive preview, or successful file export as proof that a figure is scientifically correct or publication-ready.

Then write the gate and route onward:

```bash
py ../../scripts/research_os.py update <root> --gate "visual_delivery=passed:delivery-manifest + lock verified@research-artifacts" \
   --clear-next --next "Integrate figures at final size@research-manuscript"
py ../../scripts/research_os.py event <root> --type visual_delivery_verified --summary "…" --artifact research/artifacts/visual-program/delivery-manifest.json
```

Exit only to a named next skill or a blocker. If delivery validation failed, the gate is `visual_delivery=failed` with the failure evidence, and the pilot loops back here.


## Prose drafting

When the deliverable **is** the writing -- a manuscript section, a report, a handoff document, a talk
script, a cover letter -- it goes through the `prose-drafting` capability: the running model by default,
or the writer model the researcher has registered there. The division of labour holds either way: the
running model keeps every decision, every number, every verification, and the discipline scan. Give a
delegated writer the frozen facts rather than a summary of them -- numbers with their sources, scope
limits, banned words, audience, length -- and check what comes back for facts and discipline instead of
rewriting it into your own voice.
