#!/usr/bin/env python3
"""auto-research research graph -- the shared substrate of the role fleet.

docs/role-fleet.md section 5 ("共享底座：研究图"), v3 step 1, **bypass layer**.

The narrative tree answers "what does the paper claim, and how did that change".
It cannot answer "which figure expresses this claim", "which run produced the
number under it", or "which load-bearing claim has no evidence at all", because
those objects live in four other stores (the attempt tree, the FSG figure
programs, the asset index, the evidence ledgers) that were never addressable
from one another.  This module adds exactly two cheap things on top -- one
address space and one append-only edge ledger -- and never migrates or renumbers
anything.  Every existing id keeps its meaning; the graph only says how ids in
different stores relate.

Three surfaces:

  addressing   ``ro:<project>:<space>:<id>`` with space in
               {narr, tree, fig, asset, run, claim, ledger, decision}.
               ``graph resolve`` is READ-ONLY: it returns {file, object, exists}
               and nothing else.  The short form ``ro::narr:C-041`` means "this
               project".

  edge ledger  ``.research-os/graph/edges.jsonl``, append-only, projected by
               folding the event log.  ``graph link`` refuses an edge whose
               endpoint does not resolve (and says WHICH end); ``graph unlink``
               appends a retraction event -- lines are never rewritten.

  derivation   ``graph derive`` reads the four source stores and emits the edges
               that are already implied by them.  Deterministic, idempotent
               (edge ids are content hashes, re-runs add nothing), read-only on
               every source, and it never guesses: when a source is absent it
               reports 0 for that rule and names the missing source.

  projection   ``graph view argument|coverage|role-activity`` -- <=40 lines of
               text, the budget the main controller can actually afford.

v3 (docs/v3-contract.md) promotes those three surfaces from a bypass to the
AUTHORITATIVE RELATION LAYER, and adds two more:

  transaction  ``link | retract | derive | index`` are one unit -- journal ->
               stamped events -> temp file -> fsync -> atomic rename ->
               ``graph/current.json`` re-projected -> journal DONE, all inside
               the PROJECT write lock (re-entrant, because the host CLIs call
               ``derive()`` from inside their own transaction).  A crash is
               finished by ``graph rebuild``; nothing else may write graph/.

  object index ``graph/objects.json`` -- every object of all eight spaces with
               its fingerprint and source path.  An object that disappears is
               marked ``gone``, never deleted, and derivation takes its edges
               back with a reason.

Objects themselves still belong to their own stores: the graph owns how ids
relate, never what they mean.  Writes stay inside ``.research-os/graph/`` (plus
the one-time ``state.project_id`` freeze); state.json, events.jsonl, the
narrative store, the turn runtime and the narrative lease are untouched.

Edge-direction convention (fixed by role-fleet section 5, "谁写什么边"):

  ``supported_by``  from = the supporting object (run / tree attempt / ledger
                    entry / claim), to = the narrative node it supports.
                    Read the arrow as "evidence -> claim".  ``polarity`` is
                    ``+`` (supports), ``-`` (refutes) or null (recorded but
                    unsigned); it is legal on this kind only.
  ``expressed_as``  from = the figure element, to = the claim it expresses.
  ``depends_on``    from depends on to (narrative node -> asset it needs).
  ``evolved_from``  from evolved from to.

stdlib-only, Python >= 3.9.  Exit codes: 0 ok, 1 contract refusal, 2 usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros  # noqa: E402

# ============================================================ constants

EDGE_SCHEMA = "auto-research/graph-edge-v1"
RETRACT_SCHEMA = "auto-research/graph-retract-v1"

# R9 B7/B8: the authorisation table is versioned, so a parked proposal records
# which policy judged it and `--replay-pending` re-authorises under the current
# one instead of silently inheriting yesterday's verdict.
AUTHORIZATION_POLICY_VERSION = "r9.1"
PENDING_MAX_ATTEMPTS = 3
PENDING_STALE_DAYS = 30
PENDING_STATES = ("PENDING", "RESOLVED", "DENIED", "STALE")

SPACES = ("narr", "tree", "fig", "asset", "run", "claim", "ledger", "decision")
EDGE_KINDS = ("depends_on", "supported_by", "expressed_as", "evolved_from")
POLARITIES = ("+", "-")
POLARITY_KINDS = ("supported_by",)          # the only kind that may carry a sign

RO_RE = re.compile(r"^ro:([^:]*):([a-z]+):(.+)$")
NODE_ID_RE = re.compile(r"\b([A-Z]{1,2}-[0-9]+(?:\.[0-9]+)*)\b")
LEDGER_ID_RE = re.compile(r"^[EDR]-[0-9A-Za-z][0-9A-Za-z.\-]*$")
DECISION_HEAD_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s+(.*)$")
DATE_ORD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d+))?$")

FSG_REL = "research/artifacts/visual-program/fsg"
CLAIMS_REL = "research/claims"
EVIDENCE_REL = "research/evidence"
FIGURES_REGISTRY_REL = "figures/registry.json"     # under .research-os/ (v2.9, X track)
FIGURE_REGISTRY_SCHEMA = "auto-research/figure-registry-v1"
PENDING_REL = "roles/pending_edges"                # under .research-os/
PENDING_SCHEMA = "auto-research/graph-pending-edges-v1"

# ---- v3 contract 0: the authoritative relation layer.  The event stream is
# schema v2 from here on; v1 rows are read exactly as they were written (a
# ledger is append-only, so upgrading may not rewrite one line of history).
EVENT_SCHEMA_V2 = "auto-research/graph-events-v2"
CURRENT_SCHEMA = "auto-research/graph-current-v1"
OBJECTS_SCHEMA = "auto-research/graph-objects-v1"
CONTEXT_SCHEMA = "auto-research/graph-context-v1"
JOURNAL_SCHEMA = "auto-research/graph-journal-v1"
CURRENT_REL = "graph/current.json"                 # under .research-os/
OBJECTS_REL = "graph/objects.json"                 # under .research-os/
RETRACTED_TAIL = 200          # how much retraction history current.json carries
GRAPH_CONTEXT_MAX_BYTES = 3500                     # role-fleet 3 envelope budget
GRAPH_CONTEXT_MAX_EDGES = 8                        # v3 contract 3: per object
CROSS_PROJECT_LINES = 5                            # v3 contract 5: per project

# v3 contract 2: which spaces a derivation rule reads.  `derive --since` skips a
# rule whose spaces have not changed -- the whole point of an incremental pass
# is that a figure promotion does not re-read every ledger in the project.
RULE_SOURCES = {
    "narr-basis": ("narr", "ledger", "asset"),
    "tree-close": ("tree", "narr", "claim"),
    "fsg-claim": ("fig", "narr"),
    "fig-registry": ("fig", "narr"),
    "asset-node": ("asset", "narr"),
    "run-claim": ("run", "claim", "narr"),
    "claim-verdict": ("claim", "narr"),
    "lineage": ("narr",),
}

# graph-contract 5 / role-fleet 5: who may propose which edge class.  A role
# proposing outside its column is refused (EDGE_CLASS_DENIED) and nothing is
# written -- the point of a shared substrate is that authorship stays legible,
# so the figure engineer can never quietly assert evidence.
EDGE_CLASS_POLICY = {
    "chronicler": (("narr", "narr", "evolved_from"), ("narr", "narr", "depends_on"),
                   ("narr", "claim", "depends_on"), ("narr", "claim", "supported_by")),
    "figure-engineer": (("fig", "narr", "expressed_as"),),
    "evidence-steward": (("ledger", "narr", "supported_by"),),
    "experiment-runner": (("run", "narr", "supported_by"), ("run", "claim", "supported_by")),
    "theory-operator": (("claim", "narr", "supported_by"), ("claim", "claim", "evolved_from")),
}

MAX_VIEW_LINES = 40
LEDGER_SOURCE_CAP = 24
LEDGER_BYTES_CAP = 2 * 1024 * 1024
ROLE_ACTIVITY_DAYS = 30

# R9 B5: exhaustive status -> polarity tables.  A status absent from a table
# produces NO EDGE -- it is never rounded to a sign and never sniffed out of
# free text.  ``None`` means "a real outcome that carries no sign", so an
# unsigned support edge is still written; absence means "say nothing at all".
NO_EDGE = "NO_EDGE"

RUN_OUTCOME_POLARITY = {
    "positive": "+", "confirmed": "+", "success": "+", "succeeded": "+",
    "proven": "+", "proved": "+", "replicated": "+", "holds": "+",
    "negative": "-", "refuted": "-", "failed": "-", "failure": "-",
    "disproved": "-", "no-go": "-",
    "inconclusive": None, "null": None, "mixed": None, "partial": None,
    "running": NO_EDGE, "queued": NO_EDGE, "pending": NO_EDGE,
    "aborted": NO_EDGE, "cancelled": NO_EDGE, "canceled": NO_EDGE,
}

CLAIM_VERDICT_POLARITY = {
    "proved": "+", "proven": "+",
    "refuted": "-",
    "conditional": None, "barrier": None, "equivalent_core": None,
    "open": NO_EDGE, "unresolved": NO_EDGE,
}

TREE_STATUS_POLARITY = {
    "proven": "+", "proved": "+", "success": "+",
    "refuted": "-", "failed": "-",
    "blocked": None, "abandoned": None, "superseded": None, "inconclusive": None,
}


class GraphError(Exception):
    """A contract refusal; ``text`` is what a human should read."""

    def __init__(self, text, code=1, payload=None):
        super().__init__(text)
        self.text = text
        self.code = code
        self.payload = payload or {}


# ============================================================ primitives

def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return ros.utc_now()


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def clip(value, limit=160) -> str:
    text = "" if value is None else str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def resolve_control(target=None) -> Path:
    """Project root / .research-os / any inside path -> the control dir."""
    if target:
        control = ros.locate_control(Path(target))
        if (control / "state.json").exists():
            return control
        found = ros.find_project_upwards(Path(target))
        if found is None:
            raise GraphError("no auto-research project at %s" % target, 2)
        return found
    found = ros.find_project_upwards(Path(os.getcwd()))
    if found is None:
        raise GraphError("no auto-research project in this directory or any parent", 2)
    return found


def project_root(control: Path) -> Path:
    return control.parent


def project_id(control: Path) -> str:
    """Read-only: the frozen id if there is one, otherwise the value that
    ``ensure_project_id`` would freeze.  Display and resolution may use this;
    anything that WRITES a persistent address must call ``ensure_project_id``
    first (R9 B1), so an address never changes meaning after the title does."""
    state = read_json(control / "state.json", {}) or {}
    if state.get("project_id"):
        return str(state["project_id"])
    if state.get("title"):
        return ros.slugify(str(state["title"]))
    return ros.slugify(project_root(control).name)


def ensure_project_id(control: Path) -> str:
    """R9 B1: freeze ``state.project_id`` before the first persistent address is
    written.  ``slug(title)`` is a display convenience; a renamed paper must not
    silently re-point every edge in the ledger."""
    path = control / "state.json"
    state = read_json(path, None)
    if isinstance(state, dict) and state.get("project_id"):
        return str(state["project_id"])
    frozen = project_id(control)
    if isinstance(state, dict):
        state["project_id"] = frozen
        try:
            ros.atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        except OSError:                                  # pragma: no cover
            pass
        cache_clear(control)
    return frozen


# ============================================================ addressing

def parse_ro(address: str, control: Path) -> dict:
    """``ro:<project>:<space>:<id>`` -> {ro, project, space, id, foreign}.

    The project segment may be empty (``ro::narr:C-041``) meaning "this
    project".  A non-empty segment that names another project is parsed, not
    rejected -- resolution then reports ``exists=false, reason=foreign project``
    so the caller sees why, instead of a syntax error.
    """
    if not isinstance(address, str) or not address.strip():
        raise GraphError("empty ro address", 2)
    match = RO_RE.match(address.strip())
    if not match:
        raise GraphError(
            "MALFORMED_RO %s -- expected ro:<project>:<space>:<id> "
            "(project may be empty, e.g. ro::narr:C-041)" % address, 2)
    project, space, ident = match.group(1), match.group(2), match.group(3).strip()
    if space not in SPACES:
        raise GraphError("UNKNOWN_SPACE %r in %s -- one of %s"
                         % (space, address, ", ".join(SPACES)), 2)
    if not ident:
        raise GraphError("EMPTY_ID in %s" % address, 2)
    here = project_id(control)
    return {"ro": address.strip(), "project": project or here, "space": space,
            "id": ident, "foreign": bool(project) and project != here}


def format_ro(project: str, space: str, ident: str) -> str:
    return "ro:%s:%s:%s" % (project, space, ident)


def coerce_ro(value: str, control: Path, default_space: str = "narr") -> str:
    """Accept either a full ro address or a bare id in ``default_space``."""
    text = (value or "").strip()
    if not text:
        raise GraphError("empty address", 2)
    if text.startswith("ro:"):
        parsed = parse_ro(text, control)
        return format_ro(parsed["project"], parsed["space"], parsed["id"])
    return format_ro(project_id(control), default_space, text)


# ============================================================ source readers (cached, read-only)

_CACHE: dict = {}


def _cache(control: Path, key: str, builder):
    slot = _CACHE.setdefault(str(control), {})
    if key not in slot:
        slot[key] = builder()
    return slot[key]


def cache_clear(control: Path = None) -> None:
    if control is None:
        _CACHE.clear()
    else:
        _CACHE.pop(str(control), None)


def _narrative_module():
    try:
        import narrative as _narr  # noqa: WPS433 -- same-dir sibling, stdlib-only
        return _narr
    except Exception:                                    # pragma: no cover
        return None


def head_commit(control: Path):
    path = control / "narrative" / "refs" / "heads" / "main"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:                                      # pragma: no cover
        return None


def head_snapshot(control: Path):
    """The committed narrative HEAD snapshot, or None when there is no store.

    Uses ``narrative.load_head_snapshot`` (contract section 8: derived views are
    built from the committed snapshot, never from a preview).
    """
    def build():
        if head_commit(control) is None:
            return None
        module = _narrative_module()
        if module is None:
            return None
        try:
            snapshot = module.load_head_snapshot(control, "main")
        except SystemExit:
            return None
        except Exception:                                # pragma: no cover
            return None
        return snapshot if isinstance(snapshot, dict) and snapshot.get("nodes") else None

    return _cache(control, "narr", build)


def load_tree(control: Path):
    """`.research-os/tree.json` (attempt tree) or None."""
    def build():
        data = read_json(control / "tree.json", None)
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), dict):
            return None
        return data

    return _cache(control, "tree", build)


def load_state(control: Path) -> dict:
    return _cache(control, "state", lambda: read_json(control / "state.json", {}) or {})


def asset_index(control: Path) -> dict:
    """Asset id -> {entry, ids[]}.  The index tolerates both shapes found in the
    wild: v2 dict entries and the legacy plain-string entries older projects
    still carry."""
    def build():
        out = {}
        for position, entry in enumerate(load_state(control).get("artifacts") or []):
            keys = ["#%d" % position]
            if isinstance(entry, dict):
                for field in ("id", "path"):
                    if entry.get(field):
                        keys.append(str(entry[field]))
                if entry.get("path"):
                    keys.append(Path(str(entry["path"])).name)
            else:
                text = str(entry)
                keys.append(text)
                head = re.split(r"[\s(（]", text.strip(), maxsplit=1)[0]
                if head:
                    keys.append(head)
            for key in keys:
                out.setdefault(key, {"entry": entry, "position": position})
        return out

    return _cache(control, "assets", build)


def fsg_files(control: Path) -> list:
    directory = project_root(control) / FSG_REL
    if not directory.exists():
        return []
    return sorted(directory.glob("*.fsg.json"))


def figure_registry(control: Path) -> dict:
    """The v2.9 figure-instance ledger, read-only.

    The X track owns the writer; the graph reads only the documented shape
    ``{"schema": "auto-research/figure-registry-v1",
       "figures": {"<F>": {"status": "draft|current|superseded",
                           "claim_ids": [...]}}}``
    and tolerates anything else by reporting an empty registry rather than
    guessing at a private layout.
    """
    def build():
        raw = read_json(control / FIGURES_REGISTRY_REL, None)
        if not isinstance(raw, dict):
            return {}
        figures = raw.get("figures")
        if not isinstance(figures, dict):
            return {}
        return {str(k): v for k, v in figures.items() if isinstance(v, dict)}

    return _cache(control, "figreg", build)


def registry_figures(control: Path, status: str = "current") -> list:
    return sorted(fid for fid, entry in figure_registry(control).items()
                  if str(entry.get("status") or "").strip().lower() == status)


def parse_front_matter(path: Path) -> tuple:
    """``---`` YAML-ish front matter -> (dict, body).  Flat ``key: value`` only;
    that is all procedures.py ever writes."""
    front, body = {}, ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:                                      # pragma: no cover
        return front, body
    if text.startswith("---"):
        parts = text.split("---", 2)
        for line in parts[1].splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip():
                front[key.strip()] = value.strip()
        body = parts[2] if len(parts) > 2 else ""
    else:
        body = text
    return front, body


def load_fsg(control: Path, figure_id: str):
    path = project_root(control) / FSG_REL / ("%s.fsg.json" % figure_id)
    if not path.exists():
        return None, None
    return path, read_json(path, None)


def _ledger_sources(control: Path) -> list:
    """Every file that may carry an evidence-ledger row, in a fixed order.

    Four origins: the four contract ledgers under ``research/evidence/*.jsonl``,
    any markdown beside them, project-root ledger documents (``*台账*.md`` /
    ``*ledger*.md``), and -- the one that matters for backfilled projects -- the
    documents the narrative's own ``backfill:<path>#<node>`` basis refs name,
    because that is where an older project's ``| R-01 | ... |`` table lives.
    """
    root = project_root(control)
    ordered = []

    def add(path: Path):
        if path.exists() and path.is_file() and path not in ordered:
            try:
                if path.stat().st_size <= LEDGER_BYTES_CAP:
                    ordered.append(path)
            except OSError:                              # pragma: no cover
                pass

    evidence = root / EVIDENCE_REL
    if evidence.exists():
        for path in sorted(evidence.glob("*.jsonl")) + sorted(evidence.glob("*.md")):
            add(path)
    for pattern in ("*台账*.md", "*ledger*.md", "*Ledger*.md"):
        for path in sorted(root.glob(pattern)):
            add(path)
    snapshot = head_snapshot(control)
    if snapshot:
        named = []
        for node in snapshot.get("nodes", {}).values():
            for ref in node.get("basis_refs") or []:
                if isinstance(ref, str) and ref.startswith("backfill:"):
                    rel = ref[len("backfill:"):].split("#", 1)[0].strip()
                    if rel and rel not in named:
                        named.append(rel)
        for rel in named[:8]:
            add(root / rel)
    return ordered[:LEDGER_SOURCE_CAP]


def ledger_index(control: Path) -> dict:
    """Ledger id -> {file, line, text}.  First occurrence wins (deterministic
    because ``_ledger_sources`` is ordered)."""
    def build():
        out = {}
        root = project_root(control)
        for path in _ledger_sources(control):
            rel = path.relative_to(root).as_posix() if str(path).startswith(str(root)) else str(path)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:                              # pragma: no cover
                continue
            if path.suffix == ".jsonl":
                for number, line in enumerate(text.splitlines(), 1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = row.get("id") or row.get("claim") or row.get("axis")
                    if key:
                        out.setdefault(str(key), {"file": rel, "line": number,
                                                  "text": clip(line, 300), "row": row})
                out.setdefault(path.stem, {"file": rel, "line": 0,
                                           "text": "ledger file %s" % rel, "row": None})
                continue
            for number, line in enumerate(text.splitlines(), 1):
                ident = _row_id(line)
                if ident:
                    out.setdefault(ident, {"file": rel, "line": number,
                                           "text": clip(line, 300), "row": None})
        return out

    return _cache(control, "ledger", build)


def _row_id(line: str):
    """The id a ledger line declares, or None.  Two shapes only: a markdown
    table row whose first cell is an id, and a bullet/heading that starts with
    one."""
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith("|"):
        cells = stripped.split("|")
        if len(cells) < 3:
            return None
        head = cells[1].strip().strip("`*").strip()
        return head if LEDGER_ID_RE.match(head) else None
    match = re.match(r"^\s*(?:[-*+]\s+|#{1,6}\s+)?\**\s*([A-Z]{1,3}-[0-9A-Za-z][0-9A-Za-z.\-]*)\b",
                     line)
    if match and LEDGER_ID_RE.match(match.group(1)):
        return match.group(1)
    return None


def decision_blocks(control: Path) -> list:
    """`## YYYY-MM-DD title` blocks of decisions.md, in file order.

    Addressing (the contract fixes no format, so this is the interpretive
    decision, aligned with narrative_backfill.parse_decisions):
      ``<date>``          -> the first block on that date
      ``<date>-<n>``      -> the n-th block on that date (1-based)
      ``<slug>``          -> slugified title match, else a literal token match
    """
    def build():
        path = control / "decisions.md"
        if not path.exists():
            return []
        blocks, current = [], None
        for number, line in enumerate(path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n"), 1):
            head = DECISION_HEAD_RE.match(line)
            if head:
                if current:
                    blocks.append(current)
                current = {"date": head.group(1), "title": head.group(2).strip(),
                           "line": number, "body": []}
            elif current is not None and line.strip():
                current["body"].append(line.strip())
        if current:
            blocks.append(current)
        by_date = {}
        for block in blocks:
            by_date[block["date"]] = by_date.get(block["date"], 0) + 1
            block["ordinal"] = by_date[block["date"]]
            block["slug"] = ros.slugify(block["title"])
            block["summary"] = clip(" ".join(block.pop("body")), 220)
            block["id"] = "%s-%d" % (block["date"], block["ordinal"])
        return blocks

    return _cache(control, "decisions", build)


# ============================================================ resolution

def _miss(reason: str, file=None) -> dict:
    return {"exists": False, "file": file, "object": None, "reason": reason}


def _hit(file, obj: dict) -> dict:
    return {"exists": True, "file": file, "object": obj, "reason": None}


def _rel(control: Path, path):
    if path is None:
        return None
    path = Path(path)
    try:
        return path.relative_to(project_root(control)).as_posix()
    except ValueError:
        return str(path)


def _resolve_narr(control: Path, ident: str) -> dict:
    snapshot = head_snapshot(control)
    if snapshot is None:
        return _miss("no narrative store (run `narrative init`)")
    node = snapshot.get("nodes", {}).get(ident)
    commit = head_commit(control)
    tree_hash = ((read_json(control / "narrative" / "commits" / ("%s.json" % commit), {}) or {})
                 .get("tree") if commit else None)
    file = (".research-os/narrative/objects/%s.json" % tree_hash if tree_hash
            else ".research-os/narrative/refs/heads/main")
    if node is None:
        return _miss("no node %s in the HEAD snapshot" % ident, file)
    roles = [role for role, value in (snapshot.get("role_assignments") or {}).items() if value == ident]
    return _hit(file, {"id": ident, "node_type": node.get("node_type"),
                       "title": clip(node.get("title")), "state": node.get("state"),
                       "basis_refs": (node.get("basis_refs") or [])[:8],
                       "children": len(node.get("children") or []),
                       "roles": roles, "source_commit": commit})


def _resolve_tree(control: Path, ident: str) -> dict:
    tree = load_tree(control)
    if tree is None:
        return _miss("no .research-os/tree.json (attempt tree never registered)")
    node = tree["nodes"].get(ident)
    file = ".research-os/tree.json"
    if node is None:
        return _miss("no node %s in tree.json" % ident, file)
    return _hit(file, {"code": ident, "kind": node.get("kind"),
                       "title": clip(node.get("title")), "status": node.get("status"),
                       "result": clip(node.get("result")),
                       "evidence": (node.get("evidence") or [])[:4],
                       "tags": node.get("tags") or []})


def _resolve_fig(control: Path, ident: str) -> dict:
    """Contract 1: ``figures/registry.json`` first (a registered instance is the
    published object), then the FSG program that generated it."""
    figure_id, _, member = ident.partition(".")
    registry = figure_registry(control)
    entry = registry.get(figure_id)
    if entry is not None and not member:
        keep = {k: entry[k] for k in ("id", "status", "claim_ids", "version", "title",
                                      "message", "spec", "render", "template", "qa_summary")
                if k in entry}
        keep.setdefault("id", figure_id)
        keep["source"] = "figures/registry"
        return _hit(".research-os/" + FIGURES_REGISTRY_REL, keep)
    directory = project_root(control) / FSG_REL
    if not directory.exists():
        if registry:
            return _miss("no figure %s at instance level (%d registered in %s) and no %s "
                         "directory" % (figure_id, len(registry), FIGURES_REGISTRY_REL, FSG_REL),
                         ".research-os/" + FIGURES_REGISTRY_REL)
        return _miss("neither .research-os/%s nor a %s directory (no figure store in this "
                     "project)" % (FIGURES_REGISTRY_REL, FSG_REL))
    path, fsg = load_fsg(control, figure_id)
    if fsg is None:
        return _miss("no %s.fsg.json" % figure_id, _rel(control, directory))
    file = _rel(control, path)
    if not member:
        block = fsg.get("figure") or {}
        return _hit(file, {"id": figure_id, "message": clip(block.get("message")),
                           "archetype": block.get("archetype"),
                           "version": block.get("version"),
                           "panels": len(fsg.get("panels") or []),
                           "elements": len(fsg.get("nodes") or [])})
    for group in ("panels", "nodes", "edges", "texts"):
        for entry in fsg.get(group) or []:
            if isinstance(entry, dict) and str(entry.get("id")) == member:
                keep = {k: entry[k] for k in ("id", "type", "role", "panel", "semantic",
                                              "claim_id", "claim", "content", "from", "to")
                        if k in entry}
                keep["group"] = group
                keep["figure"] = figure_id
                return _hit(file, keep)
    return _miss("no member %s in %s" % (member, figure_id), file)


def _resolve_asset(control: Path, ident: str) -> dict:
    index = asset_index(control)
    if not index:
        return _miss("state.artifacts is empty (nothing indexed in the ammo depot)",
                     ".research-os/state.json")
    found = index.get(ident)
    if found is None:
        return _miss("no artifact %s in state.artifacts" % ident, ".research-os/state.json")
    entry = found["entry"]
    if isinstance(entry, dict):
        obj = {k: entry[k] for k in ("id", "path", "role", "tags", "version", "status",
                                     "produced_by", "sha256") if k in entry}
    else:
        obj = {"id": "#%d" % found["position"], "legacy_text": clip(entry, 200)}
    obj["position"] = found["position"]
    return _hit(".research-os/state.json", obj)


def _resolve_run(control: Path, ident: str) -> dict:
    directory = control / "runs"
    if not directory.exists():
        return _miss("no .research-os/runs/ (no run ever registered)")
    path = directory / ("%s.json" % ident)
    if not path.exists():
        return _miss("no run %s" % ident, ".research-os/runs")
    run = read_json(path, {}) or {}
    return _hit(".research-os/runs/%s.json" % ident,
                {k: run[k] for k in ("run_id", "protocol", "status", "command",
                                     "tree_node", "result", "finished_at") if k in run})


def _resolve_claim(control: Path, ident: str) -> dict:
    base = project_root(control) / CLAIMS_REL
    if not base.exists():
        return _miss("no %s/ (no formal claim ever opened)" % CLAIMS_REL)
    spec = base / ident / "spec.md"
    if not spec.exists():
        return _miss("no %s/%s/spec.md" % (CLAIMS_REL, ident), _rel(control, base))
    front, _body = parse_front_matter(spec)
    keep = {k: front[k] for k in ("id", "status", "verdict", "spec_version", "frozen_sha")
            if k in front}
    keep.setdefault("id", ident)
    return _hit("%s/%s/spec.md" % (CLAIMS_REL, ident), keep)


def _resolve_ledger(control: Path, ident: str) -> dict:
    index = ledger_index(control)
    if not index:
        return _miss("no evidence ledger found (neither %s/*.jsonl nor a project "
                     "ledger document)" % EVIDENCE_REL)
    found = index.get(ident)
    if found is None:
        return _miss("no ledger row %s in %d indexed source(s)" % (ident, len(_ledger_sources(control))))
    obj = {"id": ident, "line": found["line"], "text": clip(found["text"], 200)}
    if found.get("row"):
        obj["row"] = found["row"]
    return _hit(found["file"], obj)


def _resolve_decision(control: Path, ident: str) -> dict:
    path = control / "narrative" / "decisions" / ("%s.json" % ident)
    if path.exists():
        body = read_json(path, {}) or {}
        return _hit(".research-os/narrative/decisions/%s.json" % ident,
                    {"id": ident, "user_approved": body.get("user_approved"),
                     "title": clip(body.get("title") or body.get("summary"))})
    blocks = decision_blocks(control)
    if not blocks:
        return _miss("no .research-os/decisions.md entries")
    match = DATE_ORD_RE.match(ident)
    if match:
        date, ordinal = match.group(1), int(match.group(2) or 1)
        for block in blocks:
            if block["date"] == date and block["ordinal"] == ordinal:
                return _hit(".research-os/decisions.md",
                            {"id": block["id"], "date": date, "title": clip(block["title"]),
                             "line": block["line"], "summary": block["summary"]})
        return _miss("no decision %s in decisions.md" % ident, ".research-os/decisions.md")
    slug = ros.slugify(ident)
    for block in blocks:
        if block["slug"] == slug or slug in block["slug"] or ident in block["title"]:
            return _hit(".research-os/decisions.md",
                        {"id": block["id"], "date": block["date"], "title": clip(block["title"]),
                         "line": block["line"], "summary": block["summary"]})
    return _miss("no decision matching %r in decisions.md" % ident, ".research-os/decisions.md")


RESOLVERS = {"narr": _resolve_narr, "tree": _resolve_tree, "fig": _resolve_fig,
             "asset": _resolve_asset, "run": _resolve_run, "claim": _resolve_claim,
             "ledger": _resolve_ledger, "decision": _resolve_decision}


def resolve(control: Path, address: str) -> dict:
    """Read-only resolution of one ro address -> {file, object, exists}."""
    parsed = parse_ro(address, control)
    if parsed["foreign"]:
        result = _miss("foreign project %r (this project is %r)"
                       % (parsed["project"], project_id(control)))
    else:
        result = RESOLVERS[parsed["space"]](control, parsed["id"])
    out = {"ro": format_ro(parsed["project"], parsed["space"], parsed["id"]),
           "project": parsed["project"], "space": parsed["space"], "id": parsed["id"]}
    out.update(result)
    return out


# ============================================================ edge ledger

def graph_dir(control: Path) -> Path:
    return control / "graph"


def edges_path(control: Path) -> Path:
    return graph_dir(control) / "edges.jsonl"


def edge_id_for(edge: dict) -> str:
    """Content hash, short 12.  Deliberately excludes ``at`` so that re-running
    ``derive`` produces the same id (idempotency), and includes ``by``+``basis``
    so that two authors asserting the same relation for different reasons stay
    two distinguishable assertions."""
    return sha256_text(canonical({"from": edge["from"], "to": edge["to"],
                                  "kind": edge["kind"], "polarity": edge.get("polarity"),
                                  "basis": edge.get("basis") or "",
                                  "by": edge.get("by") or ""}))[:12]


def read_edge_events(control: Path) -> list:
    path = edges_path(control)
    if not path.exists():
        return []
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            raise GraphError("edges.jsonl line %d is not valid JSON (append-only ledger "
                             "was hand-edited?)" % number, 1)
        row["_line"] = number
        events.append(row)
    return events


def project_edges(control: Path) -> dict:
    """Fold the append-only log into ``edge_id -> edge``, in event order.

    R9 B2: a retraction is its own event ``{event, target_edge_id, by, basis,
    at}``; the link row is never rewritten.  ``retracted`` / ``retraction`` on
    the projected edge are computed here, not stored on disk, so the ledger
    holds exactly one durable statement per author per moment.
    """
    edges = {}
    for event in read_edge_events(control):
        kind = event.get("event")
        if kind == "link":
            edge_id = event.get("edge_id")
            if not edge_id:
                continue
            edge = {k: event.get(k) for k in
                    ("edge_id", "from", "to", "kind", "polarity", "basis", "by", "at",
                     "source_commit", "source_fingerprint", "also_from")}
            edge["retracted"] = False
            edge["retraction"] = None
            edges[edge_id] = edge
        elif kind in ("retract", "unlink"):
            # `unlink` is the pre-R9 spelling; it is read, never written.
            target = event.get("target_edge_id") or event.get("edge_id")
            if not target or target not in edges:
                continue
            edges[target]["retracted"] = True
            edges[target]["retraction"] = {
                "by": event.get("by"), "at": event.get("at"),
                "basis": event.get("basis") or event.get("reason"),
                "event_id": event.get("event_id")}
    return edges


def known_edge_ids(control: Path) -> set:
    """Every edge id the ledger has ever seen -- retracted ones included."""
    return {e.get("edge_id") for e in read_edge_events(control)
            if e.get("event") == "link" and e.get("edge_id")}


def append_events(control: Path, rows: list, kind: str = "link") -> None:
    """Every write to the event stream goes through ONE transaction (v3 0).

    Before v3 this appended straight to the file under a lock private to
    ``graph/``.  That was safe against a second writer and unsafe against a
    crash: the projection everything reads (``current.json``) could disagree
    with the ledger and nobody would know.  Now the append, the projection and
    the journal that can finish the job after a power cut are one unit, held
    inside the PROJECT write lock so a graph write cannot interleave with the
    state write it belongs to.
    """
    if not rows:
        return
    with transaction(control, kind) as tx:
        tx.add(rows)


def make_edge(control: Path, from_ro: str, to_ro: str, kind: str, basis: str,
              by: str, polarity=None, at=None, source_commit=None,
              source_fingerprint=None, also_from=None) -> dict:
    if kind not in EDGE_KINDS:
        raise GraphError("UNKNOWN_KIND %r -- one of %s" % (kind, ", ".join(EDGE_KINDS)), 2)
    if polarity not in (None, "", "+", "-"):
        raise GraphError("UNKNOWN_POLARITY %r -- '+' or '-'" % polarity, 2)
    polarity = polarity or None
    if polarity and kind not in POLARITY_KINDS:
        raise GraphError("POLARITY_NOT_ALLOWED: only %s carries a sign, not %s"
                         % ("/".join(POLARITY_KINDS), kind), 1)
    if not basis:
        raise GraphError("--basis is required: an edge without a stated basis is a guess", 2)
    edge = {"schema": EDGE_SCHEMA, "event": "link",
            "from": from_ro, "to": to_ro, "kind": kind, "polarity": polarity,
            "basis": basis, "by": by or "main", "at": at or utc_now()}
    commit = source_commit if source_commit is not None else head_commit(control)
    if commit:
        edge["source_commit"] = commit
    if source_fingerprint:
        edge["source_fingerprint"] = source_fingerprint
    if also_from:
        edge["also_from"] = list(also_from)
    edge["edge_id"] = edge_id_for(edge)
    return edge


def link(control: Path, from_ro: str, to_ro: str, kind: str, basis: str,
         by: str = "main", polarity=None) -> dict:
    """Add one edge.  BOTH endpoints must resolve; the refusal names which end.

    R9 B1: the short form ``ro::<space>:<id>`` is expanded against the frozen
    project id before it is booked -- what lands in the ledger is always a
    complete address.
    """
    ensure_project_id(control)
    from_ro = coerce_ro(from_ro, control)
    to_ro = coerce_ro(to_ro, control)
    unresolved = []
    for side, address in (("from", from_ro), ("to", to_ro)):
        result = resolve(control, address)
        if not result["exists"]:
            unresolved.append("%s=%s (%s)" % (side, address, result["reason"]))
    if unresolved:
        raise GraphError("UNRESOLVED_ENDPOINT: " + "; ".join(unresolved)
                         + " -- an edge may only join objects that exist", 1)
    edge = make_edge(control, from_ro, to_ro, kind, basis, by, polarity)
    existing = project_edges(control).get(edge["edge_id"])
    if existing and not existing.get("retracted"):
        return {"result": "ALREADY_LINKED", "edge": existing}
    append_events(control, [edge])
    payload = dict(edge)
    payload.pop("event", None)
    return {"result": "RELINKED" if existing else "LINKED", "edge": payload}


def retract(control: Path, edge_id: str, basis: str, by: str = "main") -> dict:
    """Append a retraction event (R9 B2).  Lines are never deleted or rewritten.

    R9 B4: a hand-written retraction may not cancel a derived edge.  A derived
    edge exists exactly as long as its source says so, and `derive` alone takes
    it back; a human who disagrees asserts the opposite edge and the pair shows
    up as ``EDGE_CONFLICT`` for someone to adjudicate.
    """
    if not basis:
        raise GraphError("--basis is required: a silent retraction is amnesia", 2)
    edges = project_edges(control)
    edge = edges.get(edge_id)
    if edge is None:
        raise GraphError("UNKNOWN_EDGE %s (%d edge(s) in the ledger)" % (edge_id, len(edges)), 1)
    if edge.get("retracted"):
        return {"result": "ALREADY_RETRACTED", "edge": edge}
    if (edge.get("by") or "") == "derive" and (by or "main") != "derive":
        raise GraphError(
            "DERIVED_EDGE_NOT_MANUALLY_RETRACTABLE %s was derived by %r: it lives exactly "
            "as long as its source says so and `graph derive` alone takes it back. To "
            "disagree, assert the opposite edge -- the pair surfaces as EDGE_CONFLICT."
            % (edge_id, edge.get("basis")), 1)
    at = utc_now()
    event = {"schema": RETRACT_SCHEMA, "event": "retract", "target_edge_id": edge_id,
             "event_id": sha256_text(canonical({"target_edge_id": edge_id, "at": at,
                                                "by": by or "main"}))[:12],
             "basis": basis, "by": by or "main", "at": at}
    append_events(control, [event])
    edge = dict(edge)
    edge["retracted"] = True
    edge["retraction"] = {"by": event["by"], "at": at, "basis": basis,
                          "event_id": event["event_id"]}
    return {"result": "RETRACTED", "edge": edge}


def unlink(control: Path, edge_id: str, reason: str, by: str = "main") -> dict:
    """Pre-R9 spelling of :func:`retract`, kept so callers do not break."""
    result = retract(control, edge_id, reason, by=by)
    if result["result"] == "RETRACTED":
        return dict(result, result="UNLINKED")
    return result


def list_edges(control: Path, from_ro=None, to_ro=None, kind=None,
               by=None, include_retracted=False) -> list:
    rows = []
    for edge in project_edges(control).values():
        if edge.get("retracted") and not include_retracted:
            continue
        if from_ro and edge.get("from") != coerce_ro(from_ro, control):
            continue
        if to_ro and edge.get("to") != coerce_ro(to_ro, control):
            continue
        if kind and edge.get("kind") != kind:
            continue
        if by and edge.get("by") != by:
            continue
        rows.append(edge)
    rows.sort(key=lambda e: (e.get("kind") or "", e.get("from") or "",
                             e.get("to") or "", e.get("edge_id") or ""))
    return rows


def active_edges(control: Path) -> list:
    return list_edges(control)


# ==================================== logical edges (R9 B4)

def logical_key(edge: dict) -> tuple:
    return (edge.get("from"), edge.get("to"), edge.get("kind"))


def logical_edges(control: Path, edges=None) -> list:
    """R9 B4: the *current effective relation*, not the raw assertions.

    Several sources may say the same thing for different reasons; the ledger
    keeps every one of them, but a view and a count must not see the relation
    three times.  Same ``(from, to, kind)`` and same sign => one logical edge
    carrying merged provenance.  Opposite signs => one logical edge marked
    ``conflict`` with both sides listed: the graph reports the disagreement, it
    does not resolve it.
    """
    rows = active_edges(control) if edges is None else list(edges)
    groups = {}
    for edge in rows:
        key = logical_key(edge)
        slot = groups.setdefault(key, {"from": key[0], "to": key[1], "kind": key[2],
                                       "provenance": [], "by_polarity": {}})
        polarity = edge.get("polarity") or None
        slot["provenance"].append({"edge_id": edge.get("edge_id"), "by": edge.get("by"),
                                   "basis": edge.get("basis"), "at": edge.get("at"),
                                   "polarity": polarity})
        slot["by_polarity"].setdefault(polarity, []).append(edge.get("edge_id"))
        for extra in edge.get("also_from") or []:
            slot["provenance"].append({"edge_id": edge.get("edge_id"), "by": "derive",
                                       "basis": extra, "at": edge.get("at"),
                                       "polarity": polarity})
    out = []
    for key in sorted(groups):
        slot = groups[key]
        signed = sorted(p for p in slot["by_polarity"] if p in POLARITIES)
        conflict = len(signed) > 1
        slot["conflict"] = conflict
        slot["polarity"] = None if conflict or not signed else signed[0]
        slot["sources"] = len(slot["provenance"])
        out.append(slot)
    return out


def conflicting_edges(control: Path, edges=None) -> list:
    return [row for row in logical_edges(control, edges) if row["conflict"]]

# ============================================================ v3 §0: the authoritative relation layer

_LOCKS_HELD = set()


class graph_lock:
    """The PROJECT write lock (v3 contract 0), made re-entrant inside one process.

    Contract 2 has the host CLIs call :func:`derive` in-process at the end of
    their own transaction -- and those CLIs are already inside
    ``research_os.hold_lock`` on ``.research-os/.lock``.  A second blocking
    acquisition would stall for the whole lock wait and then abort the host
    command, so a lock file that names THIS pid counts as already held and is
    left untouched on exit.  Every other writer queues exactly as before.
    """

    def __init__(self, control: Path):
        self.control = Path(control)
        self.key = str(self.control)
        self.inner = None

    def _held_by_me(self) -> bool:
        try:
            holder = json.loads((self.control / ".lock").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(holder, dict) and holder.get("pid") == os.getpid()

    def __enter__(self):
        if self.key in _LOCKS_HELD or self._held_by_me():
            return self
        inner = ros.hold_lock(self.control)
        try:
            inner.__enter__()
        except SystemExit as exc:                        # research_os.die -> SystemExit
            raise GraphError("GRAPH_LOCK_BUSY another session holds the write lock on %s; "
                             "retry shortly" % self.control, 1) from exc
        self.inner = inner
        _LOCKS_HELD.add(self.key)
        return self

    def __exit__(self, *exc):
        if self.inner is not None:
            _LOCKS_HELD.discard(self.key)
            self.inner.__exit__(*exc)
            self.inner = None
        return False


def journal_dir(control: Path) -> Path:
    return graph_dir(control) / "journal"


def current_path(control: Path) -> Path:
    return control / CURRENT_REL


def objects_path(control: Path) -> Path:
    return control / OBJECTS_REL


def snapshots_dir(control: Path) -> Path:
    return graph_dir(control) / "snapshots"


def derive_log_path(control: Path) -> Path:
    return graph_dir(control) / "derive.log"


def incidents_dir(control: Path) -> Path:
    return graph_dir(control) / "incidents"


def _fsync_write(path: Path, text: str) -> None:
    """temp file -> fsync -> atomic rename (contract 0).

    ``ros.atomic_write`` does the temp+rename half but not the fsync, and the
    whole point of the transaction is that a power cut leaves a file that is
    either entirely old or entirely new -- not a renamed-but-unflushed one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-graph-",
                                      suffix=path.suffix or ".tmp")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:                                  # pragma: no cover
            pass
        raise


def write_incident(control: Path, kind: str, detail=None) -> dict:
    """Contract 2: a derivation that fails inside a host transaction records an
    incident and returns -- it never propagates into the host's own write."""
    incident = {"schema": "auto-research/graph-incident-v1",
                "id": "gi_" + uuid.uuid4().hex[:12], "kind": str(kind),
                "at": utc_now(), "detail": detail or {}}
    try:
        path = incidents_dir(control) / ("%s_%s.json" % (incident["at"].replace(":", ""),
                                                         incident["id"]))
        _fsync_write(path, json.dumps(incident, ensure_ascii=False, indent=2) + "\n")
        incident["file"] = _rel(control, path)
    except BaseException:                                # pragma: no cover
        pass
    return incident


def open_incidents(control: Path) -> list:
    directory = incidents_dir(control)
    if not directory.exists():
        return []
    out = []
    for path in sorted(directory.glob("*.json")):
        body = read_json(path, None)
        if isinstance(body, dict):
            out.append(body)
    return out


# ---- event identity (schema v2; v1 rows stay readable)

def event_stamp(row: dict, line_no: int = 0) -> str:
    """The stable identity of ONE event line.

    v2 events carry ``event_id``.  v1 ``link`` rows predate it and are
    identified by their edge id, v1 ``retract`` rows already had one -- so a
    ledger written before this contract still yields a comparable tail marker
    and does not force a rewrite of history.
    """
    ident = row.get("event_id") or row.get("edge_id")
    return str(ident) if ident else ("line-%d" % (line_no or row.get("_line") or 0))


def stream_head(control: Path) -> tuple:
    """``(event_count, last_event_id)`` read straight off the event file.

    Deliberately not a projection: this is the cheap fact that tells a reader
    whether ``current.json`` is still the truth.
    """
    path = edges_path(control)
    if not path.exists():
        return 0, None
    count, last = 0, None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            count += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                row = {}
            last = event_stamp(row, count)
    except OSError:                                      # pragma: no cover
        return 0, None
    return count, last


def _next_event_id(previous, row: dict, index: int) -> str:
    """A hash chain: each event id covers the previous one, so a line silently
    removed from the middle of the ledger changes every id after it."""
    return "ge_" + sha256_text("%s|%d|%s" % (previous or "", index, canonical(row)))[:16]


def _stamp_rows(control: Path, rows: list) -> list:
    count, previous = stream_head(control)
    stamped = []
    for offset, row in enumerate(rows, 1):
        row = {k: v for k, v in row.items() if k != "_line"}
        row["schema"] = EVENT_SCHEMA_V2
        if not row.get("event_id"):
            row["event_id"] = _next_event_id(previous, row, count + offset)
        previous = row["event_id"]
        stamped.append(row)
    return stamped


def _write_stream(control: Path, stamped: list) -> None:
    """Append through the same temp+fsync+rename path as everything else.

    A plain ``open(mode="a")`` is a torn tail waiting to happen; rewriting the
    file wholesale costs a copy the size of the ledger and buys the property
    the contract asks for -- the event file is never observed half-written.
    """
    if not stamped:
        return
    path = edges_path(control)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    body = "\n".join(json.dumps(row, ensure_ascii=False) for row in stamped) + "\n"
    _fsync_write(path, existing + body)


# ---- journal

def _journal_write(control: Path, entry: dict) -> dict:
    entry["updated_at"] = utc_now()
    # `_file` is the reader's convenience, not part of the record.
    body = {k: v for k, v in entry.items() if not k.startswith("_")}
    _fsync_write(journal_dir(control) / ("%s.json" % entry["tx_id"]),
                 json.dumps(body, ensure_ascii=False, indent=2) + "\n")
    return entry


def journal_entries(control: Path) -> list:
    directory = journal_dir(control)
    if not directory.exists():
        return []
    out = []
    for path in sorted(directory.glob("tx_*.json")):
        body = read_json(path, None)
        if isinstance(body, dict) and body.get("tx_id"):
            body["_file"] = path
            out.append(body)
    return out


def _journal_prune(control: Path, keep: int = 20) -> None:
    done = [e for e in journal_entries(control) if e.get("status") in ("DONE", "ABORTED")]
    for entry in done[:-keep] if len(done) > keep else []:
        try:
            entry["_file"].unlink()
        except OSError:                                  # pragma: no cover
            pass


def _replay_journal(control: Path) -> list:
    """Finish a transaction interrupted by a crash (contract 0 / 8.1).

    The event file is the authority and ``current.json`` is a projection, so
    repair is mechanical and needs nobody to regenerate anything:

    ``OPEN``       nothing had been decided yet -> ABORTED.
    ``PREPARED``   the events were stamped and journalled but may not have
                   reached the file -> re-append the ones whose ``event_id``
                   is not in the stream (idempotent), then rebuild.
    ``COMMITTED``  the events landed, ``current.json`` may not have -> rebuild.
    """
    repaired = []
    entries = [e for e in journal_entries(control)
               if e.get("status") in ("OPEN", "PREPARED", "COMMITTED")]
    if not entries:
        return repaired
    for entry in entries:
        status = entry.get("status")
        if status == "OPEN":
            entry["status"] = "ABORTED"
            entry["repair"] = "nothing had been written when the process stopped"
            _journal_write(control, entry)
            repaired.append({"tx_id": entry["tx_id"], "result": "ABORTED"})
            continue
        rows = [r for r in (entry.get("events") or []) if isinstance(r, dict)]
        seen = {event_stamp(r, i) for i, r in enumerate(read_edge_events(control), 1)}
        missing = [r for r in rows if r.get("event_id") and r["event_id"] not in seen]
        if missing:
            _write_stream(control, missing)
        entry["status"] = "DONE"
        entry["repair"] = "replayed %d event(s), rebuilt current.json" % len(missing)
        _journal_write(control, entry)
        repaired.append({"tx_id": entry["tx_id"], "result": "REPLAYED",
                         "events": len(missing)})
    rebuild_current(control, trigger="journal-replay")
    _journal_prune(control)
    return repaired


class transaction:
    """One write to the relation layer = one transaction (contract 0).

    journal(OPEN) -> stamp the events -> journal(PREPARED, events inline) ->
    event file rewritten through temp+fsync+rename -> journal(COMMITTED) ->
    ``current.json`` rebuilt the same way -> journal(DONE).  Held inside the
    project write lock throughout, so no second window interleaves.

    Every mutating entry point in this module goes through it; nothing else may
    touch ``graph/`` (``hook_guard`` blocks hand edits, contract 8.1).
    """

    def __init__(self, control: Path, kind: str, detail=None, derive_note=None):
        self.control = Path(control)
        self.kind = str(kind)
        self.detail = detail or {}
        self.derive_note = derive_note
        self.rows = []
        self.lock = None
        self.entry = None
        self.current = None
        self.replayed = []

    def __enter__(self):
        self.lock = graph_lock(self.control)
        self.lock.__enter__()
        try:
            graph_dir(self.control).mkdir(parents=True, exist_ok=True)
            journal_dir(self.control).mkdir(parents=True, exist_ok=True)
            self.replayed = _replay_journal(self.control)
            count, last = stream_head(self.control)
            self.entry = _journal_write(self.control, {
                "schema": JOURNAL_SCHEMA, "tx_id": "tx_" + uuid.uuid4().hex[:12],
                "kind": self.kind, "status": "OPEN", "at": utc_now(),
                "session": os.environ.get("AR_SESSION") or "", "pid": os.getpid(),
                "base_event_count": count, "base_event_id": last,
                "detail": self.detail, "events": []})
        except BaseException:
            self.lock.__exit__(None, None, None)
            raise
        return self

    def add(self, rows) -> None:
        for row in rows or []:
            if isinstance(row, dict):
                self.rows.append(row)

    def commit(self) -> dict:
        stamped = _stamp_rows(self.control, self.rows)
        self.entry["events"] = stamped
        self.entry["status"] = "PREPARED"
        _journal_write(self.control, self.entry)
        _write_stream(self.control, stamped)
        self.entry["status"] = "COMMITTED"
        _journal_write(self.control, self.entry)
        self.current = rebuild_current(self.control, derive_note=self.derive_note,
                                       trigger=self.kind)
        self.entry["status"] = "DONE"
        self.entry["last_event_id"] = self.current.get("last_event_id")
        _journal_write(self.control, self.entry)
        _journal_prune(self.control)
        return self.current

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                if self.entry is not None and self.entry.get("status") == "OPEN":
                    self.entry["status"] = "ABORTED"
                    self.entry["repair"] = clip(exc, 200)
                    _journal_write(self.control, self.entry)
                return False
            self.commit()
        finally:
            self.lock.__exit__(None, None, None)
        return False


# ---- current.json: the materialised "current effective relations"

def _authors(edges: list) -> dict:
    out = {}
    for edge in edges:
        slot = out.setdefault(edge.get("by") or "main", {"n": 0, "last": ""})
        slot["n"] += 1
        slot["last"] = max(slot["last"], edge.get("at") or "")
    return out


def _objects_summary(control: Path) -> dict:
    index = read_json(objects_path(control), None)
    if not isinstance(index, dict) or not isinstance(index.get("objects"), dict):
        return {"built_at": None, "total": 0, "present": 0, "gone": 0, "by_space": {},
                "indexed": False}
    by_space, gone, present = {}, 0, 0
    for entry in index["objects"].values():
        if not isinstance(entry, dict):
            continue
        space = entry.get("space") or "?"
        slot = by_space.setdefault(space, {"total": 0, "gone": 0})
        slot["total"] += 1
        if entry.get("status") == "gone":
            slot["gone"] += 1
            gone += 1
        else:
            present += 1
    return {"built_at": index.get("built_at"), "total": len(index["objects"]),
            "present": present, "gone": gone, "by_space": by_space, "indexed": True}


def _last_derive(control: Path):
    path = derive_log_path(control)
    if not path.exists():
        return None
    try:
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:                                      # pragma: no cover
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and not row.get("dry_run"):
            return row
    return None


def build_current(control: Path, derive_note=None, trigger=None) -> dict:
    """Project the event stream into the one file every reader is allowed to
    read (contract 0).  Everything expensive -- resolving titles, reading the
    narrative snapshot for the coverage counters -- happens HERE, once per
    write, instead of in every view, every hook and every dispatch."""
    cache_clear(control)
    count, last = stream_head(control)
    assertions = project_edges(control)
    active = [e for e in assertions.values() if not e.get("retracted")]
    logical = []
    for row in logical_edges(control, active):
        row = dict(row)
        row.pop("by_polarity", None)
        logical.append(row)
    conflicts = [row for row in logical if row.get("conflict")]
    retracted = sorted((e for e in assertions.values() if e.get("retracted")),
                       key=lambda e: ((e.get("retraction") or {}).get("at") or "",
                                      e.get("edge_id") or ""))
    tail = [{"edge_id": e.get("edge_id"), "from": e.get("from"), "to": e.get("to"),
             "kind": e.get("kind"), "polarity": e.get("polarity"), "by": e.get("by"),
             "at": e.get("at"),
             "retracted_by": (e.get("retraction") or {}).get("by"),
             "retracted_at": (e.get("retraction") or {}).get("at"),
             "retraction_basis": clip((e.get("retraction") or {}).get("basis"), 80)}
            for e in retracted[-RETRACTED_TAIL:]]
    try:
        report = coverage_report(control, edges=logical)
        coverage = {"nodes": report["nodes"], "load_bearing": len(report["load_bearing"]),
                    "a_no_figure": len(report["no_figure"]),
                    "b_mute_figures": len(report["mute_figures"]),
                    "c_held_unsupported": len(report["held_unsupported"]),
                    "d_contested_active": len(report["contested_active"]),
                    "e_conflicts": len(report["conflicts"])}
    except BaseException:
        # A project whose narrative store is mid-repair must still get a
        # current.json: the relation layer does not depend on the story layer.
        coverage = None
    derive_row = derive_note if derive_note is not None else _last_derive(control)
    if isinstance(derive_row, dict):
        derive_block = {k: derive_row.get(k) for k in
                        ("at", "trigger", "rules", "added", "retracted", "ms", "since")}
    else:
        derive_block = None
    try:
        pending = pending_summary(control)
    except BaseException:                                # pragma: no cover
        pending = {"edges": 0, "actionable": 0, "total": 0, "by_status": {}}
    return {
        "schema": CURRENT_SCHEMA,
        "project": project_id(control),
        "built_at": utc_now(),
        "built_by": trigger or "rebuild",
        "last_event_id": last,
        "event_count": count,
        "source_commit": head_commit(control),
        "counts": {"events": count, "assertions": len(assertions), "active": len(active),
                   "logical": len(logical), "conflicts": len(conflicts),
                   "retracted": len(retracted)},
        "edges": logical,
        "conflicts": conflicts,
        "retracted": tail,
        "retracted_total": len(retracted),
        "authors": _authors(active),
        "objects": _objects_summary(control),
        "coverage": coverage,
        "pending": {"actionable": pending.get("actionable", 0),
                    "total": pending.get("total", 0),
                    "by_status": pending.get("by_status", {})},
        "derive": derive_block,
    }


def rebuild_current(control: Path, derive_note=None, trigger=None) -> dict:
    payload = build_current(control, derive_note=derive_note, trigger=trigger)
    _fsync_write(current_path(control),
                 json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def current_state(control: Path) -> tuple:
    """``(payload | None, stale)``.  READ-ONLY -- safe to call cross-project.

    ``stale`` means ``current.json`` disagrees with the event stream tail: the
    contract's ``GRAPH_REBUILD_REQUIRED`` condition, checked in constant time
    rather than by re-projecting the whole ledger.
    """
    payload = read_json(current_path(control), None)
    count, last = stream_head(control)
    if not isinstance(payload, dict) or payload.get("schema") != CURRENT_SCHEMA:
        return None, count > 0
    stale = (payload.get("last_event_id") != last
             or int(payload.get("event_count") or 0) != count)
    return payload, stale


def ensure_current(control: Path, allow_build: bool = True):
    """The reader-side entry point.  ``None`` when this project has no ledger.

    A MISSING ``current.json`` is materialised on the spot (a projection that
    was never built is not a disagreement -- this is what lets a v2.9 project
    keep working the moment it is upgraded).  A current.json that has fallen
    BEHIND is a different animal: something wrote events outside the
    transaction, so the reader is refused with ``GRAPH_REBUILD_REQUIRED``
    instead of being handed a stale answer.
    """
    payload, stale = current_state(control)
    if payload is not None and not stale:
        return payload
    if payload is None:
        if not edges_path(control).exists() or not allow_build:
            return None
        with transaction(control, "materialise"):
            pass
        return read_json(current_path(control), None)
    count, _last = stream_head(control)
    raise GraphError(
        "GRAPH_REBUILD_REQUIRED current.json is behind the event stream "
        "(materialised %s events, ledger has %d) -- run `graph rebuild`"
        % (payload.get("event_count"), count), 1,
        {"code": "GRAPH_REBUILD_REQUIRED", "current_event_count": payload.get("event_count"),
         "ledger_event_count": count})


def active_logical_edges(control: Path) -> list:
    """Contract 0: every view, the SessionStart line and the role context read
    ``current.json`` -- never the raw event file."""
    payload = ensure_current(control)
    return list((payload or {}).get("edges") or [])


def rebuild(control: Path, reason: str = "manual") -> dict:
    """``graph rebuild``: replay any interrupted transaction, then re-project.

    An empty transaction IS the rebuild -- entering replays the journal and
    committing re-materialises current.json -- so there is exactly one code
    path that writes this file.
    """
    ensure_project_id(control)
    with transaction(control, "rebuild", detail={"reason": reason}) as tx:
        pass
    return {"result": "REBUILT", "replayed": tx.replayed,
            "last_event_id": (tx.current or {}).get("last_event_id"),
            "event_count": (tx.current or {}).get("event_count"),
            "counts": (tx.current or {}).get("counts"),
            "current": _rel(control, current_path(control))}


# ---- snapshots

def snapshot(control: Path, label: str, keep=None, milestone: bool = False) -> dict:
    """Contract 0: freeze the three files of the relation layer side by side.

    ``--keep N`` rotates ordinary snapshots; one marked ``--milestone`` is
    never rotated away, because the reason to take it was that somebody will
    want it long after N more snapshots have happened.
    """
    label = ros.slugify(str(label or "").strip() or "snapshot")
    with graph_lock(control):
        _replay_journal(control)
        stamp = utc_now().replace(":", "").replace("-", "")
        target = snapshots_dir(control) / ("%s_%s" % (stamp, label))
        target.mkdir(parents=True, exist_ok=True)
        copied = []
        for source in (edges_path(control), objects_path(control), current_path(control)):
            if source.exists():
                _fsync_write(target / source.name, source.read_text(encoding="utf-8"))
                copied.append(source.name)
        count, last = stream_head(control)
        meta = {"schema": "auto-research/graph-snapshot-v1", "label": label,
                "at": utc_now(), "milestone": bool(milestone), "files": copied,
                "event_count": count, "last_event_id": last}
        _fsync_write(target / "meta.json",
                     json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        rotated = []
        if keep is not None:
            ordinary = []
            for directory in sorted(snapshots_dir(control).glob("*")):
                if not directory.is_dir():
                    continue
                info = read_json(directory / "meta.json", {}) or {}
                if not info.get("milestone"):
                    ordinary.append(directory)
            for directory in ordinary[:-int(keep)] if len(ordinary) > int(keep) else []:
                shutil.rmtree(str(directory), ignore_errors=True)
                rotated.append(directory.name)
    return {"result": "SNAPSHOT", "dir": _rel(control, target), "files": copied,
            "meta": meta, "rotated": rotated}


def snapshots(control: Path) -> list:
    directory = snapshots_dir(control)
    if not directory.exists():
        return []
    out = []
    for child in sorted(directory.glob("*")):
        if child.is_dir():
            info = read_json(child / "meta.json", {}) or {}
            info["dir"] = child.name
            out.append(info)
    return out


# ============================================================ v3 §1: the object index

def _space_source_files(control: Path, space: str) -> list:
    """The files whose change means "this space may have new objects".

    Fingerprinting these (name+size+mtime) is what makes ``index update
    --space`` cheap: an unchanged space is skipped without opening a single
    object.
    """
    root = project_root(control)
    if space == "narr":
        return [control / "narrative" / "refs" / "heads" / "main"]
    if space == "tree":
        return [control / "tree.json"]
    if space == "fig":
        return [control / FIGURES_REGISTRY_REL] + list(fsg_files(control))
    if space == "asset":
        return [control / "state.json"]
    if space == "run":
        directory = control / "runs"
        return sorted(directory.glob("*.json")) if directory.exists() else []
    if space == "claim":
        base = root / CLAIMS_REL
        return sorted(base.glob("*/spec.md")) if base.exists() else []
    if space == "ledger":
        return list(_ledger_sources(control))
    if space == "decision":
        directory = control / "narrative" / "decisions"
        return [control / "decisions.md"] + (sorted(directory.glob("*.json"))
                                             if directory.exists() else [])
    return []


