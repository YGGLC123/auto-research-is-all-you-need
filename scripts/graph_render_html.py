#!/usr/bin/env python3
"""auto-research research-graph view -- single-file HTML renderer (v3 §4).

`research_graph.py view argument|coverage|conflicts|objects` emits <=40 lines of
text for the main controller's budget.  This module emits the *interactive*
surface a human reads instead: one page, four tabs, no network of any kind.

    argument   the argument chain from the headline (or `--node`) as an outline
               tree -- claim -> evidence (supported_by, + solid green dot, -
               red dot, contested `(!)`) -> run / data (depends_on).  Every row
               is selectable; the inspector shows that object's in/out edges,
               each source's by / basis / at, and `[GONE]` / `[MISSING]`.
    coverage   the A-E lists of the coverage report (load-bearing claims no
               figure expresses / figures expressing no claim / HELD with no
               positive support / carries a (-) edge yet still ACTIVE /
               EDGE_CONFLICT).  Every item jumps into the argument view at that
               object.
    conflicts  each EDGE_CONFLICT with both sides laid out side by side
               (source / polarity / basis / time) and a "提议裁决" button that
               exports a `auto-research/decision-draft-v1` JSON.
    objects    the object index grouped by space (id / title / status), `[GONE]`
               greyed, with a search filter.

Data source is `.research-os/graph/current.json` (v3 contract §0) and nothing
else: the page is a projection of the materialised current-relations file, so it
can never disagree with the text views.  When that file has not been built yet
the module degrades to projecting the same shape live out of `research_graph`
(and says so in the receipt), which keeps the view usable mid-migration.

The page NEVER writes research state.  The only thing it produces is a decision
DRAFT the user exports and a human adjudicates through the CLI.

Public API (the `--html` forwarding target):
    render(control, view="argument", out_path=None, node=None, project="")
        -> receipt dict when `out_path` is given, else the page as a string

Standalone:
    py scripts/graph_render_html.py --project <root> \
        --view argument|coverage|conflicts|objects [--node <ro>] --out <file>
    py scripts/graph_render_html.py --self-test

Visual language is the narrative axis page's, deliberately: same system font
stack, same 13/11 two-size type on a 32px row grid, same 22px indent, same 1px
hairline `details/summary` outline tree, semantic colour only in the 6px dot,
selection = 3px left bar + 4% wash, sticky 360px inspector, tri-state theme
tokens, zero external references, guarded localStorage.

stdlib-only, Python >= 3.9.  Exit codes: 0 ok, 1 refusal, 2 environment/usage.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros  # noqa: E402

VIEW_SCHEMA = "auto-research/graph-view-v1"
DECISION_DRAFT_SCHEMA = "auto-research/decision-draft-v1"
CURRENT_REL = "graph/current.json"
VIEWS = ("argument", "coverage", "conflicts", "objects")
SPACES = ("narr", "claim", "tree", "run", "fig", "asset", "ledger", "decision")
SIZE_BUDGET = 400 * 1024
MAX_DEPTH = 4


# ============================================================ payload assembly

def _s(value) -> str:
    return "" if value is None else str(value)


def _short_ro(address: str) -> str:
    """`ro:<project>:<space>:<id>` -> `<space>:<id>`; anything else unchanged."""
    parts = _s(address).split(":", 3)
    if len(parts) == 4 and parts[0] == "ro":
        return parts[2] + ":" + parts[3]
    return _s(address) or "?"


def _ro_parts(address: str) -> tuple:
    parts = _s(address).split(":", 3)
    if len(parts) == 4 and parts[0] == "ro":
        return parts[1], parts[2], parts[3]
    return "", "", _s(address)


def _norm_source(row) -> dict:
    if not isinstance(row, dict):
        return {"edge_id": "", "by": _s(row), "basis": "", "at": "", "polarity": None}
    polarity = row.get("polarity")
    return {"edge_id": _s(row.get("edge_id")), "by": _s(row.get("by")) or "main",
            "basis": _s(row.get("basis")), "at": _s(row.get("at")),
            "polarity": polarity if polarity in ("+", "-") else None}


def _norm_edge(row: dict) -> dict:
    """Accept both the nested `logical_key` shape of the v3 contract and the
    flat `from/to/kind` shape `research_graph.logical_edges()` already returns,
    so the page does not care which side of the migration wrote the file."""
    key = row.get("logical_key")
    if isinstance(key, dict):
        frm, to, kind = key.get("from"), key.get("to"), key.get("kind")
    else:
        frm, to, kind = row.get("from"), row.get("to"), row.get("kind")
    # graph-contract 4.1: on a logical edge `sources` is the COUNT of merged
    # assertions and `provenance` is the list; older/other shapes may carry a
    # list under `sources`. Accept both, never iterate an integer.
    sources = row.get("provenance")
    if not isinstance(sources, list):
        sources = row.get("sources")
    if not isinstance(sources, list):
        sources = []
    sources = [_norm_source(s) for s in sources]
    polarity = row.get("polarity")
    signs = sorted({s["polarity"] for s in sources if s["polarity"]})
    conflict = row.get("conflict")
    if conflict is None:
        conflict = len(signs) > 1
    return {"from": _s(frm), "to": _s(to), "kind": _s(kind) or "depends_on",
            "polarity": None if conflict else (polarity if polarity in ("+", "-")
                                               else (signs[0] if len(signs) == 1 else None)),
            "conflict": bool(conflict), "sources": sources,
            "from_title": _s(row.get("from_title")), "to_title": _s(row.get("to_title"))}


def _norm_objects(raw) -> dict:
    out = {}
    if not isinstance(raw, dict):
        return out
    for address, body in raw.items():
        body = body if isinstance(body, dict) else {}
        project, space, ident = _ro_parts(address)
        out[address] = {"space": _s(body.get("space")) or space or "?",
                        "id": _s(body.get("id")) or ident,
                        "title": _s(body.get("title")),
                        "status": _s(body.get("status")) or "present"}
    return out


def _fill_endpoints(edges: list, objects: dict) -> None:
    """An endpoint the index never learned about is still a real endpoint: give
    it a placeholder row marked `missing` rather than dropping the edge."""
    for edge in edges:
        for end, title_key in (("from", "from_title"), ("to", "to_title")):
            address = edge[end]
            if not address:
                continue
            record = objects.get(address)
            if record is None:
                _project, space, ident = _ro_parts(address)
                objects[address] = {"space": space or "?", "id": ident,
                                    "title": edge.get(title_key) or "",
                                    "status": "missing"}
            elif not record["title"] and edge.get(title_key):
                record["title"] = edge[title_key]


def load_current(control: Path) -> dict:
    """Read `graph/current.json`; when it is absent, project the same shape out
    of `research_graph` in-process so the page still works before V3-A's
    materialiser lands.  Never writes anything."""
    path = Path(control) / CURRENT_REL
    if path.exists():
        body = ros.read_json_file(path, {}) or {}
        body.setdefault("source", str(path))
        return body
    return _synthesize_current(control)


def _synthesize_current(control: Path) -> dict:
    try:
        import research_graph as rg
    except Exception as exc:                                  # pragma: no cover
        raise SystemExit("cannot read %s and cannot import research_graph (%s)"
                         % (CURRENT_REL, exc))
    edges = rg.logical_edges(control)
    events = rg.read_edge_events(control)
    last = ""
    for event in events:
        last = event.get("edge_id") or event.get("event_id") or last
    objects = {}
    for edge in edges:
        for address in (edge.get("from"), edge.get("to")):
            if address and address not in objects:
                result = rg.resolve(control, address)
                obj = result.get("object") or {}
                _project, space, ident = _ro_parts(address)
                title = (obj.get("title") or obj.get("message") or obj.get("semantic")
                         or obj.get("protocol") or obj.get("text") or "")
                objects[address] = {"space": space, "id": ident, "title": title,
                                    "status": "present" if result.get("exists") else "gone"}
    return {"schema": "auto-research/graph-current-v1", "built_at": "",
            "last_event_id": last, "edges": edges,
            "conflicts": [e for e in edges if e.get("conflict")],
            "objects": objects, "summary": {}, "source": "live:research_graph"}


def load_coverage(control: Path, current: dict) -> dict:
    """The A-E lists.  `current.json` may carry them under `coverage`; if not we
    ask `research_graph.coverage_report()`; if that is unavailable the coverage
    tab says so instead of inventing lists."""
    embedded = current.get("coverage")
    # current.json materialises coverage as COUNTS (ints); the A-E lists come
    # from coverage_report(). Only trust an embedded dict that holds lists.
    if isinstance(embedded, dict) and embedded and all(isinstance(v, list) for v in embedded.values()):
        return embedded
    try:
        import research_graph as rg
        return rg.coverage_report(Path(control))
    except Exception as exc:
        return {"unavailable": "coverage report unavailable: %s" % exc}


def coerce_ro(value: str, project: str, default_space: str = "narr") -> str:
    """`ro::narr:C-041` / `narr:C-041` / `C-041` -> a full `ro:` address.  Same
    short forms `research_graph.coerce_ro` accepts, without importing it."""
    value = _s(value).strip()
    if not value:
        return ""
    if value.startswith("ro::"):
        return "ro:%s:%s" % (project, value[4:])
    if value.startswith("ro:"):
        return value
    head = value.split(":", 1)[0]
    if head in SPACES and ":" in value:
        return "ro:%s:%s" % (project, value)
    return "ro:%s:%s:%s" % (project, default_space, value)


def _pick_start(current: dict, coverage: dict, edges: list, project: str, node=None) -> str:
    if node:
        return coerce_ro(node, project)
    summary = current.get("summary") or {}
    for key in ("headline", "headline_ro", "root", "root_ro"):
        value = summary.get(key)
        if isinstance(value, str) and value:
            return coerce_ro(value, project)
    bearing = coverage.get("load_bearing") or []
    if bearing and project:
        return "ro:%s:narr:%s" % (project, bearing[0])
    incoming = {}
    for edge in edges:
        if edge["to"]:
            incoming[edge["to"]] = incoming.get(edge["to"], 0) + 1
    if incoming:
        return sorted(incoming.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return edges[0]["to"] if edges else ""


def build_payload(control: Path, view: str = "argument", node=None,
                  project: str = "") -> dict:
    control = Path(control)
    current = load_current(control)
    edges = [_norm_edge(row) for row in (current.get("edges") or [])
             if isinstance(row, dict)]
    edges.sort(key=lambda e: (e["to"], e["kind"], e["from"]))
    raw_objects = current.get("objects")
    # current.json carries `objects` as a COUNT SUMMARY (total/present/gone/...);
    # the address -> record map lives in graph/objects.json. Prefer the index,
    # fall back to an embedded map, ignore a summary.
    try:
        idx_path = Path(control) / "graph" / "objects.json"
        if idx_path.exists():
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            if isinstance(idx.get("objects"), dict) and idx["objects"]:
                raw_objects = idx["objects"]
    except Exception:
        pass
    if isinstance(raw_objects, dict) and raw_objects and not any(str(k).startswith("ro:") for k in raw_objects):
        raw_objects = {}
    objects = _norm_objects(raw_objects)
    _fill_endpoints(edges, objects)
    conflicts = [_norm_edge(row) for row in (current.get("conflicts") or [])
                 if isinstance(row, dict)]
    if not conflicts:
        conflicts = [e for e in edges if e["conflict"]]
    coverage = load_coverage(control, current)

    if not project:
        project = (_s(current.get("project")) or _s(coverage.get("project"))
                   or _s((current.get("summary") or {}).get("project")))
    if not project:
        state = ros.read_json_file(control / "state.json", {}) or {}
        project = _s(state.get("project_id")) or control.parent.name
    start = _pick_start(current, coverage, edges, project, node)
    gone = sum(1 for r in objects.values() if r["status"] == "gone")
    missing = sum(1 for r in objects.values() if r["status"] == "missing")
    return {
        "schema": VIEW_SCHEMA,
        "project": project,
        "view": view if view in VIEWS else "argument",
        "start": start,
        "graph": {"built_at": _s(current.get("built_at")),
                  "last_event_id": _s(current.get("last_event_id")),
                  "source": _s(current.get("source")),
                  "edges": edges, "conflicts": conflicts, "objects": objects,
                  "summary": current.get("summary") or {}},
        "coverage": coverage,
        "counts": {"edges": len(edges), "conflicts": len(conflicts),
                   "objects": len(objects), "gone": gone, "missing": missing},
    }


# ============================================================ page: appearance

PAGE_CSS = """
:root {
  color-scheme: light;
  --bg: #ffffff;
  --ink: #1b1d20;
  --ink-2: #5b6167;
  --ink-3: #8b9198;
  --line: #d9dde3;
  --line-hi: #b3bac2;
  --hover: rgba(16, 20, 26, .04);
  --chip: rgba(16, 20, 26, .06);
  --accent: #4a5866;
  --ok: #2f7d4f;
  --no: #b3352f;
  --wait: #9aa1a9;
  --indent: 22px;
  --row: 32px;
  --font: system-ui, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  --mono: ui-monospace, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #16181b;
    --ink: #e6e8ea;
    --ink-2: #a3a9b0;
    --ink-3: #767c84;
    --line: #3a4148;
    --line-hi: #59626c;
    --hover: rgba(255, 255, 255, .05);
    --chip: rgba(255, 255, 255, .07);
    --accent: #9fb0c0;
    --ok: #63b884;
    --no: #e0736c;
    --wait: #767c84;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #16181b;
  --ink: #e6e8ea;
  --ink-2: #a3a9b0;
  --ink-3: #767c84;
  --line: #3a4148;
  --line-hi: #59626c;
  --hover: rgba(255, 255, 255, .05);
  --chip: rgba(255, 255, 255, .07);
  --accent: #9fb0c0;
  --ok: #63b884;
  --no: #e0736c;
  --wait: #767c84;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: var(--font);
  font-size: 13px;
  line-height: 1.5;
  display: flex;
  flex-direction: column;
}
.mono { font-family: var(--mono); }
button, input, textarea, select {
  font: inherit;
  color: inherit;
  background: transparent;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 2px 8px;
}
button { cursor: pointer; color: var(--ink-2); }
button:hover { border-color: var(--line-hi); color: var(--ink); }
button[disabled] { opacity: .45; cursor: default; }
:focus-visible { outline: 1px solid var(--accent); outline-offset: 1px; }

