---
name: research-manuscript
description: Develop, restructure, or refine a research manuscript from verified claims and evidence — contribution framing, claim-evidence mapping, section architecture, drafting, citation integration, limitations, and coherence repair. Use when routed by /auto-research:pilot after substantial research artifacts exist. Do not use for cold review, rebuttal strategy, unverified literature discovery, or inventing missing experimental evidence.
---

# Research Manuscript

Write the paper the evidence supports, not the paper the project hoped to support.

```
CLI  = py ../../scripts/research_os.py      (POSIX: python3 ../../scripts/research_os.py)
TURN = py ../../scripts/turn_runtime.py     (turn / lease / role commands)
```

If SessionStart printed `RECOVER_TURN_REQUIRED` or `REHYDRATE_REQUIRED`, clear it before drafting a word: `TURN lease status` → `TURN turn probe`, take or recover the write lease, resume the OPEN turn by replaying its manifest under the same idempotency key. Without the lease your writes are refused; a turn outlives the session that opened it.

## Entry

`CLI probe --phase manuscript --write-state <root>`, then obey each verdict's `on_missing` policy ([capability-registry.md](../../docs/capability-registry.md)): `degrade` → use the registered fallback and record the degradation in state; `blocker` → `CLI blocker add` with a `queued_action`, continue other manuscript work; `stop` → route to another open gate. Silent skipping is a contract violation. Read the verdict, never your memory of what tools exist.

**Register before running** ([attempt-tree.md](../../docs/attempt-tree.md)): every bounded try this phase makes — a search strategy, a proof route, a protocol variant, a rescue plan — is a coded node in the attempt tree. `CLI tree <root> register --kind attempt --parent <route> --title "…"` before executing, `close --status … --result "…"` at the verdict; run `CLI tree <root> list --under <route>` first so a dead sibling is never re-run.

| capability | claude-code first choice | codex first choice |
|---|---|---|
| latex-toolchain | latexmk → pdflatex | same |
| overleaf-sync | overleaf-skill (git-bridge) | chrome UI (`chrome:control-chrome`) |
| citation-library | project-local BibTeX ledger | zotero:Zotero → local BibTeX |
| pro-refine | external high-reasoning model (user-accepted only) | same |

## Build the claim-evidence map first

Before drafting a word, list every headline and supporting claim with its evidence source: theorem, experiment run, dataset fact, citation, figure, or explicit limitation. Mark unsupported claims. Route missing citations to `/auto-research:research-evidence`, formal gaps to `/auto-research:research-theory-siege`, empirical gaps to `/auto-research:research-experiments` — never patch an evidence gap with prose. Register the map: `CLI asset add <root> --path <map> --role ledger --tag claim-map`.

## Choose the core story

Write one sentence each for problem, why prior approaches fail in this regime, key insight, method, strongest evidence, and consequence. Keep one primary contribution thesis; distinguish contributions from implementation details and results. Adapt emphasis by paper type:

- Theoretical ML: statement hierarchy, proof intuition, assumptions, boundary, algorithmic consequence.
- Empirical ML: construct, protocol, comparative evidence, uncertainty, failure cases.
- Domain application: domain problem, information set, mechanism, domain-native value, restrained interpretation.
- Systems or tooling: workload pain, system insight, design, operational evidence, limitations.
- Survey or position: selection method, taxonomy, synthesis, counterposition, implications.

## The story is versioned state

This is the phase where the narrative actually moves, so it is the phase that owes the ledger the most. The story lives as a tree with a commit history ([narrative-contract.md](../../docs/narrative-contract.md)); rewriting a section is not the same event as changing what the paper claims.

- **Every turn here gets a disposition.** `TURN turn probe` gives the `turn_uuid` and frozen `manifest_id`. When the manifest is narrative-bearing, rebuild the chronicler (`TURN role hydrate chronicler` → spawn the **research-chronicler** agent with that payload) and send one `CHRONICLE_TURN/v1` envelope. Its ≤30-line four-field reply — `proposed_ops` / `commit_meta` / `flags` / `memory_delta` — is a **proposal**; you record it with `TURN turn record-disposition --manifest … (--ops-file … --meta-file … | --no-change --reason …)` and the CLI constructs the receipt. Polishing prose that changes no claim is a legitimate `NARRATIVE_REVIEWED_NO_CHANGE`; inventing a node to look productive is not.
- **Same claim, better wording ⇒ same node id.** A changed subject, target, predicate, quantifier, domain, condition, or polarity is a **new** id with `lineage` — never a quiet edit under the old one. That distinction is what later tells a refinement apart from a swapped headline.
- **Changing the headline is not a drafting decision.** Swapping headline / flagship / backbone / empirical-core / foil is a `restructure` commit whose `cause` you must state honestly: a review that stung you is `unforced` unless the old story genuinely could not stand. Replacing the root story is refused outright until the user approves it (`OVERTHROW_REQUIRES_USER_APPROVAL`). Route both to `/auto-research:research-narrative`.
- Knocking off is gated: `TURN turn finalize` refuses while any manifest lacks its receipt and prints `CHRONICLE_REQUIRED`. `TURN turn escape --reason "…" --user-approved` only on explicit user authorization — it writes an incident, not a receipt.

## "这里要张图" — the user says one sentence, you do four steps

Drafting is where figures are actually demanded, and the demand is the cheapest moment to
book one. **The demand arrives as a sentence, never as a request shape**: "这里要张图" · "4.3
配个图说明引用率怎么随附录长度变" · "把这张表画出来" · "can we show this instead of telling it" —
all of it is the same request, and none of it obliges the user to name a route, an id, a
claim, a venue, or a template ([pilot](../pilot/SKILL.md), *How the user experiences this*,
rule 1). You supply what registration needs from the manuscript you are drafting: the reader
question from the passage, the claim id from the claim-evidence map, the section from where
the cursor is, the venue from state. Ask only what genuinely changes the figure, once, with a
recommendation — usually nothing.

