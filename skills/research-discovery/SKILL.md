---
name: research-discovery
description: Convert a topic, preliminary evidence, failed submission direction, or application opportunity into a falsifiable and differentiated research problem. Use when explicitly routed by /auto-research:pilot for early-stage problem discovery, contribution design, cross-domain transfer, feasibility triage, or choosing among candidate directions. Do not use as a substitute for verified literature review or detailed method implementation.
---

# Research Discovery

Design a problem that can survive both novelty and falsification.

```
CLI = py ../../scripts/research_os.py      (POSIX: python3 ../../scripts/research_os.py)
```

## Entry

`CLI probe --phase discovery --write-state <root>` before promising anything. Obey each verdict's `on_missing` policy from [../../docs/capability-registry.md](../../docs/capability-registry.md); record degradations in state and raise `CLI blocker add` for anything needing permission or a user decision — silent skipping is a contract violation.

**Register before running** ([attempt-tree.md](../../docs/attempt-tree.md)): every bounded try this phase makes — a search strategy, a proof route, a protocol variant, a rescue plan — is a coded node in the attempt tree. `CLI tree <root> register --kind attempt --parent <route> --title "…"` before executing, `close --status … --result "…"` at the verdict; run `CLI tree <root> list --under <route>` first so a dead sibling is never re-run.

| capability | claude-code first choice | codex first choice | on_missing |
|---|---|---|---|
| web-evidence | WebSearch/WebFetch | web | blocker |

## Start from evidence

Read the intake, nearest-prior matrix, data constraints, and any reviewer feedback. If a central prior or dataset fact is unknown, route that bounded question to `/auto-research:research-evidence`; do not invent around it. Recollection without a checked source is `UNVERIFIED_RECOLLECTION` and cannot anchor a card.

## Generate problem cards

For each serious candidate, specify:

- observation and decision setting;
- target quantity, intervention, or theorem;
- stakeholder or scientific value;
- closest-prior gap on explicit axes;
- proposed mechanism, not merely an architecture name;
- available evidence and minimum viable test;
- strongest plausible claim and what would refute it;
- fatal risks, ethical or licensing constraints, and kill criteria.

Generate a small diverse set. Vary the source of contribution: new problem or data regime, formal principle, method, evaluation protocol, or domain discovery. Do not count multiple modules of one architecture as independent contributions. Score each card on significance, novelty defensibility, falsifiability, data and compute feasibility, theoretical depth, evaluation credibility, and target-venue fit.

## Subagent playbook

Stress-testing the top cards is the fan-out core of this phase: attack each card with independent, bounded subagents — one attack, one agent, run in parallel. The six standing attacks:

1. an existing-method-plus-tuning explanation;
2. a simpler baseline explanation;
3. a leakage or label-validity failure;
4. a non-identifiability or impossibility case;
5. a domain-value objection;
6. a reviewer summary explaining why it is only an application paper.

Every attack prompt must carry: (1) the frozen card — its id and the exact claim text, immutable during the attack; (2) precise file paths to the card deck and any evidence ledgers it may read; (3) the expected product and return format (verdict `KILLS | WOUNDS | SURVIVES`, the concrete counter-scenario or prior, and what evidence would settle it); (4) the falsification/stop condition (what finding ends the attack; return `INCONCLUSIVE` with the missing fact rather than padding). Merge their evidence, never their confidence — one grounded kill outweighs five vague survivals. When a survived attack is load-bearing for the frozen thesis, send it once more to the research-verifier agent for a fresh-context cold pass (Claude side; Codex side = a new session with only the card and the attack transcript paths).

## Select and freeze the thesis

Choose one primary thesis, one fallback, and explicit non-goals. Write a contribution contract linking each proposed claim to required evidence, theory, experiment, and artifact. For formal unknowns, create claim IDs and route them to `/auto-research:research-theory-siege`. For an engineering unknown, route to `/auto-research:research-method` or `/auto-research:research-experiments`.

Anti-amnesia: register the card deck and contract — `CLI asset add <root> --path research/problem-cards.md --role cards --tag discovery --produced-by research-discovery` (same for the contribution contract); stamp the freeze `CLI update <root> --version thesis=v1`. Re-freezing a previously frozen thesis is a destructive rewrite: `CLI snapshot <root> --label pre-refreeze` first, then bump the version.

## Exit gate

Finish when one problem has a falsifiable statement, defensible gap, credible data or proof route, kill criteria, and a concrete next gate. Record rejected candidates and why so they are not rediscovered later.

```
CLI update <root> --gate "problem_frozen=passed:thesis v1 + contribution contract@research-discovery" \
   --set active_skill=research-discovery --clear-next --next "<next step>@<owner_skill>"
CLI event <root> --type phase_advanced --summary "problem frozen; rejected candidates recorded" --artifact research/problem-cards.md
```

If no card survives, the gate is `failed` with the killing evidence — never quietly weakened. Route forward only by fully qualified name: `/auto-research:research-theory-siege`, `/auto-research:research-method`, `/auto-research:research-experiments`, or back to `/auto-research:research-evidence`.
