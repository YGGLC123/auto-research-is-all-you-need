# Context Hygiene — keeping the control plane decision-grade

The orchestrator's context window is the scarcest resource in the system. Every long
document read inline displaces the state that steers the run — the observed failure
mode is the mainline paraphrasing a 300-line Pro transcript section by section and
drifting off-plan. This document is the normative umbrella for every return-size rule
in the plugin; the librarian cap ([growth-layer.md](growth-layer.md)) and the task
return cap ([collab-protocol.md](collab-protocol.md)) are instances of it.

## Rules

1. **Decision-grade only.** The mainline holds verdicts, deltas, gate states, pointers,
   and next actions. Raw material — transcripts, paper bodies, long logs, library
   entry bodies, bulk search results — never enters the orchestrator's context.
2. **The 40-line rule.** Any external material longer than ~40 lines is digested by a
   subagent, which returns a **bounded summary + disk pointer** to the full text. The
   mainline reads the summary; it follows the pointer only by dispatching again.
3. **Pro returns (the repeat offender).** The harvest agent returns to the mainline
   only the CONTROL block + verdict + key delta — ≤30 lines total. The full transcript
   lands on disk as a run evidence file; verifier/harvester agents process it in their
   own contexts, never the mainline's.
4. **Subagent return contract.** Every fan-out prompt declares its return format and a
   line cap. A subagent brief without a return contract is malformed — fix the prompt,
   not the overflow after the fact.
5. **Violation self-check.** The moment the mainline notices itself reading a long
   document section by section, that is the signal: stop, dispatch a subagent, resume
   from its bounded summary.

## Mechanical enforcement

Discipline that can be mechanized is mechanized. The rules above are the
spec; these are the teeth:

| Rule | Enforcement | Where |
|---|---|---|
| Task returns are bounded | `task` return digest truncated/rejected by the CLI at ≤30 lines; full artifacts stay on disk | collab bus (P3), [collab-protocol.md](collab-protocol.md) |
| Library retrieval is bounded | research-librarian subagent returns ≤30 lines; the orchestrator never opens entry bodies | growth layer (P2), [growth-layer.md](growth-layer.md) |
| Logs are never re-read in full | `events status` prints a per-reader unread catalog; `events read` yields increments only | implemented — [loop-protocol.md](loop-protocol.md), `../scripts/research_os.py` |

Skill playbooks cite this document instead of restating it. Where a rule here and a
skill's phrasing disagree, this document wins.
