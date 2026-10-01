# workflows/ — orchestration templates for the mainline

Reference Workflow scripts the mainline **copies, renames, and edits** for a specific run.
Nothing here runs automatically; the plugin ships them as starting points so a fan-out is a
paste-and-fill, not a from-scratch design. Each template is a self-contained `.md` with the
Workflow script in a fenced block plus trimming notes.

## Which template

| Template | Use when |
|---|---|
| [review-fanout.md](review-fanout.md) | Cold/panel review: score N dimensions, then adversarially verify each finding. |
| [novelty-sweep.md](novelty-sweep.md) | Prior-art / collision search across venue·method·term·author lenses, loop-until-dry. |
| [claim-siege.md](claim-siege.md) | Harden one formal claim: FALSIFY → CONSTRUCT → VERIFY → RED_TEAM. |
| [migration-batch.md](migration-batch.md) | Mechanical same-shape change over many files, one isolated worktree each. |
| [role-fanout.md](role-fanout.md) | Dispatch 2–3 standing roles (chronicler / figure / evidence …) in one star-shaped round, then book every reply. |

## The mini-API these scripts assume

- `agent(prompt, opts?)` — spawn a **fresh-context** subagent; returns its structured result.
  - `opts.schema` — force a structured return (see below).
  - `opts.subagent_type` — dispatch a named agent: `research-refuter` (attack), `research-verifier`
    (independent check), `research-reproducer` (re-run), or a phase reviewer. These three judges are
    gate-triggered one-shots, never companion passes — see
    [../docs/verification-policy.md](../docs/verification-policy.md) before wiring one into a fan-out.
  - `opts.isolation: "worktree"` — give the agent its own git worktree (mechanical edits).
- `parallel(fns)` — run an array of thunks `() => agent(...)` concurrently; returns the results in order.
- `sh(cmd)` / `write(path, text)` — host escapes used by `role-fanout.md` only (a Bash call and a Write
  call on Claude Code): the CLI mints the envelope and books the receipt; a model never does either.
- `pipeline(seed, ...stages)` — sequential phases. **Stage 1 is mapped over `seed`** when `seed` is a
  collection (fan-out, results gathered); each later stage receives the previous stage's full output.

## args convention

Every template marks its knobs with `// EDIT ME`. Fill the ALL-CAPS placeholders (`DIMENSIONS`,
`LENSES`, `CLAIM`, `CAP`) and the angle-bracket prompt slots (`<claim>`, `<artifact>`, `<venue>`,
`<migration rule>`) before running. Keep ids project-root-relative (`research/claims/<id>/…`) so a
paste into any project resolves the same way.

## Relationship to context hygiene (non-negotiable)

These templates are the applied form of [context-hygiene.md](../docs/context-hygiene.md):

1. **Every `agent()` prompt carries a return line cap** — `<=30 lines` for retrieval/scan agents,
   `<=40 lines` for verdict agents. A fan-out prompt without a cap is malformed; the templates model
   the cap inline so you do not forget it when you edit.
2. **`schema` forces a decision-grade return.** The mainline receives typed rows (verdict, path,
   number), never prose paragraphs it would have to re-read and paraphrase.
3. **Full artifacts land on disk; the workflow returns only the filtered result** — confirmed
   weaknesses, real collisions, the siege verdict. Attacks, proofs, and diffs are written under
   `research/…` and referenced by path, so the mainline's window holds pointers, not bodies.
