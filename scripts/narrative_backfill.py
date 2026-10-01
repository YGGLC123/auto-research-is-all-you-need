#!/usr/bin/env python3
"""auto-research narrative backfill (B track) -- docs/narrative-contract.md v1.2 §12.

Turns a pile of dated, hand-written narrative-tree markdown files into a real
commit chain on the authoritative narrative store, *without* letting hindsight
leak in:

  * the snapshot sequence is ordered from path metadata only (filename /
    parent-folder date stamps, `rN` revision ordinals, mtime as last resort) --
    building the order never opens a file;
  * while producing S_t -> S_{t+1} the reader may only open material whose own
    timestamp is <= S_{t+1}'s `as_of`; every open is appended to
    `backfill/access.log` and the gate refuses anything later (§12);
  * each snapshot gets a Historical Frame (`backfill/frames/<id>.json`)
    reconstructed from the material visible at that moment;
  * the tree delta is expressed as §3.3 ops and committed through the one write
    path `narrative.commit_from_ops(...)` with `origin=backfill`,
    `author.role=backfill`; kind stays mechanical, cause stays `unknown`
    (`forced_status=none`) until a human adjudicates blind;
  * `role_assignments` are left null on purpose -- guessing which node was the
    headline on 2026-08-11 is exactly the hindsight the contract forbids;
  * bearing prose with no section number is never attached to a guessed id: it
    goes to `backfill/unmapped.json`.

Structure convention (the reference ledger): the reader-facing trees carry no
C/D/F ids in the body, only numbered headings (`## 0 ...`, `### 0.1 ...`,
`#### 4.3.2 ...`), so the section number *is* the node id (`N-4.3.2`; the
contract id regex was widened for exactly this).  The root is `N-0`; a
top-level section literally numbered `0` therefore shares the root's slot and
its title/body are folded into the root node rather than duplicated.
The internal ledger (`### N-x.y` cards with `C-xxx [level|verdict] ... -> refs`
rows) hangs claim nodes under the matching section node.

CLI (also reachable as `narrative backfill ...` via a thin forward to main()):
    narrative_backfill.py --sources <dir> --project <root> [--dry-run]
    narrative_backfill.py adjudicate --project <root> [--blind|--reveal]
    narrative_backfill.py status --project <root>
    narrative_backfill.py self-test

stdlib-only, Python >= 3.9.  Exit codes: 0 ok, 1 contract refusal, 2 usage/env.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros           # noqa: E402
import narrative as nar             # noqa: E402

# ============================================================ constants

FRAME_SCHEMA = "auto-research/backfill-frame-v1"
ADJ_SCHEMA = "auto-research/backfill-adjudication-v1"
UNMAPPED_SCHEMA = "auto-research/backfill-unmapped-v1"

ROOT_ID = "R-0"   # the tree root is not a paper section; section "0" keeps its own id N-0
STATEMENT_CAP = 8000
SUMMARY_CAP = 200
UNMAPPED_MIN_CHARS = 120

# path-metadata timestamp: 20260811-1740 / 20260818 / 2026-08-11
TS_RE = re.compile(r"(20\d{2})[-_]?(0[1-9]|1[0-2])[-_]?(0[1-9]|[12]\d|3[01])"
                   r"(?:[-_T](\d{2})(\d{2}))?")
REV_RE = re.compile(r"[._-]r(\d+)", re.IGNORECASE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
SECNUM_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)*)[\s\.:、，·]*(.*)$")
CARD_RE = re.compile(r"^(N-[0-9]+(?:\.[0-9]+)*)\s*(.*)$")
CLAIM_RE = re.compile(r"^[-*]\s*(C-[0-9]+)\s*[\[［]([^\]］]*)[\]］]\s*(.*)$")
REF_TOKEN_RE = re.compile(r"\b([A-Z]{1,2}-[0-9]+(?:\.[0-9]+)*)")
DECISION_HEAD_RE = re.compile(r"^##\s+(20\d{2}-\d{2}-\d{2})\s+(.*?)\s*$")
VENUE_VOCAB = ("RFS", "JFEc", "JFQA", "JFE", "JF", "AAAI", "JBES", "MS", "EJC", "IAAI")

HELD_WORDS = ("CLOSED", "已闭合", "已证", "PROVED", "HELD")
LIVE_WORDS = ("在飞", "待读数", "IN-FLIGHT", "IN FLIGHT", "PILOT",
              "试跑", "待冷评", "AWAITING-REVIEW", "AWAITING REVIEW",
              "PENDING_READING")

STRUCTURE_MARK = ("正式版", "草稿")     # 正式版 / 草稿
INTERNAL_MARK = ("内部版",)                     # 内部版
TODO_MARK = ("待办",)                               # 待办
TREE_MARK = "叙事树"                            # 叙事树
FRAME_MARK = ("简化版", "详细版", "骨架图",
              "QA台账", "叙事与旗舰")
FRAME_EXTRA_NAMES = ("_01_叙事与旗舰_当前.md", "decisions.md")


def emit(obj) -> int:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ============================================================ paths

def backfill_dir(control: Path) -> Path:
    return nar.ndir(control) / "backfill"


def frames_dir(control: Path) -> Path:
    return backfill_dir(control) / "frames"


def adjudications_dir(control: Path) -> Path:
    return backfill_dir(control) / "adjudications"


def access_log_path(control: Path) -> Path:
    return backfill_dir(control) / "access.log"


def unmapped_path(control: Path) -> Path:
    return backfill_dir(control) / "unmapped.json"


# ============================================================ time gate (§12)

class AccessGate:
    """Every content read goes through here.

    `as_of` is the horizon of the snapshot currently being produced.  A file
    whose own timestamp is later than the horizon is refused -- that is the
    machine form of "processing S_t -> S_{t+1} may only read material dated
    <= S_{t+1}".  Every allowed open is appended to backfill/access.log so the
    claim is auditable after the fact rather than merely asserted.
    """

    def __init__(self, control: Path, enabled: bool = True):
        self.control = control
        self.enabled = enabled
        self.as_of = None
        self.frame_id = None
        self.key = None
        self.records = []

    def horizon(self, frame_id: str, as_of: str, key=None) -> None:
        """`key` is the driving version's full sort key, so two versions stamped
        the same day (r1 then r2) stay ordered: while producing the r1 snapshot
        the r2 file is still in the future and may not be opened."""
        self.frame_id = frame_id
        self.as_of = as_of
        self.key = key

    def visible(self, doc) -> bool:
        if self.as_of is None:
            return True
        if doc.ts_iso > self.as_of:
            return False
        if self.key is None or doc.role in ("todo", "frame"):
            return True          # contemporaneous side material, never a milestone
        return doc.sort_key <= self.key

    def read(self, doc) -> str:
        if not self.visible(doc):
            nar.refuse("BACKFILL_HINDSIGHT_READ",
                       f"{doc.rel} is dated {doc.ts_iso}, later than horizon {self.as_of}")
        raw = doc.path.read_bytes()
        record = {"at": nar.now_iso(), "frame": self.frame_id, "as_of": self.as_of,
                  "horizon_rev": (self.key or ("", 0, ""))[1], "path": doc.rel,
                  "file_ts": doc.ts_iso, "rev": doc.rev, "bytes": len(raw),
                  "sha256": sha256_bytes(raw)}
        self.records.append(record)
        if self.enabled:
            path = access_log_path(self.control)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return raw.decode("utf-8", errors="replace")


# ============================================================ source inventory

def _under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


class SourceDoc:
    """One candidate input file.  Its timestamp comes from path metadata only --
    deriving the snapshot order must never require opening anything."""

    def __init__(self, path: Path, base: Path, role: str, ts: datetime, ts_source: str,
                 rev: int):
        self.path = path
        self.rel = str(path.relative_to(base)).replace("\\", "/") if _under(path, base) \
            else path.name
        self.role = role                     # structure | internal | todo | frame
        self.ts = ts
        self.ts_iso = iso(ts)
        self.ts_source = ts_source
        self.rev = rev

    @property
    def sort_key(self):
        return (self.ts_iso, self.rev, self.rel)

    def describe(self) -> dict:
        return {"path": self.rel, "role": self.role, "ts": self.ts_iso,
                "ts_source": self.ts_source, "rev": self.rev}


def _ts_from_text(text: str):
    match = TS_RE.search(text)
    if not match:
        return None
    year, month, day, hour, minute = match.groups()
    try:
        return datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0),
                        tzinfo=timezone.utc)
    except ValueError:
        return None


def path_timestamp(path: Path, base: Path):
    """filename stamp -> nearest parent-folder stamp -> mtime.  Metadata only."""
    found = _ts_from_text(path.name)
    if found:
        return found, "filename"
    parent = path.parent
    while True:
        found = _ts_from_text(parent.name)
        if found:
            return found, "folder:" + parent.name
        if parent == base or parent == parent.parent:
            break
        parent = parent.parent
    stat = path.stat()
    return (datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).replace(microsecond=0),
            "mtime")


def _rev_of(name: str) -> int:
    match = REV_RE.search(name)
    return int(match.group(1)) if match else 0


def classify(name: str) -> "str | None":
    if TREE_MARK in name:
        if any(mark in name for mark in INTERNAL_MARK):
            return "internal"
        if any(mark in name for mark in TODO_MARK):
            return "todo"
        if any(mark in name for mark in STRUCTURE_MARK):
            return "structure"
        return "frame"
    if name in FRAME_EXTRA_NAMES or any(mark in name for mark in FRAME_MARK):
        return "frame"
    return None


def inventory(sources: Path, extra, base: Path, max_depth: int = 3) -> list:
    docs, seen, candidates = [], set(), []
    for root, dirs, files in os.walk(sources):
        depth = len(Path(root).relative_to(sources).parts)
        if depth >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.lower().endswith(".md"):
                candidates.append(Path(root) / name)
    candidates.extend(extra or [])
    for path in candidates:
        path = path.resolve()
        if not path.exists() or path in seen:
            continue
        seen.add(path)
        role = classify(path.name)
        if role is None:
            continue
        ts, ts_source = path_timestamp(path, sources)
        docs.append(SourceDoc(path, base, role, ts, ts_source, _rev_of(path.name)))
    docs.sort(key=lambda d: d.sort_key)
    return docs


# ============================================================ markdown parsing

def split_headings(text: str):
    """([(level, heading, body, line_no)], lead_paragraph) in document order."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, current, lead, fenced = [], None, [], False
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        match = None if fenced else HEADING_RE.match(line)
        if match:
            if current:
                out.append(current)
            current = [len(match.group(1)), match.group(2), [], number]
        elif current:
            current[2].append(line)
        else:
            lead.append(line)
    if current:
        out.append(current)
    return ([(lvl, head, "\n".join(body).strip(), line) for lvl, head, body, line in out],
            "\n".join(lead).strip())