def space_fingerprint(control: Path, space: str) -> str:
    parts = []
    for path in _space_source_files(control, space):
        try:
            stat = Path(path).stat()
            parts.append([_rel(control, path), stat.st_size, int(stat.st_mtime_ns)])
        except OSError:
            parts.append([_rel(control, path), None, None])
    if space == "narr":
        parts.append(["HEAD", head_commit(control), None])
    return sha256_text(canonical(sorted(parts, key=lambda p: str(p[0]))))[:16]


def space_fingerprints(control: Path) -> dict:
    return {space: space_fingerprint(control, space) for space in SPACES}


def enumerate_space(control: Path, space: str) -> list:
    """Every id that space currently contains.  Ids only -- the object itself
    comes from ``resolve`` so the index can never drift from resolution."""
    if space == "narr":
        return sorted(_narr_ids(control))
    if space == "tree":
        tree = load_tree(control)
        return sorted(tree["nodes"]) if tree else []
    if space == "fig":
        ids = {path.name.split(".fsg.json")[0] for path in fsg_files(control)}
        ids.update(figure_registry(control).keys())
        return sorted(ids)
    if space == "asset":
        out = []
        for position, entry in enumerate(load_state(control).get("artifacts") or []):
            if isinstance(entry, dict) and entry.get("id"):
                out.append(str(entry["id"]))
            else:
                out.append("#%d" % position)
        return sorted(set(out))
    if space == "run":
        directory = control / "runs"
        return sorted(p.stem for p in directory.glob("*.json")) if directory.exists() else []
    if space == "claim":
        base = project_root(control) / CLAIMS_REL
        return sorted(p.parent.name for p in base.glob("*/spec.md")) if base.exists() else []
    if space == "ledger":
        return sorted(ledger_index(control).keys())
    if space == "decision":
        ids = {block["id"] for block in decision_blocks(control)}
        directory = control / "narrative" / "decisions"
        if directory.exists():
            ids.update(p.stem for p in directory.glob("*.json"))
        return sorted(ids)
    return []


