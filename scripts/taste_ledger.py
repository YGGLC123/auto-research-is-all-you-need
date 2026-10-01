#!/usr/bin/env python3
"""auto-research taste ledger -- the researcher's academic taste as sourced rules.

Models are poor judges of research taste (Anthropic's 2026 TASTE study: the best
model agreed with expert researchers 60% of the time against 77% between
experts), so this module never asks a model what good research looks like.  It
records what the *researcher* has already ruled -- a sentence they said, a
dated decision, a signed approval -- as an explicit rule with its source, and
checks what the AI changed against those rules.

Files (``.research-os/taste/``, written only by this CLI):
  rules.jsonl       append-only events (``add`` / ``retire``); the current rule
                    set is their fold.  A rule without a source is refused:
                    unattributable taste is the model's taste, not the user's.
  candidates.json   derived cache from ``harvest`` -- verdicts found in
                    decisions.md and in approved narrative commits that have
                    not yet become rules.  A candidate is never applied until a
                    human accepts it (``accept``).

A rule may carry a regular expression.  ``avoid`` rules with a pattern are
checked mechanically (story nodes the AI touched, and manuscript files);
every other rule is shown beside the changes it concerns as a reminder for
the human to judge -- the ledger drives the check, it never acquits.

stdlib-only.  Exit codes: 0 ok, 1 contract refusal, 2 environment/usage error.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros  # noqa: E402

EVENT_SCHEMA = "auto-research/taste-event-v1"
CANDIDATES_SCHEMA = "auto-research/taste-candidates-v1"
SCOPES = ("story", "writing", "figures", "ideas", "method", "all")
STANCES = ("avoid", "prefer")
SOURCE_KINDS = ("user", "decision", "approval")
RULE_ID_RE = re.compile(r"^T-(\d+)$")
DECISION_HEAD_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s+(.*)$")
# A decision line counts as a candidate verdict only when it says who ruled.
VERDICT_MARKERS = re.compile(r"用户|所有者|拍板|裁定|裁决|user-approved|user decided|"
                             r"the user|owner decided|approved by", re.I)
NODE_TEXT_FIELDS = ("title", "summary", "statement")
MAX_FILES_SCANNED = 400


class TasteError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def taste_dir(control: Path) -> Path:
    return control / "taste"


def rules_path(control: Path) -> Path:
    return taste_dir(control) / "rules.jsonl"


def candidates_path(control: Path) -> Path:
    return taste_dir(control) / "candidates.json"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def clip(text, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ============================================================ the ledger

def read_events(control: Path) -> list:
    path = rules_path(control)
    if not path.exists():
        return []
    out = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise TasteError("RULES_CORRUPT", f"rules.jsonl line {number}: {exc}")
    return out


def fold_rules(events: list, include_retired: bool = False) -> dict:
    rules = {}
    for event in events:
        kind = event.get("event")
        if kind == "add":
            rules[event["id"]] = dict(event, status="active")
        elif kind == "retire" and event.get("id") in rules:
            rules[event["id"]].update(status="retired", retired_at=event.get("at"),
                                      retire_reason=event.get("reason"))
    if not include_retired:
        rules = {k: v for k, v in rules.items() if v["status"] == "active"}
    return rules


def current_rules(control: Path, include_retired: bool = False) -> dict:
    return fold_rules(read_events(control), include_retired)


def _append_event(control: Path, event: dict) -> None:
    path = rules_path(control)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _next_id(events: list) -> str:
    numbers = [int(m.group(1)) for e in events for m in [RULE_ID_RE.match(str(e.get("id", "")))] if m]
    return "T-%02d" % ((max(numbers) if numbers else 0) + 1)


def add_rule(control: Path, text: str, scopes, stance: str, source_kind: str,
             source_ref: str = "", quote: str = "", pattern: str = "", by: str = "main") -> dict:
    text = (text or "").strip()
    if not text:
        raise TasteError("RULE_TEXT_EMPTY")
    scopes = [s.strip() for s in (scopes or ["all"]) if s and s.strip()]
    bad = [s for s in scopes if s not in SCOPES]
    if bad:
        raise TasteError("RULE_SCOPE_UNKNOWN", f"{bad}; allowed {list(SCOPES)}")
    if stance not in STANCES:
        raise TasteError("RULE_STANCE_UNKNOWN", f"{stance!r}; allowed {list(STANCES)}")
    if source_kind not in SOURCE_KINDS:
        raise TasteError("RULE_SOURCE_REQUIRED",
                         f"source kind must be one of {list(SOURCE_KINDS)}: taste the "
                         f"researcher never expressed is the model's taste")
    if source_kind == "user" and not (quote or "").strip():
        raise TasteError("RULE_SOURCE_REQUIRED", "a user rule carries the user's own words (--quote)")
    if source_kind in ("decision", "approval") and not (source_ref or "").strip():
        raise TasteError("RULE_SOURCE_REQUIRED", f"a {source_kind} rule names its record (--source-ref)")
    if pattern:
        try:
            re.compile(pattern, re.I)
        except re.error as exc:
            raise TasteError("RULE_PATTERN_INVALID", str(exc))
    with ros.hold_lock(control):
        events = read_events(control)
        for rule in fold_rules(events).values():
            if _norm(rule["text"]) == _norm(text):
                raise TasteError("RULE_DUPLICATE", f"{rule['id']} already says this")
        event = {"schema": EVENT_SCHEMA, "event": "add", "id": _next_id(events), "text": text,
                 "scope": scopes, "stance": stance, "pattern": pattern or None,
                 "source": {"kind": source_kind, "ref": source_ref or None,
                            "quote": clip(quote, 400) or None},
                 "by": by, "at": ros.utc_now()}
        _append_event(control, event)
    return dict(event, status="active")


def retire_rule(control: Path, rule_id: str, reason: str, by: str = "main") -> dict:
    if not (reason or "").strip():
        raise TasteError("RETIRE_REASON_REQUIRED")
    with ros.hold_lock(control):
        rules = current_rules(control)
        if rule_id not in rules:
            raise TasteError("RULE_NOT_ACTIVE", rule_id)
        event = {"schema": EVENT_SCHEMA, "event": "retire", "id": rule_id,
                 "reason": reason.strip(), "by": by, "at": ros.utc_now()}
        _append_event(control, event)
    return event


# ============================================================ harvest (derived)

def _decision_verdicts(control: Path) -> list:
    path = control / "decisions.md"
    if not path.exists():
        return []
    out, block, ordinal = [], None, {}
    for line in path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n"):
        head = DECISION_HEAD_RE.match(line)
        if head:
            date = head.group(1)
            ordinal[date] = ordinal.get(date, 0) + 1
            block = {"date": date, "title": head.group(2).strip(),
                     "ref": "decision:%s-%d" % (date, ordinal[date])}
            if VERDICT_MARKERS.search(block["title"]):
                out.append({"source_kind": "decision", "source_ref": block["ref"],
                            "date": date, "quote": block["title"]})
            continue
        text = line.strip().lstrip("-*").strip()
        if block and text and VERDICT_MARKERS.search(text):
            out.append({"source_kind": "decision", "source_ref": block["ref"],
                        "date": block["date"], "quote": clip(text, 300),
                        "context": block["title"]})
    return out


def _approved_commits(control: Path) -> list:
    commits = control / "narrative" / "commits"
    if not commits.is_dir():
        return []
    out = []
    for path in sorted(commits.glob("*.json")):
        try:
            commit = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        approvals = {k: v for k, v in (commit.get("approval_refs") or {}).items() if v}
        if not approvals and commit.get("forced_status") != "confirmed":
            continue
        quote = commit.get("counterfactual") or commit.get("message") or ""
        out.append({"source_kind": "approval", "source_ref": "commit:" + commit["id"][:12],
                    "date": (commit.get("at") or "")[:10], "quote": clip(quote, 300),
                    "context": ", ".join("%s=%s" % kv for kv in sorted(approvals.items()))
                    or "forced cause confirmed"})
    return out


def harvest(control: Path, write: bool = True) -> dict:
    """Collect verdicts the researcher already made that are not yet rules."""
    rules = current_rules(control, include_retired=True)
    taken = {(r["source"].get("ref"), _norm(r["source"].get("quote") or "")) for r in rules.values()}
    taken_refs = {r["source"].get("ref") for r in rules.values() if r["source"].get("ref")}
    found = _decision_verdicts(control) + _approved_commits(control)
    candidates = []
    for index, item in enumerate(found, 1):
        if (item["source_ref"], _norm(item["quote"])) in taken:
            continue
        item = dict(item, id="cand-%03d" % index,
                    already_ruled_on=item["source_ref"] in taken_refs)
        candidates.append(item)
    payload = {"schema": CANDIDATES_SCHEMA, "built_at": ros.utc_now(),
               "count": len(candidates), "candidates": candidates}
    if write:
        with ros.hold_lock(control):
            ros.atomic_write(candidates_path(control),
                             json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def accept_candidate(control: Path, candidate_id: str, text: str, scopes, stance: str,
                     pattern: str = "", by: str = "user") -> dict:
    payload = ros.read_json_file(candidates_path(control), None) or harvest(control, write=False)
    match = [c for c in payload.get("candidates") or [] if c.get("id") == candidate_id]
    if not match:
        raise TasteError("CANDIDATE_NOT_FOUND", f"{candidate_id} (run `harvest` first)")
    cand = match[0]
    return add_rule(control, text, scopes, stance, cand["source_kind"], cand["source_ref"],
                    cand.get("quote", ""), pattern, by)


# ============================================================ checks

def _applies(rule: dict, scope: str) -> bool:
    scopes = rule.get("scope") or ["all"]
    return scope in scopes or "all" in scopes


def check_nodes(rules: dict, nodes: dict, only_ids=None, scope: str = "story") -> dict:
    """``{"flags": [...], "reminders": [...]}``.  A flag = an ``avoid`` rule's
    pattern found in a node's reader text; a reminder = a rule a human must judge."""
    flags, reminders = [], []
    for rule in sorted(rules.values(), key=lambda r: r["id"]):
        if not _applies(rule, scope):
            continue
        pattern = rule.get("pattern")
        if rule.get("stance") != "avoid" or not pattern:
            reminders.append({"rule": rule["id"], "text": rule["text"], "stance": rule["stance"]})
            continue
        regex = re.compile(pattern, re.I)
        for node_id in sorted(nodes):
            if only_ids is not None and node_id not in only_ids:
                continue
            node = nodes[node_id] or {}
            for field in NODE_TEXT_FIELDS:
                text = str(node.get(field) or "")
                found = regex.search(text)
                if found:
                    start = max(0, found.start() - 24)
                    flags.append({"rule": rule["id"], "rule_text": rule["text"], "node": node_id,
                                  "title": clip(node.get("title"), 60), "field": field,
                                  "match": found.group(0),
                                  "excerpt": clip(text[start:found.end() + 24], 90)})
                    break
    return {"flags": flags, "reminders": reminders}