def tidy(text: str, cap: int) -> str:
    text = re.sub(r"[ \t]+", " ", (text or "").replace("\r", "")).strip()
    if len(text) > cap:
        text = text[:cap].rstrip() + " …"
    return text


def parse_structure(text: str, rel: str) -> dict:
    """Reader-facing narrative tree: numbered headings become N-<sec> sections."""
    headings, lead = split_headings(text)
    title, sections, order, unmapped = "", {}, [], []
    for level, head, body, line in headings:
        if level == 1 and not title:
            title = tidy(head, SUMMARY_CAP)
            if body:
                lead = (lead + "\n" + body).strip() if lead else body
            continue
        match = SECNUM_RE.match(head)
        if not match or not match.group(1):
            if len(body) >= UNMAPPED_MIN_CHARS:
                unmapped.append({"source": rel, "heading": tidy(head, 160), "line": line,
                                 "chars": len(body), "reason": "unnumbered-bearing-section",
                                 "excerpt": tidy(body, 240)})
            continue
        sec, rest = match.group(1), match.group(2)
        if sec in sections:
            unmapped.append({"source": rel, "heading": tidy(head, 160), "line": line,
                             "chars": len(body), "reason": "duplicate-section-number",
                             "excerpt": tidy(body, 240)})
            continue
        sections[sec] = {"sec": sec, "title": tidy(rest or head, SUMMARY_CAP),
                         "statement": tidy(body, STATEMENT_CAP), "line": line}
        order.append(sec)
    return {"title": title, "lead": tidy(lead, STATEMENT_CAP), "sections": sections,
            "order": order, "unmapped": unmapped}


def epistemic_of(verdict: str) -> str:
    upper = (verdict or "").upper()
    return "HELD" if any(word.upper() in upper for word in HELD_WORDS) else "PENDING"


def is_live(verdict: str) -> bool:
    upper = (verdict or "").upper()
    return any(word.upper() in upper for word in LIVE_WORDS)


def parse_internal(text: str, rel: str) -> dict:
    """Internal ledger: `### N-x.y title` cards holding `C-xxx [level|verdict] ...` rows."""
    headings, _lead = split_headings(text)
    cards, order, unmapped = {}, [], []
    for level, head, body, line in headings:
        match = CARD_RE.match(head)
        if not match:
            if len(body) >= UNMAPPED_MIN_CHARS and level >= 2:
                unmapped.append({"source": rel, "heading": tidy(head, 160), "line": line,
                                 "chars": len(body), "reason": "crosscut-table-no-section-id",
                                 "excerpt": tidy(body, 240)})
            continue
        host = match.group(1)
        claims = []
        for offset, row in enumerate(body.split("\n")):
            hit = CLAIM_RE.match(row.strip())
            if not hit:
                continue
            cid, bracket, rest = hit.group(1), hit.group(2), hit.group(3)
            pieces = [p.strip() for p in re.split(r"[|｜]", bracket)]
            bearing = pieces[0] if pieces else "unspecified"
            verdict = pieces[1] if len(pieces) > 1 else (pieces[0] if pieces else "")
            statement, marker, bindings = rest.partition("→")
            if not marker:
                statement, marker, bindings = rest.partition("->")
            claims.append({"id": cid, "host": host, "bearing": bearing or "unspecified",
                           "verdict": verdict or "unspecified",
                           "statement": tidy(rest, STATEMENT_CAP),
                           "refs": sorted(set(REF_TOKEN_RE.findall(bindings))),
                           "line": line + 1 + offset})
        if host in cards:
            continue
        cards[host] = {"host": host, "title": tidy(match.group(2) or host, SUMMARY_CAP),
                       "claims": claims, "line": line}
        order.append(host)
    return {"cards": cards, "order": order, "unmapped": unmapped}


def parse_decisions(text: str) -> list:
    """`## YYYY-MM-DD title` blocks from .research-os/decisions.md."""
    out = []
    current = None
    for line in text.replace("\r\n", "\n").split("\n"):
        head = DECISION_HEAD_RE.match(line)
        if head:
            if current:
                out.append(current)
            current = {"at": head.group(1) + "T00:00:00Z",
                       "title": tidy(head.group(2), SUMMARY_CAP),
                       "ref": "decision:" + head.group(1) + "-" + str(len(out) + 1),
                       "body": []}
        elif current is not None and line.strip():
            current["body"].append(line.strip())
    if current:
        out.append(current)
    for item in out:
        item["summary"] = tidy(" ".join(item.pop("body"))[:400], 400)
    return out


