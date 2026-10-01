# Verification Policy — the gatekeeper, not the companion

Verification exists to stop a bad object from
passing through a door. It does not walk alongside ordinary work. Normal writing, drafting,
and figure generation run **generate → record → continue**; they are not auto-intercepted by a
verifier or a refuter.

## Default: reuse the smallest sufficient evidence

Apply [verify-and-stop](../skills/verify-and-stop/SKILL.md), loaded once by pilot or
research-loop. This policy takes precedence over legacy phase text that asks for a
fresh rerun, full doctor, or hash inventory merely because a phase/turn ended.
Reuse a pass whose relevant inputs, code, assumptions and environment are unchanged.
Known edits and existing versions/results establish scope; do not read and hash all
files to prove absence of change. New edits, failures or concrete unresolved questions
invalidate only the affected evidence. Uncertain evidence remains unchecked.

## Gate points and sufficient evidence

1. **CLI write-time** — machine validation whenever the CLI persists state, a claim, a run, or a
   delivery lock. Cheap, deterministic, always on.
2. **Before an irreversible operation** — one check, immediately before: `push`, `release`,
   deletion, overwrite of a recorded artifact, or a narrative `overthrow`.
3. **Closing a core scientific object** — one relevant key. Reuse an existing valid
   independent reproduction or proof check; otherwise recompute the affected object
   (`research-reproducer`, a regression command, a re-derivation). Reach
   for `research-verifier` only when there is no mechanical criterion.
4. **Phase exit / snapshot / submission** — reuse sufficient checks already completed.
   `doctor` checks the plugin package/host, not a research result: run it only after a
   relevant plugin/environment change, a suspected installation fault, or an explicit
   request. A focused check can suffice even after a plugin change. Never run a full
   doctor merely for a phase boundary, small commit, report, or unchanged submission.

## Hash and reading budget

Use existing revision/run references and known dependencies by default. Hash only for
a required frozen content identity, scientific input integrity, or transfer boundary,
then reuse that identity until the relevant content changes. Do not create redundant
per-step file inventories, checksum archives or hash reports. Turn scanning caches no
new ledger: metadata in its existing snapshots lets it reuse unchanged file digests;
strict content mode remains available for genuine integrity questions. These metadata
checks are not proof against deliberately preserved timestamps or tampering.
Read targeted sections and prior summaries; do not repeat full-file/log retrievals.

## Three tiers

| Tier | Content |
|---|---|
| **Keep** | Required correctness evidence and the gate conditions above; valid evidence may be reused. |
| **Spot-check** | `research-refuter` only against a central theorem or a fragile object; cold review at stage granularity (once per version at a gate), not per section or per edit; full figure QA only when an artifact is promoted to `current`. |
| **Delete** | Two-key closure as the default (one key closes; a second key is an escalation the user or a central theorem earns); `doctor` every tick; cold verification after purely generative work; the same failure mode re-checked in hook, lint, and agent layers — one layer owns each failure mode. |

## Calling the judge agents

The three judge agents ([research-verifier](../agents/research-verifier.md),
[research-refuter](../agents/research-refuter.md), [research-reproducer](../agents/research-reproducer.md))
are one-shot and gate-triggered. Do not dispatch them because a phase "could" use a second opinion.
When a check is genuinely wanted twice, prefer **diversity of check over repetition**: a re-run plus a
read beats two reads.

## What a skipped check costs

Reusing a still-valid pass is not a skipped check. Briefly cite its existing result;
do not produce a second audit artifact. A required check with no sufficient evidence
is unchecked, failed, unavailable or blocked as appropriate. Once acceptance is met,
stop; no extra tests, polish or cleanup without a concrete reason.
