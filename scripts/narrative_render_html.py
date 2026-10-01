#!/usr/bin/env python3
"""auto-research narrative axis view -- single-file HTML renderer (U track).

`narrative.py render --view axis` emits text; this module emits the *interactive*
narrative-tree page (UI草图要求_叙事轴_20260825.md + the 2026-08-26 six-point
revision):

    逐层展开   = nothing is pre-expanded; every fold is an in-place subtree
                 update (no full re-render, no scroll/selection reset);
    只留文本   = the window shows titles, briefs and plain-Chinese state words;
                 ids / hashes / codes live behind a folded "技术细节";
    本地简介   = an expanded node shows its brief right under its title, a
                 leaf opens to its full text; the right pane holds the rest;
    可拖拽比例 = a splitter drags the right pane (persisted per project);
    多种结构   = 树形 (outline tree) / 目录 (Miller columns) / 轴线 (chapter
                 chain + one chapter's tree), plus optional numbering + search;
    可以在旁边提问 = the right pane carries a question box that emits a question
                 pack (artifact republish -> download -> clipboard).

Output is ONE file: no CDN, no web font, no network of any kind, openable from
`file://` and equally valid as a published artifact page (a complete document,
doctype first, which is what `artifact.publish(html)` requires).

The page never writes research state.  Edits accumulate into a `NarrativePatch
v1` (contract §13) that the user exports and the CLI validates -- the browser
only ever produces a proposal.

Public API (the `--html` forwarding target):
    render_axis_html(control, ref="main", out_path=None)
        -> receipt dict when `out_path` is given, else the page as a string

Standalone:
    py scripts/narrative_render_html.py --project <root> [--ref main] --out <file>
    py scripts/narrative_render_html.py --self-test

stdlib-only, Python >= 3.9.  Exit codes: 0 ok, 1 refusal, 2 environment/usage.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros  # noqa: E402
import narrative as nar  # noqa: E402

VIEW_SCHEMA = "auto-research/narrative-axis-view-v1"
QUESTION_SCHEMA = "auto-research/narrative-question-v1"
AXIS_DEPTH = nar.AXIS_DEFAULT_DEPTH
MAX_ANNOTATIONS_PER_NODE = 24
MAX_BLAME_PER_NODE = 12
MAX_EXCLUSIONS = 40
SIZE_BUDGET = 400 * 1024


# ============================================================ payload assembly

def _subtree_hashes(snapshot: dict) -> dict:
    """rev_hash per node (§2) plus a subtree hash = sha over the DFS list of
    (id, rev_hash), so a question can name exactly what it was asked about."""
    nodes = snapshot.get("nodes") or {}
    out: dict = {}

    def walk(nid: str) -> list:
        node = nodes.get(nid)
        if not node:
            return []
        rows = [[nid, nar.rev_hash(node)]]
        for child in node.get("children") or []:
            rows.extend(walk(child))
        out[nid] = {"rev": rows[0][1], "subtree": nar.sha_of(rows)}
        return rows

    root = snapshot.get("root")
    if root:
        walk(root)
    # detached-but-present nodes (MERGED / DROPPED bodies kept in the map)
    for nid, node in nodes.items():
        if nid not in out:
            out[nid] = {"rev": nar.rev_hash(node), "subtree": nar.rev_hash(node)}
    return out


def collect_annotations(control: Path) -> dict:
    """§10 -- append-only Q&A / notes / reviews, indexed by node."""
    directory = nar.ndir(control) / "annotations"
    index: dict = {}
    if not directory.exists():
        return index
    for path in sorted(directory.glob("*/*.json")):
        body = ros.read_json_file(path, {}) or {}
        node_id = body.get("node_id") or path.parent.name
        row = {"annotation_id": body.get("annotation_id") or path.stem,
               "kind": body.get("kind"), "question": body.get("question"),
               "answer": body.get("answer"), "by": body.get("by"),
               "at": body.get("at"), "commit_id": body.get("commit_id"),
               "supersedes": body.get("supersedes")}
        index.setdefault(node_id, []).append(row)
    for node_id, rows in index.items():
        rows.sort(key=lambda r: str(r.get("at") or ""))
        if len(rows) > MAX_ANNOTATIONS_PER_NODE:
            index[node_id] = rows[-MAX_ANNOTATIONS_PER_NODE:]
    return index


def _touched_ids(commit: dict) -> set:
    fn = getattr(nar, "_commit_touched_ids", None)
    if fn:
        return fn(commit)
    out = set()
    for op in commit.get("ops") or []:
        for key in ("id", "parent", "new_parent", "into"):
            value = op.get(key)
            if isinstance(value, str):
                out.add(value)
            elif isinstance(value, dict) and value.get("id"):
                out.add(value["id"])
        if op.get("op") == "add_node":
            out.add((op.get("node") or {}).get("id"))
        out.update(op.get("ids") or [])
    return {x for x in out if x}


_FIELD_CN = {"title": "标题", "summary": "简介", "statement": "正文", "basis_refs": "依据",
             "state_meta": "结算条件", "disposition_meta": "去向", "lineage": "谱系"}
_EP_CN = {"HELD": "已成立", "KILLED": "已证否", "PENDING": "待定"}
_NA_CN = {"ACTIVE": "在用", "DROPPED": "已弃", "DEMOTED": "已降级", "MERGED": "已并入"}
_CAUSE_CN = {"none": None, "forced": "受迫", "unforced": "主动", "unknown": "原因未记"}
_BACKFILL_RE = re.compile(r"^backfill\s+frame-\d+\s*<-\s*(.+)$")
_CJK_RE = re.compile(r"[一-鿿]")


def _op_targets(op: dict) -> set:
    out = set()
    for key in ("id", "new_parent", "into"):
        value = op.get(key)
        if isinstance(value, str):
            out.add(value)
        elif isinstance(value, dict) and value.get("id"):
            out.add(value["id"])
    if op.get("op") == "add_node":
        out.add((op.get("node") or {}).get("id"))
        if op.get("parent"):
            out.add(op["parent"])
    out.update(op.get("ids") or [])
    for part in op.get("parts") or []:
        if isinstance(part, dict) and part.get("id"):
            out.add(part["id"])
    return {x for x in out if x}


_SENT_RE = re.compile(r"[^。！？；\n]+[。！？；]?|\n")
DIFF_BUDGET = 520          # chars of statement diff carried per history entry
FIELD_BUDGET = 160         # chars kept per before/after value


def _sentences(text: str) -> list:
    text = str(text or "").replace("**", "")
    # CJK enders always end a sentence; ASCII enders only before whitespace / end
    # (so "4.3.2" or "t > 3.0" never split).
    parts = re.split(r"(?<=[。！？；])|(?<=[.;!?])(?=\s|$|[一-鿿])|\n", text)
    return [s.strip() for s in parts if s and s.strip()]


_PUNCT_MAP = str.maketrans({"：": ":", "，": ",", "；": ";", "（": "(", "）": ")", "“": '"', "”": '"',
                            "‘": "'", "’": "'", "、": ",", "。": ".", "！": "!", "？": "?"})


def _same_text(a, b) -> bool:
    """Equal up to punctuation width and whitespace -- a change of only that
    is not information the reader needs."""
    norm = lambda s: re.sub(r"\s+", "", str(s or "")).translate(_PUNCT_MAP)
    return norm(a) == norm(b)


def _bigram_similarity(a, b) -> float:
    norm = lambda s: re.sub(r"\s+", "", str(s or "").replace("**", "")).translate(_PUNCT_MAP)
    x, y = norm(a), norm(b)
    if len(x) < 4 or len(y) < 4:          # too short to call a "rewording"
        return 1.0 if x == y else 0.0
    ga = {x[i:i + 2] for i in range(len(x) - 1)}
    gb = {y[i:i + 2] for i in range(len(y) - 1)}
    return len(ga & gb) / len(ga | gb) if (ga or gb) else 1.0


def _clip(text, n: int = FIELD_BUDGET) -> str:
    s = re.sub(r"\s+", " ", str(text or "").replace("**", "")).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def sentence_diff(before: str, after: str, budget: int = DIFF_BUDGET) -> dict:
    """Sentence-level diff: segments of {t: '=' | '-' | '+', s: text}, equal
    runs collapsed, the whole thing clipped to `budget` chars."""
    import difflib
    a, b = _sentences(before), _sentences(after)
    # Compare sentences up to punctuation width / whitespace: a pass that only
    # normalised punctuation must not read as a rewrite.
    norm = lambda s: re.sub(r"\s+", "", s).translate(_PUNCT_MAP)
    ka, kb = [norm(s) for s in a], [norm(s) for s in b]
    segs, ins, dele = [], 0, 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ka, kb, autojunk=False).get_opcodes():
        if tag == "equal":
            keep = a[i1:i2]
            segs.append({"t": "=", "s": keep[0] if len(keep) == 1 else keep[0] + " … " + keep[-1]} if keep else None)
        else:
            for s in a[i1:i2]:
                segs.append({"t": "-", "s": s})
                dele += 1
            for s in b[j1:j2]:
                segs.append({"t": "+", "s": s})
                ins += 1
    segs = [s for s in segs if s]
    used, out, truncated = 0, [], False
    for s in segs:
        text = _clip(s["s"], 200)
        if used + len(text) > budget:
            truncated = True
            break
        out.append({"t": s["t"], "s": text})
        used += len(text)
    return {"segments": out, "ins": ins, "del": dele, "truncated": truncated}


def describe_changes(node_id: str, ops: list, before: dict, after: dict) -> list:
    """What a commit did *to this paragraph*: field before → after, sentence
    diff of the statement, state moves, structure moves.  Derived from the ops
    and the two snapshots, never from the commit message."""
    bn = (before.get("nodes") or {}).get(node_id) or {}
    an = (after.get("nodes") or {}).get(node_id) or {}
    nodes_after = after.get("nodes") or {}
    nodes_before = before.get("nodes") or {}

    def title_of(nid, op_body=None):
        body = op_body or nodes_after.get(nid) or nodes_before.get(nid) or {}
        t = str(body.get("title") or nid)
        if t.startswith(nid):
            t = t[len(nid):].lstrip(" :：·-—") or t
        return "「" + t + "」"

    changes = []
    for op in ops or []:
        kind = op.get("op")
        if kind == "add_node":
            body = op.get("node") or {}
            if body.get("id") == node_id:
                changes.append({"kind": "text", "text": "新增这一段"})
                if body.get("statement"):
                    changes.append({"kind": "field", "field": "正文", "before": "", "after": _clip(body["statement"], 240)})
            elif op.get("parent") == node_id:
                changes.append({"kind": "text", "text": "新增下级 " + title_of(body.get("id"), body)})
        elif kind == "patch_fields" and op.get("id") == node_id:
            fields = op.get("fields") or {}
            for key, label in (("title", "标题"), ("summary", "简介")):
                if key in fields and not _same_text(bn.get(key), fields.get(key)):
                    if _bigram_similarity(bn.get(key), fields.get(key)) >= 0.85:
                        changes.append({"kind": "text", "text": label + "措辞微调"})
                    else:
                        changes.append({"kind": "field", "field": label,
                                        "before": _clip(bn.get(key)), "after": _clip(fields.get(key))})
            if "statement" in fields and not _same_text(bn.get("statement"), fields.get("statement")):
                d = sentence_diff(bn.get("statement") or "", fields.get("statement") or "")
                d.update({"kind": "diff", "field": "正文"})
                changes.append(d)
            if "basis_refs" in fields:
                old = set(bn.get("basis_refs") or [])
                new = [x for x in fields.get("basis_refs") or [] if x not in old]
                if new:
                    changes.append({"kind": "text", "text": "补了 %d 条依据记号" % len(new)})
            st = fields.get("state") or {}
            if isinstance(st, dict):
                if st.get("epistemic") and st["epistemic"] != (bn.get("state") or {}).get("epistemic"):
                    changes.append({"kind": "field", "field": "状态",
                                    "before": _EP_CN.get((bn.get("state") or {}).get("epistemic"), "待定"),
                                    "after": _EP_CN.get(st["epistemic"], st["epistemic"])})
                if st.get("narrative") and st["narrative"] != (bn.get("state") or {}).get("narrative", "ACTIVE"):
                    changes.append({"kind": "field", "field": "叙事位置",
                                    "before": _NA_CN.get((bn.get("state") or {}).get("narrative", "ACTIVE"), "在用"),
                                    "after": _NA_CN.get(st["narrative"], st["narrative"])})
            sm = fields.get("state_meta") or {}
            if isinstance(sm, dict) and sm.get("resolution_condition") and \
                    sm.get("resolution_condition") != (bn.get("state_meta") or {}).get("resolution_condition"):
                changes.append({"kind": "field", "field": "结算条件",
                                "before": _clip((bn.get("state_meta") or {}).get("resolution_condition")),
                                "after": _clip(sm["resolution_condition"])})
        elif kind == "move_node":
            if op.get("id") == node_id:
                old_parent = next((p for p, b in nodes_before.items() if node_id in (b.get("children") or [])), None)
                changes.append({"kind": "field", "field": "位置",
                                "before": title_of(old_parent) if old_parent else "—",
                                "after": title_of(op.get("new_parent")) + " 下"})
            elif op.get("new_parent") == node_id:
                changes.append({"kind": "text", "text": "收进了 " + title_of(op.get("id"))})
        elif kind == "detach_node" and op.get("id") == node_id:
            disp = op.get("narrative_disposition") or ""
            reason = (op.get("disposition_meta") or {}).get("reason") or ""
            changes.append({"kind": "text", "text": "从故事里移出（" + _NA_CN.get(disp, disp or "已弃") + "）"
                                                  + ("：" + _clip(reason, 120) if reason else "")})
        elif kind == "split_node":
            parts = [p for p in op.get("parts") or [] if isinstance(p, dict)]
            if op.get("id") == node_id:
                changes.append({"kind": "text", "text": "拆出了 " + "、".join(title_of(p.get("id"), p) for p in parts)})
            elif any(p.get("id") == node_id for p in parts):
                changes.append({"kind": "text", "text": "由 " + title_of(op.get("id")) + " 拆出"})
        elif kind == "merge_nodes":
            into = op.get("into") if isinstance(op.get("into"), str) else (op.get("into") or {}).get("id")
            if into == node_id:
                others = [x for x in op.get("ids") or [] if x != node_id]
                if others:
                    changes.append({"kind": "text", "text": "合并了 " + "、".join(title_of(x) for x in others)})
            elif node_id in (op.get("ids") or []):
                changes.append({"kind": "text", "text": "并入了 " + title_of(into)})
        elif kind == "set_role" and op.get("id") == node_id:
            role = op.get("role") or ""
            changes.append({"kind": "text", "text": "定为" + {"headline": "头条", "flagship": "旗舰", "backbone": "承重",
                                                          "empirical_core": "实证核", "foil": "对照"}.get(role, role)})
    return changes


def describe_ops_for(node_id: str, ops: list, titles: dict) -> list:
    """Compatibility wrapper: phrases only (no snapshots) -- used by tests and
    by callers that have no before/after."""
    fake_nodes = {nid: {"title": t} for nid, t in titles.items()}
    out = []
    for c in describe_changes(node_id, ops, {"nodes": {}}, {"nodes": fake_nodes}):
        if c["kind"] == "text":
            out.append(c["text"])
        elif c["kind"] == "field":
            out.append(c["field"] + "：" + (c["before"] or "—") + " → " + (c["after"] or "—"))
        else:
            out.append("正文：+%d −%d 句" % (c["ins"], c["del"]))
    return out


def why_of(commit: dict) -> dict:
    """The reason the reader may see: cause, trigger, counterfactual, message."""
    out = {"cause": commit.get("cause"), "cause_cn": _CAUSE_CN.get(commit.get("cause"), None),
           "trigger": commit.get("trigger_ref") or commit.get("trigger_class"),
           "counterfactual": commit.get("counterfactual"),
           "note": humanize_message(commit.get("message"))}
    return out


def humanize_message(message) -> str | None:
    """A commit message the reader may see; machine-shaped ones return None
    (they stay available under the folded technical details)."""
    text = str(message or "").strip()
    if not text:
        return None
    m = _BACKFILL_RE.match(text)
    if m:
        return "回填自历史文件 " + Path(m.group(1).strip()).name
    if text.startswith("genesis (backfill)"):
        return "回填起点：第一个有日期的版本之前的空根"
    if _CJK_RE.search(text) and "<-" not in text and "->" not in text:
        return text
    return None


def collect_blame(control: Path, ref: str) -> dict:
    """§4 of the UI appendix: which commits touched this block, what each did
    to it (derived from the ops), and the cause."""
    index: dict = {}
    try:
        chain = nar.commit_chain(control, ref)
    except Exception:
        return index
    before: dict = {"nodes": {}}
    for commit in chain:
        ops = commit.get("ops") or []
        try:
            after = nar.commit_snapshot(control, commit["id"])
        except Exception:
            after = before
        touched = _touched_ids(commit)
        for op in ops:
            touched |= _op_targets(op)
        why = why_of(commit)
        for nid in touched:
            row = {"commit": str(commit.get("id"))[:12], "at": commit.get("at"),
                   "kind": commit.get("kind"), "cause": commit.get("cause"),
                   "trigger_class": commit.get("trigger_class"),
                   "message": commit.get("message"),
                   "note": why["note"], "why": why,
                   "changes": describe_changes(nid, ops, before, after)}
            index.setdefault(nid, []).append(row)
        before = after
    for nid, rows in index.items():
        rows.reverse()  # newest first
        if len(rows) > MAX_BLAME_PER_NODE:
            index[nid] = rows[:MAX_BLAME_PER_NODE]
    return index


def collect_card(control: Path, ref: str) -> dict:
    """§8 card.narrative_part.  Only main carries a derived card; for a side ref
    we still show the head's role assignments (from the snapshot itself)."""
    if ref == "main":
        try:
            card = nar.derive_card(control)
            part = dict(card.get("narrative_part") or {})
            part["source_commit_id"] = card.get("source_commit_id")
            exclusions = part.get("exclusions") or []
            if len(exclusions) > MAX_EXCLUSIONS:
                part["exclusions"] = exclusions[:MAX_EXCLUSIONS]
                part["exclusions_truncated"] = len(exclusions) - MAX_EXCLUSIONS
            return part
        except Exception:
            pass
    stored = ros.read_json_file(nar.ndir(control) / "card.json", None)
    if isinstance(stored, dict):
        return dict(stored.get("narrative_part") or {})
    return {}


