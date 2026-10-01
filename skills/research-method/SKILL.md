---
name: research-method
description: Design, formalize, and audit the method, model, algorithm, objective, or system architecture of a research paper. Routed by /auto-research:pilot after a problem is selected and before or during implementation, especially when components, assumptions, interfaces, complexity, or novelty are not yet stable. Do not use for broad literature collection, experiment execution, or manuscript-only edits.
---

# Research Method Design

Turn the research thesis into an implementable and falsifiable mechanism.

```
CLI = py ../../scripts/research_os.py      (POSIX: python3 ../../scripts/research_os.py)
```

## Entry

`CLI probe --phase method --write-state <root>` first. Obey each verdict's `on_missing` policy per [../../docs/capability-registry.md](../../docs/capability-registry.md): `degrade` → use the registered fallback and record the degradation in state; `blocker` → `CLI blocker <root> add --type capability …` with a `queued_action` and continue other work. Never assume a tool exists because prose mentions it; never silently skip.

**Register before running** ([attempt-tree.md](../../docs/attempt-tree.md)): every bounded try this phase makes — a search strategy, a proof route, a protocol variant, a rescue plan — is a coded node in the attempt tree. `CLI tree <root> register --kind attempt --parent <route> --title "…"` before executing, `close --status … --result "…"` at the verdict; run `CLI tree <root> list --under <route>` first so a dead sibling is never re-run.

| capability | claude-code first choice | codex first choice |
|---|---|---|
| pro-consult (optional design red-team) | any high-reasoning model you can reach (verify the tier in-session) | same |
| diagram-editable (via artifacts skill) | code-authored SVG/TikZ | drawio → engineering-figure-agent |

## Freeze interfaces before modules

Specify inputs, outputs, observation time, prediction or intervention time, available information, target, training signal, and deployment contract. For domain applications, make entity identity, missingness, revisions, and label timing first-class. For systems work, define workload and service boundaries. For theoretical work, align notation with the frozen claims from /auto-research:research-theory-siege.

## Build the method contract

Write `research/method-spec.md` with:

- mathematical or algorithmic data flow;
- objective and regularizers, with units and normalization;
- assumptions, invariances, constraints, and identifiability scope;
- module responsibilities and interfaces;
- computational and memory complexity;
- optimization and inference procedures;
- failure modes, degeneracies, and fallback behavior;
- claim-to-component map and test hooks.

For each nontrivial component, state what failure it prevents, why a simpler alternative is insufficient, and which ablation or theorem can test that necessity. Remove decorative modules.

**When the spec freezes**: `CLI asset <root> add --path research/method-spec.md --role spec --tag spec --version-label v<N>` and `CLI update <root> --version method-spec=v<N>` — freezing and recording are the same action. Before any destructive rewrite of a frozen spec: `CLI snapshot <root> --label pre-method-spec-v<N+1>`; re-registering the same asset id requires `--supersede`, never deletion.

## Adapt by paper type

- Theoretical ML: derive the algorithm from the formal object and expose every assumption used by theorems.
- Empirical ML: control capacity, tuning budget, train-test information flow, and baseline parity.
- Domain application: preserve domain semantics and compare against domain-native mechanisms.
- Systems or tooling: specify APIs, fault boundaries, concurrency, state, resource budgets, and observability.
- Survey or position: treat the taxonomy and inclusion rule as the method; make coding reliability auditable.

## Attack the design

Check non-identifiability, trivial solutions, discontinuities, scaling, causality overclaim, optimization mismatch, information leakage, invalid approximations, and whether the implementation will match the equations. Route genuinely formal gaps to /auto-research:research-theory-siege. Route feasibility spikes or measurement questions to /auto-research:research-experiments.

## Subagent playbook

Fan out when auditing is separable from designing: one subagent per attack axis (identifiability, leakage, complexity accounting, equation-vs-implementation match), or an independent "naive implementer" pass that must rebuild the method from `method-spec.md` alone. Every prompt must carry: the frozen spec version and exact path to `research/method-spec.md`, the single attack axis in scope, the expected artifact (a findings list with file:line or equation references) and return format, and a stop condition ("report the first two load-bearing gaps, do not redesign"). Merge findings as evidence, never confidence — a subagent saying "looks sound" is not a necessity argument. Independent cold audit is a **gate-point** spend, not a companion pass ([../../docs/verification-policy.md](../../docs/verification-policy.md)): send a finished spec to the research-verifier agent once, when it is frozen for the exit gate, with fresh context — the spec and the claim map only, no design rationale attached. Drafts in progress are audited by these in-house attack axes, not by a judge agent.

## Exit gate

Finish when an independent implementer could build the method without inventing semantics, every claimed contribution has a necessity argument and verification hook, and known limits are explicit. Hand off a versioned method spec, never a prose-only architecture sketch:

```
CLI update <root> --gate "method_spec=passed:frozen v<N>@research-method" --clear-next --next "Implement vertical slice@research-experiments"
CLI event <root> --type phase_advanced --summary "method-spec v<N> frozen" --artifact research/method-spec.md
```

When a method/architecture figure is needed, this skill owns the exact module, interface, state, and arrow semantics; route visual contract compilation and production to /auto-research:research-artifacts.
