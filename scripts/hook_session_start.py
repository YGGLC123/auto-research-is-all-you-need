#!/usr/bin/env python3
"""SessionStart hook: the turn/lease half of the narrative contract 6.7.

Runs AFTER research_os.py context (hooks.json orders them), so a session that
resumes into an unfinished turn sees, in this order: the project context, then
the runtime state it must repair before doing anything else.

It only reports and sets flags. The mandatory first step -- rehydrating the
chronicler and disposing of the open manifests -- belongs to the main thread;
a hook that tried to do it would be writing narrative state from inside a
session-start callback, which the contract forbids.

Silent when there is no project. Fails open (exit 0) on any error: a hook bug
must never brick a session.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    try:
        import turn_runtime as tr
    except Exception:
        return 0
    control = None
    try:
        start = Path(payload.get("cwd") or os.getcwd())
        control = tr.ros.find_project_upwards(start)
        if control is None:
            return 0  # no project here: stay quiet
        result = tr.session_start(control)
        lines = result.get("lines") or []
        if lines:
            print("== auto-research turn runtime ==")
            for line in lines:
                print(line)
    except Exception:
        pass
    try:
        # Role fleet digest (docs/role-fleet.md 4 / 6.1): hot roles awaiting
        # rehydration, unanswered dispatches, stale memories. Fail-open like
        # the rest of this hook.
        if control is not None:
            import role_runtime as rr
            role_lines = rr.status_lines(control)
            if role_lines:
                print("== auto-research role fleet ==")
                for line in role_lines:
                    print(line)
    except Exception:
        pass
    try:
        # Research graph digest (docs/v3-contract.md 6): one line read straight
        # out of graph/current.json, and only where an edge ledger exists -- a
        # project that never linked anything stays silent. A second line
        # GRAPH_REBUILD_REQUIRED appears when the materialised projection has
        # fallen behind the event stream; the main thread's first step is then
        # `graph rebuild`, because a hook may not write the relation layer from
        # inside a session-start callback. session_lines() swallows its own
        # errors too.
        if control is not None:
            import research_graph as rg
            for line in rg.session_lines(control):
                print(line)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