def _object_title(obj: dict) -> str:
    if not isinstance(obj, dict):
        return ""
    text = obj.get("text") or ""
    if isinstance(text, str) and text.startswith("|"):
        cells = [c.strip() for c in text.split("|") if c.strip()]
        text = cells[1] if len(cells) > 1 else text
    for key in ("title", "message", "semantic", "protocol", "legacy_text", "path", "content"):
        value = obj.get(key)
        if value:
            return clip(value, 90)
    return clip(text, 90)


def _object_status(space: str, obj: dict) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in ("status", "state", "verdict", "outcome", "epistemic"):
        value = obj.get(key)
        if isinstance(value, dict):
            value = value.get("epistemic") or value.get("narrative")
        if value:
            return clip(value, 40)
    return ""


def index_objects(control: Path, spaces=None, previous=None) -> dict:
    """Enumerate + resolve.  Returns ``{ro: entry}`` for the requested spaces."""
    pid = project_id(control)
    previous = previous if isinstance(previous, dict) else {}
    now = utc_now()
    out = {}
    for space in (spaces or SPACES):
        for ident in enumerate_space(control, space):
            address = format_ro(pid, space, ident)
            try:
                result = resolve(control, address)
            except GraphError:
                continue
            obj = result.get("object") or {}
            entry = {"space": space, "id": ident,
                     "title": _object_title(obj),
                     "status": _object_status(space, obj) or ("present" if result["exists"]
                                                              else "gone"),
                     "fingerprint": sha256_text(canonical(obj))[:16],
                     "source_path": result.get("file"),
                     "updated_at": now}
            if not result["exists"]:
                entry["status"] = "gone"
                entry["reason"] = clip(result.get("reason"), 120)
            old = previous.get(address)
            if isinstance(old, dict) and old.get("fingerprint") == entry["fingerprint"] \
                    and old.get("status") == entry["status"]:
                entry["updated_at"] = old.get("updated_at") or now
            out[address] = entry
    return out