def parse_todo(text: str) -> list:
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if re.match(r"^[-*|]\s*\**\s*(T-[0-9]+|Q-[0-9]+)", stripped):
            out.append(tidy(stripped.lstrip("-*| "), 180))
    return out


# ============================================================ snapshot model

def section_parent(sec: str, present: set) -> str:
    parts = sec.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in present:
            return "N-" + candidate
    return ROOT_ID


def node_id(sec: str) -> str:
    return "N-" + sec


def build_state(structure: dict, internal: dict, source_rel: dict) -> dict:
    """The intended tree for one snapshot: {nodes: {id: fields}, order: [ids]}.

    fields = {node_type, identity_key, title, summary, statement, state,
              state_meta, basis_refs, parent}
    """
    nodes = {}
    order = []
    unmapped = []
    present = set(structure["sections"])
    lead = (structure.get("lead") or "").strip()
    # A preamble that is metadata (blockquote / **版本** note) is not a north-star
    # sentence; leave the statement empty for the author to set in the tree.
    root_statement = "" if (not lead or lead.startswith(">") or lead.startswith("**")) else lead
    root_title = re.sub(r"[(（][^()（）]*(UTC|服务器钟)[^()（）]*[)）]\s*$", "",
                        structure["title"] or "").strip()
    nodes[ROOT_ID] = {
        "node_type": "narrative",
        "identity_key": {"role_hint": "north_star", "thesis": "root:" + ROOT_ID},
        "title": root_title or "narrative tree",
        "summary": tidy(root_statement, SUMMARY_CAP) if root_statement else "",
        "statement": tidy(root_statement, STATEMENT_CAP),
        "state": {"epistemic": "PENDING", "narrative": "ACTIVE"},
        "state_meta": {"resolution_condition": None, "live_bet": False},
        "basis_refs": [], "parent": None,
    }
    order.append(ROOT_ID)
    for sec in structure["order"]:
        nid = node_id(sec)                # section "0" is an ordinary axis node (N-0)
        node = structure["sections"][sec]
        nodes[nid] = {
            "node_type": "narrative",
            "identity_key": {"role_hint": "section", "thesis": "sec:" + sec},
            "title": node["title"],
            "summary": tidy(node["statement"] or node["title"], SUMMARY_CAP),
            "statement": node["statement"],
            "state": {"epistemic": "PENDING", "narrative": "ACTIVE"},
            "state_meta": {"resolution_condition": None, "live_bet": False},
            "basis_refs": [], "parent": section_parent(sec, present),
        }
        order.append(nid)
    # ---- internal ledger claims hang under their host section, never guessed
    for host in internal.get("order", []):
        card = internal["cards"][host]
        for claim in card["claims"]:
            if host not in nodes:
                unmapped.append({"source": source_rel.get("internal", "?"),
                                 "heading": claim["id"] + " @ " + host,
                                 "line": claim["line"], "chars": len(claim["statement"]),
                                 "reason": "card-host-section-absent",
                                 "excerpt": tidy(claim["statement"], 240)})
                continue
            if claim["id"] in nodes:
                unmapped.append({"source": source_rel.get("internal", "?"),
                                 "heading": claim["id"] + " @ " + host,
                                 "line": claim["line"], "chars": len(claim["statement"]),
                                 "reason": "duplicate-claim-id",
                                 "excerpt": tidy(claim["statement"], 240)})
                continue
            epistemic = epistemic_of(claim["verdict"])
            live = is_live(claim["verdict"])
            basis = list(claim["refs"])
            basis.append("backfill:" + source_rel.get("internal", "?") + "#" + claim["id"])
            nodes[claim["id"]] = {
                "node_type": "claim",
                "identity_key": {"subject": host, "target": claim["id"],
                                 "predicate": "ledger-assertion", "quantifier": "unspecified",
                                 "domain": "narrative-ledger", "conditions": [],
                                 "polarity": "affirm"},
                "title": claim["id"] + " " + tidy(claim["statement"], 80),
                "summary": "[" + claim["bearing"] + "|" + claim["verdict"] + "] "
                           + tidy(claim["statement"], SUMMARY_CAP - 40),
                "statement": claim["statement"],
                "state": {"epistemic": epistemic, "narrative": "ACTIVE"},
                "state_meta": {
                    "resolution_condition": ("verdict=" + claim["verdict"]) if live else None,
                    "live_bet": bool(live and epistemic == "PENDING")},
                "basis_refs": sorted(set(basis)), "parent": host,
            }
            order.append(claim["id"])
    return {"nodes": nodes, "order": order, "unmapped": unmapped}


def children_plan(state: dict) -> dict:
    """parent -> ordered children, following document order."""
    plan = {}
    for nid in state["order"]:
        parent = state["nodes"][nid]["parent"]
        if parent:
            plan.setdefault(parent, []).append(nid)
    return plan


# ============================================================ state -> ops (§3.3)

COMPARED_FIELDS = ("title", "summary", "statement", "state", "state_meta", "basis_refs")


def snapshot_to_state(snapshot: dict) -> dict:
    """Read the committed snapshot back into the same shape build_state produces."""
    nodes = {}
    order = []
    parents = {}
    for nid, node in snapshot.get("nodes", {}).items():
        for child in node.get("children", []):
            parents[child] = nid
    for nid, node in snapshot.get("nodes", {}).items():
        nodes[nid] = {"node_type": node["node_type"], "identity_key": node["identity_key"],
                      "title": node["title"], "summary": node["summary"],
                      "statement": node["statement"], "state": node["state"],
                      "state_meta": node["state_meta"], "basis_refs": node["basis_refs"],
                      "parent": parents.get(nid)}
        order.append(nid)
    return {"nodes": nodes, "order": sorted(order), "unmapped": []}


def _node_payload(nid: str, fields: dict) -> dict:
    return {"id": nid, "node_type": fields["node_type"],
            "identity_key": fields["identity_key"], "title": fields["title"],
            "summary": fields["summary"], "statement": fields["statement"],
            "state": dict(fields["state"]), "state_meta": dict(fields["state_meta"]),
            "basis_refs": list(fields["basis_refs"])}


def removed_order(removed: list, old_nodes: dict, old_children: dict) -> list:
    """Post-order over the removed forest, siblings visited in reverse order.

    Children therefore leave before their parent (every detach sees a leaf) and
    the mirrored inverse re-adds parents before children, in ascending index."""
    removed_set = set(removed)
    roots = [nid for nid in removed
             if old_nodes.get(nid, {}).get("parent") not in removed_set]
    roots.sort(key=lambda nid: -_sibling_index(nid, old_nodes, old_children))
    out, seen = [], set()

    def walk(node_id: str) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        for child in reversed(old_children.get(node_id, [])):
            if child in removed_set:
                walk(child)
        out.append(node_id)

    for root in roots:
        walk(root)
    for nid in removed:                      # anything the forest missed
        walk(nid)
    return out


def _sibling_index(nid: str, old_nodes: dict, old_children: dict) -> int:
    parent = old_nodes.get(nid, {}).get("parent")
    kids = old_children.get(parent, [])
    return kids.index(nid) if nid in kids else 0


