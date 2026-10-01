#!/usr/bin/env python3
"""auto-research research map -- can the researcher see what the AI did?

One page, one map: the story tree as a mind map (fold / zoom / search), with
three overlays read from the ledgers the project already keeps -- never from a
model's re-summary of itself:

  changes    what was added, rewritten, moved or dropped since the researcher
             last looked, each change with its commit, author and cause
             (forced by evidence and signed / chosen / unrecorded);
  evidence   what each node stands on and what contests it (research graph),
             which held claims have no evidence, which load-bearing claims no
             figure expresses, lever debt, live bets, human-signed decisions;
  taste      where a changed node trips one of the researcher's own rules
             (taste_ledger.py).

The page also says how far the recorded story lags the project: the last story
commit, and the story-bearing files (manuscript, decisions, narrative notes)
modified after it.

Interop: ``export`` writes markmap Markdown, JSON Canvas (Obsidian) and XMind;
``import`` reads an edited markmap/XMind file back by stable node id and turns
it into a NarrativePatch -- a proposal that lands only through
``narrative apply`` -> ``commit --pending-patch``.  A topic deleted in the map
is never detached automatically; a new topic is never added automatically.

Read-only against the project except two append-only side files it owns:
``.research-os/map/views.jsonl`` (when the researcher last looked) and the page
it renders.  stdlib-only.  Exit codes: 0 ok, 1 refusal, 2 environment/usage.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import io
import json
import os
import re
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros  # noqa: E402

MODEL_SCHEMA = "auto-research/research-map-v1"
VIEW_SCHEMA = "auto-research/map-view-v1"
ID_MARK_RE = re.compile(r"<!--\s*ar:([A-Z]{1,2}-[0-9]+(?:\.[0-9]+)*)\s*-->")
NOTE_MARK_RE = re.compile(r"\[\[ar:([A-Z]{1,2}-[0-9]+(?:\.[0-9]+)*)\]\]")
NODE_ID_RE = re.compile(r"^[A-Z]{1,2}-[0-9]+(\.[0-9]+)*$")
BADGE_GLYPHS = "✚✎⇄⚡?▢⚠◷✔✦"
BADGE_TAIL_RE = re.compile(r"\s+\[[%s\s]+\]\s*$" % re.escape(BADGE_GLYPHS))
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SKIP_DIRS = {".git", ".research-os", "node_modules", "__pycache__", ".venv", "venv",
             ".ipynb_checkpoints", ".mypy_cache", ".pytest_cache"}
SCAN_FILE_CAP, SCAN_SECONDS = 40000, 8.0
CLIP_STATEMENT, CLIP_SUMMARY = 1600, 400

LABELS = {
    "zh": {
        "lp": "（", "rp": "）", "colon": "：", "comma": "，", "enum": "、", "lq": "「", "rq": "」",
        "page_title": "研究地图", "since_last_view": "上次查看", "since_previous": "上一次记录",
        "since_arg": "指定起点", "since_genesis": "最初", "last_update": "故事记录最后更新",
        "days_ago": "天前", "stale": "此后项目里有 {n} 个相关文件改过，地图可能落后于实际进展",
        "since_line": "自{src}（{date}）以来", "added": "新增", "changed": "改写", "moved": "移动",
        "dropped": "移出", "no_change": "没有新的改动",
        "expand_all": "全部展开", "two_levels": "两层", "only_changes": "只看改动",
        "only_problems": "只看问题", "fit": "适应窗口", "search": "搜索",
        "ov_changes": "改动", "ov_evidence": "证据", "ov_taste": "口味",
        "b_new": "新增", "b_changed": "改写", "b_moved": "移动了位置", "b_conflict": "有反驳或矛盾",
        "b_no_evidence": "已成立但没有证据支撑", "b_no_figure": "承重但没有图表达",
        "b_lever": "被雪藏：没被推翻，却被挪下主线", "b_live_bet": "在飞的赌注", "b_signed": "你签过字的决定",
        "b_taste": "踩你雷区：碰了你的口味规则",
        "legend": "标记说明", "overview": "总览", "state": "状态", "roles": "角色",
        "summary": "简介", "statement": "正文", "changes": "这次的改动", "before": "改前",
        "after": "改后", "commits": "相关提交", "evidence": "证据", "supports": "支持",
        "refutes": "反驳", "figures": "图", "taste": "口味", "rule": "规则",
        "found_in": "出现在", "left_story": "离开故事的节点", "reason": "原因",
        "reason_unknown": "原因未记录", "reminders": "需要你判断的口味规则", "notes": "说明",
        "stale_files": "故事记录之后改过的文件", "none": "无", "select_hint": "点节点看详情；点圆点折叠或展开",
        "ep_HELD": "已成立", "ep_PENDING": "待定", "ep_KILLED": "已否定",
        "nar_ACTIVE": "在故事中", "nar_DEMOTED": "已降级", "nar_MERGED": "已并入", "nar_DROPPED": "已移出",
        "cause_forced": "证据所迫（已签字）", "cause_candidate": "可能是证据所迫（待你确认）",
        "cause_unforced": "主动调整（不是证据所迫）", "cause_unknown": "原因未记录",
        "cause_none": "非结构性修订（不涉及承重部分）",
        "by_chronicler": "AI（记录员）", "by_main": "AI（主控）", "by_user": "你", "by_backfill": "历史回填",
        "f_title": "标题", "f_summary": "简介", "f_statement": "正文", "f_state": "状态",
        "f_state_meta": "状态说明", "f_basis_refs": "依据", "f_disposition_meta": "处置说明",
        "f_lineage": "谱系", "f_identity_key": "身份",
        "note_graph_stale": "关系层落后于事件流，证据叠层已跳过（需要先重建关系层）",
        "note_graph_missing": "这个项目还没有关系层，证据叠层为空",
        "note_no_rules": "还没有记下任何口味规则",
        "lever_note": "从没被推翻，也没被哪个新结果取代，却被悄悄挪下了主线",
        "live_bet_note": "待定的承重判断，结论条件：", "signed_note": "你签字的依据：",
        "held_unsupported_note": "标为已成立，但没有任何证据边支撑它",
        "lg_dashed": "虚线下划线：待定（还没有定论）", "lg_grey": "灰色斜体：已降级或已并入",
        "no_figure_note": "承重节点，但没有任何图表达它",
    },
    "en": {
        "lp": " (", "rp": ")", "colon": ": ", "comma": ", ", "enum": ", ", "lq": "“", "rq": "”",
        "page_title": "Research map", "since_last_view": "your last view", "since_previous": "the previous record",
        "since_arg": "the chosen start", "since_genesis": "the beginning", "last_update": "Story last recorded",
        "days_ago": "days ago", "stale": "{n} story-bearing file(s) changed since then; the map may lag the work",
        "since_line": "Since {src} ({date})", "added": "added", "changed": "rewritten", "moved": "moved",
        "dropped": "dropped", "no_change": "no new changes",
        "expand_all": "Expand all", "two_levels": "Two levels", "only_changes": "Changes only",
        "only_problems": "Problems only", "fit": "Fit", "search": "Search",
        "ov_changes": "Changes", "ov_evidence": "Evidence", "ov_taste": "Taste",
        "b_new": "new", "b_changed": "rewritten", "b_moved": "moved", "b_conflict": "refuted or contested",
        "b_no_evidence": "held without supporting evidence", "b_no_figure": "load-bearing, no figure",
        "b_lever": "benched: sidelined without being refuted", "b_live_bet": "live bet(s)",
        "b_signed": "signed by you", "b_taste": "trips your taste rules",
        "legend": "Legend", "overview": "Overview", "state": "State", "roles": "Roles",
        "summary": "Summary", "statement": "Statement", "changes": "What changed", "before": "before",
        "after": "after", "commits": "Commits", "evidence": "Evidence", "supports": "supports",
        "refutes": "refutes", "figures": "figures", "taste": "Taste", "rule": "rule",
        "found_in": "found in", "left_story": "Left the story", "reason": "reason",
        "reason_unknown": "no reason recorded", "reminders": "Taste rules for you to judge", "notes": "Notes",
        "stale_files": "Files changed after the last story record", "none": "none",
        "select_hint": "Click a node for details; click a dot to fold",
        "ep_HELD": "held", "ep_PENDING": "pending", "ep_KILLED": "refuted",
        "nar_ACTIVE": "in the story", "nar_DEMOTED": "demoted", "nar_MERGED": "merged", "nar_DROPPED": "dropped",
        "cause_forced": "forced by evidence (signed)", "cause_candidate": "possibly forced (awaiting you)",
        "cause_unforced": "chosen, not forced", "cause_unknown": "cause not recorded",
        "cause_none": "non-structural edit (no load-bearing part touched)",
        "by_chronicler": "AI (chronicler)", "by_main": "AI (controller)", "by_user": "you", "by_backfill": "backfill",
        "f_title": "title", "f_summary": "summary", "f_statement": "statement", "f_state": "state",
        "f_state_meta": "state note", "f_basis_refs": "basis", "f_disposition_meta": "disposition",
        "f_lineage": "lineage", "f_identity_key": "identity",
        "note_graph_stale": "The relation layer is behind its event stream; evidence overlay skipped (rebuild it first)",
        "note_graph_missing": "This project has no relation layer yet; evidence overlay is empty",
        "note_no_rules": "No taste rules recorded yet",
        "lever_note": "Never refuted, never superseded. Sidelined anyway.",
        "live_bet_note": "Pending load-bearing bet; resolves when: ", "signed_note": "Signed basis: ",
        "held_unsupported_note": "Marked held, but no evidence edge supports it",
        "lg_dashed": "Dashed underline: pending (not settled)", "lg_grey": "Grey italic: demoted or merged",
        "no_figure_note": "Load-bearing, but no figure expresses it",
    },
}


class MapError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _narrative():
    import narrative  # noqa: WPS433 -- same-dir sibling
    return narrative


def _graph():
    try:
        import research_graph  # noqa: WPS433
        return research_graph
    except Exception:                                    # pragma: no cover
        return None


def _taste():
    try:
        import taste_ledger  # noqa: WPS433
        return taste_ledger
    except Exception:                                    # pragma: no cover
        return None


def clip(text, limit: int = 160) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def map_dir(control: Path) -> Path:
    return control / "map"


def views_path(control: Path) -> Path:
    return map_dir(control) / "views.jsonl"


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


# ============================================================ since

def read_views(control: Path) -> list:
    path = views_path(control)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def record_view(control: Path, model: dict, out) -> dict:
    row = {"schema": VIEW_SCHEMA, "at": ros.utc_now(), "ref": model["ref"],
           "head": model["head"], "since": model["since"]["commit"],
           "out": str(out) if out else None}
    with ros.hold_lock(control):
        path = views_path(control)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def resolve_since(control: Path, ref: str, chain: list, since) -> dict:
    narr = _narrative()
    ids = [c["id"] for c in chain]
    if since:
        if DATE_RE.match(since):
            before = [c for c in chain if c["at"] < since + "T00:00:00Z"]
            commit = before[-1]["id"] if before else None
            return {"commit": commit, "source": "arg" if commit else "genesis"}
        return {"commit": narr.resolve_commitish(control, since), "source": "arg"}
    for row in reversed(read_views(control)):
        if row.get("ref") == ref and row.get("head") in ids:
            return {"commit": row["head"], "source": "last_view"}
    if len(chain) >= 2:
        return {"commit": chain[-2]["id"], "source": "previous"}
    return {"commit": None, "source": "genesis"}


# ============================================================ overlays

def _cause_key(commit: dict) -> str:
    if commit.get("cause") == "forced" and commit.get("forced_status") == "confirmed":
        return "cause_forced"
    if commit.get("forced_status") == "candidate":
        return "cause_candidate"
    return "cause_" + (commit.get("cause") or "unknown")


def _commit_brief(commit: dict) -> dict:
    author = (commit.get("author") or {}).get("role") or "main"
    return {"id": commit["id"][:12], "at": commit.get("at", "")[:10], "kind": commit.get("kind"),
            "cause": _cause_key(commit), "by": "by_" + author if author in
            ("chronicler", "main", "user", "backfill") else "by_main",
            "trigger": commit.get("trigger_class"), "message": clip(commit.get("message"), 200),
            "counterfactual": clip(commit.get("counterfactual"), 300) or None}


def change_overlay(control: Path, chain: list, base: dict, head: dict, since_commit) -> tuple:
    narr = _narrative()
    ids = [c["id"] for c in chain]
    start = ids.index(since_commit) + 1 if since_commit in ids else 0
    between = chain[start:]
    per_node, detached = {}, {}
    for commit in between:
        brief = _commit_brief(commit)
        for nid in narr._commit_touched_ids(commit):
            per_node.setdefault(nid, []).append(brief)
        for nid, record in narr._detach_records(commit).items():
            detached[nid] = dict(record, commit=brief)
    changes = {}
    for op in narr.snapshot_diff(base, head):
        nid = op.get("id")
        if op["op"] == "add_node":
            changes.setdefault(nid, {"kinds": [], "fields": []})["kinds"].append("added")
        elif op["op"] == "patch_fields" and nid in head["nodes"]:
            entry = changes.setdefault(nid, {"kinds": [], "fields": []})
            entry["kinds"].append("changed")
            entry["fields"] = op.get("fields") or []
        elif op["op"] == "move_node" and nid in head["nodes"] and nid in base["nodes"]:
            old_parent = narr.parent_of(base, nid)
            if old_parent != op.get("new_parent"):
                changes.setdefault(nid, {"kinds": [], "fields": []})["kinds"].append("moved")
    for nid, entry in changes.items():
        entry["commits"] = per_node.get(nid, [])[-6:]
        if "changed" in entry["kinds"]:
            old, new = base["nodes"][nid], head["nodes"][nid]
            entry["before"] = {f: clip(old.get(f), CLIP_STATEMENT) for f in ("title", "summary", "statement")
                               if f in entry["fields"]}
            entry["after"] = {f: clip(new.get(f), CLIP_STATEMENT) for f in ("title", "summary", "statement")
                              if f in entry["fields"]}
            if "state" in entry["fields"]:
                entry["before"]["state"] = old.get("state")
                entry["after"]["state"] = new.get("state")
        if "moved" in entry["kinds"]:
            old_parent = narr.parent_of(base, nid)
            entry["moved_from"] = clip((base["nodes"].get(old_parent) or {}).get("title"), 60)
    dropped = []
    for nid in sorted(set(base["nodes"]) - set(head["nodes"])):
        record = detached.get(nid) or {}
        dropped.append({"id": nid, "title": clip(base["nodes"][nid].get("title"), 80),
                        "disposition": record.get("disposition"),
                        "reason": clip(record.get("reason"), 240) or None,
                        "basis_refs": list(record.get("basis_refs") or [])[:4],
                        "commit": record.get("commit")})
    return changes, dropped


def evidence_overlay(control: Path, head: dict) -> dict:
    """READ-ONLY: reads graph/current.json; never builds or rebuilds it."""
    rg = _graph()
    out = {"per_node": {}, "held_unsupported": [], "no_figure": [], "note": None}
    if rg is None:
        out["note"] = "note_graph_missing"
        return out
    payload, stale = rg.current_state(control)
    if payload is None:
        out["note"] = "note_graph_stale" if stale else "note_graph_missing"
        return out
    if stale:
        out["note"] = "note_graph_stale"
        return out
    edges = list(payload.get("edges") or [])
    pid = rg.project_id(control)
    prefix = "ro:%s:narr:" % pid
    labels = {}

    def label(address):
        if address not in labels:
            try:
                labels[address] = rg._node_label(control, address)
            except Exception:                            # pragma: no cover
                labels[address] = address
        return labels[address]

    for edge in edges:
        to = edge.get("to") or ""
        if not to.startswith(prefix):
            continue
        nid = to[len(prefix):]
        if nid not in head["nodes"]:
            continue
        slot = out["per_node"].setdefault(nid, {"supports": [], "refutes": [], "figures": [],
                                                "conflict": False})
        item = clip(label(edge.get("from")), 90)
        if edge.get("kind") == "expressed_as":
            slot["figures"].append(item)
        elif edge.get("kind") == "supported_by":
            (slot["refutes"] if edge.get("polarity") == "-" else slot["supports"]).append(item)
        if edge.get("conflict"):
            slot["conflict"] = True
    try:
        report = rg.coverage_report(control, edges=edges)
        out["held_unsupported"] = [x["id"] for x in report.get("held_unsupported") or []]
        out["no_figure"] = [x["id"] for x in report.get("no_figure") or []]
    except Exception:                                    # pragma: no cover
        pass
    return out


def signed_overlay(control: Path, chain: list, head: dict) -> dict:
    narr = _narrative()
    signed = {}
    for commit in chain:
        approvals = {k: v for k, v in (commit.get("approval_refs") or {}).items() if v}
        if not approvals and commit.get("forced_status") != "confirmed":
            continue
        what = ", ".join(sorted(approvals.values())) or "forced cause confirmed"
        for nid in narr._commit_touched_ids(commit):
            if nid in head["nodes"]:
                signed.setdefault(nid, []).append("%s (%s)" % (what, commit["id"][:12]))
    for nid, node in head["nodes"].items():
        for ref in node.get("basis_refs") or []:
            if isinstance(ref, str) and ref.startswith("decision:") and narr.resolve_decision(control, ref):
                signed.setdefault(nid, []).append(ref)
    return signed


def staleness(control: Path, head_at: "str | None") -> dict:
    """Story-bearing files modified after the last story commit (mtime)."""
    narr = _narrative()
    root = control.parent
    patterns = list((narr.load_config(control) or {}).get("bearing_globs")
                    or narr.DEFAULT_BEARING_GLOBS)
    if not head_at:
        return {"count": 0, "files": [], "partial": False}
    cutoff = _parse_ts(head_at).timestamp()
    hits, visited, partial, started = [], 0, False, time.time()
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            visited += 1
            if visited > SCAN_FILE_CAP or time.time() - started > SCAN_SECONDS:
                partial = True
                break
            rel = os.path.relpath(os.path.join(current, name), root).replace(os.sep, "/")
            bearing = name.lower().endswith(".tex") or any(
                fnmatch.fnmatch(rel if ("/" in pat or "**" in pat) else name, pat) for pat in patterns)
            if not bearing:
                continue
            try:
                mtime = os.path.getmtime(os.path.join(current, name))
            except OSError:
                continue
            if mtime > cutoff:
                hits.append((mtime, rel))
        if partial:
            break
    hits.sort(reverse=True)
    return {"count": len(hits), "partial": partial,
            "files": [{"path": rel, "modified": datetime.fromtimestamp(m, timezone.utc)
                       .strftime("%Y-%m-%d")} for m, rel in hits[:8]]}


# ============================================================ model

CJK_RE = re.compile(r"[一-鿿]")


def auto_lang(head: dict) -> str:
    """Chinese labels for a story told in Chinese, English otherwise."""
    titles = [str((n or {}).get("title") or "") for n in (head.get("nodes") or {}).values()]
    root_title = str(((head.get("nodes") or {}).get(head.get("root")) or {}).get("title") or "")
    with_cjk = sum(1 for t in titles if CJK_RE.search(t))
    return "zh" if CJK_RE.search(root_title) or (titles and with_cjk / len(titles) >= 0.3) else "en"


def build_model(control: Path, ref: str = "main", since=None, lang: str = "auto",
                scan_files: bool = True) -> dict:
    narr = _narrative()
    narr.require_store(control)
    chain = narr.commit_chain(control, ref)
    if not chain:
        raise MapError("EMPTY_STORY", "the narrative store has no commit on %s" % ref)
    head = narr.load_head_snapshot(control, ref)
    if lang not in LABELS:
        lang = auto_lang(head)
    since_info = resolve_since(control, ref, chain, since)
    base = (narr.commit_snapshot(control, since_info["commit"]) if since_info["commit"]
            else narr.empty_snapshot())
    changes, dropped = change_overlay(control, chain, base, head, since_info["commit"])
    # every commit since the last view, oldest first, with its full time and what it touched
    ids = [c["id"] for c in chain]
    start = ids.index(since_info["commit"]) + 1 if since_info["commit"] in ids else 0
    log = [dict(_commit_brief(c), at_full=c.get("at", ""),
                nodes=sorted(narr._commit_touched_ids(c)),
                ops=[{"op": o.get("op"), "id": o.get("id")} for o in (c.get("ops") or [])][:12])
           for c in chain[start:]][-60:]
    evidence = evidence_overlay(control, head)
    signed = signed_overlay(control, chain, head)
    history = narr.derive_history(control, ref)
    lever = {x["node"]: x for x in narr.lever_debt(history)}
    bets = {x["node"]: x for x in narr.live_bets(head)}

    rules, taste_flags, reminders = {}, {}, []
    tl = _taste()
    if tl is not None:
        rules = tl.current_rules(control)
        touched = {nid for nid, c in changes.items() if {"added", "changed"} & set(c["kinds"])}
        result = tl.check_nodes(rules, head["nodes"], only_ids=touched)
        for flag in result["flags"]:
            source = (rules.get(flag["rule"]) or {}).get("source") or {}
            flag = dict(flag, quote=clip(source.get("quote"), 200) or None)
            taste_flags.setdefault(flag["node"], []).append(flag)
        reminders = result["reminders"]

    roles_of = {}
    for role, nid in (head.get("role_assignments") or {}).items():
        if nid:
            roles_of.setdefault(nid, []).append(role)
    parents, nodes, order = {}, [], []

    def walk(nid, depth, parent):
        order.append((nid, depth))
        parents[nid] = parent
        for child in head["nodes"][nid].get("children") or []:
            if child in head["nodes"]:
                walk(child, depth + 1, nid)

    walk(head["root"], 0, None)
    held_unsupported, no_figure = set(evidence["held_unsupported"]), set(evidence["no_figure"])
    for nid, depth in order:
        node = head["nodes"][nid]
        state = node.get("state") or {}
        ev = evidence["per_node"].get(nid) or {}
        change = changes.get(nid)
        badges = {
            "new": bool(change and "added" in change["kinds"]),
            "changed": bool(change and "changed" in change["kinds"]),
            "moved": bool(change and "moved" in change["kinds"]),
            "refute": len(ev.get("refutes") or []), "conflict": bool(ev.get("conflict")),
            "support": len(ev.get("supports") or []), "figure": len(ev.get("figures") or []),
            "no_evidence": nid in held_unsupported, "no_figure": nid in no_figure,
            "lever": nid in lever, "live_bet": nid in bets, "signed": nid in signed,
            "taste": len(taste_flags.get(nid) or []),
        }
        nodes.append({
            "id": nid, "title": clip(node.get("title") or nid, 200), "depth": depth,
            "parent": parents.get(nid), "children": [c for c in node.get("children") or []
                                                     if c in head["nodes"]],
            "type": node.get("node_type"), "epistemic": state.get("epistemic"),
            "narrative": state.get("narrative"), "roles": roles_of.get(nid, []),
            "summary": clip(node.get("summary"), CLIP_SUMMARY),
            "statement": clip(node.get("statement"), CLIP_STATEMENT),
            "badges": badges, "change": change, "evidence": ev or None,
            "taste": taste_flags.get(nid) or [], "signed": signed.get(nid) or [],
            "lever": lever.get(nid), "live_bet": bets.get(nid),
        })

    since_commit = since_info["commit"]
    since_at = next((c["at"] for c in chain if c["id"] == since_commit), None)
    head_at = chain[-1]["at"]
    counts = {"added": sum(1 for n in nodes if n["badges"]["new"]),
              "changed": sum(1 for n in nodes if n["badges"]["changed"]),
              "moved": sum(1 for n in nodes if n["badges"]["moved"]),
              "dropped": len(dropped),
              "conflict": sum(1 for n in nodes if n["badges"]["refute"] or n["badges"]["conflict"]),
              "no_evidence": len(held_unsupported), "no_figure": len(no_figure),
              "lever": len(lever), "live_bet": len(bets),
              "signed": sum(1 for n in nodes if n["badges"]["signed"]),
              "taste": sum(1 for n in nodes if n["badges"]["taste"])}
    notes = []
    if evidence["note"]:
        notes.append(evidence["note"])
    if not rules:
        notes.append("note_no_rules")
    state = ros.read_json_file(control / "state.json", {}) or {}
    # The page shows times on the clock of the machine that built it: the night the
    # researcher actually lived through, whoever opens the file later.
    try:
        tz_offset = int(_parse_ts(head_at).astimezone().utcoffset().total_seconds() // 60)
    except (ValueError, OverflowError, OSError, AttributeError):
        tz_offset = None
    return {
        "schema": MODEL_SCHEMA, "built_at": ros.utc_now(), "lang": lang if lang in LABELS else "zh",
        "project": state.get("title") or control.parent.name, "ref": ref,
        "head": chain[-1]["id"], "head_at": head_at,
        "head_age_days": round((datetime.now(timezone.utc) - _parse_ts(head_at)).total_seconds()
                               / 86400, 1),
        "since": {"commit": since_commit, "at": since_at, "source": since_info["source"]},
        "root": head["root"], "nodes": nodes, "dropped": dropped, "log": log, "counts": counts,
        "taste_rules": len(rules), "reminders": reminders, "notes": notes, "tz_offset_minutes": tz_offset,
        "staleness": staleness(control, head_at) if scan_files else {"count": 0, "files": [],
                                                                      "partial": False},
    }


def summary_lines(model: dict, limit: int = 40) -> list:
    """<= 40 lines of plain text for the controller."""
    L = LABELS[model["lang"]]
    c = model["counts"]
    src = {"last_view": L["since_last_view"], "previous": L["since_previous"],
           "arg": L["since_arg"], "genesis": L["since_genesis"]}[model["since"]["source"]]
    lines = ["%s — %s" % (L["page_title"], model["project"]),
             "%s %s%s%s %s%s" % (L["last_update"], model["head_at"][:10], L["lp"], model["head_age_days"],
                                L["days_ago"], L["rp"])]
    if model["staleness"]["count"]:
        lines.append(L["stale"].format(n=model["staleness"]["count"]))
    lines.append("%s: +%d ~%d ⇄%d −%d | ⚡%d ?%d ▢%d ⚠%d ◷%d ✔%d ✦%d" % (
        L["since_line"].format(src=src, date=(model["since"]["at"] or "")[:10]),
        c["added"], c["changed"], c["moved"], c["dropped"], c["conflict"], c["no_evidence"],
        c["no_figure"], c["lever"], c["live_bet"], c["signed"], c["taste"]))
    for node in model["nodes"]:
        b = node["badges"]
        marks = "".join(g for g, on in (("✚", b["new"]), ("✎", b["changed"]), ("⇄", b["moved"]),
                                        ("⚡", b["refute"] or b["conflict"]), ("✦", b["taste"]),
                                        ("⚠", b["lever"])) if on)
        if marks and len(lines) < limit - 6:
            lines.append("  %s %s %s" % (marks, node["id"], clip(node["title"], 50)))
    for item in model["dropped"][:5]:
        lines.append("  − %s %s — %s" % (item["id"], clip(item["title"], 40),
                                         clip(item["reason"] or L["reason_unknown"], 60)))
    for note in model["notes"]:
        lines.append("  · " + L[note])
    return lines[:limit]


# ============================================================ HTML

PAGE = r"""<!DOCTYPE html>
<html lang="__LANG__"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#fbfaf7;--fg:#1f2328;--muted:#6b6f76;--line:#d9d6cf;--panel:#ffffff;--sel:#fff3c4}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.5 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;height:100vh;display:flex;flex-direction:column}
header{padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel)}
header h1{font-size:16px;margin:0 0 2px}
.sub{color:var(--muted)}
.warn{color:#9a3412;margin-top:2px}
.chips{margin-top:6px;display:flex;flex-wrap:wrap;gap:6px}
.chip{border:1px solid var(--line);border-radius:10px;padding:0 8px;background:#fff;cursor:pointer}
.chip b{margin-right:3px}
.bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:6px 16px;border-bottom:1px solid var(--line)}
.bar button{border:1px solid var(--line);background:#fff;border-radius:6px;padding:2px 9px;cursor:pointer;font:inherit}
.bar label{color:var(--muted);margin-left:4px}
.bar input[type=search]{border:1px solid var(--line);border-radius:6px;padding:2px 8px;font:inherit;min-width:160px}
main{flex:1;display:flex;min-height:0}
#mapwrap{flex:1;position:relative;overflow:hidden;cursor:grab}
#mapwrap.drag{cursor:grabbing}
svg{width:100%;height:100%;display:block}
aside{width:380px;max-width:45vw;border-left:1px solid var(--line);background:var(--panel);overflow-y:auto;overflow-x:hidden;padding:12px 16px;overflow-wrap:anywhere}
aside h2{font-size:15px;margin:0 0 6px}
aside h3{font-size:12px;color:var(--muted);text-transform:none;margin:14px 0 4px;border-top:1px solid var(--line);padding-top:8px}
aside p{margin:4px 0;white-space:pre-wrap}
aside ul{margin:4px 0;padding-left:18px}
.diff{display:grid;grid-template-columns:max-content 1fr;gap:2px 8px}
.diff .k{color:var(--muted)}
.old{color:#8a3b12;text-decoration:line-through;text-decoration-color:#e3b39c}
.new{color:#14532d}
.muted{color:var(--muted)}
.lab{font-size:13px;cursor:pointer}
.lab.dem{fill:#8a8f98;font-style:italic}
.lab.hit{font-weight:700}
.sel rect.hl{fill:var(--sel)}
@media (max-width:760px){main{flex-direction:column}aside{width:auto;max-width:none;height:45vh;border-left:0;border-top:1px solid var(--line)}}
</style></head><body>
<header><h1 id="title"></h1><div class="sub" id="upd"></div><div class="warn" id="stale"></div>
<div class="sub" id="since"></div><div class="chips" id="chips"></div></header>
<div class="bar" id="bar"></div>
<main><div id="mapwrap"><svg id="svg"><g id="vp"></g></svg></div><aside id="panel"></aside></main>
<script type="application/json" id="map-data">__DATA__</script>
<script>
(function(){
"use strict";
var D=JSON.parse(document.getElementById("map-data").textContent),L=D.labels,SVGNS="http://www.w3.org/2000/svg";
var byId={};D.nodes.forEach(function(n){byId[n.id]=n;});
var COLORS=["#0072B2","#E69F00","#009E73","#CC79A7","#56B4E9","#D55E00","#7B3294","#8C6D1F"];
var BADGES=[["new","✚","#009E73","b_new","changes"],["changed","✎","#0072B2","b_changed","changes"],["moved","⇄","#0072B2","b_moved","changes"],
 ["conflict","⚡","#D55E00","b_conflict","evidence"],["no_evidence","?","#B7791F","b_no_evidence","evidence"],["no_figure","▢","#B7791F","b_no_figure","evidence"],
 ["lever","⚠","#C2410C","b_lever","evidence"],["live_bet","◷","#7B3294","b_live_bet","evidence"],["signed","✔","#374151","b_signed","evidence"],["taste","✦","#C2185B","b_taste","taste"]];
var ROW=30,GAP=34,collapsed={},overlays={changes:true,evidence:true,taste:true},sel=null,query="";
var T={k:1,x:40,y:40};
function h(tag,attrs,kids){var e=document.createElement(tag);if(attrs)for(var a in attrs){if(a==="text")e.textContent=attrs[a];else if(a==="onclick")e.addEventListener("click",attrs[a]);else e.setAttribute(a,attrs[a]);}
 (kids||[]).forEach(function(k){if(k!=null)e.appendChild(typeof k==="string"?document.createTextNode(k):k);});return e;}
function s(tag,attrs){var e=document.createElementNS(SVGNS,tag);for(var a in attrs)e.setAttribute(a,attrs[a]);return e;}
function on(n,key){var b=n.badges;if(key==="conflict")return b.conflict||b.refute>0;if(key==="taste")return b.taste>0;return !!b[key];}
function badges(n){return BADGES.filter(function(B){return overlays[B[4]]&&on(n,B[0]);});}
function hasChange(n){return n.badges.new||n.badges.changed||n.badges.moved;}
function hasProblem(n){var b=n.badges;return b.conflict||b.refute>0||b.no_evidence||b.no_figure||b.lever||b.taste>0;}
function anc(id){var out=[],p=byId[id].parent;while(p&&byId[p]){out.push(p);p=byId[p].parent;}return out;}
function depthPreset(k){collapsed={};D.nodes.forEach(function(n){if(n.depth>=k&&n.children.length)collapsed[n.id]=1;});}
function focus(pred){depthPreset(1);D.nodes.filter(pred).forEach(function(n){anc(n.id).forEach(function(a){delete collapsed[a];});});}
function label(n){return n.title.length>32?n.title.slice(0,31)+"…":n.title;}
function tw(t){var w=0;for(var i=0;i<t.length;i++){w+=t.charCodeAt(i)>0x2e7f?13.2:7.3;}return w;}
function nodeW(n){return tw(label(n))+badges(n).length*15+14;}
function branch(n){if(!n.parent)return "#4b5563";var a=n;while(byId[a.parent]&&byId[a.parent].parent)a=byId[a.parent];
 var i=byId[a.parent].children.indexOf(a.id);return COLORS[(i<0?0:i)%COLORS.length];}
function matches(n){return query&&(n.title+" "+n.summary+" "+n.statement+" "+n.id).toLowerCase().indexOf(query)>=0;}
function layout(){var pos={},row=0;
 (function walk(n,x){var w=nodeW(n),kids=collapsed[n.id]?[]:n.children.map(function(c){return byId[c];});pos[n.id]={x:x,w:w};
  if(!kids.length){pos[n.id].y=row*ROW;row++;}else{kids.forEach(function(k){walk(k,x+w+GAP);});pos[n.id].y=(pos[kids[0].id].y+pos[kids[kids.length-1].id].y)/2;}
 })(byId[D.root],0);return pos;}
function draw(){var vp=document.getElementById("vp");while(vp.firstChild)vp.removeChild(vp.firstChild);var pos=layout();
 D.nodes.forEach(function(n){var p=pos[n.id];if(!p||!n.parent||!pos[n.parent])return;var q=pos[n.parent],a=q.x+q.w,m=(a+p.x)/2;
  vp.appendChild(s("path",{d:"M"+a+","+q.y+" C"+m+","+q.y+" "+m+","+p.y+" "+p.x+","+p.y,fill:"none",stroke:branch(n),"stroke-width":1.4,opacity:.75}));});
 D.nodes.forEach(function(n){var p=pos[n.id];if(!p)return;var col=branch(n),g=s("g",{transform:"translate("+p.x+","+p.y+")","class":sel===n.id?"sel":""});
  g.appendChild(s("rect",{"class":"hl",x:-2,y:-18,width:p.w+4,height:22,rx:4,fill:"transparent"}));
  var ul={d:"M0,0 H"+p.w,stroke:col,"stroke-width":n.depth===0?3:1.6,fill:"none"};if(n.epistemic==="PENDING")ul["stroke-dasharray"]="4 3";g.appendChild(s("path",ul));
  var t=s("text",{x:2,y:-5,"class":"lab"+(n.narrative!=="ACTIVE"?" dem":"")+(matches(n)?" hit":"")});t.appendChild(document.createTextNode(label(n)));
  badges(n).forEach(function(B){var sp=s("tspan",{fill:B[2],dx:4});sp.textContent=B[1];t.appendChild(sp);});
  var tip=s("title",{});tip.textContent=n.title;t.appendChild(tip);t.addEventListener("click",function(e){e.stopPropagation();select(n.id);});g.appendChild(t);
  if(n.children.length){var c=s("circle",{cx:p.w,cy:0,r:4.5,fill:collapsed[n.id]?col:"#fff",stroke:col,"stroke-width":1.5,style:"cursor:pointer"});
   c.addEventListener("click",function(e){e.stopPropagation();if(collapsed[n.id])delete collapsed[n.id];else collapsed[n.id]=1;draw();});g.appendChild(c);}
  vp.appendChild(g);});apply();}
function apply(){document.getElementById("vp").setAttribute("transform","translate("+T.x+","+T.y+") scale("+T.k+")");}
function fit(){var vp=document.getElementById("vp"),b=vp.getBBox(),r=document.getElementById("mapwrap").getBoundingClientRect();if(!b.width)return;
 T.k=Math.min(1.2,(r.width-40)/b.width,Math.max((r.height-40)/b.height,.6));T.k=Math.max(T.k,.6);T.x=20-b.x*T.k;T.y=20-b.y*T.k;apply();}
(function panzoom(){var w=document.getElementById("mapwrap"),start=null;
 w.addEventListener("wheel",function(e){e.preventDefault();var r=w.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top,f=e.deltaY<0?1.12:1/1.12,k=Math.min(3,Math.max(.15,T.k*f));
  T.x=mx-(mx-T.x)*k/T.k;T.y=my-(my-T.y)*k/T.k;T.k=k;apply();},{passive:false});
 w.addEventListener("mousedown",function(e){start={x:e.clientX,y:e.clientY,tx:T.x,ty:T.y};w.classList.add("drag");});
 window.addEventListener("mousemove",function(e){if(!start)return;T.x=start.tx+e.clientX-start.x;T.y=start.ty+e.clientY-start.y;apply();});
 window.addEventListener("mouseup",function(){start=null;w.classList.remove("drag");});
 w.addEventListener("click",function(e){if(e.target.tagName==="svg"){sel=null;draw();panel();}});})();
function stateText(n){return (L["ep_"+n.epistemic]||n.epistemic||"")+" · "+(L["nar_"+n.narrative]||n.narrative||"");}
function commitLine(c){return c.at+" · "+(L[c.by]||c.by)+" · "+(L[c.cause]||c.cause)+(c.message?" — "+c.message:"")+(c.counterfactual?" ｜ "+c.counterfactual:"");}
function diffRows(ch){var rows=[];["title","summary","statement"].forEach(function(f){if(ch.before&&f in ch.before){rows.push(h("div",{"class":"k",text:L["f_"+f]}));
  rows.push(h("div",{},[h("div",{"class":"old",text:ch.before[f]||"∅"}),h("div",{"class":"new",text:ch.after[f]||"∅"})]));}});
 if(ch.before&&ch.before.state){rows.push(h("div",{"class":"k",text:L.f_state}));rows.push(h("div",{text:(L["ep_"+ch.before.state.epistemic]||"")+"/"+(L["nar_"+ch.before.state.narrative]||"")+" → "+(L["ep_"+ch.after.state.epistemic]||"")+"/"+(L["nar_"+ch.after.state.narrative]||"")}));}
 return h("div",{"class":"diff"},rows);}
function list(items){return items&&items.length?h("ul",{},items.map(function(t){return h("li",{text:t});})):h("p",{"class":"muted",text:L.none});}
function legend(){var ul=h("ul",{},BADGES.map(function(B){var li=h("li",{});li.appendChild(h("span",{style:"color:"+B[2],text:B[1]+" "}));li.appendChild(document.createTextNode(L[B[3]]));return li;}));ul.appendChild(h("li",{text:L.lg_dashed}));ul.appendChild(h("li",{text:L.lg_grey}));return ul;}
function panel(){var P=document.getElementById("panel");P.innerHTML="";var n=sel&&byId[sel];
 if(!n){P.appendChild(h("h2",{text:L.overview}));P.appendChild(h("p",{"class":"muted",text:L.select_hint}));
  if(D.notes.length){P.appendChild(h("h3",{text:L.notes}));P.appendChild(list(D.notes.map(function(k){return L[k];})));}
  P.appendChild(h("h3",{text:L.left_story+" ("+D.dropped.length+")"}));P.appendChild(list(D.dropped.map(function(d){return d.title+" — "+(d.reason||L.reason_unknown)+(d.commit?" ("+d.commit.at+", "+(L[d.commit.by]||"")+")":"");})));
  if(D.reminders.length){P.appendChild(h("h3",{text:L.reminders}));P.appendChild(list(D.reminders.map(function(r){return r.rule+" "+r.text;})));}
  if(D.staleness.count){P.appendChild(h("h3",{text:L.stale_files}));P.appendChild(list(D.staleness.files.map(function(f){return f.modified+"  "+f.path;})));}
  P.appendChild(h("h3",{text:L.legend}));P.appendChild(legend());return;}
 P.appendChild(h("h2",{text:n.title}));P.appendChild(h("p",{"class":"muted",text:L.state+": "+stateText(n)+(n.roles.length?" · "+L.roles+": "+n.roles.join(", "):"")}));
 var bs=badges(n);if(bs.length)P.appendChild(h("p",{},bs.map(function(B){return h("span",{style:"color:"+B[2]+";margin-right:8px",text:B[1]+" "+L[B[3]]});})));
 if(n.summary){P.appendChild(h("h3",{text:L.summary}));P.appendChild(h("p",{text:n.summary}));}
 if(n.statement){P.appendChild(h("h3",{text:L.statement}));P.appendChild(h("p",{text:n.statement}));}
 if(n.change){P.appendChild(h("h3",{text:L.changes+L.colon+n.change.kinds.map(function(k){return L[k]||k;}).join(L.enum)+(n.change.moved_from?L.lp+"← "+n.change.moved_from+L.rp:"")}));
  if(n.change.before)P.appendChild(diffRows(n.change));P.appendChild(h("p",{"class":"muted",text:L.commits}));P.appendChild(list(n.change.commits.map(commitLine)));}
 var ev=n.evidence;if(ev||n.badges.no_evidence||n.badges.no_figure){P.appendChild(h("h3",{text:L.evidence}));
  if(ev){if(ev.supports.length){P.appendChild(h("p",{"class":"muted",text:L.supports}));P.appendChild(list(ev.supports));}
   if(ev.refutes.length){P.appendChild(h("p",{"class":"muted",text:L.refutes}));P.appendChild(list(ev.refutes));}
   if(ev.figures.length){P.appendChild(h("p",{"class":"muted",text:L.figures}));P.appendChild(list(ev.figures));}}
  if(n.badges.no_evidence)P.appendChild(h("p",{text:"? "+L.held_unsupported_note}));if(n.badges.no_figure)P.appendChild(h("p",{text:"▢ "+L.no_figure_note}));}
 if(n.taste.length){P.appendChild(h("h3",{text:L.taste}));P.appendChild(list(n.taste.map(function(f){return f.rule+" "+f.rule_text+" — "+L.found_in+" "+(L["f_"+f.field]||f.field)+L.colon+L.lq+f.match+L.rq+" "+f.excerpt;})));}
 if(n.lever)P.appendChild(h("p",{text:"⚠ "+L.lever_note}));if(n.live_bet)P.appendChild(h("p",{text:"◷ "+L.live_bet_note+n.live_bet.resolution_condition}));
 if(n.signed.length)P.appendChild(h("p",{text:"✔ "+L.signed_note+n.signed.join("; ")}));
 P.appendChild(h("p",{"class":"muted",text:n.id+(n.type?" · "+n.type:"")}));}
function select(id){sel=id;anc(id).forEach(function(a){delete collapsed[a];});draw();panel();try{history.replaceState(null,"","#node="+encodeURIComponent(id));}catch(e){}}
function header(){document.getElementById("title").textContent=L.page_title+" — "+D.project;
 document.getElementById("upd").textContent=L.last_update+" "+D.head_at.slice(0,10)+L.lp+D.head_age_days+" "+L.days_ago+L.rp;
 if(D.staleness.count)document.getElementById("stale").textContent=L.stale.replace("{n}",D.staleness.count);
 var src={last_view:L.since_last_view,previous:L.since_previous,arg:L.since_arg,genesis:L.since_genesis}[D.since.source],c=D.counts;
 var tot=c.added+c.changed+c.moved+c.dropped;document.getElementById("since").textContent=L.since_line.replace("{src}",src).replace("{date}",(D.since.at||"").slice(0,10))+L.colon+
  (tot?[L.added+" "+c.added,L.changed+" "+c.changed,L.moved+" "+c.moved,L.dropped+" "+c.dropped].join(L.comma):L.no_change);
 var chips=document.getElementById("chips");[["conflict","conflict"],["no_evidence","no_evidence"],["no_figure","no_figure"],["lever","lever"],["live_bet","live_bet"],["signed","signed"],["taste","taste"]].forEach(function(p){
  var B=BADGES.filter(function(b){return b[0]===p[0];})[0],n=c[p[1]];if(!n)return;
  chips.appendChild(h("span",{"class":"chip",title:L[B[3]],onclick:function(){focus(function(x){return on(x,p[0]);});draw();fit();}},[h("b",{style:"color:"+B[2],text:B[1]}),String(n)+" "+L[B[3]]]));});}
function bar(){var b=document.getElementById("bar");
 function btn(k,fn){b.appendChild(h("button",{text:L[k],onclick:function(){fn();draw();fit();}}));}
 btn("expand_all",function(){collapsed={};});btn("two_levels",function(){depthPreset(2);});btn("only_changes",function(){focus(hasChange);});btn("only_problems",function(){focus(hasProblem);});
 b.appendChild(h("button",{text:L.fit,onclick:fit}));
 ["changes","evidence","taste"].forEach(function(k){var cb=h("input",{type:"checkbox",checked:"checked"});cb.addEventListener("change",function(){overlays[k]=cb.checked;draw();});
  b.appendChild(h("label",{},[cb," "+L["ov_"+k]]));});
 var q=h("input",{type:"search",placeholder:L.search});q.addEventListener("input",function(){query=q.value.trim().toLowerCase();
  if(query)D.nodes.filter(matches).forEach(function(n){anc(n.id).forEach(function(a){delete collapsed[a];});});draw();});b.appendChild(q);}
header();bar();depthPreset(2);D.nodes.filter(hasChange).forEach(function(n){anc(n.id).forEach(function(a){delete collapsed[a];});});
draw();panel();requestAnimationFrame(fit);
var deep=(location.hash||"").match(/node=([^&]+)/);if(deep){var want=decodeURIComponent(deep[1]);if(byId[want])requestAnimationFrame(function(){select(want);});}
})();
</script></body></html>
"""


# The page people see: a prebuilt single-file app (source in web/research-map, built
# with `npm run build`). PAGE above stays as the plain fallback (`render --classic`).
APP_TEMPLATE = Path(__file__).resolve().parent / "research_map_app.html"


def render_html(model: dict, classic: bool = False) -> str:
    payload = dict(model, labels=LABELS[model["lang"]])
    # Every "<" becomes < (as Lighthouse does): escaping only "</" still lets a
    # claim containing "<!--<script>" push the HTML parser past the closing tag.
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    title = "%s — %s" % (LABELS[model["lang"]]["page_title"], model["project"])
    template = PAGE if classic or not APP_TEMPLATE.exists() else APP_TEMPLATE.read_text(encoding="utf-8")
    # data goes in last, so nothing inside the model can be mistaken for a placeholder
    return (template.replace("__LANG__", "zh-CN" if model["lang"] == "zh" else "en")
            .replace("__TITLE__", _html_escape(title)).replace("__DATA__", data))


def _html_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# ============================================================ exports

def _badge_text(node: dict) -> str:
    b = node["badges"]
    glyphs = [g for g, on in (("✚", b["new"]), ("✎", b["changed"]), ("⇄", b["moved"]),
                              ("⚡", b["refute"] or b["conflict"]), ("?", b["no_evidence"]),
                              ("▢", b["no_figure"]), ("⚠", b["lever"]), ("◷", b["live_bet"]),
                              ("✔", b["signed"]), ("✦", b["taste"])) if on]
    return " [%s]" % "".join(glyphs) if glyphs else ""


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def export_markmap(model: dict) -> str:
    lines = ["---", "title: %s" % _one_line(model["project"]), "markmap:",
             "  colorFreezeLevel: 2", "  initialExpandLevel: 2", "---", ""]
    for node in model["nodes"]:
        text = "%s%s <!-- ar:%s -->" % (_one_line(node["title"]), _badge_text(node), node["id"])
        if node["depth"] == 0:
            lines.append("# " + text)
        else:
            lines.append("  " * (node["depth"] - 1) + "- " + text)
    return "\n".join(lines) + "\n"


def _full_layout(model: dict, row: int = 90, col: int = 360) -> dict:
    by_id = {n["id"]: n for n in model["nodes"]}
    pos, counter = {}, [0]

    def walk(nid):
        node = by_id[nid]
        if not node["children"]:
            pos[nid] = (node["depth"] * col, counter[0] * row)
            counter[0] += 1
            return
        for child in node["children"]:
            walk(child)
        ys = [pos[c][1] for c in node["children"]]
        pos[nid] = (node["depth"] * col, (ys[0] + ys[-1]) // 2)

    walk(model["root"])
    return pos


def export_canvas(model: dict) -> dict:
    pos = _full_layout(model)
    nodes, edges = [], []
    for node in model["nodes"]:
        x, y = pos[node["id"]]
        b = node["badges"]
        color = "1" if (b["refute"] or b["conflict"] or b["taste"] or b["lever"]) else \
            ("5" if (b["new"] or b["changed"] or b["moved"]) else None)
        entry = {"id": node["id"], "type": "text", "x": x, "y": y, "width": 300, "height": 70,
                 "text": "**%s**%s\n%s" % (_one_line(node["title"]), _badge_text(node),
                                          clip(_one_line(node["summary"]), 120))}
        if color:
            entry["color"] = color
        nodes.append(entry)
        if node["parent"]:
            edges.append({"id": "e-%s" % node["id"], "fromNode": node["parent"], "fromSide": "right",
                          "toNode": node["id"], "toSide": "left"})
    return {"nodes": nodes, "edges": edges}


def _badge_labels(node: dict, lang: str) -> list:
    L = LABELS[lang]
    b = node["badges"]
    return [L[k] for k, on in (("b_new", b["new"]), ("b_changed", b["changed"]), ("b_moved", b["moved"]),
                               ("b_conflict", b["refute"] or b["conflict"]),
                               ("b_no_evidence", b["no_evidence"]), ("b_no_figure", b["no_figure"]),
                               ("b_lever", b["lever"]), ("b_live_bet", b["live_bet"]),
                               ("b_signed", b["signed"]), ("b_taste", b["taste"])) if on]


def export_xmind(model: dict, out: Path) -> Path:
    by_id = {n["id"]: n for n in model["nodes"]}

    def topic(nid):
        node = by_id[nid]
        body = {"id": nid, "class": "topic", "title": _one_line(node["title"]),
                "notes": {"plain": {"content": "[[ar:%s]]\n%s" % (nid, node["summary"] or "")}}}
        labels = _badge_labels(node, model["lang"])
        if labels:
            body["labels"] = labels
        if node["children"]:
            body["children"] = {"attached": [topic(c) for c in node["children"]]}
        return body

    root = topic(model["root"])
    root["structureClass"] = "org.xmind.ui.logic.right"
    content = [{"id": "ar-sheet", "class": "sheet", "title": _one_line(model["project"])[:60],
                "rootTopic": root}]
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("content.json", json.dumps(content, ensure_ascii=False))
        archive.writestr("metadata.json", json.dumps({"creator": {"name": "auto-research",
                                                                  "version": "3.1.0"}}))
        archive.writestr("manifest.json", json.dumps({"file-entries": {"content.json": {},
                                                                       "metadata.json": {}}}))
    return out


# ============================================================ import -> NarrativePatch

def _parse_markmap(text: str) -> list:
    """[(id|None, title, parent_key, index)] in document order; keys are ids or
    ``new:<n>`` for topics the map added."""
    entries, stack, counter = [], [], 0
    for raw in text.replace("\r\n", "\n").split("\n"):
        heading = re.match(r"^#\s+(.*)$", raw)
        item = re.match(r"^(\s*)[-*+]\s+(.*)$", raw)
        if heading:
            depth, body = 0, heading.group(1)
        elif item:
            depth, body = len(item.group(1).replace("\t", "  ")) // 2 + 1, item.group(2)
        else:
            continue
        found = ID_MARK_RE.search(body)
        title = BADGE_TAIL_RE.sub("", ID_MARK_RE.sub("", body)).strip()
        counter += 1
        key = found.group(1) if found else "new:%d" % counter
        del stack[depth:]
        parent = stack[-1] if stack else None
        entries.append({"key": key, "id": found.group(1) if found else None, "title": title,
                        "parent": parent, "depth": depth})
        stack.append(key)
    return entries


def _parse_xmind(path: Path) -> list:
    with zipfile.ZipFile(path) as archive:
        content = json.loads(archive.read("content.json").decode("utf-8"))
    sheet = content[0] if isinstance(content, list) else content
    entries, counter = [], [0]

    def walk(topic, parent, depth):
        counter[0] += 1
        ident = topic.get("id") if NODE_ID_RE.match(str(topic.get("id") or "")) else None
        if not ident:
            note = (((topic.get("notes") or {}).get("plain") or {}).get("content") or "")
            found = NOTE_MARK_RE.search(note)
            ident = found.group(1) if found else None
        key = ident or "new:%d" % counter[0]
        entries.append({"key": key, "id": ident, "title": _one_line(topic.get("title")),
                        "parent": parent, "depth": depth})
        for child in ((topic.get("children") or {}).get("attached") or []):
            walk(child, key, depth + 1)

    walk(sheet["rootTopic"], None, 0)
    return entries


def import_map(control: Path, path: Path, ref: str = "main") -> dict:
    narr = _narrative()
    narr.require_store(control)
    head_id = narr.read_ref(control, ref)
    head = narr.load_head_snapshot(control, ref)
    if path.suffix.lower() == ".xmind":
        entries = _parse_xmind(path)
    else:
        entries = _parse_markmap(path.read_text(encoding="utf-8"))
    if not entries:
        raise MapError("MAP_EMPTY", str(path))
    nodes = head["nodes"]
    seen, ops, todos, report = set(), [], [], {"renamed": [], "moved": [], "new": [], "missing": []}
    titles = {e["key"]: e["title"] for e in entries}
    siblings = {}
    for entry in entries:
        siblings.setdefault(entry["parent"], []).append(entry)
    for entry in entries:
        nid = entry["id"]
        if nid and nid in nodes and nid not in seen:
            seen.add(nid)
            if entry["title"] and _one_line(entry["title"]) != _one_line(nodes[nid].get("title")):
                ops.append({"op": "patch_fields", "id": nid, "fields": {"title": entry["title"]}})
                report["renamed"].append(nid)
            if entry["parent"] is None:
                if nid != head["root"]:
                    todos.append({"kind": "root_change", "id": nid,
                                  "note": "the map puts a different node at the root; "
                                          "replacing the root needs the researcher's approval"})
                continue
            new_parent = entry["parent"]
            if new_parent not in nodes:
                todos.append({"kind": "under_new_topic", "id": nid, "title": entry["title"],
                              "under": titles.get(new_parent),
                              "note": "moved under a topic the tree does not have yet"})
                continue
            if new_parent != narr.parent_of(head, nid):
                mapped = [e["id"] for e in siblings[entry["parent"]] if e["id"] in nodes]
                ops.append({"op": "move_node", "id": nid, "new_parent": new_parent,
                            "index": mapped.index(nid)})
                report["moved"].append(nid)
        else:
            parent_title = titles.get(entry["parent"]) if entry["parent"] else None
            todos.append({"kind": "new_topic", "title": entry["title"], "under": parent_title,
                          "under_id": entry["parent"] if entry["parent"] in nodes else None,
                          "note": "needs a node type and identity before it can enter the tree"})
            report["new"].append(entry["title"])
    for nid in sorted(set(nodes) - seen):
        todos.append({"kind": "removed_in_map", "id": nid, "title": nodes[nid].get("title"),
                      "note": "not detached automatically: dropping a node needs a disposition "
                              "and a reason"})
        report["missing"].append(nid)
    errors = []
    if ops:
        try:
            preview, _, _ = narr.apply_ops(copy.deepcopy(head), copy.deepcopy(ops), control)
            errors = narr.validate_snapshot(preview)
        except narr.NarrativeError as exc:
            errors = [str(exc)]
    rg = _graph()
    patch = {"schema": narr.PATCH_SCHEMA, "project": rg.project_id(control) if rg else None,
             "ref": ref, "base_commit_id": head_id, "base_tree_hash": narr.tree_hash(head),
             "tree_ops": ops, "todo_proposals": todos, "generated_at": ros.utc_now(),
             "source": {"kind": "map-import", "file": path.name}}
    return {"patch": patch, "report": report, "errors": errors}


# ============================================================ CLI

def emit(payload) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0 if payload.get("ok", True) else 1


def _control(args) -> Path:
    return ros.locate_control(Path(args.project or "."))


def cmd_render(args) -> int:
    control = _control(args)
    model = build_model(control, args.ref, args.since, args.lang, scan_files=not args.no_scan)
    out = Path(args.out) if args.out else map_dir(control) / "research-map.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    ros.atomic_write(out, render_html(model, classic=args.classic))
    view = None if args.no_mark else record_view(control, model, out)
    return emit({"ok": True, "out": str(out), "bytes": out.stat().st_size, "counts": model["counts"],
                 "since": model["since"], "staleness": model["staleness"]["count"],
                 "view_recorded": bool(view), "summary": summary_lines(model)})


def cmd_summary(args) -> int:
    model = build_model(_control(args), args.ref, args.since, args.lang, scan_files=not args.no_scan)
    sys.stdout.write("\n".join(summary_lines(model)) + "\n")
    return 0


def cmd_export(args) -> int:
    control = _control(args)
    model = build_model(control, args.ref, args.since, args.lang, scan_files=False)
    out = Path(args.out)
    if args.format == "markmap":
        ros.atomic_write(out, export_markmap(model))
    elif args.format == "canvas":
        ros.atomic_write(out, json.dumps(export_canvas(model), ensure_ascii=False, indent=2) + "\n")
    else:
        export_xmind(model, out)
    return emit({"ok": True, "format": args.format, "out": str(out), "nodes": len(model["nodes"])})


def cmd_import(args) -> int:
    control = _control(args)
    result = import_map(control, Path(args.file), args.ref)
    if result["errors"]:
        return emit({"ok": False, "error": "MAP_PATCH_WOULD_BE_INVALID", "errors": result["errors"][:6],
                     "report": result["report"]})
    out = Path(args.out)
    ros.atomic_write(out, json.dumps(result["patch"], ensure_ascii=False, indent=2) + "\n")
    return emit({"ok": True, "patch": str(out), "ops": len(result["patch"]["tree_ops"]),
                 "todo_proposals": len(result["patch"]["todo_proposals"]), "report": result["report"],
                 "note": "a proposal only: land it with `narrative apply --patch` then "
                         "`narrative commit --pending-patch <id>`"})


# ============================================================ self-test

def run_self_test() -> int:
    import shutil
    import subprocess
    rg = _graph()
    narr = _narrative()
    tl = _taste()
    rows, failed = [], []

    def ok(name, condition, detail=""):
        rows.append(name)
        if not condition:
            failed.append({"name": name, "detail": str(detail)[:400]})

    def run(script, *argv):
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / script)] + list(argv),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        try:
            return proc.returncode, json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return proc.returncode, {"raw": (proc.stdout or proc.stderr)[:300]}

    tmp = Path(tempfile.mkdtemp(prefix="map-selftest-"))
    try:
        control = rg._fixture(tmp)
        root = control.parent
        chain0 = narr.commit_chain(control, "main")
        before_edits = chain0[-1]["id"]
        # -- three kinds of change after `before_edits`
        rg._run_narrative(root, "node", "patch", "--id", "C-002", "--set",
                          'title="the price of search, restated"', "--set",
                          'statement="One limitation is that the sample is short."',
                          "--out", str(root / "ops.json"))
        rg._run_narrative(root, "commit", "--ops-file", str(root / "ops.json"), "--message",
                          "restate C-002")
        rg._run_narrative(root, "node", "move", "--id", "C-004", "--new-parent", "C-001",
                          "--out", str(root / "ops.json"))
        rg._run_narrative(root, "commit", "--ops-file", str(root / "ops.json"), "--message",
                          "C-004 under C-001")
        rg._run_narrative(root, "node", "detach", "--id", "C-003", "--disposition", "DROPPED",
                          "--reason", "superseded by C-004", "--out", str(root / "ops.json"))
        rg._run_narrative(root, "commit", "--ops-file", str(root / "ops.json"), "--message",
                          "drop C-003")
        code, linked = run("research_graph.py", "link", "--root", str(root), "--from",
                           "ro::run:proto-r02", "--to", "ro::narr:C-002", "--kind", "supported_by",
                           "--polarity", "-", "--basis", "placebo grid")
        ok("fixture: a refuting edge can be linked", code == 0, linked)
        tl.add_rule(control, "No defensive wording", ["story"], "avoid", "user",
                    quote="不要防御性写作", pattern=r"limitation")

        model = build_model(control, "main", before_edits, "zh")
        by = {n["id"]: n for n in model["nodes"]}
        ok("since = the chosen commit", model["since"]["commit"] == before_edits
           and model["since"]["source"] == "arg", model["since"])
        ok("rewritten node carries before/after", by["C-002"]["badges"]["changed"]
           and "restated" in by["C-002"]["change"]["after"]["title"]
           and by["C-002"]["change"]["before"]["title"] == "claim C-002", by["C-002"]["change"])
        ok("moved node names where it came from", by["C-004"]["badges"]["moved"]
           and by["C-004"]["parent"] == "C-001", by["C-004"]["change"])
        dropped = {d["id"]: d for d in model["dropped"]}
        ok("dropped node listed with its recorded reason",
           dropped.get("C-003", {}).get("reason") == "superseded by C-004", model["dropped"])
        ok("change commits say who and why", by["C-002"]["change"]["commits"][-1]["by"] == "by_main"
           and by["C-002"]["change"]["commits"][-1]["cause"].startswith("cause_"),
           by["C-002"]["change"]["commits"])
        ok("unchanged node carries no change", by["C-001"]["change"] is None)
        ok("refuting edge shows on the node", by["C-002"]["badges"]["refute"] == 1, by["C-002"]["evidence"])
        ok("derived support edges show", any(n["badges"]["support"] for n in model["nodes"]),
           [(n["id"], n["badges"]["support"]) for n in model["nodes"]])
        ok("taste flag lands on the changed node that trips it", by["C-002"]["badges"]["taste"] == 1
           and by["C-002"]["taste"][0]["match"].lower() == "limitation", by["C-002"]["taste"])
        ok("counts agree with nodes", model["counts"]["changed"] == 1 and model["counts"]["moved"] == 1
           and model["counts"]["dropped"] == 1, model["counts"])
        ok("summary is <= 40 lines", 0 < len(summary_lines(model)) <= 40)

        genesis = build_model(control, "main", "2000-01-01", "zh", scan_files=False)
        ok("a date before the story means everything is new",
           genesis["since"]["source"] == "genesis" and all(n["badges"]["new"] for n in genesis["nodes"]),
           genesis["since"])

        html = render_html(model)
        classic = render_html(model, classic=True)    # the plain fallback page
        for name, page in (("app", html), ("classic", classic)):
            payload = re.search(r'<script type="application/json" id="map-data">(.*?)</script>', page, re.S)
            ok(f"{name} page embeds the model as JSON that round-trips",
               payload and json.loads(payload.group(1))["head"] == model["head"])
            # inline data: URIs and in-page #fragments are fine; anything fetched is not
            ok(f"{name} page loads nothing from outside",
               not re.search(r"<script[^>]+src=|<link[^>]+href=|@import|url\((?!\s*['\"]?(?:data:|#))", page, re.I))
            ok(f"{name} page: deep links select a node", "#node=" in page and "location.hash" in page)
        ok("app page is the built template with every placeholder filled",
           APP_TEMPLATE.exists() and len(html) > 200_000
           and not any(ph in html for ph in ("__DATA__", "__LANG__", "__TITLE__")))
        hostile = "x</script><!--<script>alert(1)</script>"
        page = render_html(dict(model, project=hostile))
        payload = re.search(r'<script type="application/json" id="map-data">(.*?)</script>', page, re.S)
        ok("markup inside the story cannot close or re-open the data script",
           payload and "<" not in payload.group(1) and json.loads(payload.group(1))["project"] == hostile)
        ok("app page speaks both languages in plain words", all(w in html for w in ("被雪藏", "Benched", "研究地图")))
        ok("classic page carries the labels in the model's language", "只看改动" in classic and "研究地图" in classic)
        english = render_html(dict(model, lang="en"), classic=True)
        ok("language follows the story: CJK root -> zh, ASCII story -> en",
           auto_lang({"root": "R", "nodes": {"R": {"title": "论文 · 叙事树"}}}) == "zh"
           and auto_lang({"root": "R", "nodes": {"R": {"title": "Graph self-test"}}}) == "en")
        ok("english labels available", "Changes only" in english)
        ok("model carries the night's commits with full times",
           all("at_full" in c and "ops" in c for c in model.get("log") or [])
           and isinstance(model.get("tz_offset_minutes"), int))

        out = root / "map.html"
        code, rendered = run("research_map.py", "--project", str(root), "render", "--since", before_edits,
                             "--out", str(out))
        ok("render writes the page and records the view", code == 0 and out.exists()
           and rendered.get("view_recorded") is True, rendered)
        again = build_model(control, "main", None, "zh", scan_files=False)
        ok("next open defaults to 'since your last view' (nothing new)",
           again["since"]["source"] == "last_view" and sum(again["counts"][k] for k in
                                                           ("added", "changed", "moved", "dropped")) == 0,
           again["since"])
        code, _ = run("research_map.py", "--project", str(root), "render", "--no-mark", "--out", str(out))
        ok("--no-mark leaves the view log alone", code == 0 and len(read_views(control)) == 1,
           len(read_views(control)))

        md = export_markmap(model)
        ok("markmap export carries stable ids and badges", "<!-- ar:C-002 -->" in md and "[✎⚡✦]" in md, md[:400])
        canvas = export_canvas(model)
        ok("canvas export: one card per node, one edge per parent link",
           len(canvas["nodes"]) == len(model["nodes"]) and len(canvas["edges"]) == len(model["nodes"]) - 1)
        xm = export_xmind(model, root / "map.xmind")
        with zipfile.ZipFile(xm) as archive:
            content = json.loads(archive.read("content.json"))
            names = set(archive.namelist())
        ok("xmind export: content.json + manifest, root keeps its id",
           {"content.json", "manifest.json", "metadata.json"} <= names
           and content[0]["rootTopic"]["id"] == model["root"], names)

        # -- the researcher edits the markmap file
        edited = []
        for line in md.split("\n"):
            if "ar:C-001 " in line:
                line = line.replace("claim C-001", "the deployment gap")
            if "ar:C-002 " in line:
                continue
            if "ar:C-004 " in line:
                line = line.lstrip()                     # one level up: under the root
                line = "- " + line[2:]
            edited.append(line)
        edited.append("- a brand new idea")
        (root / "edited.md").write_text("\n".join(edited), encoding="utf-8")
        result = import_map(control, root / "edited.md")
        kinds = sorted((op["op"], op["id"]) for op in result["patch"]["tree_ops"])
        ok("import: rename + move become ops", ("patch_fields", "C-001") in kinds
           and ("move_node", "C-004") in kinds, kinds)
        todo_kinds = sorted(t["kind"] for t in result["patch"]["todo_proposals"])
        ok("import: new topic and deleted node become proposals, never ops",
           "new_topic" in todo_kinds and "removed_in_map" in todo_kinds
           and not any(op["op"] in ("add_node", "detach_node") for op in result["patch"]["tree_ops"]),
           todo_kinds)
        ok("import: patch validates against the head", result["errors"] == [], result["errors"])
        (root / "patch.json").write_text(json.dumps(result["patch"], ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "narrative.py"),
                               "apply", str(root), "--patch", str(root / "patch.json")],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        ok("narrative apply accepts the imported patch", proc.returncode == 0
           and "pending_patch_id" in proc.stdout, (proc.stdout or proc.stderr)[:300])

        with zipfile.ZipFile(xm) as archive:
            content = json.loads(archive.read("content.json"))
        content[0]["rootTopic"]["children"]["attached"][0]["title"] = "renamed in XMind"
        first_child = content[0]["rootTopic"]["children"]["attached"][0]["id"]
        with zipfile.ZipFile(root / "edited.xmind", "w") as archive:
            archive.writestr("content.json", json.dumps(content, ensure_ascii=False))
        xres = import_map(control, root / "edited.xmind")
        ok("xmind round-trip: a rename comes back by stable id",
           [(o["op"], o["id"]) for o in xres["patch"]["tree_ops"]] == [("patch_fields", first_child)]
           and not xres["patch"]["todo_proposals"], xres["patch"])

        # -- read-only against the relation layer: a stale projection is reported, not rebuilt
        current = control / "graph" / "current.json"
        saved = current.read_text(encoding="utf-8")
        body = json.loads(saved)
        body["event_count"] = int(body.get("event_count") or 0) + 99
        current.write_text(json.dumps(body), encoding="utf-8")
        stale = build_model(control, "main", before_edits, "zh", scan_files=False)
        ok("stale relation layer: evidence skipped with a note, nothing rebuilt",
           "note_graph_stale" in stale["notes"] and json.loads(current.read_text(encoding="utf-8"))
           ["event_count"] == body["event_count"], stale["notes"])
        current.write_text(saved, encoding="utf-8")

        (root / "paper").mkdir(exist_ok=True)
        (root / "paper" / "sec1.tex").write_text("x\n", encoding="utf-8")
        future = time.time() + 5
        os.utime(root / "paper" / "sec1.tex", (future, future))
        scan = staleness(control, model["head_at"])
        ok("staleness counts story-bearing files changed after the last story commit",
           any(f["path"] == "paper/sec1.tex" for f in scan["files"]), scan)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return emit({"ok": not failed, "checks": len(rows), "failed": len(failed),
                 "failures": failed, "rows": rows})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research_map.py",
                                     description="what the AI did, on one map")
    parser.add_argument("--project", help="project root or .research-os (default: cwd)")
    parser.add_argument("--self-test", dest="self_test", action="store_true")
    sub = parser.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--ref", default="main")
        p.add_argument("--since", help="commit / ref / YYYY-MM-DD (default: your last view)")
        p.add_argument("--lang", choices=sorted(LABELS) + ["auto"], default="auto",
                       help="page language (default: follow the story's own language)")
        return p

    p = common(sub.add_parser("render", help="write the single-file map page"))
    p.add_argument("--out")
    p.add_argument("--no-mark", dest="no_mark", action="store_true",
                   help="do not record this as the researcher's view")
    p.add_argument("--no-scan", dest="no_scan", action="store_true",
                   help="skip the file-staleness scan")
    p.add_argument("--classic", action="store_true",
                   help="write the plain fallback page instead of the app")
    p.set_defaults(func=cmd_render)

    p = common(sub.add_parser("summary", help="<= 40 plain lines for the controller"))
    p.add_argument("--no-scan", dest="no_scan", action="store_true")
    p.set_defaults(func=cmd_summary)

    p = common(sub.add_parser("export", help="markmap / JSON Canvas / XMind"))
    p.add_argument("--format", choices=("markmap", "canvas", "xmind"), required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="an edited markmap / XMind file -> NarrativePatch proposal")
    p.add_argument("--file", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--ref", default="main")
    p.set_defaults(func=cmd_import)
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
    except MapError as exc:
        return emit({"ok": False, "error": exc.code, "detail": exc.detail})


if __name__ == "__main__":
    raise SystemExit(main())
