---
name: research-evidence
description: Build a verified literature, closest-prior, novelty, citation, dataset, and provenance evidence base for a research project. Use when explicitly routed by /auto-research:pilot during preliminary study, novelty re-audit, related-work repair, dataset selection, or when a claim depends on external sources. Do not use for free-form ideation, model implementation, or prose-only polishing.
---

# Research Evidence

Turn external sources into claim-level evidence rather than a pile of PDFs.

```
CLI = py ../../scripts/research_os.py      (POSIX: python3 ../../scripts/research_os.py)
```

## Entry

`CLI probe --phase evidence --write-state <root>` before any search. Obey each verdict's `on_missing` policy from [../../docs/capability-registry.md](../../docs/capability-registry.md): `degrade` means use the registered fallback and record the degraded route in state so the paper trail shows which route produced which entry; anything needing permission or a user decision becomes `CLI blocker add` — silent skipping is a contract violation. Never assume a tool exists because prose mentions it.

**Register before running** ([attempt-tree.md](../../docs/attempt-tree.md)): every bounded try this phase makes — a search strategy, a proof route, a protocol variant, a rescue plan — is a coded node in the attempt tree. `CLI tree <root> register --kind attempt --parent <route> --title "…"` before executing, `close --status … --result "…"` at the verdict; run `CLI tree <root> list --under <route>` first so a dead sibling is never re-run.

| capability | claude-code first choice | codex first choice | on_missing |
|---|---|---|---|
| web-evidence | WebSearch/WebFetch | web | blocker |
| lit-orchestration | deep-research skill → parallel Explore agents + ledgers | $paper-lit-orchestrator | degrade |
| citation-library | project-local BibTeX ledger | zotero:Zotero → local BibTeX | degrade |
| pdf-tools | Read tool (native) | pdf:pdf → pdftotext | degrade |
| local-git | git CLI | git CLI | stop |

## Freeze the evidence questions

List the claims that need evidence, closest-prior risks, required dates, domain constraints, and data feasibility questions. Separate discovery queries from verification queries. Use lit-orchestration for multi-paper intake, normalized citation ledgers, OA or full-text triage, and novelty-risk audit; citation-library only when the task needs the library, BibTeX, or an authorized import; web-evidence for current or niche facts — and rely on primary papers, official documentation, and authoritative datasets for technical claims.

## Build four linked ledgers

Store compact, project-local records under `research/evidence/`:

1. `search-log`: query, source, date, filters, exclusions, and access gaps.
2. `evidence-ledger`: stable ID, bibliographic identity, verified source URL, full-text status, exact claim touched, relation type, safe wording, forbidden wording, and confidence.
3. `closest-prior-matrix`: contribution axis by prior work, with fatal novelty risks distinguished from ordinary context.
4. `data-provenance`: source, license, coverage, point-in-time availability, revisions, identifiers, missingness, label construction, privacy, and acquisition path.

Keep quotations short and traceable. Mark recollection without a checked source as `UNVERIFIED_RECOLLECTION`; never let it support a theorem or novelty claim. As each ledger becomes load-bearing, index it: `CLI asset add <root> --path research/evidence/<ledger>.md --role ledger --tag ledger --produced-by research-evidence` (all four). On a major re-audit, bump `CLI update <root> --version evidence=v2`; before destructively rewriting the closest-prior matrix, `CLI snapshot <root> --label pre-novelty-reaudit`.

**Mechanized ledger writes** (`py ../../scripts/procedures.py ledger`, POSIX `python3`): append every row through `ledger add <root> --ledger search-log|evidence|closest-prior|data-provenance --data key=value …`. The required keys are checked by the CLI (`search-log`: query/source/date · `evidence`: id/claim/source/relation/confidence · `closest-prior`: axis/prior/risk · `data-provenance`: source/license/coverage), so a row missing one is refused, never silently half-written. **An `evidence` row added without `--data verified=1` is auto-stamped `UNVERIFIED_RECOLLECTION` by the machine** — the recollection discipline above is enforced, not trusted to memory. Query with `ledger list --ledger <name> [--where key=value]`; regenerate the bounded `research/evidence/LEDGERS.md` digest (last five rows per ledger) with `ledger render <root>`.

## Adapt the audit

- Theoretical ML: verify theorem ancestry, assumptions, and whether the claimed boundary is already known.
- Empirical ML: find strongest baselines, accepted protocols, datasets, and negative or contradictory results.
- Domain application: include domain-native literature, observation timing, construct validity, and regulatory or ethical constraints.
- Systems or tooling: verify comparable systems, workloads, costs, and operational claims.
- Survey or position: document inclusion criteria, coverage, competing taxonomies, and counterpositions.

## Subagent playbook

Fan out when the question list is wide and independent: one subagent per frozen evidence question or per closest-prior candidate, run in parallel (discovery sweeps may batch; verification of a fatal-risk prior gets its own agent). Every prompt must carry: (1) the frozen question or claim id, verbatim, immutable during the run; (2) exact paths — where the ledgers live under `research/evidence/` and which entries it may extend; (3) the expected product and return format (ledger-row candidates with source URL, full-text status, exact quoted span, relation type, confidence); (4) the falsification/stop condition (what finding closes the question; return `NOT_FOUND` with the queries tried rather than a plausible guess). Merge their evidence, not their confidence: every returned row is re-verified against its primary source before entering the evidence-ledger, and only this skill writes the ledgers. The novelty thesis is a core scientific object, so its **closing** gets one key: for the final adjudication only, use the research-verifier agent for an independent cold pass over the matrix (Claude side; Codex side = a fresh session given only the ledger paths) — it must try to break the novelty thesis, not confirm it. Per-question sweeps and candidate rows close on primary-source re-verification alone; do not attach a judge agent to each ([../../docs/verification-policy.md](../../docs/verification-policy.md)).

## Adjudicate novelty and feasibility

Write a one-paragraph defensible novelty thesis and a `what-not-to-claim` list. Identify missing full texts and data blockers separately: `CLI blocker add <root> --type external --description "paywalled: <ref>" --queued-action "<acquisition path>"` — a missing paywalled source remains an explicit queue item; it is never silently treated as evidence.

## Exit gate

Finish when the nearest prior is verified, dangerous claims are bounded, the data path is credible or explicitly blocked, citations are traceable, and the next discovery or method decision can be made from the ledger. Do not draft the paper here.

```
CLI update <root> --gate "novelty_map=passed:closest prior verified + what-not-to-claim@research-evidence" \
   --gate "data_path=passed:provenance credible or explicitly blocked@research-evidence" \
   --set active_skill=research-evidence --clear-next --next "<next step>@<owner_skill>"
CLI event <root> --type phase_advanced --summary "evidence base built; novelty thesis adjudicated" --artifact research/evidence/closest-prior-matrix.md
```

A fatal collision is a `failed` gate with the killing prior as evidence, not a softened thesis. Route forward only by fully qualified name: `/auto-research:research-discovery` or `/auto-research:research-method`.
