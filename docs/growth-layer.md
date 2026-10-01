# Growth Layer — verified experience that compounds

The system gets better with use: effective playbooks, hard-won fixes, and reusable snippets sediment into a library that future runs retrieve. Every mechanism below is grounded in the 2026-07 community research (Voyager/AWM, ACE, DSPy-GEPA, SkillWeaver, Agent KB, Cursor Bugbot, Claude Code ecosystem survey).

## Five axioms

1. **Quality gate first** (DSPy/SkillWeaver/Bugbot): nothing enters retrieval without a verification signal — `tested` (executed proof, snippets must pass a selftest), `user-confirmed`, or `used-twice`. Ungated sedimentation is garbage accumulation; the community's gateless schemes all rotted.
2. **Full lifecycle** (Bugbot, the only complete prior): `candidate → trial → active → retired/superseded`, driven by helpful/harmful counters. Trial + 2 helpful uses ⇒ auto-active; harmful ≥ helpful ⇒ auto-retired. Only-in, never-out is library cancer.
3. **Itemized entries, deterministic merge, never an LLM rewrite** (ACE's collapse evidence: one wholesale rewrite shrank an 18k-token playbook to 122 tokens). `INDEX.md` is re-rendered by code; ids are stable; supersede, don't overwrite.
4. **Two granularities, three stores** (Agent KB): `playbooks/` (planning-level workflows), `fixes/` (symptom → diagnosis → fix → verify), `snippets/` (executable, selftest-gated — the strongest representation of experience is a program, per SkillWeaver).
5. **Hard injection budget**: `INDEX.md` ≤ 200 lines, render-enforced — over budget means merge or retire before anything new enters. Instruction-following collapses with volume; the budget keeps the library lean by force.

## Layout & CLI

```
library/                      # global tier: ships with the plugin, git-backed
├── INDEX.md  growth.json  playbooks/  fixes/  snippets/
<root>/.research-os/learned/  # project tier, same structure
```

`scripts/growth.py` (project tier via `--project <root>`):

- `propose --kind --title --phase … --content|--file --project-name --node --evidence` — provenance is mandatory (project + tree node/run id): unattributable experience is inadmissible.
- `adopt --id --gate tested|user-confirmed|used-twice [--selftest CMD]` — candidate → trial; snippets must pass their selftest to adopt.
- `use --id [--harmful] [--note] [--project-name --node]` — records an application; counters drive auto-transitions; applying a retired entry is refused.
- `promote --id --project <root>` — lifts a project entry to global; requires provenance/usage from ≥2 projects (`--force REASON` for genuine cross-cutting cases).
- `retire --id --reason` · `list [--status --kind --phase-filter]` · `render` (deterministic, budget-enforced).

## Sedimentation triggers

Propose at the moment of verification, not later: a tree node closing `success/proven` with a generalizable lesson; a blocker resolution whose fix transfers; a user correction. The **Reflector is separate** (ACE): at milestones/`loop_stopped`, a dedicated subagent audits the run — what deserves `propose`, which cited entries deserve `use --helpful/--harmful` — instead of the main loop sedimenting as a side effect.

## Retrieval — librarian-only

The main controller **never opens entry bodies** ([context-hygiene.md](context-hygiene.md)). It sends the research-librarian agent a ≤10-line brief; the librarian reads INDEX (both tiers), opens ≤5 bodies, returns ≤30 lines of distilled, directly executable guidance with entry ids. The caller then closes the loop with `use --id … [--harmful]` — retrieval precision is maintained by the lifecycle, not by search sophistication: entries whose citations keep failing retire themselves.

## Deliberately not built

No vector search (a single-user library at this scale doesn't need it — AWM ran at 0.9+ utility with whole-file injection); no gateless self-writing rules; no LLM library maintenance; no resident sleep-time consolidation (milestone-triggered reflection instead).
