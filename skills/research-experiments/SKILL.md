---
name: research-experiments
description: Design, implement, execute, and verify a research experiment or computational theorem check with leakage-safe protocols and reproducible evidence. Routed by /auto-research:pilot for feasibility studies, benchmark design, baseline implementation, ablations, robustness tests, statistical analysis, debugging result discrepancies, or experiment sufficiency review. Do not use for publication figure styling or unsupported result narration.
---

# Research Experiments

Make every reported number traceable to a frozen question, protocol, and run.

Use [verify-and-stop](../verify-and-stop/SKILL.md): reuse valid results for unchanged
dependencies. A gate handoff does not require another reproduction, report or checksum
package. New scientific inputs/code need relevant validation; test only affected paths.

```
CLI = py ../../scripts/research_os.py      (POSIX: python3 ../../scripts/research_os.py)
```

## Entry

`CLI probe --phase experiments --write-state <root>` first. Obey each verdict's `on_missing` policy per [../../docs/capability-registry.md](../../docs/capability-registry.md): `degrade` → use the registered fallback and record the degradation in state; `blocker` → `CLI blocker <root> add --type capability …` with a `queued_action` and continue other work. Silent skipping is a contract violation.

**Register before running** ([attempt-tree.md](../../docs/attempt-tree.md)): every bounded try this phase makes — a search strategy, a proof route, a protocol variant, a rescue plan — is a coded node in the attempt tree. `CLI tree <root> register --kind attempt --parent <route> --title "…"` before executing, `close --status … --result "…"` at the verdict; run `CLI tree <root> list --under <route>` first so a dead sibling is never re-run.

| capability | claude-code first choice | codex first choice |
|---|---|---|
| python-env | `py` → python3 | same |
| compute-server | ssh to your own compute host (register first) | same |

## Freeze the protocol

Before a full run, write or update `research/protocols/<protocol-id>.md` with: hypothesis and primary endpoint; dataset version, population, unit of analysis, label, and point-in-time availability; train/validation/test, embargo, grouping, and leakage controls; strongest domain-native and methodological baselines; tuning budget and compute parity; primary metrics, uncertainty, seeds, and multiplicity treatment; ablations tied to method components or formal claims; robustness, negative controls, stress cases, and stopping rules; expected output files and acceptance checks.

Freezing is recording: `CLI asset <root> add --path research/protocols/<protocol-id>.md --role spec --tag protocol --version-label v<N>` + `CLI update <root> --version protocol=v<N>`. Snapshot before rewriting a frozen protocol: `CLI snapshot <root> --label pre-<protocol-id>-v<N+1>`.

For theoretical ML, use experiments to validate encodings, boundary instances, reductions, or numerical behavior; never present finite measurements as proof of asymptotic separation.

## Implement with verification hooks

These are the retained machine checks — cheap, deterministic, always on — and they are what lets everything else stay unescorted ([../../docs/verification-policy.md](../../docs/verification-policy.md)). Inspect existing code and preserve its conventions. Build the smallest vertical slice first. Add unit tests for preprocessing, identity and timestamp joins, splits, metrics, and critical equations; add invariant or property tests where possible. Pin randomness; record environment and command metadata. Run a smoke test before expensive work; estimate time, memory, GPU, storage, and external cost. Ask before a materially costly or externally mutating run that was not already in scope.

## Execute and diagnose

Assign immutable run IDs and write outputs under `results/<protocol-id>/<run-id>/`. **Never overwrite a prior run**; a retry gets a new `run_id` plus `parent_run_id`. Separate code defects, data defects, protocol defects, resource failures, and negative scientific results. **A failed hypothesis is evidence**; do not tune it away without recording the change as exploratory.

**Capability `compute-server`** — any run expected >30 min or >8 workers goes to a compute host, not the laptop. The protocol is binding: register in the run ledger before launching, run inside tmux, long jobs must checkpoint and resume. Red line: **zero credentials on the server** — no tokens, keys, or sensitive text, public data and code only. If the server is unreachable, `on_missing=degrade`: run a reduced local slice and record the degradation.

