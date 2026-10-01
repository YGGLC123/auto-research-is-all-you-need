# Attempt Tree — structured exploration record (`tree.json` + `ATTEMPTS.md`)

Text logs record *that* something happened; the attempt tree records **the shape of the search**: which routes exist, which attempt under which route produced what outcome, and what is still open. It is the antidote to the two classic long-context failures — re-trying a dead branch and forgetting why a branch died.

Files (both live in `.research-os/`, both written only by the CLI):

- `tree.json` — the structured truth (schema `auto-research/tree-v1`).
- `ATTEMPTS.md` — the human master table, **re-rendered automatically on every mutation**: ASCII tree with status glyphs + full table + totals. Always current; never edited by hand.

## The strict coding scheme

A node code is dot-separated segments; each segment is `LETTER(S) + 2-digit sequence`. The letter carries meaning (the legend is stored in `tree.json` and printed in `ATTEMPTS.md`):

| letter | kind | meaning |
|---|---|---|
| `R` | route | a direction / branch of the search (方向/分路) |
| `A` | attempt | one concrete try under a route/claim/experiment |
| `C` | claim | a formal claim under siege |
| `E` | experiment | a protocol / experimental line |
| `V` | verification | an independent check of the parent |
| `D` | decision | an adjudication or user decision point |

Examples: `R01` (route 1), `R01.A03` (3rd attempt on route 1), `C02.A01.V01` (independent verification of the 1st attempt on claim 2). Children always extend the parent's code; sequence numbers are auto-assigned by `register` and never reused.

## Lifecycle — register first, always

```
register  →  start  →  close --status <terminal> --result "…" [--evidence …]
```

1. **`register` before anything runs.** Same discipline as the compute-server rule (先登记后开跑): an unregistered attempt cannot `start`, and `start`/`close` on unknown codes are rejected. Registration forces you to say *what* is being tried and *under which branch* before spending effort.
2. `start` marks it running (only from `registered`).
3. `close` requires `--result` (a closed attempt without a recorded outcome is amnesia) and a terminal status: `success | failed | proven | refuted | blocked | abandoned | superseded | inconclusive`. `blocked` additionally requires `--blocker-id` — the blocker queue and the tree stay linked. `--force` allows closing a never-started node (e.g. superseded by a sibling) and branching off a closed parent.
4. `note` appends evidence / updates the running result without closing.

Every transition also appends an event (`tree_registered` / `tree_started` / `tree_closed`) to `events.jsonl`, so the audit stream and the tree cannot drift apart.

## Commands

```bash
CLI tree <root> register --kind route --title "PSPACE re-hypothecation route" [--parent R01] [--goal …] [--owner research-theory-siege] [--tag …]
CLI tree <root> start  --code R01.A02
CLI tree <root> note   --code R01.A02 --evidence research/claims/x/run-07.md
CLI tree <root> close  --code R01.A02 --status refuted --result "…" --evidence …
CLI tree <root> show                 # print ATTEMPTS.md (re-rendered)
CLI tree <root> list [--status running] [--under R01]
```

(`CLI` = `py scripts/research_os.py` from the plugin root; `../../scripts/` from inside a skill.)

## Integration rules

- **Phase skills**: any bounded try that could fail (a reduction strategy, a proof route, a model family, a rescue plan) is a node. Register it at the moment you commit to trying it; close it in the same breath as the verdict. Claims in `research/claims/<id>/` mirror to `C##` nodes; their five-verdict outcomes map to `proven/refuted/…`.
- **research-loop**: every tick's "execute one bounded step" runs inside a node — register (or `start` a registered one) before executing, `close`/`note` before the tick's write-back. A tick that cannot say which node it advanced is not a valid tick.
- **Verifications**: fresh-context checks (research-verifier) hang under the node they check as `V##`; their verdict closes the V-node, not the parent — the parent closes only on adjudication.
- **context digest** prints status totals, running nodes, and the last closed nodes; the full picture is always one `tree show` away.
- Duplicate hunch? `list --under R01` first — if an earlier sibling already died on the same idea, the tree just saved a re-run.