def build_payload(control: Path, ref: str = "main", project: str = "",
                  depth: int = AXIS_DEPTH) -> dict:
    """Everything the page needs, and nothing else -- it is a *view* of one
    committed snapshot (never the preview; §8 forbids deriving from working/)."""
    nar.require_store(control)
    snapshot = nar.load_head_snapshot(control, ref)
    head = nar.read_ref(control, ref)
    commit = {}
    if head:
        try:
            raw = nar.load_commit(control, head)
            commit = {"id": raw.get("id"), "at": raw.get("at"), "ref": raw.get("ref"),
                      "kind": raw.get("kind"), "cause": raw.get("cause"),
                      "forced_status": raw.get("forced_status"),
                      "message": raw.get("message"), "author": raw.get("author")}
        except Exception:
            commit = {"id": head}
    root_node = (snapshot.get("nodes") or {}).get(snapshot.get("root")) or {}
    if not project:
        project = control.parent.name
    return {
        "schema": VIEW_SCHEMA,
        "project": project,
        "ref": ref,
        "title": root_node.get("title") or project,
        "north_star": root_node.get("statement") or root_node.get("title") or "",
        "axis_depth": depth,
        "generated_at": nar.now_iso(),
        "commit": commit,
        "tree_hash": nar.tree_hash(snapshot) if snapshot.get("root") else "",
        "snapshot": snapshot,
        "hashes": _subtree_hashes(snapshot),
        "card": collect_card(control, ref),
        "annotations": collect_annotations(control),
        "blame": collect_blame(control, ref),
        "literature": collect_literature(control),
        "pending": {"questions": [], "notes": []},
    }


def collect_literature(control: Path):
    """The literature layer (refs + node links) when the project has built one;
    only the 树形 structure of the page draws it."""
    try:
        import literature_index
        return literature_index.load_for_page(control)
    except Exception:
        return None


# ============================================================ page: style

PAGE_CSS = """
:root {
  color-scheme: light;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --ink: #1b1f24;
  --ink-2: #4e5661;
  --ink-3: #8a929c;
  --line: #dde1e6;
  --line-hi: #b6bdc6;
  --hover: rgba(20, 28, 40, .045);
  --sel: rgba(47, 111, 219, .10);
  --chip: rgba(20, 28, 40, .07);
  --accent: #2f6fdb;
  --mark: rgba(255, 196, 0, .35);
  --ok: #2e8b57;
  --no: #c9433c;
  --wait: #9aa3ad;
  --lit: #6b4fd8;
  --indent: 22px;
  --row: 32px;
  --insp: 380px;
  --font: system-ui, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  --mono: ui-monospace, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #141719;
    --panel: #1b1f23;
    --ink: #e7eaee;
    --ink-2: #b2b9c2;
    --ink-3: #7c848e;
    --line: #2d333a;
    --line-hi: #4a525c;
    --hover: rgba(255, 255, 255, .05);
    --sel: rgba(110, 163, 255, .16);
    --chip: rgba(255, 255, 255, .08);
    --accent: #6ea3ff;
    --mark: rgba(255, 196, 0, .28);
    --ok: #5fc08a;
    --no: #e5726b;
    --wait: #7c848e;
    --lit: #a08cff;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #141719;
  --panel: #1b1f23;
  --ink: #e7eaee;
  --ink-2: #b2b9c2;
  --ink-3: #7c848e;
  --line: #2d333a;
  --line-hi: #4a525c;
  --hover: rgba(255, 255, 255, .05);
  --sel: rgba(110, 163, 255, .16);
  --chip: rgba(255, 255, 255, .08);
  --accent: #6ea3ff;
  --mark: rgba(255, 196, 0, .28);
  --ok: #5fc08a;
  --no: #e5726b;
  --wait: #7c848e;
  --lit: #a08cff;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 13px/1.5 var(--font);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
body.dragging { cursor: col-resize; user-select: none; }
button, input, select, textarea { font: inherit; color: inherit; }
button {
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  padding: 4px 9px;
  cursor: pointer;
  color: var(--ink-2);
}
button:hover { background: var(--hover); color: var(--ink); }
button.on { background: var(--sel); color: var(--ink); }
button:focus-visible, .row:focus-visible, .item:focus-visible, .blab:focus-visible,
input:focus-visible, textarea:focus-visible, select:focus-visible, .splitter:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.mono { font-family: var(--mono); font-size: 11px; }
mark { background: var(--mark); color: inherit; padding: 0 1px; border-radius: 2px; }

/* ---------------------------------------------------------------- top bar */
.top {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 48px;
  padding: 6px 14px;
  background: var(--panel);
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}
.top .brand { display: flex; flex-direction: column; min-width: 0; max-width: 34%; }
.top .ptitle { font-weight: 600; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.top .pns { color: var(--ink-3); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 7px; padding: 2px; gap: 2px; }
.seg button { border-radius: 5px; padding: 3px 10px; }
.top .search {
  flex: 1 1 160px;
  max-width: 320px;
  min-width: 120px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--bg);
}
.top .grow { flex: 1 1 auto; }
.top label { display: inline-flex; align-items: center; gap: 4px; color: var(--ink-2); cursor: pointer; }
.top .group { display: inline-flex; align-items: center; gap: 2px; }
.top .divider { width: 1px; height: 20px; background: var(--line); }

/* ---------------------------------------------------------------- main */
.main {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 6px var(--insp);
}
.stage { overflow: auto; padding: 14px 18px 40px; min-width: 0; }
.splitter {
  background: var(--line);
  cursor: col-resize;
  position: relative;
  touch-action: none;
}
.splitter::after {
  content: "";
  position: absolute;
  inset: 0 -4px;
}
.splitter:hover, body.dragging .splitter { background: var(--accent); }
.inspector {
  overflow: auto;
  background: var(--panel);
  border-left: 1px solid var(--line);
  padding: 16px 18px 40px;
  min-width: 0;
}
@media (max-width: 820px) {
  .main { grid-template-columns: 1fr; grid-template-rows: minmax(0, 1fr) minmax(0, 45%); }
  .splitter { display: none; }
  .inspector { border-left: 0; border-top: 1px solid var(--line); }
}

/* ---------------------------------------------------------------- tree view */
.tree { list-style: none; margin: 0; padding: 0; }
.tree .tree { padding-left: var(--indent); position: relative; }
.tree .tree::before {
  content: "";
  position: absolute;
  left: 11px;
  top: 0;
  bottom: 0;
  border-left: 1px solid var(--line);
}
.tree .tree:hover::before { border-color: var(--line-hi); }
.nd { position: relative; }
.row {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: var(--row);
  padding: 5px 8px;
  border-radius: 6px;
  cursor: pointer;
  position: relative;
}
.row:hover { background: var(--hover); }
.row.is-sel { background: var(--sel); box-shadow: inset 3px 0 0 var(--accent); }
.chev {
  width: 16px;
  height: 16px;
  flex: 0 0 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--ink-3);
  font-size: 10px;
  border-radius: 4px;
  transition: transform 150ms ease;
}
.chev:hover { background: var(--chip); color: var(--ink); }
.chev.none { visibility: hidden; }
.nd.open > .row .chev { transform: rotate(90deg); }
.dot {
  width: 7px;
  height: 7px;
  flex: 0 0 7px;
  border-radius: 50%;
  background: var(--wait);
  border: 1px solid transparent;
}
.dot.ep-HELD { background: var(--ok); }
.dot.ep-KILLED { background: var(--no); }
.dot.ep-PENDING { background: var(--wait); }
.dot.na-DEMOTED { opacity: .45; }
.dot.na-DROPPED, .dot.na-MERGED { background: transparent; border-color: currentColor; opacity: .55; }
.dot.ep-HELD.na-DROPPED, .dot.ep-HELD.na-MERGED { color: var(--ok); }
.dot.ep-KILLED.na-DROPPED, .dot.ep-KILLED.na-MERGED { color: var(--no); }
.dot.ep-PENDING.na-DROPPED, .dot.ep-PENDING.na-MERGED { color: var(--wait); }
.num { color: var(--ink-3); font-variant-numeric: tabular-nums; flex: 0 0 auto; }
.ttl { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row.lv1 .ttl { font-weight: 600; }
.nd.faded > .row .ttl { color: var(--ink-3); }
.tag {
  background: var(--chip);
  color: var(--ink-2);
  border-radius: 10px;
  padding: 0 7px;
  height: 18px;
  line-height: 18px;
  font-size: 11px;
  flex: 0 0 auto;
}
.cnt { color: var(--ink-3); font-size: 11px; flex: 0 0 auto; font-variant-numeric: tabular-nums; }
.brief {
  margin: -2px 8px 6px 31px;
  padding: 0 0 0 8px;
  color: var(--ink-2);
  font-size: 12.5px;
  line-height: 1.55;
  max-width: 68ch;
  border-left: 2px solid var(--line);
  animation: ar-in 150ms ease;
}
.brief .more { margin-top: 4px; color: var(--ink-3); font-size: 12px; white-space: pre-wrap; }
.nd > .tree { animation: ar-in 150ms ease; }
@keyframes ar-in { from { opacity: 0; } to { opacity: 1; } }
@media (prefers-reduced-motion: reduce) {
  * { animation: none !important; transition: none !important; }
}
.empty { color: var(--ink-3); padding: 24px 8px; }

/* ---------------------------------------------------------------- columns view */
.cols {
  display: flex;
  gap: 0;
  height: 100%;
  min-height: 320px;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}
.col {
  flex: 0 0 250px;
  width: 250px;
  border-right: 1px solid var(--line);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}
.col:last-child { border-right: 0; }
.colhead {
  position: sticky;
  top: 0;
  background: var(--panel);
  padding: 8px 12px;
  font-size: 11px;
  color: var(--ink-3);
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  z-index: 1;
}
.item {
  display: grid;
  grid-template-columns: 10px 1fr 12px;
  column-gap: 8px;
  align-items: start;
  padding: 8px 12px;
  cursor: pointer;
  border-bottom: 1px solid transparent;
}
.item:hover { background: var(--hover); }
.item.is-path { background: var(--chip); }
.item.is-sel { background: var(--sel); box-shadow: inset 3px 0 0 var(--accent); }
.item .dot { margin-top: 7px; }
.item .it { min-width: 0; }
.item .it .ttl { white-space: normal; font-weight: 500; }
.item .it .ex {
  color: var(--ink-3);
  font-size: 12px;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.item .arrow { color: var(--ink-3); font-size: 12px; margin-top: 3px; }
.item .arrow.none { visibility: hidden; }

/* ---------------------------------------------------------------- tree view: brace flow */
.bracetree { padding: 8px 4px 24px; width: max-content; min-width: 100%; }
.bn { display: flex; align-items: center; }
.bcol { display: flex; flex-direction: column; min-width: 0; }
.blab {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: var(--row);
  padding: 4px 8px;
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
  max-width: 380px;
}
.blab:hover { background: var(--hover); }
.blab.is-sel { background: var(--sel); box-shadow: inset 0 0 0 1px var(--accent); }
.blab .ttl { overflow: hidden; text-overflow: ellipsis; }
.blab.lv1 .ttl { font-weight: 600; }
.blab.lv0 .ttl { font-weight: 700; font-size: 14px; }
.bn.open > .bcol > .blab .chev { transform: rotate(90deg); }
.bn.faded > .bcol > .blab .ttl { color: var(--ink-3); }
.bbrief { color: var(--ink-3); font-size: 12px; line-height: 1.45; max-width: 340px; white-space: normal; margin: -2px 8px 4px 31px; }
.brace { width: 14px; align-self: stretch; display: flex; flex-direction: column; margin: 0 4px 0 2px; min-height: 28px; }
.brace .h { flex: 1; border-left: 1.5px solid var(--line-hi); margin-left: 6px; }
.brace .h.hu { border-top: 1.5px solid var(--line-hi); border-top-left-radius: 9px; }
.brace .h.hd { border-bottom: 1.5px solid var(--line-hi); border-bottom-left-radius: 9px; }
.brace .nib { height: 0; position: relative; }
.brace .nib::before {
  content: "";
  position: absolute;
  left: 0;
  top: -7px;
  width: 7px;
  height: 14px;
  border-right: 1.5px solid var(--line-hi);
  border-top-right-radius: 9px;
  border-bottom-right-radius: 9px;
}
.bkids { display: flex; flex-direction: column; gap: 2px; }
.lit {
  font-size: 10.5px;
  color: var(--lit);
  border: 1px solid var(--lit);
  border-radius: 9px;
  padding: 0 5px;
  line-height: 15px;
  flex: 0 0 auto;
  opacity: .85;
}
.lit:hover { background: var(--lit); color: #fff; opacity: 1; }
.litlist { list-style: none; margin: 0; padding: 0; }
.litlist li { padding: 6px 0; border-bottom: 1px solid var(--line); }
.litlist li:last-child { border-bottom: 0; }
.litlist a { cursor: pointer; color: var(--ink); font-weight: 500; text-decoration: none; }
.litlist a:hover { color: var(--accent); }
.litlist .lt { display: block; color: var(--ink-3); font-size: 12px; }
.litlist .lr { color: var(--ink-2); font-size: 12px; margin-top: 2px; }
.tag.ok { color: var(--ok); }
.tag.warn { color: var(--no); }
a.back { display: inline-block; color: var(--accent); cursor: pointer; font-size: 12px; margin-bottom: 4px; }
.extlinks a { color: var(--accent); margin-right: 12px; }

/* ---------------------------------------------------------------- search view */
.hits { list-style: none; margin: 0; padding: 0; }
.hit { padding: 8px 10px; border-radius: 6px; cursor: pointer; }
.hit:hover { background: var(--hover); }
.hit .crumb { color: var(--ink-3); font-size: 11px; }
.hit .ex { color: var(--ink-2); font-size: 12px; }

/* ---------------------------------------------------------------- inspector */
.inspector h2 { font-size: 16px; line-height: 1.35; margin: 4px 0 6px; text-wrap: balance; }
.inspector h3 {
  font-size: 11px;
  letter-spacing: .04em;
  color: var(--ink-3);
  margin: 18px 0 6px;
  font-weight: 600;
}
.crumbs { color: var(--ink-3); font-size: 11px; display: flex; flex-wrap: wrap; gap: 2px 4px; }
.crumbs a { color: var(--ink-2); cursor: pointer; text-decoration: none; }
.crumbs a:hover { color: var(--accent); text-decoration: underline; }
.metaline { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; color: var(--ink-2); }
.prose { max-width: 62ch; color: var(--ink); }
.prose p { margin: 0 0 8px; }
.prose.dim { color: var(--ink-2); }
.hint { color: var(--ink-3); }
.kv { margin: 0 0 4px; }
.kv b { font-weight: 600; color: var(--ink-2); margin-right: 4px; }
.list { margin: 0; padding-left: 16px; }
.list li { margin: 0 0 4px; }
.linklist { list-style: none; margin: 0; padding: 0; }
.linklist li { padding: 4px 0; border-bottom: 1px solid var(--line); display: flex; gap: 8px; align-items: baseline; }
.linklist li:last-child { border-bottom: 0; }
.linklist a { cursor: pointer; color: var(--ink); text-decoration: none; }
.linklist a:hover { color: var(--accent); }
.linklist .lab { color: var(--ink-3); font-size: 11px; flex: 0 0 56px; }
.hist { list-style: none; margin: 0; padding: 0; }
.hist li { padding: 6px 0; border-bottom: 1px solid var(--line); }
.hist li:last-child { border-bottom: 0; }
.hist .when { color: var(--ink-3); font-size: 11px; }
.hist .what { color: var(--ink); }
.hist .chg { display: flex; flex-wrap: wrap; gap: 4px 6px; align-items: baseline; margin: 2px 0; }
.hist .chg .lab { color: var(--ink-3); font-size: 11px; flex: 0 0 auto; }
.hist .chg .old { color: var(--ink-3); text-decoration: line-through; text-decoration-color: var(--no); }
.hist .chg .arr { color: var(--ink-3); }
.hist .chg .new { color: var(--ink); }
.hist .chg.stack { display: block; }
.hist .chg .old2, .hist .chg .new2 { display: flex; gap: 6px; align-items: baseline; margin: 2px 0 2px 0; font-size: 12px; line-height: 1.55; }
.hist .chg .old2 { color: var(--ink-3); }
.hist .chg .new2 { color: var(--ink); }
.hist .chg .old2 .lab, .hist .chg .new2 .lab { flex: 0 0 14px; }
.hist .why { color: var(--ink-2); font-size: 12px; margin-top: 3px; }
.hist .difftoggle, .hist .asklink { padding: 1px 6px; font-size: 11px; color: var(--accent); border-color: transparent; }
.hist .asklink { margin-top: 2px; }
.hist .diff { margin: 4px 0 6px; padding: 6px 8px; border-left: 2px solid var(--line); font-size: 12px; line-height: 1.6; }
.hist .diff span { display: block; }
.hist .diff .eq { color: var(--ink-3); }
.hist .diff .del { color: var(--ink-3); text-decoration: line-through; text-decoration-color: var(--no); }
.hist .diff .ins { color: var(--ink); background: color-mix(in srgb, var(--ok) 12%, transparent); border-radius: 3px; }
.qa { border-left: 2px solid var(--line); padding: 2px 0 2px 10px; margin: 0 0 10px; }
.qa .q { font-weight: 500; }
.qa .a { color: var(--ink-2); margin-top: 2px; }
.qa .meta { color: var(--ink-3); font-size: 11px; margin-top: 2px; }
.qa.busy { border-left-color: var(--accent); }
.qa.busy .meta { color: var(--accent); }
.qa.busy .meta::after { content: ""; display: inline-block; width: 6px; height: 6px; margin-left: 6px; border-radius: 50%; background: var(--accent); animation: ar-pulse 1s ease-in-out infinite; }
.qa.failed { border-left-color: var(--no); }
@keyframes ar-pulse { 0%, 100% { opacity: .2; } 50% { opacity: 1; } }
.field { display: block; margin: 8px 0; }
.field span { display: block; color: var(--ink-3); font-size: 11px; margin-bottom: 3px; }
.field input, .field textarea, .field select {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--bg);
  padding: 6px 8px;
}
.field textarea { min-height: 72px; resize: vertical; }
.raw { min-height: 160px; }
.btns { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
.btns button { border-color: var(--line); }
.btns button.primary { background: var(--accent); color: #fff; border-color: transparent; }
.btns button.primary:hover { filter: brightness(1.08); }
details.tech { margin-top: 20px; }
details.tech summary { cursor: pointer; color: var(--ink-3); font-size: 11px; }
details.tech .kv { font-size: 12px; }

/* ---------------------------------------------------------------- status bar */
.statusbar {
  display: flex;
  gap: 12px;
  align-items: center;
  height: 28px;
  padding: 0 14px;
  font-size: 11px;
  color: var(--ink-3);
  background: var(--panel);
  border-top: 1px solid var(--line);
}
.statusbar .msg { flex: 1 1 auto; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.statusbar .msg.warn { color: var(--no); }
"""