def diff_states(old_state: dict, new_state: dict, old_children: dict, new_children: dict) -> list:
    """Node-level diff -> ops, ordered so every op is legal when it is applied.

    add (DFS, parents first) -> move survivors -> detach removed subtree roots
    -> patch_fields -> reorder.  Survivors are moved out *before* detaches so a
    detach cascade never swallows a node that still exists downstream.
    """
    ops = []
    old_nodes, new_nodes = old_state["nodes"], new_state["nodes"]
    added = [nid for nid in new_state["order"] if nid not in old_nodes]
    removed = [nid for nid in old_state["order"] if nid not in new_nodes]
    both = [nid for nid in new_state["order"] if nid in old_nodes]

    # 1) additions in DFS order (a parent is always added before its children)
    live = set(old_nodes)
    pending = list(added)
    guard = 0
    while pending and guard <= len(added) + 2:
        guard += 1
        deferred = []
        for nid in pending:
            fields = new_nodes[nid]
            parent = fields["parent"]
            if parent and parent not in live:
                deferred.append(nid)
                continue
            index = (new_children.get(parent, []).index(nid)
                     if parent and nid in new_children.get(parent, []) else None)
            ops.append({"op": "add_node", "node": _node_payload(nid, fields),
                        "parent": parent, "index": index})
            live.add(nid)
        if len(deferred) == len(pending):
            break
        pending = deferred
    for nid in pending:                       # unreachable parent: never guess a home
        new_state["unmapped"].append({"source": "(diff)", "heading": nid, "line": 0,
                                      "chars": 0, "reason": "unreachable-parent",
                                      "excerpt": str(new_nodes[nid]["parent"])})

    # 2) survivors that changed parent move first
    for nid in both:
        if old_nodes[nid]["parent"] != new_nodes[nid]["parent"] and new_nodes[nid]["parent"]:
            parent = new_nodes[nid]["parent"]
            index = (new_children.get(parent, []).index(nid)
                     if nid in new_children.get(parent, []) else None)
            ops.append({"op": "move_node", "id": nid, "new_parent": parent, "index": index})

    # 3) detach what left the tree, deepest first, siblings in reverse order.
    #    Detaching a *childless* node keeps the generated inverse_ops exact: a
    #    non-leaf detach archives the parent with its children[] still filled,
    #    and replaying that inverse would re-insert children that the archived
    #    body already lists.  Reverse sibling order makes the mirrored re-adds
    #    land back on their original indices.
    for nid in removed_order(removed, old_nodes, old_children):
        ops.append({"op": "detach_node", "id": nid, "narrative_disposition": "DROPPED",
                    "disposition_meta": {"reason": "section absent from the next dated "
                                                   "version of the narrative tree",
                                         "basis_refs": []}})

    # 4) field patches
    for nid in both:
        before, after = old_nodes[nid], new_nodes[nid]
        fields = {}
        for key in COMPARED_FIELDS:
            if nar.canonical(before.get(key)) != nar.canonical(after.get(key)):
                fields[key] = after.get(key)
        if fields:
            ops.append({"op": "patch_fields", "id": nid, "fields": fields})

    # 5) sibling reordering, simulated on the surviving membership
    simulated = {}
    survivors = set(new_nodes)
    for parent, kids in old_children.items():
        if parent in survivors:
            simulated[parent] = [k for k in kids if k in survivors
                                 and new_nodes[k]["parent"] == parent]
    for nid in added:
        parent = new_nodes[nid]["parent"]
        if parent:
            simulated.setdefault(parent, [])
            if nid not in simulated[parent]:
                index = (new_children.get(parent, []).index(nid)
                         if nid in new_children.get(parent, []) else None)
                if index is None or index >= len(simulated[parent]):
                    simulated[parent].append(nid)
                else:
                    simulated[parent].insert(index, nid)
    for nid in both:
        parent = new_nodes[nid]["parent"]
        if parent and old_nodes[nid]["parent"] != parent:
            simulated.setdefault(parent, [])
            if nid not in simulated[parent]:
                simulated[parent].append(nid)
    for parent, desired in new_children.items():
        current = simulated.get(parent, [])
        for index, want in enumerate(desired):
            if index < len(current) and current[index] == want:
                continue
            if want not in current:
                continue
            current.remove(want)
            current.insert(index, want)
            ops.append({"op": "move_node", "id": want, "new_parent": parent, "index": index})
        simulated[parent] = current
    return ops


# ============================================================ Historical Frame (§12)

def build_frame(frame_id: str, as_of: str, gate: AccessGate, visible: list,
                structure: dict, internal: dict, todo_lines: list,
                decisions: list, project_hint: str) -> dict:
    """Everything that was *knowable at as_of* about target/venue/standard.

    Only material already opened under the horizon feeds this; nothing is
    inferred from a later version.
    """
    venue = None
    for token in VENUE_VOCAB:
        if re.search(r"(^|[^A-Za-z])" + token + r"([^A-Za-z]|$)", project_hint):
            venue = token
            break
    haystack = "\n".join([structure.get("title", ""), structure.get("lead", "")])
    if venue is None:
        for token in VENUE_VOCAB:
            if re.search(r"(^|[^A-Za-z])" + token + r"([^A-Za-z]|$)", haystack):
                venue = token
                break
    target = ""
    for line in (structure.get("lead") or "").split("\n"):
        if "中心" in line or "口号" in line or "主线" in line:
            target = tidy(line, 400)
            break
    if not target:
        target = tidy(structure.get("title") or "", 400)
    standard = ""
    for line in (structure.get("lead") or "").split("\n"):
        if "纪律" in line or "Evidence Gate" in line or "晋升规则" in line or "判据" in line:
            standard = tidy(line, 400)
            break
    known_basis = set()
    for card in (internal.get("cards") or {}).values():
        for claim in card["claims"]:
            known_basis.update(claim["refs"])
    frame = {
        "schema": FRAME_SCHEMA,
        "id": frame_id,
        "as_of": as_of,
        "source_manifest": [dict(record, role=role) for record, role in visible],
        "root_statement": tidy(structure.get("lead") or structure.get("title") or "", 2000),
        "target": target,
        "venue": venue,
        "evidence_standard": standard,
        "known_basis_refs": sorted(known_basis)[:400],
        "open_questions": todo_lines[:200],
        "decision_refs": [{"ref": d["ref"], "at": d["at"], "title": d["title"]}
                          for d in decisions],
    }
    return frame


# ============================================================ the run

def ensure_store(control: Path, project: Path, title: str, venue, at: str,
                 session: str) -> dict:
    if nar.ndir(control).exists() and nar.read_ref(control, "main"):
        return {"initialised": False}
    argv = ["init", str(project), "--root-id", ROOT_ID, "--title", title,
            "--thesis", "root:" + ROOT_ID, "--session", session, "--at", at,
            "--message", "genesis (backfill): empty root before the first dated version"]
    if venue:
        argv += ["--venue", venue]
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        code = nar.main(argv)
    if code != 0:
        ros.die("narrative init failed", 2)
    return {"initialised": True}


def already_committed(control: Path, frame_id: str, ref: str) -> "str | None":
    for commit in nar.commit_chain(control, ref):
        if commit.get("input_manifest_id") == frame_id and commit.get("origin") == "backfill":
            return commit.get("id")
    return None


def record_unmapped(control: Path, entries: list) -> int:
    """Append-only (§0 auxiliary layer): existing rows are never rewritten."""
    store = ros.read_json_file(unmapped_path(control),
                               {"schema": UNMAPPED_SCHEMA, "entries": []})
    store.setdefault("schema", UNMAPPED_SCHEMA)
    store.setdefault("entries", [])
    seen = {(e.get("frame"), e.get("source"), e.get("heading"), e.get("line"))
            for e in store["entries"]}
    added = 0
    for entry in entries:
        key = (entry.get("frame"), entry.get("source"), entry.get("heading"), entry.get("line"))
        if key in seen:
            continue
        seen.add(key)
        store["entries"].append(entry)
        added += 1
    store["generated_at"] = nar.now_iso()
    ros.atomic_write(unmapped_path(control), nar.dump_json(store))
    return added