def check_files(rules: dict, root: Path, patterns) -> dict:
    """Scan manuscript-type files (scope ``writing``) line by line."""
    files, seen = [], set()
    for pattern in patterns or []:
        for name in glob.glob(str(root / pattern), recursive=True):
            path = Path(name)
            if path.is_file() and path not in seen:
                seen.add(path)
                files.append(path)
    files = sorted(files)[:MAX_FILES_SCANNED]
    active = [r for r in rules.values()
              if r.get("stance") == "avoid" and r.get("pattern") and _applies(r, "writing")]
    flags = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for rule in active:
            regex = re.compile(rule["pattern"], re.I)
            for number, line in enumerate(lines, 1):
                if line.lstrip().startswith("%"):
                    continue                      # LaTeX comment: not reader text
                found = regex.search(line)
                if found:
                    flags.append({"rule": rule["id"], "file": str(path.relative_to(root)),
                                  "line": number, "match": found.group(0),
                                  "excerpt": clip(line.strip(), 110)})
    return {"files_scanned": len(files), "flags": flags}


def _narrative():
    import narrative  # noqa: WPS433 -- same-dir sibling
    return narrative


def changed_node_ids(control: Path, ref: str = "main", since=None) -> "set | None":
    """Node ids added or rewritten between ``since`` and head; None = all nodes."""
    if not since:
        return None
    narr = _narrative()
    head = narr.load_head_snapshot(control, ref)
    base = narr.commit_snapshot(control, narr.resolve_commitish(control, since))
    out = set()
    for op in narr.snapshot_diff(base, head):
        if op.get("op") in ("add_node", "patch_fields") and op.get("id") in head["nodes"]:
            out.add(op["id"])
    return out