PAGE_JS = r"""
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("ar-snapshot").textContent);
  var BASE = DATA.snapshot;
  var PATCH_SCHEMA = "auto-research/narrative-patch-v1";
  var QUESTION_SCHEMA = "auto-research/narrative-question-v1";

  // Everything the reader sees is plain language.  Codes stay in the data.
  var EP = { HELD: "已成立", KILLED: "已证否", PENDING: "待定" };
  var NA = { ACTIVE: "在用", DROPPED: "已弃", DEMOTED: "已降级", MERGED: "已并入" };
  var ROLE_CN = { headline: "头条", flagship: "旗舰", backbone: "承重",
                  empirical_core: "实证核", foil: "对照" };
  var ROLE_ORDER = ["headline", "flagship", "backbone", "empirical_core", "foil"];
  var KIND_CN = { refine: "修订", restructure: "重构", overthrow: "推翻", merge: "合并" };
  var CAUSE_CN = { none: "无外因", forced: "受迫", unforced: "主动", unknown: "原因未记" };
  var TYPE_CN = { claim: "主张", theorem: "定理", evidence: "证据", narrative: "叙事",
                  artifact: "工件", decision: "决定" };
  var IDFIELD = { claim: "predicate", theorem: "conclusion", evidence: "estimand",
                  narrative: "thesis", artifact: "ref", decision: "decision_ref" };
  // 树形 = the sketch: chapter chain across the top, each chapter unfolding
  // downward in its own column.  目录 = one outline.  分栏 = Miller columns.
  var VIEW_CN = { tree: "树形", outline: "目录", cols: "分栏" };

  var DEFAULT_OPEN_DEPTH = 0;   // progressive disclosure: nothing pre-expanded
  var BRIEF_MAX = 220;          // inline brief length in the tree
  var INSP_MIN = 280, INSP_DEFAULT = 380;

  var LS_KEY = "arv2:" + DATA.project + ":" + DATA.ref;

  var S = {
    snapshot: clone(BASE),
    parent: {},
    ops: [],
    todos: [],
    questions: ((DATA.pending || {}).questions || []).slice(),
    selected: null,
    edit: false,
    open: lsGet(":open", {}),
    view: lsGet(":view", "tree"),
    path: lsGet(":path", []),
    chapter: lsGet(":chapter", null),
    numbering: !!lsGet(":num", false),
    inspW: lsGet(":insp", INSP_DEFAULT),
    q: "",
    raw: null,
    pending: {},
    qaTimer: null,
    ref: null                                       // a study open in the right pane (树形 only)
  };
  if (S.view === "axis") S.view = "tree";           // 3.0.2 name
  if (["tree", "outline", "cols"].indexOf(S.view) < 0) S.view = "tree";
  S.live = null;                                     // {model} when served by `narrative serve`

  var stage = document.getElementById("stage");
  var inspector = document.getElementById("inspector");

  // ---------------------------------------------------------------- helpers
  function clone(x) { return JSON.parse(JSON.stringify(x)); }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function lsGet(key, fallback) {
    try {
      var raw = window.localStorage.getItem(LS_KEY + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (e) { return fallback; }
  }
  function lsSet(key, value) {
    try { window.localStorage.setItem(LS_KEY + key, JSON.stringify(value)); } catch (e) { /* private mode */ }
  }
  function cssId(id) {
    return (window.CSS && CSS.escape) ? CSS.escape(id) : String(id).replace(/["\\]/g, "\\$&");
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function nowIso() { return new Date().toISOString().replace(/\.\d{3}Z$/, "Z"); }
  function fmtDate(iso) {
    if (!iso) return "";
    var m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/);
    if (!m) return String(iso);
    return m[1] + "-" + m[2] + "-" + m[3] + (m[4] ? " " + m[4] + ":" + m[5] : "");
  }
  function short(x, n) { return x ? String(x).slice(0, n || 7) : "—"; }
  function node(id) { return S.snapshot.nodes[id]; }
  function rebuildParents() {
    S.parent = {};
    Object.keys(S.snapshot.nodes).forEach(function (pid) {
      ((S.snapshot.nodes[pid] || {}).children || []).forEach(function (kid) { S.parent[kid] = pid; });
    });
  }
  function parentOf(id) { return S.parent[id] || null; }
  function ancestorsOf(id) {           // nearest first, root excluded
    var out = [], cur = parentOf(id);
    while (cur && cur !== S.snapshot.root) { out.push(cur); cur = parentOf(cur); }
    return out;
  }
  function siblingsOf(id) {
    var p = parentOf(id);
    return p ? (S.snapshot.nodes[p].children || []).slice() : [];
  }
  function descendantsOf(id) {
    var out = [], stack = kidsOf(id);
    while (stack.length) {
      var cur = stack.shift();
      out.push(cur);
      stack = kidsOf(cur).concat(stack);
    }
    return out;
  }
  function kidsOf(id) {
    var n = node(id);
    return ((n && n.children) || []).filter(function (k) { return !!node(k); });
  }
  function roleOf(id) {
    var ra = S.snapshot.role_assignments || {};
    return ROLE_ORDER.filter(function (r) { return ra[r] === id; });
  }
  // Some projects prefix every title with its own id; the page never shows ids
  // in the window, so strip the prefix rather than printing a code.
  function titleOf(id, n) {
    n = n || node(id);
    var t = String((n && n.title) || "");
    if (id && t.indexOf(id) === 0) {
      var rest = t.slice(id.length).replace(/^[\s::：·\-—]+/, "");
      if (rest) t = rest;
    }
    return t || "（无标题）";
  }
  // Leading bracket tags ("[critical|CLOSED] …") are ledger marks, not prose.
  function stripMarks(text) {
    return String(text || "").replace(/^\s*(\[[^\]\n]{1,40}\]\s*)+/, "");
  }
  function firstSentence(text, max) {
    var s = stripMarks(text).replace(/\*\*/g, "").replace(/\s+/g, " ").trim();
    if (!s) return "";
    var cut = s.search(/[。！？.!?]\s|[。！？]/);
    if (cut > 0 && cut < max) s = s.slice(0, cut + 1);
    if (s.length > max) s = s.slice(0, max - 1) + "…";
    return s;
  }
  // Inline brief for the tree: the summary, unless it merely repeats the title,
  // else the first sentence of the statement.
  function briefOf(id, n) {
    n = n || node(id);
    var title = titleOf(id, n);
    var sum = String(n.summary || "").replace(/\s+/g, " ").trim();
    var out = "";
    if (sum && sum !== title && sum !== String(n.title || "").trim()) out = firstSentence(sum, BRIEF_MAX);
    else out = firstSentence(n.statement || "", BRIEF_MAX);
    return out === title || out === String(n.title || "").trim() ? "" : out;
  }
  function numberOf(id) {
    var chain = [id].concat(ancestorsOf(id)).reverse();
    var parts = [], parent = S.snapshot.root;
    for (var i = 0; i < chain.length; i++) {
      var idx = kidsOf(parent).indexOf(chain[i]);
      if (idx < 0) return "";
      parts.push(idx + 1);
      parent = chain[i];
    }
    return parts.join(".");
  }
  function similarity(a, b) {           // character-bigram Jaccard, 0..1
    function grams(s) {
      var set = {}, n = 0;
      for (var i = 0; i + 1 < s.length; i++) { var g = s.slice(i, i + 2); if (!set[g]) { set[g] = 1; n++; } }
      return { set: set, n: n };
    }
    var A = grams(a), B = grams(b), inter = 0;
    Object.keys(A.set).forEach(function (g) { if (B.set[g]) inter++; });
    var union = A.n + B.n - inter;
    return union ? inter / union : 0;
  }
  function mdLite(text) {
    var paras = String(text || "").split(/\n\s*\n/);
    return paras.map(function (p) {
      var h = esc(p.trim()).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");
      return h ? "<p>" + h + "</p>" : "";
    }).join("");
  }
  function annotationsOf(id) { return (DATA.annotations || {})[id] || []; }
  function questionsOf(id) {
    return S.questions.filter(function (q) { return q.node_id === id; });
  }
  function status(msg, warn) {
    var bar = document.getElementById("statusmsg");
    bar.textContent = msg;
    bar.className = "msg" + (warn ? " warn" : "");
  }
  function stateWords(n) {
    var st = n.state || {};
    var out = [EP[st.epistemic] || st.epistemic || "待定"];
    if (st.narrative && st.narrative !== "ACTIVE") out.push(NA[st.narrative] || st.narrative);
    return out;
  }

  // ---------------------------------------------------------------- op replay
  function removeChild(snap, parent, id) {
    var kids = snap.nodes[parent].children || [];
    var i = kids.indexOf(id);
    if (i >= 0) kids.splice(i, 1);
    return i;
  }
  function insertChild(snap, parent, id, index) {
    var host = snap.nodes[parent];
    if (!host.children) host.children = [];
    if (index === null || index === undefined || index < 0 || index > host.children.length) {
      host.children.push(id);
    } else {
      host.children.splice(index, 0, id);
    }
  }
  function snapParent(snap, id) {
    var keys = Object.keys(snap.nodes);
    for (var i = 0; i < keys.length; i++) {
      if ((snap.nodes[keys[i]].children || []).indexOf(id) >= 0) return keys[i];
    }
    return null;
  }
  function applyOne(snap, op) {
    if (op.op === "patch_fields") {
      var n = snap.nodes[op.id];
      if (!n) return;
      Object.keys(op.fields || {}).forEach(function (k) { n[k] = op.fields[k]; });
    } else if (op.op === "move_node") {
      var p = snapParent(snap, op.id);
      if (p) removeChild(snap, p, op.id);
      if (snap.nodes[op.new_parent]) insertChild(snap, op.new_parent, op.id, op.index);
    } else if (op.op === "merge_nodes") {
      var into = typeof op.into === "string" ? op.into : (op.into || {}).id;
      (op.ids || []).forEach(function (src) {
        if (src === into) return;
        var sn = snap.nodes[src];
        if (!sn || !snap.nodes[into]) return;
        (sn.children || []).slice().forEach(function (kid) {
          removeChild(snap, src, kid);
          insertChild(snap, into, kid, null);
        });
        var host = snapParent(snap, src);
        if (host) removeChild(snap, host, src);
        sn.state = sn.state || {};
        sn.state.narrative = "MERGED";
        sn.disposition_meta = { target: into, reason: op.reason || ("merged into " + into) };
      });
    } else if (op.op === "split_node") {
      (op.parts || []).forEach(function (part) {
        var body = clone(part);
        body.children = body.children || [];
        snap.nodes[body.id] = body;
        insertChild(snap, op.id, body.id, null);
      });
    } else if (op.op === "add_node") {
      var body2 = clone(op.node);
      body2.children = body2.children || [];
      snap.nodes[body2.id] = body2;
      if (op.parent) insertChild(snap, op.parent, body2.id, op.index);
    }
  }
  function replay() {
    var snap = clone(BASE);
    for (var i = 0; i < S.ops.length; i++) {
      try { applyOne(snap, S.ops[i]); } catch (e) { /* proposal only; CLI adjudicates */ }
    }
    S.snapshot = snap;
    rebuildParents();
  }
  function pushOp(op, label) {
    S.ops.push(op);
    replay();
    renderAll();
    status(label + "（已记入补丁，共 " + S.ops.length + " 步；导出后由主控校验）");
  }
  function dirty(id) {
    for (var i = 0; i < S.ops.length; i++) {
      var op = S.ops[i];
      if (op.id === id) return true;
      if ((op.ids || []).indexOf(id) >= 0) return true;
      if (op.into === id || op.new_parent === id) return true;
    }
    return false;
  }

  // ---------------------------------------------------------------- open state
  function isOpen(id, level) {
    var v = S.open[id];
    return v === undefined ? (level <= DEFAULT_OPEN_DEPTH) : !!v;
  }
  function setOpen(id, value) {
    S.open[id] = value ? 1 : 0;
    lsSet(":open", S.open);
  }

  // ---------------------------------------------------------------- shared bits
  function dotEl(n) {
    var st = n.state || {};
    var d = el("span", "dot ep-" + (st.epistemic || "PENDING") + " na-" + (st.narrative || "ACTIVE"));
    var words = stateWords(n).join(" · ");
    d.title = words;
    d.setAttribute("aria-label", words);
    return d;
  }
  function tagEl(id, n) {
    var roles = roleOf(id);
    if (roles.length) return el("span", "tag", ROLE_CN[roles[0]] || roles[0]);
    if ((n.state_meta || {}).live_bet) return el("span", "tag", "在飞");
    return null;
  }
  function isFaded(n) {
    var na = (n.state || {}).narrative;
    return na === "DROPPED" || na === "MERGED" || na === "DEMOTED";
  }

  // ---------------------------------------------------------------- tree view
  function treeEl(parentId, level) {
    var ul = el("ul", "tree");
    ul.setAttribute("role", level === 1 ? "tree" : "group");
    kidsOf(parentId).forEach(function (id) { ul.appendChild(liEl(id, level)); });
    return ul;
  }
  function liEl(id, level) {
    var n = node(id);
    var li = el("li", "nd" + (isFaded(n) ? " faded" : ""));
    li.setAttribute("data-id", id);
    li.appendChild(rowEl(id, n, level));
    if (isOpen(id, level)) expandLi(li, id, level);
    return li;
  }
  function rowEl(id, n, level) {
    var kids = kidsOf(id), brief = briefOf(id, n);
    var row = el("div", "row lv" + level + (S.selected === id ? " is-sel" : ""));
    row.setAttribute("data-id", id);
    row.setAttribute("data-level", String(level));
    row.setAttribute("role", "treeitem");
    row.setAttribute("tabindex", "0");
    row.setAttribute("aria-level", String(level));
    if (kids.length || brief) row.setAttribute("aria-expanded", isOpen(id, level) ? "true" : "false");
    var chev = el("span", "chev" + (kids.length || brief ? "" : " none"), "\u25B6");
    chev.setAttribute("data-act", "toggle");
    row.appendChild(chev);
    row.appendChild(dotEl(n));
    if (S.numbering) row.appendChild(el("span", "num", numberOf(id)));
    var ttl = el("span", "ttl", titleOf(id, n) + (dirty(id) ? " ·改" : ""));
    ttl.title = brief || "";
    row.appendChild(ttl);
    var tag = tagEl(id, n);
    if (tag) row.appendChild(tag);
    if (kids.length) row.appendChild(el("span", "cnt", String(kids.length)));
    return row;
  }
  function expandLi(li, id, level) {
    var n = node(id);
    li.classList.add("open");
    var row = li.querySelector(":scope > .row");
    if (row) row.setAttribute("aria-expanded", "true");
    var brief = briefOf(id, n);
    var kids = kidsOf(id);
    if (brief) {
      var b = el("div", "brief");
      b.appendChild(el("div", "", brief));
      if (!kids.length) {                         // a leaf opens to its full text
        var full = String(n.statement || "").replace(/\*\*/g, "").trim();
        if (full && full !== brief && full.length > brief.length) {
          b.appendChild(el("div", "more", full));
        }
      }
      li.appendChild(b);
    }
    if (kids.length) li.appendChild(treeEl(id, level + 1));
  }
  function collapseLi(li) {
    li.classList.remove("open");
    var row = li.querySelector(":scope > .row");
    if (row) row.setAttribute("aria-expanded", "false");
    Array.prototype.slice.call(li.children).forEach(function (c) {
      if (c !== row) li.removeChild(c);
    });
  }
  // In-place fold: only this node's subtree changes; nothing else re-renders.
  function toggle(id, force) {
    var li = stage.querySelector('li.nd[data-id="' + cssId(id) + '"]');
    if (!li) { toggleBrace(id, force); return; }
    var level = parseInt(li.querySelector(":scope > .row").getAttribute("data-level"), 10) || 1;
    var now = isOpen(id, level);
    var next = force === undefined ? !now : !!force;
    if (next === now && li.classList.contains("open") === now) return;
    setOpen(id, next);
    if (next) expandLi(li, id, level); else collapseLi(li);
  }
  function renderOutline() {
    var root = S.snapshot.root;
    if (!root || !kidsOf(root).length) {
      stage.replaceChildren(el("p", "empty", "叙事树还是空的：根节点下没有章节。"));
      return;
    }
    stage.replaceChildren(treeEl(root, 1));
  }

  // ---------------------------------------------------------------- columns view
  function validPath() {
    var out = [], parent = S.snapshot.root;
    for (var i = 0; i < (S.path || []).length; i++) {
      if (kidsOf(parent).indexOf(S.path[i]) < 0) break;
      out.push(S.path[i]);
      parent = S.path[i];
    }
    S.path = out;
    return out;
  }
  function colEl(pid, depth) {
    var col = el("div", "col");
    col.setAttribute("data-depth", String(depth));
    col.setAttribute("data-parent", pid);
    var headText = pid === S.snapshot.root ? "章节" : titleOf(pid);
    var head = el("div", "colhead", headText);
    head.title = headText;
    col.appendChild(head);
    var kids = kidsOf(pid);
    if (!kids.length) col.appendChild(el("p", "empty", "（这一节没有下级）"));
    kids.forEach(function (id) {
      var n = node(id);
      var item = el("div", "item" + (S.path[depth] === id ? " is-path" : "") +
                                  (S.selected === id ? " is-sel" : ""));
      item.setAttribute("data-id", id);
      item.setAttribute("tabindex", "0");
      item.setAttribute("role", "button");
      item.appendChild(dotEl(n));
      var it = el("div", "it");
      var ttl = el("div", "ttl", (S.numbering ? numberOf(id) + "  " : "") + titleOf(id, n) + (dirty(id) ? " ·改" : ""));
      it.appendChild(ttl);
      var tag = tagEl(id, n);
      if (tag) ttl.appendChild(document.createTextNode(" ")), ttl.appendChild(tag);
      var brief = briefOf(id, n);
      if (brief) it.appendChild(el("div", "ex", brief));
      item.appendChild(it);
      item.appendChild(el("span", "arrow" + (kidsOf(id).length ? "" : " none"), "\u203A"));
      col.appendChild(item);
    });
    return col;
  }
  function renderCols() {
    var root = S.snapshot.root;
    if (!root || !kidsOf(root).length) {
      stage.replaceChildren(el("p", "empty", "叙事树还是空的：根节点下没有章节。"));
      return;
    }
    var wrap = el("div", "cols");
    var chain = [root].concat(validPath());
    chain.forEach(function (pid, i) { wrap.appendChild(colEl(pid, i)); });
    stage.replaceChildren(wrap);
  }
  function descendCols(id, depth) {
    var wrap = stage.querySelector(".cols");
    if (!wrap) return;
    S.path = validPath().slice(0, depth).concat([id]);
    lsSet(":path", S.path);
    var cols = wrap.querySelectorAll(":scope > .col");
    for (var i = cols.length - 1; i > depth; i--) wrap.removeChild(cols[i]);
    var col = cols[depth];
    if (col) {
      Array.prototype.forEach.call(col.querySelectorAll(".item"), function (it) {
        it.classList.toggle("is-path", it.getAttribute("data-id") === id);
      });
    }
    if (kidsOf(id).length) {
      var next = colEl(id, depth + 1);
      wrap.appendChild(next);
      if (wrap.scrollWidth > wrap.clientWidth) wrap.scrollLeft = wrap.scrollWidth;
    }
  }

  // ---------------------------------------------------------------- tree view: brace flow
  // The argument read left to right: a parent { its children stacked to the
  // right.  No boxes -- text, a brace, a state dot.  Only THIS structure
  // carries the literature layer (a purple count beside a paragraph).
  function litOf(id) {
    var L = DATA.literature;
    if (!L || !L.refs) return [];
    var rows = (L.links || {})[id] || [], seen = {}, out = [];
    rows.forEach(function (r) {
      var ref = L.refs[r.key];
      if (!ref || seen[r.key]) return;
      seen[r.key] = 1;
      out.push({ ref: ref, how: r.how, chapter: r.chapter, text: r.text,
                 role: r.role || (((ref.roles || {})[String(r.chapter)] || {}).role) || "" });
    });
    return out;
  }
  function citingNodes(key) {
    var L = DATA.literature, out = [];
    if (!L) return out;
    Object.keys(L.links || {}).forEach(function (nid) {
      if (node(nid) && (L.links[nid] || []).some(function (r) { return r.key === key; })) out.push(nid);
    });
    return out;
  }
  function blabEl(id, n, level) {
    var kids = kidsOf(id), brief = briefOf(id, n), open = isOpen(id, level) || id === S.snapshot.root;
    var lab = el("div", "blab lv" + level + (S.selected === id ? " is-sel" : ""));
    lab.setAttribute("data-id", id);
    lab.setAttribute("data-level", String(level));
    lab.setAttribute("tabindex", "0");
    lab.setAttribute("role", "treeitem");
    if (kids.length || brief) lab.setAttribute("aria-expanded", open ? "true" : "false");
    var chev = el("span", "chev" + (kids.length || brief ? "" : " none"), "▶");
    chev.setAttribute("data-act", "toggle");
    lab.appendChild(chev);
    if (id !== S.snapshot.root) lab.appendChild(dotEl(n));
    if (S.numbering && id !== S.snapshot.root) lab.appendChild(el("span", "num", numberOf(id)));
    var ttl = el("span", "ttl", titleOf(id, n) + (dirty(id) ? " ·改" : ""));
    ttl.title = brief || "";
    lab.appendChild(ttl);
    var tag = tagEl(id, n);
    if (tag) lab.appendChild(tag);
    var lit = litOf(id).length;
    if (lit) {
      var lp = el("span", "lit", String(lit));
      lp.setAttribute("data-lit", id);
      lp.title = "相关研究 " + lit + " 篇";
      lab.appendChild(lp);
    }
    if (kids.length && !open) lab.appendChild(el("span", "cnt", String(kids.length)));
    return lab;
  }
  function bnEl(id, level) {
    var n = node(id), kids = kidsOf(id);
    var open = isOpen(id, level) || id === S.snapshot.root;
    var bn = el("div", "bn" + (open ? " open" : "") + (isFaded(n) ? " faded" : ""));
    bn.setAttribute("data-id", id);
    bn.setAttribute("data-level", String(level));
    var col = el("div", "bcol");
    col.appendChild(blabEl(id, n, level));
    var brief = briefOf(id, n);
    if (open && brief && id !== S.snapshot.root) col.appendChild(el("div", "bbrief", brief));
    bn.appendChild(col);
    if (open && kids.length) {
      var br = el("div", "brace");
      br.appendChild(el("div", "h hu"));
      br.appendChild(el("div", "nib"));
      br.appendChild(el("div", "h hd"));
      bn.appendChild(br);
      var bk = el("div", "bkids");
      kids.forEach(function (k) { bk.appendChild(bnEl(k, level + 1)); });
      bn.appendChild(bk);
    }
    return bn;
  }
  function renderTreeView() {
    var root = S.snapshot.root;
    if (!root || !kidsOf(root).length) {
      stage.replaceChildren(el("p", "empty", "叙事树还是空的：根节点下没有章节。"));
      return;
    }
    var wrap = el("div", "bracetree");
    wrap.appendChild(bnEl(root, 0));
    stage.replaceChildren(wrap);
  }
  // In-place: only this node's subtree is rebuilt.
  function toggleBrace(id, force) {
    var bn = stage.querySelector('.bn[data-id="' + cssId(id) + '"]');
    if (!bn) return false;
    var level = parseInt(bn.getAttribute("data-level"), 10) || 0;
    var now = isOpen(id, level), next = force === undefined ? !now : !!force;
    if (next !== now) setOpen(id, next);
    bn.replaceWith(bnEl(id, level));
    return true;
  }

  // ---------------------------------------------------------------- search
  function searchHits(q) {
    var needle = q.toLowerCase(), hits = [];
    (function walk(id) {
      kidsOf(id).forEach(function (kid) {
        var n = node(kid);
        var hay = [titleOf(kid, n), n.summary || "", n.statement || ""].join("\n").toLowerCase();
        if (hay.indexOf(needle) >= 0) hits.push(kid);
        walk(kid);
      });
    })(S.snapshot.root);
    return hits;
  }
  function highlight(text, q) {
    var h = esc(text), i = h.toLowerCase().indexOf(q.toLowerCase());
    if (i < 0) return h;
    return h.slice(0, i) + "<mark>" + h.slice(i, i + q.length) + "</mark>" + h.slice(i + q.length);
  }
  function renderSearch() {
    var q = S.q, hits = searchHits(q);
    var ul = el("ul", "hits");
    if (!hits.length) ul.appendChild(el("p", "empty", "没有找到「" + q + "」。"));
    hits.forEach(function (id) {
      var n = node(id);
      var li = el("li", "hit");
      li.setAttribute("data-id", id);
      li.setAttribute("tabindex", "0");
      li.setAttribute("role", "button");
      var crumb = ancestorsOf(id).reverse().map(function (a) { return titleOf(a); }).join(" › ");
      if (crumb) li.appendChild(el("div", "crumb", crumb));
      var t = el("div", "ttl");
      t.innerHTML = highlight(titleOf(id, n), q);
      li.appendChild(t);
      var text = String(n.summary || n.statement || "").replace(/\*\*/g, "").replace(/\s+/g, " ");
      var at = text.toLowerCase().indexOf(q.toLowerCase());
      if (text) {
        var start = Math.max(0, at - 60);
        var ex = (start ? "…" : "") + text.slice(start, start + 160) + (text.length > start + 160 ? "…" : "");
        var x = el("div", "ex");
        x.innerHTML = highlight(ex, q);
        li.appendChild(x);
      }
      ul.appendChild(li);
    });
    stage.replaceChildren(ul);
  }

  // ---------------------------------------------------------------- reveal
  function reveal(id) {
    var anc = ancestorsOf(id).reverse();     // root-side first
    if (S.view === "cols") {
      S.path = anc.slice();
      lsSet(":path", S.path);
    } else {
      anc.forEach(function (a) { setOpen(a, true); });
    }
    renderStage();
    var target = stage.querySelector('[data-id="' + cssId(id) + '"]');
    if (target) target.scrollIntoView({ block: "center", inline: "nearest" });
  }

  // ---------------------------------------------------------------- selection
  function select(id, focus) {
    S.selected = id;
    lsSet(":sel", id || null);                 // a reload comes back to the same paragraph
    Array.prototype.forEach.call(stage.querySelectorAll(".is-sel"), function (x) { x.classList.remove("is-sel"); });
    Array.prototype.forEach.call(stage.querySelectorAll('[data-id="' + cssId(id) + '"]'), function (x) {
      if (x.classList.contains("row") || x.classList.contains("item") || x.classList.contains("blab")) {
        x.classList.add("is-sel");
        if (focus) x.focus();
      }
    });
    renderInspector();
  }

  // ---------------------------------------------------------------- stage
  function renderStage() {
    var st = stage.scrollTop, sl = stage.scrollLeft;
    if (S.q) renderSearch();
    else if (S.view === "cols") renderCols();
    else if (S.view === "outline") renderOutline();
    else renderTreeView();
    stage.scrollTop = st;
    stage.scrollLeft = sl;
  }
  function renderTop() {
    var root = S.snapshot.root, rn = node(root) || {};
    document.getElementById("ptitle").textContent = titleOf(root, rn) || DATA.project;
    var ns = rn.statement || rn.summary || "";
    var pns = document.getElementById("pns");
    pns.textContent = firstSentence(ns, 120);
    pns.title = ns;
    Array.prototype.forEach.call(document.querySelectorAll("[data-view]"), function (b) {
      b.className = b.getAttribute("data-view") === S.view ? "on" : "";
      b.setAttribute("aria-pressed", b.getAttribute("data-view") === S.view ? "true" : "false");
    });
    document.getElementById("numbering").checked = S.numbering;
    document.getElementById("patchbtn").textContent =
      S.ops.length || S.todos.length ? "导出修改（" + (S.ops.length + S.todos.length) + "）" : "导出修改";
    var count = Object.keys(S.snapshot.nodes).length - 1;
    document.getElementById("srcline").textContent =
      (DATA.ref === "main" ? "主线" : DATA.ref) + " · 快照 " + fmtDate(DATA.commit.at || DATA.generated_at) +
      " · " + count + " 段";
  }
  function renderAll() {
    renderTop();
    renderStage();
    renderInspector();
  }

  // ---------------------------------------------------------------- inspector
  function linkTo(id, text) {
    var a = el("a", "", text || titleOf(id));
    a.setAttribute("data-go", id);
    a.setAttribute("tabindex", "0");
    return a;
  }
  function section(title) {
    var h = el("h3", "", title);
    return h;
  }
  var LIST_CAP = 8;
  // A link list that folds its tail: the overview must stay a glance, not a ledger.
  function capped(ids) {
    var ul = el("ul", "linklist");
    function row(id) {
      var li = el("li");
      li.appendChild(dotEl(node(id)));
      li.appendChild(linkTo(id));
      return li;
    }
    ids.slice(0, LIST_CAP).forEach(function (id) { ul.appendChild(row(id)); });
    if (ids.length > LIST_CAP) {
      var tail = el("li");
      var more = el("button", "", "还有 " + (ids.length - LIST_CAP) + " 项");
      more.type = "button";
      more.addEventListener("click", function () {
        ul.removeChild(tail);
        ids.slice(LIST_CAP).forEach(function (id) { ul.appendChild(row(id)); });
      });
      tail.appendChild(more);
      ul.appendChild(tail);
    }
    return ul;
  }
  function overviewEl() {
    var card = DATA.card || {}, ra = S.snapshot.role_assignments || {};
    var root = S.snapshot.root, rn = node(root) || {};
    var box = el("div");
    box.appendChild(el("h2", "", titleOf(root, rn) || DATA.project));
    var ns = rn.statement || rn.summary || "";
    if (ns) { var p = el("div", "prose dim"); p.innerHTML = mdLite(ns); box.appendChild(p); }
    box.appendChild(el("p", "hint", S.live ? "点开一段即可阅读，也可以直接对它提问。" : "点开一段即可阅读。"));

    var roles = ROLE_ORDER.filter(function (r) { return ra[r] && node(ra[r]); });
    if (roles.length) {
      box.appendChild(section("承担角色的段落"));
      var ul = el("ul", "linklist");
      roles.forEach(function (r) {
        var li = el("li");
        li.appendChild(el("span", "lab", ROLE_CN[r]));
        li.appendChild(linkTo(ra[r]));
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }
    var bets = (card.live_bets || []).filter(function (b) { return node(b.node); });
    if (bets.length) {
      box.appendChild(section("还在飞的赌注（" + bets.length + "）"));
      box.appendChild(capped(bets.map(function (b) { return b.node; })));
    }
    var debt = (card.lever_debt || []).filter(function (d) { return node(d.node); });
    var lost = (card.lost_levers || []).filter(function (d) { return node(d.node); });
    if (debt.length || lost.length) {
      box.appendChild(section("曾经承重、后来被放下的段落"));
      var ul3 = el("ul", "linklist");
      debt.concat(lost).forEach(function (d) {
        var li = el("li");
        li.appendChild(el("span", "lab", (d.roles_held || []).map(function (r) { return ROLE_CN[r] || r; }).join("/") || "承重"));
        li.appendChild(linkTo(d.node));
        if (d.cause) li.appendChild(el("span", "hint", CAUSE_CN[d.cause] || d.cause));
        ul3.appendChild(li);
      });
      box.appendChild(ul3);
    }
    var ub = card.unresolved_branches || [];
    if (ub.length) {
      box.appendChild(section("尚未了结的支线"));
      var ul4 = el("ul", "list");
      ub.forEach(function (b) { ul4.appendChild(el("li", "", b.title || b.name || b.branch || String(b))); });
      box.appendChild(ul4);
    }
    box.appendChild(techEl(null));
    return box;
  }
  function techEl(id) {
    var d = el("details", "tech");
    d.appendChild(el("summary", "", "技术细节"));
    var rows = [];
    if (id) {
      var n = node(id), h = (DATA.hashes || {})[id] || {};
      rows.push(["编号", id]);
      rows.push(["类型", (TYPE_CN[n.node_type] || n.node_type || "") + " (" + (n.node_type || "") + ")"]);
      rows.push(["版本", short(h.rev, 12) + " / 子树 " + short(h.subtree, 12)]);
      (n.basis_refs || []).forEach(function (b) { rows.push(["依据", b]); });
      (n.lineage || []).forEach(function (l) { rows.push(["谱系", l.rel + " of " + l.of]); });
      ((DATA.blame || {})[id] || []).forEach(function (b) {
        rows.push(["提交", b.commit + " · " + (b.message || "")]);
      });
      if (dirty(id)) rows.push(["补丁", "本段有未导出的改动"]);
    } else {
      rows.push(["分支", DATA.ref]);
      rows.push(["提交", short((DATA.commit || {}).id, 12)]);
      rows.push(["树哈希", short(DATA.tree_hash, 12)]);
      rows.push(["生成", DATA.generated_at || ""]);
    }
    rows.forEach(function (r) {
      var kv = el("div", "kv");
      kv.appendChild(el("b", "", r[0]));
      kv.appendChild(el("span", "mono", r[1]));
      d.appendChild(kv);
    });
    return d;
  }
  function nodeEl(id) {
    var n = node(id);
    var box = el("div");
    var anc = ancestorsOf(id).reverse();
    var crumbs = el("nav", "crumbs");
    crumbs.appendChild(linkTo(null, "总览")).setAttribute("data-go", "");
    anc.forEach(function (a) {
      crumbs.appendChild(el("span", "", "›"));
      crumbs.appendChild(linkTo(a));
    });
    box.appendChild(crumbs);
    box.appendChild(el("h2", "", (S.numbering ? numberOf(id) + "  " : "") + titleOf(id, n)));

    var meta = el("div", "metaline");
    roleOf(id).forEach(function (r) { meta.appendChild(el("span", "tag", ROLE_CN[r])); });
    meta.appendChild(el("span", "", stateWords(n).join(" · ")));
    if ((n.state_meta || {}).live_bet) meta.appendChild(el("span", "tag", "在飞"));
    var kids = kidsOf(id);
    if (kids.length) meta.appendChild(el("span", "hint", "下辖 " + kids.length + " 段"));
    box.appendChild(meta);

    var brief = stripMarks(n.summary).trim();
    var body = stripMarks(n.statement).trim();
    var briefCore = brief.replace(/[…\.\s]+$/, "").replace(/\s+/g, "");
    var bodyCore = body.replace(/\s+/g, "");
    // Repeats if the brief is the body's opening, or the two are near-identical
    // short texts (backfilled nodes often carry the same sentence twice).
    var repeats = body && briefCore && (bodyCore.indexOf(briefCore) === 0 ||
                  (bodyCore.length < 200 && similarity(briefCore, bodyCore) >= 0.6));
    if (brief && !repeats && brief !== titleOf(id, n) && brief !== String(n.title || "").trim()) {
      box.appendChild(section("简介"));
      var p1 = el("div", "prose");
      p1.innerHTML = mdLite(brief);
      box.appendChild(p1);
    }
    if (body && body !== titleOf(id, n) && body !== String(n.title || "").trim()) {
      box.appendChild(section("正文"));
      var p2 = el("div", "prose");
      p2.innerHTML = mdLite(body);
      box.appendChild(p2);
    }
    if ((n.state_meta || {}).resolution_condition) {
      box.appendChild(section("结算条件"));
      box.appendChild(el("div", "prose", n.state_meta.resolution_condition));
    }
    if ((n.disposition_meta || {}).target) {
      box.appendChild(section("去向"));
      var dv = el("div", "prose");
      dv.appendChild(document.createTextNode("并入 "));
      dv.appendChild(linkTo(n.disposition_meta.target));
      if (n.disposition_meta.reason) dv.appendChild(el("span", "hint", " · " + n.disposition_meta.reason));
      box.appendChild(dv);
    }
    if (kids.length) {
      box.appendChild(section("下级"));
      var ul = el("ul", "linklist");
      kids.forEach(function (k) {
        var li = el("li");
        li.appendChild(dotEl(node(k)));
        li.appendChild(linkTo(k));
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }

    // Literature -- only the 树形 structure carries it (its responsibility);
    // 目录 / 分栏 stay pure structure.
    var lits = S.view === "tree" ? litOf(id) : [];
    if (lits.length) {
      var lh = section("相关研究（" + lits.length + "）");
      lh.id = "lit-section";
      box.appendChild(lh);
      var lul = el("ul", "litlist");
      lits.forEach(function (x) {
        var li = el("li");
        var a = el("a", "", x.ref.cite);
        a.setAttribute("data-ref", x.ref.key);
        a.setAttribute("tabindex", "0");
        li.appendChild(a);
        if (x.ref.title) li.appendChild(el("span", "lt", x.ref.title));
        if (x.role) li.appendChild(el("div", "lr", x.role));
        else if (x.how === "mention") li.appendChild(el("div", "lr hint", "正文提及"));
        else if (x.ref.report && x.ref.report.use_in_paper) li.appendChild(el("div", "lr", x.ref.report.use_in_paper));
        lul.appendChild(li);
      });
      box.appendChild(lul);
    }

    // Q&A -- one thread: filed answers, questions still being answered, and
    // (static page) questions waiting for the controller.
    box.appendChild(section("问答"));
    var thread = el("div", "thread");
    thread.id = "qa-thread";
    box.appendChild(thread);
    fillThread(thread, id);
    if (S.live) syncQa(id);
    var fld = el("label", "field");
    fld.appendChild(el("span", "", S.live ? "对这一段提问" : "对这一段提问（记为待回答）"));
    var ta = el("textarea");
    ta.id = "ask-box";
    fld.appendChild(ta);
    box.appendChild(fld);
    var btns = el("div", "btns");
    var ask = el("button", "primary", "提问");
    ask.type = "button";
    ask.id = "ask-btn";
    btns.appendChild(ask);
    box.appendChild(btns);

    // history
    var blame = (DATA.blame || {})[id] || [];
    if (blame.length) {
      box.appendChild(section("这一段的历史"));
      var hl = el("ul", "hist");
      blame.forEach(function (b) {
        var li = el("li");
        li.appendChild(el("div", "when", fmtDate(b.at) + " · " + (KIND_CN[b.kind] || b.kind || "")));
        var changes = b.changes || [];
        if (!changes.length) li.appendChild(el("div", "what", "这一轮涉及了这一段，但没有改它的文字"));
        changes.forEach(function (c) {
          if (c.kind === "text") { li.appendChild(el("div", "what", c.text)); return; }
          if (c.kind === "field") {
            var longer = (c.before || "").length + (c.after || "").length > 70;
            var row = el("div", "chg" + (longer ? " stack" : ""));
            row.appendChild(el("span", "lab", c.field));
            if (longer) {                       // long text: 原 / 现 on two lines, no strike-through wall
              if (c.before) { var o = el("div", "old2"); o.appendChild(el("span", "lab", "原")); o.appendChild(document.createTextNode(c.before)); row.appendChild(o); }
              var nw = el("div", "new2"); nw.appendChild(el("span", "lab", "现")); nw.appendChild(document.createTextNode(c.after || "（删去）")); row.appendChild(nw);
            } else {
              if (c.before) row.appendChild(el("span", "old", c.before));
              if (c.before && c.after) row.appendChild(el("span", "arr", "→"));
              row.appendChild(el("span", "new", c.after || "（删去）"));
            }
            li.appendChild(row);
            return;
          }
          if (c.kind === "diff") {
            var head = el("button", "difftoggle", "正文改动：+" + c.ins + " 句 −" + c.del + " 句 · 看差异");
            head.type = "button";
            var body = el("div", "diff");
            body.hidden = true;
            (c.segments || []).forEach(function (s) {
              body.appendChild(el("span", s.t === "-" ? "del" : s.t === "+" ? "ins" : "eq", s.s));
            });
            if (c.truncated) body.appendChild(el("span", "eq", "…（差异较长，只显示开头）"));
            head.addEventListener("click", function () {
              body.hidden = !body.hidden;
              head.textContent = "正文改动：+" + c.ins + " 句 −" + c.del + " 句 · " + (body.hidden ? "看差异" : "收起");
            });
            li.appendChild(head);
            li.appendChild(body);
          }
        });
        var why = b.why || {};
        var reasons = [];
        if (why.note) reasons.push(why.note);
        if (why.counterfactual) reasons.push("若无此事：" + why.counterfactual);
        if (why.trigger) reasons.push("触发：" + why.trigger);
        if (why.cause_cn && (why.cause === "unknown" ? !reasons.length : true)) reasons.push(why.cause_cn);
        li.appendChild(el("div", "why", reasons.length ? "原因：" + reasons.join(" · ") : "原因：未记录"));
        var askIt = el("button", "asklink", "问这次改动");
        askIt.type = "button";
        askIt.setAttribute("data-ask", "这一段在 " + fmtDate(b.at) + " 的那次改动（" +
          changes.filter(function (c) { return c.kind === "field"; }).slice(0, 2).map(function (c) {
            return c.field + "从「" + (c.before || "无") + "」改成「" + (c.after || "无") + "」";
          }).join("，") + "）是为什么？依据是什么？");
        li.appendChild(askIt);
        hl.appendChild(li);
      });
      box.appendChild(hl);
    }

    if (S.edit) box.appendChild(editorEl(id));
    var ps = patchSummaryEl();
    if (ps) box.appendChild(ps);
    box.appendChild(techEl(id));
    return box;
  }
  function optionsEl(selectId, ids) {
    var sel = el("select");
    sel.id = selectId;
    ids.forEach(function (k) {
      var o = el("option", "", (S.numbering ? numberOf(k) + " " : "") + titleOf(k));
      o.value = k;
      sel.appendChild(o);
    });
    return sel;
  }
  function fieldEl(label, input) {
    var f = el("label", "field");
    f.appendChild(el("span", "", label));
    f.appendChild(input);
    return f;
  }
  function btnEl(label, edit, disabled) {
    var b = el("button", "", label);
    b.type = "button";
    b.setAttribute("data-edit", edit);
    if (disabled) b.disabled = true;
    return b;
  }
  function editorEl(id) {
    var n = node(id);
    var sibs = siblingsOf(id), pos = sibs.indexOf(id);
    var banned = [id].concat(descendantsOf(id));
    var box = el("div");
    box.appendChild(section("修改（只是提案，导出后由主控核对入账）"));
    var t = el("input"); t.id = "ed-title"; t.value = n.title || "";
    box.appendChild(fieldEl("标题", t));
    var s = el("textarea"); s.id = "ed-summary"; s.value = n.summary || "";
    box.appendChild(fieldEl("简介", s));
    var b1 = el("div", "btns");
    b1.appendChild(btnEl("记录改写", "fields"));
    b1.appendChild(btnEl("← 前移", "up", pos <= 0));
    b1.appendChild(btnEl("后移 →", "down", pos < 0 || pos >= sibs.length - 1));
    box.appendChild(b1);
    var options = Object.keys(S.snapshot.nodes).filter(function (k) {
      return banned.indexOf(k) < 0 && k !== parentOf(id) && k !== S.snapshot.root;
    });
    if (options.length) {
      box.appendChild(fieldEl("把这一段（连同下级）移到", optionsEl("ed-parent", options)));
      var b2 = el("div", "btns"); b2.appendChild(btnEl("移动", "move")); box.appendChild(b2);
    }
    var mergeable = sibs.filter(function (k) { return k !== id; });
    if (mergeable.length) {
      box.appendChild(fieldEl("把这一段并入同级的", optionsEl("ed-merge", mergeable)));
      var b3 = el("div", "btns"); b3.appendChild(btnEl("合并", "merge")); box.appendChild(b3);
    }
    var sp = el("textarea"); sp.id = "ed-split";
    box.appendChild(fieldEl("拆分：粘贴要拆出的那一段正文", sp));
    var st = el("input"); st.id = "ed-split-title";
    box.appendChild(fieldEl("拆出段的标题", st));
    var b4 = el("div", "btns"); b4.appendChild(btnEl("拆分", "split")); box.appendChild(b4);
    var td = el("input"); td.id = "ed-todo";
    box.appendChild(fieldEl("给这一段记一条待办", td));
    var b5 = el("div", "btns"); b5.appendChild(btnEl("记录待办", "todo")); box.appendChild(b5);
    return box;
  }
  function patchSummaryEl() {
    if (!S.ops.length && !S.todos.length) return null;
    var box = el("div");
    box.appendChild(section("待导出的修改"));
    var ul = el("ul", "list");
    var OP_CN = { patch_fields: "改写", move_node: "移动", merge_nodes: "合并", split_node: "拆分", add_node: "新增" };
    S.ops.forEach(function (op) {
      var who = op.id ? titleOf(op.id) : (op.ids || []).map(function (k) { return titleOf(k); }).join(" + ");
      ul.appendChild(el("li", "", (OP_CN[op.op] || op.op) + "：" + who));
    });
    S.todos.forEach(function (t) { ul.appendChild(el("li", "", "待办：" + titleOf(t.node_id) + " — " + t.text)); });
    box.appendChild(ul);
    var btns = el("div", "btns");
    var u = el("button", "", "撤销上一步"); u.type = "button"; u.id = "undo-btn";
    var c = el("button", "", "清空"); c.type = "button"; c.id = "clear-btn";
    btns.appendChild(u); btns.appendChild(c);
    box.appendChild(btns);
    return box;
  }
  // One study: bibliographic record, the three-line report (contribution /
  // boundary relative to this paper / how the paper uses it), verification,
  // links to the original, and the paragraphs that rest on it.
  function refEl(key) {
    var L = DATA.literature || {}, r = (L.refs || {})[key];
    var box = el("div");
    var back = el("a", "back", "← 回到「" + (S.selected && node(S.selected) ? titleOf(S.selected) : "总览") + "」");
    back.setAttribute("data-go", S.selected || "");
    back.setAttribute("tabindex", "0");
    box.appendChild(back);
    if (!r) { box.appendChild(el("p", "hint", "台账里没有这条文献。")); return box; }
    box.appendChild(el("h2", "", r.cite));
    var bib = (r.title ? "“" + r.title + "”" : "") + (r.venue ? " · " + r.venue : "") +
              (r.volume ? " " + r.volume : "") + (r.number ? "(" + r.number + ")" : "") +
              (r.pages ? ", " + r.pages : "") + (r.year ? " · " + r.year : "");
    box.appendChild(el("div", "prose dim", bib));
    var meta = el("div", "metaline");
    var v = (r.verified || {}).status;
    meta.appendChild(el("span", "tag " + (v === "verified" ? "ok" : v === "unverified" ? "warn" : ""),
                        v === "verified" ? "书目已核实" : v === "unverified" ? "链接未核实" : "未核实"));
    var chs = (r.cited_in_chapters || []).filter(function (c) { return c; });
    if (chs.length) meta.appendChild(el("span", "hint", "被引于第 " + chs.join("、") + " 章"));
    box.appendChild(meta);
    var rep = r.report;
    box.appendChild(section("贡献"));
    box.appendChild(el("div", "prose", rep && rep.contribution ? rep.contribution : "报告未生成"));
    box.appendChild(section("边界（相对本文）"));
    box.appendChild(el("div", "prose", rep && rep.boundary ? rep.boundary : "报告未生成"));
    box.appendChild(section("本文怎么用它"));
    box.appendChild(el("div", "prose", rep && rep.use_in_paper ? rep.use_in_paper : "报告未生成"));
    var here = S.selected ? litOf(S.selected).filter(function (x) { return x.ref.key === key; })[0] : null;
    var role = here && here.chapter ? (r.roles || {})[String(here.chapter)] : null;
    if (role && (role.role || role.focus)) {
      box.appendChild(section("在这一章的角色"));
      if (role.role) box.appendChild(el("div", "prose", role.role));
      if (role.focus) box.appendChild(el("div", "hint", "读时看：" + role.focus));
    }
    if ((r.map_notes || []).length) {
      box.appendChild(section("文献地图备注"));
      r.map_notes.forEach(function (n) { box.appendChild(el("div", "prose dim", n)); });
    }
    box.appendChild(section("原文与链接"));
    var links = el("div", "extlinks");
    if (r.doi) { var d = el("a", "", "DOI " + r.doi); d.href = "https://doi.org/" + r.doi; d.target = "_blank"; d.rel = "noopener"; links.appendChild(d); }
    if (r.url && (!r.doi || r.url.indexOf(r.doi) < 0)) { var u = el("a", "", "链接"); u.href = r.url; u.target = "_blank"; u.rel = "noopener"; links.appendChild(u); }
    if (!r.doi && !r.url) links.appendChild(el("span", "hint", "台账里没有链接"));
    box.appendChild(links);
    var users = citingNodes(key);
    if (users.length) {
      box.appendChild(section("依托它的段落（" + users.length + "）"));
      box.appendChild(capped(users));
    }
    var btns = el("div", "btns");
    var ask = el("button", "", "问：它对这一段的贡献和边界");
    ask.type = "button";
    ask.setAttribute("data-askref", "「" + r.cite + "」对这一段的贡献是什么？它的边界在哪里，本文在哪一步用它？");
    btns.appendChild(ask);
    box.appendChild(btns);
    if (rep && rep.at) box.appendChild(el("div", "hint", "报告由证据管家依据本地材料写成 · " + fmtDate(rep.at)));
    return box;
  }
  function renderInspector() {
    var id = S.selected;
    var st = inspector.scrollTop;
    if (S.ref && S.view === "tree") inspector.replaceChildren(refEl(S.ref));
    else inspector.replaceChildren(id && node(id) ? nodeEl(id) : overviewEl());
    inspector.scrollTop = id ? 0 : st;
    wireInspector();
  }
  function wireInspector() {
    var ask = document.getElementById("ask-btn");
    if (ask) ask.addEventListener("click", askQuestion);
    var askBox = document.getElementById("ask-box");
    if (askBox) askBox.addEventListener("keydown", function (e) {   // Enter sends, Shift+Enter breaks a line
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); askQuestion(); }
    });
    Array.prototype.forEach.call(inspector.querySelectorAll("[data-ref]"), function (a) {
      a.addEventListener("click", function () { S.ref = a.getAttribute("data-ref"); renderInspector(); });
      a.addEventListener("keydown", function (e) { if (e.key === "Enter") a.click(); });
    });
    Array.prototype.forEach.call(inspector.querySelectorAll("[data-askref]"), function (btn) {
      btn.addEventListener("click", function () {         // back to the paragraph, question pre-filled
        var text = btn.getAttribute("data-askref");
        S.ref = null;
        renderInspector();
        var boxEl = document.getElementById("ask-box");
        if (!boxEl) return;
        boxEl.value = text;
        boxEl.scrollIntoView({ block: "center" });
        boxEl.focus();
      });
    });
    Array.prototype.forEach.call(inspector.querySelectorAll("[data-ask]"), function (btn) {
      btn.addEventListener("click", function () {         // a history entry pre-fills the question
        var boxEl = document.getElementById("ask-box");
        if (!boxEl) return;
        boxEl.value = btn.getAttribute("data-ask");
        boxEl.scrollIntoView({ block: "center" });
        boxEl.focus();
      });
    });
    Array.prototype.forEach.call(inspector.querySelectorAll("[data-edit]"), function (btn) {
      btn.addEventListener("click", function () { editAction(btn.getAttribute("data-edit")); });
    });
    var undo = document.getElementById("undo-btn");
    if (undo) undo.addEventListener("click", function () {
      if (!S.ops.length) { S.todos.pop(); renderAll(); return; }
      S.ops.pop();
      replay();
      renderAll();
      status("已撤销最后一步；还剩 " + S.ops.length + " 步修改");
    });
    var clr = document.getElementById("clear-btn");
    if (clr) clr.addEventListener("click", function () {
      S.ops = [];
      S.todos = [];
      replay();
      renderAll();
      status("修改已清空");
    });
  }

  // ---------------------------------------------------------------- editing
  function nextSplitIds(id, count) {
    var out = [], i = 1;
    while (out.length < count) {
      var candidate = id + "." + i;
      if (!S.snapshot.nodes[candidate] && !BASE.nodes[candidate] && out.indexOf(candidate) < 0) out.push(candidate);
      i += 1;
      if (i > 999) break;
    }
    return out;
  }
  function derivedIdentity(origin, label) {
    var key = clone(origin.identity_key || {});
    var field = IDFIELD[origin.node_type];
    if (field) key[field] = String(key[field] === undefined || key[field] === null ? "" : key[field]) + " · " + label;
    else key.split_label = label;
    return key;
  }
  function newPart(origin, id, title, statement, label) {
    return {
      id: id,
      node_type: origin.node_type,
      identity_key: derivedIdentity(origin, label),
      title: title,
      summary: statement.slice(0, 90),
      statement: statement,
      state: { epistemic: "PENDING", narrative: "ACTIVE" },
      state_meta: { resolution_condition: null, live_bet: false },
      basis_refs: [],
      disposition_meta: {},
      lineage: [{ rel: "decomposes", of: origin.id }],
      children: []
    };
  }
  function editAction(kind) {
    var id = S.selected;
    var n = node(id);
    if (!n) return;
    if (kind === "fields") {
      var title = document.getElementById("ed-title").value;
      var summary = document.getElementById("ed-summary").value;
      var fields = {};
      if (title !== n.title) fields.title = title;
      if (summary !== n.summary) fields.summary = summary;
      if (!Object.keys(fields).length) { status("标题与简介都没有变化", true); return; }
      pushOp({ op: "patch_fields", id: id, fields: fields }, "改写「" + titleOf(id) + "」");
      return;
    }
    if (kind === "up" || kind === "down") {
      var parent = parentOf(id);
      if (!parent) { status("根节点不能重排", true); return; }
      var sibs = S.snapshot.nodes[parent].children || [];
      var pos = sibs.indexOf(id);
      var target = kind === "up" ? pos - 1 : pos + 1;
      if (target < 0 || target >= sibs.length) return;
      pushOp({ op: "move_node", id: id, new_parent: parent, index: target },
             (kind === "up" ? "前移" : "后移") + "「" + titleOf(id) + "」");
      return;
    }
    if (kind === "move") {
      var np = document.getElementById("ed-parent").value;
      if (!np) return;
      pushOp({ op: "move_node", id: id, new_parent: np, index: null }, "移动「" + titleOf(id) + "」到「" + titleOf(np) + "」");
      return;
    }
    if (kind === "merge") {
      var into = document.getElementById("ed-merge").value;
      if (!into) return;
      pushOp({ op: "merge_nodes", ids: [id, into], into: into,
               reason: "UI merge of " + id + " into " + into }, "合并「" + titleOf(id) + "」进「" + titleOf(into) + "」");
      return;
    }
    if (kind === "split") {
      var excerpt = document.getElementById("ed-split").value.trim();
      if (!excerpt) { status("先填要拆出的那一段", true); return; }
      var stmt = n.statement || "";
      var rest = stmt.split(excerpt).join(" ").replace(/\s+/g, " ").trim() || (n.summary || n.title || "");
      var ids = nextSplitIds(id, 2);
      if (ids.length < 2) { status("生成新编号失败", true); return; }
      var partTitle = document.getElementById("ed-split-title").value || (n.title + " · 拆出");
      var parts = [
        newPart(n, ids[0], partTitle, excerpt, partTitle),
        newPart(n, ids[1], (n.title || "段落") + " · 其余", rest, "其余")
      ];
      pushOp({ op: "split_node", id: id, mode: "structural", parts: parts }, "拆分「" + titleOf(id) + "」");
      return;
    }
    if (kind === "todo") {
      var text = document.getElementById("ed-todo").value.trim();
      if (!text) return;
      S.todos.push({ node_id: id, kind: "todo", text: text, proposed_at: nowIso() });
      renderTop();
      renderInspector();
      status("待办已记入修改");
    }
  }

  // ---------------------------------------------------------------- export
  function capability(name) {
    if (typeof window.claude === "undefined" || !window.claude || typeof window.claude.use !== "function") {
      return Promise.resolve(null);
    }
    try {
      return Promise.resolve(window.claude.use(name)).then(function (c) { return c || null; },
                                                           function () { return null; });
    } catch (e) { return Promise.resolve(null); }
  }
  function showRaw(text) {
    S.raw = text;
    var sec = el("div");
    sec.appendChild(section("手动复制"));
    var ta = el("textarea", "raw mono");
    ta.value = text;
    sec.appendChild(ta);
    inspector.insertBefore(sec, inspector.firstChild);
    ta.focus();
    ta.select();
  }
  function exportJson(filename, payload) {
    var text = JSON.stringify(payload, null, 2);
    return capability("downloads").then(function (dl) {
      if (dl && typeof dl.save === "function") {
        return dl.save({ filename: filename, data: text }).then(function () { return "downloads"; },
                                                                function (err) {
          if (err && err.code === "declined") return "declined";
          return null;
        });
      }
      return null;
    }).then(function (done) {
      if (done) return done;
      try {
        var blob = new Blob([text], { type: "application/json" });
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
        return "download";
      } catch (e) { return null; }
    }).then(function (done) {
      if (done) return done;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text).then(function () { return "clipboard"; },
                                                        function () { showRaw(text); return "manual"; });
      }
      showRaw(text);
      return "manual";
    });
  }
  function buildPatch() {
    return {
      schema: PATCH_SCHEMA,
      project: DATA.project,
      ref: DATA.ref,
      base_commit_id: (DATA.commit || {}).id || null,
      base_tree_hash: DATA.tree_hash || null,
      tree_ops: clone(S.ops),
      todo_proposals: clone(S.todos),
      generated_at: nowIso()
    };
  }
  var HOW = { downloads: "已交给查看者保存", download: "已开始下载",
              clipboard: "已复制到剪贴板", manual: "请从上方文本框手动复制",
              declined: "查看者取消了保存" };
  function exportPatch() {
    if (!S.ops.length && !S.todos.length) { status("还没有修改：先勾选「修改」再动手", true); return; }
    var patch = buildPatch();
    exportJson("narrative-patch-" + DATA.ref + ".json", patch).then(function (how) {
      status("修改 " + S.ops.length + " 步 / 待办 " + S.todos.length + " 条 — " + (HOW[how] || how) + "；把文件交给主控即可");
    });
  }

  // ---------------------------------------------------------------- rebuild
  // The page's canonical source, rebuilt from its own SOURCE text (the style,
  // the shell template and this script), never from the live DOM.
  var SHELL_BODY = document.getElementById("ar-shell").innerHTML;
  function rebuildHtml() {
    var css = document.getElementById("ar-css").textContent;
    var app = document.getElementById("ar-app").textContent;
    var payload = clone(DATA);
    payload.pending = { questions: clone(S.questions), notes: (payload.pending || {}).notes || [] };
    var json = JSON.stringify(payload).split("<" + "/").join("<\\/");
    var title = document.title.replace(/[<>&]/g, "");
    var TAG = "<" + "/";
    return "<!doctype html>\n" +
      '<html lang="zh"><head><meta charset="utf-8">\n' +
      '<meta name="viewport" content="width=device-width, initial-scale=1">\n' +
      "<title>" + title + TAG + "title>\n" +
      '<style id="ar-css">' + css + TAG + "style>\n</head>\n<body>\n" +
      '<template id="ar-shell">' + SHELL_BODY + TAG + "template>\n" +
      SHELL_BODY +
      '<script type="application/json" id="ar-snapshot">' + json + TAG + "script>\n" +
      '<script id="ar-app">' + app + TAG + "script>\n" + TAG + "body>" + TAG + "html>\n";
  }
  // ---------------------------------------------------------------- live Q&A
  // Served by `narrative serve`: the question goes to the chronicler with this
  // node's context, the answer streams into the thread, and the server files
  // it as a qa annotation.  From file:// the page only records the question.
  function detectLive() {
    if (DATA.live && DATA.live.model) { S.live = DATA.live; return; }
    if (!/^https?:$/.test(location.protocol) || typeof fetch !== "function") return;
    fetch("/api/ping", { cache: "no-store" }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j && j.live) { S.live = { model: j.model }; renderInspector(); } })
      .catch(function () { /* static host */ });
  }
  function qaEl(question, answer, meta, cls) {
    var qa = el("div", "qa" + (cls ? " " + cls : ""));
    qa.appendChild(el("div", "q", question));
    var a = el("div", "a");
    if (answer) a.innerHTML = mdLite(answer);
    qa.appendChild(a);
    qa.appendChild(el("div", "meta", meta || ""));
    return qa;
  }
  // The thread is rebuilt from three sources every time: filed annotations,
  // in-flight questions (server memory, survives a reload), static-page pending.
  function fillThread(thread, id) {
    thread.replaceChildren();
    var anns = annotationsOf(id), qs = questionsOf(id), pend = S.pending[id] || [];
    if (!anns.length && !qs.length && !pend.length) {
      thread.appendChild(el("div", "hint", "还没有人对这一段提问。"));
      return;
    }
    anns.forEach(function (a) {
      thread.appendChild(qaEl(a.question || ("（" + (a.kind || "备注") + "）"), a.answer,
                              [a.by === "chronicler-live" ? "史官" : a.by, fmtDate(a.at)].filter(Boolean).join(" · ")));
    });
    pend.forEach(function (p) {
      var note = p.error ? "没有答上来：" + p.error : (p.partial ? "正在回答…" : "已发出，正在思考（通常 15–30 秒）…");
      thread.appendChild(qaEl(p.question, p.partial, note, p.error ? "failed" : "busy"));
    });
    qs.forEach(function (q) {
      thread.appendChild(qaEl(q.question, "", "待回答 · " + fmtDate(q.asked_at)));
    });
  }
  function refreshThread(id) {
    var thread = document.getElementById("qa-thread");
    if (thread && S.selected === id) fillThread(thread, id);
  }
  // Poll the node's Q&A while anything is in flight; stop when nothing is.
  function syncQa(id) {
    if (!S.live || typeof fetch !== "function") return;
    if (S.qaTimer) { clearTimeout(S.qaTimer); S.qaTimer = null; }
    fetch("/api/qa/" + encodeURIComponent(id), { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j) return;
        DATA.annotations = DATA.annotations || {};
        DATA.annotations[id] = j.annotations || [];
        S.pending[id] = j.pending || [];
        refreshThread(id);
        var btn = document.getElementById("ask-btn");
        var busy = S.pending[id].some(function (p) { return !p.error; });
        if (btn && S.selected === id) { btn.disabled = busy; btn.textContent = busy ? "回答中…" : "提问"; }
        if (busy && S.selected === id) S.qaTimer = setTimeout(function () { syncQa(id); }, 1500);
      })
      .catch(function () { /* transient; the next selection re-syncs */ });
  }
  function askLive(id, question, box, btn) {
    // Show the question at once, before the server has even replied.
    S.pending[id] = (S.pending[id] || []).concat([{ id: "local", question: question, partial: "", error: null }]);
    refreshThread(id);
    var last = document.querySelector("#qa-thread .qa:last-child");
    if (last && last.scrollIntoView) last.scrollIntoView({ block: "nearest" });
    btn.disabled = true;
    btn.textContent = "回答中…";
    box.value = "";
    status("已发出，正在回答；刷新页面也不会丢");
    fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ node_id: id, question: question }) })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        setTimeout(function () { syncQa(id); }, 300);   // the server now lists it as in flight
        return r.text();                                  // wait for the whole reply
      })
      .then(function (body) {
        var lines = body.split("\n").filter(Boolean), last = null;
        try { last = JSON.parse(lines[lines.length - 1]); } catch (e) { last = null; }
        if (last && last.error) throw new Error(last.error);
        syncQa(id);
        status("已回答并记入这一段的问答");
      })
      .catch(function (err) {
        S.pending[id] = (S.pending[id] || []).filter(function (p) { return p.id !== "local"; });
        syncQa(id);
        status("提问失败：" + (err && err.message || err), true);
      });
  }
  function askQuestion() {
    var box = document.getElementById("ask-box");
    var question = (box && box.value || "").trim();
    if (!question) { status("先写下问题", true); return; }
    var id = S.selected;
    if (S.live) { askLive(id, question, box, document.getElementById("ask-btn")); return; }
    var h = (DATA.hashes || {})[id] || {};
    var pack = {
      schema: QUESTION_SCHEMA,
      project: DATA.project,
      ref: DATA.ref,
      base_commit_id: (DATA.commit || {}).id || null,
      node_id: id,
      subtree_hash: h.subtree || null,
      rev_hash: h.rev || null,
      question: question,
      asked_at: nowIso()
    };
    capability("artifact").then(function (art) {
      if (art && typeof art.publish === "function") {
        S.questions.push(pack);
        return art.publish(rebuildHtml()).then(function () { return "artifact"; }, function (err) {
          S.questions.pop();
          if (err && err.code === "conflict") return "conflict";
          return null;
        });
      }
      return null;
    }).then(function (done) {
      if (done === "artifact") {
        renderInspector();
        status("问题已随页面发布，主控回答后会出现在这一段的问答里");
        return;
      }
      if (done === "conflict") {
        status("页面刚被别人更新，正在重载；请再问一次", true);
        return;
      }
      // Static page: keep the question on the node and hand its packet to the
      // clipboard -- no download, nothing leaves the page on its own.
      S.questions.push(pack);
      renderInspector();
      var text = JSON.stringify(pack, null, 2);
      var copied = navigator.clipboard && navigator.clipboard.writeText
        ? navigator.clipboard.writeText(text).then(function () { return true; }, function () { return false; })
        : Promise.resolve(false);
      return copied.then(function (ok) {
        status(ok ? "已记为待回答，问题已复制到剪贴板；要即时回答请让主控打开实时页"
                  : "已记为待回答；要即时回答请让主控打开实时页");
      });
    });
  }

  // ---------------------------------------------------------------- layout
  function applyLayout() {
    document.getElementById("main").style.setProperty("--insp", Math.round(S.inspW) + "px");
  }
  function wireSplitter() {
    var sp = document.getElementById("splitter"), main = document.getElementById("main");
    var drag = null;
    sp.addEventListener("pointerdown", function (e) {
      drag = { x: e.clientX, w: S.inspW };
      sp.setPointerCapture(e.pointerId);
      document.body.classList.add("dragging");
      e.preventDefault();
    });
    sp.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var max = Math.max(INSP_MIN, main.clientWidth * 0.7);
      S.inspW = Math.min(max, Math.max(INSP_MIN, drag.w - (e.clientX - drag.x)));
      applyLayout();
    });
    function end() {
      if (!drag) return;
      drag = null;
      document.body.classList.remove("dragging");
      lsSet(":insp", S.inspW);
    }
    sp.addEventListener("pointerup", end);
    sp.addEventListener("pointercancel", end);
    sp.addEventListener("dblclick", function () { S.inspW = INSP_DEFAULT; applyLayout(); lsSet(":insp", S.inspW); });
    sp.addEventListener("keydown", function (e) {
      var step = e.key === "ArrowLeft" ? 24 : e.key === "ArrowRight" ? -24 : 0;
      if (!step) return;
      e.preventDefault();
      S.inspW = Math.max(INSP_MIN, S.inspW + step);
      applyLayout();
      lsSet(":insp", S.inspW);
    });
  }
  function applyTheme(mode) {
    if (mode === "light" || mode === "dark") document.documentElement.setAttribute("data-theme", mode);
    else document.documentElement.removeAttribute("data-theme");
    lsSet(":theme", mode);
    Array.prototype.forEach.call(document.querySelectorAll("[data-theme-btn]"), function (b) {
      b.className = b.getAttribute("data-theme-btn") === mode ? "on" : "";
    });
  }
  function setView(view) {
    if (S.view === view) return;
    S.view = view;
    S.ref = null;
    lsSet(":view", view);
    renderTop();
    renderStage();
    if (S.selected) reveal(S.selected);
    status("已切到" + VIEW_CN[view] + "结构");
  }
  function expandOneLevel() {
    if (S.q) return;
    if (S.view === "cols") {
      var path = validPath(), last = path.length ? path[path.length - 1] : S.snapshot.root;
      var first = kidsOf(last)[0];
      if (first && kidsOf(first).length) descendCols(first, path.length);
      return;
    }
    var shut = Array.prototype.filter.call(stage.querySelectorAll(".bn"), function (b) {
      return !b.classList.contains("open") && kidsOf(b.getAttribute("data-id")).length;
    });
    if (shut.length) {
      var minL = Math.min.apply(null, shut.map(function (b) { return parseInt(b.getAttribute("data-level"), 10) || 0; }));
      shut.forEach(function (b) {
        if ((parseInt(b.getAttribute("data-level"), 10) || 0) === minL) toggleBrace(b.getAttribute("data-id"), true);
      });
      return;
    }
    var closed = Array.prototype.filter.call(stage.querySelectorAll("li.nd"), function (li) {
      return !li.classList.contains("open") && kidsOf(li.getAttribute("data-id")).length;
    });
    if (!closed.length) { status("已经全部展开"); return; }
    var minLevel = Math.min.apply(null, closed.map(function (li) {
      return parseInt(li.querySelector(":scope > .row").getAttribute("data-level"), 10) || 1;
    }));
    closed.forEach(function (li) {
      var lv = parseInt(li.querySelector(":scope > .row").getAttribute("data-level"), 10) || 1;
      if (lv === minLevel) toggle(li.getAttribute("data-id"), true);
    });
  }
  function collapseAll() {
    S.open = {};
    Object.keys(S.snapshot.nodes).forEach(function (k) { S.open[k] = 0; });
    lsSet(":open", S.open);
    if (S.view === "cols") { S.path = []; lsSet(":path", S.path); }
    renderStage();
  }

  // ---------------------------------------------------------------- events
  function focusables() {
    return Array.prototype.filter.call(stage.querySelectorAll(".row, .item, .blab, .hit"), function (x) {
      return x.offsetParent !== null;
    });
  }
  function wire() {
    stage.addEventListener("click", function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      var act = t.closest("[data-act]");
      if (act) {
        e.stopPropagation();
        var host = act.closest(".row") || act.closest(".blab");
        if (host) toggle(host.getAttribute("data-id"));
        return;
      }
      var lit = t.closest("[data-lit]");
      if (lit) {                                   // the purple count: open this paragraph's studies
        e.stopPropagation();
        select(lit.getAttribute("data-lit"));
        var sec = document.getElementById("lit-section");
        if (sec) sec.scrollIntoView({ block: "start" });
        return;
      }
      var blab = t.closest(".blab");
      if (blab) {
        var bid = blab.getAttribute("data-id");
        select(bid);
        var bnode = blab.closest(".bn");
        if (bnode && !bnode.classList.contains("open") && !blab.querySelector(".chev.none")) toggleBrace(bid, true);
        return;
      }
      var row = t.closest(".row");
      if (row) {
        var id = row.getAttribute("data-id");
        select(id);
        var li = row.closest("li.nd");
        if (li && !li.classList.contains("open") && !row.querySelector(".chev.none")) toggle(id, true);
        return;
      }
      var item = t.closest(".item");
      if (item) {
        var iid = item.getAttribute("data-id");
        var depth = parseInt(item.closest(".col").getAttribute("data-depth"), 10) || 0;
        select(iid);
        descendCols(iid, depth);
        return;
      }
      var hit = t.closest(".hit");
      if (hit) {
        var hid = hit.getAttribute("data-id");
        S.q = "";
        document.getElementById("search").value = "";
        reveal(hid);
        select(hid, true);
      }
    });
    stage.addEventListener("keydown", function (e) {
      var host = e.target && e.target.closest ? e.target.closest(".row, .item, .blab, .hit") : null;
      if (!host) return;
      var id = host.getAttribute("data-id");
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
        e.preventDefault();
        host.click();
      } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        var isRow = host.classList.contains("row"), isBlab = host.classList.contains("blab");
        if (!isRow && !isBlab) return;
        e.preventDefault();
        var holder = host.closest(isRow ? "li.nd" : ".bn"), open = holder.classList.contains("open");
        if (e.key === "ArrowRight") { if (!open) toggle(id, true); }
        else if (open) toggle(id, false);
        else { var p = parentOf(id); var prow = p && stage.querySelector((isRow ? '.row' : '.blab') + '[data-id="' + cssId(p) + '"]'); if (prow) prow.focus(); }
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        var list = focusables(), i = list.indexOf(host);
        var next = list[i + (e.key === "ArrowDown" ? 1 : -1)];
        if (next) next.focus();
      }
    });
    inspector.addEventListener("click", function (e) {
      var go = e.target && e.target.closest ? e.target.closest("[data-go]") : null;
      if (!go) return;
      var id = go.getAttribute("data-go");
      S.ref = null;
      if (!id) { S.selected = null; select(null); return; }
      if (!node(id)) return;
      reveal(id);
      select(id);
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-view]"), function (b) {
      b.addEventListener("click", function () { setView(b.getAttribute("data-view")); });
    });
    var search = document.getElementById("search"), timer = null;
    search.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        S.q = search.value.trim();
        renderStage();
        if (S.q) status("搜索「" + S.q + "」：" + stage.querySelectorAll(".hit").length + " 段命中");
      }, 120);
    });
    search.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { search.value = ""; S.q = ""; renderStage(); }
    });
    document.getElementById("expand-one").addEventListener("click", expandOneLevel);
    document.getElementById("collapse-all").addEventListener("click", collapseAll);
    document.getElementById("numbering").addEventListener("change", function (e) {
      S.numbering = !!e.target.checked;
      lsSet(":num", S.numbering);
      renderStage();
      renderInspector();
    });
    document.getElementById("patchbtn").addEventListener("click", exportPatch);
    var editBox = document.getElementById("editmode");
    editBox.addEventListener("change", function () {
      S.edit = editBox.checked;
      renderInspector();
      status(S.edit ? "修改模式：改动只累积成提案，页面永不直接写研究状态" : "只读模式");
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-theme-btn]"), function (b) {
      b.addEventListener("click", function () { applyTheme(b.getAttribute("data-theme-btn")); });
    });
    wireSplitter();
  }

  function selectAfterLoad() {
    // Come back to the paragraph the reader had open; otherwise land on the overview.
    var last = lsGet(":sel", null);
    if (last && node(last)) { reveal(last); select(last); return; }
    select(null);
  }

  rebuildParents();
  applyTheme(lsGet(":theme", "system"));
  applyLayout();
  wire();
  detectLive();
  renderAll();
  selectAfterLoad();
  status((Object.keys(S.snapshot.nodes).length - 1) + " 段 · 点标题看内容，点三角展开下级 · 拖动中缝调整比例");
})();
"""