def run_backfill(sources: Path, project: Path, dry_run: bool = False,
                 ref: str = "main", session: str = "backfill") -> dict:
    sources = sources.expanduser().resolve()
    project = project.expanduser().resolve()
    if not sources.is_dir():
        ros.die("--sources must be a directory: " + str(sources), 2)
    control = ros.locate_control(project)
    if not control.exists():
        ros.die("no .research-os under " + str(project), 2)

    extra = [control / "decisions.md"]
    docs = inventory(sources, extra, sources)
    structure_docs = [d for d in docs if d.role == "structure"]
    internal_docs = [d for d in docs if d.role == "internal"]
    if not structure_docs:
        ros.die("no numbered narrative-tree versions under " + str(sources), 2)

    # ---- snapshot sequence: every dated structural OR internal version is a state
    milestones = sorted(structure_docs + internal_docs, key=lambda d: d.sort_key)
    frames_plan = []
    for index, doc in enumerate(milestones, start=1):
        frames_plan.append({"frame_id": "frame-%02d" % index, "as_of": doc.ts_iso,
                            "key": doc.sort_key, "driver": doc})

    first = milestones[0]
    gate = AccessGate(control, enabled=not dry_run)
    gate.horizon("frame-00", first.ts_iso, first.sort_key)
    genesis_title = tidy(parse_structure(gate.read(structure_docs[0]),
                                         structure_docs[0].rel)["title"], SUMMARY_CAP) \
        or project.name
    venue_hint = project.name

    if not dry_run:
        ensure_store(control, project, genesis_title or project.name,
                     next((t for t in VENUE_VOCAB
                           if re.search(r"(^|[^A-Za-z])" + t + r"([^A-Za-z]|$)", venue_hint)),
                          None),
                     first.ts_iso, session)
        for sub in ("backfill", "backfill/frames", "backfill/adjudications"):
            (nar.ndir(control) / sub).mkdir(parents=True, exist_ok=True)

    prev_state = snapshot_to_state(nar.load_head_snapshot(control, ref)) if not dry_run \
        else {"nodes": {}, "order": [], "unmapped": []}
    prev_children = {}
    if not dry_run:
        head_snapshot = nar.load_head_snapshot(control, ref)
        for nid, node in head_snapshot.get("nodes", {}).items():
            if node.get("children"):
                prev_children[nid] = list(node["children"])

    results = []
    all_unmapped = []
    previous_commit = nar.read_ref(control, ref) if not dry_run else None
    for plan in frames_plan:
        frame_id, as_of = plan["frame_id"], plan["as_of"]
        gate.horizon(frame_id, as_of, plan["key"])
        visible_docs = [d for d in docs if gate.visible(d)]
        latest_structure = max([d for d in structure_docs if gate.visible(d)],
                              key=lambda d: d.sort_key, default=None)
        latest_internal = max([d for d in internal_docs if gate.visible(d)],
                             key=lambda d: d.sort_key, default=None)
        if latest_structure is None:
            continue
        opened = []
        structure_text = gate.read(latest_structure)
        opened.append((gate.records[-1], "structure"))
        structure = parse_structure(structure_text, latest_structure.rel)
        internal = {"cards": {}, "order": [], "unmapped": []}
        source_rel = {"structure": latest_structure.rel}
        if latest_internal is not None:
            internal_text = gate.read(latest_internal)
            opened.append((gate.records[-1], "internal"))
            internal = parse_internal(internal_text, latest_internal.rel)
            source_rel["internal"] = latest_internal.rel
        todo_lines = []
        for doc in [d for d in visible_docs if d.role == "todo"]:
            todo_lines.extend(parse_todo(gate.read(doc)))
            opened.append((gate.records[-1], "todo"))
        decisions = []
        for doc in [d for d in visible_docs if d.rel.endswith("decisions.md")]:
            decisions = [d for d in parse_decisions(gate.read(doc)) if d["at"] <= as_of]
            opened.append((gate.records[-1], "frame"))
        for doc in [d for d in visible_docs if d.role == "frame"
                    and not d.rel.endswith("decisions.md")][:6]:
            gate.read(doc)
            opened.append((gate.records[-1], "frame"))

        state = build_state(structure, internal, source_rel)
        new_children = children_plan(state)
        for entry in structure["unmapped"] + internal["unmapped"] + state["unmapped"]:
            all_unmapped.append(dict(entry, frame=frame_id, as_of=as_of))
        ops = diff_states(prev_state, state, prev_children, new_children)
        for entry in state["unmapped"]:
            if entry.get("reason") == "unreachable-parent":
                all_unmapped.append(dict(entry, frame=frame_id, as_of=as_of))

        frame = build_frame(frame_id, as_of, gate, opened, structure, internal,
                            todo_lines, decisions, venue_hint)
        row = {"frame_id": frame_id, "as_of": as_of, "driver": plan["driver"].rel,
               "ops": len(ops), "nodes": len(state["nodes"]),
               "op_counts": _op_counts(ops)}

        if dry_run or not ops:
            row["commit_id"] = None
            row["skipped"] = "no-op" if not ops else "dry-run"
            results.append(row)
            prev_state, prev_children = state, new_children
            continue

        existing = already_committed(control, frame_id, ref)
        if existing:
            row["commit_id"] = existing
            row["skipped"] = "already-committed"
            results.append(row)
            previous_commit = existing
            prev_state, prev_children = state, new_children
            continue

        meta = {
            "message": "backfill " + frame_id + " <- " + plan["driver"].rel,
            "author": {"session": session, "role": "backfill"},
            "origin": "backfill",
            "at": as_of,
            "frame_id": frame_id,
            "trigger_ref": None,
            "trigger_class": None,
            "approval_refs": {"overthrow": None, "forced_cause": None},
        }
        outcome = nar.commit_from_ops(control, ref, ops, meta)
        frame["commit_id"] = outcome["commit_id"]
        frame["before_commit"] = previous_commit
        frame["kind"] = outcome["kind"]
        frame["cause"] = outcome["cause"]
        frame["forced_status"] = outcome["forced_status"]
        ros.atomic_write(frames_dir(control) / (frame_id + ".json"), nar.dump_json(frame))
        row.update({"commit_id": outcome["commit_id"], "kind": outcome["kind"],
                    "cause": outcome["cause"], "forced_status": outcome["forced_status"],
                    "before_commit": previous_commit})
        previous_commit = outcome["commit_id"]
        results.append(row)
        committed = nar.load_head_snapshot(control, ref)
        prev_state = snapshot_to_state(committed)
        prev_children = {nid: list(node.get("children") or [])
                         for nid, node in committed.get("nodes", {}).items()}

    unmapped_added = 0 if dry_run else record_unmapped(control, all_unmapped)
    return {"ok": True, "dry_run": dry_run, "project": str(project), "sources": str(sources),
            "control": str(control), "ref": ref,
            "sources_seen": [d.describe() for d in docs],
            "snapshots": len(frames_plan), "commits": len([r for r in results
                                                           if r.get("commit_id")
                                                           and not r.get("skipped")]),
            "rows": results, "unmapped": len(all_unmapped),
            "unmapped_written": unmapped_added,
            "access_log_entries": len(gate.records)}


def _op_counts(ops: list) -> dict:
    counts = {}
    for op in ops:
        counts[op["op"]] = counts.get(op["op"], 0) + 1
    return counts