def index_build(control: Path, spaces=None) -> dict:
    """``graph index build``: full sweep of all eight spaces."""
    ensure_project_id(control)
    cache_clear(control)
    spaces = list(spaces or SPACES)
    # Contract 0 names `index` among the single-transaction commands: leaving the
    # block re-projects current.json, whose object summary this write changed.
    with transaction(control, "index-build", detail={"spaces": spaces}):
        objects = index_objects(control, spaces)
        payload = {"schema": OBJECTS_SCHEMA, "project": project_id(control),
                   "built_at": utc_now(), "sources": space_fingerprints(control),
                   "objects": dict(sorted(objects.items()))}
        _fsync_write(objects_path(control),
                     json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return {"result": "INDEX_BUILT", "spaces": spaces, "objects": len(payload["objects"]),
            "by_space": _objects_summary(control)["by_space"],
            "file": _rel(control, objects_path(control))}


def index_update(control: Path, spaces=None, force: bool = False) -> dict:
    """``graph index update --space …``: re-scan only what changed.

    An object that has DISAPPEARED is marked ``gone`` and kept (contract 1) --
    deleting the row would erase the only record that an edge used to point at
    something real, which is exactly the audit trail derivation needs to
    retract that edge with a reason.
    """
    ensure_project_id(control)
    cache_clear(control)
    existing = read_json(objects_path(control), None)
    if not isinstance(existing, dict) or not isinstance(existing.get("objects"), dict):
        return index_build(control, spaces)
    wanted = list(spaces or SPACES)
    live = space_fingerprints(control)
    stored = existing.get("sources") or {}
    changed = [s for s in wanted if force or stored.get(s) != live.get(s)]
    skipped = [s for s in wanted if s not in changed]
    if not changed:
        return {"result": "INDEX_UNCHANGED", "scanned": [], "skipped": skipped,
                "objects": len(existing["objects"]), "gone": 0, "new": 0}
    with transaction(control, "index-update", detail={"spaces": changed}):
        objects = dict(existing["objects"])
        fresh = index_objects(control, changed, previous=existing["objects"])
        now = utc_now()
        new_ids, gone_ids = [], []
        for address, entry in fresh.items():
            if address not in objects:
                new_ids.append(address)
            objects[address] = entry
        for address, entry in list(objects.items()):
            if not isinstance(entry, dict) or entry.get("space") not in changed:
                continue
            if address in fresh:
                continue
            if entry.get("status") != "gone":
                entry["status"] = "gone"
                entry["gone_at"] = now
                entry["updated_at"] = now
                entry["reason"] = "no longer present in the %s store" % entry.get("space")
            gone_ids.append(address)
        sources = dict(stored)
        for space in changed:
            sources[space] = live.get(space)
        payload = {"schema": OBJECTS_SCHEMA, "project": project_id(control),
                   "built_at": now, "sources": sources,
                   "objects": dict(sorted(objects.items()))}
        _fsync_write(objects_path(control),
                     json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return {"result": "INDEX_UPDATED", "scanned": changed, "skipped": skipped,
            "objects": len(payload["objects"]), "new": len(new_ids),
            "gone": len(gone_ids), "gone_objects": gone_ids[:20],
            "file": _rel(control, objects_path(control))}


def load_objects(control: Path) -> dict:
    index = read_json(objects_path(control), None)
    if not isinstance(index, dict) or not isinstance(index.get("objects"), dict):
        return {}
    return index["objects"]


def gone_objects(control: Path) -> set:
    return {address for address, entry in load_objects(control).items()
            if isinstance(entry, dict) and entry.get("status") == "gone"}


# ==================================== role edge proposals (contract 5)

def pending_dir(control: Path) -> Path:
    return control / PENDING_REL


def policy_for(role: str) -> tuple:
    return EDGE_CLASS_POLICY.get(role) or ()


def policy_text(role: str) -> str:
    classes = policy_for(role)
    if not classes:
        return "role %r is assigned no edge class (role-fleet 5)" % role
    return ", ".join("%s->%s:%s" % triple for triple in classes)


def _prepare_proposal(control: Path, role: str, proposal, message_id: str) -> dict:
    """One proposed edge -> a normalised row plus a verdict.

    R9 B7 fixes the ORDER: the authorisation unit is
    ``(role, kind, from_space, to_space)`` and it is decided BEFORE the
    endpoints are resolved.  Otherwise a role could learn whether an object it
    has no business asserting about exists, and an out-of-column proposal for a
    not-yet-created object would park as ``pending`` and quietly link itself
    later.  ``verdict`` is one of ``ok`` (link it now), ``pending``
    (authorised, but an endpoint does not exist yet), ``denied`` (outside the
    role's column -- never parked) or ``invalid`` (malformed).
    """
    if not isinstance(proposal, dict):
        return {"verdict": "invalid", "reason": "proposal is not an object", "raw": clip(proposal)}
    raw_from = proposal.get("from") or proposal.get("from_ro")
    raw_to = proposal.get("to") or proposal.get("to_ro")
    kind = str(proposal.get("kind") or "").strip()
    row = {"from": raw_from, "to": raw_to, "kind": kind,
           "polarity": proposal.get("polarity") or None}
    if not raw_from or not raw_to:
        row.update({"verdict": "invalid", "reason": "an edge needs both `from` and `to`"})
        return row
    if kind not in EDGE_KINDS:
        row.update({"verdict": "invalid",
                    "reason": "unknown kind %r (one of %s)" % (kind, ", ".join(EDGE_KINDS))})
        return row
    try:
        from_ro = coerce_ro(str(raw_from), control)
        to_ro = coerce_ro(str(raw_to), control)
        from_parsed = parse_ro(from_ro, control)
        to_parsed = parse_ro(to_ro, control)
    except GraphError as exc:
        row.update({"verdict": "invalid", "reason": exc.text})
        return row
    row["from"], row["to"] = from_ro, to_ro
    if row["polarity"] and row["polarity"] not in POLARITIES:
        row.update({"verdict": "invalid", "reason": "polarity must be '+' or '-'"})
        return row
    if row["polarity"] and kind not in POLARITY_KINDS:
        row.update({"verdict": "invalid",
                    "reason": "only %s carries a sign" % "/".join(POLARITY_KINDS)})
        return row
    # ---- authorise first (R9 B7), on (role, kind, from_space, to_space) only.
    row["authorization_policy_version"] = AUTHORIZATION_POLICY_VERSION
    triple = (from_parsed["space"], to_parsed["space"], kind)
    if role not in EDGE_CLASS_POLICY:
        row.update({"verdict": "denied",
                    "reason": "EDGE_CLASS_DENIED unknown role %r -- a proposal is only "
                              "authorised through a trusted role receipt; the CLI writes "
                              "as by=main" % role})
        return row
    if triple not in policy_for(role):
        row.update({"verdict": "denied",
                    "reason": "EDGE_CLASS_DENIED %s->%s:%s is not %s's to assert; allowed: %s"
                              % (triple[0], triple[1], triple[2], role, policy_text(role))})
        return row
    basis = str(proposal.get("basis") or "").strip()
    row["basis"] = ("%s | msg=%s" % (basis, message_id)) if basis else ("msg=%s" % message_id)
    # ---- only now may we look at whether the endpoints exist.
    for side, address in (("from", from_ro), ("to", to_ro)):
        probe = resolve(control, address)
        if not probe["exists"]:
            row.update({"verdict": "pending",
                        "reason": "%s=%s does not exist yet (%s)"
                                  % (side, address, probe["reason"])})
            return row
    row["verdict"] = "ok"
    return row


# ---- R9 B8: PENDING -> RESOLVED | DENIED | STALE

def _age_days(stamp) -> float:
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400.0


def _seed_pending_row(row: dict, at: str) -> dict:
    row = dict(row)
    row.setdefault("status", "PENDING")
    row.setdefault("attempts", 1)
    row.setdefault("first_seen", at)
    row.setdefault("last_attempt", at)
    row.setdefault("authorization_policy_version", AUTHORIZATION_POLICY_VERSION)
    return row


def pending_row_status(row: dict) -> str:
    """The row's live state.  A park that nobody can satisfy is not a standing
    obligation: three failed replays or 30 days without the object appearing
    turns it ``STALE`` -- kept for audit, dropped from the actionable count, so
    the SessionStart line stops nagging about a proposal that will never land."""
    status = str(row.get("status") or "PENDING").upper()
    if status != "PENDING":
        return status if status in PENDING_STATES else "PENDING"
    if int(row.get("attempts") or 0) >= PENDING_MAX_ATTEMPTS:
        return "STALE"
    if _age_days(row.get("first_seen")) >= PENDING_STALE_DAYS:
        return "STALE"
    return "PENDING"


def _write_pending(control: Path, role: str, message_id: str, rows: list) -> str:
    directory = pending_dir(control)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"schema": PENDING_SCHEMA, "message_id": message_id, "role": role,
               "at": utc_now(), "authorization_policy_version": AUTHORIZATION_POLICY_VERSION,
               "edges": rows}
    path = directory / ("%s.json" % message_id)
    ros.atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return _rel(control, path)


def submit_edges(control: Path, role: str, message_id: str, proposals) -> dict:
    """Contract 5 / R9 B7-B8: dispose of one receipt's ``proposed_edges[]``.

    ``role`` must come from the trusted receipt the caller already validated
    (role_runtime passes the dispatch's role; a direct CLI writer is ``main``
    and owns no edge class).  Authorised and resolvable -> written now.
    Authorised but an endpoint does not exist yet -> parked with a lifecycle.
    Unauthorised -> ``DENIED`` and dropped: a denied proposal never enters the
    pending queue, so it can never link itself later.
    """
    proposals = proposals or []
    if not isinstance(proposals, list):
        return {"proposed": 0, "linked": [], "pending": [], "denied": [],
                "invalid": [{"reason": "proposed_edges must be a list"}],
                "pending_file": None, "flags": ["EDGE_PROPOSAL_MALFORMED"],
                "authorization_policy_version": AUTHORIZATION_POLICY_VERSION}
    linked, pending, denied, invalid = [], [], [], []
    at = utc_now()
    for proposal in proposals:
        row = _prepare_proposal(control, role, proposal, message_id)
        verdict = row.pop("verdict")
        if verdict == "ok":
            result = link(control, row["from"], row["to"], row["kind"], row["basis"],
                          by=role, polarity=row.get("polarity"))
            linked.append({"edge_id": result["edge"]["edge_id"], "result": result["result"],
                           "from": row["from"], "to": row["to"], "kind": row["kind"],
                           "polarity": row.get("polarity")})
        elif verdict == "pending":
            pending.append(_seed_pending_row(row, at))
        elif verdict == "denied":
            row["status"] = "DENIED"
            denied.append(row)
        else:
            invalid.append(row)
    pending_file = _write_pending(control, role, message_id, pending) if pending else None
    flags = []
    if denied:
        flags.append("EDGE_CLASS_DENIED")
    if invalid:
        flags.append("EDGE_PROPOSAL_MALFORMED")
    if pending:
        flags.append("EDGE_PENDING")
    return {"proposed": len(proposals), "linked": linked, "pending": pending,
            "denied": denied, "invalid": invalid, "pending_file": pending_file,
            "flags": flags,
            "authorization_policy_version": AUTHORIZATION_POLICY_VERSION}


def pending_files(control: Path) -> list:
    directory = pending_dir(control)
    if not directory.exists():
        return []
    return sorted(directory.glob("*.json"))


def pending_summary(control: Path) -> dict:
    """``edges`` counts only what is still ACTIONABLE (status PENDING).  DENIED
    and STALE rows stay on disk for audit and out of every nag line."""
    files, actionable, total, by_role = 0, 0, 0, {}
    by_status = {}
    for path in pending_files(control):
        payload = read_json(path, None)
        if not isinstance(payload, dict):
            continue
        rows = [r for r in (payload.get("edges") or []) if isinstance(r, dict)]
        files += 1
        total += len(rows)
        role = payload.get("role") or "?"
        for row in rows:
            status = pending_row_status(row)
            by_status[status] = by_status.get(status, 0) + 1
            if status == "PENDING":
                actionable += 1
                by_role[role] = by_role.get(role, 0) + 1
    return {"files": files, "edges": actionable, "actionable": actionable,
            "total": total, "by_role": by_role, "by_status": by_status}


def replay_pending(control: Path) -> dict:
    """Retry every parked proposal under the CURRENT authorisation policy (R9
    B8): the rules may have changed since the proposal was made, so a park is
    re-judged, never grandfathered.  What links is cleared, what is refused now
    becomes ``DENIED``, and what nobody can satisfy ages out to ``STALE``."""
    cache_clear(control)
    linked, still, cleared, stale, denied = [], [], [], [], []
    now = utc_now()
    for path in pending_files(control):
        payload = read_json(path, None)
        if not isinstance(payload, dict):
            continue
        role = payload.get("role") or "main"
        message_id = payload.get("message_id") or path.name[:-5]
        keep = []
        for row in payload.get("edges") or []:
            if not isinstance(row, dict):
                continue
            status = pending_row_status(row)
            if status in ("DENIED", "STALE"):
                keep.append(dict(row, status=status))
                (denied if status == "DENIED" else stale).append(
                    dict(row, status=status, message_id=message_id, role=role))
                continue
            retry = _prepare_proposal(control, role, row, message_id)
            verdict = retry.pop("verdict")
            attempts = int(row.get("attempts") or 0) + 1
            if verdict == "ok":
                result = link(control, retry["from"], retry["to"], retry["kind"],
                              row.get("basis") or retry["basis"], by=role,
                              polarity=retry.get("polarity"))
                linked.append({"message_id": message_id, "role": role,
                               "edge_id": result["edge"]["edge_id"],
                               "result": result["result"], "from": retry["from"],
                               "to": retry["to"], "kind": retry["kind"],
                               "status": "RESOLVED"})
                continue
            retry["basis"] = row.get("basis") or retry.get("basis")
            retry["attempts"] = attempts
            retry["first_seen"] = row.get("first_seen") or now
            retry["last_attempt"] = now
            retry["authorization_policy_version"] = AUTHORIZATION_POLICY_VERSION
            if verdict == "denied":
                retry["status"] = "DENIED"
                denied.append(dict(retry, message_id=message_id, role=role))
            else:
                retry["status"] = pending_row_status(dict(retry, status="PENDING"))
                if retry["status"] == "STALE":
                    stale.append(dict(retry, message_id=message_id, role=role))
                else:
                    still.append(dict(retry, message_id=message_id, role=role))
            keep.append(retry)
        if keep:
            _write_pending(control, role, message_id, keep)
        else:
            cleared.append(message_id)
            try:
                path.unlink()
            except OSError:                              # pragma: no cover
                pass
    return {"linked": linked, "still_pending": still, "denied": denied, "stale": stale,
            "cleared": cleared, "remaining": pending_summary(control),
            "authorization_policy_version": AUTHORIZATION_POLICY_VERSION}




# ============================================================ derivation

def map_polarity(table: dict, status) -> str:
    """R9 B5: look the status up in an exhaustive table.  Anything the table
    does not list returns ``NO_EDGE`` -- the rule then writes nothing, rather
    than guessing a sign out of a free-text result field."""
    key = str(status or "").strip().lower().replace(" ", "-").replace("_", "-")
    if key in table:
        return table[key]
    key = key.replace("-", "_")
    return table.get(key, NO_EDGE)


def fingerprint(source: dict) -> str:
    """R9 B3: the content hash of the source object that states this relation.
    When the source changes (HELD -> KILLED, a claim_id removed) the
    fingerprint moves, so reconciliation can see the old edge is no longer
    wanted instead of leaving it standing forever."""
    return sha256_text(canonical(source))[:16]


def _narr_ids(control: Path) -> set:
    snapshot = head_snapshot(control)
    return set((snapshot or {}).get("nodes", {}).keys())


def _claims_by_tree_code(control: Path) -> dict:
    """``tree_code -> claim_id``, straight out of the spec front matter that
    procedures.py writes.  An exact back-reference, never a text guess."""
    def build():
        base = project_root(control) / CLAIMS_REL
        if not base.exists():
            return {}
        out = {}
        for spec in sorted(base.glob("*/spec.md")):
            front, _body = parse_front_matter(spec)
            code = str(front.get("tree_code") or "").strip()
            if code:
                out[code] = spec.parent.name
        return out

    return _cache(control, "claims_by_tree_code", build)


def _rule_tree_close(control: Path, pid: str) -> tuple:
    """Closed attempt-tree nodes -> ``supported_by``, signed by the terminal
    status: to the narrative node their ``claim``/``result`` names, and to the
    formal claim whose spec records this exact ``tree_code``."""
    tree = load_tree(control)
    if tree is None:
        return [], "tree-close: no .research-os/tree.json"
    ids = _narr_ids(control)
    by_code = _claims_by_tree_code(control)
    terminal = {"success", "failed", "proven", "refuted", "blocked",
                "abandoned", "superseded", "inconclusive"}
    out, closed = [], 0
    for code in sorted(tree["nodes"]):
        node = tree["nodes"][code]
        if (node.get("status") or "") not in terminal:
            continue
        closed += 1
        status = str(node.get("status") or "").strip().lower()
        polarity = map_polarity(TREE_STATUS_POLARITY, status)
        if polarity == NO_EDGE:
            continue
        text_fields = [str(node.get("claim") or ""), str(node.get("result") or ""),
                       str(node.get("title") or "")]
        text_fields += [str(t) for t in (node.get("tags") or [])]
        text_fields += [str(e) for e in (node.get("evidence") or [])]
        found = []
        for text in text_fields:
            for ident in NODE_ID_RE.findall(text):
                if ident in ids and ident not in found:
                    found.append(ident)
        for ident in found:
            out.append({"from": format_ro(pid, "tree", code),
                        "to": format_ro(pid, "narr", ident),
                        "kind": "supported_by", "polarity": polarity,
                        "basis": "derive:tree-close",
                        "source": {"rule": "tree-close", "code": code,
                                   "status": status, "target": "narr:%s" % ident}})
        if code in by_code:
            out.append({"from": format_ro(pid, "tree", code),
                        "to": format_ro(pid, "claim", by_code[code]),
                        "kind": "supported_by", "polarity": polarity,
                        "basis": "derive:tree-close",
                        "source": {"rule": "tree-close", "code": code, "status": status,
                                   "target": "claim:%s" % by_code[code]}})
    if not out:
        if not closed:
            return [], "tree-close: %d tree node(s), none closed yet" % len(tree["nodes"])
        if not ids and not by_code:
            return [], ("tree-close: %d closed node(s) but NO NARRATIVE ENDPOINT -- this "
                        "project has no narrative store and no claim spec carries a "
                        "tree_code; a tree->narr edge has nothing to point at" % closed)
        return [], ("tree-close: %d closed node(s), none naming a live narrative node or a "
                    "registered claim" % closed)
    return out, None


def _rule_fsg_claim(control: Path, pid: str) -> tuple:
    """FSG panels/elements carrying ``claim_id`` / ``claim`` -> ``expressed_as``
    (fig -> narr)."""
    files = fsg_files(control)
    if not files:
        return [], "fig: no %s/*.fsg.json" % FSG_REL
    ids = _narr_ids(control)
    out = []
    for path in files:
        fsg = read_json(path, None)
        if not isinstance(fsg, dict):
            continue
        figure_id = ((fsg.get("figure") or {}).get("id")
                     or path.name.split(".fsg.json")[0])
        for group in ("panels", "nodes", "texts"):
            for entry in fsg.get(group) or []:
                if not isinstance(entry, dict):
                    continue
                raw = entry.get("claim_id") or entry.get("claim")
                if not raw:
                    continue
                for ident in NODE_ID_RE.findall(str(raw)) or []:
                    if ids and ident not in ids:
                        continue
                    member = entry.get("id")
                    source = "%s.%s" % (figure_id, member) if member else figure_id
                    out.append({"from": format_ro(pid, "fig", source),
                                "to": format_ro(pid, "narr", ident),
                                "kind": "expressed_as", "polarity": None,
                                "basis": "derive:fsg-claim",
                                "source": {"rule": "fsg-claim", "figure": figure_id,
                                           "member": member, "claim": ident}})
    if not out:
        return [], ("fig: %d .fsg.json program(s), none carrying a claim_id/claim on a panel "
                    "or element that names a live narrative node" % len(files))
    return out, None


def _rule_asset_node(control: Path, pid: str) -> tuple:
    """Asset-index entries whose ``produced_by``/``tags`` name a narrative node
    -> ``depends_on`` (narr -> asset)."""
    artifacts = load_state(control).get("artifacts") or []
    if not artifacts:
        return [], "asset: state.artifacts is empty"
    typed = [a for a in artifacts if isinstance(a, dict)]
    if not typed:
        return [], ("asset: %d artifact(s) are legacy plain strings with no "
                    "produced_by/tags fields" % len(artifacts))
    ids = _narr_ids(control)
    out = []
    for entry in typed:
        asset_id = str(entry.get("id") or entry.get("path") or "")
        if not asset_id:
            continue
        texts = [str(entry.get("produced_by") or "")]
        texts += [str(t) for t in (entry.get("tags") or [])]
        found = []
        for text in texts:
            for ident in NODE_ID_RE.findall(text):
                if ident in ids and ident not in found:
                    found.append(ident)
        for ident in found:
            out.append({"from": format_ro(pid, "narr", ident),
                        "to": format_ro(pid, "asset", asset_id),
                        "kind": "depends_on", "polarity": None,
                        "basis": "derive:asset-node",
                        "source": {"rule": "asset-node", "asset": asset_id,
                                   "node": ident,
                                   "produced_by": str(entry.get("produced_by") or ""),
                                   "tags": [str(t) for t in (entry.get("tags") or [])]}})
    if not out:
        if not ids:
            return [], ("asset: %d typed artifact(s) but no narrative HEAD -- an asset edge "
                        "has no node to hang from" % len(typed))
        return [], ("asset: %d typed artifact(s), none whose produced_by/tags names a live "
                    "narrative node" % len(typed))
    return out, None


def _rule_narr_basis(control: Path, pid: str) -> tuple:
    """Narrative ``basis_refs`` naming an ``E-``/``D-``/``R-`` ledger row or a
    full ``ro:`` address -> ``supported_by`` (basis -> narr).

    Sign (interpretive, and stated so a reader can discount it): the contract
    (section 4) already requires a HELD node's basis to be its supporting basis
    and a KILLED node's basis to be a qualifying refutation, so HELD => ``+``,
    KILLED => ``-``, PENDING => unsigned.
    """
    snapshot = head_snapshot(control)
    if snapshot is None:
        return [], "narr: no narrative store"
    ids = set(snapshot.get("nodes", {}).keys())
    sign = {"HELD": "+", "KILLED": "-"}
    out = []
    for node_id in sorted(ids):
        node = snapshot["nodes"][node_id]
        epistemic = ((node.get("state") or {}).get("epistemic") or "").upper()
        polarity = sign.get(epistemic)
        for ref in node.get("basis_refs") or []:
            if not isinstance(ref, str):
                continue
            ref = ref.strip()
            if ref.startswith("ro:"):
                try:
                    parsed = parse_ro(ref, control)
                except GraphError:
                    continue
                source = format_ro(parsed["project"], parsed["space"], parsed["id"])
            elif LEDGER_ID_RE.match(ref):
                space = "narr" if ref in ids else "ledger"
                source = format_ro(pid, space, ref)
            else:
                continue
            target = format_ro(pid, "narr", node_id)
            if source == target:
                continue
            out.append({"from": source, "to": target, "kind": "supported_by",
                        "polarity": polarity, "basis": "derive:narr-basis",
                        "source": {"rule": "narr-basis", "node": node_id,
                                   "epistemic": epistemic, "ref": ref}})
    return out, None


def _rule_fig_registry(control: Path, pid: str) -> tuple:
    """Registered figure instances at ``status=current`` whose ``claim_ids``
    name a narrative node -> ``expressed_as`` (fig -> narr).

    Only ``current`` counts: a draft is not yet how the reader sees the claim,
    and a superseded instance expressed it in a version that no longer ships.
    """
    registry = figure_registry(control)
    if not registry:
        return [], "fig-registry: no .research-os/%s" % FIGURES_REGISTRY_REL
    current = registry_figures(control, "current")
    if not current:
        return [], ("fig-registry: %d registered figure(s), none at status=current"
                    % len(registry))
    ids = _narr_ids(control)
    out, carrying = [], 0
    for figure_id in current:
        raw_ids = registry[figure_id].get("claim_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            continue
        carrying += 1
        found = []
        for raw in raw_ids:
            for ident in NODE_ID_RE.findall(str(raw)):
                if ids and ident not in ids:
                    continue
                if ident not in found:
                    found.append(ident)
        for ident in found:
            out.append({"from": format_ro(pid, "fig", figure_id),
                        "to": format_ro(pid, "narr", ident),
                        "kind": "expressed_as", "polarity": None,
                        "basis": "derive:fig-registry",
                        "source": {"rule": "fig-registry", "figure": figure_id,
                                   "status": "current", "claim": ident,
                                   "render": str(registry[figure_id].get("current_render")
                                                 or "")}})
    if not carrying:
        return [], ("fig-registry: %d current figure(s), none carrying claim_ids[]"
                    % len(current))
    return out, None


def _rule_run_claim(control: Path, pid: str) -> tuple:
    """Settled runs carrying ``claims[]`` -> ``supported_by`` (run -> claim/narr),
    signed by ``outcome``.

    A run still in flight is deliberately skipped: an unsigned support edge from
    an unfinished run reads like evidence and is not.
    """
    directory = control / "runs"
    if not directory.exists():
        return [], "run-claim: no .research-os/runs/ (no run ever registered)"
    files = sorted(directory.glob("*.json"))
    if not files:
        return [], "run-claim: .research-os/runs/ is empty"
    ids = _narr_ids(control)
    out, carrying, in_flight = [], 0, 0
    unmapped = []
    for path in files:
        run = read_json(path, None)
        if not isinstance(run, dict):
            continue
        claims = run.get("claims")
        if not isinstance(claims, list) or not claims:
            continue
        carrying += 1
        outcome = run.get("outcome")
        if isinstance(outcome, dict):
            outcome_status = outcome.get("status") or outcome.get("verdict")
            outcome_text = "%s %s" % (outcome.get("summary") or "", outcome.get("result") or "")
        else:
            outcome_status, outcome_text = outcome, ""
        status = str(run.get("status") or "").strip().lower()
        if outcome_status is None and status not in ("done", "failed"):
            in_flight += 1
            continue
        vocabulary = outcome_status or status
        polarity = map_polarity(RUN_OUTCOME_POLARITY, vocabulary)
        if polarity == NO_EDGE:
            unmapped.append("%s outcome=%r" % (path.name, vocabulary))
            continue
        run_id = str(run.get("run_id") or path.name[:-5])
        seen = []
        for raw in claims:
            ref = str(raw).strip()
            if not ref or ref in seen:
                continue
            seen.append(ref)
            if ref.startswith("ro:"):
                try:
                    parsed = parse_ro(ref, control)
                except GraphError:
                    continue
                target = format_ro(parsed["project"], parsed["space"], parsed["id"])
            elif ref in ids:
                target = format_ro(pid, "narr", ref)
            else:
                target = format_ro(pid, "claim", ref)
            out.append({"from": format_ro(pid, "run", run_id), "to": target,
                        "kind": "supported_by", "polarity": polarity,
                        "basis": "derive:run-claim",
                        "source": {"rule": "run-claim", "run": run_id,
                                   "outcome": vocabulary, "target": target}})
    if not carrying:
        return [], "run-claim: %d run(s), none carrying claims[]" % len(files)
    if not out and unmapped:
        return [], ("run-claim: %d settled run(s) report an outcome outside the polarity "
                    "table, so no sign may be assumed: %s"
                    % (len(unmapped), "; ".join(unmapped[:4])))
    if not out and in_flight:
        return [], ("run-claim: %d run(s) carry claims[] but none has settled "
                    "(no outcome, status not done/failed)" % in_flight)
    return out, None


def _rule_claim_verdict(control: Path, pid: str) -> tuple:
    """Adjudicated formal claims -> ``supported_by`` (claim -> narr).

    procedures.py's five verdicts: ``proven`` => ``+``, ``refuted`` => ``-``;
    ``conditional``/``barrier``/``equivalent_core`` are real outcomes but not a
    sign, so they stay unsigned instead of being rounded to one.
    """
    base = project_root(control) / CLAIMS_REL
    if not base.exists():
        return [], "claim-verdict: no %s/ (no formal claim ever opened)" % CLAIMS_REL
    specs = sorted(base.glob("*/spec.md"))
    if not specs:
        return [], "claim-verdict: %s/ holds no <id>/spec.md" % CLAIMS_REL
    ids = _narr_ids(control)
    out, adjudicated, unmapped = [], 0, []
    for spec in specs:
        claim_id = spec.parent.name
        front, body = parse_front_matter(spec)
        verdict = str(front.get("verdict") or "").strip().lower()
        if not verdict:
            continue
        adjudicated += 1
        polarity = map_polarity(CLAIM_VERDICT_POLARITY, verdict)
        if polarity == NO_EDGE:
            unmapped.append("%s verdict=%r" % (claim_id, verdict))
            continue
        texts = [str(front.get(key) or "") for key in
                 ("narrative_node", "narr_node", "node", "narrative", "verdict_result")]
        texts.append(body)
        found = []
        for text in texts:
            for ident in NODE_ID_RE.findall(text):
                if ident in ids and ident not in found:
                    found.append(ident)
        for ident in found:
            out.append({"from": format_ro(pid, "claim", claim_id),
                        "to": format_ro(pid, "narr", ident),
                        "kind": "supported_by", "polarity": polarity,
                        "basis": "derive:claim-verdict",
                        "source": {"rule": "claim-verdict", "claim": claim_id,
                                   "verdict": verdict, "node": ident}})
    if not adjudicated:
        return [], ("claim-verdict: %d claim(s), none adjudicated (no verdict in the "
                    "front matter)" % len(specs))
    if not out and unmapped:
        return [], ("claim-verdict: %d adjudicated claim(s) carry a verdict outside the "
                    "five-state table, so no edge is produced: %s"
                    % (len(unmapped), "; ".join(unmapped[:4])))
    if not out:
        return [], ("claim-verdict: %d adjudicated claim(s), none naming a live narrative "
                    "node" % adjudicated)
    return out, None


def _rule_lineage(control: Path, pid: str) -> tuple:
    """Narrative ``lineage[{rel, of}]`` -> ``evolved_from`` (new -> old).

    The ancestor may already be gone from HEAD (that is what a replacement
    means); the edge is still emitted and the views mark it ``[MISSING]``,
    because a lineage pointing at nothing is exactly the fact worth seeing.
    """
    snapshot = head_snapshot(control)
    if snapshot is None:
        return [], "lineage: no narrative store"
    nodes = snapshot.get("nodes") or {}
    out = []
    for node_id in sorted(nodes):
        for entry in nodes[node_id].get("lineage") or []:
            if not isinstance(entry, dict):
                continue
            ancestor = str(entry.get("of") or "").strip()
            if not ancestor or ancestor == node_id:
                continue
            rel = str(entry.get("rel") or "").strip() or "unspecified"
            out.append({"from": format_ro(pid, "narr", node_id),
                        "to": format_ro(pid, "narr", ancestor),
                        "kind": "evolved_from", "polarity": None,
                        "basis": "derive:lineage rel=%s" % rel,
                        "source": {"rule": "lineage", "node": node_id,
                                   "ancestor": ancestor, "rel": rel}})
    if not out:
        return [], "lineage: no node carries a lineage[] entry"
    return out, None


# Contract 3, in table order.
DERIVE_RULES = (("narr-basis", _rule_narr_basis),
                ("tree-close", _rule_tree_close),
                ("fsg-claim", _rule_fsg_claim),
                ("fig-registry", _rule_fig_registry),
                ("asset-node", _rule_asset_node),
                ("run-claim", _rule_run_claim),
                ("claim-verdict", _rule_claim_verdict),
                ("lineage", _rule_lineage))


def _rule_of(basis: str) -> str:
    """``derive:lineage rel=narrows`` -> ``lineage``."""
    text = str(basis or "")
    if not text.startswith("derive:"):
        return ""
    return text[len("derive:"):].split(" ", 1)[0].strip()


def _fig_base(address: str) -> str:
    """``ro:p:fig:F01.E01`` -> ``F01``: the figure, not the element inside it."""
    parsed = RO_RE.match(address or "")
    return parsed.group(3).split(".")[0] if parsed else (address or "")


def _dedupe_figure_rules(by_rule: dict) -> list:
    """R9 B5: ``fsg-claim`` and ``fig-registry`` can both say *this figure
    expresses this claim*.  That is ONE logical relation, so the registry wins
    (it is the v2.9 instance ledger and knows which render actually ships) and
    the FSG program is appended to the surviving edge's provenance instead of
    becoming a second edge that has to be counted, retracted and reconciled
    separately."""
    registry_rows = by_rule.get("fig-registry") or []
    fsg_rows = by_rule.get("fsg-claim") or []
    if not registry_rows or not fsg_rows:
        return []
    index = {}
    for row in registry_rows:
        index.setdefault((_fig_base(row["from"]), row["to"], row["kind"]), row)
    kept, dropped = [], []
    for row in fsg_rows:
        key = (_fig_base(row["from"]), row["to"], row["kind"])
        winner = index.get(key)
        if winner is None:
            kept.append(row)
            continue
        note = "derive:fsg-claim %s" % _short(row["from"])
        extra = winner.setdefault("also_from", [])
        if note not in extra:
            extra.append(note)
            extra.sort()
        dropped.append({"suppressed": row["from"], "in_favour_of": winner["from"],
                        "to": row["to"]})
    by_rule["fsg-claim"] = kept
    return dropped


def _derive_log_append(control: Path, row: dict) -> None:
    try:
        path = derive_log_path(control)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:                                      # pragma: no cover
        pass


def derive_log(control: Path, limit=20) -> list:
    path = derive_log_path(control)
    if not path.exists():
        return []
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:                                      # pragma: no cover
        return []
    return rows if limit is None else rows[-int(limit):]


def _since_fingerprints(control: Path, since: str):
    """The space fingerprints recorded by the derive that produced ``since``."""
    for row in reversed(derive_log(control, limit=None)):
        if not isinstance(row, dict):
            continue
        if since in (row.get("last_event_id"), row.get("derive_id")):
            return row.get("space_fingerprints") or {}
    return None


def _select_rules(rules) -> list:
    names = [name for name, _ in DERIVE_RULES]
    if not rules:
        return list(names)
    requested = [str(r).strip() for r in
                 (rules.split(",") if isinstance(rules, str) else rules) if str(r).strip()]
    unknown = [r for r in requested if r not in names]
    if unknown:
        raise GraphError("UNKNOWN_RULE %s -- one of %s"
                         % (", ".join(unknown), ", ".join(names)), 2)
    chosen = set(requested)
    if chosen & {"fsg-claim", "fig-registry"}:
        # 3.3 dedupes these two against each other; running one without the
        # other would let the suppressed rule re-assert the relation as a
        # second edge.  They are one rule with two sources.
        chosen |= {"fsg-claim", "fig-registry"}
    return [name for name in names if name in chosen]


def _derive_core(control: Path, rules, reason, since, dry_run, started) -> dict:
    cache_clear(control)
    pid = ensure_project_id(control)
    at = utc_now()
    commit = head_commit(control)
    selected = _select_rules(rules)
    live_fingerprints = space_fingerprints(control)

    since_note, skipped_unchanged = None, []
    if since:
        base = _since_fingerprints(control, since)
        if base is None:
            since_note = "since=%s is not in derive.log: every selected rule ran" % since
        else:
            since_note = "since=%s" % since
            fresh_rules = []
            for name in selected:
                sources = RULE_SOURCES.get(name) or tuple(SPACES)
                if all(base.get(space) == live_fingerprints.get(space) for space in sources):
                    skipped_unchanged.append(name)
                else:
                    fresh_rules.append(name)
            selected = fresh_rules

    if not dry_run:
        # Contract 1: reconciliation reads `gone` off the object index, so the
        # index has to be current BEFORE the rules are compared against it.
        index_update(control)
        cache_clear(control)

    by_rule, missing = {}, []
    for name, rule in DERIVE_RULES:
        if name not in selected:
            continue
        candidates, gap = rule(control, pid)
        if gap:
            missing.append(gap)
        by_rule[name] = list(candidates)
    deduped = _dedupe_figure_rules(by_rule)

    standing = {}
    for edge in active_edges(control):
        if (edge.get("by") or "") != "derive":
            continue
        standing.setdefault(_rule_of(edge.get("basis")), {})[edge["edge_id"]] = edge

    def _retraction(edge_id, why):
        return {"schema": EVENT_SCHEMA_V2, "event": "retract", "target_edge_id": edge_id,
                "event_id": sha256_text(canonical({"target_edge_id": edge_id, "at": at,
                                                   "by": "derive"}))[:12],
                "basis": why, "by": "derive", "at": at}

    per_rule, fresh, retractions, duplicates = {}, [], [], 0
    taken_back = set()
    for name, _rule in DERIVE_RULES:
        if name not in selected:
            continue
        desired, order = {}, []
        for candidate in by_rule.get(name) or []:
            source = candidate.get("source") or {"rule": name, "from": candidate["from"],
                                                 "to": candidate["to"],
                                                 "kind": candidate["kind"],
                                                 "polarity": candidate.get("polarity")}
            edge = make_edge(control, candidate["from"], candidate["to"], candidate["kind"],
                             candidate["basis"], "derive", candidate.get("polarity"),
                             at=at, source_commit=commit,
                             source_fingerprint=fingerprint(source),
                             also_from=candidate.get("also_from"))
            if edge["edge_id"] in desired:
                duplicates += 1
                continue
            desired[edge["edge_id"]] = edge
            order.append(edge["edge_id"])
        live = standing.get(name) or {}
        added = [desired[i] for i in order if i not in live]
        gone = [live[i] for i in sorted(live) if i not in desired]
        fresh += added
        for edge in gone:
            taken_back.add(edge["edge_id"])
            retractions.append(_retraction(
                edge["edge_id"],
                "derive:%s no longer states this relation (source reconciled)" % name))
        per_rule[name] = {
            "candidates": len(by_rule.get(name) or []), "new": len(added),
            "retracted": len(gone), "active": len(desired),
            "source_fingerprint": sha256_text(canonical(
                sorted(e.get("source_fingerprint") or "" for e in desired.values())))[:16]}

    # Contract 1: an object that has DISAPPEARED takes its derived edges with
    # it.  The per-rule reconciliation above already covers the ordinary case
    # (the source stops naming the relation); this covers the one it cannot --
    # a derived edge whose far end vanished from a store the rule no longer
    # reads at all, which would otherwise stand forever pointing at nothing.
    vanished = gone_objects(control)
    gone_edges = []
    if vanished:
        for edge in active_edges(control):
            if (edge.get("by") or "") != "derive" or edge["edge_id"] in taken_back:
                continue
            if _rule_of(edge.get("basis")) not in selected:
                continue
            dead = [side for side in (edge.get("from"), edge.get("to")) if side in vanished]
            if not dead:
                continue
            taken_back.add(edge["edge_id"])
            gone_edges.append({"edge_id": edge["edge_id"], "gone": dead})
            retractions.append(_retraction(
                edge["edge_id"],
                "derive:gone endpoint %s is marked gone in the object index" % _short(dead[0])))

    fresh.sort(key=lambda e: (e["basis"], e["from"], e["to"], e["edge_id"]))
    retractions.sort(key=lambda e: e["target_edge_id"])
    elapsed_ms = int((time.time() - started) * 1000)
    log_row = {"at": at, "trigger": reason or "manual", "rules": selected,
               "skipped_unchanged": skipped_unchanged, "since": since,
               "added": len(fresh), "retracted": len(retractions), "ms": elapsed_ms,
               "dry_run": bool(dry_run),
               "derive_id": "dv_" + sha256_text("%s|%s|%s" % (at, reason, canonical(selected)))[:12],
               "space_fingerprints": live_fingerprints}

    last_event_id = None
    if not dry_run:
        with transaction(control, "derive", detail={"trigger": reason or "manual",
                                                    "rules": selected},
                         derive_note=log_row) as tx:
            tx.add(fresh + retractions)
        last_event_id = (tx.current or {}).get("last_event_id")
        log_row["last_event_id"] = last_event_id
        _derive_log_append(control, log_row)

    by_kind = {}
    for edge in fresh:
        key = edge["kind"] + (edge["polarity"] or "")
        by_kind[key] = by_kind.get(key, 0) + 1
    return {"ok": True, "dry_run": bool(dry_run), "added": len(fresh),
            "retracted": len(retractions), "skipped_existing": duplicates,
            "rules": selected, "skipped_unchanged": skipped_unchanged,
            "since": since, "since_note": since_note, "trigger": reason or "manual",
            "by_rule": per_rule, "by_kind": by_kind, "missing_sources": missing,
            "deduped": deduped, "gone_endpoints": gone_edges, "ms": elapsed_ms,
            "last_event_id": last_event_id,
            "total_edges": len(project_edges(control)) if not dry_run else None}


def derive(control: Path, rules=None, reason: str = "manual", since=None,
           dry_run: bool = False) -> dict:
    """Deterministic, idempotent, read-only on every source, RECONCILING -- and,
    since v3 contract 2, called by the host CLIs instead of by a human.

    Each rule computes the set of relations its sources currently state
    (``desired``), fingerprinted by source content, and compares it with the
    rule's own active derived edges: what is newly stated is added, what the
    source has stopped stating is retracted.  A node moving HELD -> KILLED does
    not leave a stale ``(+)`` edge standing beside the new ``(-)`` one, and a
    claim_id deleted from a figure takes its ``expressed_as`` edge with it.
    Derived edges are the rule's to give and the rule's to take back -- nothing
    here ever touches an edge a human or a role wrote.

    ``rules``   restrict to a subset (contract 2's per-CLI columns).
    ``reason``  who triggered it; lands in ``graph/derive.log`` and in the
                SessionStart line.
    ``since``   an event id from a previous derive: rules whose source spaces
                have not changed since then are skipped by fingerprint.

    **This function never raises.**  It runs at the tail of somebody else's
    transaction (``narrative commit``, ``figure_router quick --promote-current``,
    ``research_os tree close``, ``procedures claim verdict`` …); a derivation
    that blows up must cost an incident file, not the host's committed write.
    """
    started = time.time()
    try:
        return _derive_core(control, rules, reason, since, dry_run, started)
    except BaseException as exc:                         # noqa: BLE001 -- deliberate
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        detail = {"trigger": reason or "manual", "rules": rules, "since": since,
                  "dry_run": bool(dry_run), "error": clip(exc, 400),
                  "error_type": type(exc).__name__}
        incident = write_incident(control, "derive_failed", detail)
        return {"ok": False, "error": clip(exc, 400), "error_type": type(exc).__name__,
                "incident": incident.get("file") or incident.get("id"),
                "dry_run": bool(dry_run), "added": 0, "retracted": 0,
                "skipped_existing": 0, "rules": [], "skipped_unchanged": [],
                "trigger": reason or "manual", "by_rule": {}, "by_kind": {},
                "missing_sources": [], "deduped": [], "gone_endpoints": [],
                "ms": int((time.time() - started) * 1000), "total_edges": None}


# ============================================================ projections (<=40 lines)

def _budget(lines: list, limit: int = MAX_VIEW_LINES) -> list:
    if len(lines) <= limit:
        return lines
    kept = lines[: limit - 1]
    kept.append("… +%d more line(s); FULL: graph list --json" % (len(lines) - limit + 1))
    return kept


def _section(title: str, rows: list, cap: int) -> list:
    out = [title]
    if not rows:
        out.append("  (none)")
        return out
    for row in rows[: cap - 1] if len(rows) > cap else rows:
        out.append("  " + row)
    if len(rows) > cap:
        out.append("  … +%d more" % (len(rows) - (cap - 1)))
    return out


def _short(address: str) -> str:
    parsed = RO_RE.match(address or "")
    return "%s:%s" % (parsed.group(2), parsed.group(3)) if parsed else (address or "?")


def _node_label(control: Path, address: str) -> str:
    result = resolve(control, address)
    obj = result.get("object") or {}
    text = obj.get("text") or ""
    if text.startswith("|"):                 # ledger table row: keep the payload cell
        cells = [c.strip() for c in text.split("|") if c.strip()]
        text = cells[1] if len(cells) > 1 else text
    title = obj.get("title") or obj.get("message") or obj.get("semantic") or text \
        or obj.get("legacy_text") or obj.get("protocol") or ""
    mark = "" if result["exists"] else " [MISSING]"
    return ("%s %s%s" % (_short(address), clip(title, 60), mark)).rstrip()


def view_argument(control: Path, node=None, **_) -> list:
    """claim -> evidence -> run -> data, from a chosen node (default: the
    headline role, else the tree root)."""
    snapshot = head_snapshot(control)
    pid = project_id(control)
    if node:
        start = coerce_ro(node, control)
    elif snapshot:
        roles = snapshot.get("role_assignments") or {}
        start = format_ro(pid, "narr", roles.get("headline") or snapshot.get("root"))
    else:
        raise GraphError("no narrative store and no --node: nothing to walk", 1)
    # R9 B4: walk LOGICAL edges -- one relation per (from, to, kind), however
    # many sources assert it, so a claim backed three ways is not printed three
    # times and a contested one is printed once, marked.  v3 contract 0: they
    # come out of current.json, not out of a fresh fold of the ledger.
    edges = active_logical_edges(control)
    incoming, outgoing = {}, {}
    for edge in edges:
        incoming.setdefault(edge["to"], []).append(edge)
        outgoing.setdefault(edge["from"], []).append(edge)

    lines = ["ARGUMENT  %s  ·  %d logical edge(s)"
             % (_node_label(control, start), len(edges))]
    if not edges:
        lines.append("  (no edges yet -- run `graph derive`)")
        return _budget(lines)
    seen = {start}

    def walk(address, depth):
        if depth > 3:
            return
        supports = sorted(incoming.get(address, []),
                          key=lambda e: (e["kind"], e.get("polarity") or "~", e["from"]))
        for edge in supports:
            sign = "(!)" if edge.get("conflict") else \
                {"+": "(+)", "-": "(-)"}.get(edge.get("polarity"), "( )")
            marker = "  [%d sources]" % edge["sources"] if edge["sources"] > 1 else ""
            lines.append("%s%s %s %s%s" % ("  " * depth, sign, edge["kind"],
                                           _node_label(control, edge["from"]), marker))
            if edge["from"] in seen:
                continue
            seen.add(edge["from"])
            walk(edge["from"], depth + 1)
            for down in sorted(outgoing.get(edge["from"], []), key=lambda e: e["to"]):
                if down["to"] == address:
                    continue
                lines.append("%s-> %s %s" % ("  " * (depth + 1), down["kind"],
                                             _node_label(control, down["to"])))
        for down in sorted(outgoing.get(address, []), key=lambda e: (e["kind"], e["to"])):
            if down["kind"] == "depends_on":
                lines.append("%s-> depends_on %s" % ("  " * depth, _node_label(control, down["to"])))

    walk(start, 1)
    if len(lines) == 1:
        lines.append("  (this node has no incoming evidence and no dependencies)")
        ranked = {}
        for edge in edges:
            ranked[edge["to"]] = ranked.get(edge["to"], 0) + 1
        best = sorted(ranked.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        if best:
            lines.append("  best-supported nodes to start from instead:")
            for address, count in best:
                lines.append("    --node %s  (%d incoming)"
                             % (_short(address).split(":", 1)[-1], count))
    return _budget(lines)


def _load_bearing(snapshot: dict) -> list:
    """R9 B6, the load-bearing set, stated once so every list counts the same
    thing: the current root, the five assigned role nodes, and any node the
    author has explicitly flagged ``load_bearing: true``.

    The pre-R9 rule ("every direct child of the root") made the set a function
    of tree shape -- inserting one grouping node silently changed which claims
    the coverage report held to account.  Being load-bearing is now either
    structural (root, role) or declared, never accidental.
    """
    if not snapshot:
        return []
    nodes = snapshot.get("nodes") or {}
    out = []
    root = snapshot.get("root")
    if root in nodes:
        out.append(root)
    for value in (snapshot.get("role_assignments") or {}).values():
        if value and value in nodes and value not in out:
            out.append(value)
    for ident in sorted(nodes):
        node = nodes[ident] or {}
        flagged = node.get("load_bearing")
        if flagged is None:
            flagged = (node.get("state") or {}).get("load_bearing")
        if flagged is True and ident not in out:
            out.append(ident)
    return out


def coverage_report(control: Path, edges=None) -> dict:
    """The five lists of contract 4/5, as data.  ``view coverage`` renders them
    and ``current.json`` stores their counts; both read the same computation.

    ``edges`` is passed in only by :func:`build_current`, which is computing
    current.json at that very moment and therefore cannot read it back.
    """
    pid = project_id(control)
    snapshot = head_snapshot(control)
    nodes = (snapshot or {}).get("nodes") or {}
    # R9 B4: every list below counts ACTIVE LOGICAL edges, so a relation with
    # three sources counts once and a contested relation counts as neither
    # support nor refutation until someone adjudicates it.
    edges = active_logical_edges(control) if edges is None else list(edges)
    expressed_to = {e["to"] for e in edges if e["kind"] == "expressed_as"}
    expressed_from = {e["from"].split(":", 3)[3].split(".")[0]
                      for e in edges if e["kind"] == "expressed_as" and ":fig:" in e["from"]}
    supported_plus = {e["to"] for e in edges
                      if e["kind"] == "supported_by" and e.get("polarity") == "+"}
    negative = {}
    for edge in edges:
        if edge["kind"] == "supported_by" and edge.get("polarity") == "-":
            negative.setdefault(edge["to"], []).append(edge["from"])
    contested = [{"from": _short(e["from"]), "to": _short(e["to"]), "kind": e["kind"],
                  "sources": [{"by": p["by"], "polarity": p["polarity"],
                               "basis": clip(p["basis"], 40)} for p in e["provenance"]]}
                 for e in edges if e.get("conflict")]

    bearing = _load_bearing(snapshot)
    no_figure = [{"id": ident, "title": clip(nodes[ident].get("title"), 46)}
                 for ident in bearing
                 if format_ro(pid, "narr", ident) not in expressed_to]

    figures = [path.name.split(".fsg.json")[0] for path in fsg_files(control)]
    for figure_id in registry_figures(control, "current"):
        if figure_id not in figures:
            figures.append(figure_id)
    figures.sort()
    mute = [f for f in figures if f not in expressed_from]

    held_unsupported, contested_active = [], []
    for ident in sorted(nodes):
        node_obj = nodes[ident]
        state = node_obj.get("state") or {}
        address = format_ro(pid, "narr", ident)
        title = clip(node_obj.get("title"), 46)
        if (state.get("epistemic") or "").upper() == "HELD" and address not in supported_plus:
            held_unsupported.append({"id": ident, "title": title})
        if (state.get("narrative") or "").upper() == "ACTIVE" and address in negative:
            contested_active.append({"id": ident, "title": clip(node_obj.get("title"), 30),
                                     "against": [_short(a) for a in negative[address]],
                                     "epistemic": (state.get("epistemic") or "").upper()})
    return {"project": pid, "edges": len(edges), "nodes": len(nodes),
            "load_bearing": bearing, "figures": figures,
            "no_figure": no_figure, "mute_figures": mute,
            "held_unsupported": held_unsupported, "contested_active": contested_active,
            "conflicts": contested}


def view_coverage(control: Path, node=None, **_) -> list:
    """Four lists (contract 4): load-bearing claims no figure expresses; figures
    that express no claim; HELD nodes with no positive support; and nodes that
    carry a negative edge yet are still ACTIVE -- the story has been contradicted
    somewhere and has not moved."""
    report = coverage_report(control)
    lines = ["COVERAGE  %s  ·  %d logical edge(s)  ·  %d node(s)"
             % (report["project"], report["edges"], report["nodes"])]
    lines += _section("A. load-bearing claims no figure expresses (%d/%d):"
                      % (len(report["no_figure"]), len(report["load_bearing"])),
                      ["%s %s" % (r["id"], r["title"]) for r in report["no_figure"]], 8)
    if report["figures"]:
        lines += _section("B. figures expressing no claim (%d/%d):"
                          % (len(report["mute_figures"]), len(report["figures"])),
                          ["%s (expresses no claim)" % f for f in report["mute_figures"]], 7)
    else:
        lines += ["B. figures expressing no claim:",
                  "  (no figure instance registry and no %s/*.fsg.json)" % FSG_REL]
    lines += _section("C. HELD with no supported_by(+) (%d):" % len(report["held_unsupported"]),
                      ["%s %s" % (r["id"], r["title"]) for r in report["held_unsupported"]], 8)
    lines += _section("D. carries a (-) edge and is still ACTIVE (%d):"
                      % len(report["contested_active"]),
                      ["%s %s [%s] <- %s" % (r["id"], r["title"], r["epistemic"],
                                             ", ".join(r["against"][:2]))
                       for r in report["contested_active"]], 7)
    lines += _section("E. EDGE_CONFLICT -- sources disagree on the sign (%d):"
                      % len(report["conflicts"]),
                      ["%s -> %s %s: %s" % (r["from"], r["to"], r["kind"],
                                            ", ".join("%s(%s)" % (s["by"], s["polarity"] or "~")
                                                      for s in r["sources"][:3]))
                       for r in report["conflicts"]], 5)
    return _budget(lines)


def view_node(control: Path, node=None, **_) -> list:
    """Everything the relation layer says about ONE object: what points at it,
    what it points at, and what was retracted.  The view a role gets before it
    proposes an edge, so it can see whether the relation is already asserted.

    v3 contract 0: read out of ``current.json``, like every other view.
    """
    if not node:
        raise GraphError("view node needs an address: `graph view node ro::narr:C-041` "
                         "(or --node)", 2)
    address = coerce_ro(node, control)
    result = resolve(control, address)
    obj = result.get("object") or {}
    payload = ensure_current(control) or {}
    edges = payload.get("edges") or []
    incoming = [e for e in edges if e.get("to") == address]
    outgoing = [e for e in edges if e.get("from") == address]
    retracted = [e for e in (payload.get("retracted") or [])
                 if address in (e.get("from"), e.get("to"))]

    def row(edge, other_key):
        sign = "(!)" if edge.get("conflict") else \
            {"+": "(+)", "-": "(-)"}.get(edge.get("polarity"), "( )")
        provenance = edge.get("provenance") or []
        authors = ",".join(sorted({str(p.get("by")) for p in provenance})[:3]) or "?"
        basis = clip((provenance[0] or {}).get("basis") if provenance else "", 26)
        return "%s %-12s %-30s [%s | %s]" % (sign, edge.get("kind"),
                                             _node_label(control, edge.get(other_key)),
                                             authors, basis)

    title = (obj.get("title") or obj.get("message") or obj.get("semantic")
             or obj.get("protocol") or obj.get("text") or "")
    head = "NODE  %s%s" % (_short(address), "" if result["exists"] else "   [MISSING]")
    lines = [head, "  %s" % (result.get("file") or result.get("reason") or "-")]
    if title:
        lines.append("  %s" % clip(title, 72))
    lines += _section("in  (%d):" % len(incoming), [row(e, "from") for e in incoming], 13)
    lines += _section("out (%d):" % len(outgoing), [row(e, "to") for e in outgoing], 13)
    if retracted:
        lines += _section("retracted (%d):" % len(retracted),
                          ["%s %s %s -> %s" % (e.get("edge_id"), e.get("kind"),
                                               _short(e.get("from")), _short(e.get("to")))
                           for e in retracted], 6)
    return _budget(lines)


def view_conflicts(control: Path, node=None, **_) -> list:
    """Every EDGE_CONFLICT with both sides named (v3 contract 4 / graph 4.1).

    A contested relation counts as neither support nor refutation: this view is
    the queue of relations waiting for a human to adjudicate, not a list of
    errors.
    """
    payload = ensure_current(control) or {}
    conflicts = payload.get("conflicts") or []
    lines = ["CONFLICTS  %s  ·  %d contested relation(s) of %d logical edge(s)"
             % (payload.get("project") or project_id(control), len(conflicts),
                len(payload.get("edges") or []))]
    if not conflicts:
        lines.append("  (no source disagrees with another about a sign)")
        return _budget(lines)
    for row in conflicts[:10]:
        lines.append("(!) %s -> %s  %s" % (_short(row.get("from")), _short(row.get("to")),
                                           row.get("kind")))
        for source in (row.get("provenance") or [])[:4]:
            lines.append("      %s(%s)  %s  %s"
                         % (source.get("by"), source.get("polarity") or "~",
                            (source.get("at") or "")[:10], clip(source.get("basis"), 40)))
    if len(conflicts) > 10:
        lines.append("  … +%d more" % (len(conflicts) - 10))
    return _budget(lines)


def view_objects(control: Path, node=None, space=None, status=None, **_) -> list:
    """The object index by space (v3 contract 1).  ``[GONE]`` is greyed, never
    deleted: the row is the evidence that an edge used to point at something."""
    index = load_objects(control)
    if not index:
        return _budget(["OBJECTS  (no .research-os/graph/objects.json -- run "
                        "`graph index build`)"])
    wanted = str(space or "").strip() or None
    want_status = str(status or "").strip().lower() or None
    by_space = {}
    for address, entry in sorted(index.items()):
        if not isinstance(entry, dict):
            continue
        if wanted and entry.get("space") != wanted:
            continue
        gone = entry.get("status") == "gone"
        if want_status == "gone" and not gone:
            continue
        if want_status == "present" and gone:
            continue
        by_space.setdefault(entry.get("space") or "?", []).append((address, entry))
    total = sum(len(rows) for rows in by_space.values())
    gone_total = sum(1 for _a, e in
                     ((a, e) for rows in by_space.values() for a, e in rows)
                     if e.get("status") == "gone")
    lines = ["OBJECTS  %s  ·  %d object(s)  ·  %d gone%s"
             % (project_id(control), total, gone_total,
                "  space=%s" % wanted if wanted else "")]
    per_space = max(2, (MAX_VIEW_LINES - 2) // max(1, len(by_space)))
    for name in sorted(by_space):
        rows = by_space[name]
        rendered = []
        for address, entry in rows:
            mark = " [GONE]" if entry.get("status") == "gone" else ""
            rendered.append("%-22s %-10s %s%s" % (entry.get("id"),
                                                  clip(entry.get("status"), 10),
                                                  clip(entry.get("title"), 40), mark))
        lines += _section("%s (%d):" % (name, len(rows)), rendered, per_space)
    return _budget(lines)


def session_lines(control: Path) -> list:
    """The SessionStart digest of v3 contract 6.  Empty when this project has no
    relation layer (silence, not noise), and never raises: a hook must not
    brick a session.

    Read out of ``current.json`` only -- a hook that folded the whole event
    ledger on every session start would be paying the projection cost twice.
    """
    try:
        if not edges_path(control).exists():
            return []
        payload, stale = current_state(control)
        if payload is None:
            return ["GRAPH_REBUILD_REQUIRED no .research-os/graph/current.json for an "
                    "existing edge ledger -- run `graph rebuild`"]
        counts = payload.get("counts") or {}
        objects = payload.get("objects") or {}
        derived = payload.get("derive") or {}
        when = str(derived.get("at") or "")[:16] or "never"
        trigger = derived.get("trigger") or "-"
        # The parked proposals are the ONE number not owned by this layer:
        # roles/pending_edges/ is written by role_runtime, so a cached copy in
        # current.json goes stale the moment a receipt lands.  Counted live.
        try:
            actionable = pending_summary(control)["actionable"]
        except BaseException:                            # pragma: no cover
            actionable = (payload.get("pending") or {}).get("actionable", 0)
        out = ["GRAPH edges=%d conflicts=%d pending=%d gone=%d derive: last %s by %s"
               % (counts.get("logical", 0), counts.get("conflicts", 0),
                  actionable, objects.get("gone", 0), when, trigger)]
        if stale:
            out.append("GRAPH_REBUILD_REQUIRED current.json is behind the event stream "
                       "(%s of %d events) -- first step: `graph rebuild`"
                       % (payload.get("event_count"), stream_head(control)[0]))
        return out
    except BaseException:
        return []


def session_line(control: Path):
    """Back-compatible single string (the hook prints it verbatim)."""
    lines = session_lines(control)
    return "\n".join(lines) if lines else None


def view_role_activity(control: Path, node=None, **_) -> list:
    """Who contributed which edges in the last 30 days (role-fleet section 5).

    The one view that reads the event stream rather than ``current.json``, and
    deliberately: it is a view of HISTORY (who asserted what, retractions
    included), while current.json materialises only relations that currently
    hold.  Answering it from the projection would silently drop every edge
    somebody took back -- which is exactly what a role-activity audit is for.
    The cross-project form (contract 5) has no such licence and reads the
    per-author summary current.json carries.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=ROLE_ACTIVITY_DAYS)
    edges = list_edges(control, include_retracted=True)
    recent, older = [], 0
    for edge in edges:
        stamp = edge.get("at") or ""
        try:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            when = None
        if when is not None and when >= cutoff:
            recent.append(edge)
        else:
            older += 1
    buckets = {}
    for edge in recent:
        slot = buckets.setdefault(edge.get("by") or "main",
                                  {"n": 0, "kinds": {}, "last": "", "retracted": 0})
        slot["n"] += 1
        slot["kinds"][edge["kind"]] = slot["kinds"].get(edge["kind"], 0) + 1
        slot["last"] = max(slot["last"], edge.get("at") or "")
        if edge.get("retracted"):
            slot["retracted"] += 1
    lines = ["ROLE ACTIVITY  last %dd  ·  %d edge(s) in window  ·  %d older"
             % (ROLE_ACTIVITY_DAYS, len(recent), older)]
    rows = []
    for author in sorted(buckets, key=lambda a: (-buckets[a]["n"], a)):
        slot = buckets[author]
        kinds = ", ".join("%s=%d" % (k, v) for k, v in sorted(slot["kinds"].items()))
        retracted = "  retracted=%d" % slot["retracted"] if slot["retracted"] else ""
        rows.append("%-18s n=%-4d %s  last=%s%s"
                    % (author, slot["n"], kinds, (slot["last"] or "")[:16], retracted))
    lines += _section("by author:", rows, 20)
    samples = ["%s %s %s -> %s" % (e["edge_id"], e["kind"], _short(e["from"]), _short(e["to"]))
               for e in sorted(recent, key=lambda e: e.get("at") or "", reverse=True)]
    lines += _section("most recent:", samples, 14)
    return _budget(lines)


def promotion_status(control: Path) -> dict:
    """R9 B9: the promotion threshold of contract 7, made mechanical.

    READ-ONLY, and -- like ``view role-activity`` -- computed from the event
    stream: the bar counts distinct calendar days on which each role asserted,
    which is a fact about history, not about the relations standing today.
    It reports whether the bar is met; it never promotes the ledger
    and never changes the four-layer structure.  The bar, verbatim: within a
    rolling 30 days at least two roles each wrote valid edges on at least five
    distinct calendar days, AND at least one decision record cites the coverage
    output.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=ROLE_ACTIVITY_DAYS)
    days_by_role = {}
    for edge in active_edges(control):
        author = edge.get("by") or "main"
        if author not in EDGE_CLASS_POLICY:
            continue
        stamp = str(edge.get("at") or "")
        try:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when < cutoff:
            continue
        days_by_role.setdefault(author, set()).add(stamp[:10])
    qualifying = sorted(role for role, days in days_by_role.items() if len(days) >= 5)
    citing = []
    for block in decision_blocks(control):
        text = "%s %s" % (block.get("title") or "", block.get("summary") or "")
        lowered = text.lower()
        if "coverage" in lowered or "graph view coverage" in lowered:
            citing.append({"date": block.get("date"), "title": clip(block.get("title"), 60)})
    criteria = [
        {"id": "two-roles-five-days",
         "met": len(qualifying) >= 2,
         "detail": "%d role(s) at >=5 distinct days in %dd: %s"
                   % (len(qualifying), ROLE_ACTIVITY_DAYS,
                      ", ".join("%s=%d" % (r, len(days_by_role[r])) for r in qualifying)
                      or "(none)")},
        {"id": "decision-cites-coverage",
         "met": bool(citing),
         "detail": "%d decision record(s) reference coverage" % len(citing)},
    ]
    ready = all(row["met"] for row in criteria)
    return {"window_days": ROLE_ACTIVITY_DAYS, "ready": ready, "criteria": criteria,
            "role_days": {r: sorted(d) for r, d in sorted(days_by_role.items())},
            "decisions_citing_coverage": citing,
            "note": "read-only: meeting the bar authorises a decision to promote the "
                    "edge ledger; it does not promote anything by itself"}


VIEWS = {"argument": view_argument, "coverage": view_coverage,
         "role-activity": view_role_activity, "node": view_node,
         "conflicts": view_conflicts, "objects": view_objects}
# ============================================================ v3 §3: the role's graph context

def _refs_to_addresses(control: Path, refs) -> list:
    """The subset of a dispatch's refs that name a research object.

    A ref list is mostly file paths (``figs/f1.py``), so anything that is not
    an ``ro:`` address or a bare object id is skipped in silence -- the point
    is to hand the role what the graph knows about the objects it was given,
    not to guess that a path is a figure.
    """
    out = []
    for ref in refs or []:
        text = str(ref or "").strip()
        if not text:
            continue
        if text.startswith("ro:"):
            try:
                parsed = parse_ro(text, control)
            except GraphError:
                continue
            address = format_ro(parsed["project"], parsed["space"], parsed["id"])
        elif NODE_ID_RE.fullmatch(text):
            address = format_ro(project_id(control), "narr", text)
        else:
            continue
        if address not in out:
            out.append(address)
    return out[:6]


def _context_edge_line(control: Path, edge: dict, other: str, indent: str = "  ") -> str:
    sign = "(!)" if edge.get("conflict") else \
        {"+": "(+)", "-": "(-)"}.get(edge.get("polarity"), "( )")
    authors = sorted({p.get("by") or "?" for p in (edge.get("provenance") or [])})
    return "%s%s %s %s [%s]" % (indent, sign, edge.get("kind"),
                                _node_label(control, edge.get(other)),
                                ",".join(authors[:3]) or "?")


def graph_context(control: Path, refs=None, role=None) -> dict:
    """v3 contract 3: what ``role dispatch`` puts in ``inputs.graph_context``.

    The role no longer has to query the graph, and -- more to the point -- it
    is no longer free to query anything else: it gets a node view of the
    objects it was handed (<=8 edges each), the ``EDGE_CONFLICT``s that touch
    its own edge classes, and its own pending summary, inside 40 lines.

    Never raises and never writes: a dispatch must not fail because the
    relation layer needs a rebuild -- it says so in ``flags`` and moves on.
    """
    try:
        if not edges_path(control).exists():
            return {}                # no event ledger: this project has no relations
        payload, stale = current_state(control)
        flags = []
        if stale:
            flags.append("GRAPH_REBUILD_REQUIRED")
        if payload is None:
            payload = ensure_current(control) or {}
        edges = list(payload.get("edges") or [])
        conflicts = list(payload.get("conflicts") or [])
        objects_summary = payload.get("objects") or {}
        titles = load_objects(control)
        addresses = _refs_to_addresses(control, refs)

        lines = ["GRAPH %s  edges=%d conflicts=%d gone=%d"
                 % (payload.get("project") or project_id(control), len(edges),
                    len(conflicts), objects_summary.get("gone") or 0)]
        nodes = []
        for address in addresses:
            incoming = [e for e in edges if e.get("to") == address]
            outgoing = [e for e in edges if e.get("from") == address]
            entry = titles.get(address) if isinstance(titles.get(address), dict) else {}
            nodes.append({"ro": address, "title": entry.get("title") or "",
                          "status": entry.get("status") or "",
                          "in": len(incoming), "out": len(outgoing)})
            lines.append("NODE %s  in=%d out=%d%s"
                         % (_node_label(control, address), len(incoming), len(outgoing),
                            "  [GONE]" if entry.get("status") == "gone" else ""))
            budget = GRAPH_CONTEXT_MAX_EDGES
            for edge in incoming[:budget]:
                lines.append(_context_edge_line(control, edge, "from"))
            for edge in outgoing[:max(0, budget - len(incoming))]:
                lines.append(_context_edge_line(control, edge, "to", indent="  -> "))
            overflow = max(0, len(incoming) + len(outgoing) - budget)
            if overflow:
                lines.append("  … +%d more edge(s): graph view node %s" % (overflow, address))

        classes = set(policy_for(role) or ())
        relevant = []
        for row in conflicts:
            try:
                triple = (parse_ro(row["from"], control)["space"],
                          parse_ro(row["to"], control)["space"], row["kind"])
            except (GraphError, KeyError):
                triple = None
            touches = row.get("from") in addresses or row.get("to") in addresses
            if triple in classes or touches:
                relevant.append(row)
        if relevant:
            lines.append("EDGE_CONFLICT (%d relevant to %s):" % (len(relevant), role or "?"))
            for row in relevant[:5]:
                sources = ", ".join("%s(%s)" % (p.get("by"), p.get("polarity") or "~")
                                    for p in (row.get("provenance") or [])[:4])
                lines.append("  %s -> %s %s: %s" % (_short(row["from"]), _short(row["to"]),
                                                    row["kind"], sources))
        pending = payload.get("pending") or {}
        try:
            mine = pending_summary(control)
        except BaseException:                            # pragma: no cover
            mine = {"actionable": 0, "by_role": {}}
        my_pending = int((mine.get("by_role") or {}).get(role) or 0)
        if my_pending or pending.get("actionable"):
            lines.append("PENDING actionable=%d (yours=%d)"
                         % (pending.get("actionable") or 0, my_pending))
        for flag in flags:
            lines.append(flag)
        lines = _budget(lines)
        context = {"schema": CONTEXT_SCHEMA,
                   "project": payload.get("project") or project_id(control),
                   "role": role, "objects": nodes,
                   "conflicts": [{"from": r["from"], "to": r["to"], "kind": r["kind"],
                                  "sources": ["%s(%s)" % (p.get("by"), p.get("polarity") or "~")
                                              for p in (r.get("provenance") or [])[:4]]}
                                 for r in relevant[:5]],
                   "pending": {"actionable": pending.get("actionable") or 0,
                               "yours": my_pending},
                   "flags": flags, "lines": lines}
        # The envelope has a hard byte budget (role-fleet 3); shed detail rather
        # than let a dispatch fail because the graph got interesting.
        while len(canonical(context).encode("utf-8")) > GRAPH_CONTEXT_MAX_BYTES:
            if len(context["lines"]) > 4:
                context["lines"] = context["lines"][:-1]
                context["truncated"] = True
            elif context["conflicts"]:
                context["conflicts"] = context["conflicts"][:-1]
                context["truncated"] = True
            elif context["objects"]:
                context["objects"] = context["objects"][:-1]
                context["truncated"] = True
            else:
                break
        return context
    except BaseException:                                # noqa: BLE001 -- fail open
        return {}


# ============================================================ v3 §5: cross-project (read-only)

def portfolio_entries() -> list:
    """Every registered project that still exists on disk.  Read-only."""
    try:
        data = ros.load_portfolio()
    except BaseException:                                # pragma: no cover
        return []
    out = []
    for entry in (data.get("projects") or []):
        root = entry.get("root")
        if not root:
            continue
        control = Path(root) / ".research-os"
        if (control / "state.json").exists():
            out.append({"entry": entry, "control": control})
    return out


def _cross_label(entry: dict, payload) -> str:
    name = (payload or {}).get("project") or entry.get("project_id") \
        or ros.slugify(str(entry.get("title") or Path(entry.get("root", "")).name))
    return str(name)[:28]


def cross_project_view(view: str, limit: int = MAX_VIEW_LINES) -> list:
    """``graph --all-projects view coverage|role-activity|conflicts``.

    Each project contributes at most 5 lines and is read ONLY through its own
    ``current.json`` -- no narrative store is opened, no lock is taken, nothing
    is ever written in another project.  A project whose projection is missing
    or behind says so and is skipped, because the alternative (rebuilding
    somebody else's graph from here) is exactly the cross-project write the
    contract forbids.
    """
    if view not in ("coverage", "role-activity", "conflicts"):
        raise GraphError("--all-projects supports view coverage|role-activity|conflicts "
                         "(got %r)" % view, 2)
    projects = portfolio_entries()
    lines = ["ALL PROJECTS  %s  ·  %d registered project(s)" % (view, len(projects))]
    shown = 0
    for item in projects:
        payload, stale = current_state(item["control"])
        label = _cross_label(item["entry"], payload)
        if payload is None:
            if edges_path(item["control"]).exists():
                lines.append("%-28s (no current.json -- run `graph rebuild` there)" % label)
                shown += 1
            continue
        rows = []
        counts = payload.get("counts") or {}
        if stale:
            rows.append("  GRAPH_REBUILD_REQUIRED (current.json is behind the event stream)")
        if view == "coverage":
            coverage = payload.get("coverage") or {}
            if coverage:
                rows.append("  A no-figure=%d  B mute-figures=%d  C held-unsupported=%d  "
                            "D contested=%d  E conflicts=%d"
                            % (coverage.get("a_no_figure", 0), coverage.get("b_mute_figures", 0),
                               coverage.get("c_held_unsupported", 0),
                               coverage.get("d_contested_active", 0),
                               coverage.get("e_conflicts", 0)))
            else:
                rows.append("  (no coverage summary in current.json)")
        elif view == "role-activity":
            authors = payload.get("authors") or {}
            top = sorted(authors.items(), key=lambda kv: (-kv[1].get("n", 0), kv[0]))[:4]
            rows.append("  " + ("  ".join("%s=%d" % (name, slot.get("n", 0))
                                          for name, slot in top) or "(no edges)"))
        else:
            conflicts = payload.get("conflicts") or []
            if not conflicts:
                rows.append("  (no EDGE_CONFLICT)")
            for row in conflicts[:3]:
                rows.append("  %s -> %s %s: %s"
                            % (_short(row.get("from")), _short(row.get("to")), row.get("kind"),
                               ", ".join("%s(%s)" % (p.get("by"), p.get("polarity") or "~")
                                         for p in (row.get("provenance") or [])[:3])))
        header = ("%-28s edges=%-4d conflicts=%-3d gone=%d"
                  % (label, counts.get("logical", 0), counts.get("conflicts", 0),
                     (payload.get("objects") or {}).get("gone", 0)))
        lines.append(header)
        lines += rows[:CROSS_PROJECT_LINES - 1]
        shown += 1
    if shown == 0:
        lines.append("  (no registered project has a relation layer yet)")
    return _budget(lines, limit)


def find_edges(control_list, kind=None, to_space=None, from_space=None,
               title_like=None, limit: int = 40) -> dict:
    """``graph find`` -- who ever expressed something like this, and where.

    Read-only across projects (contract 5): the ammunition-reuse entry point.
    Every hit is reported as a complete ``ro:`` address in its OWN project, so
    the answer is something you can go and read, not something to copy blind.
    """
    needle = (title_like or "").strip().lower()
    hits, scanned, skipped = [], 0, []
    for item in control_list:
        control = item["control"] if isinstance(item, dict) else item
        payload, stale = current_state(control)
        if payload is None:
            skipped.append({"project": str(control), "reason": "no current.json"})
            continue
        scanned += 1
        titles = load_objects(control)
        for edge in payload.get("edges") or []:
            if kind and edge.get("kind") != kind:
                continue
            try:
                to_parsed = parse_ro(edge["to"], control)
                from_parsed = parse_ro(edge["from"], control)
            except (GraphError, KeyError):
                continue
            if to_space and to_parsed["space"] != to_space:
                continue
            if from_space and from_parsed["space"] != from_space:
                continue
            to_entry = titles.get(edge["to"]) if isinstance(titles.get(edge["to"]), dict) else {}
            from_entry = titles.get(edge["from"]) if isinstance(titles.get(edge["from"]), dict) else {}
            haystack = " ".join([str(to_entry.get("title") or ""), str(from_entry.get("title") or ""),
                                 edge.get("to") or "", edge.get("from") or ""]).lower()
            if needle and needle not in haystack:
                continue
            hits.append({"project": payload.get("project"), "from": edge["from"],
                         "to": edge["to"], "kind": edge["kind"],
                         "polarity": edge.get("polarity"), "conflict": edge.get("conflict"),
                         "from_title": clip(from_entry.get("title"), 48),
                         "to_title": clip(to_entry.get("title"), 48),
                         "stale": bool(stale)})
    hits.sort(key=lambda h: (h["project"] or "", h["kind"], h["from"], h["to"]))
    lines = ["FIND  kind=%s from-space=%s to-space=%s title-like=%r  ·  %d project(s)  ·  %d hit(s)"
             % (kind or "*", from_space or "*", to_space or "*", title_like or "", scanned, len(hits))]
    for hit in hits[:limit]:
        lines.append("  %s  %s -> %s  %s" % (hit["kind"], hit["from"], hit["to"],
                                             hit["to_title"] or hit["from_title"] or ""))
    if not hits:
        lines.append("  (nothing matched)")
    return {"hits": hits, "projects": scanned, "skipped": skipped, "lines": _budget(lines)}


# ============================================================ v3 §4 forwarding

def render_html(control: Path, view: str, out) -> dict:
    """Thin forwarder to ``graph_render_html.render(control, view, out)``.

    The page itself is not this module's business (contract 4 gives it its own
    file); what belongs here is the refusal when it is not installed, so a
    missing renderer is a usage error with a name, not an ImportError traceback.
    """
    try:
        import graph_render_html                          # noqa: WPS433 -- optional sibling
    except Exception as exc:                              # noqa: BLE001
        raise GraphError("GRAPH_RENDER_UNAVAILABLE scripts/graph_render_html.py is not "
                         "installed (%s) -- `graph render --html` needs it; every other "
                         "graph command works without it" % clip(exc, 120), 2)
    render = getattr(graph_render_html, "render", None)
    if not callable(render):
        raise GraphError("GRAPH_RENDER_UNAVAILABLE graph_render_html has no render(control, "
                         "view, out) entry point", 2)
    try:
        result = render(control, view, Path(out))
    except GraphError:
        raise
    except Exception as exc:                             # noqa: BLE001
        # The renderer owns the page; this module owns the boundary.  Surfacing
        # its failure by name beats a traceback out of `graph render`, and
        # beats swallowing it -- a page that did not render is not a page.
        raise GraphError("GRAPH_RENDER_FAILED graph_render_html.render(%s) raised %s: %s"
                         % (view, type(exc).__name__, clip(exc, 200)), 1)
    return result if isinstance(result, dict) else {"result": "RENDERED", "out": str(out)}


# ============================================================ self-test

class _Report:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append({"name": name, "ok": bool(ok), "detail": clip(detail, 200)})
        return bool(ok)

    @property
    def ok(self):
        return all(row["ok"] for row in self.rows)


def _write(path: Path, text: str) -> None:
    ros.atomic_write(path, text)


def _run_narrative(root: Path, *args) -> dict:
    import subprocess
    script = str(Path(__file__).resolve().parent / "narrative.py")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # `narrative.py <cmd> <root>` for flat commands, `<cmd> <action> <root>` for groups.
    head = list(args[:2]) if args[0] in ("node", "branch") else [args[0]]
    rest = list(args[len(head):])
    proc = subprocess.run([sys.executable, script] + head + [str(root)] + rest,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)
    if proc.returncode != 0:
        raise GraphError("narrative %s failed: %s" % (args[0], (proc.stdout or proc.stderr).strip()[:300]), 2)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def _identity(*pairs) -> list:
    out = []
    for pair in pairs:
        out += ["--identity", pair]
    return out


def _fixture(base: Path) -> Path:
    """A minimal but complete project: a real narrative tree (built with
    narrative.py, not hand-written), an attempt tree, two figure programs, an
    asset index, a run, a formal claim, two ledgers and two decisions."""
    root = base / "graph-selftest"
    control = root / ".research-os"
    control.mkdir(parents=True, exist_ok=True)
    _write(control / "state.json", json.dumps({
        "schema_version": "auto-research/v2", "project_id": "graph-selftest",
        "title": "Graph self-test", "status": "active", "stage": "manuscript_draft",
        "artifacts": [
            {"id": "tab-main", "path": "paper/tables/main.tex", "role": "evidence",
             "tags": ["table", "C-001"], "produced_by": "research-experiments",
             "status": "current"},
            {"id": "fig-history", "path": "paper/figures/history.pdf", "role": "evidence",
             "tags": ["figure"], "produced_by": "R01.A01 for C-002", "status": "current"},
            "legacy plain-string artifact (older projects still carry these)",
        ],
    }, ensure_ascii=False, indent=2) + "\n")

    # -- narrative tree, built through the only legal writer
    _run_narrative(root, "init", "--title", "Graph self-test", "--thesis",
                   "search has a price", "--summary", "root")
    claim_identity = _identity("subject=deployment", "target=returns", "predicate=gap",
                               "quantifier=all", "domain=us-equities", "conditions=",
                               "polarity=+")
    for node_id, epistemic, basis in (("C-001", "HELD", ["R-01", "ro::ledger:E-09"]),
                                      ("C-002", "PENDING", ["D-02"]),
                                      ("C-003", "HELD", ["backfill:legacy.md#C-003"])):
        args = ["node", "add", "--id", node_id, "--node-type", "claim", "--parent", "R-0",
                "--title", "claim %s" % node_id, "--statement", "statement %s" % node_id,
                "--epistemic", epistemic, "--out", str(root / "ops.json")]
        args += _identity("subject=%s" % node_id, "target=returns", "predicate=gap",
                          "quantifier=all", "domain=us-equities", "conditions=", "polarity=+") \
            if node_id != "C-001" else claim_identity
        for ref in basis:
            args += ["--basis", ref]
        _run_narrative(root, *args)
        _run_narrative(root, "commit", "--ops-file", str(root / "ops.json"),
                       "--message", "add %s" % node_id)
    lineage_args = ["node", "add", "--id", "C-004", "--node-type", "claim", "--parent", "R-0",
                    "--title", "claim C-004", "--statement", "narrowed restatement of C-003",
                    "--epistemic", "PENDING", "--lineage", "narrows:C-003",
                    "--out", str(root / "ops.json")]
    lineage_args += _identity("subject=C-004", "target=returns", "predicate=gap",
                              "quantifier=some", "domain=us-equities", "conditions=",
                              "polarity=+")
    _run_narrative(root, *lineage_args)
    _run_narrative(root, "commit", "--ops-file", str(root / "ops.json"),
                   "--message", "add C-004 narrowing C-003")
    _run_narrative(root, "node", "set-role", "--role", "headline", "--id", "C-001",
                   "--out", str(root / "ops.json"))
    _run_narrative(root, "commit", "--ops-file", str(root / "ops.json"),
                   "--message", "headline := C-001")

    # -- attempt tree
    _write(control / "tree.json", json.dumps({
        "schema_version": "auto-research/tree-v1",
        "legend": {"route": "R", "attempt": "A", "claim": "C", "experiment": "E",
                   "verification": "V", "decision": "D"},
        "nodes": {
            "R01": {"code": "R01", "kind": "route", "parent": None, "title": "main route",
                    "status": "running", "result": "", "evidence": [], "tags": []},
            "R01.A01": {"code": "R01.A01", "kind": "attempt", "parent": "R01",
                        "title": "bootstrap test of C-001", "status": "proven",
                        "result": "C-001 proved on the frozen sample", "evidence": [],
                        "tags": []},
            "R01.A02": {"code": "R01.A02", "kind": "attempt", "parent": "R01",
                        "title": "stress test", "status": "refuted",
                        "result": "C-002 refuted by the placebo grid", "evidence": [],
                        "tags": []},
            "R01.A03": {"code": "R01.A03", "kind": "attempt", "parent": "R01",
                        "title": "open work", "status": "running", "result": "",
                        "evidence": [], "tags": []},
        }}, ensure_ascii=False, indent=2) + "\n")

    # -- figure programs
    fsg = root / FSG_REL
    _write(fsg / "F01.fsg.json", json.dumps({
        "figure": {"id": "F01", "message": "the deployment gap", "archetype": "mechanism"},
        "panels": [{"id": "P01", "role": "mechanism", "claim_id": "C-001"}],
        "nodes": [{"id": "E01", "panel": "P01", "semantic": "threshold curve",
                   "claim": "C-002"}],
        "texts": [], "edges": []}, ensure_ascii=False, indent=2) + "\n")
    _write(fsg / "F02.fsg.json", json.dumps({
        "figure": {"id": "F02", "message": "decorative overview", "archetype": "overview"},
        "panels": [{"id": "P01", "role": "overview"}],
        "nodes": [{"id": "E01", "panel": "P01", "semantic": "logo"}],
        "texts": [], "edges": []}, ensure_ascii=False, indent=2) + "\n")

    # -- figure instance registry (v2.9): resolution order registry -> FSG
    _write(control / FIGURES_REGISTRY_REL, json.dumps({
        "schema": FIGURE_REGISTRY_SCHEMA,
        "figures": {
            "F-101": {"id": "F-101", "status": "current", "claim_ids": ["C-001"],
                      "title": "the price of search", "version": 3},
            "F-102": {"id": "F-102", "status": "draft", "claim_ids": ["C-002"]},
            "F-103": {"id": "F-103", "status": "current", "claim_ids": []},
        }}, ensure_ascii=False, indent=2) + "\n")

    # -- runs, claims, ledgers, decisions
    _write(control / "runs" / "proto-r01.json", json.dumps({
        "schema_version": "auto-research/run-v1", "run_id": "proto-r01",
        "protocol": "proto", "command": "py fit.py", "status": "done",
        "tree_node": "R01.A01", "result": "ok", "claims": ["C-001"],
        "outcome": "success"}, ensure_ascii=False, indent=2) + "\n")
    _write(control / "runs" / "proto-r02.json", json.dumps({
        "schema_version": "auto-research/run-v1", "run_id": "proto-r02",
        "protocol": "proto", "command": "py placebo.py", "status": "failed",
        "tree_node": "R01.A02", "result": "the placebo grid refuted it",
        "claims": ["C-002", "price-of-search"], "outcome": "failed"},
        ensure_ascii=False, indent=2) + "\n")
    _write(control / "runs" / "proto-r03.json", json.dumps({
        "schema_version": "auto-research/run-v1", "run_id": "proto-r03",
        "protocol": "proto", "command": "py long.py", "status": "running",
        "claims": ["C-003"]}, ensure_ascii=False, indent=2) + "\n")
    _write(root / CLAIMS_REL / "price-of-search" / "spec.md",
           "---\nid: price-of-search\nstatus: adjudicated\nspec_version: 2\n"
           "verdict: proven\nverdict_result: C-001 holds on the frozen sample\n"
           "frozen_sha: deadbeef\n---\n\nThe claim.\n")
    _write(root / CLAIMS_REL / "deployment-gap" / "spec.md",
           "---\nid: deployment-gap\nstatus: adjudicated\nspec_version: 1\n"
           "verdict: refuted\nverdict_result: C-003 fails out of sample\n"
           "tree_code: R01.A02\nfrozen_sha: cafe1234\n---\n\nThe gap claim.\n")
    _write(root / EVIDENCE_REL / "evidence.jsonl",
           json.dumps({"id": "E-09", "claim": "C-001", "source": "Harvey et al. 2016",
                       "relation": "supports", "confidence": "high", "verified": "1"},
                      ensure_ascii=False) + "\n")
    _write(root / "_台账_横切总表.md",
           "# Ledger\n\n| id | source | verdict |\n|---|---|---|\n"
           "| R-01 | Harvey, Liu & Zhu (2016) | SUPPORTED |\n"
           "| D-02 | 20 papers x 5 tools | measured |\n")
    _write(control / "decisions.md",
           "# Research Decisions\n\n"
           "## 2026-08-01 route decision (all in RFS)\n- Route B approved.\n\n"
           "## 2026-08-01 kill gate frozen\n- three failure signals stop the rebuild.\n\n"
           "## 2026-08-11 headline is the price of search\n- user-approved.\n")
    return control


def run_self_test() -> int:
    import shutil
    import tempfile
    report = _Report()
    saved = os.environ.get("AR_SESSION")
    os.environ["AR_SESSION"] = "W001"
    tmp = Path(tempfile.mkdtemp(prefix="ar-graph-selftest-"))
    try:
        control = _fixture(tmp)
        cache_clear()
        pid = project_id(control)

        # --- 1. addressing: every space resolves, and misses explain themselves
        cases = {"narr": "C-001", "tree": "R01.A01", "fig": "F01.E01", "asset": "tab-main",
                 "run": "proto-r01", "claim": "price-of-search", "ledger": "R-01",
                 "decision": "2026-08-01-2"}
        misses = []
        for space, ident in cases.items():
            result = resolve(control, format_ro(pid, space, ident))
            if not result["exists"]:
                misses.append("%s:%s (%s)" % (space, ident, result["reason"]))
        report.check("resolve: all 8 spaces resolve", not misses, "; ".join(misses))
        report.check("resolve: short form ro::narr:C-001 == long form",
                     resolve(control, "ro::narr:C-001")["exists"])
        absent = resolve(control, "ro::narr:C-999")
        report.check("resolve: unknown id -> exists=false with a reason",
                     absent["exists"] is False and bool(absent["reason"]), absent["reason"])
        report.check("resolve: foreign project is reported, not crashed",
                     resolve(control, "ro:other-paper:narr:C-001")["exists"] is False)
        try:
            resolve(control, "narr:C-001")
            report.check("resolve: malformed address refused", False)
        except GraphError as exc:
            report.check("resolve: malformed address refused",
                         exc.text.startswith("MALFORMED_RO"), exc.text)
        instance = resolve(control, "ro::fig:F-101")
        report.check("resolve: fig hits figures/registry before the FSG programs",
                     instance["exists"]
                     and (instance["object"] or {}).get("source") == "figures/registry"
                     and (instance["object"] or {}).get("claim_ids") == ["C-001"],
                     instance.get("reason") or (instance["object"] or {}).get("source"))
        report.check("resolve: a figure only in FSG still resolves through the fallback",
                     (resolve(control, "ro::fig:F01")["object"] or {}).get("panels") == 1)

        decision = resolve(control, format_ro(pid, "decision", "2026-08-01-2"))
        report.check("resolve: decision by date+ordinal picks the right block",
                     (decision["object"] or {}).get("title", "").startswith("kill gate"),
                     (decision["object"] or {}).get("title"))

        # --- 2. link / unlink / list
        linked = link(control, "ro::run:proto-r01", "ro::narr:C-001", "supported_by",
                      "self-test manual link", by="experiment-runner", polarity="+")
        report.check("link: writes one edge", linked["result"] == "LINKED", linked["result"])
        again = link(control, "ro::run:proto-r01", "ro::narr:C-001", "supported_by",
                     "self-test manual link", by="experiment-runner", polarity="+")
        report.check("link: identical link is idempotent",
                     again["result"] == "ALREADY_LINKED", again["result"])
        try:
            link(control, "ro::run:proto-r01", "ro::narr:C-404", "supported_by", "x")
            report.check("link: unresolvable endpoint refused", False)
        except GraphError as exc:
            report.check("link: refusal names WHICH end",
                         "UNRESOLVED_ENDPOINT" in exc.text and "to=" in exc.text and "from=" not in exc.text,
                         exc.text)
        try:
            link(control, "ro::fig:F01.E01", "ro::narr:C-001", "expressed_as", "x",
                 polarity="+")
            report.check("link: polarity refused on a non-supported_by kind", False)
        except GraphError as exc:
            report.check("link: polarity refused on a non-supported_by kind",
                         exc.text.startswith("POLARITY_NOT_ALLOWED"), exc.text)
        edge_id = linked["edge"]["edge_id"]
        by_kind_rows = list_edges(control, kind="supported_by")
        by_to_rows = list_edges(control, to_ro="ro::narr:C-001")
        report.check("list: --kind / --to filters agree",
                     all(e["kind"] == "supported_by" for e in by_kind_rows)
                     and all(e["to"].endswith(":narr:C-001") for e in by_to_rows)
                     and any(e["edge_id"] == edge_id for e in by_kind_rows)
                     and any(e["edge_id"] == edge_id for e in by_to_rows),
                     "%d kind rows / %d to rows" % (len(by_kind_rows), len(by_to_rows)))
        events_before = len(read_edge_events(control))
        active_before = len(list_edges(control))
        dropped = unlink(control, edge_id, "self-test retraction")
        report.check("unlink: appends a retraction, keeps the line",
                     dropped["result"] == "UNLINKED"
                     and len(list_edges(control)) == active_before - 1
                     and any(e["edge_id"] == edge_id
                             for e in list_edges(control, include_retracted=True)),
                     dropped["result"])
        report.check("unlink: the ledger is append-only (one more event, no line rewritten)",
                     len(read_edge_events(control)) == events_before + 1,
                     "%d -> %d" % (events_before, len(read_edge_events(control))))

        # --- 3. derive: deterministic, idempotent, every rule fires
        settled = len(read_edge_events(control))
        first = derive(control, dry_run=True)
        report.check("derive --dry-run writes nothing",
                     len(read_edge_events(control)) == settled,
                     "%d -> %d" % (settled, len(read_edge_events(control))))
        real = derive(control)
        after_first = len(project_edges(control))
        second = derive(control)
        after_second = len(project_edges(control))
        report.check("derive: dry-run count == real count",
                     first["added"] == real["added"], "%s vs %s" % (first["added"], real["added"]))
        report.check("derive: idempotent (re-run adds nothing)",
                     second["added"] == 0 and after_first == after_second,
                     "%d -> %d" % (after_first, after_second))
        # `active`, not `new`: a rule that fired during `narrative commit` has
        # nothing left to add here, but it still STATES its relations -- which is
        # what "the rule fires on this fixture" has to mean once derivation is
        # real-time (v3 contract 2).
        rules_fired = {name: real["by_rule"][name]["active"] for name, _ in DERIVE_RULES}
        report.check("derive: all eight contract rules fire on the fixture",
                     len(DERIVE_RULES) == 8 and all(count > 0 for count in rules_fired.values()),
                     rules_fired)
        report.check("derive fig-registry: only status=current is expressed",
                     any(e["basis"] == "derive:fig-registry" and e["from"].endswith(":fig:F-101")
                         for e in list_edges(control, kind="expressed_as"))
                     and not any(":fig:F-102" in e["from"]
                                 for e in list_edges(control, kind="expressed_as")),
                     [e["from"] for e in list_edges(control, kind="expressed_as")])
        report.check("derive run-claim: a settled run signs, an in-flight run is skipped",
                     any(e["from"].endswith(":run:proto-r02") and e.get("polarity") == "-"
                         for e in list_edges(control, kind="supported_by"))
                     and not any(":run:proto-r03" in e["from"] for e in list_edges(control)),
                     [e["from"] for e in list_edges(control, kind="supported_by")])
        report.check("derive run-claim: a run may support a formal claim, not only a node",
                     any(e["from"].endswith(":run:proto-r02") and ":claim:price-of-search" in e["to"]
                         for e in list_edges(control)))
        report.check("derive tree-close: a claim spec's tree_code is an exact tree->claim edge",
                     any(e["from"].endswith(":tree:R01.A02")
                         and e["to"].endswith(":claim:deployment-gap")
                         and e.get("polarity") == "-" and e["basis"] == "derive:tree-close"
                         for e in list_edges(control)),
                     [e["to"] for e in list_edges(control) if ":tree:" in e["from"]])
        report.check("derive claim-verdict: proven -> (+), refuted -> (-)",
                     any(":claim:price-of-search" in e["from"] and e["to"].endswith(":narr:C-001")
                         and e.get("polarity") == "+" and e["basis"] == "derive:claim-verdict"
                         for e in list_edges(control))
                     and any(":claim:deployment-gap" in e["from"] and e.get("polarity") == "-"
                             for e in list_edges(control)))
        report.check("derive lineage: evolved_from carries the relation in its basis",
                     any(e["kind"] == "evolved_from" and e["from"].endswith(":narr:C-004")
                         and e["to"].endswith(":narr:C-003") and "rel=narrows" in e["basis"]
                         for e in list_edges(control, kind="evolved_from")),
                     [e["basis"] for e in list_edges(control, kind="evolved_from")])
        kinds = real["by_kind"]
        report.check("derive: signs come from the sources",
                     kinds.get("supported_by+", 0) > 0 and kinds.get("supported_by-", 0) > 0
                     and kinds.get("expressed_as", 0) > 0 and kinds.get("depends_on", 0) > 0,
                     kinds)
        report.check("derive: an unlinked edge is not resurrected",
                     len(list_edges(control, to_ro="ro::narr:C-001", by="experiment-runner")) == 0)
        report.check("derive: missing sources are named, not silently skipped",
                     isinstance(real["missing_sources"], list))

        # --- 4. views
        for name, fn in sorted(VIEWS.items()):
            lines = fn(control, node="C-001") if name == "node" else fn(control)
            report.check("view %s: <=%d lines and non-empty" % (name, MAX_VIEW_LINES),
                         0 < len(lines) <= MAX_VIEW_LINES, "%d lines" % len(lines))
        coverage = view_coverage(control)
        report.check("view coverage: names the mute figures F02 and F-103",
                     any("F02" in line for line in coverage)
                     and any("F-103" in line for line in coverage), coverage[:3])
        report.check("view coverage: A/B/C/D are all present",
                     all(any(line.startswith(letter) for line in coverage)
                         for letter in ("A.", "B.", "C.", "D.")), coverage[:2])
        contested = coverage_report(control)["contested_active"]
        report.check("view coverage D: C-002 carries a (-) edge and is still ACTIVE",
                     any(row["id"] == "C-002" for row in contested),
                     [row["id"] for row in contested])
        report.check("view coverage D: a node with no (-) edge stays out of D",
                     not any(row["id"] == "C-001" for row in contested))
        node_view = view_node(control, node="C-001")
        report.check("view node: shows both directions for one object",
                     any(line.startswith("in  (") for line in node_view)
                     and any(line.startswith("out (") for line in node_view)
                     and node_view[0].startswith("NODE  narr:C-001"), node_view[:2])
        missing_view = view_node(control, node="ro::narr:C-404")
        report.check("view node: an unknown object is [MISSING], not a crash",
                     "[MISSING]" in missing_view[0], missing_view[0])
        raised = False
        try:
            view_node(control)
        except GraphError:
            raised = True
        report.check("view node: refuses to guess which object you meant", raised)
        argument = view_argument(control, node="C-001")
        report.check("view argument: walks claim -> evidence",
                     any("supported_by" in line for line in argument), argument[:3])
        activity = view_role_activity(control)
        report.check("view role-activity: buckets by author",
                     any("derive" in line for line in activity), activity[:3])

        # --- 5. role edge proposals (contract 5)
        proposals = [
            {"from": "ro::run:proto-r01", "to": "ro::narr:C-002", "kind": "supported_by",
             "polarity": "+", "basis": "proto/r01@results.json"},
            {"from": "ro::run:proto-r99", "to": "ro::narr:C-001", "kind": "supported_by",
             "polarity": "-", "basis": "proto/r99@not-registered-yet"},
        ]
        submitted = submit_edges(control, "experiment-runner", "msg-001", proposals)
        report.check("receipt edges: a resolvable proposal is linked under the role's name",
                     len(submitted["linked"]) == 1
                     and submitted["linked"][0]["result"] == "LINKED"
                     and len(list_edges(control, by="experiment-runner")) == 1,
                     submitted["linked"])
        linked_edge = list_edges(control, by="experiment-runner")[0]
        report.check("receipt edges: the basis carries the message id",
                     "msg=msg-001" in (linked_edge.get("basis") or ""), linked_edge.get("basis"))
        report.check("receipt edges: an unresolvable end is parked, naming WHICH end",
                     len(submitted["pending"]) == 1
                     and submitted["pending"][0]["reason"].startswith("from=")
                     and pending_summary(control)["edges"] == 1,
                     submitted["pending"])
        report.check("receipt edges: the parked file lives under roles/pending_edges/",
                     (submitted["pending_file"] or "").endswith("roles/pending_edges/msg-001.json"),
                     submitted["pending_file"])
        denied = submit_edges(control, "figure-engineer", "msg-002", [
            {"from": "ro::run:proto-r01", "to": "ro::narr:C-001", "kind": "supported_by",
             "polarity": "+", "basis": "not mine to assert"}])
        report.check("receipt edges: an out-of-column proposal is EDGE_CLASS_DENIED",
                     "EDGE_CLASS_DENIED" in denied["flags"] and len(denied["denied"]) == 1
                     and not denied["linked"], denied["flags"])
        report.check("receipt edges: a denied proposal writes no edge and no pending file",
                     not list_edges(control, by="figure-engineer")
                     and pending_summary(control)["edges"] == 1,
                     len(list_edges(control, by="figure-engineer")))
        report.check("receipt edges: the refusal says which classes the role does own",
                     "fig->narr:expressed_as" in denied["denied"][0]["reason"],
                     denied["denied"][0]["reason"])
        nothing = replay_pending(control)
        report.check("replay: with the object still absent nothing is linked and nothing lost",
                     not nothing["linked"] and pending_summary(control)["edges"] == 1,
                     nothing["still_pending"])
        _write(control / "runs" / "proto-r99.json", json.dumps({
            "schema_version": "auto-research/run-v1", "run_id": "proto-r99",
            "protocol": "proto", "status": "failed", "result": "arrived late"},
            ensure_ascii=False, indent=2) + "\n")
        cache_clear(control)
        replayed = replay_pending(control)
        report.check("replay: once the object exists the parked edge lands",
                     len(replayed["linked"]) == 1
                     and replayed["linked"][0]["from"].endswith(":run:proto-r99"),
                     replayed["linked"])
        report.check("replay: the cleared file is removed and the count returns to 0",
                     replayed["cleared"] == ["msg-001"]
                     and pending_summary(control)["edges"] == 0
                     and not pending_files(control),
                     pending_summary(control))
        # v3 contract 6 restates the line: it is read out of current.json and
        # reports the relation layer's health, not a coverage recomputation.
        report.check("session line: edges / conflicts / pending / gone / last derive",
                     (session_line(control) or "").startswith("GRAPH edges=")
                     and "conflicts=" in session_line(control)
                     and "pending=0" in session_line(control)
                     and "gone=" in session_line(control)
                     and "derive: last " in session_line(control),
                     session_line(control))

        # --- 5b. R9 B10: the four negative cases the amendment demands
        # (i) the source stops saying it -> the derived edge is retracted, not left standing
        before = [e for e in list_edges(control, kind="expressed_as")
                  if e["basis"] == "derive:fig-registry"]
        _write(control / FIGURES_REGISTRY_REL, json.dumps({
            "schema": FIGURE_REGISTRY_SCHEMA,
            "figures": {
                "F-101": {"id": "F-101", "status": "superseded", "claim_ids": ["C-001"]},
                "F-102": {"id": "F-102", "status": "draft", "claim_ids": ["C-002"]},
                "F-103": {"id": "F-103", "status": "current", "claim_ids": []},
            }}, ensure_ascii=False, indent=2) + "\n")
        cache_clear(control)
        reconciled = derive(control)
        after = [e for e in list_edges(control, kind="expressed_as")
                 if e["basis"] == "derive:fig-registry"]
        report.check("B10-1 derive retracts a derived edge once its source stops stating it",
                     len(before) > 0 and not after
                     and reconciled["by_rule"]["fig-registry"]["retracted"] == len(before)
                     and reconciled["added"] == 0,
                     "before=%d after=%d retracted=%d"
                     % (len(before), len(after),
                        reconciled["by_rule"]["fig-registry"]["retracted"]))
        retired = [e for e in list_edges(control, kind="expressed_as", include_retracted=True)
                   if e["basis"] == "derive:fig-registry" and e.get("retracted")]
        report.check("B10-1 the retraction is an event, not an edited line",
                     bool(retired) and (retired[0].get("retraction") or {}).get("by") == "derive"
                     and "retracted_by" not in retired[0],
                     (retired[0].get("retraction") if retired else None))
        report.check("B10-1 derive is still idempotent after a reconciliation",
                     derive(control)["added"] == 0 and derive(control)["retracted"] == 0)

        # (ii) a hand edge of the opposite sign -> EDGE_CONFLICT, and it may not
        #      cancel the derived one
        derived_plus = [e for e in list_edges(control, by="derive", kind="supported_by")
                        if e["from"].endswith(":run:proto-r01") and e.get("polarity") == "+"]
        report.check("B10-2 fixture has a derived (+) edge to contest", len(derived_plus) == 1,
                     [e["edge_id"] for e in derived_plus])
        link(control, derived_plus[0]["from"], derived_plus[0]["to"], "supported_by",
             "hand re-reading of the same run", by="user", polarity="-")
        clashes = conflicting_edges(control)
        report.check("B10-2 opposite polarities on one logical key surface as EDGE_CONFLICT",
                     len(clashes) == 1 and clashes[0]["polarity"] is None
                     and len(clashes[0]["provenance"]) == 2,
                     [(c["from"], c["to"], sorted(c["by_polarity"])) for c in clashes])
        report_now = coverage_report(control)
        against_c001 = [a for row in report_now["contested_active"] if row["id"] == "C-001"
                        for a in row["against"]]
        report.check("B10-2 a contested relation counts as neither support nor refutation",
                     len(report_now["conflicts"]) == 1
                     and not any("proto-r01" in a for a in against_c001)
                     and format_ro(pid, "narr", "C-001") not in
                     {e["to"] for e in logical_edges(control)
                      if e["kind"] == "supported_by" and e["polarity"] == "+"
                      and e["from"].endswith(":run:proto-r01")},
                     "against=%s conflicts=%d" % (against_c001, len(report_now["conflicts"])))
        conflict_view = view_coverage(control)
        report.check("B10-2 coverage prints an E. EDGE_CONFLICT section",
                     any(line.startswith("E. EDGE_CONFLICT") for line in conflict_view)
                     and len(conflict_view) <= MAX_VIEW_LINES,
                     "%d lines" % len(conflict_view))
        try:
            unlink(control, derived_plus[0]["edge_id"], "I disagree", by="user")
            report.check("B10-2 a hand retraction may not cancel a derived edge", False)
        except GraphError as exc:
            report.check("B10-2 a hand retraction may not cancel a derived edge",
                         exc.text.startswith("DERIVED_EDGE_NOT_MANUALLY_RETRACTABLE"), exc.text)

        # (iii) replay re-authorises under the CURRENT policy, and refuses
        parked = submit_edges(control, "experiment-runner", "msg-004", [
            {"from": "ro::run:proto-r88", "to": "ro::narr:C-001", "kind": "supported_by",
             "polarity": "+", "basis": "a run that is not registered yet"}])
        report.check("B10-4 the proposal parks while its endpoint is absent",
                     len(parked["pending"]) == 1
                     and parked["pending"][0]["status"] == "PENDING"
                     and parked["pending"][0]["authorization_policy_version"]
                     == AUTHORIZATION_POLICY_VERSION,
                     parked["pending"])
        saved_policy = EDGE_CLASS_POLICY["experiment-runner"]
        EDGE_CLASS_POLICY["experiment-runner"] = (("run", "claim", "supported_by"),)
        try:
            rejudged = replay_pending(control)
        finally:
            EDGE_CLASS_POLICY["experiment-runner"] = saved_policy
        report.check("B10-4 replay re-authorises under the current policy and DENIES",
                     len(rejudged["denied"]) == 1
                     and rejudged["denied"][0]["message_id"] == "msg-004"
                     and "EDGE_CLASS_DENIED" in rejudged["denied"][0]["reason"]
                     and not rejudged["linked"],
                     rejudged["denied"])
        report.check("B10-4 a denied park stops being actionable but is kept for audit",
                     pending_summary(control)["actionable"] == 0
                     and pending_summary(control)["by_status"].get("DENIED") == 1,
                     pending_summary(control))

        # (iv) a park nobody can satisfy ages out to STALE instead of nagging forever
        submit_edges(control, "experiment-runner", "msg-005", [
            {"from": "ro::run:proto-r99b", "to": "ro::narr:C-001", "kind": "supported_by",
             "polarity": "+", "basis": "an object that never arrives"}])
        report.check("B10-3 the fresh park is actionable exactly once",
                     pending_summary(control)["actionable"] == 1,
                     pending_summary(control))
        replay_pending(control)
        mid = pending_summary(control)
        replay_pending(control)
        end = pending_summary(control)
        report.check("B10-3 three failed attempts turn the park STALE",
                     mid["actionable"] == 1 and end["actionable"] == 0
                     and end["by_status"].get("STALE") == 1,
                     "mid=%s end=%s" % (mid["by_status"], end["by_status"]))
        report.check("B10-3 a STALE park is kept on disk, not deleted",
                     end["total"] == 2 and len(pending_files(control)) == 2,
                     [p.name for p in pending_files(control)])
        report.check("B10-3 the session line stops counting stale parks",
                     "pending=0" in (session_line(control) or ""), session_line(control))

        # --- 5c. R9 B9: the promotion bar is reported, never applied
        promotion = promotion_status(control)
        report.check("B9 promotion-status is mechanical and read-only",
                     promotion["ready"] is False and len(promotion["criteria"]) == 2
                     and all("met" in row and "detail" in row for row in promotion["criteria"]),
                     [row["id"] for row in promotion["criteria"]])

        # ================================================================ v3
        # --- V1. contract 0/8.1: one transaction, and a crash it can finish
        signature = (lambda rows: sorted((r["from"], r["to"], r["kind"], r.get("polarity"),
                                          bool(r.get("conflict")), r.get("sources"))
                                         for r in rows))
        payload, stale = current_state(control)
        report.check("v3-0 every write leaves current.json matching the event stream",
                     payload is not None and not stale
                     and payload["last_event_id"] == stream_head(control)[1]
                     and signature(payload["edges"]) == signature(logical_edges(control)),
                     "stale=%s events=%s/%s" % (stale, (payload or {}).get("event_count"),
                                                stream_head(control)[0]))
        report.check("v3-0 graph/ carries the four transaction artefacts",
                     current_path(control).exists() and edges_path(control).exists()
                     and journal_dir(control).exists() and derive_log_path(control).exists(),
                     sorted(p.name for p in graph_dir(control).iterdir()))

        # (a) crash AFTER the append, BEFORE the projection: the reader refuses
        #     to answer out of a projection it knows is behind.
        smuggled = make_edge(control, format_ro(pid, "tree", "R01.A02"),
                             format_ro(pid, "narr", "C-003"), "supported_by",
                             "written behind the transaction's back", "user", "+")
        with edges_path(control).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(smuggled, ensure_ascii=False) + "\n")
        cache_clear(control)
        _payload, stale_now = current_state(control)
        report.check("v3-0 an event written outside the transaction marks current.json stale",
                     stale_now is True)
        report.check("v3-6 the SessionStart line says GRAPH_REBUILD_REQUIRED",
                     any(line.startswith("GRAPH_REBUILD_REQUIRED")
                         for line in session_lines(control)), session_lines(control))
        refused = ""
        try:
            view_coverage(control)
        except GraphError as exc:
            refused = exc.text
        report.check("v3-0 a view refuses a stale projection instead of answering from it",
                     refused.startswith("GRAPH_REBUILD_REQUIRED"), refused)
        rebuilt = rebuild(control, reason="self-test")
        payload, stale = current_state(control)
        report.check("v3-0 `graph rebuild` makes current.json agree with the stream again",
                     rebuilt["result"] == "REBUILT" and not stale
                     and signature(payload["edges"]) == signature(logical_edges(control))
                     and any(e["from"].endswith(":tree:R01.A02") for e in payload["edges"]),
                     "%d logical edge(s)" % len(payload["edges"]))

        # (b) crash INSIDE the transaction: journalled and stamped, never appended.
        orphan = make_edge(control, format_ro(pid, "run", "proto-r01"),
                           format_ro(pid, "narr", "C-003"), "supported_by",
                           "interrupted between the journal and the append", "user", "+")
        with graph_lock(control):
            staged = _stamp_rows(control, [orphan])
            _journal_write(control, {"schema": JOURNAL_SCHEMA,
                                     "tx_id": "tx_" + "selftestcrash",
                                     "kind": "link", "status": "PREPARED", "at": utc_now(),
                                     "base_event_count": stream_head(control)[0],
                                     "base_event_id": stream_head(control)[1],
                                     "detail": {"simulated": "crash"}, "events": staged})
        events_before = len(read_edge_events(control))
        replay = rebuild(control, reason="self-test crash replay")
        cache_clear(control)
        report.check("v3-8.1 a crash between journal and append is replayed, once",
                     len(read_edge_events(control)) == events_before + 1
                     and any(r.get("result") == "REPLAYED" for r in replay["replayed"])
                     and any(e["edge_id"] == orphan["edge_id"] for e in active_edges(control)),
                     replay["replayed"])
        again = rebuild(control, reason="self-test crash replay twice")
        report.check("v3-8.1 replaying a finished transaction adds nothing",
                     len(read_edge_events(control)) == events_before + 1
                     and not again["replayed"], again["replayed"])
        payload, stale = current_state(control)
        report.check("v3-8.1 after the replay the projection equals the stream",
                     not stale
                     and signature(payload["edges"]) == signature(logical_edges(control)))

        # (c) hook_guard: the relation layer is not hand-editable
        def _guard(path):
            import subprocess
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent / "hook_guard.py")],
                input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": path}}),
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            try:
                return json.loads(proc.stdout or "{}")
            except json.JSONDecodeError:
                return {}

        blocked_current = _guard(str(current_path(control)))
        blocked_edges = _guard(str(edges_path(control)))
        allowed = _guard(str(control / "decisions.md"))
        report.check("v3-8.1 hook_guard blocks Edit/Write on graph/*.json and edges.jsonl",
                     blocked_current.get("decision") == "block"
                     and blocked_edges.get("decision") == "block"
                     and "research_graph.py" in (blocked_current.get("reason") or "")
                     and not allowed,
                     [blocked_current.get("decision"), blocked_edges.get("decision"), allowed])

        # --- V2. contract 1: the object index, and what `gone` does to a derived edge
        built = index_build(control)
        index = load_objects(control)
        mismatched = []
        for space in SPACES:
            live = [i for i in enumerate_space(control, space)
                    if resolve(control, format_ro(pid, space, i))["exists"]]
            indexed = [a for a, e in index.items()
                       if e.get("space") == space and e.get("status") != "gone"]
            if len(live) != len(indexed):
                mismatched.append("%s resolve=%d index=%d" % (space, len(live), len(indexed)))
        report.check("v3-1 index build: all eight spaces agree with resolve",
                     built["objects"] > 0 and not mismatched, mismatched or built["by_space"])
        report.check("v3-1 the index records source_path and a fingerprint per object",
                     all(e.get("fingerprint") and e.get("space") for e in index.values())
                     and any(e.get("source_path") for e in index.values()))

        doomed = format_ro(pid, "run", "proto-r02")
        edges_of_doomed = [e for e in active_edges(control) if e["from"] == doomed]
        (control / "runs" / "proto-r02.json").unlink()
        cache_clear(control)
        scoped = index_update(control, ["asset"])
        updated = index_update(control)
        after_index = load_objects(control)
        report.check("v3-1 a vanished object is marked GONE and KEPT, not deleted",
                     doomed in after_index
                     and after_index[doomed].get("status") == "gone"
                     and after_index[doomed].get("gone_at")
                     and updated["gone"] >= 1, updated)
        report.check("v3-1 index update only re-scans the space whose fingerprint moved",
                     scoped["result"] == "INDEX_UNCHANGED"
                     and updated["scanned"] == ["run"]
                     and set(updated["skipped"]) == set(SPACES) - {"run"},
                     [scoped["result"], updated["scanned"], updated["skipped"]])
        report.check("v3-1 view objects marks it [GONE] inside the budget",
                     any("[GONE]" in line for line in view_objects(control, space="run"))
                     and len(view_objects(control)) <= MAX_VIEW_LINES,
                     view_objects(control, space="run")[:3])
        reconciled_gone = derive(control, reason="self-test gone")
        report.check("v3-1/8.2 derive takes back the edges of a gone object",
                     len(edges_of_doomed) > 0
                     and not any(e["from"] == doomed for e in active_edges(control))
                     and reconciled_gone["retracted"] >= len(edges_of_doomed),
                     "had=%d retracted=%d" % (len(edges_of_doomed),
                                              reconciled_gone["retracted"]))
        report.check("v3-1 the retracted-for-gone edges are out of current.json too",
                     not any(e["from"] == doomed
                             for e in (read_json(current_path(control), {}) or {}).get("edges", [])))

        # --- V3. contract 2: the API the host CLIs call, from a stub caller
        _write(control / FIGURES_REGISTRY_REL, json.dumps({
            "schema": FIGURE_REGISTRY_SCHEMA,
            "figures": {"F-101": {"id": "F-101", "status": "current", "claim_ids": ["C-002"]}},
        }, ensure_ascii=False, indent=2) + "\n")
        cache_clear(control)
        host = derive(control, rules=["fig-registry"],
                      reason="figure_router quick --promote-current")
        materialised = read_json(current_path(control), {}) or {}
        wanted = (format_ro(pid, "fig", "F-101"), format_ro(pid, "narr", "C-002"),
                  "expressed_as")
        report.check("v3-2/8.3 a host CLI calls derive() and current.json already has the edge",
                     host["ok"] and host["added"] >= 1
                     and any((e["from"], e["to"], e["kind"]) == wanted
                             for e in materialised.get("edges") or []),
                     [host.get("added"), host.get("rules")])
        report.check("v3-2 derive() restricted to one rule leaves the other rules alone",
                     set(host["rules"]) == {"fig-registry", "fsg-claim"}
                     and "lineage" not in host["by_rule"], host["rules"])
        log_tail = derive_log(control, limit=1)
        report.check("v3-2 graph/derive.log records trigger, rules, counts and duration",
                     len(log_tail) == 1
                     and log_tail[0]["trigger"] == "figure_router quick --promote-current"
                     and log_tail[0]["added"] == host["added"]
                     and "retracted" in log_tail[0] and isinstance(log_tail[0]["ms"], int),
                     log_tail[0] if log_tail else None)
        report.check("v3-6 current.json carries the last derive for the SessionStart line",
                     (materialised.get("derive") or {}).get("trigger")
                     == "figure_router quick --promote-current"
                     and "by figure_router" in (session_line(control) or ""),
                     session_line(control))
        incremental = derive(control, since=host["last_event_id"], reason="self-test since")
        report.check("v3-2 derive --since skips the rules whose sources have not moved",
                     incremental["ok"] and incremental["added"] == 0
                     and len(incremental["skipped_unchanged"]) == len(DERIVE_RULES)
                     and not incremental["rules"], incremental["skipped_unchanged"])
        broken = derive(control, rules=["no-such-rule"], reason="self-test failure path")
        report.check("v3-2 a failing derive records an incident and never raises",
                     broken["ok"] is False and broken["added"] == 0
                     and "UNKNOWN_RULE" in (broken.get("error") or "")
                     and len(open_incidents(control)) == 1,
                     broken.get("incident"))

        # --- V4. contract 3: the role's graph context
        context = graph_context(control, ["ro::narr:C-001", "figs/f1.py", "C-003"],
                                "experiment-runner")
        report.check("v3-3 graph_context is <=40 lines and inside the envelope budget",
                     0 < len(context["lines"]) <= MAX_VIEW_LINES
                     and len(canonical(context).encode("utf-8")) <= GRAPH_CONTEXT_MAX_BYTES,
                     "%d lines / %d bytes" % (len(context["lines"]),
                                              len(canonical(context).encode("utf-8"))))
        report.check("v3-3 it carries a node view for each resolvable ref, refs only",
                     [o["ro"] for o in context["objects"]]
                     == [format_ro(pid, "narr", "C-001"), format_ro(pid, "narr", "C-003")],
                     [o["ro"] for o in context["objects"]])
        blocks = []
        for line in context["lines"]:
            if line.startswith("NODE "):
                blocks.append(0)
            elif blocks and line.startswith("  "):
                blocks[-1] += 1
        report.check("v3-3 no object's node view exceeds 8 edges",
                     bool(blocks) and all(n <= GRAPH_CONTEXT_MAX_EDGES + 1 for n in blocks),
                     blocks)
        report.check("v3-3 the EDGE_CONFLICT in this role's own edge class is included",
                     len(context["conflicts"]) == 1
                     and context["conflicts"][0]["kind"] == "supported_by"
                     and any(line.startswith("EDGE_CONFLICT") for line in context["lines"]),
                     context["conflicts"])
        blank = tmp / "no-graph" / ".research-os"
        blank.mkdir(parents=True, exist_ok=True)
        _write(blank / "state.json", json.dumps(
            {"schema_version": "auto-research/v2", "project_id": "no-graph",
             "title": "no graph"}, ensure_ascii=False) + chr(10))
        report.check("v3-3 a project with no relation layer gets {}, not a stub",
                     graph_context(blank, ["C-001"], "chronicler") == {})

        # --- V6. contract 5: cross-project, read-only
        second = tmp / "second-paper"
        second_control = second / ".research-os"
        second_control.mkdir(parents=True, exist_ok=True)
        _write(second_control / "state.json", json.dumps(
            {"schema_version": "auto-research/v2", "project_id": "second-paper",
             "title": "Second paper", "status": "active",
             "artifacts": [{"id": "tab-cost", "path": "b/cost.tex", "role": "evidence",
                            "tags": ["table"], "status": "current"}]},
            ensure_ascii=False, indent=2) + "\n")
        _write(second_control / "tree.json", json.dumps(
            {"schema_version": "auto-research/tree-v1",
             "nodes": {"R01": {"kind": "attempt", "title": "replicate the price of search",
                               "status": "success"}}}, ensure_ascii=False, indent=2) + "\n")
        cache_clear()
        link(second_control, "ro::tree:R01", "ro::asset:tab-cost", "depends_on",
             "the replication needs the cost table")
        index_build(second_control)
        saved_portfolio = ros.load_portfolio
        try:
            ros.load_portfolio = lambda: {
                "schema_version": "auto-research/portfolio-v1",
                "projects": [{"root": str(project_root(control)), "project_id": pid},
                             {"root": str(second), "project_id": "second-paper"}]}
            registered = portfolio_entries()
            for view in ("coverage", "role-activity", "conflicts"):
                lines = cross_project_view(view)
                report.check("v3-5 --all-projects view %s: <=%d lines, both projects"
                             % (view, MAX_VIEW_LINES),
                             len(lines) <= MAX_VIEW_LINES
                             and sum(1 for line in lines if line.startswith("second-paper")) == 1
                             and sum(1 for line in lines if line.startswith(pid)) == 1,
                             lines[:4])
            report.check("v3-5 each project spends at most 5 lines",
                         len(cross_project_view("coverage")) <= 1 + CROSS_PROJECT_LINES * 2,
                         len(cross_project_view("coverage")))
            found = find_edges(registered, kind="expressed_as", to_space="narr",
                               title_like="C-002")
            report.check("v3-5 --all-projects find returns full ro: addresses in their "
                         "own project",
                         len(registered) == 2 and found["projects"] == 2
                         and found["hits"]
                         and all(h["kind"] == "expressed_as" and ":narr:" in h["to"]
                                 and h["from"].startswith("ro:") for h in found["hits"])
                         and len(found["lines"]) <= MAX_VIEW_LINES,
                         [h["from"] for h in found["hits"]][:3])
            cross = find_edges(registered, kind="depends_on", to_space="asset")
            report.check("v3-5 find reaches into the OTHER project's current.json",
                         any(h["project"] == "second-paper" for h in cross["hits"]),
                         [h["project"] for h in cross["hits"]])
            before_second = edges_path(second_control).read_text(encoding="utf-8")
            cross_project_view("coverage")
            find_edges(registered, kind="depends_on")
            report.check("v3-5 cross-project reading never writes another project",
                         edges_path(second_control).read_text(encoding="utf-8")
                         == before_second)
        finally:
            ros.load_portfolio = saved_portfolio

        # --- V7. snapshots and the HTML forwarder
        first_snap = snapshot(control, "before-submission", milestone=True)
        for index_n in range(3):
            snapshot(control, "routine-%d" % index_n, keep=2)
        kept = snapshots(control)
        report.check("v3-0 snapshot copies the three files and rotates only the ordinary ones",
                     set(first_snap["files"]) == {"edges.jsonl", "objects.json", "current.json"}
                     and any(s.get("milestone") for s in kept)
                     and len([s for s in kept if not s.get("milestone")]) == 2,
                     [s["dir"] for s in kept])
        report.check("v3-0 a snapshot records the event id it froze",
                     first_snap["meta"]["last_event_id"] == stream_head(control)[1])
        # contract 4 lives in graph_render_html.py; what belongs to THIS module is
        # the forward and the refusal, so both are exercised against a stub.
        class _StubRenderer:
            calls = []

            @staticmethod
            def render(control_arg, view_arg, out_arg):
                _StubRenderer.calls.append((str(control_arg), view_arg, str(out_arg)))
                return {"result": "RENDERED", "out": str(out_arg), "view": view_arg}

        saved_renderer = sys.modules.get("graph_render_html")
        try:
            sys.modules["graph_render_html"] = _StubRenderer
            forwarded = render_html(control, "conflicts", tmp / "graph.html")
            report.check("v3-4 render --html forwards (control, view, out) unchanged",
                         forwarded["result"] == "RENDERED"
                         and _StubRenderer.calls
                         == [(str(control), "conflicts", str(tmp / "graph.html"))],
                         _StubRenderer.calls)

            class _Empty:
                pass

            sys.modules["graph_render_html"] = _Empty
            render_error = ""
            try:
                render_html(control, "argument", tmp / "graph.html")
            except GraphError as exc:
                render_error = "%s|%d" % (exc.text, exc.code)
            report.check("v3-4 a missing renderer is named and exits 2",
                         render_error.startswith("GRAPH_RENDER_UNAVAILABLE")
                         and render_error.endswith("|2"), render_error)
        finally:
            if saved_renderer is None:
                sys.modules.pop("graph_render_html", None)
            else:
                sys.modules["graph_render_html"] = saved_renderer

        # --- 6. a missing-source project reports 0 instead of failing
        bare = tmp / "bare" / ".research-os"
        bare.mkdir(parents=True, exist_ok=True)
        _write(bare / "state.json", json.dumps(
            {"schema_version": "auto-research/v2", "project_id": "bare", "title": "bare"}) + "\n")
        cache_clear()
        empty = derive(bare, dry_run=True)
        report.check("derive on an empty project: 0 edges, 8 named gaps",
                     empty["added"] == 0 and len(empty["missing_sources"]) == 8,
                     empty["missing_sources"])
        report.check("session line: a project with no edge ledger stays silent",
                     session_line(bare) is None, session_line(bare))
    finally:
        os.environ.pop("AR_SESSION", None)
        if saved is not None:
            os.environ["AR_SESSION"] = saved
        cache_clear()
        shutil.rmtree(str(tmp), ignore_errors=True)
    passed = sum(1 for row in report.rows if row["ok"])
    print(json.dumps({"ok": report.ok, "passed": passed, "total": len(report.rows),
                      "checks": report.rows}, ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


# ============================================================ CLI

def emit(payload) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_resolve(args) -> int:
    control = resolve_control(args.root)
    return emit(dict({"ok": True, "action": "resolve"}, **resolve(control, args.address)))


def cmd_link(args) -> int:
    control = resolve_control(args.root)
    if args.replay_pending:
        if args.from_ro or args.to_ro or args.kind or args.basis:
            raise GraphError("--replay-pending replays parked proposals; it takes no "
                             "--from/--to/--kind/--basis", 2)
        result = replay_pending(control)
        return emit(dict({"ok": True, "action": "link-replay-pending",
                          "linked_count": len(result["linked"]),
                          "still_pending_count": len(result["still_pending"])}, **result))
    missing = [name for name, value in (("--from", args.from_ro), ("--to", args.to_ro),
                                        ("--kind", args.kind), ("--basis", args.basis))
               if not value]
    if missing:
        raise GraphError("link needs %s (or --replay-pending)" % ", ".join(missing), 2)
    # R9 B7: a role identity may only arrive through a trusted role receipt
    # (role_runtime -> submit_edges).  A direct CLI writer is `main`, which owns
    # no edge class and therefore cannot borrow a role's authority.
    author = args.by or "main"
    if author in EDGE_CLASS_POLICY:
        raise GraphError(
            "ROLE_IDENTITY_NOT_SELF_ASSERTED --by=%s is a role: role edges are booked "
            "from a validated `role receipt` (proposed_edges[]), not from the CLI. "
            "Use --by main|user, or send the proposal through the receipt." % author, 2)
    result = link(control, args.from_ro, args.to_ro, args.kind, args.basis,
                  by=author, polarity=args.polarity)
    return emit(dict({"ok": True, "action": "link"}, **result))


def cmd_retract(args) -> int:
    control = resolve_control(args.root)
    result = retract(control, args.edge_id, args.reason, by=args.by or "main")
    return emit(dict({"ok": True, "action": "retract"}, **result))


def cmd_promotion_status(args) -> int:
    control = resolve_control(args.root)
    payload = promotion_status(control)
    if getattr(args, "json", False):
        return emit(dict({"ok": True, "action": "promotion-status"}, **payload))
    print("PROMOTION STATUS  %s  window=%dd"
          % ("READY" if payload["ready"] else "NOT READY", payload["window_days"]))
    for row in payload["criteria"]:
        print("  [%s] %-26s %s" % ("x" if row["met"] else " ", row["id"], row["detail"]))
    print("  %s" % payload["note"])
    return 0


def cmd_list(args) -> int:
    control = resolve_control(args.root)
    rows = list_edges(control, from_ro=args.from_ro, to_ro=args.to_ro, kind=args.kind,
                      by=args.by, include_retracted=args.include_retracted)
    if args.json:
        return emit({"ok": True, "action": "list", "count": len(rows), "edges": rows})
    if not rows:
        print("(no edges)")
        return 0
    for edge in rows:
        sign = {"+": "+", "-": "-"}.get(edge.get("polarity"), " ")
        state = "  RETRACTED" if edge.get("retracted") else ""
        print("%s  %-13s%s  %-26s -> %-26s  [%s | %s]%s"
              % (edge["edge_id"], edge["kind"], sign, _short(edge["from"]),
                 _short(edge["to"]), edge.get("by"), clip(edge.get("basis"), 32), state))
    print("%d edge(s)" % len(rows))
    return 0


def cmd_derive(args) -> int:
    control = resolve_control(args.root)
    result = derive(control, rules=args.rules, reason=args.reason or "cli",
                    since=args.since, dry_run=args.dry_run)
    payload = dict({"ok": True, "action": "derive"}, **result)
    emit(payload)
    return 0 if result.get("ok", True) else 1


def cmd_rebuild(args) -> int:
    control = resolve_control(args.root)
    return emit(dict({"ok": True, "action": "rebuild"},
                     **rebuild(control, reason=args.reason or "cli")))


def cmd_snapshot(args) -> int:
    control = resolve_control(args.root)
    if args.list:
        rows = snapshots(control)
        return emit({"ok": True, "action": "snapshot-list", "count": len(rows),
                     "snapshots": rows})
    if not args.label:
        raise GraphError("snapshot needs --label (or --list)", 2)
    return emit(dict({"ok": True, "action": "snapshot"},
                     **snapshot(control, args.label, keep=args.keep,
                                milestone=args.milestone)))


def cmd_index(args) -> int:
    control = resolve_control(args.root)
    spaces = [args.space] if args.space else None
    if args.action == "build":
        result = index_build(control, spaces)
    else:
        result = index_update(control, spaces, force=args.force)
    return emit(dict({"ok": True, "action": "index-" + args.action}, **result))


def cmd_find(args) -> int:
    if args.all_projects:
        targets = portfolio_entries()
    else:
        targets = [{"control": resolve_control(args.root)}]
    result = find_edges(targets, kind=args.kind, to_space=args.to_space,
                        from_space=args.from_space, title_like=args.title_like)
    if args.json:
        return emit(dict({"ok": True, "action": "find"}, **result))
    for line in result["lines"]:
        print(line)
    return 0


def cmd_render(args) -> int:
    control = resolve_control(args.root)
    if not args.html:
        raise GraphError("render currently renders one thing: `graph render --html "
                         "--view <argument|coverage|conflicts|objects> --out <file>`", 2)
    return emit(dict({"ok": True, "action": "render"},
                     **render_html(control, args.view, args.out)))


def cmd_view(args) -> int:
    if getattr(args, "all_projects", False):
        lines = cross_project_view(args.view)
    else:
        control = resolve_control(args.root)
        lines = VIEWS[args.view](control, node=args.node or args.target,
                                 space=args.space, status=args.status)
    if args.json:
        return emit({"ok": True, "action": "view", "view": args.view,
                     "all_projects": bool(getattr(args, "all_projects", False)),
                     "lines": lines})
    for line in lines:
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research_graph.py",
        description="auto-research research graph: ro: addressing, the cross-store edge "
                    "ledger, its derivation rules and its <=40-line projections "
                    "(docs/role-fleet.md section 5, docs/v3-contract.md).")
    parser.add_argument("--all-projects", dest="all_projects", action="store_true",
                        help="read-only across the portfolio registry (v3 contract 5): "
                             "`graph --all-projects view coverage|role-activity|conflicts`, "
                             "`graph --all-projects find --kind … --to-space … --title-like …`")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_root(target):
        target.add_argument("--root", help="project root, .research-os dir, or any path "
                                           "inside the project (default: search upwards)")
        return target

    p = with_root(sub.add_parser("resolve", help="read-only: ro address -> {file, object, exists}"))
    p.add_argument("address", help="ro:<project>:<space>:<id> (project may be empty)")
    p.set_defaults(func=cmd_resolve)

    p = with_root(sub.add_parser("link", help="append one edge (both ends must resolve), "
                                         "or replay the parked role proposals"))
    p.add_argument("--from", dest="from_ro")
    p.add_argument("--to", dest="to_ro")
    p.add_argument("--kind", choices=EDGE_KINDS)
    p.add_argument("--polarity", choices=POLARITIES, help="supported_by only")
    p.add_argument("--basis", help="why this edge exists")
    p.add_argument("--by", help="role | main | user (default: main)")
    p.add_argument("--replay-pending", dest="replay_pending", action="store_true",
                   help="retry every roles/pending_edges/*.json proposal; clear what links")
    p.set_defaults(func=cmd_link)

    p = with_root(sub.add_parser("retract", aliases=["unlink"],
                                 help="append a retraction event (never deletes); a "
                                      "derived edge is reconciled by `derive`, not by hand"))
    p.add_argument("edge_id")
    p.add_argument("--reason", required=True, help="why this relation no longer holds")
    p.add_argument("--by")
    p.set_defaults(func=cmd_retract)

    p = with_root(sub.add_parser("promotion-status",
                                 help="read-only: does the ledger meet the contract 7 "
                                      "promotion bar? (never promotes anything)"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_promotion_status)

    p = with_root(sub.add_parser("list", help="list edges"))
    p.add_argument("--from", dest="from_ro")
    p.add_argument("--to", dest="to_ro")
    p.add_argument("--kind", choices=EDGE_KINDS)
    p.add_argument("--by")
    p.add_argument("--include-retracted", dest="include_retracted", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = with_root(sub.add_parser("derive", help="deterministic, idempotent edge derivation"))
    p.add_argument("--dry-run", dest="dry_run", action="store_true")
    p.add_argument("--rules", help="comma-separated subset, e.g. fig-registry,fsg-claim")
    p.add_argument("--since", dest="since",
                   help="an event id from a previous derive: skip rules whose source "
                        "spaces have not changed since then")
    p.add_argument("--reason", help="who triggered this (lands in graph/derive.log)")
    p.set_defaults(func=cmd_derive)

    p = with_root(sub.add_parser("rebuild",
                                 help="replay any interrupted transaction and re-project "
                                      "graph/current.json from the event stream"))
    p.add_argument("--reason")
    p.set_defaults(func=cmd_rebuild)

    p = with_root(sub.add_parser("snapshot",
                                 help="freeze edges.jsonl + objects.json + current.json "
                                      "under graph/snapshots/"))
    p.add_argument("--label", help="short name for this snapshot")
    p.add_argument("--keep", type=int, help="rotate ordinary snapshots down to N")
    p.add_argument("--milestone", action="store_true",
                   help="never rotate this one away")
    p.add_argument("--list", action="store_true", help="list snapshots instead")
    p.set_defaults(func=cmd_snapshot)

    p = with_root(sub.add_parser("index", help="the object index (graph/objects.json)"))
    p.add_argument("action", choices=["build", "update"])
    p.add_argument("--space", choices=SPACES, help="update one space only")
    p.add_argument("--force", action="store_true",
                   help="re-scan even when the source fingerprint is unchanged")
    p.set_defaults(func=cmd_index)

    p = with_root(sub.add_parser("find",
                                 help="read-only search over materialised relations; with "
                                      "--all-projects, across the portfolio"))
    p.add_argument("--kind", choices=EDGE_KINDS)
    p.add_argument("--from-space", dest="from_space", choices=SPACES)
    p.add_argument("--to-space", dest="to_space", choices=SPACES)
    p.add_argument("--title-like", dest="title_like")
    p.add_argument("--all-projects", dest="all_projects", action="store_true",
                   default=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_find)

    p = with_root(sub.add_parser("render", help="render the graph interface (contract 4)"))
    p.add_argument("--html", action="store_true")
    p.add_argument("--view", choices=["argument", "coverage", "conflicts", "objects"],
                   default="argument")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_render)

    p = with_root(sub.add_parser("view", help="<=40-line projection"))
    p.add_argument("view", choices=sorted(VIEWS))
    p.add_argument("target", nargs="?", help="the object, for `view node <ro>`")
    p.add_argument("--node", help="starting node (ro address or bare narrative id)")
    p.add_argument("--space", choices=SPACES, help="view objects: one space")
    p.add_argument("--status", help="view objects: present | gone")
    p.add_argument("--all-projects", dest="all_projects", action="store_true",
                   default=argparse.SUPPRESS,
                   help="coverage | role-activity | conflicts across the portfolio")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_view)

    sub.add_parser("self-test", help="fixture-backed regression of the whole surface")
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "graph":     # `graph <sub>` per role-fleet section 7
        argv = argv[1:]
    args = build_parser().parse_args(argv)
    if args.command == "self-test":
        return run_self_test()
    try:
        return args.func(args)
    except GraphError as exc:
        print(json.dumps({"ok": False, "error": exc.text, "code": exc.code,
                          "payload": exc.payload}, ensure_ascii=False), file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