SHELL_BODY = """<header class="top">
  <div class="brand"><span class="ptitle" id="ptitle"></span><span class="pns" id="pns"></span></div>
  <div class="seg" role="group" aria-label="结构"><button type="button" data-view="tree">树形</button><button type="button" data-view="outline">目录</button><button type="button" data-view="cols">分栏</button></div>
  <input class="search" id="search" type="search" placeholder="搜索" aria-label="搜索">
  <span class="group"><button type="button" id="expand-one">展开一层</button><button type="button" id="collapse-all">全部收起</button></span>
  <label><input type="checkbox" id="numbering">编号</label>
  <span class="grow"></span>
  <label><input type="checkbox" id="editmode">修改</label>
  <button type="button" id="patchbtn">导出修改</button>
  <span class="divider"></span>
  <span class="group"><button type="button" data-theme-btn="system">跟随系统</button><button type="button" data-theme-btn="light">亮</button><button type="button" data-theme-btn="dark">暗</button></span>
</header>
<div class="main" id="main">
  <section class="stage" id="stage" aria-label="叙事树"><noscript>这一页需要 JavaScript 才能展开叙事树；文字版请用 `narrative render --view axis`。</noscript></section>
  <div class="splitter" id="splitter" role="separator" aria-orientation="vertical" aria-label="调整左右比例" tabindex="0"></div>
  <aside class="inspector" id="inspector" aria-label="内容栏"></aside>
</div>
<footer class="statusbar"><span class="msg" id="statusmsg"></span><span id="srcline"></span></footer>
"""