When a long run launches, immediately `CLI event <root> --type run_launched --summary "<protocol-id>/<run-id> on <host>"` — later ticks of /auto-research:research-loop reconcile in-flight work **by `run_id`, never by blind relaunch**. Keep transient caches in ignored local scratch; do not commit or upload them.

**Mechanized run ledger** (`py ../../scripts/procedures.py run`, POSIX `python3`; it records and reconciles — it never executes, execution stays with the executor/compute-server): `run register <root> --protocol <pid> --command "…" [--node <tree>] [--parent <run>]` books an auto-numbered `run_id`; `run launch --id` marks it `running` at kickoff; `run finish --id --status done|failed --result "…" [--artifact REL]` closes it — **a `done` run must name at least one `--artifact` that actually exists on disk, enforced by the CLI (a finished run may not point at nothing)**. Each /auto-research:research-loop tick settles in-flight work with `run reconcile <root> [--stale-hours N]`, which lists stale `running` rows to reconcile **by `run_id`, never by blind relaunch** (a `running` row may still be alive on the server).

## Analyze without cherry-picking

Report the primary analysis first, then robustness and exploratory findings. Include effect sizes, uncertainty, sample counts, runtime or resource use where relevant, and failure cases. Compare against the predeclared protocol and disclose deviations. After method changes, rerun only the counterexample/theorem regressions whose assumptions or dependencies were affected.

## Subagent playbook

Fan out for separable, protocol-frozen work: independent baseline implementations, per-ablation runs, or a reproduction pass from the recorded command alone. Every prompt must carry: the frozen `protocol-id` and version, exact paths (protocol file, code entry point, `results/<protocol-id>/<run-id>/` output dir), the expected artifacts (output files + a run manifest with command, seed, environment) and return format, and a stop condition ("if the smoke test fails, report and stop — do not modify the protocol"). Merge outputs as evidence keyed by `run_id`, never merge confidence — "the numbers look right" is not an acceptance check. Closing a headline result spends **one** key ([../../docs/verification-policy.md](../../docs/verification-policy.md)): reuse an existing valid independent reproduction for unchanged dependencies; otherwise have research-reproducer rerun only the affected recorded computation. Send the research-verifier agent (fresh context, the frozen protocol and raw run artifacts only, no analysis narrative attached) when the acceptance criterion is not mechanical, e.g. protocol-vs-result conformance or leakage judgment. Intermediate runs, ablation sweeps, and exploratory passes are closed by their own acceptance checks, not by a judge agent.

## Sufficiency and exit gate

Ask whether results distinguish the proposed mechanism from simpler alternatives and support each paper claim. Route missing formal support to /auto-research:research-theory-siege, method changes to /auto-research:research-method, publication visuals to /auto-research:research-artifacts. Experiments own every plotted value, denominator, uncertainty rule, run ID, data version, and comparison set; if any hashed result input changes after a visual request is frozen, mark that request stale and require a new Phase-1 freeze — the plotting layer never adapts silently.

Register primary outputs using their existing run/version references: `CLI asset <root> add --path results/<protocol-id>/<run-id>/<file> --role evidence --tag headline`. Add `--hash` only when a frozen protocol, content identity or transfer-integrity contract actually requires it; reuse an already-recorded unchanged digest instead of making another inventory. Then:

```
CLI update <root> --gate "experiments=passed:<protocol-id> primary reproduced from recorded command@research-experiments" --clear-next --next "…"
CLI event <root> --type phase_advanced --summary "results accepted: <run-ids>" --artifact results/<protocol-id>/<run-id>/
```

Finish when sufficient reproduction evidence exists for the current primary result, affected checks pass, deviations are explicit, and conclusions point to existing run artifacts. Reuse an unchanged valid reproduction; do not rerun it solely to close this phase. Make a scoped local git commit; do not push or upload data without the required authorization.
