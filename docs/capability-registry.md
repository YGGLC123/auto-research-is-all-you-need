# Capability Registry (v2) — human companion to [capability-registry.json](capability-registry.json)

The JSON file is the contract; this page explains how to use it. **Never assume a tool exists because a skill mentions it** — that assumption is exactly what broke v1 (Zotero referenced for months, never enabled; Codex-only connectors cited on hosts that lack them).

## Protocol

1. **Probe at phase entry**: `probe --phase <phase> --write-state <root>` via the state CLI ([../scripts/research_os.py](../scripts/research_os.py)). The verdicts land in `state.capability_status`.
2. **Read the verdict**, not your memory: `available` (first-choice provider reachable), `degraded` (a fallback in the chain is reachable — use it and say so), `conversation_probe_required` (only the running session can tell — verify in-session before relying on it, e.g. MCP connectors, browser bridges), `missing` (nothing reachable).
3. **On `missing`, obey `on_missing.action`**:
   - `degrade` — use the registered fallback pattern and record the degradation in state; the paper trail must show which route produced which artifact.
   - `blocker` — raise a blocker (`type=capability`) with the queued action, continue other work. **Silent skipping is a contract violation.**
   - `stop` — the phase cannot proceed honestly (e.g. exact plots without a deterministic renderer); route to other open gates and surface the stop.

## Current capability map (summary; JSON is authoritative)

| id | need | claude-code first choice | codex first choice | on_missing |
|---|---|---|---|---|
| local-git | durable checkpoints | git CLI | git CLI | stop |
| python-env | control plane, plots | `py` → python3 → python | same | stop |
| web-evidence | current facts, primary sources | WebSearch/WebFetch | web | blocker |
| lit-orchestration | multi-paper intake, novelty audit | deep-research skill → parallel Explore agents + ledgers | $paper-lit-orchestrator | degrade |
| citation-library | BibTeX management | project-local BibTeX ledger | zotero:Zotero → local BibTeX | degrade |
| pro-consult | external high-reasoning model consult (theory, design red-team) | any high-reasoning model you can reach (verify the tier in-session) | same | blocker |
| playwright-pool | browser automation accelerator | playwright MCP pool | browser/computer-use | degrade |
| exact-plot | deterministic E2/G0 plots | matplotlib scripts + dataviz tokens | scientific-publication-plotter | **stop** |
| diagram-editable | editable vector diagrams | code-authored SVG/TikZ | drawio → engineering-figure-agent | degrade |
| imagegen-conceptual | G1/G2 illustration only | Figure Studio pipeline (codex exec gpt-image-2) → authored SVG | visualize/imagegen | degrade |
| figure-layout | Figure Studio graph layout | Graphviz dot (PATH → default install) | Graphviz dot | degrade |
| figure-pdf-export | SVG → PDF export | Inkscape CLI (PATH → default install) | Inkscape CLI | degrade |
| latex-toolchain | compile + render verification | latexmk → pdflatex | same | blocker |
| pdf-tools | PDF reading | Read tool (native) | pdf:pdf → pdftotext | degrade |
| overleaf-sync | Overleaf push/pull | overleaf-skill (git-bridge) | chrome UI | blocker |
| github | remote repo ops | gh (authenticated) | gh | degrade |
| drive-sync | Drive inputs/uploads | Google Drive MCP connector | rclone | blocker |
| review-panel | multi-agent cold review | review-paper / review-paper-light | local subagents | degrade |
| compute-server | >30 min or >8 workers | ssh to your own compute host (register first) | same | degrade |
| pro-refine | gated external-model manuscript refinement | any high-reasoning model you can reach (user-accepted) | same | degrade |
| prose-drafting | prose a human reads end to end (manuscript sections, reports, talk scripts) | the running model (or a writer model you register) | same | degrade |
| renderer.matplotlib | Route A chart rendering (every chart-template is a matplotlib function) | matplotlib (python package) | same | **blocker** |
| style.scienceplots | journal style stack for Route A | scienceplots (python package) | same | degrade |
| colormap.cmcrameri | perceptually uniform, colour-vision-safe colormaps | cmcrameri (python package) | same | degrade |
| renderer.vegalite | optional Vega-Lite render of an emitted spec | `vl-convert` CLI -> vl_convert package | same | degrade |
| component.bioicons | Route B component assembly from the local icon index | `{plugin}/library/components/bioicons` (index + readable assets) | same | degrade |
| dsl.plotneuralnet | neural-network architecture diagrams via LaTeX/TikZ (needs latex-toolchain) | PlotNeuralNet checkout | same | degrade |
| qa.scipilot | optional visual self-check merged into figure QA | scipilot-figure-skill | same | degrade |

