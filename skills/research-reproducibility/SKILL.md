---
name: research-reproducibility
description: Audit, package, archive, release, and selectively synchronize final research code, data manifests, environments, results, manuscripts, and knowledge state. Use when routed by /auto-research:pilot near a milestone, submission, rebuttal, handoff, release, or project close, or when recovery and reproducibility are at risk. Does not require GitHub, Drive, Zotero, or cloud authentication during earlier phases.
---

# Research Reproducibility

Make the verified result reconstructable without turning every service into a prerequisite.

```
CLI = py ../../scripts/research_os.py      (POSIX: python3 ../../scripts/research_os.py)
```

## Entry

`CLI probe --phase reproducibility --write-state <root>`, then obey each verdict's `on_missing` policy ([capability-registry.md](../../docs/capability-registry.md)): `degrade` → registered fallback, recorded; `blocker` → `CLI blocker add` with a `queued_action`; `stop` → that route halts (no `local-git`/`python-env` means no honest packaging). **A missing `github` or `drive-sync` capability queues a blocker and must not block the rest of the packaging.**

**Register before running** ([attempt-tree.md](../../docs/attempt-tree.md)): every bounded try this phase makes — a search strategy, a proof route, a protocol variant, a rescue plan — is a coded node in the attempt tree. `CLI tree <root> register --kind attempt --parent <route> --title "…"` before executing, `close --status … --result "…"` at the verdict; run `CLI tree <root> list --under <route>` first so a dead sibling is never re-run.

| capability | claude-code first choice | codex first choice |
|---|---|---|
| github | gh (authenticated) | gh |
| drive-sync | Google Drive MCP connector | rclone |
| overleaf-sync | overleaf-skill (git-bridge) | chrome UI |
| latex-toolchain / pdf-tools | latexmk → pdflatex / Read tool | same / pdf → pdftotext |

## Audit the package

Trace each manuscript claim to code, protocol, run, result, theorem, citation, figure, and table. Verify:

- environment and dependency lock; deterministic entry commands and documented randomness;
- data source, version, license, checksum, acquisition and transformation lineage;
- point-in-time and split construction where applicable; tests and a smoke run;
- configuration and hardware assumptions; expected outputs and tolerances;
- manuscript, supplement, artifact, and anonymity compliance (scan all files, including binary layers — no blocklist shipped in the package);
- visual-program prompt/freeze hashes, figure specs, editable or reproducible sources, exports, captions, semantic/visual QA with reviewer identities, delivery manifest and detached delivery lock, rendered manuscript placement;
- absence of secrets, private paths, raw credentials, and unlicensed redistributable data.

Create a concise reproducibility manifest with checksums, and index every deliverable: `CLI asset add <root> --path <file> --role deliverable --tag package --hash` — the asset index with sha256 is the machine-readable manifest. Run from a clean environment or fresh checkout when proportional to the claim and milestone.

## Version locally first

Inspect git status, stage explicit task files only, run `CLI validate <root>`, and create a scoped local commit. Preserve unrelated changes and large data outside git. **A local commit is the default durable checkpoint; a remote is never required.**

## Activate external services only when needed

Read [authorization-boundaries.md](../../docs/authorization-boundaries.md) before any external write. Capability availability is never authorization.

- GitHub: probe only for collaboration, backup, PR, release, or public-artifact work. Push, PR, release, and remote creation must stay within the recorded `state.authorization` scope; denied/missing authorization → blocker with the exact queued command, continue packaging.
- Drive (`drive-sync`): on demand for authorized inputs or ordinary-sized final documents. Large final data: show path, size, sensitivity, license, checksum, destination, and **require explicit confirmation for every upload** (`drive_upload: confirm_each_action` is validator-enforced). Keep small manifests or pointers in git.
- Citations (`citation-library`): export or synchronize only when the package needs it and the library scope is authorized; degraded route = local BibTeX ledger.
- Overleaf (`overleaf-sync`): an optional manuscript destination, never the canonical checkpoint. Sync only after a scoped local commit or checksummed snapshot, a successful compile, and PDF inspection; verify the remote compile and record non-secret revision evidence. When copies go out to coauthors or reviewers, follow the sharing discipline in [overleaf.md](../research-manuscript/references/overleaf.md): dry run first, and one registry records what went where.

Do not upload transient runs, caches, checkpoints, raw browser captures, or intermediate data. Do not commit tokens or machine-specific credentials. Server red line: the shared compute server gets zero credentials.

## Subagent playbook

Fan out independent audit lanes (claim-trace, environment/smoke-run, secret-and-anonymity scan, visual-manifest check). Each prompt carries: the frozen manifest and asset index snapshot, exact absolute paths, the expected artifact (a per-lane checklist with pass/fail + evidence path per row) and return format, and a falsify/stop condition ("a claim you cannot trace to an artifact is a FAIL row, not a footnote"). Merge evidence rows, never merge confidence — one lane's pass does not soften another's fail. The final clean-checkout reproduction goes to the `research-reproducer` agent with fresh context and only the manifest as input — it re-runs the recorded commands verbatim rather than reading them, and a package that will not run as recorded is itself the finding. Reach for `research-verifier` here only for the parts that have no mechanical criterion to recompute.

## Archive and recover

Terminalize in this order — each step gates the next:

1. `CLI validate <root>` — must pass;
2. `CLI snapshot <root> --label milestone-<name>`;
3. `CLI update <root> --set status=archived --clear-next` (record remaining known limitations in decisions.md first);
4. `CLI event <root> --type milestone_archived --summary "<milestone>: package hashed, N deliverables indexed"`.

Archive immutable release metadata without moving user files destructively. Test that a new session can resume from `CLI context <root>` and the manifest alone, without chat history.

## Exit

Finish when the milestone package is internally reproducible, locally versioned, legally and ethically distributable as configured, recoverable from compact state, and externally synchronized only to destinations the user authorized — with every deferred sync parked as a visible blocker, never dropped. Route post-archive follow-ups to `/auto-research:pilot`.
