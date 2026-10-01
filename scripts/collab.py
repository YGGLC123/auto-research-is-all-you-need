#!/usr/bin/env python3
"""auto-research cross-host collaboration bus (K-task mailbox).

Codex and Claude Code collaborate on the same project through numbered,
file-backed tasks under `.research-os/collab/tasks/` — the durable mailbox
lane — plus a direct headless-exec fast lane (`codex exec` / `claude -p`)
that MUST still book a K number first. Protocol: docs/collab-protocol.md.

Context hygiene is enforced here, not advised: `task return --digest` is
hard-capped at DIGEST_MAX_LINES; full artifacts go to disk, the mailbox
carries pointers.

Reuses research_os for locking, events, and session identity (same dir
import). stdlib-only. Exit codes: 0 ok, 1 contract refusal, 2 environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros  # noqa: E402

TASK_SCHEMA = "auto-research/task-v1"
TASK_STATUSES = {"open", "claimed", "done", "failed", "returned"}
TO_HOSTS = {"codex", "claude-code", "any"}
LANES = {"mailbox", "direct"}
DIGEST_MAX_LINES = 30


def tasks_dir(control: Path) -> Path:
    return control / "collab" / "tasks"


def load_task(control: Path, kid: str) -> dict:
    path = tasks_dir(control) / f"{kid}.json"
    if not path.exists():
        ros.die(f"unknown task {kid!r}", 1)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        ros.die(f"{kid}.json invalid JSON: {exc}", 1)
    raise AssertionError


def save_task(control: Path, task: dict) -> None:
    ros.atomic_write(tasks_dir(control) / f"{task['id']}.json",
                     json.dumps(task, ensure_ascii=False, indent=2) + "\n")


def next_task_id(control: Path) -> str:
    best = 0
    directory = tasks_dir(control)
    if directory.exists():
        for path in directory.glob("K*.json"):
            try:
                best = max(best, int(path.stem[1:]))
            except ValueError:
                continue
    return f"K{best + 1:02d}"


def require_session(control: Path) -> str:
    session = ros.current_session_id(control)
    if not session:
        ros.die("no registered session: run `research_os.py session ensure <root> --intent …` first "
                "(every collab actor carries a W## badge)", 1)
    return session


def cmd_post(args) -> int:
    control = ros.locate_control(args.root)
    if not (control / "state.json").exists():
        ros.die(f"no project state under {control}; bootstrap first")
    session = require_session(control)
    if not args.title or not args.spec:
        ros.die("task post requires --title and --spec (the spec must be self-contained: "
                "the executor gets NO conversational context)")
    if args.to not in TO_HOSTS:
        ros.die(f"--to must be one of {sorted(TO_HOSTS)}")
    if args.lane not in LANES:
        ros.die(f"--lane must be one of {sorted(LANES)}")
    kid = next_task_id(control)
    task = {
        "schema_version": TASK_SCHEMA,
        "id": kid,
        "from_session": session,
        "to_host": args.to,
        "lane": args.lane,
        "title": args.title,
        "spec": args.spec,
        "inputs": args.input or [],
        "expected_artifact": args.expect or "",
        "tree_node": args.node or "",
        "status": "open",
        "posted_at": ros.utc_now(),
        "claimed_by": None, "claimed_at": None,
        "finished_at": None, "result": None,
    }
    for rel in task["inputs"]:
        if not (control.parent / rel).exists():
            ros.die(f"input {rel!r} does not exist under the project root — "
                    "a spec pointing at missing inputs is not self-contained", 1)
    save_task(control, task)
    ros.append_event(control, {"event": "task_posted", "summary": f"{kid} -> {args.to} [{args.lane}]: {args.title}",
                               "task": kid})
    print(json.dumps({"ok": True, "id": kid, "lane": args.lane,
                      "note": ("direct lane: execute now via headless exec, still through claim/return"
                               if args.lane == "direct" else
                               "mailbox lane: the target host's next poll will pick it up")},
                     ensure_ascii=False))
    return 0


def cmd_claim(args) -> int:
    control = ros.locate_control(args.root)
    session = require_session(control)
    task = load_task(control, args.id)
    if task["status"] != "open":
        ros.die(f"{args.id} is {task['status']}; only open tasks can be claimed", 1)
    host = ros.detect_host("auto")
    if task["to_host"] not in ("any", host):
        ros.die(f"{args.id} is addressed to {task['to_host']}, this host is {host}; "
                "claiming across hosts breaks the lane accounting", 1)
    task.update({"status": "claimed", "claimed_by": session, "claimed_at": ros.utc_now()})
    save_task(control, task)
    ros.append_event(control, {"event": "task_claimed", "summary": f"{args.id} by {session} [{host}]",
                               "task": args.id})
    print(json.dumps({"ok": True, "id": args.id, "claimed_by": session}, ensure_ascii=False))
    return 0


def cmd_return(args) -> int:
    control = ros.locate_control(args.root)
    session = require_session(control)
    task = load_task(control, args.id)
    if task["status"] != "claimed":
        ros.die(f"{args.id} is {task['status']}; only claimed tasks can be returned", 1)
    if task.get("claimed_by") != session:
        ros.die(f"{args.id} was claimed by {task.get('claimed_by')}, you are {session}", 1)
    status = "failed" if args.failed else "done"
    if not args.digest:
        ros.die("--digest required: the poster's context is protected — "
                "summarize outcome + artifact pointers, never raw output")
    digest_lines = args.digest.strip().splitlines()
    if len(digest_lines) > DIGEST_MAX_LINES:
        ros.die(f"digest is {len(digest_lines)} lines (> {DIGEST_MAX_LINES}). Hard cap, not advice: "
                "write the full material to disk, point at it, resubmit a bounded digest", 1)
    artifacts = args.artifact or []
    if status == "done" and not artifacts:
        ros.die("a successful return names at least one --artifact path (disk is the delivery channel)")
    for rel in artifacts:
        if not (control.parent / rel).exists():
            ros.die(f"claimed artifact {rel!r} does not exist — a return may not point at nothing", 1)
    task.update({"status": status, "finished_at": ros.utc_now(),
                 "result": {"digest": args.digest.strip(), "artifacts": artifacts}})
    save_task(control, task)
    ros.append_event(control, {"event": "task_returned" if status == "done" else "task_failed",
                               "summary": f"{args.id} [{status}] {digest_lines[0][:120]}",
                               "task": args.id, "artifacts": artifacts})
    print(json.dumps({"ok": True, "id": args.id, "status": status}, ensure_ascii=False))
    return 0


def cmd_ack(args) -> int:
    control = ros.locate_control(args.root)
    session = require_session(control)
    task = load_task(control, args.id)
    if task["status"] not in ("done", "failed"):
        ros.die(f"{args.id} is {task['status']}; only done/failed tasks can be acknowledged", 1)
    task["status"] = "returned"
    task["acked_by"] = session
    task["acked_at"] = ros.utc_now()
    save_task(control, task)
    print(json.dumps({"ok": True, "id": args.id, "status": "returned"}, ensure_ascii=False))
    return 0


def cmd_list(args) -> int:
    control = ros.locate_control(args.root)
    directory = tasks_dir(control)
    tasks = []
    if directory.exists():
        for path in sorted(directory.glob("K*.json")):
            try:
                tasks.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    host = ros.detect_host("auto")
    if args.inbox:
        tasks = [t for t in tasks if t.get("status") == "open" and t.get("to_host") in ("any", host)]
    if args.status:
        tasks = [t for t in tasks if t.get("status") == args.status]
    if args.to:
        tasks = [t for t in tasks if t.get("to_host") == args.to]
    slim = [{k: t.get(k) for k in ("id", "title", "to_host", "lane", "status",
                                   "from_session", "claimed_by", "tree_node")} for t in tasks]
    print(json.dumps({"ok": True, "host": host, "count": len(slim), "tasks": slim},
                     ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collab", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("post", help="book a K task (mandatory before ANY cross-host work, incl. direct exec)")
    p.add_argument("root", type=Path)
    p.add_argument("--to", required=True, choices=sorted(TO_HOSTS))
    p.add_argument("--lane", default="mailbox", choices=sorted(LANES))
    p.add_argument("--title", required=True)
    p.add_argument("--spec", required=True, help="self-contained instructions (executor has no chat context)")
    p.add_argument("--input", action="append", help="project-relative input path (must exist)")
    p.add_argument("--expect", help="expected artifact description")
    p.add_argument("--node", help="attempt-tree node this task serves")
    p.set_defaults(func=cmd_post)

    p = sub.add_parser("claim", help="claim an open task addressed to this host")
    p.add_argument("root", type=Path)
    p.add_argument("--id", required=True)
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser("return", help="deliver: artifacts on disk + digest (hard-capped)")
    p.add_argument("root", type=Path)
    p.add_argument("--id", required=True)
    p.add_argument("--digest", help=f"outcome summary, max {DIGEST_MAX_LINES} lines (CLI-enforced)")
    p.add_argument("--artifact", action="append", help="project-relative delivered path (must exist)")
    p.add_argument("--failed", action="store_true")
    p.set_defaults(func=cmd_return)

    p = sub.add_parser("ack", help="poster acknowledges a done/failed task (closes the loop)")
    p.add_argument("root", type=Path)
    p.add_argument("--id", required=True)
    p.set_defaults(func=cmd_ack)

    p = sub.add_parser("list", help="query the mailbox (--inbox = open tasks for this host)")
    p.add_argument("root", type=Path)
    p.add_argument("--inbox", action="store_true")
    p.add_argument("--status", choices=sorted(TASK_STATUSES))
    p.add_argument("--to", choices=sorted(TO_HOSTS))
    p.set_defaults(func=cmd_list)

    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    control = ros.locate_control(Path(args.root))
    if args.command in ("post", "claim", "return", "ack") and control.exists():
        with ros.hold_lock(control):
            return args.func(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