# ============================================================ CLI

def emit(payload) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0 if payload.get("ok", True) else 1


def _control(args) -> Path:
    return ros.locate_control(Path(args.project or "."))


def cmd_add(args) -> int:
    rule = add_rule(_control(args), args.text, args.scope, args.stance, args.source_kind,
                    args.source_ref or "", args.quote or "", args.pattern or "", args.by)
    return emit({"ok": True, "rule": rule})


def cmd_retire(args) -> int:
    return emit({"ok": True, "event": retire_rule(_control(args), args.id, args.reason, args.by)})


def cmd_list(args) -> int:
    rules = current_rules(_control(args), include_retired=args.all)
    return emit({"ok": True, "count": len(rules), "rules": list(rules.values())})


def cmd_harvest(args) -> int:
    payload = harvest(_control(args), write=not args.dry_run)
    shown = payload["candidates"][: args.limit]
    return emit({"ok": True, "count": payload["count"], "shown": len(shown),
                 "candidates": shown,
                 "note": "a candidate becomes a rule only through `accept`, by the researcher"})


def cmd_accept(args) -> int:
    rule = accept_candidate(_control(args), args.id, args.text, args.scope, args.stance,
                            args.pattern or "", args.by)
    return emit({"ok": True, "rule": rule})


def cmd_check(args) -> int:
    control = _control(args)
    rules = current_rules(control)
    narr = _narrative()
    nodes = narr.load_head_snapshot(control, args.ref).get("nodes") or {}
    only = changed_node_ids(control, args.ref, args.since)
    story = check_nodes(rules, nodes, only)
    files = check_files(rules, control.parent, args.files) if args.files else None
    return emit({"ok": True, "rules": len(rules), "nodes_checked": len(only) if only is not None
                 else len(nodes), "story_flags": story["flags"], "reminders": story["reminders"],
                 "file_check": files})