Do not open a plotting script; register the figure and let the Router decide
([figure-engineering-contract.md](../../docs/figure-engineering-contract.md)). The four steps
below are yours; what the user hears back is "图做好了,放在 4.3,这版是要进稿的" plus the
render — not `F-101`, not the QA table, and not the graph edge.

```
FIG = py ../../scripts/figure_router.py     (POSIX: python3 ../../scripts/figure_router.py)
```

1. **Register** — `FIG quick --register --intent "<the reader question>" --claim C-057 --section 4.3 [--data <csv> --columns "x=…,y=…"] [--semantic "…"] --venue <venue>`. This mints `F-1NN`, decides Route A (data → matplotlib template) / B (components) / C (concept → figure-studio), and books the instance.
2. **Produce** — Route A: `FIG quick --render F-101`; Route B: `FIG quick --assemble F-101`; Route C: `FIG quick --promote-to-fsg F-101` and hand off to `/auto-research:research-artifacts`.
3. **Read the QA** — `qa.json` next to the artifact reports venue width, embedded fonts, live svg text, missing cells and component licences. It reports; you decide.
4. **Promote** — `FIG quick --promote-current F-101 [--render-id R-...]` flips it to the version that ships, supersedes the previous `current` for the same claims, saves the registry and runs `graph derive`, whose fig-registry rule books the `expressed_as` edge (the figure line never links edges itself) so `graph view coverage` stops listing that claim as unexpressed.

A figure that never went through step 1 has no provenance and no claim binding; do not cite
it in the manuscript. Deep visual work — a paper-wide visual program, a rescue, a
composition-ready architecture figure — still routes to `/auto-research:research-artifacts`.

## Draft in dependency order

Stabilize contributions and result claims before polishing the abstract. Build method and experiment sections from versioned specs and protocols. Integrate related work by comparison axis, not citation lists. Cite only from verified evidence records. State limitations next to the affected claims, not as a generic final paragraph.

**Version discipline (the number long contexts always lose)**: every time the manuscript materially advances, bump in the same breath — `CLI update <root> --version manuscript=vN` — and register load-bearing outputs with `CLI asset add --tag manuscript [--hash]`. Before any destructive rewrite, restructure, or model switch: `CLI snapshot <root> --label pre-<what>`. The `context` digest is authoritative; conversation memory is not.

External-model refinement (`pro-refine`) only when the user requests or has standing-accepted it; otherwise the degraded route is independent local subagent passes, recorded as such. When an exact Overleaf project is an input or destination, read [overleaf.md](references/overleaf.md) and route through the `overleaf-sync` capability; compile and visually verify the PDF locally before any remote synchronization.

## Check consistency

This audit is in-house and mechanical wherever it can be — it is what keeps drafting unescorted, so run it yourself rather than dispatching a judge agent. Audit notation, dataset counts, metric names, table and figure references, theorem numbering, claim strength, tense, and abstract-to-conclusion consistency. Search for **causal, first, optimal, guaranteed, robust, interpretable** language and demand evidence for each occurrence — downgrade the wording or fetch the evidence, never leave the word standing on hope.

## Visual ownership

This skill owns each visual's reader question, claim ID, caption job, argumentative order, and intended placement; `/auto-research:research-artifacts` owns the frozen visual contract, specialist production, and QA. Integrate only a verified delivery, then compile and inspect its page. Route independent paper criticism to `/auto-research:research-review`.

## Subagent playbook

Fan out when sections are independent (e.g. related-work synthesis vs. method drafting) or for the consistency audit. Every subagent prompt must carry: the frozen inputs (claim-evidence map path, contribution thesis, current manuscript version label), exact absolute paths to read, the expected artifact and return format (file path + list of changed claims), and a falsify/stop condition ("if a claim has no evidence entry, report it — do not draft around it"). Merge evidence, never merge confidence: a subagent's "looks consistent" is a report, not a pass. Independent cold verification of claim-evidence alignment is a **gate-point** spend: send it to the `research-verifier` agent once, at the exit gate below, with fresh context and no access to your draft rationale ([../../docs/verification-policy.md](../../docs/verification-policy.md)). Ordinary drafting is **generate → record → continue** — a section, a paragraph, or a rewritten abstract does not summon a verifier or a refuter; the consistency audit and the compile are the checks that run in between.

## Exit

Finish when the manuscript has a coherent claim-evidence story, no known unsupported headline claim, verified citations, explicit limitations, and pointers to all result artifacts. Then:

```
CLI update <root> --gate "claim_evidence_map=passed:<map path>@research-manuscript" \
    --gate "manuscript_compile=passed:<pdf + log>@research-manuscript" --version manuscript=vN \
    --clear-next --next "Cold review vN@research-review"
CLI event <root> --type phase_advanced --summary "manuscript vN drafted/revised" --artifact <pdf>
```

A failed gate loops back here with evidence; missing capability or authorization → blocker, visibly parked. Route criticism to `/auto-research:research-review`, figure production to `/auto-research:research-artifacts`, packaging to `/auto-research:research-reproducibility`.


## Prose drafting

When the deliverable **is** the writing -- a manuscript section, a report, a handoff document, a talk
script, a cover letter -- it goes through the `prose-drafting` capability: the running model by default,
or the writer model the researcher has registered there. The division of labour holds either way: the
running model keeps every decision, every number, every verification, and the discipline scan. Give a
delegated writer the frozen facts rather than a summary of them -- numbers with their sources, scope
limits, banned words, audience, length -- and check what comes back for facts and discipline instead of
rewriting it into your own voice.