# ============================================================ blind adjudication (§12)

def adjudicate(project: Path, reveal: bool = False, ref: str = "main") -> dict:
    control = ros.locate_control(Path(project))
    nar.require_store(control)
    frames = sorted(frames_dir(control).glob("frame-*.json"))
    loaded = [ros.read_json_file(path, {}) for path in frames]
    loaded = [f for f in loaded if f.get("commit_id")]
    loaded.sort(key=lambda f: (f.get("as_of") or "", f.get("id") or ""))
    adjudications_dir(control).mkdir(parents=True, exist_ok=True)

    rows, created, kept = [], 0, 0
    for index in range(1, len(loaded)):
        before, after = loaded[index - 1], loaded[index]
        adj_id = "adj-" + after["id"]
        path = adjudications_dir(control) / (adj_id + ".json")
        commit = nar.load_commit(control, after["commit_id"])
        summary = _structural_summary(commit)
        candidates = [d for d in (after.get("decision_refs") or [])
                      if (before.get("as_of") or "") < d["at"] <= after["as_of"]]
        if not candidates:
            candidates = (after.get("decision_refs") or [])[-5:]
        body = {
            "schema": ADJ_SCHEMA,
            "id": adj_id,
            "frame_id": after["id"],
            "before_frame": before["id"],
            "before_commit": before["commit_id"],
            "after_commit_candidate": after["commit_id"],
            "as_of": after["as_of"],
            "window": {"from": before.get("as_of"), "to": after["as_of"]},
            "committed_kind": commit.get("kind"),
            "committed_cause": commit.get("cause"),
            "committed_forced_status": commit.get("forced_status"),
            "structural_summary": summary,
            "candidate_triggers": candidates,
            "blind_decision": {"cause": None, "forced_status": None, "trigger_ref": None},
            "approval_ref": None,
            "retrospective_notes": [],
            "blind": True,
            "generated_at": nar.now_iso(),
        }
        if path.exists():
            kept += 1
            body = ros.read_json_file(path, body)        # §12: never overwrite an adjudication
        else:
            ros.atomic_write(path, nar.dump_json(body))
            created += 1
        rows.append((before, after, commit, summary, candidates, body))

    queue = _queue_markdown(rows, reveal=reveal, control=control)
    queue_path = adjudications_dir(control) / "queue.md"
    ros.atomic_write(queue_path, queue)
    pending = len([r for r in rows if (r[5].get("blind_decision") or {}).get("cause") is None])
    return {"ok": True, "pairs": len(rows), "created": created, "kept": kept,
            "pending": pending, "reveal": bool(reveal), "queue": str(queue_path)}


def _structural_summary(commit: dict) -> dict:
    counts, ids = {}, {}
    for op in commit.get("ops") or []:
        name = op.get("op")
        counts[name] = counts.get(name, 0) + 1
        target = op.get("id") or (op.get("node") or {}).get("id")
        if target:
            ids.setdefault(name, []).append(target)
    return {"counts": counts, "ids": {k: sorted(set(v))[:24] for k, v in ids.items()},
            "flags": commit.get("flags") or []}


def _queue_markdown(rows: list, reveal: bool, control: Path) -> str:
    out = ["# 回填盲裁队列 · backfill adjudication queue", "",
           "每行 = 一对相邻快照。第一遍**盲裁**:只看窗口内(≤ 本行 as_of)的材料,",
           "不看任何更晚版本的叙事;裁 `cause` / `forced_status` / `trigger_ref` 三格,",
           "写回 `backfill/adjudications/<id>.json` 的 `blind_decision`(旧件永不覆盖)。",
           "",
           "- `cause=forced` 必须同时给 `approval_ref`(user-approved decision record)与反事实;",
           "- 找不到当时的合格 trigger ⇒ 保持 `unknown` / `none`(契约 §3.2 回填未裁行)。", ""]
    if not rows:
        out.append("_(尚无相邻快照对)_")
        return "\n".join(out) + "\n"
    for before, after, commit, summary, candidates, body in rows:
        decided = (body.get("blind_decision") or {}).get("cause")
        out.append("## " + body["id"] + " · " + (before.get("as_of") or "?")
                   + " → " + after["as_of"]
                   + ("  [已裁:" + str(decided) + "]" if decided else "  [待裁]"))
        out.append("")
        out.append("- 提交:`" + str(body["before_commit"])[:12] + "` → `"
                   + str(body["after_commit_candidate"])[:12] + "`"
                   + "(kind=" + str(body["committed_kind"])
                   + ",当前 cause=" + str(body["committed_cause"])
                   + "/" + str(body["committed_forced_status"]) + ")")
        counts = summary["counts"]
        out.append("- 结构变化:" + (", ".join(k + "×" + str(v)
                                                  for k, v in sorted(counts.items()))
                                       or "(无)"))
        for name in ("add_node", "detach_node", "move_node"):
            if summary["ids"].get(name):
                out.append("  - " + name + ": " + ", ".join(summary["ids"][name][:12])
                           + (" …" if len(summary["ids"][name]) > 12 else ""))
        if summary["ids"].get("patch_fields"):
            shown = summary["ids"]["patch_fields"]
            out.append("  - patch_fields: " + ", ".join(shown[:12])
                       + (" …+" + str(len(shown) - 12) if len(shown) > 12 else ""))
        out.append("- 当时可见的候选 trigger(decisions.md,时间 ≤ 本行 as_of):")
        if candidates:
            for item in candidates[-6:]:
                out.append("  - `" + item["ref"] + "` " + item["at"][:10] + " — "
                           + item["title"])
        else:
            out.append("  - (窗口内无记录的决定)")
        out.append("- 待裁:`cause` = forced | unforced | unknown;`forced_status`;"
                   "`trigger_ref`(必须早于本提交且触及被改节点)")
        if reveal:
            out.append("- **后见材料(--reveal)**:")
            out.append("  - 本对之后的快照:" + (", ".join(
                sorted(p.stem for p in frames_dir(control).glob("frame-*.json")
                       if ros.read_json_file(p, {}).get("as_of", "") > after["as_of"]))
                or "(无)"))
            out.append("  - 事后说明位:写进 `retrospective_notes[]`,不改 `blind_decision`。")
        out.append("")
    return "\n".join(out) + "\n"


# ============================================================ status

def status(project: Path, ref: str = "main") -> dict:
    control = ros.locate_control(Path(project))
    nar.require_store(control)
    frames = sorted(frames_dir(control).glob("frame-*.json"))
    commits = []
    for commit in nar.commit_chain(control, ref):
        if commit.get("origin") == "backfill":
            commits.append({"id": commit["id"], "at": commit["at"], "kind": commit["kind"],
                            "cause": commit["cause"],
                            "forced_status": commit["forced_status"],
                            "frame": commit.get("input_manifest_id")})
    unmapped = ros.read_json_file(unmapped_path(control), {"entries": []})
    reasons = {}
    for entry in unmapped.get("entries", []):
        reasons[entry.get("reason")] = reasons.get(entry.get("reason"), 0) + 1
    pending, decided = 0, 0
    for path in sorted(adjudications_dir(control).glob("adj-*.json")):
        body = ros.read_json_file(path, {})
        if (body.get("blind_decision") or {}).get("cause") is None:
            pending += 1
        else:
            decided += 1
    snapshot = nar.load_head_snapshot(control, ref)
    log_lines = 0
    if access_log_path(control).exists():
        with open(access_log_path(control), "r", encoding="utf-8") as handle:
            log_lines = sum(1 for _ in handle)
    return {"ok": True, "project": str(Path(project).resolve()), "ref": ref,
            "snapshots": len(frames), "backfill_commits": len(commits),
            "nodes_at_head": len(snapshot.get("nodes", {})),
            "unmapped": len(unmapped.get("entries", [])), "unmapped_by_reason": reasons,
            "adjudications_pending": pending, "adjudications_decided": decided,
            "access_log_entries": log_lines, "commits": commits}