## Standing rules

### Figure engineering (v2.9): what each route needs, and what happens when it is missing

The seven `renderer.*` / `style.*` / `colormap.*` / `component.*` / `dsl.*` / `qa.*` capabilities implement section 8 of the [figure engineering contract](figure-engineering-contract.md). They are probed per route, not all at once:

- **Route A (data -> chart)** needs `renderer.matplotlib`. It is the one **blocker** in the group: an exact numeric panel must never fall through to image generation, so a missing matplotlib queues the render as a capability blocker while other work continues. `style.scienceplots` and `colormap.cmcrameri` only degrade -- the plot still renders, in an approximate style or on the palette's hex list, and the degradation is recorded in the figure's provenance. `renderer.vegalite` degrades to writing `spec.vl.json` and stopping there.
- **Route B (component assembly)** prefers `dsl.plotneuralnet` for neural-network architectures and otherwise uses `component.bioicons`. PlotNeuralNet is unusable without `latex-toolchain` even when present, so probe both before routing an architecture figure to it.
- **QA** is mechanical and always runs; `qa.scipilot` only adds a rendered visual self-check on top. Everything it reports is advisory -- the light QA read-out never blocks.

Probe paths that start with `{plugin}/` resolve against this package's own root, so the bundled indexes are found however the plugin was installed (skills directory, marketplace cache, or a checkout).

**Probe mechanics.** `probe_provider` in the state CLI supports `always`, `command` (PATH, then `fallback_globs`), `path` / `skill-dir`, `python-import` and `conversation`. The four Python-package capabilities use `python-import`: the probe imports the module under the interpreter the CLI runs in, so any virtualenv, conda prefix or editable install that interpreter can see is found. If a verdict surprises you, confirm with `py -c "import matplotlib"` and fix the environment rather than the registry.

**The component index is a build artifact, not a vendored library.** `library/components/*/index.json` is tracked; the upstream clone under `repo/` and any manually imported `svg/` bodies are git-ignored. `py scripts/component_index.py index build --source bioicons` reclones (sparse: only `static/icons`) and rebuilds in one step. Licenses in the index are transcribed from upstream metadata and never inferred: a component whose license cannot be read is recorded `UNKNOWN`, which blocks shipping until it is resolved at the source URL.

### Prose drafting

Any deliverable whose value is the writing itself -- a manuscript section, a report, a briefing document, a talk script -- goes through the `prose-drafting` capability. By default the running model drafts in-session; a researcher who prefers another writer registers its CLI first in that chain. Either way, hand the writer the frozen facts (numbers with their sources, claims, scope limits, banned words) rather than a summary, and do not paraphrase its output back into your own voice. The running model still owns the decisions, the verification and every number.

- A browser bridge is a **transport**, not a model tier: when a consult goes through a chat UI, verify the selected model on every run. A Playwright pool is an optional accelerator, never a prerequisite.
- External-model consults and refinement always require the user's request or standing acceptance; degraded mode = independent local subagent channels, recorded as such.
- Exact numeric panels must never fall through to image generation, whatever is missing.
- Adding a capability = edit the JSON (id, phases, per-host provider chain with probes, on_missing) — `doctor` validates the schema; prose-only tool references in skills are forbidden.