/* ---------------- source bar: one line, 11px mono ---------------- */
.topbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 32px;
  padding: 0 12px;
  border-bottom: 1px solid var(--line);
  font-family: var(--mono);
  font-size: 11px;
  color: var(--ink-3);
  white-space: nowrap;
  overflow-x: auto;
}
.topbar b { color: var(--ink-2); font-weight: 600; }
.topbar .grow { flex: 1 1 auto; }
.topbar button { font-size: 11px; padding: 1px 7px; border-color: transparent; }
.topbar button:hover { border-color: var(--line); }
.topbar button.on { color: var(--ink); border-color: var(--line); }
.counts .warn { color: var(--no); }

/* ---------------- tabs: text, underlined, never boxes ---------------- */
.tabs {
  flex: 0 0 auto;
  display: flex;
  gap: 2px;
  padding: 0 12px;
  border-bottom: 1px solid var(--line);
}
.tabs button {
  border: 0;
  border-bottom: 1px solid transparent;
  border-radius: 0;
  padding: 6px 10px;
  font-size: 13px;
  color: var(--ink-3);
  margin-bottom: -1px;
}
.tabs button:hover { color: var(--ink); border-color: var(--line); }
.tabs button.on { color: var(--ink); border-bottom-color: var(--accent); }
.tabs .tabn { font-family: var(--mono); font-size: 11px; color: var(--ink-3); }