# ============================================================ self-test

FIX_V1 = """# 微型叙事树 正式版 v0.1(2026-01-01 09:00)

> 本件为对外叙事的正式版;主线口号=甲乙丙。纪律:不使用"首次"。

## 0 开场

### 0.1 通道一

通道一的论证正文,第一版措辞。

### 0.2 通道二

通道二的论证正文,第一版措辞,本节稍后会被删掉。

## 1 判据

### 1.1 判据主体

判据主体的正文,第一版。

## 2 装置

装置一节的正文,第一版。

## 附录 · 无编号承重段

这一段有实质内容但没有章节号。回填不得替它猜任何现有 id,只能原样进 unmapped 清单,
留给人工判断它到底属于哪一节、或者该不该单独建节点。它写得足够长,是为了越过承重门槛
的字数下界,从而确保这条路径在 self-test 里真的被走到,而不是因为太短被静默丢弃;
同时它也演示了"宁可不映射,也不猜 id"这条纪律在解析器里的实际落点。
"""

FIX_INT1 = """# 微型叙事树 内部版 v0.1(2026-01-01 10:00)

## A 部分 · 逐节控盘卡

### N-0.1 通道一
- C-011 [critical|CLOSED] 通道一断言 → R-01 D-01
- C-012 [supporting|在飞] 通道一在飞读数 → S-01

### N-1.1 判据主体
- C-111 [critical|OPEN] 判据主体尚未闭合 → R-02
"""

FIX_V2 = """# 微型叙事树 正式版 v0.2(2026-01-02 09:00)

> 本件为对外叙事的正式版;主线口号=甲乙丙丁(改口)。纪律:不使用"首次"。

## 0 开场

### 0.1 通道一

通道一的论证正文,第二版措辞已改写。

## 2 装置

装置一节的正文,第一版。

## 1 判据

### 1.1 判据主体(改名)

判据主体的正文,第一版。

### 1.2 判据补节

第二版新增的补节正文。
"""

FIX_INT2 = """# 微型叙事树 内部版 v0.2(2026-01-02 10:00)

## A 部分 · 逐节控盘卡

### N-0.1 通道一
- C-011 [critical|CLOSED] 通道一断言 → R-01 D-01
- C-012 [supporting|在飞] 通道一在飞读数 → S-01

### N-1.1 判据主体
- C-111 [critical|CLOSED] 判据主体已闭合 → R-02 D-07
"""

FIX_FUTURE = """# 微型叙事树 正式版 v0.3(2026-02-01 09:00)

> 未来版本:处理更早的快照时绝不许打开本件。

## 0 开场

### 0.1 通道一

未来版本的正文。
"""

FIX_DECISIONS = """# Research Decisions

## 2026-01-01 起手:口径冻结
- 冻结第一版口径。

## 2026-01-02 冷评压力下删节
- 通道二一节按冷评意见删除;补节 1.2。
"""


def _fixture(root: Path) -> Path:
    sources = root / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    control = root / ".research-os"
    control.mkdir(parents=True, exist_ok=True)
    ros.atomic_write(control / "state.json", nar.dump_json(
        {"schema": "auto-research/state-v2", "project": "backfill-selftest"}))
    ros.atomic_write(control / "decisions.md", FIX_DECISIONS)
    ros.atomic_write(sources / "_叙事树_正式版_v0.1_20260101-0900.md", FIX_V1)
    ros.atomic_write(sources / "_叙事树_内部版_v0.1_20260101-1000.md", FIX_INT1)
    ros.atomic_write(sources / "_叙事树_正式版_v0.2_20260102-0900.md", FIX_V2)
    ros.atomic_write(sources / "_叙事树_内部版_v0.2_20260102-1000.md", FIX_INT2)
    ros.atomic_write(sources / "_叙事树_正式版_v0.3_20260201-0900.md", FIX_FUTURE)
    return sources