# ============================================================ self-test

def run_self_test() -> int:
    import shutil
    rows, failed = [], []

    def ok(name, condition, detail=""):
        rows.append(name)
        if not condition:
            failed.append({"name": name, "detail": str(detail)[:300]})

    tmp = Path(tempfile.mkdtemp(prefix="taste-selftest-"))
    try:
        control = tmp / "proj" / ".research-os"
        control.mkdir(parents=True)
        (control / "decisions.md").write_text(
            "# Research Decisions\n\n## 2026-08-01 route decision\n- Route B approved.\n"
            "- 用户明确：正文不写防御性段落。\n\n## 2026-08-02 用户拍板: 只投 RFS\n- 其余照旧。\n",
            encoding="utf-8")
        for code, call in (
                ("RULE_SOURCE_REQUIRED", lambda: add_rule(control, "x", ["story"], "avoid", "model")),
                ("RULE_SOURCE_REQUIRED", lambda: add_rule(control, "x", ["story"], "avoid", "user")),
                ("RULE_SOURCE_REQUIRED", lambda: add_rule(control, "x", ["story"], "avoid", "decision")),
                ("RULE_SCOPE_UNKNOWN", lambda: add_rule(control, "x", ["vibes"], "avoid", "user", quote="q")),
                ("RULE_PATTERN_INVALID", lambda: add_rule(control, "x", ["story"], "avoid", "user",
                                                          quote="q", pattern="(")),
        ):
            try:
                call()
                ok("refused: " + code, False, "accepted")
            except TasteError as exc:
                ok("refused: " + code, exc.code == code, exc.code)
        rule = add_rule(control, "No defensive wording in reader text", ["story", "writing"], "avoid",
                        "user", quote="不要防御性写作", pattern=r"limitation|局限|失败")
        ok("add assigns T-01 and records the user's words", rule["id"] == "T-01"
           and rule["source"]["quote"] == "不要防御性写作", rule)
        try:
            add_rule(control, "no defensive   wording in reader text", ["story"], "avoid", "user", quote="q")
            ok("duplicate refused", False)
        except TasteError as exc:
            ok("duplicate refused", exc.code == "RULE_DUPLICATE", exc.code)
        prefer = add_rule(control, "Every main figure carries an overview", ["figures"], "prefer",
                          "decision", source_ref="decision:2026-08-01-1", quote="overview first")
        nodes = {"C-1": {"title": "Main result", "statement": "A limitation of the design is X."},
                 "C-2": {"title": "Clean", "statement": "The price of search is positive."},
                 "C-3": {"title": "失败的尝试", "statement": ""}}
        story = check_nodes(current_rules(control), nodes)
        flagged = sorted(f["node"] for f in story["flags"])
        ok("avoid pattern flags exactly the offending nodes", flagged == ["C-1", "C-3"], flagged)
        ok("only rules in scope are reminders", [r["rule"] for r in story["reminders"]] == [], story)
        limited = check_nodes(current_rules(control), nodes, only_ids={"C-2"})
        ok("only_ids restricts the scan to what the AI touched", limited["flags"] == [], limited)
        paper = tmp / "proj" / "paper"
        paper.mkdir()
        (paper / "sec1.tex").write_text("We show X.\n% limitation: kept in a comment\n"
                                        "One limitation is Y.\n", encoding="utf-8")
        files = check_files(current_rules(control), tmp / "proj", ["paper/*.tex"])
        ok("manuscript scan skips LaTeX comments, flags reader lines",
           [(f["file"].replace("\\", "/"), f["line"]) for f in files["flags"]] == [("paper/sec1.tex", 3)],
           files)
        cands = harvest(control)
        quotes = [c["quote"] for c in cands["candidates"]]
        ok("harvest finds verdict lines and verdict headings",
           any("防御性" in q for q in quotes) and any("只投 RFS" in q for q in quotes), quotes)
        ok("harvest skips lines that name no ruler", not any("Route B" in q for q in quotes), quotes)
        ok("candidates cache written", candidates_path(control).exists())
        first = next(c for c in cands["candidates"] if "防御性" in c["quote"])
        accepted = accept_candidate(control, first["id"], "Say it positively", ["writing"], "prefer")
        ok("accept carries the decision as the source", accepted["source"]["ref"] == first["source_ref"]
           and accepted["by"] == "user", accepted)
        again = harvest(control, write=False)
        ok("accepted candidate no longer offered",
           not any(c["quote"] == first["quote"] for c in again["candidates"]), again)
        retire_rule(control, prefer["id"], "superseded by the figure contract")
        ok("retire removes from the active set", prefer["id"] not in current_rules(control))
        ok("retired rule kept in history",
           current_rules(control, include_retired=True)[prefer["id"]]["status"] == "retired")
        try:
            retire_rule(control, prefer["id"], "again")
            ok("double retire refused", False)
        except TasteError as exc:
            ok("double retire refused", exc.code == "RULE_NOT_ACTIVE", exc.code)
        lines = rules_path(control).read_text(encoding="utf-8").splitlines()
        ok("ledger is append-only events", len(lines) == 4 and all(json.loads(l)["schema"] == EVENT_SCHEMA
                                                                   for l in lines), len(lines))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return emit({"ok": not failed, "checks": len(rows), "failed": len(failed),
                 "failures": failed, "rows": rows})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taste_ledger.py",
                                     description="the researcher's taste as sourced rules")
    parser.add_argument("--project", help="project root or .research-os (default: cwd)")
    parser.add_argument("--self-test", dest="self_test", action="store_true")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("add", help="record a rule the researcher stated")
    p.add_argument("--text", required=True, help="the rule, in the project's language")
    p.add_argument("--scope", action="append", choices=SCOPES)
    p.add_argument("--stance", choices=STANCES, default="avoid")
    p.add_argument("--pattern", help="regex checked mechanically (avoid rules)")
    p.add_argument("--source-kind", dest="source_kind", choices=SOURCE_KINDS, required=True)
    p.add_argument("--source-ref", dest="source_ref")
    p.add_argument("--quote", help="the researcher's own words")
    p.add_argument("--by", default="main")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("retire", help="retire a rule (history kept)")
    p.add_argument("--id", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--by", default="main")
    p.set_defaults(func=cmd_retire)

    p = sub.add_parser("list", help="current rules")
    p.add_argument("--all", action="store_true", help="include retired rules")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("harvest", help="verdicts already made that are not yet rules")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--dry-run", dest="dry_run", action="store_true")
    p.set_defaults(func=cmd_harvest)

    p = sub.add_parser("accept", help="turn a harvested verdict into a rule")
    p.add_argument("--id", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--scope", action="append", choices=SCOPES)
    p.add_argument("--stance", choices=STANCES, default="avoid")
    p.add_argument("--pattern")
    p.add_argument("--by", default="user")
    p.set_defaults(func=cmd_accept)

    p = sub.add_parser("check", help="check story nodes (and manuscript files) against the rules")
    p.add_argument("--ref", default="main")
    p.add_argument("--since", help="only nodes changed since this commit/ref")
    p.add_argument("--files", action="append", help="glob under the project root, e.g. paper/*.tex")
    p.set_defaults(func=cmd_check)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test or args.cmd == "self-test":
        return run_self_test()
    if not args.cmd:
        build_parser().print_help()
        return 2
    try:
        return int(args.func(args) or 0)
    except TasteError as exc:
        return emit({"ok": False, "error": exc.code, "detail": exc.detail})


if __name__ == "__main__":
    raise SystemExit(main())
