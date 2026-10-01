#!/usr/bin/env python3
"""PreToolUse guard: mechanical enforcement of the state-write discipline.

Blocks Edit/Write/MultiEdit/NotebookEdit calls that target .research-os
control files (state.json, tree.json, events.jsonl, sessions.json,
cursors.json, ATTEMPTS.md, .lock) and tells the model to use the CLI
instead. decisions.md and everything else stays hand-editable.

Since v3 the relation layer joins them: .research-os/graph/*.json
(current.json, objects.json, the transaction journal, the snapshots) and
graph/edges.jsonl are written by ONE transaction inside the project write
lock. A hand edit there does not merely lose an entry -- it silently
desynchronises the materialised projection from the append-only event
stream, which is exactly the failure current.json exists to make impossible
(docs/v3-contract.md 0 and 8.1).

Reads the hook payload from stdin; emits a PreToolUse deny decision on a
match, stays silent otherwise. Fails open (exit 0) on any parse error so a
guard bug never bricks the session.
"""

import json
import re
import sys

# Matches control files directly under .research-os AND inside its frozen
# snapshots (checkpoint copies are audit artifacts — equally hand-edit-proof).
PROTECTED = re.compile(
    r"[\\/]\.research-os[\\/](?:.*[\\/])?(state\.json|tree\.json|events\.jsonl|sessions\.json|"
    r"cursors\.json|ATTEMPTS\.md|state\.v1\.bak\.json|\.lock)$"
)
# v3-contract 0: the whole graph/ subtree is transaction-owned.
GRAPH_PROTECTED = re.compile(
    r"[\\/]\.research-os[\\/](?:.*[\\/])?graph[\\/](?:.*[\\/])?[^\\/]+\.(?:json|jsonl|log)$"
)
# 3.1: the taste ledger is the researcher's own rulings, appended by one CLI;
# a hand edit there is a rule the researcher never stated.
TASTE_PROTECTED = re.compile(
    r"[\\/]\.research-os[\\/](?:.*[\\/])?taste[\\/][^\\/]+\.(?:json|jsonl)$"
)
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

TASTE_REASON = (
    "{path} belongs to the taste ledger: every rule there carries the researcher's own words "
    "or a decision record, appended by one CLI. A hand-written rule is taste nobody stated. "
    "Use `py <plugin>/scripts/taste_ledger.py` (add / accept / retire) instead."
)

GRAPH_REASON = (
    "{path} belongs to the research graph's relation layer: edges.jsonl is an append-only "
    "event stream and current.json / objects.json are projections of it, rebuilt inside one "
    "locked transaction (journal -> temp file -> fsync -> atomic rename). Editing either by "
    "hand leaves the projection disagreeing with the stream -- GRAPH_REBUILD_REQUIRED at "
    "best, a silently wrong coverage report at worst. Use `py <plugin>/scripts/"
    "research_graph.py` (link / retract / derive / index / rebuild / snapshot) instead."
)
STATE_REASON = (
    "{path} is an auto-research control file: it is written only by the state CLI "
    "(validate-before-write, atomic, locked). Use `py <plugin>/scripts/research_os.py` "
    "(update / tree / event / blocker / asset / session / events) instead of editing it by hand."
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool = payload.get("tool_name", "")
    if tool not in WRITE_TOOLS:
        return 0
    tool_input = payload.get("tool_input") or {}
    path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if not path:
        return 0
    if GRAPH_PROTECTED.search(path):
        reason = GRAPH_REASON.format(path=path)
    elif TASTE_PROTECTED.search(path):
        reason = TASTE_REASON.format(path=path)
    elif PROTECTED.search(path):
        reason = STATE_REASON.format(path=path)
    else:
        return 0
    print(json.dumps({
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
