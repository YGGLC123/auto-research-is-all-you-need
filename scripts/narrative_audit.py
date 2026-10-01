#!/usr/bin/env python3
"""auto-research narrative tree -- story-admission audit with two checkers.

A narrative node is a piece of the story the paper tells its reader: a claim
about the subject, a piece of evidence, a theorem, or a structural move
(motivation / mechanism / boundary / implication).  Backfills and busy writing
turns let other things in -- wording decisions ("the closing sentence is now
X"), to-dos, status notes, notes about the tree itself, fragments nobody
outside the project can read, duplicates.  The reader sees them as noise; the
drift readings count them as story.

`narrative audit` asks two independent checkers -- the Claude CLI and the
Codex CLI, different model families -- to classify every node, merges the two
verdicts, and writes a report.  A node is *actionable* only when both agree it
is not story; disagreements are listed for the controller.  With
`--emit-patch` the agreed non-story leaves become `detach_node DROPPED` ops in
a NarrativePatch (contract §13) -- a proposal the controller applies through
`narrative apply` / `commit --pending-patch`; nothing here writes the tree.

    py scripts/narrative_audit.py --project <root> [--ref main]
        [--checkers claude,codex] [--emit-patch <file>] [--batch 50]
    py scripts/narrative_audit.py --self-test

stdlib-only, Python >= 3.9.  Exit codes: 0 ok, 1 refusal, 2 environment/usage.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros  # noqa: E402
import narrative as nar  # noqa: E402

AUDIT_SCHEMA = "auto-research/narrative-audit-v1"
CATEGORIES = ("story", "editorial", "todo", "status", "meta", "unreadable", "duplicate")
NON_STORY = ("editorial", "todo", "status", "meta")
CATEGORY_CN = {"story": "故事", "editorial": "编辑决定", "todo": "待办", "status": "状态记录",
               "meta": "流程备注", "unreadable": "读不懂", "duplicate": "重复"}
DEFAULT_CLAUDE_MODEL = "opus"
MIN_CONFIDENCE = 0.6
STATEMENT_CHARS = 700
DISALLOWED_TOOLS = "Bash,PowerShell,Edit,Write,MultiEdit,NotebookEdit,Agent,Task,WebFetch,WebSearch"

RUBRIC = """你是一篇论文"叙事树"的审查员。叙事树里的每个节点都应当是论文讲给读者的故事的一部分：对研究对象的一个断言、一条证据、一个定理，或一步结构性论证（动机、机制、边界、含义）。请把下面每个节点归入一类：
- story：读者会在论文正文里读到的实质内容。
- editorial：关于措辞、定稿句、命名、格式、引用方式的编辑决定（例如"收束句定稿为……""'桥'这一措辞弃用"）。
- todo：待办、计划、"待补 / 未算出 / 待读数"之类的过程备注。
- status：进度或状态记录（"已落盘""设计稿尚未落盘""在飞"）。
- meta：关于叙事树本身、写作流程、分工或版本的备注。
- unreadable：不了解项目内部代号的读者读不懂（残留代码、缩写、无上下文的片段）。
- duplicate：与另一个节点实质重复，给出 duplicate_of。
只看内容不看层级；有下级的节点也照样分类。每个节点给 confidence（0 到 1）和不超过 40 字的 reason。
只输出一个 JSON 对象，不要任何别的文字：
{"verdicts":[{"id":"…","category":"…","confidence":0.9,"reason":"…","duplicate_of":null}]}
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "duplicate_of": {"type": ["string", "null"]},
                },
                "required": ["id", "category", "confidence", "reason", "duplicate_of"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


# ============================================================ nodes -> prompt

def _title(node_id: str, node: dict) -> str:
    t = str(node.get("title") or "")
    if node_id and t.startswith(node_id):
        rest = t[len(node_id):].lstrip(" :：·-—")
        if rest:
            t = rest
    return t or "（无标题）"


def dump_nodes(snapshot: dict) -> list:
    """Every non-root node with its path, in DFS order."""
    nodes = snapshot.get("nodes") or {}
    root = snapshot.get("root")
    out = []

    def walk(nid, path):
        for kid in (nodes.get(nid) or {}).get("children") or []:
            body = nodes.get(kid)
            if not body:
                continue
            out.append({"id": kid, "path": path, "node_type": body.get("node_type"),
                        "title": _title(kid, body), "summary": str(body.get("summary") or ""),
                        "statement": str(body.get("statement") or ""),
                        "children": [k for k in body.get("children") or [] if k in nodes],
                        "state": body.get("state") or {}})
            walk(kid, path + [_title(kid, body)])
    if root in nodes:
        walk(root, [])
    return out


def render_batch(rows: list) -> str:
    parts = []
    for r in rows:
        stmt = re.sub(r"\s+", " ", r["statement"]).strip()
        if len(stmt) > STATEMENT_CHARS:
            stmt = stmt[:STATEMENT_CHARS - 1] + "…"
        parts.append("### " + r["id"] + "\n路径: " + (" › ".join(r["path"]) or "（一级章节）")
                     + "\n标题: " + r["title"] + "\n简介: " + re.sub(r"\s+", " ", r["summary"]).strip()
                     + "\n正文: " + (stmt or "（空）"))
    return "\n\n".join(parts)


def build_prompt(rows: list) -> str:
    return RUBRIC + "\n共 " + str(len(rows)) + " 个节点，必须每个都给出裁决：\n\n" + render_batch(rows)


def parse_verdicts(text: str) -> list:
    """Find the JSON object in a checker's reply; tolerate prose around it."""
    text = str(text or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in checker output")
    body = json.loads(text[start:end + 1])
    rows = body.get("verdicts") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise ValueError("checker output has no verdicts[]")
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        cat = str(r.get("category") or "").strip().lower()
        if cat not in CATEGORIES:
            continue
        try:
            conf = float(r.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0
        out.append({"id": str(r["id"]), "category": cat, "confidence": max(0.0, min(1.0, conf)),
                    "reason": str(r.get("reason") or "")[:120],
                    "duplicate_of": r.get("duplicate_of") or None})
    return out


# ============================================================ checkers

def _find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    pattern = os.path.expandvars("%USERPROFILE%/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe")
    import glob
    hits = glob.glob(pattern)
    return sorted(hits, key=lambda p: [int(x) for x in re.findall(r"(\d+)", p)])[-1] if hits else None


class ClaudeChecker:
    name = "claude"

    def __init__(self, model: str = DEFAULT_CLAUDE_MODEL, workdir: Path | None = None):
        self.model = model
        self.cli = _find_claude()
        self.workdir = workdir or Path(tempfile.mkdtemp(prefix="ar-audit-claude-"))

    def available(self) -> bool:
        return bool(self.cli)

    def __call__(self, prompt: str) -> list:
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        proc = subprocess.run([self.cli, "-p", "--model", self.model, "--output-format", "text",
                               "--max-turns", "1", "--disallowedTools", DISALLOWED_TOOLS],
                              input=prompt.encode("utf-8"), cwd=str(self.workdir), env=env,
                              capture_output=True, timeout=1800)
        if proc.returncode != 0 and not proc.stdout.strip():
            raise RuntimeError("claude exited %s: %s" % (proc.returncode, proc.stderr.decode("utf-8", "replace")[:300]))
        return parse_verdicts(proc.stdout.decode("utf-8", "replace"))


class CodexChecker:
    name = "codex"

    def __init__(self, model: str | None = None, workdir: Path | None = None):
        self.model = model
        self.cli = shutil.which("codex")
        self.workdir = workdir or Path(tempfile.mkdtemp(prefix="ar-audit-codex-"))

    def available(self) -> bool:
        return bool(self.cli)

    def __call__(self, prompt: str) -> list:
        schema = self.workdir / "schema.json"
        schema.write_text(json.dumps(OUTPUT_SCHEMA), encoding="utf-8")
        out = self.workdir / "last.txt"
        if out.exists():
            out.unlink()
        cmd = [self.cli, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
               "-C", str(self.workdir), "--output-schema", str(schema), "-o", str(out)]
        if self.model:
            cmd += ["-m", self.model]
        proc = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, timeout=1800)
        text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else proc.stdout.decode("utf-8", "replace")
        if not text.strip():
            raise RuntimeError("codex produced no output: %s" % proc.stderr.decode("utf-8", "replace")[:300])
        return parse_verdicts(text)


class FakeChecker:
    """Self-test checker: a fixed verdict table."""

    def __init__(self, name: str, table: dict):
        self.name = name
        self.table = table
        self.calls = 0

    def available(self) -> bool:
        return True

    def __call__(self, prompt: str) -> list:
        self.calls += 1
        ids = re.findall(r"^### (\S+)$", prompt, flags=re.M)
        return [{"id": i, "category": self.table.get(i, ("story", 0.9, ""))[0],
                 "confidence": self.table.get(i, ("story", 0.9, ""))[1],
                 "reason": self.table.get(i, ("story", 0.9, ""))[2], "duplicate_of": None}
                for i in ids]


# ============================================================ merge + report

def run_checkers(rows: list, checkers: list, batch: int = 50) -> dict:
    """{checker_name: {node_id: verdict}} plus per-checker errors."""
    results, errors = {}, {}
    for checker in checkers:
        verdicts = {}
        try:
            for i in range(0, len(rows), batch):
                for v in checker(build_prompt(rows[i:i + batch])):
                    verdicts[v["id"]] = v
        except Exception as exc:
            errors[checker.name] = "%s: %s" % (type(exc).__name__, exc)
        results[checker.name] = verdicts
    return {"verdicts": results, "errors": errors}


def merge(rows: list, results: dict, min_conf: float = MIN_CONFIDENCE) -> dict:
    """Two-key merge: agreed / disagreed / missing, per node."""
    names = list(results.keys())
    merged = []
    for r in rows:
        per = {}
        for n in names:
            v = results[n].get(r["id"])
            if v:
                per[n] = v
        cats = {n: v["category"] for n, v in per.items()}
        distinct = set(cats.values())
        if len(per) == len(names) and len(distinct) == 1:
            cat = distinct.pop()
            conf = min(v["confidence"] for v in per.values())
            status = "agreed" if conf >= min_conf else "agreed_low_confidence"
        elif len(per) < len(names):
            cat, conf, status = (next(iter(distinct)) if len(distinct) == 1 else "story"), 0.0, "missing"
        else:
            cat, conf, status = "story", 0.0, "disagreed"
        merged.append({"id": r["id"], "title": r["title"], "path": r["path"], "children": r["children"],
                       "category": cat, "confidence": round(conf, 2), "status": status,
                       "per_checker": per})
    return {"checkers": names, "rows": merged}


def actionable(merged: dict) -> list:
    """Agreed, confident, non-story, and a leaf: safe to propose dropping."""
    return [m for m in merged["rows"]
            if m["status"] == "agreed" and m["category"] in NON_STORY and not m["children"]]


def report_lines(merged: dict, errors: dict, limit: int = 40) -> list:
    rows = merged["rows"]
    names = merged["checkers"]
    lines = ["叙事树故事审查 · 检查员：" + " + ".join(names) + " · 节点 " + str(len(rows))]
    for n, e in errors.items():
        lines.append("！检查员 " + n + " 失败：" + e[:100])
    counts = {}
    for m in rows:
        key = m["category"] if m["status"].startswith("agreed") else m["status"]
        counts[key] = counts.get(key, 0) + 1
    lines.append("一致判定：" + "，".join("%s %d" % (CATEGORY_CN.get(k, k), v) for k, v in counts.items()
                                       if k in CATEGORIES) +
                 "；分歧 %d；缺失 %d" % (counts.get("disagreed", 0), counts.get("missing", 0)))
    act = actionable(merged)
    lines.append("可移出故事（双方一致、末端节点）：%d" % len(act))
    for m in act[:14]:
        lines.append("  - %s · %s · %s" % (CATEGORY_CN[m["category"]], m["title"],
                                          next(iter(m["per_checker"].values()))["reason"][:40]))
    if len(act) > 14:
        lines.append("  … 还有 %d 项，见 JSON" % (len(act) - 14))
    parents = [m for m in rows if m["status"] == "agreed" and m["category"] in NON_STORY and m["children"]]
    if parents:
        lines.append("一致非故事但带下级（需人工处理）：" + "、".join(m["title"] for m in parents[:6]))
    unread = [m for m in rows if m["status"] == "agreed" and m["category"] == "unreadable"]
    if unread:
        lines.append("双方都判读不懂（转写债）：" + "、".join(m["title"] for m in unread[:8]))
    dups = [m for m in rows if m["status"] == "agreed" and m["category"] == "duplicate"]
    if dups:
        lines.append("双方都判重复：" + "、".join(m["title"] for m in dups[:8]))
    dis = [m for m in rows if m["status"] == "disagreed"]
    if dis:
        lines.append("分歧（%d）：" % len(dis))
        for m in dis[:8]:
            lines.append("  - %s · " % m["title"] + " / ".join(
                "%s=%s" % (n, CATEGORY_CN.get(v["category"], v["category"])) for n, v in m["per_checker"].items()))
    return lines[:limit]


def emit_patch(control: Path, ref: str, merged: dict, audit_ref: str) -> dict:
    head = nar.read_ref(control, ref)
    snapshot = nar.load_head_snapshot(control, ref)
    ops = []
    for m in actionable(merged):
        reason = next(iter(m["per_checker"].values()))["reason"][:80]
        ops.append({"op": "detach_node", "id": m["id"], "narrative_disposition": "DROPPED",
                    "disposition_meta": {"reason": "story audit: %s — %s" % (CATEGORY_CN[m["category"]], reason),
                                         "audit": audit_ref}})
    return {"schema": nar.PATCH_SCHEMA, "project": control.parent.name, "ref": ref,
            "base_commit_id": head, "base_tree_hash": nar.tree_hash(snapshot),
            "tree_ops": ops, "todo_proposals": [], "generated_at": nar.now_iso(),
            "source": "narrative_audit"}


def run_audit(control: Path, ref: str, checkers: list, batch: int = 50, out_dir: Path | None = None,
              patch_path: Path | None = None, min_conf: float = MIN_CONFIDENCE) -> dict:
    nar.require_store(control)
    snapshot = nar.load_head_snapshot(control, ref)
    rows = dump_nodes(snapshot)
    raw = run_checkers(rows, checkers, batch=batch)
    merged = merge(rows, raw["verdicts"], min_conf=min_conf)
    out_dir = out_dir or (nar.ndir(control) / "audits")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = nar.now_iso().replace(":", "").replace("-", "")[:13]
    base = out_dir / ("%s-%s" % (stamp, ref))
    record = {"schema": AUDIT_SCHEMA, "project": control.parent.name, "ref": ref,
              "commit": nar.read_ref(control, ref), "at": nar.now_iso(),
              "checkers": merged["checkers"], "errors": raw["errors"], "rows": merged["rows"]}
    lines = report_lines(merged, raw["errors"])
    ros.atomic_write(base.with_suffix(".json"), nar.dump_json(record))
    ros.atomic_write(base.with_suffix(".md"), "\n".join(lines) + "\n")
    result = {"ok": True, "audit": str(base.with_suffix(".json")), "report": str(base.with_suffix(".md")),
              "nodes": len(rows), "actionable": len(actionable(merged)),
              "disagreed": sum(1 for m in merged["rows"] if m["status"] == "disagreed"),
              "errors": raw["errors"], "lines": lines}
    if patch_path is not None:
        patch = emit_patch(control, ref, merged, str(base.with_suffix(".json")))
        ros.atomic_write(Path(patch_path), nar.dump_json(patch))
        result["patch"] = str(patch_path)
        result["patch_ops"] = len(patch["tree_ops"])
    return result


# ============================================================ self-test

def self_test() -> int:
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    tmp = Path(tempfile.mkdtemp(prefix="ar-audit-"))
    try:
        control = tmp / "proj" / ".research-os"
        control.mkdir(parents=True)
        buffer, saved = io.StringIO(), sys.stdout
        sys.stdout = buffer
        try:
            nar.cmd_init(argparse.Namespace(
                root=control.parent, root_id="N-0", title="临时项目", thesis="临时论点",
                summary="", venue="RFS", message=None, session="W-a", at="2026-08-26T00:00:00Z",
                turn_uuid=None, manifest_id=None))
        finally:
            sys.stdout = saved

        def claim(nid, title, statement):
            return {"id": nid, "node_type": "claim",
                    "identity_key": {"subject": nid, "target": "returns", "predicate": "p-" + nid,
                                     "quantifier": "all", "domain": "US equities", "conditions": [],
                                     "polarity": "positive"},
                    "title": title, "summary": "", "statement": statement,
                    "state": {"epistemic": "PENDING", "narrative": "ACTIVE"}}
        nar.commit_from_ops(control, "main", [
            {"op": "add_node", "node": claim("C-001", "动机", "现有研究假定附录无人读。"), "parent": "N-0"},
            {"op": "add_node", "node": claim("C-002", "机制", "阅读量取决于审稿流程。"), "parent": "C-001"},
            {"op": "add_node", "node": claim("C-003", "收束句降格定稿", "收束句定稿为「与之共同补齐」；「桥」弃用。"), "parent": "N-0"},
            {"op": "add_node", "node": claim("C-004", "待补读数", "d_p 未算出，待读数。"), "parent": "N-0"},
            {"op": "add_node", "node": claim("C-005", "版本备注", "v2.3 已落盘。"), "parent": "N-0"},
        ], {"message": "seed", "at": "2026-08-26T01:00:00Z",
            "author": {"session": "W-a", "role": "main"}, "origin": "live",
            "approval_refs": {"overthrow": None, "forced_cause": None}})

        snapshot = nar.load_head_snapshot(control, "main")
        rows = dump_nodes(snapshot)
        check("dump lists every non-root node with path", [r["id"] for r in rows] == ["C-001", "C-002", "C-003", "C-004", "C-005"]
              and rows[1]["path"] == ["动机"])
        prompt = build_prompt(rows[:2])
        check("prompt carries rubric + nodes, JSON-only instruction",
              "story" in prompt and "### C-001" in prompt and "只输出一个 JSON" in prompt)
        check("parse tolerates prose around the JSON",
              parse_verdicts('好的。{"verdicts":[{"id":"C-001","category":"Story","confidence":0.8,"reason":"x","duplicate_of":null}]} 完')[0]["category"] == "story")

        a = FakeChecker("alpha", {"C-003": ("editorial", 0.95, "措辞定稿"), "C-004": ("todo", 0.9, "待读数"),
                                  "C-005": ("status", 0.5, "版本"), "C-001": ("meta", 0.9, "误判")})
        b = FakeChecker("beta", {"C-003": ("editorial", 0.9, "定稿句"), "C-004": ("todo", 0.85, "未算出"),
                                 "C-005": ("status", 0.9, "落盘")})
        patch_path = tmp / "audit-patch.json"
        result = run_audit(control, "main", [a, b], batch=2, patch_path=patch_path)
        check("both checkers were called in batches", a.calls == 3 and b.calls == 3)
        rec = json.loads(Path(result["audit"]).read_text(encoding="utf-8"))
        by = {m["id"]: m for m in rec["rows"]}
        check("agreement -> agreed non-story", by["C-003"]["status"] == "agreed" and by["C-003"]["category"] == "editorial"
              and by["C-004"]["status"] == "agreed")
        check("disagreement never becomes actionable", by["C-001"]["status"] == "disagreed" and by["C-001"]["category"] == "story")
        check("low confidence is agreed_low_confidence, not actionable", by["C-005"]["status"] == "agreed_low_confidence")
        check("agreed story stays story", by["C-002"]["status"] == "agreed" and by["C-002"]["category"] == "story")
        patch = json.loads(patch_path.read_text(encoding="utf-8"))
        ids = [op["id"] for op in patch["tree_ops"]]
        check("patch drops exactly the agreed non-story leaves", ids == ["C-003", "C-004"]
              and all(op["op"] == "detach_node" and op["narrative_disposition"] == "DROPPED" for op in patch["tree_ops"]))
        check("a parent is never dropped by the audit", "C-001" not in ids)
        preview, _, _ = nar.apply_ops(nar.load_head_snapshot(control, "main"), patch["tree_ops"], control)
        check("the patch applies cleanly to the head snapshot", "C-003" not in (preview["nodes"]["N-0"]["children"])
              and not nar.validate_snapshot(preview))
        check("report is <= 40 lines and names the drops", len(result["lines"]) <= 40
              and any("收束句降格定稿" in l for l in result["lines"]) and any("分歧" in l for l in result["lines"]))
        check("report + json land under narrative/audits", Path(result["report"]).exists()
              and Path(result["audit"]).parent.name == "audits")
        errs = run_checkers(rows, [FakeChecker("ok", {}), type("Bad", (), {"name": "bad", "available": lambda s: True,
                                                                          "__call__": lambda s, p: (_ for _ in ()).throw(RuntimeError("boom"))})()])
        check("a failing checker is recorded, not fatal", "bad" in errs["errors"] and "boom" in errs["errors"]["bad"])
        check("real checkers discoverable (informational)", True,
              "claude=%s codex=%s" % (bool(_find_claude()), bool(shutil.which("codex"))))
    except Exception as exc:  # pragma: no cover
        check("self-test ran to the end", False, "%s: %s" % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [c for c in checks if not c["ok"]]
    print(json.dumps({"ok": not failed, "checks": len(checks), "failed": len(failed),
                      "rows": checks if failed else [c["check"] for c in checks]},
                     ensure_ascii=False, indent=2))
    return 1 if failed else 0


# ============================================================ CLI

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="narrative_audit.py",
                                     description="story-admission audit of a narrative tree, two checkers")
    parser.add_argument("--project")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--checkers", default="claude,codex", help="comma list: claude, codex")
    parser.add_argument("--claude-model", dest="claude_model", default=DEFAULT_CLAUDE_MODEL)
    parser.add_argument("--codex-model", dest="codex_model", default=None)
    parser.add_argument("--batch", type=int, default=50)
    parser.add_argument("--min-confidence", dest="min_conf", type=float, default=MIN_CONFIDENCE)
    parser.add_argument("--emit-patch", dest="emit_patch", help="write a NarrativePatch of agreed drops here")
    parser.add_argument("--out-dir", dest="out_dir")
    parser.add_argument("--self-test", dest="self_test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.project:
        ros.die("--project is required (or --self-test)", 2)
    control = ros.locate_control(Path(args.project))
    checkers = []
    for name in [x.strip() for x in args.checkers.split(",") if x.strip()]:
        if name == "claude":
            checkers.append(ClaudeChecker(model=args.claude_model))
        elif name == "codex":
            checkers.append(CodexChecker(model=args.codex_model))
        else:
            ros.die("unknown checker %r (claude, codex)" % name, 2)
    missing = [c.name for c in checkers if not c.available()]
    if missing:
        ros.die("checker CLI not found: %s" % ", ".join(missing), 2)
    try:
        result = run_audit(control, args.ref, checkers, batch=args.batch,
                           out_dir=Path(args.out_dir) if args.out_dir else None,
                           patch_path=Path(args.emit_patch) if args.emit_patch else None,
                           min_conf=args.min_conf)
    except nar.NarrativeError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "detail": exc.detail}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    lines = result.pop("lines")
    print("\n".join(lines))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
