---
name: verify-and-stop
description: Prove existing work meets acceptance conditions without expanding scope. Use for validation-only tasks, completion checks, focused gate runs, and last-mile proof.
---

# Verify and stop

Translate acceptance conditions into smallest sufficient proof set.

- Reuse still-current results with matching repository state.
- Run focused checks before wider gates.
- Distinguish pass, fail, unavailable, and blocked exactly.
- Do not edit product code unless verification request includes fixes.
- Do not add polish, cleanup, or unrelated tests after criteria pass.

Stop immediately when acceptance proof is complete. Report commands, results, and unresolved risk only.

## Auto Research integration

Read this once when entering pilot, a research loop, or a verification gate; reuse it
within the session. It governs verification scope across the phase skills.

- Reuse a passing result when its relevant code, inputs, assumptions and environment
  are unchanged. Existing revisions, known edits and prior run results usually suffice;
  do not rehash files just to justify reuse. Do not reuse a pass when its dependencies
  are uncertain or a specific failure remains unresolved.
- After a change, check the affected dependency path only. Expand to a full check only
  for a concrete cross-cutting risk, missing relevant evidence, or an explicit request.
  Phase boundaries, small commits and report delivery are not automatic full-test gates.
- Keep necessary schema checks, scientific evidence and correctness-sensitive content
  identities. Compute hashes once when needed for a frozen input, content-addressed
  object or required transfer-integrity check; reuse them until that content changes.
  Do not add per-step directory hashes, duplicate manifests or audit-only inventories.
- A previously completed independent reproduction can satisfy the same unchanged
  scientific gate. A new experiment's own smoke/results need their relevant checks;
  this does not reopen every historical experiment or proof.
- Read the changed section or existing summary instead of reopening whole files/logs.
  When acceptance is met, report and stop. Never relabel unchecked work as passed.

Runtime note: ordinary turn scans reuse digests from existing snapshots when size,
nanosecond timestamps and file identity agree. This is a local change detector, not
tamper-proof integrity verification. New/changed files are hashed; setting
`input_scan_mode` to `content` in the existing narrative config forces fresh content
reads. Algorithmic identity, frozen-claim and delivery-integrity hash functions remain intact.

Source: adapted from [JuliusBrussee/caveman — verify-and-stop](https://github.com/JuliusBrussee/caveman/blob/main/skills/verify-and-stop/SKILL.md),
MIT, copyright 2026 Julius Brussee; see [LICENSE](LICENSE). The targeted-before-full
validation principle also aligns with [g2i-ai/agents workflow](https://github.com/g2i-ai/agents/blob/main/skills/workflow/SKILL.md);
its larger workflow was not installed. Local research-specific integration: 2026-09-06.
