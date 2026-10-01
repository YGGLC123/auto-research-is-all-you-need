#!/usr/bin/env python3
"""PostToolUse hook: the event-registration backstop of contract 6.10.

The model can always write a narrative-bearing file without telling the turn
runtime, and auto-compact can wipe the chronicler context that would have
remembered. So every Edit/Write/MultiEdit/NotebookEdit whose target matches
narrative/config.json.bearing_globs registers a synthetic input event -- pure
path matching, no judgement, no content read.

The event makes the next InputManifest bearing even if the file itself is
outside the scanned input domain (a derived path, an excluded binary), which
is the whole point: the gate closes on evidence the model cannot route around.

Silent and fail-open by construction: this runs after every file write and
must never slow one down or block one.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        if payload.get("tool_name") not in WRITE_TOOLS:
            return 0
        tool_input = payload.get("tool_input") or {}
        raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if not raw:
            return 0
        import turn_runtime as tr

        target = Path(str(raw))
        start = target.parent if target.parent != target else Path(os.getcwd())
        control = tr.ros.find_project_upwards(start)
        if control is None:
            control = tr.ros.find_project_upwards(Path(payload.get("cwd") or os.getcwd()))
        if control is None:
            return 0
        root = tr.project_root(control)
        try:
            rel = target.resolve().relative_to(root.resolve()).as_posix()
        except Exception:
            return 0
        if rel.startswith(".research-os/"):
            return 0  # derived domain never flows back in (6.2)
        cfg = tr.load_narrative_config(control)
        if not tr.any_glob_match(rel, cfg["bearing_globs"]):
            return 0
        tr.register_event(control, "file_write", {"path": rel, "tool": payload.get("tool_name")},
                          source="hook_post_tool")
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
