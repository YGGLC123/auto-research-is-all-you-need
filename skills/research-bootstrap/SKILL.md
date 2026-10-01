---
name: research-bootstrap
description: Intake and bootstrap a durable research project without forcing unrelated services. Use when explicitly routed by /auto-research:pilot to create or repair project structure, classify stage and paper type, initialize compact resumable state, establish local version control, and write the initial research brief. Do not use for literature review, method invention, experiments, or manuscript polishing.
---

# Research Bootstrap

Create the smallest durable control plane that makes the project safe to resume.

```
CLI = py ../../scripts/research_os.py      (POSIX: python3 ../../scripts/research_os.py)
```

## Entry

Probe first: `CLI probe --phase bootstrap` (add `--write-state <root>` only once state exists; re-run with it right after bootstrap so verdicts persist). Obey each verdict's `on_missing` policy from [../../docs/capability-registry.md](../../docs/capability-registry.md) — both bootstrap capabilities are `stop`: without them the phase cannot proceed honestly. Degradations must land in state; anything needing permission or a user decision becomes `CLI blocker add`, never a silent skip.

**Register before running** ([attempt-tree.md](../../docs/attempt-tree.md)): every bounded try this phase makes — a search strategy, a proof route, a protocol variant, a rescue plan — is a coded node in the attempt tree. `CLI tree <root> register --kind attempt --parent <route> --title "…"` before executing, `close --status … --result "…"` at the verdict; run `CLI tree <root> list --under <route>` first so a dead sibling is never re-run.

| capability | claude-code first choice | codex first choice |
|---|---|---|
| local-git | git CLI | git CLI |
| python-env | `py` → python3 → python | same |

## Intake

Inspect the requested root before writing. Preserve existing structure and user changes. Establish:

- working title and one-sentence objective;
- current stage and paper type, with confidence and artifact evidence;
- target venue or decision horizon if known;
- available inputs: idea, papers, data, code, results, draft, reviews;
- constraints: licenses, privacy, compute, deadline, collaborators;
- the highest-risk unknown and the next gate.

Infer what is clear. Ask only for a missing choice that would materially change project scope or create external exposure.

## Bootstrap safely

```
CLI bootstrap <root> --title "<working title>" [--stage <stage>] [--paper-type <type>] [--layout core|standard] [--init-git]
```

**`--title` is required** — the CLI rejects the call without it; never invoke bootstrap titleless. Use `--layout core` for an idea or document-only project and `--layout standard` when code and experiments are in scope. Use `--init-git` for a newly requested durable project unless it is already inside a repository.

The command is idempotent and does not overwrite existing state. If the root carries a v1-family state (`research-pipeline-os/v1`, `auto-research-v1/v1`), run `CLI migrate <root>` (writes `state.v1.bak.json` automatically) — never delete state to force a clean start. Before repairing or migrating any pre-existing state, take `CLI snapshot <root> --label pre-migrate`.

Write a concise `research/intake.md` containing the objective, non-goals, present evidence, constraints, active gate, and next actions. Do not fill the repository with speculative templates. Register it: `CLI asset add <root> --path research/intake.md --role brief --tag intake --produced-by research-bootstrap`.

## Version locally

Inspect `git status` before and after changes. Validate state with `CLI validate <root>`. Stage only paths created or changed for bootstrap and create a local commit after validation. Do not create a GitHub repository, push, configure Drive, or open any login flow during bootstrap unless the user separately put that external action in scope; when such an action is wanted later, park it visibly: `CLI blocker add <root> --type permission --description "<what>" --queued-action "<how to execute once approved>"`.

## Subagent playbook

Fan out only when the intake surface is too large to classify in one pass — a pre-existing repo with many draft/result/code areas. Launch read-only Explore agents, one per area, in parallel. Every subagent prompt must carry: (1) the frozen question ("what stage/paper-type evidence does this area contain for project <root>?"), (2) the exact absolute paths it may read, (3) the expected product and return format (bulleted list: artifact path → what it implies for stage/paper_type, plus a confidence tag), (4) the stop condition (report `UNKNOWN` instead of guessing; do not read outside the named paths). Merge their evidence, not their confidence — the stage call is made here, from artifacts. If a state repair is contested, have the research-verifier agent (Claude side; Codex side = a fresh session) re-derive stage and paper type from the same file list before you write anything.

## Exit gate

Finish when the project has valid v2 state, a usable intake brief, a classified stage and paper type, a selected next phase, and local recovery instructions. Write it back structurally:

```
CLI update <root> --gate "bootstrap=passed:intake brief + validated state@research-bootstrap" \
   --set active_skill=research-bootstrap --clear-next --next "<next step>@<owner_skill>"
CLI event <root> --type phase_advanced --summary "bootstrap complete; routed to <owner_skill>" --artifact research/intake.md
```

Route to exactly one next skill by fully qualified name: `/auto-research:research-discovery` for an idea-only project, `/auto-research:research-evidence` when the highest risk is prior work or data provenance. Unresolved external authorizations stay in the blocker queue with a `queued_action` — visible, not blocking the bootstrap gate.
