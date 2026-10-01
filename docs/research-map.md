# Research Map and Taste Ledger (3.1)

Two questions decide whether a researcher can use an AI collaborator at all: **can you see what the AI did**, and **does the work still follow your academic taste**. The ledgers this plugin already keeps (narrative tree, research graph, decision records) answer both; this contract puts them on one map and adds the one missing ledger — the researcher's taste, stated in their own words.

Why not ask a model? In Anthropic's 2026 TASTE study the strongest model agreed with expert researchers on research proposals 60% of the time, against 77% between experts, and most frontier models sat near chance. Taste therefore stays with the human; the system's job is to make it cheap to state, durable, and checked. For the same reason the map is drawn from the ledgers, never from a model's re-summary of its own work — a summary written by the AI is the AI vouching for itself.

## 1. The map (`scripts/research_map.py`, `narrative map`)

One self-contained HTML page (no network, no external asset). The story tree is drawn as a left-to-right mind map: fold / unfold, zoom, pan, search, presets *expand all · two levels · changes only · problems only*. Three overlays, each switchable:

| overlay | marks | source |
|---|---|---|
| changes | ✚ added · ✎ rewritten (before → after per field) · ⇄ moved (from where) · a *left the story* list with the recorded reason | narrative commits between `since` and head; each change lists its commits with author (AI controller / AI chronicler / you / backfill) and cause in plain words (forced by evidence and signed · possibly forced, awaiting you · chosen · unrecorded · wording) |
| evidence | ⚡ refuted or contested · ? held with no supporting edge · ▢ load-bearing with no figure · ⚠ benched (never refuted, sidelined anyway; `LEVER_DEBT` in the ledger) · ◷ live bet · ✔ a decision you signed | `graph/current.json` (read-only), `coverage_report`, `derive_history`, approval refs |
| taste | ✦ a node the AI added or rewrote trips one of your rules | `taste/rules.jsonl` |

Line style: dashed underline = pending; grey italic = demoted or merged.

**Since.** Default = the head at your last recorded view (`map/views.jsonl`), so opening the page answers "what happened since I last looked"; with no view yet, the previous commit. `--since` takes a commit, a ref, or a date (`YYYY-MM-DD` = the story as it stood that morning). `render` records a view; `--no-mark` does not.

**Staleness.** The header states when the story was last recorded and how many story-bearing files (`.tex`, plus the narrative config's `bearing_globs`) were modified after that commit, with the newest eight. A map that lags the work says so instead of looking current.

**Read-only.** The map never writes the narrative, the graph or the taste ledger. A graph projection that is behind its event stream is reported (`GRAPH_REBUILD_REQUIRED` in plain words) and the evidence overlay is skipped; it is not rebuilt from here.

## 2. Interop: export and import

- `export --format markmap` — Markdown for markmap / VS Code; each node carries `<!-- ar:<id> -->` and a badge suffix.
- `export --format canvas` — JSON Canvas (Obsidian), one card per node, laid out as the map.
- `export --format xmind` — `.xmind` (content.json + manifest); topic id = node id, and `[[ar:<id>]]` in the note as a fallback.
- `import --file <map.md|map.xmind> --out patch.json` — the edited map comes back **by stable id** as a NarrativePatch against the current head: renames become `patch_fields(title)`, re-parenting becomes `move_node`. A new topic becomes a `todo_proposal` (it needs a node type and identity), a deleted topic becomes a `todo_proposal` (dropping needs a disposition and a reason), a changed root becomes a `todo_proposal` (the overthrow gate). The patch is validated against the head before it is written and lands only through `narrative apply` → `narrative commit --pending-patch`. Nothing in a map file writes the story directly.

## 3. The taste ledger (`scripts/taste_ledger.py`, `narrative taste`)

`.research-os/taste/rules.jsonl` is append-only (`add` / `retire` events). A rule is `{id T-NN, text, scope ⊆ {story, writing, figures, ideas, method, all}, stance avoid|prefer, pattern?, source}`.

- **Every rule has a source**: `user` (the researcher's own words, `--quote`), `decision` (a decisions.md block), or `approval` (a signed narrative commit). A rule with no source is refused — unattributed taste is the model's taste.
- `harvest` lists verdicts already on record that are not yet rules (decision lines that name who ruled; commits with approval refs). Candidates are data for the researcher; only `accept` turns one into a rule.
- `check` scans story nodes (or only those changed `--since`) and manuscript files (`--files <glob>`, LaTeX comments skipped) for `avoid` rules with a pattern. Every hit is a flag for the researcher to judge, not a verdict: a pattern cannot tell a defensive sentence from a theorem that states a limit. Rules without a pattern are shown as reminders beside the changes in their scope.
- `retire` keeps the history; hand edits of `taste/` are blocked by `hook_guard`.

## 4. How the controller uses it

- "What did the AI do?", "show me the map", "这段时间改了什么" → `narrative map render` and open the page; in chat, report the one-paragraph `summary` (≤ 40 lines) in plain words.
- The researcher states a preference ("don't write X", "figures need Y") → `narrative taste add --source-kind user --quote "<their words>"`; say back the rule in one sentence. Never invent a rule on their behalf; a harvested candidate is offered, not applied.
- Before proposing story or manuscript changes, run `taste check --since <last view>`; a flag is surfaced with the proposal, not silently fixed.

## 5. Machine acceptance

`research_map.py --self-test` (30 checks): change / move / drop detection with reasons and authors, refuting and derived support edges, taste flags only on touched nodes, genesis-by-date, single-file page with round-tripping JSON and no external load, zh/en labels, view log and `--no-mark`, markmap / canvas / xmind exports, markmap and XMind import to a patch that `narrative apply` accepts, new and deleted topics only as proposals, stale graph reported without a rebuild, file staleness. `taste_ledger.py --self-test` (20 checks): source-required refusals, duplicates, scope, pattern flags, manuscript scan, harvest, accept, retire, append-only.