def _escape_json_for_script(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/")


def render_html(payload: dict) -> str:
    """The whole page: one complete document, no external reference of any kind."""
    title = (payload.get("title") or payload.get("project") or "narrative") + " · 叙事轴"
    safe_title = title.replace("<", "").replace(">", "").replace("&", "")
    parts = [
        "<!doctype html>",
        '<html lang="zh"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>" + safe_title + "</title>",
        '<style id="ar-css">' + PAGE_CSS + "</style>",
        "</head>",
        "<body>",
        '<template id="ar-shell">' + SHELL_BODY + "</template>",
        SHELL_BODY.rstrip("\n"),
        '<script type="application/json" id="ar-snapshot">' + _escape_json_for_script(payload)
        + "</script>",
        '<script id="ar-app">' + PAGE_JS + "</script>",
        "</body></html>",
        "",
    ]
    return "\n".join(parts)


def render_axis_html(control: Path, ref: str = "main", out_path=None,
                     depth: int = AXIS_DEPTH, project: str = ""):
    """Public API -- `narrative.py render --view axis --html` forwards here.

    With `out_path`: writes the page and returns a receipt dict.
    Without it: returns the page as a string (the caller prints it)."""
    control = Path(control)
    payload = build_payload(control, ref=ref, project=project, depth=depth)
    html = render_html(payload)
    if not out_path:
        return html
    out = Path(out_path)
    ros.atomic_write(out, html)
    return {"ok": True, "view": "axis", "ref": ref,
            "commit": (payload.get("commit") or {}).get("id"),
            "tree_hash": payload.get("tree_hash"),
            "nodes": len(payload["snapshot"].get("nodes") or {}),
            "bytes": len(html.encode("utf-8")), "out": str(out)}


# ============================================================ self-test

def _fixture_payload() -> dict:
    """A five-level micro tree exercising every badge the page can draw."""
    def claim(nid, title, summary, statement, children=(), **kw):
        return {
            "id": nid, "node_type": kw.get("node_type", "claim"),
            "identity_key": kw.get("identity_key", {
                "subject": nid, "target": "returns", "predicate": "predicate-" + nid,
                "quantifier": "all", "domain": "US equities", "conditions": [],
                "polarity": "positive"}),
            "title": title, "summary": summary, "statement": statement,
            "state": {"epistemic": kw.get("epistemic", "PENDING"),
                      "narrative": kw.get("narrative", "ACTIVE")},
            "state_meta": {"resolution_condition": kw.get("resolution_condition"),
                           "live_bet": kw.get("live_bet", False)},
            "basis_refs": kw.get("basis_refs", []), "disposition_meta": {},
            "lineage": kw.get("lineage", []), "children": list(children),
        }

    nodes = {
        "N-0": {"id": "N-0", "node_type": "narrative",
                "identity_key": {"role_hint": "north_star", "thesis": "给附录阅读建账"},
                "title": "北极星", "summary": "给附录阅读建账的仪器",
                "statement": "本文测量审稿人是否阅读附录。",
                "state": {"epistemic": "PENDING", "narrative": "ACTIVE"},
                "state_meta": {"resolution_condition": None, "live_bet": False},
                "basis_refs": [], "disposition_meta": {}, "lineage": [],
                "children": ["C-001", "C-010", "E-07"]},
        "C-001": claim("C-001", "动机", "现有研究假定附录无人读", "现有做法假定附录无人读。", ["C-002"]),
        "C-002": claim("C-002", "机制", "阅读量取决于审稿流程", "阅读量应当取决于审稿流程。",
                       ["C-003"], epistemic="HELD"),
        "C-003": claim("C-003", "刻画", "阅读深度刻画", "有效阅读深度刻画引用率。", ["C-004"]),
        "C-004": claim("C-004", "边界", "第四层", "第四层内容。", ["C-005"]),
        "C-005": claim("C-005", "附注", "第五层（默认折叠）", "第五层内容。"),
        "C-010": claim("C-010", "旧头条", "已降级的前旗舰", "旧头条仍成立但被降到附录。",
                       epistemic="HELD", narrative="DEMOTED"),
        "E-07": claim("E-07", "实证核", "引用率表", "自建评审样本得到引用率表。",
                      node_type="evidence", live_bet=True,
                      resolution_condition="引用率表扩样跑完",
                      identity_key={"estimand": "appendix_citation_rate", "population": "ML venues",
                                    "sample_window": "2019-2024", "method": "count",
                                    "artifact_ref": "tables/citation_rate.csv"},
                      basis_refs=["evidence:tables/citation_rate.csv"]),
    }
    snapshot = {"schema": nar.TREE_SCHEMA, "root": "N-0", "nodes": nodes,
                "role_assignments": {"headline": "C-002", "flagship": None,
                                     "backbone": "C-003", "empirical_core": "E-07",
                                     "foil": None},
                "meta": {"venue": "RFS"}}
    return {
        "schema": VIEW_SCHEMA, "project": "fixture", "ref": "main",
        "title": "北极星", "north_star": "本文测量审稿人是否阅读附录。",
        "axis_depth": AXIS_DEPTH, "generated_at": "2026-08-25T00:00:00Z",
        "commit": {"id": "a" * 64, "at": "2026-08-25T00:00:00Z", "kind": "refine",
                   "cause": "none", "message": "fixture"},
        "tree_hash": nar.tree_hash(snapshot), "snapshot": snapshot,
        "hashes": _subtree_hashes(snapshot),
        "card": {"north_star": "给附录阅读建账", "roles": {}, "exclusions": [],
                 "live_bets": [{"node": "E-07", "title": "引用率表",
                                "resolution_condition": "引用率表扩样跑完"}],
                 "lever_debt": [{"node": "C-010", "title": "旧头条",
                                 "roles_held": ["flagship"], "cause": "unforced"}],
                 "lost_levers": [], "unresolved_branches": []},
        "annotations": {"C-002": [{"annotation_id": "a01", "kind": "qa",
                                   "question": "流程差异带来多少增量？",
                                   "answer": "阅读量随流程变化，表只是求值。",
                                   "by": "chronicler", "at": "2026-08-25T00:00:00Z",
                                   "commit_id": "a" * 64, "supersedes": None}]},
        "blame": {"C-002": [{"commit": "a" * 12, "at": "2026-08-25T00:00:00Z",
                             "kind": "restructure", "cause": "unforced",
                             "trigger_class": "review-pressure", "message": "fixture"}]},
        "pending": {"questions": [], "notes": []},
    }


def _live_project_check() -> dict:
    """Build a real three-level tree with narrative.py, then render it."""
    tmp = Path(tempfile.mkdtemp(prefix="ar-axis-html-"))
    try:
        control = tmp / "proj" / ".research-os"
        control.mkdir(parents=True, exist_ok=True)
        buffer, saved = io.StringIO(), sys.stdout
        sys.stdout = buffer
        try:
            nar.cmd_init(argparse.Namespace(
                root=control.parent, root_id="N-0", title="临时项目", thesis="临时论点",
                summary="", venue="RFS", message=None, session="W-u", at="2026-08-25T00:00:00Z",
                turn_uuid=None, manifest_id=None))
        finally:
            sys.stdout = saved

        def claim(nid, title, statement):
            return {"id": nid, "node_type": "claim",
                    "identity_key": {"subject": nid, "target": "returns",
                                     "predicate": "p-" + nid, "quantifier": "all",
                                     "domain": "US equities", "conditions": [],
                                     "polarity": "positive"},
                    "title": title, "summary": title + " 的总结",
                    "statement": statement,
                    "state": {"epistemic": "PENDING", "narrative": "ACTIVE"}}

        ops = [
            {"op": "add_node", "node": claim("C-001", "动机", "第一层。"), "parent": "N-0"},
            {"op": "add_node", "node": claim("C-002", "机制", "第二层。"), "parent": "C-001"},
            {"op": "add_node", "node": claim("C-003", "证据", "第三层。"), "parent": "C-002"},
            {"op": "add_node", "node": claim("C-010", "含义", "另一根轴。"), "parent": "N-0"},
        ]
        nar.commit_from_ops(control, "main", ops, {
            "message": "three-level tree", "at": "2026-08-25T01:00:00Z",
            "author": {"session": "W-u", "role": "main"}, "origin": "live",
            "approval_refs": {"overthrow": None, "forced_cause": None}})
        out = tmp / "axis.html"
        result = render_axis_html(control, "main", out)
        text = out.read_text(encoding="utf-8")
        streamed = render_axis_html(control, "main")
        ok = (result["nodes"] == 5 and '"C-003"' in text and result["bytes"] > 4000
              and isinstance(streamed, str) and streamed.startswith("<!doctype html>")
              and '"C-003"' in streamed and abs(len(streamed) - len(text)) < 40)
        return {"ok": ok, "detail": "nodes=%s bytes=%s" % (result["nodes"], result["bytes"])}
    except Exception as exc:  # pragma: no cover - environment failure
        return {"ok": False, "detail": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def self_test() -> int:
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    payload = _fixture_payload()
    html = render_html(payload)
    size = len(html.encode("utf-8"))

    check("renders a complete document", html.startswith("<!doctype html>")
          and html.rstrip().endswith("</html>"))
    check("embeds the snapshot JSON", 'type="application/json" id="ar-snapshot"' in html
          and '"C-005"' in html)

    start = html.index('id="ar-snapshot">') + len('id="ar-snapshot">')
    end = html.index("</script>", start)
    embedded = json.loads(html[start:end].replace("<\\/", "</"))
    check("embedded JSON round-trips", embedded["snapshot"]["root"] == "N-0"
          and embedded["tree_hash"] == payload["tree_hash"]
          and len(embedded["snapshot"]["nodes"]) == 8)
    check("carries commit head + root hash",
          embedded["commit"]["id"] == payload["commit"]["id"]
          and embedded["tree_hash"] == payload["tree_hash"])
    check("carries card narrative_part + annotations",
          embedded["card"]["lever_debt"] and embedded["annotations"]["C-002"])

    outbound = html.replace("https://doi.org/", "")     # the one URL the page builds: a DOI the reader clicks
    check("no external asset of any kind (only reader-clicked DOI links)",
          ("http" + "://") not in outbound and ("https" + "://") not in outbound
          and "//cdn" not in html and "@import" not in html and "<link" not in html
          and 'src="http' not in html)
    check("no external font", "fonts." not in html and "@font-face" not in html)

    # ---- 1. progressive disclosure: nothing pre-expanded, fold in place
    check("progressive: nothing expanded on load",
          "var DEFAULT_OPEN_DEPTH = 0;" in html
          and "level <= DEFAULT_OPEN_DEPTH" in html)
    check("expand one level at a time + collapse all",
          'id="expand-one"' in html and "function expandOneLevel()" in html
          and 'id="collapse-all"' in html and "function collapseAll()" in html)

    # ---- 2. no reset on click: in-place DOM updates, not innerHTML rebuilds
    check("fold is an in-place subtree update",
          "function expandLi(li, id, level)" in html and "function collapseLi(li)" in html
          and "function toggle(id, force)" in html
          and 'stage.querySelector(\'li.nd[data-id="\' + cssId(id) + \'"]\')' in html)
    check("selection swaps a class, never re-renders the stage",
          "function select(id, focus)" in html
          and 'x.classList.remove("is-sel")' in html and 'x.classList.add("is-sel")' in html
          and "renderInspector();\n  }" in html)
    check("stage re-render preserves scroll",
          "var st = stage.scrollTop, sl = stage.scrollLeft;" in html
          and "stage.scrollTop = st;" in html)
    check("no innerHTML tree rebuild", 'getElementById("stage").innerHTML' not in html
          and "stage.innerHTML" not in html)
    check("open state / view / width / path / selection persisted per project",
          "localStorage" in html and "catch (e) { return fallback; }" in html
          and '":open"' in html and '":view"' in html and '":insp"' in html
          and '":path"' in html and '":sel"' in html and "reveal(last); select(last);" in html)

    # ---- 3. text only in the window: no ids, codes, hashes, role keys on rows
    check("rows carry title + optional Chinese tag + child count only",
          'class="rid"' not in html and "class=\"aid\"" not in html
          and 'el("span", "ttl"' in html and 'el("span", "cnt"' in html
          and 'el("span", "tag", ROLE_CN[roles[0]]' in html)
    check("state words are plain Chinese, codes never shown",
          'HELD: "已成立"' in html and 'KILLED: "已证否"' in html and 'PENDING: "待定"' in html
          and 'ACTIVE: "在用"' in html and 'DEMOTED: "已降级"' in html
          and "正 HELD" not in html and "LEVER_DEBT" not in html and "LOST_LEVER" not in html)
    check("commit kinds / causes translated",
          'refine: "修订"' in html and 'overthrow: "推翻"' in html and 'forced: "受迫"' in html)
    check("ids, hashes, basis, lineage live behind a folded 技术细节",
          'el("details", "tech")' in html and '"技术细节"' in html
          and '"依据"' in html and '"谱系"' in html and '"树哈希"' in html)
    check("top bar shows title + north star, not ref/commit/tree hash",
          'id="ptitle"' in html and 'id="pns"' in html and 'id="rolekeys"' not in html
          and '" · tree "' not in html)
    check("status line is plain: 主线 · 快照 · N 段",
          '"主线"' in html and '" · 快照 "' in html and '" 段"' in html)

    # ---- 4. brief lives in the tree at the expanded level
    check("expanded node shows its brief inline",
          'el("div", "brief")' in html and "function briefOf(id, n)" in html
          and "li.appendChild(b);" in html and ".brief {" in html)
    check("brief skips a summary that merely repeats the title",
          "sum !== title" in html and "firstSentence(n.statement" in html)
    check("a leaf opens to its full text",
          'el("div", "more", full)' in html and ".brief .more" in html)
    check("columns view shows a two-line excerpt per item",
          'el("div", "ex", brief)' in html and "-webkit-line-clamp: 2;" in html)

    # ---- 5. draggable split
    check("splitter drags the inspector width with pointer capture",
          'id="splitter"' in html and "function wireSplitter()" in html
          and "setPointerCapture" in html and "body.dragging" in html
          and "grid-template-columns: minmax(0, 1fr) 6px var(--insp);" in html
          and 'style.setProperty("--insp"' in html)
    check("split width clamped, persisted, keyboard + dblclick reset",
          "var INSP_MIN = 280, INSP_DEFAULT = 380;" in html and "main.clientWidth * 0.7" in html
          and '"dblclick"' in html and 'role="separator"' in html)

    # ---- 6. multiple structures
    check("three structures: tree (chain) / outline / columns",
          'data-view="tree"' in html and 'data-view="outline"' in html and 'data-view="cols"' in html
          and "function renderTreeView()" in html and "function renderOutline()" in html
          and "function renderCols()" in html and "function setView(view)" in html
          and 'if (S.view === "axis") S.view = "tree";' in html)
    check("tree: hairline guide outline, 22px indent, 32px rows",
          "--indent: 22px;" in html and "--row: 32px;" in html
          and ".tree .tree::before" in html and "border-left: 1px solid var(--line);" in html
          and "vw" not in html)
    check("columns: Miller columns with sticky heads and in-place descent",
          'el("div", "cols")' in html and 'el("div", "colhead"' in html
          and "function descendCols(id, depth)" in html and "position: sticky;" in html)
    check("tree: brace flow -- parent { children stacked right, no boxes, in-place fold",
          'el("div", "bracetree")' in html and "function bnEl(id, level)" in html
          and "function toggleBrace(id, force)" in html and "bn.replaceWith(bnEl(id, level));" in html
          and ".brace .h.hu" in html and ".brace .nib::before" in html and ".pill" not in PAGE_CSS)
    check("literature layer lives only in the 树形 structure",
          'var lits = S.view === "tree" ? litOf(id) : [];' in html
          and 'if (S.ref && S.view === "tree") inspector.replaceChildren(refEl(S.ref));' in html
          and 'lp.setAttribute("data-lit", id)' in html)
    check("a study opens to report / boundary / use, verification, links, citing paragraphs, ask",
          'section("贡献")' in html and 'section("边界（相对本文）")' in html and 'section("本文怎么用它")' in html
          and '"书目已核实"' in html and 'section("原文与链接")' in html and "function citingNodes(key)" in html
          and 'setAttribute("data-askref"' in html)
    check("literature payload read from DATA.literature; absent means no layer",
          "var L = DATA.literature;" in html and "if (!L || !L.refs) return [];" in html)
    check("no prefilled text anywhere: no placeholders, no seeded edit fields",
          "ta.placeholder" not in html and "td.placeholder" not in html
          and "sp.value =" not in html and "st.value =" not in html
          and 'placeholder="搜索"' in html)
    check("live Q&A: ping, ask, polled thread that survives a reload",
          "function detectLive()" in html and 'fetch("/api/ping"' in html
          and 'fetch("/api/ask"' in html and "function askLive(id, question, box, btn)" in html
          and 'fetch("/api/qa/"' in html and "function syncQa(id)" in html
          and "function fillThread(thread, id)" in html and 'id = "qa-thread"' in html)
    check("asking is visible at once: local pending row, button state, Enter sends",
          'id: "local"' in html and '"回答中…"' in html and "ar-pulse" in html
          and 'e.key === "Enter" && !e.shiftKey && !e.isComposing' in html)
    check("static page never downloads a question on its own",
          'exportJson("narrative-question-' not in html and "已记为待回答" in html)

    # ---- history in the reader's words, derived from ops, not from the message
    titles = {"C-001": "动机", "C-002": "机制"}
    ops = [{"op": "patch_fields", "id": "C-002", "fields": {"title": "x", "statement": "y",
                                                             "state": {"epistemic": "HELD"}}},
           {"op": "move_node", "id": "C-002", "new_parent": "C-001", "index": 0},
           {"op": "add_node", "node": {"id": "C-009"}, "parent": "C-002"},
           {"op": "merge_nodes", "ids": ["C-002", "C-001"], "into": "C-001"}]
    before = {"nodes": {"C-001": {"title": "动机", "children": ["C-002"]},
                        "C-002": {"title": "旧标题", "statement": "第一句。第二句。第三句。",
                                  "state": {"epistemic": "PENDING", "narrative": "ACTIVE"}}}}
    after = {"nodes": {"C-001": {"title": "动机", "children": ["C-002"]},
                       "C-002": {"title": "x", "statement": "y", "state": {"epistemic": "HELD"}},
                       "C-009": {"title": "新下级"}}}
    ops[0]["fields"]["statement"] = "第一句。改过的第二句。第三句。第四句。"
    ops[2]["node"]["title"] = "新下级"
    chg = describe_changes("C-002", ops, before, after)
    kinds = [(c["kind"], c.get("field")) for c in chg]
    check("history is before -> after: title, statement diff, state, position, child, merge",
          kinds[:2] == [("field", "标题"), ("diff", "正文")] and ("field", "状态") in kinds
          and ("field", "位置") in kinds and chg[0]["before"] == "旧标题" and chg[0]["after"] == "x"
          and chg[1]["ins"] == 2 and chg[1]["del"] == 1
          and any(c["kind"] == "text" and c["text"] == "新增下级 「新下级」" for c in chg)
          and any(c["kind"] == "text" and c["text"] == "并入了 「动机」" for c in chg), str(kinds))
    punct = describe_changes("C-002", [{"op": "patch_fields", "id": "C-002", "fields": {"title": "否定结论:统计量"}}],
                             {"nodes": {"C-002": {"title": "否定结论：统计量"}}}, {"nodes": {"C-002": {"title": "否定结论:统计量"}}})
    check("punctuation-only edits are not reported as changes", punct == [], str(punct))
    tweak = describe_changes("C-002", [{"op": "patch_fields", "id": "C-002",
                                         "fields": {"summary": "若审稿人已知会议规则,附录篇幅与引用次数确实携带信息:附录越长,引用越向正文集中。"}}],
                             {"nodes": {"C-002": {"summary": "若审稿人已知会议规则，则附录的篇幅与引用次数确实携带信息：附录越长，引用越向正文集中。"}}},
                             {"nodes": {"C-002": {}}})
    check("a near-identical rewording is reported as 措辞微调, not as a wall of text",
          tweak == [{"kind": "text", "text": "简介措辞微调"}], str(tweak))
    dp = sentence_diff("**要点。** 甲，乙。丙；丁。", "要点. 甲,乙. 丙;丁.")
    check("a punctuation-only pass yields no sentence changes", dp["ins"] == 0 and dp["del"] == 0, str(dp))
    d = sentence_diff("甲。乙。丙。", "甲。乙改。丙。丁。")
    check("sentence diff marks removed / added sentences, keeps context",
          [s["t"] for s in d["segments"]] == ["=", "-", "+", "=", "+"] and d["ins"] == 2 and d["del"] == 1)
    words = describe_ops_for("C-002", ops, titles)
    check("phrase wrapper still speaks", words and words[0].startswith("标题：") and "并入了 「动机」" in words, str(words))
    check("machine messages never reach the reader",
          humanize_message("backfill frame-06 <- _叙事树_正式版_v2.0.md") == "回填自历史文件 _叙事树_正式版_v2.0.md"
          and humanize_message("chronicler prose pass: 103 nodes") is None
          and humanize_message("把头条从附录挪回正文") == "把头条从附录挪回正文")
    check("page renders before -> after rows, a sentence diff toggle, a reason line and an ask button",
          'el("span", "old", c.before)' in html and 'el("span", "new", c.after' in html
          and 'el("button", "difftoggle"' in html and '"原因：未记录"' in html
          and 'el("button", "asklink", "问这次改动")' in html and 'rows.push(["提交", b.commit' in html
          and "function similarity(a, b)" in html)
    check("no bare '改写了标题' phrases remain", "改写了" not in PAGE_JS)
    check("optional section numbering", 'id="numbering"' in html
          and "function numberOf(id)" in html and 'el("span", "num"' in html)
    check("search with highlight + reveal in the current structure",
          'id="search"' in html and "function searchHits(q)" in html and "<mark>" in html
          and "function reveal(id)" in html and 'scrollIntoView({ block: "center"' in html)

    # ---- colour discipline (unchanged principle)
    check("semantic colour lives only in the 7px dot",
          ".dot.ep-HELD" in html and ".dot.ep-KILLED" in html and ".dot.ep-PENDING" in html
          and "width: 7px;" in html and "badge" not in html)
    check("narrative axis is dot style, not a fourth colour",
          ".dot.na-DEMOTED { opacity: .45; }" in html
          and ".dot.na-DROPPED, .dot.na-MERGED { background: transparent;" in html)
    check("exactly three semantic tokens (--ok / --no / --wait)",
          html.count("--ok:") == 3 and html.count("--no:") == 3 and html.count("--wait:") == 3)
    check("fold animation is chevron rotate + opacity, never height",
          "transform: rotate(90deg);" in html and "transition: transform 150ms ease;" in html
          and "@keyframes ar-in { from { opacity: 0; } to { opacity: 1; } }" in html
          and "height 150ms" not in html)
    check("system type stack + mono for the folded details only",
          'system-ui, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif' in html
          and "ui-monospace, Consolas, monospace" in html)

    # ---- inspector
    check("inspector: crumbs, 简介 / 正文 / 问答 / 历史 / 下级",
          'el("nav", "crumbs")' in html and 'section("简介")' in html and 'section("正文")' in html
          and 'section("问答")' in html and 'section("这一段的历史")' in html and 'section("下级")' in html)
    check("overview lands on roles / bets / lowered levers in words",
          'section("承担角色的段落")' in html and 'section("还在飞的赌注（"' in html
          and "function capped(ids)" in html and "var LIST_CAP = 8;" in html
          and 'section("曾经承重、后来被放下的段落")' in html)
    check("role five keys", all(r in html for r in nar.ROLES))
    check("inspector links reveal + select", 'data-go' in html and "reveal(id);\n      select(id);" in html)

    check("edit mode with the six operations",
          all(tok in html for tok in ('"fields"', '"up"', '"move"', '"merge"', '"split"', '"todo"'))
          and "function editAction(kind)" in html and 'id="editmode"' in html)
    check("patch export logic (NarrativePatch v1)",
          nar.PATCH_SCHEMA in html and "tree_ops" in html and "todo_proposals" in html
          and "base_tree_hash" in html and "base_commit_id" in html)
    check("three-tier export: downloads -> <a download> -> clipboard",
          'capability("downloads")' in html and "a.download = filename" in html
          and "navigator.clipboard" in html)
    check("question pack + artifact republish",
          QUESTION_SCHEMA in html and "subtree_hash" in html and 'capability("artifact")' in html
          and "art.publish(rebuildHtml())" in html and "outerHTML" not in html)

    check("theme tri-state tokens", 'prefers-color-scheme: dark' in html
          and ':root:not([data-theme="light"])' in html and ':root[data-theme="dark"]' in html
          and "background: var(--bg)" in html)
    check("reduced motion honoured", "prefers-reduced-motion" in html)
    check("keyboard: focusable rows, arrow fold, up/down walk",
          'setAttribute("tabindex", "0")' in html and 'e.key === "ArrowRight"' in html
          and 'e.key === "ArrowDown"' in html and "function focusables()" in html)
    check("narrow screens stack the panes", "@media (max-width: 820px)" in html)
    check("title carries the project + 叙事轴", "<title>北极星 · 叙事轴</title>" in html)
    check("size under 400 KB", size < SIZE_BUDGET, "%d bytes" % size)

    live = _live_project_check()
    check("live 3-level project renders", live["ok"], live["detail"])

    failed = [c for c in checks if not c["ok"]]
    print(json.dumps({"ok": not failed, "checks": len(checks), "failed": len(failed),
                      "bytes": size,
                      "rows": checks if failed else [c["check"] for c in checks]},
                     ensure_ascii=False, indent=2))
    return 1 if failed else 0


# ============================================================ CLI

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="narrative_render_html.py",
        description="single-file HTML narrative axis view (contract §2/§8/§10/§13)")
    parser.add_argument("--project", help="project root, .research-os dir, or state.json")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--out", help="write the page here (default: stdout)")
    parser.add_argument("--depth", type=int, default=AXIS_DEPTH,
                        help="levels expanded by default (deeper folds into +N)")
    parser.add_argument("--self-test", dest="self_test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.project:
        ros.die("--project is required (or --self-test)", 2)
    control = ros.locate_control(Path(args.project))
    try:
        result = render_axis_html(control, args.ref, args.out, depth=args.depth)
    except nar.NarrativeError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "detail": exc.detail},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    if isinstance(result, dict):
        print(json.dumps(result, ensure_ascii=False))
        return 0
    sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
