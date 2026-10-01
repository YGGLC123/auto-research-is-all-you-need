#!/usr/bin/env python3
"""auto-research growth layer CLI ("learn").

Sediments verified experience into a two-tier library and runs its full
lifecycle: candidate -> trial -> active -> retired/superseded, driven by
helpful/harmful usage counters. Grounded in the 2026-07 community research:
quality gate first (DSPy/SkillWeaver/Bugbot), complete retirement loop
(Bugbot), itemized entries + deterministic merge, never LLM-rewrite (ACE),
two granularities stored apart (Agent KB), hard index budget (official
memory discipline).

Tiers:
  global  = <plugin>/library/            (travels with the package, git-backed)
  project = <root>/.research-os/learned/ (per-project; `promote` lifts to global)

Retrieval is the research-librarian subagent's job; the main controller only
ever sees INDEX.md summaries and the librarian's <=30-line digest.

stdlib-only. Exit codes: 0 ok, 1 contract refusal, 2 environment error.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

GROWTH_SCHEMA = "auto-research/growth-v1"
KINDS = {"playbook": "playbooks", "fix": "fixes", "snippet": "snippets"}
STATUSES = {"candidate", "trial", "active", "retired", "superseded"}
GATES = {"tested", "user-confirmed", "used-twice"}
PHASES = {"bootstrap", "discovery", "evidence", "theory", "method", "experiments",
          "artifacts", "manuscript", "review", "reproducibility", "loop", "collab"}
INDEX_BUDGET = 200
TRIAL_TO_ACTIVE_HELPFUL = 2
PLUGIN_LIBRARY = Path(__file__).resolve().parent.parent / "library"
FIGURE_BLOCK_HEADING = "## figure library"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def die(msg: str, code: int = 2):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(code)


def slugify(value: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60] or "entry"


def atomic_write(path: Path, text: str) -> None:
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def library_root(args) -> Path:
    if args.project:
        return Path(args.project).expanduser().resolve() / ".research-os" / "learned"
    return Path(__file__).resolve().parent.parent / "library"


def load_ledger(root: Path) -> dict:
    path = root / "growth.json"
    if not path.exists():
        return {"schema_version": GROWTH_SCHEMA, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"growth.json invalid JSON: {exc}", 1)
    if data.get("schema_version") != GROWTH_SCHEMA:
        die(f"unsupported growth schema {data.get('schema_version')!r}", 1)
    return data


def save_ledger(root: Path, data: dict) -> None:
    atomic_write(root / "growth.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    render_index(root, data, enforce_budget=False)


def entry_or_die(data: dict, eid: str) -> dict:
    entry = data["entries"].get(eid)
    if entry is None:
        die(f"unknown entry {eid!r}", 1)
    return entry


def transition(entry: dict, status: str, reason: str = "") -> None:
    entry["status"] = status
    entry["status_changed_at"] = utc_now()
    if reason:
        entry["status_reason"] = reason


def existing_figure_block(root: Path) -> str:
    """The `## figure library` section already on disk, verbatim.  The fallback
    when figure_router cannot be imported: a re-render may never be the reason a
    section disappears from the index."""
    path = root / "INDEX.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    start = text.find(FIGURE_BLOCK_HEADING)
    if start == -1:
        return ""
    return text[start:].rstrip("\n") + "\n"


def figure_block(root: Path) -> str:
    """The figure half of the library index.

    INDEX.md is shared: `growth.py` owns the playbook/fix/snippet ledger above it
    and `figure_router.py` owns the figure library below it.  A deterministic
    re-render of one half must not delete the other, so the block is regenerated
    from the figure library itself and only falls back to preserving what is on
    disk when that generator is unavailable.  Project-tier libraries have no
    figure half at all.
    """
    if root.resolve() != PLUGIN_LIBRARY.resolve():
        return ""
    try:
        import figure_router
        block = figure_router.index_block()
    except Exception:                                    # pragma: no cover
        return existing_figure_block(root)
    if not block.startswith(FIGURE_BLOCK_HEADING):       # pragma: no cover
        return existing_figure_block(root)
    return block.rstrip("\n") + "\n"


def render_index(root: Path, data: dict, enforce_budget: bool = True) -> int:
    """Deterministic re-render (never an LLM rewrite). Returns line count."""
    entries = data.get("entries", {})
    lines = [
        "# Growth Library Index (auto-generated — edit via `growth.py`, never by hand)",
        "",
        "Retrieval protocol: the research-librarian subagent reads this index, opens",
        "at most 5 entry bodies, and returns a <=30-line digest. The main controller",
        "never reads entry bodies directly (docs/context-hygiene.md).",
        "",
    ]
    order = {"active": 0, "trial": 1, "candidate": 2}
    for kind, folder in KINDS.items():
        live = [e for e in entries.values()
                if e.get("kind") == kind and e.get("status") in ("active", "trial", "candidate")]
        if not live:
            continue
        lines.append(f"## {folder}")
        for entry in sorted(live, key=lambda e: (order.get(e.get("status"), 9), e.get("id", ""))):
            phases = ",".join(entry.get("phases") or []) or "any"
            lines.append(f"- `{entry['id']}` [{entry['status']}; {phases}; "
                         f"+{entry.get('helpful', 0)}/-{entry.get('harmful', 0)}] {entry.get('title', '')}")
        lines.append("")
    retired = sum(1 for e in entries.values() if e.get("status") in ("retired", "superseded"))
    lines.append(f"Archive: {retired} retired/superseded entries remain in growth.json for provenance.")
    block = figure_block(root)
    if block:
        lines.append("")
        lines.extend(block.rstrip("\n").split("\n"))

    def assemble(budget_line: str) -> str:
        out = list(lines)
        out.insert(1, budget_line)
        return "\n".join(out) + "\n"

    # The budget counts the whole shared index, figure library included; iterate to
    # the fixed point so the printed number is the number of lines actually written.
    total = 0
    for _ in range(3):
        text = assemble(f"\nBudget: {total}/{INDEX_BUDGET} lines. "
                        "Over budget => merge or retire before adding.")
        if text.count("\n") == total:
            break
        total = text.count("\n")
    if enforce_budget and total > INDEX_BUDGET:
        die(f"INDEX would be {total} lines (> {INDEX_BUDGET}). The budget is the anti-bloat gate: "
            "merge or retire entries first (learn retire / consolidate), then re-render.", 1)
    atomic_write(root / "INDEX.md", text)
    return total


def cmd_propose(args) -> int:
    root = library_root(args)
    data = load_ledger(root)
    if not args.kind or args.kind not in KINDS:
        die(f"--kind required; one of {sorted(KINDS)}")
    if not args.title:
        die("--title required")
    if not (args.project_name and args.node):
        die("provenance required: --project-name and --node (tree code or run id) — "
            "unattributable experience is not admissible")
    bad_phases = set(args.phase or []) - PHASES
    if bad_phases:
        die(f"unknown phases {sorted(bad_phases)}; allowed: {sorted(PHASES)}")
    eid = args.id or slugify(args.title)
    if eid in data["entries"]:
        die(f"entry {eid!r} exists — update it via use/adopt, or supersede-and-propose a new id "
            "(deterministic merge, never silent overwrite)", 1)
    folder = KINDS[args.kind]
    rel_file = f"{folder}/{eid}.md" if args.kind != "snippet" else f"{folder}/{eid}/"
    body = args.content or ""
    if args.file:
        src = Path(args.file).expanduser()
        if not src.exists():
            die(f"--file {src} does not exist")
        body = src.read_text(encoding="utf-8")
    if args.kind != "snippet":
        if not body.strip():
            die("--content or --file required: an entry without a body is not reusable")
        front = (f"---\nid: {eid}\nkind: {args.kind}\nstatus: candidate\n"
                 f"phases: [{', '.join(args.phase or [])}]\n---\n\n# {args.title}\n\n")
        atomic_write(root / folder / f"{eid}.md", front + body.strip() + "\n")
    else:
        (root / folder / eid).mkdir(parents=True, exist_ok=True)
        if body.strip():
            atomic_write(root / folder / eid / "README.md", body.strip() + "\n")
    entry = {
        "id": eid, "kind": args.kind, "title": args.title, "file": rel_file,
        "status": "candidate", "phases": sorted(set(args.phase or [])),
        "helpful": 0, "harmful": 0,
        "provenance": [{
            "project": args.project_name, "node": args.node,
            "evidence": args.evidence or "", "ts": utc_now(),
        }],
        "created_at": utc_now(), "status_changed_at": utc_now(),
    }
    data["entries"][eid] = entry
    save_ledger(root, data)
    print(json.dumps({"ok": True, "id": eid, "status": "candidate",
                      "next": "pass a quality gate via `learn adopt` before it may be retrieved"},
                     ensure_ascii=False))
    return 0


def cmd_adopt(args) -> int:
    root = library_root(args)
    data = load_ledger(root)
    entry = entry_or_die(data, args.id)
    if entry["status"] != "candidate":
        die(f"{args.id} is {entry['status']}; only candidates are adopted", 1)
    if args.gate not in GATES:
        die(f"--gate required; one of {sorted(GATES)} — no verification signal, no library entry")
    if entry["kind"] == "snippet":
        selftest = args.selftest or ""
        if not selftest:
            die("adopting a snippet requires --selftest '<command>' (executable experience must prove itself)")
        proc = subprocess.run(selftest, shell=True, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=str(root))
        if proc.returncode != 0:
            die(f"selftest failed (exit {proc.returncode}): {(proc.stderr or proc.stdout)[-300:]}", 1)
        entry["selftest"] = selftest
    entry["quality_gate"] = args.gate
    if args.gate_note:
        entry["quality_gate_note"] = args.gate_note
    transition(entry, "trial", f"gate={args.gate}")
    save_ledger(root, data)
    print(json.dumps({"ok": True, "id": args.id, "status": "trial",
                      "next": f"{TRIAL_TO_ACTIVE_HELPFUL} helpful uses promote it to active automatically"},
                     ensure_ascii=False))
    return 0


def cmd_use(args) -> int:
    root = library_root(args)
    data = load_ledger(root)
    entry = entry_or_die(data, args.id)
    if entry["status"] in ("retired", "superseded"):
        die(f"{args.id} is {entry['status']}; a dead entry must not be applied — "
            "check INDEX for its replacement", 1)
    key = "harmful" if args.harmful else "helpful"
    entry[key] = int(entry.get(key, 0)) + 1
    uses = entry.setdefault("uses", [])
    uses.append({"ts": utc_now(), "verdict": key, "note": args.note or "",
                 "project": args.project_name or "", "node": args.node or ""})
    auto = None
    if entry["harmful"] >= max(entry["helpful"], 1) and entry["status"] in ("trial", "active"):
        transition(entry, "retired", "auto: harmful >= helpful (usage signal)")
        auto = "retired"
    elif entry["status"] == "trial" and entry["helpful"] >= TRIAL_TO_ACTIVE_HELPFUL:
        transition(entry, "active", "auto: trial passed usage threshold")
        auto = "active"
    save_ledger(root, data)
    print(json.dumps({"ok": True, "id": args.id, "helpful": entry["helpful"],
                      "harmful": entry["harmful"], "status": entry["status"],
                      "auto_transition": auto}, ensure_ascii=False))
    return 0


def cmd_promote(args) -> int:
    if not args.project:
        die("promote runs against a project tier: pass --project <root>")
    src_root = library_root(args)
    src = load_ledger(src_root)
    entry = entry_or_die(src, args.id)
    if entry["status"] not in ("trial", "active"):
        die(f"{args.id} is {entry['status']}; only trial/active entries promote", 1)
    projects = {p.get("project") for p in entry.get("provenance", [])}
    projects |= {u.get("project") for u in entry.get("uses", []) if u.get("project")}
    projects.discard("")
    if len(projects) < 2 and not args.force:
        die(f"promotion needs provenance/usage from >=2 projects (has: {sorted(projects)}); "
            "pass --force only with a recorded reason", 1)
    dst_root = Path(__file__).resolve().parent.parent / "library"
    dst = load_ledger(dst_root)
    if args.id in dst["entries"]:
        die(f"global library already has {args.id!r}; merge manually (deterministic), never overwrite", 1)
    src_file = src_root / entry["file"]
    dst_file = dst_root / entry["file"]
    if entry["kind"] == "snippet":
        import shutil
        shutil.copytree(src_file, dst_file)
    else:
        atomic_write(dst_file, src_file.read_text(encoding="utf-8"))
    promoted = dict(entry)
    promoted["promoted_from"] = str(src_root)
    promoted["promoted_at"] = utc_now()
    if args.force:
        promoted["promotion_forced_reason"] = args.force
    dst["entries"][args.id] = promoted
    save_ledger(dst_root, dst)
    transition(entry, "superseded", "promoted to global library")
    save_ledger(src_root, src)
    print(json.dumps({"ok": True, "id": args.id, "promoted_to": str(dst_root)}, ensure_ascii=False))
    return 0


def cmd_retire(args) -> int:
    root = library_root(args)
    data = load_ledger(root)
    entry = entry_or_die(data, args.id)
    if entry["status"] in ("retired", "superseded"):
        die(f"{args.id} already {entry['status']}", 1)
    if not args.reason:
        die("--reason required: retirement without a recorded reason is amnesia")
    transition(entry, "retired", args.reason)
    save_ledger(root, data)
    print(json.dumps({"ok": True, "id": args.id, "status": "retired"}, ensure_ascii=False))
    return 0


def cmd_list(args) -> int:
    root = library_root(args)
    data = load_ledger(root)
    entries = list(data["entries"].values())
    if args.status:
        entries = [e for e in entries if e.get("status") == args.status]
    if args.phase_filter:
        entries = [e for e in entries if args.phase_filter in (e.get("phases") or [])]
    if args.kind:
        entries = [e for e in entries if e.get("kind") == args.kind]
    print(json.dumps({"ok": True, "library": str(root), "count": len(entries),
                      "entries": entries}, ensure_ascii=False, indent=2))
    return 0


def cmd_render(args) -> int:
    root = library_root(args)
    data = load_ledger(root)
    total = render_index(root, data, enforce_budget=True)
    print(json.dumps({"ok": True, "index": str(root / "INDEX.md"),
                      "lines": total, "budget": INDEX_BUDGET}, ensure_ascii=False))
    return 0


def cmd_self_test(args) -> int:
    """Machine-checked: a growth re-render keeps the figure half of INDEX.md, the
    printed budget equals the file it wrote, and the budget still refuses to be
    exceeded."""
    import shutil
    import tempfile
    rows = []

    def check(name, ok, detail=""):
        rows.append({"name": name, "status": "PASS" if ok else "FAIL",
                     "detail": str(detail)[:200]})
        return bool(ok)

    # 1. the real plugin library. Re-rendering it is deliberately idempotent, so
    # running the self-test never leaves the tracked index different from before.
    tmp = Path(tempfile.mkdtemp(prefix="growth-selftest-"))
    try:
        generated = figure_block(PLUGIN_LIBRARY)
        check("figure block: generated from the figure library", bool(generated),
              "%d lines" % generated.count("\n"))
        for want in ("lib:figure:forest-ci@1", "lib:diagram:pipeline-lr@1",
                     "lib:palette:okabe-ito@1"):
            check("figure block: carries %s" % want, want in generated)

        before = (PLUGIN_LIBRARY / "INDEX.md").read_text(encoding="utf-8") \
            if (PLUGIN_LIBRARY / "INDEX.md").exists() else ""
        had_block = FIGURE_BLOCK_HEADING in before
        data = load_ledger(PLUGIN_LIBRARY)
        total = render_index(PLUGIN_LIBRARY, data, enforce_budget=True)
        after = (PLUGIN_LIBRARY / "INDEX.md").read_text(encoding="utf-8")
        check("render: a growth re-render keeps the `## figure library` section",
              (FIGURE_BLOCK_HEADING in after) or not had_block,
              "heading present=%s" % (FIGURE_BLOCK_HEADING in after))
        check("render: the printed budget equals the lines actually written",
              total == after.count("\n"),
              "reported %d, wrote %d" % (total, after.count("\n")))
        check("render: the index stays inside its %d-line budget" % INDEX_BUDGET,
              total <= INDEX_BUDGET, "%d lines" % total)
        render_index(PLUGIN_LIBRARY, data, enforce_budget=True)
        check("render: is deterministic (same ledger, same bytes)",
              after == (PLUGIN_LIBRARY / "INDEX.md").read_text(encoding="utf-8"))

        # 2. fallback path: an unknown library root preserves what it finds
        stray = tmp / "stray"
        stray.mkdir(parents=True, exist_ok=True)
        atomic_write(stray / "growth.json",
                     json.dumps({"schema_version": GROWTH_SCHEMA, "entries": {}}) + "\n")
        atomic_write(stray / "INDEX.md",
                     "# old\n\n%s (hand kept)\n\n- sentinel-entry\n"
                     % FIGURE_BLOCK_HEADING)
        check("preserve: the fallback returns the section it finds on disk",
              "sentinel-entry" in existing_figure_block(stray),
              "read back before the re-render")
        render_index(stray, load_ledger(stray), enforce_budget=True)
        stray_text = (stray / "INDEX.md").read_text(encoding="utf-8")
        check("render: a project-tier index grows no figure block",
              FIGURE_BLOCK_HEADING not in stray_text,
              "the figure library is plugin-global, not per project")

        # 3. the budget is a real gate
        fat = tmp / "fat"
        fat.mkdir(parents=True, exist_ok=True)
        entries = {}
        for i in range(INDEX_BUDGET + 20):
            eid = "e%03d" % i
            entries[eid] = {"id": eid, "kind": "fix", "title": "t", "status": "active",
                            "phases": [], "helpful": 0, "harmful": 0,
                            "provenance": [{"project": "p", "node": "n"}]}
        atomic_write(fat / "growth.json",
                     json.dumps({"schema_version": GROWTH_SCHEMA, "entries": entries}) + "\n")
        refused = False
        try:
            render_index(fat, load_ledger(fat), enforce_budget=True)
        except SystemExit:
            refused = True
        check("budget: an over-long index is refused, not silently written", refused)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    summary = {"PASS": 0, "FAIL": 0}
    for row in rows:
        summary[row["status"]] += 1
    ok = summary["FAIL"] == 0
    print(json.dumps({"ok": ok, "summary": summary, "checks": rows}, ensure_ascii=False,
                     indent=2))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth", description=__doc__)
    parser.add_argument("--project", metavar="ROOT",
                        help="operate on the project tier (<root>/.research-os/learned/) instead of the global library")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("propose", help="register a candidate entry (provenance mandatory)")
    p.add_argument("--id")
    p.add_argument("--kind", choices=sorted(KINDS))
    p.add_argument("--title")
    p.add_argument("--phase", action="append")
    p.add_argument("--content")
    p.add_argument("--file")
    p.add_argument("--project-name")
    p.add_argument("--node", help="tree code or run id this was learned from")
    p.add_argument("--evidence")
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("adopt", help="candidate -> trial through a quality gate")
    p.add_argument("--id", required=True)
    p.add_argument("--gate", choices=sorted(GATES))
    p.add_argument("--gate-note")
    p.add_argument("--selftest", help="snippet-only: command that must exit 0")
    p.set_defaults(func=cmd_adopt)

    p = sub.add_parser("use", help="record an application; counters drive the lifecycle")
    p.add_argument("--id", required=True)
    p.add_argument("--harmful", action="store_true")
    p.add_argument("--note")
    p.add_argument("--project-name")
    p.add_argument("--node")
    p.set_defaults(func=cmd_use)

    p = sub.add_parser("promote", help="lift a project-tier entry to the global library (>=2 projects)")
    p.add_argument("--id", required=True)
    p.add_argument("--force", metavar="REASON")
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("retire", help="retire an entry with a recorded reason")
    p.add_argument("--id", required=True)
    p.add_argument("--reason")
    p.set_defaults(func=cmd_retire)

    p = sub.add_parser("list", help="query the ledger")
    p.add_argument("--status", choices=sorted(STATUSES))
    p.add_argument("--phase-filter")
    p.add_argument("--kind", choices=sorted(KINDS))
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("render", help="re-render INDEX.md (budget-enforced, deterministic)")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("self-test", help="index rendering + figure-block preservation")
    p.set_defaults(func=cmd_self_test)

    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