def self_test() -> int:
    checks = []

    def check(name: str, condition, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(condition), "detail": detail})

    tmp = Path(tempfile.mkdtemp(prefix="narrative-backfill-selftest-"))
    try:
        nar.set_now("2026-03-01T00:00:00Z")
        root = tmp / "proj"
        sources = _fixture(root)
        control = ros.locate_control(root)

        result = run_backfill(sources, root, dry_run=False, session="selftest")
        check("snapshot sequence has one state per dated version",
              result["snapshots"] == 5, str(result["snapshots"]))
        committed = [r for r in result["rows"] if r.get("commit_id")]
        check("every snapshot with a delta produced a backfill commit",
              len(committed) == 5, str([r["frame_id"] for r in committed]))

        chain = nar.commit_chain(control, "main")
        backfilled = [c for c in chain if c["origin"] == "backfill"]
        check("commit chain is linear and backfill-attributed",
              len(backfilled) == 5 and all(c["author"]["role"] == "backfill"
                                           for c in backfilled),
              str(len(backfilled)))
        check("unadjudicated backfill rows are cause=unknown/forced_status=none",
              all(c["cause"] == "unknown" and c["forced_status"] == "none"
                  for c in backfilled if c["kind"] != "refine")
              and all(c["cause"] in ("unknown", "none") for c in backfilled),
              str([(c["kind"], c["cause"], c["forced_status"]) for c in backfilled]))
        check("kind stayed mechanical (no backfill override)",
              all(c["kind"] in ("refine", "restructure", "overthrow", "merge")
                  for c in backfilled)
              and any(c["kind"] == "restructure" for c in backfilled),
              str([c["kind"] for c in backfilled]))

        # ---- frames
        frames = sorted(frames_dir(control).glob("frame-*.json"))
        check("one Historical Frame per snapshot", len(frames) == 5, str(len(frames)))
        frame_bodies = [ros.read_json_file(p, {}) for p in frames]
        check("frames carry as_of / root_statement / decision_refs",
              all(f.get("as_of") and f.get("root_statement") is not None
                  and isinstance(f.get("decision_refs"), list) for f in frame_bodies))
        check("frame decision_refs never include later decisions",
              all(all(d["at"] <= f["as_of"] for d in f["decision_refs"])
                  for f in frame_bodies))

        # ---- the time-visibility gate
        entries = []
        with open(access_log_path(control), "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    entries.append(json.loads(line))
        late = [e for e in entries if e.get("as_of") and e["file_ts"] > e["as_of"]]
        check("access.log contains no file dated later than its snapshot",
              not late, str(late[:3]))
        future_reads = [e for e in entries if "v0.3" in e["path"]
                        and (e.get("as_of") or "") < "2026-02-01T09:00:00Z"]
        check("the future version was never opened for an earlier snapshot",
              not future_reads, str(future_reads[:2]))
        check("every snapshot logged at least one open",
              len({e["frame"] for e in entries if e["frame"].startswith("frame-")}) >= 5)

        # ---- node-level diff behaviour (inspected at frame-04, the last state
        #      before the deliberately-late v0.3 fixture prunes the subtree)
        head = nar.load_head_snapshot(control, "main")

        def snapshot_at(frame_id: str) -> dict:
            for commit in chain:
                if commit.get("input_manifest_id") == frame_id:
                    return nar.load_snapshot(control, commit["tree"])
            raise AssertionError("no commit for " + frame_id)

        mid = snapshot_at("frame-04")
        nodes = mid["nodes"]
        check("section numbers became node ids",
              {"N-1", "N-1.1", "N-1.2", "N-2"} <= set(nodes), str(sorted(nodes)))
        check("a deleted section is DROPPED, never physically removed",
              "N-0.2" not in nodes and "N-0.2" not in head["nodes"])
        check("a whole removed subtree leaves together (deepest first)",
              not ({"N-1", "N-1.1", "N-1.2", "N-2"} & set(head["nodes"])),
              str(sorted(head["nodes"])))
        dropped = [op for c in backfilled for op in c["ops"]
                   if op["op"] == "detach_node" and op.get("id") == "N-0.2"]
        check("the deletion was recorded as detach_node(DROPPED)",
              len(dropped) == 1 and dropped[0]["narrative_disposition"] == "DROPPED")
        moves = [op for c in backfilled for op in c["ops"] if op["op"] == "move_node"]
        check("the heading reshuffle produced move_node ops", bool(moves), str(moves[:2]))
        renamed = [op for c in backfilled for op in c["ops"]
                   if op["op"] == "patch_fields" and op.get("id") == "N-1.1"
                   and "title" in (op.get("fields") or {})]
        check("a renamed heading is patch_fields(title), not a new id", bool(renamed))
        check("claim cards hang under their host section",
              nodes.get("C-011") is not None
              and "C-011" in nodes["N-0.1"]["children"])
        verdict = [op for c in backfilled for op in c["ops"]
                   if op["op"] == "patch_fields" and op.get("id") == "C-111"
                   and "state" in (op.get("fields") or {})]
        check("a card verdict change is a state patch on the same id", bool(verdict))
        check("verdict CLOSED maps to HELD with a non-empty basis",
              nodes["C-111"]["state"]["epistemic"] == "HELD"
              and bool(nodes["C-111"]["basis_refs"]))
        check("an in-flight card is a live bet with a resolution condition",
              nodes["C-012"]["state_meta"]["live_bet"] is True
              and bool(nodes["C-012"]["state_meta"]["resolution_condition"]))
        check("role_assignments stay null (left to blind adjudication)",
              all(v is None for v in head["role_assignments"].values()),
              str(head["role_assignments"]))
        check("every committed snapshot validates",
              not [e for c in chain
                   for e in nar.validate_snapshot(nar.load_snapshot(control, c["tree"]))])

        # ---- unmapped
        unmapped = ros.read_json_file(unmapped_path(control), {"entries": []})["entries"]
        reasons = {e["reason"] for e in unmapped}
        check("unnumbered bearing prose went to unmapped.json",
              "unnumbered-bearing-section" in reasons, str(sorted(reasons)))
        check("unmapped rows never invent an id",
              all(not re.match(r"^[A-Z]{1,2}-[0-9]", e["heading"] or "") or "@" in e["heading"]
                  for e in unmapped))

        # ---- blind adjudication
        first = adjudicate(root, reveal=False)
        check("one adjudication per adjacent snapshot pair",
              first["pairs"] == 4 and first["created"] == 4, nar.dump_json(first))
        queue = (adjudications_dir(control) / "queue.md").read_text(encoding="utf-8")
        check("queue.md lists every pair", queue.count("## adj-frame-") == 4)
        check("blind queue hides the retrospective block", "--reveal" not in queue)
        sample = ros.read_json_file(adjudications_dir(control) / "adj-frame-05.json", {})
        check("adjudication skeleton leaves cause/forced_status/trigger blank",
              sample["blind_decision"] == {"cause": None, "forced_status": None,
                                           "trigger_ref": None})
        check("candidate triggers come from decisions dated inside the window",
              all(t["at"] <= sample["as_of"] for t in sample["candidate_triggers"]))

        # ---- never overwrite an existing adjudication (§12)
        sample["blind_decision"] = {"cause": "unforced", "forced_status": "none",
                                    "trigger_ref": None}
        ros.atomic_write(adjudications_dir(control) / "adj-frame-05.json",
                         nar.dump_json(sample))
        second = adjudicate(root, reveal=True)
        kept = ros.read_json_file(adjudications_dir(control) / "adj-frame-05.json", {})
        check("a decided adjudication survives a re-run",
              kept["blind_decision"]["cause"] == "unforced" and second["kept"] == 4)
        revealed = (adjudications_dir(control) / "queue.md").read_text(encoding="utf-8")
        check("--reveal attaches the hindsight block", "--reveal" in revealed)

        # ---- idempotent re-run
        again = run_backfill(sources, root, dry_run=False, session="selftest")
        check("re-running the backfill writes no duplicate commits",
              len(nar.commit_chain(control, "main")) == len(chain),
              str(len(nar.commit_chain(control, "main"))))
        check("re-run reports the frames as already committed",
              all(r.get("skipped") in ("already-committed", "no-op")
                  for r in again["rows"] if r.get("ops")))

        report = status(root)
        check("status reports snapshots / commits / unmapped / pending",
              report["snapshots"] == 5 and report["backfill_commits"] == 5
              and report["unmapped"] >= 1 and report["adjudications_pending"] == 3,
              nar.dump_json({k: report[k] for k in
                             ("snapshots", "backfill_commits", "unmapped",
                              "adjudications_pending")}))
        lines = nar.trajectory_lines(control, window=3650)
        check("trajectory stays inside its 40-line budget", len(lines) <= 40, str(len(lines)))
    finally:
        nar.set_now(None)
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [c for c in checks if not c["ok"]]
    print(json.dumps({"ok": not failed, "suite": "narrative_backfill",
                      "passed": len(checks) - len(failed), "total": len(checks),
                      "failed": failed}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


# ============================================================ CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="narrative_backfill.py",
        description="backfill a dated narrative-tree archive into the commit graph "
                    "(contract docs/narrative-contract.md v1.2 §12)")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="parse -> frames -> node-level diff -> commits")
    run.add_argument("--sources", type=Path, required=True)
    run.add_argument("--project", type=Path, required=True)
    run.add_argument("--ref", default="main")
    run.add_argument("--session", default="backfill")
    run.add_argument("--dry-run", dest="dry_run", action="store_true")
    run.add_argument("--now")

    adj = sub.add_parser("adjudicate", help="blind adjudication queue + skeletons")
    adj.add_argument("--project", type=Path, required=True)
    adj.add_argument("--ref", default="main")
    adj.add_argument("--blind", action="store_true", default=True)
    adj.add_argument("--reveal", action="store_true",
                     help="attach hindsight material (second pass only)")
    adj.add_argument("--now")

    stat = sub.add_parser("status", help="snapshots / commits / unmapped / pending")
    stat.add_argument("--project", type=Path, required=True)
    stat.add_argument("--ref", default="main")
    stat.add_argument("--now")

    sub.add_parser("self-test", help="two-version fixture through the whole pipeline")
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    # `--sources ...` with no verb means `run` (matches `narrative backfill --sources ...`)
    if argv and argv[0].startswith("-"):
        argv = ["run"] + argv
    if not argv:
        argv = ["--help"]
    args = build_parser().parse_args(argv)
    if getattr(args, "now", None):
        nar.set_now(args.now)
    try:
        if args.command == "run":
            return emit(run_backfill(args.sources, args.project, dry_run=args.dry_run,
                                     ref=args.ref, session=args.session))
        if args.command == "adjudicate":
            return emit(adjudicate(args.project, reveal=args.reveal, ref=args.ref))
        if args.command == "status":
            return emit(status(args.project, ref=args.ref))
        if args.command == "self-test":
            return self_test()
    except nar.NarrativeError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "detail": exc.detail},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    build_parser().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