/* ---------------- layout ---------------- */
.main { flex: 1 1 auto; display: grid; grid-template-columns: 1fr 360px; min-height: 0; }
.stage { min-width: 0; overflow: auto; padding: 14px 16px 40px; }
.view[hidden] { display: none; }
.pagehead { margin: 0 0 14px; }
.pagehead .h { font-size: 15px; font-weight: 600; }
.pagehead .ns {
  font-size: 13px;
  color: var(--ink-3);
  margin-top: 2px;
  max-width: 96ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---------------- outline tree: guides, not boxes ---------------- */
.tree, .tree ul { list-style: none; margin: 0; padding: 0; }
.tree { margin-left: 11px; }
.tree li {
  position: relative;
  padding-left: var(--indent);
  border-left: 1px solid var(--line);
}
.tree li:last-child { border-color: transparent; }
.tree li::before {
  content: "";
  position: absolute;
  left: -1px;
  top: 0;
  width: calc(var(--indent) + 1px);
  height: calc(var(--row) / 2);
  border: solid var(--line);
  border-width: 0 0 1px 1px;
  border-bottom-left-radius: 5px;
}
.tree li:hover { border-color: var(--line-hi); }
.tree li:last-child:hover { border-color: transparent; }
.tree li:hover::before { border-color: var(--line-hi); }
.tree details { display: block; }
.tree summary { display: block; list-style: none; }
.tree summary::-webkit-details-marker { display: none; }
.tree summary::marker { content: ""; }

/* ---------------- one-line row ---------------- */
.row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: var(--row);
  padding: 6px 10px;
  border-radius: 6px;
  cursor: pointer;
}
.row:hover { background: var(--hover); }
.row.is-sel { background: var(--hover); box-shadow: inset 3px 0 0 var(--accent); }
.chev {
  flex: 0 0 16px;
  width: 16px;
  height: 16px;
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--ink-3);
  font-size: 10px;
  line-height: 16px;
  text-align: center;
  transition: transform 150ms ease;
}
details[open] > summary > .row > .chev { transform: rotate(90deg); }
.chev.leaf { visibility: hidden; }
.dot {
  flex: 0 0 6px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--wait);
  border: 1px solid transparent;
}
.dot.pol-plus { background: var(--ok); color: var(--ok); }
.dot.pol-minus { background: var(--no); color: var(--no); }
.dot.pol-none { background: var(--wait); color: var(--wait); }
.dot.pol-conflict { background: transparent; border-color: var(--no); color: var(--no); }
.dot.is-gone { opacity: .35; }
.rid {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--ink-3);
  flex: 0 0 auto;
  white-space: nowrap;
}
.rtitle {
  font-size: 13px;
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bang { color: var(--no); font-family: var(--mono); font-size: 11px; }
.chip {
  flex: 0 0 auto;
  height: 18px;
  line-height: 18px;
  border-radius: 16px;
  padding: 0 8px;
  background: var(--chip);
  color: var(--ink-2);
  font-size: 11px;
  max-width: 16ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.count { flex: 0 0 auto; font-size: 11px; color: var(--ink-3); font-family: var(--mono); }
.is-gone .rtitle, .is-gone .rid, tr.is-gone td { color: var(--ink-3); }
.mark { font-family: var(--mono); font-size: 11px; color: var(--ink-3); }

/* ---------------- section heads shared by coverage / conflicts / objects --- */
.sec { border-top: 1px solid var(--line); margin-top: 14px; padding-top: 9px; }
.sec:first-child { border-top: 0; margin-top: 0; }
.sec > h3 {
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: var(--ink-3);
  font-weight: 400;
  margin: 0 0 5px;
}
.sec > h3 .n { color: var(--ink-2); }
.empty { color: var(--ink-3); font-size: 13px; padding: 2px 10px; }

/* ---------------- tables: hairlines only ---------------- */
table.tbl { border-collapse: collapse; width: 100%; font-size: 13px; }
table.tbl th {
  text-align: left;
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 400;
  color: var(--ink-3);
  border-bottom: 1px solid var(--line);
  padding: 4px 8px;
}
table.tbl td {
  border-bottom: 1px solid var(--line);
  padding: 5px 8px;
  vertical-align: top;
  color: var(--ink-2);
}
table.tbl td.k { font-family: var(--mono); font-size: 11px; color: var(--ink-3); white-space: nowrap; }
table.tbl tr:hover td { background: var(--hover); }
table.tbl tr.is-sel td { box-shadow: inset 0 0 0 rgba(0, 0, 0, 0); }
table.tbl tr.is-sel td:first-child { box-shadow: inset 3px 0 0 var(--accent); }
.pol-plus-t { color: var(--ok); }
.pol-minus-t { color: var(--no); }

/* ---------------- conflict cards ---------------- */
.conflict { border-top: 1px solid var(--line); padding: 10px 0 12px; }
.conflict:first-of-type { border-top: 0; }
.conflict .ckey {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: var(--row);
}
.conflict .side { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.conflict .side > div { min-width: 0; }
.conflict .sidehead { font-family: var(--mono); font-size: 11px; color: var(--ink-3); margin-bottom: 3px; }
.adjudicate { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.adjudicate input[type="text"] { flex: 1 1 240px; min-width: 160px; }
.adjudicate label { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--ink-3); cursor: pointer; }

/* ---------------- objects filter ---------------- */
.filter { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.filter input { flex: 0 1 280px; }
.filter .hint { font-size: 11px; color: var(--ink-3); font-family: var(--mono); }

/* ---------------- inspector ---------------- */
.inspector {
  border-left: 1px solid var(--line);
  overflow: auto;
  position: sticky;
  top: 0;
  align-self: start;
  max-height: 100vh;
  padding: 12px 14px 40px;
}
.inspector h2 { font-size: 13px; font-weight: 600; margin: 0 0 2px; }
.inspector .hint { color: var(--ink-3); font-size: 13px; }
.insp-sec { border-top: 1px solid var(--line); margin-top: 12px; padding-top: 9px; }
.insp-sec > h3 {
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: var(--ink-3);
  font-weight: 400;
  margin: 0 0 5px;
}
.kv { font-size: 13px; color: var(--ink-2); word-break: break-word; }
.kv b { color: var(--ink-3); font-weight: 400; font-size: 11px; font-family: var(--mono); }
.list { margin: 0; padding-left: 15px; font-size: 13px; color: var(--ink-2); }
.list li { margin-bottom: 2px; }
.src { padding: 4px 0 5px; border-bottom: 1px solid var(--line); font-size: 13px; }
.src:last-child { border-bottom: 0; }
.src .meta { color: var(--ink-3); font-size: 11px; font-family: var(--mono); }
.row-btns { display: flex; gap: 5px; flex-wrap: wrap; margin: 7px 0; }
.row-btns button { font-size: 11px; }
.statusbar {
  flex: 0 0 auto;
  border-top: 1px solid var(--line);
  color: var(--ink-3);
  font-size: 11px;
  font-family: var(--mono);
  padding: 5px 12px;
  min-height: 24px;
  white-space: nowrap;
  overflow-x: auto;
}
.statusbar.warn { color: var(--ink); }
.raw { width: 100%; min-height: 160px; resize: vertical; }
@keyframes ar-in { from { opacity: 0; } to { opacity: 1; } }
details[open] > ul { animation: ar-in 150ms ease; }
@media (max-width: 900px) {
  .main { grid-template-columns: 1fr; }
  .inspector { border-left: 0; border-top: 1px solid var(--line); position: static; max-height: none; }
  .conflict .side { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; scroll-behavior: auto !important; }
}
"""


# ============================================================ page: behaviour

PAGE_JS = r"""
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("ar-graph").textContent);
  var G = DATA.graph || {};
  var EDGES = G.edges || [];
  var OBJECTS = G.objects || {};
  var CONFLICTS = G.conflicts || [];
  var COV = DATA.coverage || {};
  var DRAFT_SCHEMA = "auto-research/decision-draft-v1";
  var VIEWS = ["argument", "coverage", "conflicts", "objects"];
  var VIEW_CN = { argument: "论证", coverage: "覆盖", conflicts: "冲突", objects: "对象" };
  var KIND_CN = { supported_by: "supported_by", expressed_as: "expressed_as",
                  depends_on: "depends_on", evolved_from: "evolved_from" };
  var MAX_DEPTH = 4;
  var LS_KEY = "argraph:" + DATA.project;

  // ---------------------------------------------------------------- indexes
  var IN = {}, OUT = {};
  EDGES.forEach(function (e) {
    (IN[e.to] = IN[e.to] || []).push(e);
    (OUT[e.from] = OUT[e.from] || []).push(e);
  });

  var S = {
    tab: VIEWS.indexOf(DATA.view) >= 0 ? DATA.view : "argument",
    start: DATA.start || "",
    sel: null,                      // a `ro:` address
    selEdge: null,                  // an edge key, when the selection came from a row
    open: lsGet(LS_KEY + ":open", {}),
    q: "",
    draft: {}                       // conflict key -> { polarity, rationale }
  };

  // ---------------------------------------------------------------- helpers
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function lsGet(key, fallback) {
    try {
      var raw = window.localStorage.getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (e) { return fallback; }
  }
  function lsSet(key, value) {
    try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ }
  }
  function nowIso() { return new Date().toISOString().replace(/\.\d{3}Z$/, "Z"); }
  function keyOf(e) { return e.from + "|" + e.to + "|" + e.kind; }
  function objOf(ro) { return OBJECTS[ro] || null; }
  function shortRo(ro) {
    var p = String(ro || "").split(":");
    return p.length >= 4 && p[0] === "ro" ? p[2] + ":" + p.slice(3).join(":") : String(ro || "?");
  }
  function titleOf(ro) {
    var o = objOf(ro);
    return (o && o.title) || "";
  }
  function markOf(ro) {
    var o = objOf(ro);
    if (!o) return " [MISSING]";
    if (o.status === "gone") return " [GONE]";
    if (o.status === "missing") return " [MISSING]";
    return "";
  }
  function isGone(ro) {
    var o = objOf(ro);
    return !o || o.status === "gone" || o.status === "missing";
  }
  function polClass(e) {
    if (e.conflict) return "pol-conflict";
    if (e.polarity === "+") return "pol-plus";
    if (e.polarity === "-") return "pol-minus";
    return "pol-none";
  }
  function status(msg, warn) {
    var bar = document.getElementById("statusbar");
    bar.textContent = msg;
    bar.className = "statusbar" + (warn ? " warn" : "");
  }
  function roAttr(ro) { return ' data-ro="' + esc(ro) + '"'; }

  // ---------------------------------------------------------------- rows
  // One shared row grammar for every view: chevron, 6px semantic dot, mono id,
  // title, neutral chip.  Nothing else ever carries colour.
  function rowHtml(opts) {
    var cls = "row" + (opts.selected ? " is-sel" : "") + (opts.gone ? " is-gone" : "");
    var chev = opts.hasKids ? "&#9654;" : "";
    var out = ['<div class="' + cls + '" tabindex="0" role="button"' +
               (opts.ro ? roAttr(opts.ro) : "") +
               (opts.edge ? ' data-edge="' + esc(opts.edge) + '"' : "") +
               ' title="' + esc(opts.hint || "") + '">'];
    out.push('<span class="chev' + (opts.hasKids ? "" : " leaf") + '">' + chev + "</span>");
    out.push('<span class="dot ' + esc(opts.dot || "pol-none") +
             (opts.gone ? " is-gone" : "") + '"></span>');
    if (opts.bang) out.push('<span class="bang">(!)</span>');
    out.push('<span class="rid">' + esc(opts.id) + "</span>");
    out.push('<span class="rtitle">' + esc(opts.title || "(无标题)") +
             '<span class="mark">' + esc(opts.mark || "") + "</span></span>");
    if (opts.chip) out.push('<span class="chip">' + esc(opts.chip) + "</span>");
    if (opts.count) out.push('<span class="count">' + esc(opts.count) + "</span>");
    out.push("</div>");
    return out.join("");
  }

  // ---------------------------------------------------------------- argument
  // The chain is walked over LOGICAL edges: incoming evidence (evidence -> claim)
  // becomes the children of a claim, and the claim's own depends_on edges hang
  // underneath as the run / data it needs.
  // The chain has a direction and keeps it.  Arriving at an object along an
  // incoming edge (it is evidence) we keep asking "and what supports THAT, and
  // what data does it need"; arriving along an outgoing edge (it is data) we
  // only go further down -- never back up into every other claim that happens
  // to need the same dataset, which would print the whole graph under one file.
  var KIND_ORDER = { supported_by: 0, expressed_as: 1, evolved_from: 2, depends_on: 3 };

  function childEdges(ro, dir, seen, viaKey) {
    var kids = [];
    if (dir !== "down") {
      (IN[ro] || []).forEach(function (e) { kids.push(e); });
    }
    (OUT[ro] || []).forEach(function (e) {
      if (e.kind === "depends_on" || e.kind === "evolved_from") kids.push(e);
    });
    kids = kids.filter(function (e) {
      if (keyOf(e) === viaKey) return false;
      var other = e.to === ro ? e.from : e.to;
      return other !== ro && !seen[other];
    });
    kids.sort(function (a, b) {
      var ka = KIND_ORDER[a.kind], kb = KIND_ORDER[b.kind];
      if (ka === undefined) ka = 9;
      if (kb === undefined) kb = 9;
      if (ka !== kb) return ka - kb;
      var pa = a.polarity || "~", pb = b.polarity || "~";
      if (pa !== pb) return pa < pb ? -1 : 1;
      var oa = a.to === ro ? a.from : a.to, ob = b.to === ro ? b.from : b.to;
      return oa < ob ? -1 : (oa > ob ? 1 : 0);
    });
    return kids;
  }

  function openKey(path) { return path; }
  function isOpen(path, level) {
    var v = S.open[openKey(path)];
    return v === undefined ? level < 3 : !!v;
  }

  function argNode(ro, level, path, seen, viaEdge, dir) {
    var other = ro;
    var nextSeen = {};
    Object.keys(seen).forEach(function (k) { nextSeen[k] = true; });
    nextSeen[other] = true;
    var edgeKey = viaEdge ? keyOf(viaEdge) : "";
    var kids = level >= MAX_DEPTH ? [] : childEdges(other, dir, nextSeen, edgeKey);
    var open = kids.length ? isOpen(path, level) : false;
    var head = rowHtml({
      ro: other,
      edge: edgeKey,
      id: shortRo(other),
      title: titleOf(other),
      mark: markOf(other),
      gone: isGone(other),
      dot: viaEdge ? polClass(viaEdge) : "pol-none",
      bang: viaEdge ? !!viaEdge.conflict : false,
      chip: viaEdge ? (KIND_CN[viaEdge.kind] || viaEdge.kind) : "headline",
      count: viaEdge && viaEdge.sources.length > 1 ? "×" + viaEdge.sources.length : "",
      hasKids: kids.length > 0,
      selected: S.sel === other && (!S.selEdge || S.selEdge === edgeKey),
      hint: viaEdge ? (viaEdge.kind + "  " + (viaEdge.polarity || "~")) : ""
    });
    if (!kids.length) return "<li>" + head + "</li>";
    var inner = kids.map(function (e) {
      var up = e.to === other;                 // incoming: the child is evidence
      var target = up ? e.from : e.to;
      return argNode(target, level + 1, path + "/" + keyOf(e), nextSeen, e,
                     up ? "up" : "down");
    }).join("");
    return "<li><details" + (open ? " open" : "") + ' data-path="' + esc(path) + '">' +
           "<summary tabindex=\"-1\">" + head + "</summary><ul>" + inner + "</ul></details></li>";
  }

  function renderArgument() {
    var host = document.getElementById("view-argument");
    if (!EDGES.length) {
      host.innerHTML = '<div class="pagehead"><div class="h">论证链</div>' +
        '<div class="ns">图里还没有边 —— 先跑 `graph derive`。</div></div>';
      return;
    }
    var start = S.start && (objOf(S.start) || IN[S.start] || OUT[S.start]) ? S.start : DATA.start;
    if (!start) { host.innerHTML = '<div class="empty">没有可作为起点的对象。</div>'; return; }
    var html = ['<div class="pagehead"><div class="h">论证链 · ' + esc(shortRo(start)) + "</div>",
                '<div class="ns">' + esc(titleOf(start) || "(无标题)") +
                esc(markOf(start)) + "</div></div>"];
    html.push('<ul class="tree">' + argNode(start, 1, "root", {}, null, null) + "</ul>");
    html.push('<div class="row-btns"><button type="button" id="arg-expand">全展开</button>' +
              '<button type="button" id="arg-collapse">全收起</button></div>');
    host.innerHTML = html.join("");
    document.getElementById("arg-expand").addEventListener("click", function () { setAll(true); });
    document.getElementById("arg-collapse").addEventListener("click", function () { setAll(false); });
  }

  function setAll(open) {
    var nodes = document.querySelectorAll("#view-argument details[data-path]");
    Array.prototype.forEach.call(nodes, function (d) {
      S.open[d.getAttribute("data-path")] = open;
    });
    lsSet(LS_KEY + ":open", S.open);
    renderArgument();
  }

  // ---------------------------------------------------------------- coverage
  function covRo(space, id) { return "ro:" + DATA.project + ":" + space + ":" + id; }

  function covSection(letter, title, rows) {
    var out = ['<div class="sec"><h3>' + esc(letter + ". " + title) +
               ' <span class="n">(' + rows.length + ")</span></h3>"];
    if (!rows.length) { out.push('<div class="empty">(none)</div></div>'); return out.join(""); }
    out.push('<ul class="tree">');
    rows.forEach(function (r) {
      out.push("<li>" + rowHtml({
        ro: r.ro, id: r.id, title: r.title, mark: markOf(r.ro), gone: isGone(r.ro),
        dot: r.dot || "pol-none", bang: !!r.bang, chip: r.chip || "", count: r.count || "",
        selected: S.sel === r.ro, hasKids: false, hint: r.hint || "跳到论证链"
      }) + "</li>");
    });
    out.push("</ul></div>");
    return out.join("");
  }

  function renderCoverage() {
    var host = document.getElementById("view-coverage");
    if (COV.unavailable) {
      host.innerHTML = '<div class="pagehead"><div class="h">覆盖</div><div class="ns">' +
        esc(COV.unavailable) + "</div></div>";
      return;
    }
    var html = ['<div class="pagehead"><div class="h">覆盖 A–E</div><div class="ns">' +
                esc((COV.load_bearing || []).length + " 条承重主张 · " +
                    (COV.figures || []).length + " 张图 · " + EDGES.length +
                    " 条逻辑边 —— 每一项可点，跳到论证链定位") + "</div></div>"];
    html.push(covSection("A", "承重主张没有图表达", (COV.no_figure || []).map(function (r) {
      return { ro: covRo("narr", r.id), id: r.id, title: r.title, chip: "no figure" };
    })));
    html.push(covSection("B", "图不表达任何主张", (COV.mute_figures || []).map(function (f) {
      return { ro: covRo("fig", f), id: f, title: titleOf(covRo("fig", f)), chip: "mute" };
    })));
    html.push(covSection("C", "HELD 但没有 supported_by(+)",
                         (COV.held_unsupported || []).map(function (r) {
      return { ro: covRo("narr", r.id), id: r.id, title: r.title, chip: "HELD",
               dot: "pol-none" };
    })));
    html.push(covSection("D", "带 (−) 边却仍然 ACTIVE",
                         (COV.contested_active || []).map(function (r) {
      return { ro: covRo("narr", r.id), id: r.id, title: r.title,
               chip: r.epistemic || "ACTIVE", dot: "pol-minus",
               count: (r.against || []).join(", ") };
    })));
    html.push(covSection("E", "EDGE_CONFLICT —— 来源对符号不一致",
                         CONFLICTS.map(function (e) {
      return { ro: e.to, id: shortRo(e.from) + " → " + shortRo(e.to), title: titleOf(e.to),
               chip: e.kind, dot: "pol-conflict", bang: true,
               count: "×" + e.sources.length, hint: "跳到论证链定位" };
    })));
    host.innerHTML = html.join("");
  }

  // ---------------------------------------------------------------- conflicts
  function sourceTable(sources) {
    var out = ['<table class="tbl"><thead><tr><th>来源</th><th>极性</th><th>依据</th>' +
               "<th>时间</th></tr></thead><tbody>"];
    sources.forEach(function (s) {
      var pc = s.polarity === "+" ? "pol-plus-t" : (s.polarity === "-" ? "pol-minus-t" : "");
      out.push("<tr><td class=\"k\">" + esc(s.by || "main") + "</td>" +
               '<td class="k ' + pc + '">' + esc(s.polarity || "~") + "</td>" +
               "<td>" + esc(s.basis || "—") + "</td>" +
               '<td class="k">' + esc((s.at || "").slice(0, 16) || "—") + "</td></tr>");
    });
    out.push("</tbody></table>");
    return out.join("");
  }

  function renderConflicts() {
    var host = document.getElementById("view-conflicts");
    var html = ['<div class="pagehead"><div class="h">冲突裁决</div><div class="ns">' +
                esc(CONFLICTS.length + " 处 EDGE_CONFLICT —— 这一页只导出裁决草案，永不写状态") +
                "</div></div>"];
    if (!CONFLICTS.length) {
      html.push('<div class="empty">没有冲突：所有逻辑边的来源在符号上一致。</div>');
      host.innerHTML = html.join("");
      return;
    }
    CONFLICTS.forEach(function (e) {
      var k = keyOf(e);
      var plus = e.sources.filter(function (s) { return s.polarity === "+"; });
      var minus = e.sources.filter(function (s) { return s.polarity === "-"; });
      var other = e.sources.filter(function (s) { return !s.polarity; });
      var d = S.draft[k] || {};
      html.push('<div class="conflict" data-ckey="' + esc(k) + '">');
      html.push('<div class="ckey">' + rowHtml({
        ro: e.to, edge: k, id: shortRo(e.from) + " → " + shortRo(e.to),
        title: titleOf(e.to), mark: markOf(e.to), gone: isGone(e.to),
        dot: "pol-conflict", bang: true, chip: e.kind,
        count: "×" + e.sources.length, selected: S.sel === e.to, hasKids: false,
        hint: "选中后右栏看两端的全部边"
      }) + "</div>");
      html.push('<div class="side"><div><div class="sidehead">支持 (+) · ' + plus.length +
                "</div>" + (plus.length ? sourceTable(plus) :
                            '<div class="empty">(none)</div>') + "</div>");
      html.push('<div><div class="sidehead">反驳 (−) · ' + minus.length + "</div>" +
                (minus.length ? sourceTable(minus) : '<div class="empty">(none)</div>') +
                "</div></div>");
      if (other.length) {
        html.push('<div class="sidehead" style="margin-top:8px">未署符号 · ' + other.length +
                  "</div>" + sourceTable(other));
      }
      html.push('<div class="adjudicate">' +
        '<label><input type="radio" name="pol-' + esc(k) + '" value="+"' +
        (d.polarity === "+" ? " checked" : "") + ">裁定为 (+)</label>" +
        '<label><input type="radio" name="pol-' + esc(k) + '" value="-"' +
        (d.polarity === "-" ? " checked" : "") + ">裁定为 (−)</label>" +
        '<input type="text" data-rationale="' + esc(k) + '" placeholder="裁决理由（进草案）"' +
        ' value="' + esc(d.rationale || "") + '">' +
        '<button type="button" data-adj="' + esc(k) + '">提议裁决</button></div>');
      html.push("</div>");
    });
    host.innerHTML = html.join("");
    Array.prototype.forEach.call(host.querySelectorAll('input[type="radio"]'), function (r) {
      r.addEventListener("change", function () {
        var k = r.name.slice(4);
        S.draft[k] = S.draft[k] || {};
        S.draft[k].polarity = r.value;
      });
    });
    Array.prototype.forEach.call(host.querySelectorAll("[data-rationale]"), function (t) {
      t.addEventListener("input", function () {
        var k = t.getAttribute("data-rationale");
        S.draft[k] = S.draft[k] || {};
        S.draft[k].rationale = t.value;
      });
    });
    Array.prototype.forEach.call(host.querySelectorAll("[data-adj]"), function (b) {
      b.addEventListener("click", function () { proposeDecision(b.getAttribute("data-adj")); });
    });
  }

  function conflictByKey(k) {
    for (var i = 0; i < CONFLICTS.length; i++) {
      if (keyOf(CONFLICTS[i]) === k) return CONFLICTS[i];
    }
    return null;
  }

  function buildDraft(k) {
    var e = conflictByKey(k);
    if (!e) return null;
    var d = S.draft[k] || {};
    return {
      schema: DRAFT_SCHEMA,
      logical_key: { from: e.from, to: e.to, kind: e.kind },
      chosen_polarity: d.polarity || null,
      rationale: d.rationale || "",
      drafted_at: nowIso()
    };
  }

  function proposeDecision(k) {
    var draft = buildDraft(k);
    if (!draft) { status("找不到这条冲突", true); return; }
    if (!draft.chosen_polarity) { status("先选一个裁定符号 (+ 或 −)", true); return; }
    if (!draft.rationale) { status("裁决草案必须带理由：先写一句依据", true); return; }
    var name = "decision-draft-" + shortRo(draft.logical_key.to).replace(/[^\w.-]+/g, "-") +
               "-" + draft.logical_key.kind + ".json";
    exportJson(name, draft).then(function (how) {
      status("裁决草案（" + draft.chosen_polarity + "）" + (HOW[how] || how) +
             "；由人经 CLI 摄入，本页不写状态");
    });
  }

  // ---------------------------------------------------------------- objects
  function renderObjects() {
    var host = document.getElementById("view-objects");
    var q = S.q.toLowerCase();
    var bySpace = {};
    Object.keys(OBJECTS).sort().forEach(function (ro) {
      var o = OBJECTS[ro];
      var hay = (ro + " " + o.id + " " + o.title + " " + o.status).toLowerCase();
      if (q && hay.indexOf(q) < 0) return;
      (bySpace[o.space] = bySpace[o.space] || []).push({ ro: ro, o: o });
    });
    var shown = 0;
    Object.keys(bySpace).forEach(function (s) { shown += bySpace[s].length; });
    var html = ['<div class="pagehead"><div class="h">对象索引</div><div class="ns">' +
                esc(Object.keys(OBJECTS).length + " 个对象 · " + DATA.counts.gone +
                    " 个 GONE · " + DATA.counts.missing + " 个 MISSING") + "</div></div>"];
    html.push('<div class="filter"><input type="search" id="objq" placeholder="过滤 id / 标题 / 空间"' +
              ' value="' + esc(S.q) + '"><span class="hint">' + shown + "/" +
              Object.keys(OBJECTS).length + "</span></div>");
    var order = ["narr", "claim", "tree", "run", "fig", "asset", "ledger", "decision"];
    var spaces = Object.keys(bySpace).sort(function (a, b) {
      var ia = order.indexOf(a), ib = order.indexOf(b);
      if (ia < 0) ia = 99;
      if (ib < 0) ib = 99;
      return ia - ib || (a < b ? -1 : 1);
    });
    if (!spaces.length) html.push('<div class="empty">没有匹配的对象。</div>');
    spaces.forEach(function (space) {
      html.push('<div class="sec"><h3>' + esc(space) + ' <span class="n">(' +
                bySpace[space].length + ")</span></h3>");
      html.push('<table class="tbl"><thead><tr><th>id</th><th>title</th><th>status</th>' +
                "<th>边</th></tr></thead><tbody>");
      bySpace[space].forEach(function (r) {
        var gone = r.o.status === "gone" || r.o.status === "missing";
        var n = (IN[r.ro] || []).length + (OUT[r.ro] || []).length;
        var trcls = (gone ? "is-gone " : "") + (S.sel === r.ro ? "is-sel" : "");
        html.push("<tr" + (trcls.trim() ? ' class="' + trcls.trim() + '"' : "") +
                  roAttr(r.ro) + ">" +
                  '<td class="k">' + esc(r.o.id) + "</td><td>" +
                  esc(r.o.title || "(无标题)") + "</td>" +
                  '<td class="k">' + esc(r.o.status === "gone" ? "GONE" :
                                         (r.o.status === "missing" ? "MISSING" : r.o.status)) +
                  '</td><td class="k">' + n + "</td></tr>");
      });
      html.push("</tbody></table></div>");
    });
    host.innerHTML = html.join("");
    var box = document.getElementById("objq");
    if (box) {
      box.addEventListener("input", function () {
        S.q = box.value;
        renderObjects();
        var again = document.getElementById("objq");
        if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
      });
    }
  }

  // ---------------------------------------------------------------- inspector
  function edgeLine(e, otherKey) {
    var other = e[otherKey];
    var sign = e.conflict ? "(!)" : ({ "+": "(+)", "-": "(-)" }[e.polarity] || "( )");
    return "<li>" + esc(sign + " " + e.kind + "  ") +
           '<span class="mono">' + esc(shortRo(other)) + "</span> " +
           esc((titleOf(other) || "").slice(0, 40) + markOf(other)) + "</li>";
  }

  function renderInspector() {
    var box = document.getElementById("inspector");
    if (!S.sel) {
      box.innerHTML = '<h2>检视栏</h2><div class="hint">选中任意一行，这里显示该对象的出入边、' +
        "每条边的来源（by / basis / at）与 [GONE] / [MISSING] 标记。</div>";
      return;
    }
    var ro = S.sel;
    var o = objOf(ro) || { space: "?", id: shortRo(ro), title: "", status: "missing" };
    var ins = IN[ro] || [], outs = OUT[ro] || [];
    var html = ["<h2>" + esc(o.title || shortRo(ro)) + "</h2>"];
    html.push('<div class="kv mono">' + esc(shortRo(ro)) + esc(markOf(ro)) + "</div>");
    html.push('<div class="insp-sec"><h3>身份</h3>' +
      '<div class="kv"><b>ro</b> ' + esc(ro) + "</div>" +
      '<div class="kv"><b>space</b> ' + esc(o.space) + "</div>" +
      '<div class="kv"><b>id</b> ' + esc(o.id) + "</div>" +
      '<div class="kv"><b>status</b> ' + esc(o.status) + "</div></div>");
    html.push('<div class="insp-sec"><h3>in (' + ins.length + ")</h3>" +
      (ins.length ? '<ul class="list">' + ins.map(function (e) { return edgeLine(e, "from"); }).join("") + "</ul>"
                  : '<div class="hint">(none)</div>') + "</div>");
    html.push('<div class="insp-sec"><h3>out (' + outs.length + ")</h3>" +
      (outs.length ? '<ul class="list">' + outs.map(function (e) { return edgeLine(e, "to"); }).join("") + "</ul>"
                   : '<div class="hint">(none)</div>') + "</div>");
    var focus = null;
    if (S.selEdge) {
      ins.concat(outs).forEach(function (e) { if (keyOf(e) === S.selEdge) focus = e; });
    }
    var srcEdges = focus ? [focus] : ins.concat(outs);
    var rows = [];
    srcEdges.forEach(function (e) {
      e.sources.forEach(function (s) {
        rows.push('<div class="src"><div>' +
          esc((s.polarity || "~") + "  " + e.kind + "  " + shortRo(e.from) + " → " + shortRo(e.to)) +
          '</div><div class="meta">' + esc((s.by || "main") + " · " + (s.basis || "—") +
          " · " + ((s.at || "").slice(0, 16) || "—") +
          (s.edge_id ? " · " + s.edge_id : "")) + "</div></div>");
      });
    });
    html.push('<div class="insp-sec"><h3>来源 (' + rows.length + ")</h3>" +
              (rows.length ? rows.slice(0, 24).join("") : '<div class="hint">(none)</div>') +
              (rows.length > 24 ? '<div class="hint">… +' + (rows.length - 24) + "</div>" : "") +
              "</div>");
    html.push('<div class="insp-sec"><div class="row-btns">' +
      '<button type="button" id="insp-arg">在论证链里展开</button>' +
      "</div></div>");
    box.innerHTML = html.join("");
    var btn = document.getElementById("insp-arg");
    if (btn) {
      btn.addEventListener("click", function () {
        S.start = ro;
        setTab("argument");
        status("论证链起点 → " + shortRo(ro));
      });
    }
  }

  // ---------------------------------------------------------------- export
  function capability(name) {
    if (typeof window.claude === "undefined" || !window.claude ||
        typeof window.claude.use !== "function") {
      return Promise.resolve(null);
    }
    try {
      return Promise.resolve(window.claude.use(name)).then(function (c) { return c || null; },
                                                           function () { return null; });
    } catch (e) { return Promise.resolve(null); }
  }

  function showRaw(text) {
    var box = document.getElementById("inspector");
    var pre = document.createElement("div");
    pre.className = "insp-sec";
    pre.innerHTML = "<h3>手动复制</h3>";
    var ta = document.createElement("textarea");
    ta.className = "raw mono";
    ta.value = text;
    pre.appendChild(ta);
    box.insertBefore(pre, box.firstChild);
    ta.focus();
    ta.select();
  }

  function exportJson(filename, payload) {
    var text = JSON.stringify(payload, null, 2);
    return capability("downloads").then(function (dl) {
      if (dl && typeof dl.save === "function") {
        return dl.save({ filename: filename, data: text }).then(function () {
          return "downloads";
        }, function (err) {
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

  var HOW = { downloads: "已交给查看者保存", download: "已触发下载",
              clipboard: "已复制到剪贴板", manual: "请从上方文本框手动复制",
              declined: "查看者取消了保存" };

  // ---------------------------------------------------------------- shell
  function renderTop() {
    var c = DATA.counts;
    var src = document.getElementById("srcline");
    src.innerHTML = "<b>" + esc(DATA.project) + "</b>  last_event " +
      esc(DATA.graph.last_event_id || "—") + "  built " +
      esc((DATA.graph.built_at || "—").slice(0, 16));
    var counts = document.getElementById("counts");
    counts.innerHTML = "edges " + c.edges + " · conflicts " +
      (c.conflicts ? '<span class="warn">' + c.conflicts + "</span>" : "0") +
      " · gone " + (c.gone + c.missing);
  }

  function setTab(name) {
    S.tab = VIEWS.indexOf(name) >= 0 ? name : "argument";
    VIEWS.forEach(function (v) {
      document.getElementById("view-" + v).hidden = v !== S.tab;
      var b = document.querySelector('[data-tab="' + v + '"]');
      if (b) b.className = v === S.tab ? "on" : "";
    });
    lsSet(LS_KEY + ":tab", S.tab);
    render();
  }

  function render() {
    if (S.tab === "argument") renderArgument();
    else if (S.tab === "coverage") renderCoverage();
    else if (S.tab === "conflicts") renderConflicts();
    else renderObjects();
    renderInspector();
  }

  function select(ro, edgeKey, jump) {
    S.sel = ro || null;
    S.selEdge = edgeKey || null;
    if (jump) { S.start = ro; setTab("argument"); return; }
    render();
  }

  function applyTheme(mode) {
    if (mode === "light" || mode === "dark") document.documentElement.setAttribute("data-theme", mode);
    else document.documentElement.removeAttribute("data-theme");
    lsSet(LS_KEY + ":theme", mode);
    Array.prototype.forEach.call(document.querySelectorAll("[data-theme-btn]"), function (b) {
      b.className = b.getAttribute("data-theme-btn") === mode ? "on" : "";
    });
  }

  // one delegated handler for every view: rows, table rows, fold chevrons
  document.addEventListener("click", function (ev) {
    var chev = ev.target.closest ? ev.target.closest(".chev") : null;
    if (chev && !chev.classList.contains("leaf")) {
      var det = chev.closest("details");
      if (det) {
        ev.preventDefault();
        var path = det.getAttribute("data-path");
        var next = !det.open;
        det.open = next;
        if (path) { S.open[path] = next; lsSet(LS_KEY + ":open", S.open); }
        return;
      }
    }
    var host = ev.target.closest ? ev.target.closest("[data-ro]") : null;
    if (!host) return;
    var ro = host.getAttribute("data-ro");
    var edge = host.getAttribute("data-edge");
    var jump = S.tab === "coverage";
    select(ro, edge, jump);
  });

  document.addEventListener("keydown", function (ev) {
    var host = ev.target && ev.target.classList && ev.target.classList.contains("row")
      ? ev.target : null;
    if (!host) return;
    var det = host.closest ? host.closest("details") : null;
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      host.click();
    } else if (ev.key === "ArrowRight" && det && !det.open) {
      ev.preventDefault();
      det.open = true;
      var p = det.getAttribute("data-path");
      if (p) { S.open[p] = true; lsSet(LS_KEY + ":open", S.open); }
    } else if (ev.key === "ArrowLeft" && det && det.open) {
      ev.preventDefault();
      det.open = false;
      var q = det.getAttribute("data-path");
      if (q) { S.open[q] = false; lsSet(LS_KEY + ":open", S.open); }
    }
  });

  VIEWS.forEach(function (v) {
    var b = document.querySelector('[data-tab="' + v + '"]');
    if (b) b.addEventListener("click", function () { setTab(v); });
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-theme-btn]"), function (b) {
    b.addEventListener("click", function () { applyTheme(b.getAttribute("data-theme-btn")); });
  });

  applyTheme(lsGet(LS_KEY + ":theme", "system"));
  renderTop();
  setTab(S.tab);
  status("只读投影：本页从 graph/current.json 渲染，任何编辑都只导出草案，永不写状态" +
         (DATA.graph.source && DATA.graph.source.indexOf("live:") === 0
          ? "（current.json 尚未物化，本次为实时投影）" : ""));
})();
"""


SHELL_BODY = """<div class="topbar">
  <span id="srcline"></span>
  <span class="counts" id="counts"></span>
  <span class="grow"></span>
  <button type="button" data-theme-btn="system">系统</button><button type="button" data-theme-btn="light">亮</button><button type="button" data-theme-btn="dark">暗</button>
</div>
<div class="tabs">
  <button type="button" data-tab="argument">论证</button><button type="button" data-tab="coverage">覆盖</button><button type="button" data-tab="conflicts">冲突</button><button type="button" data-tab="objects">对象</button>
</div>
<div class="main">
  <div class="stage" id="stage">
    <noscript>这一页需要 JavaScript；文字版请用 `graph view argument|coverage|conflicts|objects`。</noscript>
    <section class="view" id="view-argument" aria-label="论证链"></section>
    <section class="view" id="view-coverage" aria-label="覆盖 A–E" hidden></section>
    <section class="view" id="view-conflicts" aria-label="冲突裁决" hidden></section>
    <section class="view" id="view-objects" aria-label="对象索引" hidden></section>
  </div>
  <aside class="inspector" id="inspector" aria-label="检视栏"></aside>
</div>
<div class="statusbar" id="statusbar"></div>
"""


def _escape_json_for_script(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/")


def render_html(payload: dict) -> str:
    """The whole page: one complete document, no external reference of any kind."""
    title = (payload.get("project") or "graph") + " · 研究图"
    safe_title = title.replace("<", "").replace(">", "").replace("&", "")
    parts = [
        "<!doctype html>",
        '<html lang="zh"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>" + safe_title + "</title>",
        '<style id="ar-css">' + PAGE_CSS + "</style>",
        "</head>",
        "<body>",
        SHELL_BODY.rstrip("\n"),
        '<script type="application/json" id="ar-graph">'
        + _escape_json_for_script(payload) + "</script>",
        '<script id="ar-app">' + PAGE_JS + "</script>",
        "</body></html>",
        "",
    ]
    return "\n".join(parts)


def render(control, view: str = "argument", out_path=None, node=None,
           project: str = ""):
    """Public API -- `research_graph.py render --html` forwards here.

    With `out_path`: writes the page and returns a receipt dict.
    Without it: returns the page as a string (the caller prints it)."""
    control = Path(control)
    if view and view not in VIEWS:
        raise ValueError("unknown view %r -- one of %s" % (view, ", ".join(VIEWS)))
    payload = build_payload(control, view=view or "argument", node=node, project=project)
    html = render_html(payload)
    if not out_path:
        return html
    out = Path(out_path)
    ros.atomic_write(out, html)
    receipt = {"ok": True, "view": payload["view"], "project": payload["project"],
               "start": payload["start"],
               "last_event_id": payload["graph"]["last_event_id"],
               "built_at": payload["graph"]["built_at"],
               "source": payload["graph"]["source"] or CURRENT_REL,
               "bytes": len(html.encode("utf-8")), "out": str(out)}
    receipt.update(payload["counts"])
    return receipt


# ============================================================ self-test

def _fixture_current(project: str = "graph-ui-selftest") -> dict:
    """13 objects (one GONE), 10 logical edges (one EDGE_CONFLICT), and the A-E
    coverage lists -- every badge, marker and table the page can draw."""
    def ro(space, ident):
        return "ro:%s:%s:%s" % (project, space, ident)

    def src(edge_id, by, basis, at, polarity=None):
        return {"edge_id": edge_id, "by": by, "basis": basis, "at": at,
                "polarity": polarity}

    objects = {
        ro("narr", "R-0"): {"space": "narr", "id": "R-0", "title": "附录是有代价的",
                            "status": "present"},
        ro("narr", "C-001"): {"space": "narr", "id": "C-001",
                              "title": "附录引用率在各会议都偏低", "status": "present"},
        ro("narr", "C-002"): {"space": "narr", "id": "C-002",
                              "title": "结论在期刊样本里同样成立", "status": "present"},
        ro("narr", "C-003"): {"space": "narr", "id": "C-003",
                              "title": "阅读量取决于审稿流程", "status": "present"},
        ro("claim", "T-1"): {"space": "claim", "id": "T-1",
                             "title": "命题 A：阅读深度刻画", "status": "present"},
        ro("run", "R-01"): {"space": "run", "id": "R-01",
                            "title": "基线样本 20260812", "status": "present"},
        ro("run", "R-02"): {"space": "run", "id": "R-02",
                            "title": "期刊复现 20260818", "status": "present"},
        ro("tree", "R01.A01"): {"space": "tree", "id": "R01.A01",
                                "title": "尝试：置换检验", "status": "present"},
        ro("fig", "fig-price"): {"space": "fig", "id": "fig-price",
                                 "title": "图 2 附录的代价", "status": "present"},
        ro("fig", "fig-history"): {"space": "fig", "id": "fig-history",
                                   "title": "图 3 历史面板（已删）", "status": "gone"},
        ro("fig", "fig-lead"): {"space": "fig", "id": "fig-lead",
                                "title": "图 1 引子（不表达主张）", "status": "present"},
        ro("asset", "A-reviews"): {"space": "asset", "id": "A-reviews",
                                "title": "OpenReview 评审数据", "status": "present"},
        ro("ledger", "E-09"): {"space": "ledger", "id": "E-09",
                               "title": "证据条目 E-09（Chen 2025）", "status": "present"},
    }

    edges = [
        {"logical_key": {"from": ro("run", "R-01"), "to": ro("narr", "C-001"),
                         "kind": "supported_by"}, "polarity": "+", "conflict": False,
         "sources": [src("e1a", "experiment-runner", "run:R-01 outcome=positive",
                         "2026-08-12T09:10:00Z", "+"),
                     src("e1b", "derive", "derive:run-claim", "2026-08-12T09:11:00Z", "+")],
         "from_title": "基线样本 20260812", "to_title": "附录引用率在各会议都偏低"},
        {"logical_key": {"from": ro("claim", "T-1"), "to": ro("narr", "C-001"),
                         "kind": "supported_by"}, "polarity": "+", "conflict": False,
         "sources": [src("e2", "theory-operator", "claim:T-1 verdict=proved",
                         "2026-08-14T11:00:00Z", "+")],
         "from_title": "命题 A：阅读深度刻画", "to_title": "附录引用率在各会议都偏低"},
        {"logical_key": {"from": ro("fig", "fig-price"), "to": ro("narr", "C-001"),
                         "kind": "expressed_as"}, "polarity": None, "conflict": False,
         "sources": [src("e3", "figure-engineer", "fsg:fig-price expresses C-001",
                         "2026-08-15T08:00:00Z")],
         "from_title": "图 2 附录的代价", "to_title": "附录引用率在各会议都偏低"},
        {"logical_key": {"from": ro("narr", "C-001"), "to": ro("asset", "A-reviews"),
                         "kind": "depends_on"}, "polarity": None, "conflict": False,
         "sources": [src("e4", "derive", "derive:asset-node", "2026-08-12T09:12:00Z")],
         "from_title": "附录引用率在各会议都偏低", "to_title": "OpenReview 评审数据"},
        # the EDGE_CONFLICT: two sources sign the same relation differently
        {"logical_key": {"from": ro("ledger", "E-09"), "to": ro("narr", "C-002"),
                         "kind": "supported_by"}, "polarity": None, "conflict": True,
         "sources": [src("e5a", "evidence-steward", "ledger:E-09 table 4 replicates",
                         "2026-08-16T10:00:00Z", "+"),
                     src("e5b", "experiment-runner", "re-read of E-09: sample differs",
                         "2026-08-19T15:30:00Z", "-")],
         "from_title": "证据条目 E-09（Chen 2025）", "to_title": "结论在期刊样本里同样成立"},
        {"logical_key": {"from": ro("run", "R-02"), "to": ro("narr", "C-002"),
                         "kind": "supported_by"}, "polarity": "-", "conflict": False,
         "sources": [src("e6", "experiment-runner", "run:R-02 outcome=refuted",
                         "2026-08-18T20:05:00Z", "-")],
         "from_title": "期刊复现 20260818", "to_title": "结论在期刊样本里同样成立"},
        {"logical_key": {"from": ro("narr", "C-002"), "to": ro("narr", "C-001"),
                         "kind": "evolved_from"}, "polarity": None, "conflict": False,
         "sources": [src("e7", "chronicler", "narr:commit 9f1c", "2026-08-17T07:00:00Z")],
         "from_title": "结论在期刊样本里同样成立", "to_title": "附录引用率在各会议都偏低"},
        {"logical_key": {"from": ro("tree", "R01.A01"), "to": ro("narr", "C-003"),
                         "kind": "supported_by"}, "polarity": "+", "conflict": False,
         "sources": [src("e8", "derive", "derive:tree-close", "2026-08-11T13:20:00Z", "+")],
         "from_title": "尝试：置换检验", "to_title": "阅读量取决于审稿流程"},
        {"logical_key": {"from": ro("fig", "fig-history"), "to": ro("narr", "C-003"),
                         "kind": "expressed_as"}, "polarity": None, "conflict": False,
         "sources": [src("e9", "figure-engineer", "registry:fig-history current",
                         "2026-08-09T16:40:00Z")],
         "from_title": "图 3 历史面板（已删）", "to_title": "阅读量取决于审稿流程"},
        {"logical_key": {"from": ro("narr", "C-003"), "to": ro("asset", "A-reviews"),
                         "kind": "depends_on"}, "polarity": None, "conflict": False,
         "sources": [src("e10", "derive", "derive:asset-node", "2026-08-11T13:21:00Z")],
         "from_title": "阅读量取决于审稿流程", "to_title": "OpenReview 评审数据"},
    ]

    coverage = {
        "project": project, "edges": len(edges), "nodes": 4,
        "load_bearing": ["R-0", "C-001", "C-002", "C-003"],
        "figures": ["fig-history", "fig-lead", "fig-price"],
        "no_figure": [{"id": "R-0", "title": "附录是有代价的"},
                      {"id": "C-002", "title": "结论在期刊样本里同样成立"}],
        "mute_figures": ["fig-lead"],
        "held_unsupported": [{"id": "C-002", "title": "结论在期刊样本里同样成立"}],
        "contested_active": [{"id": "C-002", "title": "结论在期刊样本里同样成立",
                              "against": ["run:R-02"], "epistemic": "HELD"}],
        "conflicts": [{"from": "ledger:E-09", "to": "narr:C-002", "kind": "supported_by",
                       "sources": [{"by": "evidence-steward", "polarity": "+",
                                    "basis": "ledger:E-09 table 4"},
                                   {"by": "experiment-runner", "polarity": "-",
                                    "basis": "re-read of E-09"}]}],
    }

    return {"schema": "auto-research/graph-current-v1",
            "built_at": "2026-08-25T04:20:00Z", "last_event_id": "e10",
            "project": project, "edges": edges,
            "conflicts": [e for e in edges if e.get("conflict")],
            "objects": objects, "coverage": coverage,
            "summary": {"project": project, "headline": ro("narr", "C-001"),
                        "active_edges": len(edges), "conflicts": 1, "gone": 1}}


def _fixture_project(base: Path) -> Path:
    root = base / "graph-ui-selftest"
    control = root / ".research-os"
    (control / "graph").mkdir(parents=True, exist_ok=True)
    ros.atomic_write(control / "state.json", json.dumps(
        {"schema_version": "auto-research/v2", "project_id": "graph-ui-selftest",
         "title": "Graph UI self-test", "status": "active"},
        ensure_ascii=False, indent=2) + "\n")
    ros.atomic_write(control / CURRENT_REL, json.dumps(
        _fixture_current(), ensure_ascii=False, indent=2) + "\n")
    return control


def self_test() -> int:
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    tmp = Path(tempfile.mkdtemp(prefix="ar-graph-ui-"))
    try:
        control = _fixture_project(tmp)
        payload = build_payload(control, view="argument")
        html = render_html(payload)
        size = len(html.encode("utf-8"))

        # ---- fixture is as advertised
        check("fixture: >=10 objects, >=8 edges, one conflict, one gone",
              len(payload["graph"]["objects"]) >= 10 and len(payload["graph"]["edges"]) >= 8
              and len(payload["graph"]["conflicts"]) == 1 and payload["counts"]["gone"] == 1,
              "%d objects / %d edges / %d conflicts / %d gone"
              % (len(payload["graph"]["objects"]), len(payload["graph"]["edges"]),
                 len(payload["graph"]["conflicts"]), payload["counts"]["gone"]))
        check("reads graph/current.json, not the event log",
              str(Path(control) / CURRENT_REL) in payload["graph"]["source"])
        check("headline start comes from summary.headline",
              payload["start"].endswith(":narr:C-001"), payload["start"])

        # ---- document + zero network
        check("renders a complete document", html.startswith("<!doctype html>")
              and html.rstrip().endswith("</html>"))
        check("no external link of any kind",
              ("http" + "://") not in html and ("https" + "://") not in html
              and "//cdn" not in html and "@import" not in html
              and "@font-face" not in html and "fonts." not in html)

        # ---- the four view containers
        for view in VIEWS:
            check("view container: " + view, ('id="view-%s"' % view) in html
                  and ('data-tab="%s"' % view) in html)
        check("one page, four tabs -- --view only picks the default",
              '"view":"argument"' in html and "function setTab(name)" in html
              and 'VIEWS.indexOf(DATA.view)' in html)
        for view in VIEWS:
            other = render_html(build_payload(control, view=view))
            check("--view %s renders the same page with that default tab" % view,
                  ('"view":"%s"' % view) in other and 'id="view-objects"' in other)

        # ---- the embedded projection round-trips
        start = html.index('id="ar-graph">') + len('id="ar-graph">')
        end = html.index("</script>", start)
        embedded = json.loads(html[start:end].replace("<\\/", "</"))
        check("embedded JSON round-trips",
              embedded["graph"]["last_event_id"] == "e10"
              and len(embedded["graph"]["edges"]) == 10
              and embedded["counts"]["conflicts"] == 1)
        check("edge sources carry by / basis / at",
              all(("by" in s and "basis" in s and "at" in s)
                  for e in embedded["graph"]["edges"] for s in e["sources"]))

        # ---- argument view: outline tree, class names shared with the axis page
        check("outline tree uses the narrative-axis class names",
              'class="tree"' in html and 'class="row' in html and 'class="chev' in html
              and 'class="dot ' in html and 'class="rid"' in html
              and 'class="rtitle"' in html and 'class="chip"' in html
              and 'class="count"' in html)
        check("guides are 1px hairlines with L corners and a broken tail",
              "border-width: 0 0 1px 1px;" in html
              and ".tree li:last-child { border-color: transparent; }" in html
              and "width: calc(var(--indent) + 1px);" in html
              and "--indent: 22px;" in html and "--row: 32px;" in html)
        check("native details/summary folding, chevron rotate only",
              "<details" in html and "<summary tabindex=" in html
              and ".tree summary::-webkit-details-marker" in html
              and "transform: rotate(90deg);" in html and "height 150ms" not in html)
        check("semantic colour only in the 6px dot (+ solid green / - red / conflict)",
              ".dot.pol-plus { background: var(--ok);" in html
              and ".dot.pol-minus { background: var(--no);" in html
              and ".dot.pol-conflict { background: transparent; border-color: var(--no);" in html
              and "width: 6px;" in html)
        check("conflict rows carry the (!) marker",
              'class="bang">(!)' in html and "bang: viaEdge ? !!viaEdge.conflict : false" in html)
        check("selection = 3px left bar + wash, no border/shadow trio",
              "box-shadow: inset 3px 0 0 var(--accent);" in html
              and ".row:hover { background: var(--hover); }" in html
              and "box-shadow: 0 " not in html)
        check("inspector shows in/out edges, sources and GONE/MISSING",
              "renderInspector" in html and "<h3>in (" in html and "<h3>out (" in html
              and "来源 (" in html and "[GONE]" in html and "[MISSING]" in html)

        # ---- coverage A-E
        check("coverage renders five lists A-E",
              all(('covSection("%s"' % letter) in html for letter in "ABCDE"))
        check("coverage items jump into the argument view",
              'var jump = S.tab === "coverage";' in html and "S.start = ro" in html)

        # ---- conflicts
        check("conflict table: 来源 / 极性 / 依据 / 时间",
              "<th>来源</th>" in html and "<th>极性</th>" in html
              and "<th>依据</th>" in html and "<th>时间</th>" in html
              and "function sourceTable(sources)" in html)
        check("both sides laid out side by side",
              ".conflict .side { display: grid; grid-template-columns: 1fr 1fr;" in html
              and "支持 (+)" in html and "反驳 (−)" in html)
        check("提议裁决 button builds a decision draft",
              "提议裁决" in html and "function proposeDecision(" in html
              and "function buildDraft(" in html)
        check("decision draft is exactly the contract shape",
              DECISION_DRAFT_SCHEMA in html and "logical_key: { from: e.from, to: e.to, kind: e.kind }"
              in html and "chosen_polarity:" in html and "rationale:" in html
              and "drafted_at: nowIso()" in html)
        check("three-tier export: downloads -> <a download> -> clipboard",
              'capability("downloads")' in html and "a.download = filename" in html
              and "navigator.clipboard" in html)
        check("the page never writes state",
              "永不写状态" in html and "fetch(" not in html and "XMLHttpRequest" not in html)

        # ---- objects
        check("objects grouped by space with a search filter",
              'id="objq"' in html and "bySpace" in html and 'type="search"' in html
              and "<th>status</th>" in html)
        check("GONE greyed, never deleted",
              ".is-gone .rtitle, .is-gone .rid, tr.is-gone td { color: var(--ink-3); }" in html
              and '(gone ? "is-gone " : "")' in html and 'opts.gone ? " is-gone" : ""' in html)

        # ---- shell / type / theme
        check("top bar carries project, last_event_id, built_at and the counts",
              'id="srcline"' in html and "last_event " in html and "built " in html
              and "edges " in html and "conflicts " in html and "gone " in html)
        check("main grid 1fr / 360px with a sticky inspector",
              "grid-template-columns: 1fr 360px;" in html and "position: sticky;" in html)
        check("type locked to 13/11 on a 32px row grid, system stack",
              "font-size: 13px;" in html and "font-size: 11px;" in html
              and 'system-ui, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif' in html
              and "ui-monospace, Consolas, monospace" in html
              and "min-height: var(--row);" in html)
        check("exactly three semantic tokens (--ok / --no / --wait)",
              html.count("--ok:") == 3 and html.count("--no:") == 3
              and html.count("--wait:") == 3)
        check("theme tri-state tokens", "prefers-color-scheme: dark" in html
              and ':root:not([data-theme="light"])' in html
              and ':root[data-theme="dark"]' in html and "background: var(--bg)" in html)
        check("reduced motion honoured", "prefers-reduced-motion" in html)
        check("open state + tab + theme in localStorage, guarded",
              "localStorage" in html and "catch (e) { return fallback; }" in html
              and '":open"' in html and '":tab"' in html and '":theme"' in html)
        check("keyboard focusable rows + arrow-key fold",
              'tabindex="0"' in html and 'role="button"' in html
              and 'ev.key === "ArrowRight"' in html)
        check("title carries the project", "<title>graph-ui-selftest · 研究图</title>" in html)
        check("size under 400 KB", size < SIZE_BUDGET, "%d bytes" % size)

        # ---- receipt path
        out = tmp / "graph.html"
        receipt = render(control, "conflicts", out)
        check("render() writes the file and returns a receipt",
              receipt["ok"] and out.exists() and receipt["view"] == "conflicts"
              and receipt["edges"] == 10 and receipt["conflicts"] == 1
              and receipt["last_event_id"] == "e10")
        check("written file is LF-only UTF-8",
              b"\r\n" not in out.read_bytes()
              and out.read_bytes().decode("utf-8").startswith("<!doctype html>"))

        # ---- degraded path: no current.json -> live projection, page still whole
        bare = tmp / "bare" / ".research-os"
        (bare / "graph").mkdir(parents=True, exist_ok=True)
        ros.atomic_write(bare / "state.json", json.dumps(
            {"schema_version": "auto-research/v2", "project_id": "bare"},
            ensure_ascii=False) + "\n")
        try:
            bare_html = render_html(build_payload(bare, view="objects"))
            ok = bare_html.startswith("<!doctype html>") and 'id="view-objects"' in bare_html
            detail = "%d bytes" % len(bare_html.encode("utf-8"))
        except Exception as exc:                                # pragma: no cover
            ok, detail = False, repr(exc)
        check("no current.json yet -> live projection, page still renders", ok, detail)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

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
        prog="graph_render_html.py",
        description="single-file HTML research-graph view (v3 contract §4)")
    parser.add_argument("--project", help="project root, .research-os dir, or state.json")
    parser.add_argument("--view", default="argument", choices=list(VIEWS),
                        help="which tab opens first (the page always carries all four)")
    parser.add_argument("--node", help="argument-chain start, e.g. ro::narr:C-041")
    parser.add_argument("--out", help="write the page here (default: stdout)")
    parser.add_argument("--self-test", dest="self_test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.project:
        ros.die("--project is required (or --self-test)", 2)
    control = ros.locate_control(Path(args.project))
    try:
        result = render(control, args.view, args.out, node=args.node)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    if isinstance(result, dict):
        print(json.dumps(result, ensure_ascii=False))
        return 0
    sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
