#!/usr/bin/env python3
"""Stop guard: two things a turn may not end without.

1. Loop discipline (v2.7): an autonomous loop may not end its turn without a
   pending wakeup. If loop.mode == "autonomous" and loop.wakeup_pending is
   false, block the stop once and tell the model to either re-arm the wakeup
   (ScheduleWakeup, then `update --wakeup 1`) or stop the loop explicitly
   (`update --loop-mode manual`).

2. The close-of-turn gate (v2.8, narrative contract 6.3 / 6.9): if the project
   has an OPEN turn, call `turn finalize`. Finalize re-scans the inputs, writes
   the receipts the CLI is allowed to write by itself, and closes the turn --
   or refuses, in which case its CHRONICLE_REQUIRED text is echoed verbatim
   and the stop is blocked. A narrative-bearing input delta must be
   dispositioned before the session ends.

Both reasons are combined into the one block decision a Stop hook may emit.

stop_hook_active is respected: the second time around the stop is allowed
through, because a hook that blocks forever is worse than a missed gate. But
6.9 says the contract does not pretend that never happens -- a forced-through
stop with an OPEN turn writes a `stop_forced_through` incident, which the next
SessionStart surfaces as the repair path.

Fails open on any error.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def find_control(start: Path):
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".research-os" / "state.json").exists():
            return candidate / ".research-os"
    return None


def wakeup_reason(control: Path):
    try:
        state = json.loads((control / "state.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    loop = state.get("loop") or {}
    if loop.get("mode") == "autonomous" and not loop.get("wakeup_pending"):
        return (
            "loop.mode=autonomous but no wakeup is pending for this project. Before ending the "
            "turn either (a) re-arm: call ScheduleWakeup with the loop prompt, then "
            "`research_os.py update <root> --wakeup 1`; or (b) stop the loop explicitly: "
            "`research_os.py update <root> --loop-mode manual` and record why. "
            "An autonomous loop must never end silently."
        )
    return None


def chronicle_reason(control: Path):
    """Best-effort `turn finalize`. Returns CHRONICLE_REQUIRED text, or None."""
    try:
        import turn_runtime as tr
    except Exception:
        return None
    try:
        return tr.stop_gate(control)
    except Exception:
        return None


def note_forced_through(control: Path) -> None:
    try:
        import turn_runtime as tr

        turn = tr.read_turn(control)
        if not turn or turn.get("status") != "OPEN":
            return
        tr.write_incident(control, "stop_forced_through", {
            "turn_uuid": turn.get("turn_uuid"), "display_id": turn.get("display_id"),
            "manifests_without_receipt": tr.missing_receipts(control, turn),
            "note": "Stop was allowed through with an OPEN turn (stop_hook_active). "
                    "Contract 6.9: the next SessionStart must recover this turn.",
        })
    except Exception:
        return


def main() -> int:
    # The block reason quotes changed paths verbatim, and a research project's
    # bearing_globs are Chinese by default. On Windows stdout defaults to the
    # ANSI code page, which would hand the host mojibake instead of JSON.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        control = find_control(Path(payload.get("cwd") or os.getcwd()))
        if control is None:
            return 0
        if payload.get("stop_hook_active"):
            # Already blocked once this stop cycle; let it end, but record that
            # an unfinished turn went out the door (6.9).
            note_forced_through(control)
            return 0
        reasons = [r for r in (chronicle_reason(control), wakeup_reason(control)) if r]
        if reasons:
            print(json.dumps({"decision": "block", "reason": "\n\n".join(reasons)},
                             ensure_ascii=False))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
