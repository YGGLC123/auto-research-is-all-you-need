#!/usr/bin/env python3
"""auto-research narrative time-tree core (N track) -- docs/narrative-contract.md v1.2.

The narrative tree is the project's *authoritative story state*: what the paper
currently claims, which node carries which structural role, what was killed and
on what basis, and -- crucially -- whether each structural change was forced by
evidence or merely chosen.  It is a content-addressed, append-only commit graph
(git-shaped, but over a typed research tree rather than files).

Layers (contract §0):
  authoritative   objects/ commits/ refs/heads/* branches/registry.json tags/
                  -- tree snapshots, commits and refs are written by exactly ONE
                     transaction: `narrative commit` (§6.1).
  derived cache   card.json/card.md, trajectory, renders, index/
                  -- deletable, always carries source_commit_id, never hand-edited.
  auxiliary       annotations/ incidents/ backfill/ runtime/
                  -- append-only, outside the tree hash.

Discipline that is mechanical here, not advisory:
  * identity is a hash of the typed identity_key -- change the key, get a new id
    (§1); the only exception is the explicitly approved `rekey_identity` op.
  * `kind` (refine/restructure/overthrow/merge) is computed by the CLI from the
    ops, never taken from the model's proposal (§3.1).
  * cause/forced_status live in one matrix with the iron law
    `cause=forced <=> forced_status=confirmed` (§3.2).
  * replacing the root requires a user-approved decision record, or exit 1
    `OVERTHROW_REQUIRES_USER_APPROVAL` with nothing written (§3.4).
  * a demoted-but-still-held former lever raises LEVER_DEBT; a dropped former
    headline with no adjudication raises LOST_LEVER (§4).  The system never
    auto-drops a branch (§5).

Reuses research_os for locking / events / atomic writes (same-dir import), the
same way procedures.py and collab.py do.  stdlib-only, Python >= 3.9.
Exit codes: 0 ok, 1 contract refusal, 2 environment/usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros  # noqa: E402

# ============================================================ §0  constants / layout

TREE_SCHEMA = "auto-research/narrative-tree-v1"
COMMIT_SCHEMA = "auto-research/narrative-commit-v1"
PATCH_SCHEMA = "auto-research/narrative-patch-v1"
RECEIPT_SCHEMA = "auto-research/receipt-v1"

ROLES = ("headline", "flagship", "backbone", "empirical_core", "foil")
LEVER_ROLES = ("headline", "flagship", "backbone")

NODE_TYPES = ("claim", "theorem", "evidence", "narrative", "artifact", "decision")
IDENTITY_KEYS = {  # contract §1 -- the typed identity of each node kind
    "claim": ("subject", "target", "predicate", "quantifier", "domain", "conditions", "polarity"),
    "theorem": ("formal_hash", "hypotheses", "conclusion"),
    "evidence": ("estimand", "population", "sample_window", "method", "artifact_ref"),
    "narrative": ("role_hint", "thesis"),
    "artifact": ("ref", "kind"),
    "decision": ("decision_ref",),
}

EPISTEMIC = ("PENDING", "HELD", "KILLED")
NARRATIVE = ("ACTIVE", "DROPPED", "DEMOTED", "MERGED")
# §4 legal combinations.  KILLED may never be ACTIVE (nor demoted/merged).
LEGAL_STATES = {(n, e) for n in ("ACTIVE", "DEMOTED", "MERGED") for e in ("PENDING", "HELD")}
LEGAL_STATES |= {("DROPPED", e) for e in ("PENDING", "HELD", "KILLED")}

# §4 -- a KILLED node must point at one of these adjudicating basis kinds.
KILL_BASIS_KINDS = ("refutation", "null-result", "theorem-failure", "novelty-collision",
                    "contract-incompatibility", "external-fact", "review-finding-with-evidence")
# §4 -- classes that count as "a new refutation" when testing ROLE_REVERSAL.
REFUTING_TRIGGERS = ("refutation", "null-result", "theorem-failure")

TRIGGER_CLASSES = ("refutation", "null-result", "theorem-failure", "novelty-collision",
                   "contract-incompatibility", "review-pressure", "user-taste", "scope", "other")

KINDS = ("refine", "restructure", "overthrow", "merge")
CAUSES = ("none", "forced", "unforced", "unknown")
FORCED_STATUS = ("none", "candidate", "confirmed")

# §5 branch disposition events.  A promoted branch is MERGED (the contract's
# §4 vocabulary); whether that merge was a fast-forward or a two-parent
# commit lives in the event's reason/meta, NOT in a second enum value.
DISPOSITIONS = ("OPEN", "MERGED", "KILLED", "DROPPED")
DETACH_DISPOSITIONS = ("DROPPED", "MERGED")

# §1 -- id regex, widened (D1 fix) to allow the section-style dotted ids the historical
# backfill reuses verbatim from the historical ledger, e.g. `N-4.3.2`.
ID_RE = re.compile(r"^[A-Z]{1,2}-[0-9]+(\.[0-9]+)*$")
BRANCH_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")

PATCHABLE_FIELDS = ("title", "summary", "statement", "state", "state_meta",
                    "basis_refs", "disposition_meta", "lineage")
EXPRESSION_FIELDS = ("title", "summary", "statement")  # §5 narrow auto-merge window
LINEAGE_RELS = ("clarifies", "narrows", "broadens", "replaces", "decomposes")

DEFAULT_BEARING_GLOBS = ["*叙事*", "*narrative*", "decisions.md", "*旗舰*", "*路线*",
                         "*裁决*", "pro_reviews/**"]

ROLE_REVERSAL_DAYS = 60
AXIS_DEFAULT_DEPTH = 4
TRAJECTORY_BUDGET = {"current": 4, "graph": 14, "open_loops": 6,
                     "hotspots": 5, "unresolved": 5, "history": 6}
CARD_MD_MAX = 120
CARD_BRIEF_MAX = 40


class NarrativeError(Exception):
    """A contract refusal (exit 1).  Carries a stable machine code."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}{': ' + detail if detail else ''}")
        self.code = code
        self.detail = detail


def refuse(code: str, detail: str = "") -> "None":
    raise NarrativeError(code, detail)


# ---------------------------------------------------------------- paths

def ndir(control: Path) -> Path:
    return control / "narrative"


def objects_dir(control: Path) -> Path:
    return ndir(control) / "objects"


def commits_dir(control: Path) -> Path:
    return ndir(control) / "commits"


def refs_dir(control: Path) -> Path:
    return ndir(control) / "refs" / "heads"


def registry_path(control: Path) -> Path:
    return ndir(control) / "branches" / "registry.json"


def working_path(control: Path, ref: str) -> Path:
    return ndir(control) / "working" / f"{ref}.json"


def index_path(control: Path) -> Path:
    return ndir(control) / "index" / "objects.json"


def config_path(control: Path) -> Path:
    return ndir(control) / "config.json"


def journal_dir(control: Path) -> Path:
    return ndir(control) / "journal"


def pending_dir(control: Path) -> Path:
    return ndir(control) / "pending"


def receipts_dir(control: Path) -> Path:
    return control / "runtime" / "receipts"


def decisions_dir(control: Path) -> Path:
    """Aux store for machine-checkable decision records.

    The contract requires approval refs to *point at* a user-approved decision
    record (§3.4/§4) but does not define its format, and `.research-os/decisions.md`
    (state-contract) is free prose.  So we resolve a ref two ways: a structured
    `narrative/decisions/<slug>.json` with `user_approved: true` (written by the
    main controller / user, never by this module), or a line in `decisions.md`
    that mentions the slug and the marker `user-approved`.
    """
    return ndir(control) / "decisions"


# ---------------------------------------------------------------- time

_NOW_OVERRIDE: "str | None" = None


def set_now(value: "str | None") -> None:
    """Freeze the clock (self-test / --now).  Hashes must not depend on wall time."""
    global _NOW_OVERRIDE
    _NOW_OVERRIDE = value


def now_iso() -> str:
    return _NOW_OVERRIDE or ros.utc_now()


def parse_ts(value: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def days_between(earlier: str, later: str) -> float:
    return (parse_ts(later) - parse_ts(earlier)).total_seconds() / 86400.0


# ---------------------------------------------------------------- canonical hashing (§2)

def canonical(obj) -> str:
    """The one canonical form.  Everything hashed goes through here (§2/§6.2)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_of(obj) -> str:
    return sha256_text(canonical(obj))


def dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def emit(obj) -> int:
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- re-entrant write lease

_LOCK_DEPTH = 0


class narrative_lock:
    """ros.hold_lock, made re-entrant so nested transactions (promote -> commit)
    do not deadlock on the same file lock."""

    def __init__(self, control: Path):
        self.control = control
        self.inner = None

    def __enter__(self):
        global _LOCK_DEPTH
        if _LOCK_DEPTH == 0 and self.control.exists():
            self.inner = ros.hold_lock(self.control)
            self.inner.__enter__()
        _LOCK_DEPTH += 1
        return self

    def __exit__(self, *exc):
        global _LOCK_DEPTH
        _LOCK_DEPTH -= 1
        if _LOCK_DEPTH == 0 and self.inner is not None:
            self.inner.__exit__(*exc)
        return False


def log_event(control: Path, event: dict) -> None:
    """Narrative CLI events feed the bearing lower bound (§6.5).  Best-effort:
    a bare narrative store without a research_os project still works."""
    if not (control / "state.json").exists():
        return
    with ros.contextlib_suppress():
        ros.append_event(control, event)


# ============================================================ §1  nodes & identity

def empty_snapshot() -> dict:
    return {"schema": TREE_SCHEMA, "root": None, "nodes": {},
            "role_assignments": {role: None for role in ROLES}, "meta": {}}


def normalize_node(raw: dict) -> dict:
    """Force every node into the same key set so rev_hash is stable (§2)."""
    if not isinstance(raw, dict):
        refuse("NODE_MALFORMED", "node must be an object")
    node_id = raw.get("id")
    if not isinstance(node_id, str) or not ID_RE.match(node_id):
        refuse("NODE_ID_INVALID", f"{node_id!r} does not match {ID_RE.pattern}")
    node_type = raw.get("node_type")
    if node_type not in NODE_TYPES:
        refuse("NODE_TYPE_INVALID", f"{node_id}: {node_type!r} not in {NODE_TYPES}")
    identity_key = raw.get("identity_key")
    if not isinstance(identity_key, dict) or not identity_key:
        refuse("IDENTITY_KEY_MISSING", f"{node_id}: {node_type} needs identity_key "
                                       f"{IDENTITY_KEYS[node_type]}")
    missing = [k for k in IDENTITY_KEYS[node_type] if k not in identity_key]
    if missing:
        refuse("IDENTITY_KEY_INCOMPLETE", f"{node_id}: missing {missing}")
    state = raw.get("state") or {}
    state_meta = raw.get("state_meta") or {}
    node = {
        "id": node_id,
        "node_type": node_type,
        "identity_key": identity_key,
        "title": raw.get("title") or "",
        "summary": raw.get("summary") or "",
        "statement": raw.get("statement") or "",
        "state": {
            "epistemic": state.get("epistemic", "PENDING"),
            "narrative": state.get("narrative", "ACTIVE"),
        },
        "state_meta": {
            "resolution_condition": state_meta.get("resolution_condition"),
            "live_bet": bool(state_meta.get("live_bet", False)),
        },
        "basis_refs": list(raw.get("basis_refs") or []),
        "disposition_meta": dict(raw.get("disposition_meta") or {}),
        "lineage": [dict(entry) for entry in (raw.get("lineage") or [])],
        "children": list(raw.get("children") or []),
    }
    for entry in node["lineage"]:
        if entry.get("rel") not in LINEAGE_RELS:
            refuse("LINEAGE_REL_INVALID", f"{node_id}: {entry.get('rel')!r} not in {LINEAGE_RELS}")
        if not entry.get("of"):
            refuse("LINEAGE_TARGET_MISSING", f"{node_id}: lineage entry without `of`")
    return node


def identity_hash(node_or_key) -> str:
    """sha256 of the canonical identity_key (§1).  This is what `identity changed`
    means everywhere else in the file."""
    key = node_or_key.get("identity_key") if "identity_key" in node_or_key else node_or_key
    return sha_of(key)


def rev_hash(node: dict) -> str:
    """§2: sha256(canonical(node - children)).  Children live in the tree
    manifest, so a pure re-parent does not perturb a node's own revision."""
    body = {k: v for k, v in node.items() if k != "children"}
    return sha_of(body)


def identity_diff(old_key: dict, new_key: dict, node_type: str) -> dict:
    """Which identity fields moved, and does that mean a new id?

    §1 identity invariant: any change of the canonical identity_key hash means a
    new id + lineage.  `rekey_identity` is the single approved exception.
    """
    fields = IDENTITY_KEYS.get(node_type, tuple(sorted(set(old_key) | set(new_key))))
    changed = [f for f in fields if canonical(old_key.get(f)) != canonical(new_key.get(f))]
    extra = sorted((set(old_key) | set(new_key)) - set(fields))
    changed += [f for f in extra if canonical(old_key.get(f)) != canonical(new_key.get(f))]
    same = sha_of(old_key) == sha_of(new_key)
    return {
        "changed_fields": changed,
        "same_identity": same,
        "verdict": "same_id" if same else "new_id",
        "requires": None if same else "new id + lineage (or approved rekey_identity)",
    }


def split_plan(mode: str) -> dict:
    """§1 case ②: one node becomes two.

    structural  -- the decomposition was always there: the original stays as the
                   parent, its statement is untouched, the two parts get new ids.
    semantic    -- the original claim is being replaced: it stays in history with
                   a `decomposes` lineage on the parts and is disposed by fact
                   (KILLED or DROPPED), never silently deleted.
    Either way the parts inherit lineage, never identity.
    """
    if mode not in ("structural", "semantic"):
        refuse("SPLIT_MODE_INVALID", f"{mode!r} not in structural|semantic")
    if mode == "structural":
        return {"mode": "structural", "origin_kept_in_tree": True,
                "origin_statement_unchanged": True, "origin_disposed": False,
                "parts_are_children_of_origin": True, "parts_get_new_ids": True,
                "parts_inherit_lineage": True, "parts_inherit_identity": False,
                "requires_origin_disposition": False}
    return {"mode": "semantic", "origin_kept_in_tree": False,
            "origin_statement_unchanged": True, "origin_disposed": True,
            "parts_are_children_of_origin": False, "parts_get_new_ids": True,
            "parts_inherit_lineage": True, "parts_inherit_identity": False,
            "requires_origin_disposition": True}


def evidence_swap_verdict(claim_old_key: dict, claim_new_key: dict,
                          ev_old_key: dict, ev_new_key: dict) -> dict:
    """§1 case ③: the evidence under a claim is replaced.

    If the claim's own identity_key is untouched the claim keeps its id and only
    its rev_hash moves (basis_refs changed).  But a new estimand / population /
    sample_window is a warning to re-read the claim: if that pulled the claim's
    target / domain / conditions with it, the claim MUST get a new id.
    """
    ev_changed = [f for f in ("estimand", "population", "sample_window")
                  if canonical(ev_old_key.get(f)) != canonical(ev_new_key.get(f))]
    claim_changed = [f for f in ("target", "domain", "conditions")
                     if canonical(claim_old_key.get(f)) != canonical(claim_new_key.get(f))]
    if claim_changed:
        return {"claim_verdict": "new_id", "evidence_scope_changed": ev_changed,
                "claim_changed_fields": claim_changed,
                "reason": "evidence scope moved and dragged the claim's target/domain/conditions"}
    return {"claim_verdict": "same_id", "evidence_scope_changed": ev_changed,
            "claim_changed_fields": [], "rev_hash_changes": True,
            "reason": "claim identity untouched; only basis_refs moved"}


# ============================================================ §2  tree snapshot

def parent_map(snapshot: dict) -> dict:
    parents = {}
    for nid, node in snapshot["nodes"].items():
        for child in node.get("children", []):
            parents.setdefault(child, []).append(nid)
    return parents


def parent_of(snapshot: dict, node_id: str) -> "str | None":
    for nid, node in snapshot["nodes"].items():
        if node_id in node.get("children", []):
            return nid
    return None


def child_index(snapshot: dict, parent: str, node_id: str) -> int:
    return snapshot["nodes"][parent]["children"].index(node_id)


def tree_manifest(snapshot: dict) -> list:
    """[[id, rev_hash, parent, order], ...] in root-first depth-first order,
    siblings by their position in children[] (§2/§6.2)."""
    out = []
    root = snapshot.get("root")
    if not root or root not in snapshot["nodes"]:
        return out
    seen = set()

    def walk(nid: str, parent, order: int) -> None:
        if nid in seen:  # defensive: validate_snapshot reports the real cycle
            return
        seen.add(nid)
        node = snapshot["nodes"].get(nid)
        if node is None:
            return
        out.append([nid, rev_hash(node), parent, order])
        for position, child in enumerate(node.get("children", [])):
            walk(child, nid, position)

    walk(root, None, 0)
    return out


def tree_hash(snapshot: dict) -> str:
    return sha_of({"root": snapshot.get("root"),
                   "role_assignments": snapshot.get("role_assignments", {}),
                   "meta": snapshot.get("meta", {}),
                   "manifest": tree_manifest(snapshot)})


def descendants(snapshot: dict, node_id: str) -> list:
    """DFS pre-order, node itself first."""
    out, stack = [], [node_id]
    seen = set()
    while stack:
        nid = stack.pop()
        if nid in seen or nid not in snapshot["nodes"]:
            continue
        seen.add(nid)
        out.append(nid)
        stack.extend(reversed(snapshot["nodes"][nid].get("children", [])))
    return out


def qualifying_basis(basis_refs) -> list:
    """A basis ref counts as adjudicating when it names one of the §4 kinds,
    written as `<kind>:<ref>` (e.g. `null-result:E-07`)."""
    out = []
    for ref in basis_refs or []:
        head = str(ref).split(":", 1)[0].strip()
        if head in KILL_BASIS_KINDS:
            out.append(ref)
    return out


def bearing_ids(snapshot: dict) -> set:
    """Load-bearing = the root's direct children plus whatever currently holds a
    role (§3.1).  Structural surgery on these is a restructure, not a refine."""
    out = set()
    root = snapshot.get("root")
    if root and root in snapshot["nodes"]:
        out.update(snapshot["nodes"][root].get("children", []))
    for value in (snapshot.get("role_assignments") or {}).values():
        if value:
            out.add(value)
    return out


def validate_snapshot(snapshot: dict) -> list:
    """§2 validator.  Returns a list of human-readable errors (empty = valid)."""
    errors = []
    if snapshot.get("schema") != TREE_SCHEMA:
        errors.append(f"schema must be {TREE_SCHEMA!r}, got {snapshot.get('schema')!r}")
    nodes = snapshot.get("nodes")
    if not isinstance(nodes, dict):
        return errors + ["nodes must be an object"]
    root = snapshot.get("root")
    if nodes and (not root or root not in nodes):
        errors.append(f"root {root!r} not present in nodes")

    # ---- structure: one parent, no duplicate children, full reachability
    parents = parent_map(snapshot)
    if root and root in parents:
        errors.append(f"root {root} is listed as a child of {parents[root]}")
    for nid, node in nodes.items():
        if node.get("id") != nid:
            errors.append(f"node key {nid} != node.id {node.get('id')}")
        children = node.get("children", [])
        if len(set(children)) != len(children):
            errors.append(f"{nid}: duplicate entries in children[]")
        for child in children:
            if child not in nodes:
                errors.append(f"{nid}: child {child} does not exist (orphan reference)")
    for nid, holders in parents.items():
        if len(holders) > 1:
            errors.append(f"{nid}: has {len(holders)} parents {sorted(holders)} (must be exactly 1)")
    if root and root in nodes:
        reachable = set(descendants(snapshot, root))
        stranded = sorted(set(nodes) - reachable)
        if stranded:
            errors.append(f"unreachable from root: {stranded}")
    for nid in nodes:
        if nid != root and nid not in parents:
            errors.append(f"{nid}: has no parent (only the root may be parentless)")

    # ---- §4 state matrix and hard rules
    for nid, node in sorted(nodes.items()):
        state = node.get("state") or {}
        combo = (state.get("narrative"), state.get("epistemic"))
        if combo not in LEGAL_STATES:
            errors.append(f"{nid}: illegal state {combo[0]}|{combo[1]}")
        if state.get("epistemic") == "KILLED" and not qualifying_basis(node.get("basis_refs")):
            errors.append(f"{nid}: KILLED without a qualifying basis_ref {KILL_BASIS_KINDS}")
        if state.get("epistemic") == "HELD" and not node.get("basis_refs"):
            errors.append(f"{nid}: HELD with empty basis_refs (needs evidence or an "
                          f"approved decision record)")
        if state.get("narrative") in ("DEMOTED", "MERGED") and \
                not (node.get("disposition_meta") or {}).get("target"):
            errors.append(f"{nid}: {state.get('narrative')} requires disposition_meta.target")
        meta = node.get("state_meta") or {}
        if state.get("epistemic") == "PENDING" and meta.get("live_bet") \
                and not meta.get("resolution_condition"):
            errors.append(f"{nid}: live bet without state_meta.resolution_condition")

    # ---- §2 roles: five keys always present, non-null must point at ACTIVE nodes
    roles = snapshot.get("role_assignments")
    if not isinstance(roles, dict):
        errors.append("role_assignments must be an object with all five keys")
    else:
        for role in ROLES:
            if role not in roles:
                errors.append(f"role_assignments missing key {role!r}")
        for extra in sorted(set(roles) - set(ROLES)):
            errors.append(f"role_assignments has unknown key {extra!r}")
        for role in ROLES:
            value = roles.get(role)
            if value is None:
                continue
            if value not in nodes:
                errors.append(f"role {role} -> {value} which is not in the tree")
            elif (nodes[value].get("state") or {}).get("narrative") != "ACTIVE":
                errors.append(f"role {role} -> {value} which is "
                              f"{(nodes[value].get('state') or {}).get('narrative')}, not ACTIVE")
    return errors


def assert_valid(snapshot: dict, where: str = "snapshot") -> None:
    errors = validate_snapshot(snapshot)
    if errors:
        refuse("SNAPSHOT_INVALID", f"{where}: " + "; ".join(errors[:8]))


# ============================================================ §3.3  ops and inverse ops

class OpContext:
    """Bookkeeping accumulated while an op batch is applied.  Everything the
    kind/cause machinery (§3.1/§3.2) and the overthrow gate (§3.4) needs is
    derived from here -- never from the model's proposal."""

    def __init__(self, control: Path, restore: bool = False):
        self.control = control
        self.restore = restore          # applying inverse_ops: skip approval gates
        self.added_ids = set()          # ids created inside this batch
        self.changed = set()            # any node touched
        self.structural_ids = set()     # add/detach/move/split/merge participants
        self.role_changes = []          # (role, old, new)
        self.venue_changed = False
        self.meta_changed = False
        self.set_root_used = False
        self.rekeyed = set()
        self.detached = []              # {id, disposition, disposition_meta, ...}
        self.expression_only = True     # merge-cause helper (§3.1)

    def mark_expression_break(self) -> None:
        self.expression_only = False


def _require_node(snapshot: dict, node_id: str) -> dict:
    node = snapshot["nodes"].get(node_id)
    if node is None:
        refuse("NODE_NOT_FOUND", str(node_id))
    return node


def _insert_child(snapshot: dict, parent: str, node_id: str, index) -> None:
    children = snapshot["nodes"][parent]["children"]
    if node_id in children:
        refuse("DUPLICATE_CHILD", f"{parent} already has child {node_id}")
    if index is None or index < 0 or index > len(children):
        children.append(node_id)
    else:
        children.insert(int(index), node_id)


def _remove_child(snapshot: dict, parent: str, node_id: str) -> int:
    children = snapshot["nodes"][parent]["children"]
    position = children.index(node_id)
    children.pop(position)
    return position


def op_add_node(snapshot: dict, op: dict, ctx: OpContext) -> list:
    node = normalize_node(op.get("node") or {})
    node_id = node["id"]
    if node_id in snapshot["nodes"]:
        refuse("NODE_ALREADY_PRESENT", node_id)
    known = load_registry_index(ctx.control).get("ids", {}).get(node_id)
    if known and known.get("node_type") != node["node_type"]:
        # §1: ids are never reused.  Re-adding a previously detached node with
        # the same node_type is a RESTORE, not a reuse -- that stays legal.
        refuse("NODE_ID_REUSE_FORBIDDEN",
               f"{node_id} already exists as {known.get('node_type')}")
    parent = op.get("parent")
    if snapshot.get("root") is None:
        if parent:
            refuse("ROOT_CANNOT_HAVE_PARENT", f"{node_id}: tree is empty, parent must be null")
        snapshot["nodes"][node_id] = node
        snapshot["root"] = node_id
    else:
        if not parent:
            refuse("PARENT_REQUIRED", f"{node_id}: only the first node may be parentless")
        _require_node(snapshot, parent)
        snapshot["nodes"][node_id] = node
        _insert_child(snapshot, parent, node_id, op.get("index"))
    ctx.added_ids.add(node_id)
    ctx.changed.add(node_id)
    ctx.structural_ids.add(node_id)
    ctx.mark_expression_break()
    return [{"op": "delete_uncommitted", "id": node_id}]


def op_patch_fields(snapshot: dict, op: dict, ctx: OpContext) -> list:
    node_id = op.get("id")
    node = _require_node(snapshot, node_id)
    fields = op.get("fields") or {}
    if not isinstance(fields, dict) or not fields:
        refuse("PATCH_EMPTY", f"{node_id}: patch_fields needs a non-empty fields object")
    forbidden = [k for k in fields if k not in PATCHABLE_FIELDS]
    if forbidden:
        # §1: patch_fields may NEVER move identity_key / node_type / id.
        refuse("PATCH_FIELD_FORBIDDEN",
               f"{node_id}: {forbidden} (allowed {list(PATCHABLE_FIELDS)})")
    before = {k: json.loads(canonical(node.get(k))) for k in fields}
    for key, value in fields.items():
        if key in ("state", "state_meta"):
            merged = dict(node[key])
            merged.update(value or {})
            node[key] = merged
        else:
            node[key] = value
    snapshot["nodes"][node_id] = normalize_node(node)
    ctx.changed.add(node_id)
    if any(k not in EXPRESSION_FIELDS for k in fields):
        ctx.mark_expression_break()
    return [{"op": "patch_fields", "id": node_id, "fields": before}]


def op_patch_meta(snapshot: dict, op: dict, ctx: OpContext) -> list:
    fields = op.get("fields")
    if not isinstance(fields, dict):
        refuse("PATCH_META_INVALID", "patch_meta needs a fields object")
    before = json.loads(canonical(snapshot.get("meta") or {}))
    if op.get("replace"):
        snapshot["meta"] = json.loads(canonical(fields))
    else:
        meta = dict(snapshot.get("meta") or {})
        meta.update(fields)
        snapshot["meta"] = meta
    if before.get("venue") != (snapshot["meta"] or {}).get("venue"):
        ctx.venue_changed = True      # §3.1: a venue move is mechanically restructure
    if canonical(before) != canonical(snapshot["meta"]):
        ctx.meta_changed = True
        ctx.mark_expression_break()
    return [{"op": "patch_meta", "fields": before, "replace": True}]


def op_move_node(snapshot: dict, op: dict, ctx: OpContext) -> list:
    node_id = op.get("id")
    _require_node(snapshot, node_id)
    if node_id == snapshot.get("root"):
        refuse("CANNOT_MOVE_ROOT", node_id)
    new_parent = op.get("new_parent")
    _require_node(snapshot, new_parent)
    if new_parent in descendants(snapshot, node_id):
        refuse("MOVE_WOULD_CYCLE", f"{new_parent} is inside the subtree of {node_id}")
    old_parent = parent_of(snapshot, node_id)
    old_index = _remove_child(snapshot, old_parent, node_id)
    _insert_child(snapshot, new_parent, node_id, op.get("index"))
    ctx.changed.add(node_id)
    ctx.structural_ids.add(node_id)
    ctx.mark_expression_break()
    return [{"op": "move_node", "id": node_id, "new_parent": old_parent, "index": old_index}]


def op_detach_node(snapshot: dict, op: dict, ctx: OpContext) -> list:
    """Detach = leave the living tree with a recorded disposition.

    The node object is never physically destroyed: it survives in every earlier
    snapshot, in the id registry, and verbatim inside this commit's inverse_ops.
    §3.3: a DEMOTED node may NOT be detached -- demotion keeps it reachable from
    the root so LEVER_DEBT can still see it.
    """
    node_id = op.get("id")
    node = _require_node(snapshot, node_id)
    if node_id == snapshot.get("root"):
        refuse("CANNOT_DETACH_ROOT", f"{node_id}: re-root with set_root first (§3.4 gate)")
    disposition = op.get("narrative_disposition")
    if disposition not in DETACH_DISPOSITIONS:
        refuse("DETACH_DISPOSITION_INVALID",
               f"{node_id}: {disposition!r} not in {DETACH_DISPOSITIONS}")
    if (node.get("state") or {}).get("narrative") == "DEMOTED" and not ctx.restore:
        refuse("DEMOTED_NODE_NOT_DETACHABLE",
               f"{node_id}: a demoted node stays in the tree (move it to an appendix subtree)")
    meta = dict(op.get("disposition_meta") or {})
    if disposition == "MERGED" and not meta.get("target"):
        refuse("DISPOSITION_TARGET_REQUIRED",
               f"{node_id}: MERGED requires disposition_meta.target")

    subtree = descendants(snapshot, node_id)      # DFS pre-order, top node first
    old_parent = parent_of(snapshot, node_id)
    # §3.3 inverse exactness.  Every archived body is stored with an EMPTY
    # children[]: replayed in DFS pre-order the archive re-adds each parent
    # before its own children, and every `add_node` re-links exactly one edge at
    # its recorded index, so the original children[] arrays rebuild themselves in
    # order.  Archiving the bodies verbatim (children included) made the inverse
    # of a NON-LEAF detach re-insert every edge twice -> DUPLICATE_CHILD, i.e. the
    # §6.1 INVERSE_OPS_NOT_EXACT refusal that made a hand-written non-leaf detach
    # (and `revert` of one) impossible.
    archive = []  # (body, parent, index) in DFS order -- the inverse's payload
    for nid in subtree:
        body = json.loads(canonical(snapshot["nodes"][nid]))
        body["children"] = []
        holder = parent_of(snapshot, nid)
        archive.append((body, holder, child_index(snapshot, holder, nid) if holder else None))
    _remove_child(snapshot, old_parent, node_id)
    for nid in subtree:
        body = snapshot["nodes"].pop(nid)
        body["state"] = dict(body["state"])
        if nid == node_id:
            # the DROPPED/MERGED disposition and its meta are recorded on the
            # detached TOP node only ...
            body["state"]["narrative"] = disposition
            if op.get("epistemic"):
                body["state"]["epistemic"] = op["epistemic"]
            body["disposition_meta"] = dict(meta)
        else:
            # ... a descendant leaves the tree as a cascade, not as a verdict
            body["disposition_meta"] = {"detached_with": node_id}
        ctx.detached.append({
            "id": nid,
            "disposition": disposition if nid == node_id else None,
            "detached_with": None if nid == node_id else node_id,
            "disposition_meta": body["disposition_meta"],
            "epistemic": body["state"]["epistemic"],
            "basis_refs": list(body.get("basis_refs") or []),
            "cascade_of": None if nid == node_id else node_id,
        })
    ctx.changed.update(subtree)
    ctx.structural_ids.update(subtree)
    ctx.mark_expression_break()
    return [{"op": "add_node", "node": body, "parent": holder, "index": index}
            for body, holder, index in archive]


def op_delete_uncommitted(snapshot: dict, op: dict, ctx: OpContext) -> list:
    node_id = op.get("id")
    _require_node(snapshot, node_id)
    if node_id not in ctx.added_ids and not ctx.restore:
        refuse("NODE_ALREADY_COMMITTED",
               f"{node_id}: only nodes added in this uncommitted batch may be deleted; "
               f"use detach_node")
    subtree = descendants(snapshot, node_id)
    archive = []
    for nid in subtree:
        body = json.loads(canonical(snapshot["nodes"][nid]))
        holder = parent_of(snapshot, nid)
        archive.append((body, holder, child_index(snapshot, holder, nid) if holder else None))
    parent = parent_of(snapshot, node_id)
    if parent:
        _remove_child(snapshot, parent, node_id)
    elif snapshot.get("root") == node_id:
        snapshot["root"] = None
    for nid in subtree:
        snapshot["nodes"].pop(nid, None)
    ctx.added_ids.discard(node_id)
    ctx.changed.update(subtree)
    ctx.structural_ids.update(subtree)
    ctx.mark_expression_break()
    return [{"op": "add_node", "node": body, "parent": holder, "index": index}
            for body, holder, index in archive]


def op_split_node(snapshot: dict, op: dict, ctx: OpContext) -> list:
    """§1 case 2 / §3.3.  Composed from primitives so the inverse is exact."""
    node_id = op.get("id")
    origin = _require_node(snapshot, node_id)
    plan = split_plan(op.get("mode") or "")
    parts = op.get("parts") or []
    if len(parts) < 2:
        refuse("SPLIT_NEEDS_TWO_PARTS", f"{node_id}: got {len(parts)} part(s)")
    origin_parent = parent_of(snapshot, node_id)
    origin_index = child_index(snapshot, origin_parent, node_id) if origin_parent else None
    inverse: list = []

    for offset, part in enumerate(parts):
        body = normalize_node(part)
        if identity_hash(body) == identity_hash(origin):
            refuse("SPLIT_PART_INHERITS_IDENTITY",
                   f"{body['id']}: a split part must carry its own identity_key (§1)")
        body["lineage"] = list(body.get("lineage") or [])
        if not any(e.get("rel") == "decomposes" and e.get("of") == node_id
                   for e in body["lineage"]):
            body["lineage"].append({"rel": "decomposes", "of": node_id})
        if plan["parts_are_children_of_origin"]:
            sub = {"op": "add_node", "node": body, "parent": node_id, "index": None}
        else:
            sub = {"op": "add_node", "node": body, "parent": origin_parent,
                   "index": None if origin_index is None else origin_index + 1 + offset}
        inverse = op_add_node(snapshot, sub, ctx) + inverse

    if plan["requires_origin_disposition"]:
        disposition = op.get("origin_disposition")
        if not isinstance(disposition, dict) or not disposition.get("narrative_disposition"):
            refuse("SPLIT_ORIGIN_DISPOSITION_REQUIRED",
                   f"{node_id}: a semantic split must dispose the original by fact "
                   f"(KILLED / DROPPED)")
        if disposition.get("epistemic") == "KILLED" and \
                not qualifying_basis(disposition.get("basis_refs")):
            refuse("KILL_BASIS_REQUIRED", f"{node_id}: KILLED needs {list(KILL_BASIS_KINDS)}")
        if disposition.get("basis_refs"):
            inverse = op_patch_fields(snapshot, {
                "op": "patch_fields", "id": node_id,
                "fields": {"basis_refs": list(disposition["basis_refs"])}}, ctx) + inverse
        inverse = op_detach_node(snapshot, {
            "op": "detach_node", "id": node_id,
            "narrative_disposition": disposition["narrative_disposition"],
            "epistemic": disposition.get("epistemic"),
            "disposition_meta": disposition.get("disposition_meta")
            or {"target": parts[0].get("id"), "reason": "semantic split"},
        }, ctx) + inverse
    else:
        if op.get("origin_statement") not in (None, origin.get("statement")):
            refuse("STRUCTURAL_SPLIT_CHANGES_STATEMENT",
                   f"{node_id}: a structural split must not rewrite the original statement")
    ctx.mark_expression_break()
    return inverse


def op_merge_nodes(snapshot: dict, op: dict, ctx: OpContext) -> list:
    """merge_nodes(ids[], into).  Sources leave the tree as MERGED with
    disposition_meta.target = into (§4); their children re-parent onto `into`."""
    ids = list(op.get("ids") or [])
    into = op.get("into")
    if len(ids) < 2:
        refuse("MERGE_NEEDS_TWO_NODES", f"got {ids}")
    inverse: list = []
    if isinstance(into, dict):
        into_id = normalize_node(into)["id"]
        anchor_parent = parent_of(snapshot, ids[0])
        anchor_index = child_index(snapshot, anchor_parent, ids[0]) if anchor_parent else None
        inverse = op_add_node(snapshot, {"op": "add_node", "node": into,
                                         "parent": anchor_parent,
                                         "index": anchor_index}, ctx) + inverse
    else:
        into_id = into
        _require_node(snapshot, into_id)
    for source in ids:
        if source == into_id:
            continue
        _require_node(snapshot, source)
        for child in list(snapshot["nodes"][source].get("children", [])):
            inverse = op_move_node(snapshot, {"op": "move_node", "id": child,
                                              "new_parent": into_id,
                                              "index": None}, ctx) + inverse
        inverse = op_detach_node(snapshot, {
            "op": "detach_node", "id": source, "narrative_disposition": "MERGED",
            "disposition_meta": {"target": into_id,
                                 "reason": (op.get("reason") or f"merged into {into_id}")},
        }, ctx) + inverse
    ctx.mark_expression_break()
    return inverse


def op_set_role(snapshot: dict, op: dict, ctx: OpContext) -> list:
    role = op.get("role")
    if role not in ROLES:
        refuse("ROLE_INVALID", f"{role!r} not in {ROLES}")
    value = op.get("id")
    if value is not None:
        node = _require_node(snapshot, value)
        if (node.get("state") or {}).get("narrative") != "ACTIVE" and not ctx.restore:
            refuse("ROLE_NODE_NOT_ACTIVE",
                   f"{role} -> {value} is {(node.get('state') or {}).get('narrative')}")
    old = (snapshot.get("role_assignments") or {}).get(role)
    snapshot["role_assignments"][role] = value
    if old != value:
        ctx.role_changes.append((role, old, value))
        ctx.mark_expression_break()
    return [{"op": "set_role", "role": role, "id": old}]


def op_set_root(snapshot: dict, op: dict, ctx: OpContext) -> list:
    """Re-root the tree onto an existing node by reversing the parent chain."""
    new_root = op.get("new_root_id")
    _require_node(snapshot, new_root)
    old_root = snapshot.get("root")
    if new_root == old_root:
        refuse("SET_ROOT_NOOP", f"{new_root} is already the root")
    path = [new_root]
    cursor = new_root
    while cursor != old_root:
        nxt = parent_of(snapshot, cursor)
        if nxt is None:
            refuse("SET_ROOT_UNREACHABLE", f"{new_root} is not connected to root {old_root}")
        path.append(nxt)
        cursor = nxt
    restore = []
    for nid in path:
        holder = parent_of(snapshot, nid)
        if holder is not None:
            restore.append((nid, holder, child_index(snapshot, holder, nid)))
    for index in range(len(path) - 1):
        child, parent = path[index], path[index + 1]
        _remove_child(snapshot, parent, child)
        _insert_child(snapshot, child, parent, None)
    snapshot["root"] = new_root
    ctx.set_root_used = True
    ctx.changed.update(path)
    ctx.structural_ids.update(path)
    ctx.mark_expression_break()
    inverse = [{"op": "set_root", "new_root_id": old_root}]
    inverse += [{"op": "move_node", "id": nid, "new_parent": holder, "index": index}
                for nid, holder, index in restore]
    return inverse


def op_rekey_identity(snapshot: dict, op: dict, ctx: OpContext) -> list:
    """§1's single approved identity exception.  Hard conditions: node_type
    unchanged, old_key_hash matches, explicit user approval."""
    node_id = op.get("id")
    node = _require_node(snapshot, node_id)
    old_hash = identity_hash(node)
    if op.get("old_key_hash") != old_hash:
        refuse("REKEY_OLD_HASH_MISMATCH",
               f"{node_id}: expected {old_hash[:16]}, got {str(op.get('old_key_hash'))[:16]}")
    new_key = op.get("new_identity_key")
    if not isinstance(new_key, dict) or not new_key:
        refuse("REKEY_KEY_MISSING", str(node_id))
    missing = [k for k in IDENTITY_KEYS[node["node_type"]] if k not in new_key]
    if missing:
        refuse("IDENTITY_KEY_INCOMPLETE", f"{node_id}: missing {missing}")
    relation = op.get("relation") or "clarifies"
    if relation not in LINEAGE_RELS:
        refuse("LINEAGE_REL_INVALID", relation)
    if not ctx.restore:
        if not op.get("basis_ref"):
            refuse("REKEY_BASIS_REQUIRED", f"{node_id}: needs the frozen prior definition")
        if not resolve_decision(ctx.control, op.get("approval_ref")):
            refuse("REKEY_APPROVAL_REQUIRED",
                   f"{node_id}: approval_ref {op.get('approval_ref')!r} is not a "
                   f"user-approved decision record")
    before_key = json.loads(canonical(node["identity_key"]))
    before_lineage = json.loads(canonical(node["lineage"]))
    node["identity_key"] = json.loads(canonical(new_key))
    node["lineage"] = list(node["lineage"]) + [
        {"rel": relation, "of": node_id, "old_key_hash": old_hash}]
    snapshot["nodes"][node_id] = normalize_node(node)
    ctx.rekeyed.add(node_id)
    ctx.changed.add(node_id)
    ctx.structural_ids.add(node_id)
    ctx.mark_expression_break()
    return [{"op": "rekey_identity", "id": node_id,
             "old_key_hash": identity_hash(snapshot["nodes"][node_id]),
             "new_identity_key": before_key, "relation": relation,
             "basis_ref": op.get("basis_ref"), "approval_ref": op.get("approval_ref"),
             "_restore_lineage": before_lineage}]


OP_TABLE = {
    "add_node": op_add_node,
    "patch_fields": op_patch_fields,
    "patch_meta": op_patch_meta,
    "move_node": op_move_node,
    "detach_node": op_detach_node,
    "delete_uncommitted": op_delete_uncommitted,
    "split_node": op_split_node,
    "merge_nodes": op_merge_nodes,
    "set_role": op_set_role,
    "set_root": op_set_root,
    "rekey_identity": op_rekey_identity,
}

STRUCTURAL_OPS = ("add_node", "detach_node", "move_node", "split_node",
                  "merge_nodes", "delete_uncommitted")


def apply_ops(snapshot: dict, ops: list, control: Path, restore: bool = False):
    """Apply an op batch to a *copy* of the snapshot.

    Returns (new_snapshot, inverse_ops, ctx).  inverse_ops is ordered so that
    applying it to the new snapshot restores the old one exactly -- that is not
    a promise, it is verified by tree_hash in commit_from_ops (§3.3).
    """
    working = json.loads(canonical(snapshot))
    working["nodes"] = {k: normalize_node(v) for k, v in working.get("nodes", {}).items()}
    ctx = OpContext(control, restore=restore)
    inverse: list = []
    for index, op in enumerate(ops):
        if not isinstance(op, dict) or op.get("op") not in OP_TABLE:
            refuse("OP_UNKNOWN",
                   f"ops[{index}]: {(op or {}).get('op')!r} not in {sorted(OP_TABLE)}")
        inv = OP_TABLE[op["op"]](working, op, ctx)
        # `_restore_lineage` rides along on a rekey inverse so the exact prior
        # lineage array comes back instead of accumulating a second entry.
        if op["op"] == "rekey_identity" and restore and "_restore_lineage" in op:
            working["nodes"][op["id"]]["lineage"] = json.loads(canonical(op["_restore_lineage"]))
            working["nodes"][op["id"]] = normalize_node(working["nodes"][op["id"]])
        inverse = inv + inverse
    return working, inverse, ctx


# ============================================================ §0  object store I/O

def require_store(control: Path) -> None:
    if not ndir(control).exists():
        ros.die(f"no narrative store under {ndir(control)}; run `narrative init` first", 2)


def load_config(control: Path) -> dict:
    return ros.read_json_file(config_path(control),
                              {"schema": "auto-research/narrative-config-v1",
                               "bearing_globs": list(DEFAULT_BEARING_GLOBS),
                               "axis_depth": AXIS_DEFAULT_DEPTH})


def load_registry_index(control: Path) -> dict:
    """Derived id registry (§1: ids are never reused).  Rebuildable from the
    commit graph -- see `rebuild_index`."""
    return ros.read_json_file(index_path(control), {"ids": {}, "source_commits": {}})


def read_ref(control: Path, ref: str) -> "str | None":
    path = refs_dir(control) / ref
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def all_refs(control: Path) -> dict:
    out = {}
    if refs_dir(control).exists():
        for path in sorted(refs_dir(control).iterdir()):
            if path.is_file():
                out[path.name] = path.read_text(encoding="utf-8").strip()
    return out


def load_commit(control: Path, commit_id: str) -> dict:
    path = commits_dir(control) / f"{commit_id}.json"
    if not path.exists():
        refuse("COMMIT_NOT_FOUND", commit_id)
    return json.loads(path.read_text(encoding="utf-8"))


def load_snapshot(control: Path, hash_value: str) -> dict:
    path = objects_dir(control) / f"{hash_value}.json"
    if not path.exists():
        refuse("SNAPSHOT_NOT_FOUND", hash_value)
    return json.loads(path.read_text(encoding="utf-8"))


def commit_snapshot(control: Path, commit_id: str) -> dict:
    return load_snapshot(control, load_commit(control, commit_id)["tree"])


def load_head_snapshot(control: Path, ref: str = "main") -> dict:
    """Public API (G track).  Always the COMMITTED snapshot -- never the preview
    (§8: derived views may not be built from working/)."""
    head = read_ref(control, ref)
    if not head:
        return empty_snapshot()
    return commit_snapshot(control, head)


def commit_chain(control: Path, ref_or_commit: str, limit: "int | None" = None) -> list:
    """First-parent chain, oldest first."""
    head = read_ref(control, ref_or_commit) or ref_or_commit
    out, cursor, seen = [], head, set()
    while cursor and cursor not in seen:
        seen.add(cursor)
        path = commits_dir(control) / f"{cursor}.json"
        if not path.exists():
            break
        commit = json.loads(path.read_text(encoding="utf-8"))
        out.append(commit)
        parents = commit.get("parents") or []
        cursor = parents[0] if parents else None
        if limit and len(out) >= limit:
            break
    return list(reversed(out))


def ancestors(control: Path, commit_id: str) -> set:
    """Full ancestor set (all parents), inclusive of commit_id."""
    out, stack = set(), [commit_id]
    while stack:
        cursor = stack.pop()
        if not cursor or cursor in out:
            continue
        path = commits_dir(control) / f"{cursor}.json"
        if not path.exists():
            continue
        out.add(cursor)
        stack.extend(json.loads(path.read_text(encoding="utf-8")).get("parents") or [])
    return out


def load_branch_registry(control: Path) -> dict:
    return ros.read_json_file(registry_path(control),
                              {"schema": "auto-research/narrative-branches-v1", "branches": {}})


def branch_disposition(entry: dict) -> str:
    """§5: the current disposition is the projection of the LAST history event."""
    history = entry.get("disposition_history") or []
    return history[-1]["disposition"] if history else "OPEN"


# ---------------------------------------------------------------- approvals & triggers

def resolve_decision(control: Path, ref) -> "dict | None":
    """Resolve an approval ref to a user-approved decision record, or None.

    Interpretive decision (contract fixes neither format): `decision:<slug>`
    resolves against `narrative/decisions/<slug>.json` (`user_approved: true`)
    or, failing that, a line of `.research-os/decisions.md` that names the slug
    and carries the literal marker `user-approved`.  This module only READS
    decision records; it never writes one.
    """
    if not ref or not isinstance(ref, str):
        return None
    slug = ref.split(":", 1)[1] if ref.startswith("decision:") else ref
    path = decisions_dir(control) / f"{slug}.json"
    if path.exists():
        body = ros.read_json_file(path, {})
        if body.get("user_approved") is True:
            return {"ref": ref, "source": str(path), "record": body}
        return None
    md = control / "decisions.md"
    if md.exists():
        for line in md.read_text(encoding="utf-8").splitlines():
            if slug in line and "user-approved" in line:
                return {"ref": ref, "source": str(md), "record": {"line": line.strip()}}
    return None


def resolve_trigger(control: Path, ref) -> "dict | None":
    """Resolve a trigger_ref to {at, adjudicated, touches[]} or None (§3.2).

    Accepted forms: `annotation:<node_id>/<annotation_id>` (§10),
    `decision:<slug>`, `incident:<id>`, and a bare `<node_id>` naming a node.
    """
    if not ref or not isinstance(ref, str):
        return None
    kind, _, rest = ref.partition(":")
    if kind == "annotation" and "/" in rest:
        node_id, _, annotation_id = rest.partition("/")
        path = ndir(control) / "annotations" / node_id / f"{annotation_id}.json"
        if not path.exists():
            return None
        body = ros.read_json_file(path, {})
        return {"ref": ref, "at": body.get("at"), "touches": [body.get("node_id")],
                "adjudicated": bool(body.get("answer") or body.get("adjudicated")),
                "record": body}
    if kind == "decision":
        record = resolve_decision(control, ref)
        if not record:
            return None
        body = record["record"]
        return {"ref": ref, "at": body.get("at"), "touches": list(body.get("touches") or []),
                "adjudicated": True, "record": body}
    if kind == "incident":
        path = ndir(control) / "incidents" / f"{rest}.json"
        if not path.exists():
            return None
        body = ros.read_json_file(path, {})
        return {"ref": ref, "at": body.get("at"), "touches": list(body.get("touches") or []),
                "adjudicated": bool(body.get("adjudicated")), "record": body}
    return None


def trigger_qualified(control: Path, trigger_ref, commit_at: str,
                      changed_ids: set, roles: dict) -> "tuple":
    """The four preconditions of §3.2: the trigger exists, predates this commit,
    has itself been adjudicated, and touches a changed node or its role."""
    record = resolve_trigger(control, trigger_ref)
    if record is None:
        return False, "trigger_ref does not resolve"
    if not record.get("at") or parse_ts(record["at"]) > parse_ts(commit_at):
        return False, "trigger is not earlier than the commit"
    if not record.get("adjudicated"):
        return False, "trigger has not been adjudicated"
    touched = set(record.get("touches") or [])
    role_nodes = {v for v in (roles or {}).values() if v}
    if not touched or not (touched & (set(changed_ids) | role_nodes)):
        return False, "trigger does not touch a changed node or its role"
    return True, "ok"


# ============================================================ §3.1  mechanical kind

def determine_kind(old_snapshot: dict, new_snapshot: dict, ops: list,
                   parents: list, ctx: OpContext) -> "tuple":
    """kind is COMPUTED, never accepted from the chronicler (§3.1).

    two parents                                  -> merge
    root identity moved (set_root / rekey root)  -> overthrow
    role change | venue change | structural
    surgery on a load-bearing node | set_root    -> restructure
    anything else                                -> refine
    """
    flags = []
    if len(ctx.role_changes) >= 3:
        flags.append("LARGE_RESTRUCTURE")

    old_root, new_root = old_snapshot.get("root"), new_snapshot.get("root")
    old_identity = identity_hash(old_snapshot["nodes"][old_root]) if old_root else None
    new_identity = identity_hash(new_snapshot["nodes"][new_root]) if new_root else None
    root_identity_moved = bool(old_root) and bool(new_root) and old_identity != new_identity

    if len(parents) >= 2:
        return "merge", flags, root_identity_moved
    if root_identity_moved and (ctx.set_root_used or old_root in ctx.rekeyed
                                or new_root in ctx.rekeyed):
        return "overthrow", flags, True

    bearing = bearing_ids(old_snapshot) | bearing_ids(new_snapshot)
    structural_hit = any(op.get("op") in STRUCTURAL_OPS
                         for op in ops if isinstance(op, dict)) and \
        bool(ctx.structural_ids & bearing)
    if ctx.role_changes or ctx.venue_changed or ctx.set_root_used or ctx.rekeyed or structural_hit:
        return "restructure", flags, root_identity_moved
    return "refine", flags, root_identity_moved


# ============================================================ §3.2  cause / forced_status

def resolve_cause(control: Path, kind: str, meta: dict, ctx: OpContext,
                  new_snapshot: dict, commit_at: str) -> dict:
    """The single cause matrix.  Iron law: cause=forced <=> forced_status=confirmed."""
    proposed_cause = meta.get("cause") or meta.get("cause_proposal")
    origin = meta.get("origin") or "live"
    trigger_ref = meta.get("trigger_ref")
    trigger_class = meta.get("trigger_class")
    counterfactual = meta.get("counterfactual")
    approvals = dict(meta.get("approval_refs") or {})
    notes = []

    if trigger_class not in (None, "null") and trigger_class not in TRIGGER_CLASSES:
        refuse("TRIGGER_CLASS_INVALID", f"{trigger_class!r} not in {list(TRIGGER_CLASSES)}")

    structural = kind in ("restructure", "overthrow") or \
        (kind == "merge" and not ctx.expression_only)

    if kind == "refine" or (kind == "merge" and ctx.expression_only):
        # §3.1 merge cause: an expression-only merge is none/none, exactly like a refine.
        if proposed_cause not in (None, "none"):
            notes.append(f"CAUSE_CORRECTED: proposed {proposed_cause!r} on a "
                         f"non-structural {kind}; matrix says none/none")
        return {"cause": "none", "forced_status": "none", "trigger_ref": None,
                "trigger_class": None, "counterfactual": None,
                "approval_refs": {"overthrow": approvals.get("overthrow"), "forced_cause": None},
                "notes": notes, "structural": False}

    if origin == "backfill":
        # §3.2 backfill rows: unadjudicated -> unknown/none; blind adjudication decides.
        adjudication = meta.get("blind_decision") or {}
        if not adjudication:
            return {"cause": "unknown", "forced_status": "none", "trigger_ref": trigger_ref,
                    "trigger_class": trigger_class, "counterfactual": None,
                    "approval_refs": {"overthrow": approvals.get("overthrow"),
                                      "forced_cause": None},
                    "notes": notes + ["backfill row not yet adjudicated"], "structural": True}
        blind = adjudication.get("cause")
        if blind == "forced":
            if not meta.get("approval_ref") and not approvals.get("forced_cause"):
                refuse("BACKFILL_FORCED_NEEDS_APPROVAL",
                       "a blind adjudication of `forced` must carry approval_ref")
            return {"cause": "forced", "forced_status": "confirmed", "trigger_ref": trigger_ref,
                    "trigger_class": trigger_class,
                    "counterfactual": counterfactual or adjudication.get("counterfactual"),
                    "approval_refs": {"overthrow": approvals.get("overthrow"),
                                      "forced_cause": approvals.get("forced_cause")
                                      or meta.get("approval_ref")},
                    "notes": notes + ["blind adjudication: forced"], "structural": True}
        return {"cause": "unforced", "forced_status": "none", "trigger_ref": trigger_ref,
                "trigger_class": trigger_class, "counterfactual": None,
                "approval_refs": {"overthrow": approvals.get("overthrow"), "forced_cause": None},
                "notes": notes + ["blind adjudication: unforced"], "structural": True}

    # ---- live structural change
    signed = resolve_decision(control, approvals.get("forced_cause"))
    if signed and counterfactual:
        return {"cause": "forced", "forced_status": "confirmed", "trigger_ref": trigger_ref,
                "trigger_class": trigger_class, "counterfactual": counterfactual,
                "approval_refs": {"overthrow": approvals.get("overthrow"),
                                  "forced_cause": approvals.get("forced_cause")},
                "notes": notes, "structural": True}
    if signed and not counterfactual:
        refuse("FORCED_CAUSE_NEEDS_COUNTERFACTUAL",
               "forced_status=confirmed requires the written counterfactual (§3.2)")
    ok, why = trigger_qualified(control, trigger_ref, commit_at, ctx.changed,
                                new_snapshot.get("role_assignments") or {})
    if ok:
        return {"cause": "unknown", "forced_status": "candidate", "trigger_ref": trigger_ref,
                "trigger_class": trigger_class, "counterfactual": counterfactual,
                "approval_refs": {"overthrow": approvals.get("overthrow"), "forced_cause": None},
                "notes": notes + ["trigger passed the four preconditions, not yet signed"],
                "structural": True}
    if trigger_ref:
        notes.append(f"trigger not qualified ({why}) -> unforced")
    return {"cause": "unforced", "forced_status": "none", "trigger_ref": trigger_ref,
            "trigger_class": trigger_class, "counterfactual": None,
            "approval_refs": {"overthrow": approvals.get("overthrow"), "forced_cause": None},
            "notes": notes, "structural": bool(structural)}


def assert_cause_matrix(cause: str, forced_status: str) -> None:
    if cause not in CAUSES:
        refuse("CAUSE_INVALID", cause)
    if forced_status not in FORCED_STATUS:
        refuse("FORCED_STATUS_INVALID", forced_status)
    if (cause == "forced") != (forced_status == "confirmed"):
        refuse("CAUSE_MATRIX_VIOLATION",
               f"cause={cause} forced_status={forced_status} breaks "
               f"`forced <=> confirmed` (§3.2)")


# ============================================================ §6.1  the one transaction

def _fsync_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with ros.contextlib_suppress():
            os.unlink(tmp)
        raise


def run_transaction(control: Path, txid: str, plan: list, note: str = "",
                    receipt_call: "dict | None" = None) -> dict:
    """journal -> temp file -> fsync -> atomic rename -> completion marker (§6.1).

    `plan` is a list of {"path": <abs>, "text": <content>} and optional
    {"path": ..., "unlink": true}.  A crash between any two steps is repaired by
    `journal replay`, which re-applies the SAME recorded plan -- the model is
    never asked to regenerate ops.
    """
    entry = {"txid": txid, "status": "begin", "note": note, "at": now_iso(),
             "receipt_call": receipt_call,
             "plan": [{"path": str(item["path"]),
                       "text": item.get("text"),
                       "unlink": bool(item.get("unlink"))} for item in plan]}
    jpath = journal_dir(control) / f"{txid}.json"
    _fsync_write(jpath, dump_json(entry))
    for item in entry["plan"]:
        target = Path(item["path"])
        if item["unlink"]:
            with ros.contextlib_suppress():
                target.unlink()
        else:
            _fsync_write(target, item["text"])
    if receipt_call:
        entry["receipt_path"] = deliver_receipt(control, receipt_call)
    entry["status"] = "done"
    entry["completed_at"] = now_iso()
    _fsync_write(jpath, dump_json(entry))
    return entry


def journal_replay(control: Path) -> dict:
    """Finish every transaction that did not reach its completion marker."""
    repaired = []
    directory = journal_dir(control)
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            entry = ros.read_json_file(path, {})
            if entry.get("status") == "done":
                continue
            for item in entry.get("plan") or []:
                target = Path(item["path"])
                if item.get("unlink"):
                    with ros.contextlib_suppress():
                        target.unlink()
                elif item.get("text") is not None:
                    _fsync_write(target, item["text"])
            if entry.get("receipt_call"):
                entry["receipt_path"] = deliver_receipt(control, entry["receipt_call"])
            entry["status"] = "done"
            entry["completed_at"] = now_iso()
            entry["replayed"] = True
            _fsync_write(path, dump_json(entry))
            repaired.append(entry.get("txid"))
    return {"ok": True, "replayed": repaired, "count": len(repaired)}


# ---------------------------------------------------------------- receipts (§6.4)

def receipt_body(turn_uuid: str, manifest_id: str, kind: str, payload: dict) -> dict:
    """§6.4.  The receipt's own `kind` is one of the four receipt states and is
    written LAST: a payload that happens to carry a commit `kind`
    (refine/restructure/...) must never overwrite it.  The payload is also kept
    nested, so a reader can use the same shape the G track writes."""
    payload = json.loads(canonical(payload or {}))
    body = dict(payload)
    body.update({"schema": RECEIPT_SCHEMA, "turn_uuid": turn_uuid,
                 "manifest_id": manifest_id, "kind": kind, "at": now_iso(),
                 "payload": payload})
    return body


def receipt_path_for(control: Path, turn_uuid: str, manifest_id: str) -> Path:
    return receipts_dir(control) / f"{turn_uuid}.{manifest_id}.json"


def write_receipt(control: Path, turn_uuid: str, manifest_id: str,
                  kind: str, payload: dict) -> str:
    """Receipts are ALWAYS constructed by the CLI, never supplied by a model
    (§6.1).  The G track owns the runtime, so a real turn is handed to
    `turn_runtime.write_receipt`; a detached commit (no turn) and a host without
    the G track fall back to the same §6.4 file written here."""
    if turn_uuid and turn_uuid != "detached":
        try:
            import turn_runtime  # type: ignore  (G track)
            return str(turn_runtime.write_receipt(control, turn_uuid, manifest_id,
                                                  kind, payload))
        except ImportError:
            pass
    path = receipt_path_for(control, turn_uuid, manifest_id)
    _fsync_write(path, dump_json(receipt_body(turn_uuid, manifest_id, kind, payload)))
    return str(path)


def deliver_receipt(control: Path, call: dict) -> str:
    """Receipt delivery rides INSIDE the commit transaction, so a crash between
    the commit and the receipt is repaired by `journal replay` rather than by
    asking a model to redo anything (§6.1).  A duplicate on replay is not an
    error: the G track refusing a second receipt for one manifest is exactly the
    invariant we want, and the first one already landed."""
    try:
        return write_receipt(control, call["turn_uuid"], call["manifest_id"],
                             call["kind"], call.get("payload") or {})
    except Exception:  # noqa: BLE001 -- see docstring: replay must stay idempotent
        path = receipt_path_for(control, call["turn_uuid"], call["manifest_id"])
        if not path.exists():
            _fsync_write(path, dump_json(receipt_body(call["turn_uuid"], call["manifest_id"],
                                                      call["kind"], call.get("payload") or {})))
        return str(path)


# ---------------------------------------------------------------- derived index

def rebuild_index(control: Path) -> dict:
    """Rebuild the id registry from the commit graph (derived layer, §0)."""
    index = {"ids": {}, "source_commits": {}}
    for ref, head in all_refs(control).items():
        for commit in commit_chain(control, ref):
            snapshot = load_snapshot(control, commit["tree"])
            for nid, node in snapshot["nodes"].items():
                slot = index["ids"].setdefault(nid, {"node_type": node["node_type"],
                                                     "first_commit": commit["id"],
                                                     "roles_held": []})
                slot["last_commit"] = commit["id"]
                slot["last_state"] = node["state"]
            for role, value in (snapshot.get("role_assignments") or {}).items():
                if value and value in index["ids"] and \
                        role not in index["ids"][value]["roles_held"]:
                    index["ids"][value]["roles_held"].append(role)
            # detached nodes survive verbatim inside the commit's inverse_ops
            for op in commit.get("inverse_ops") or []:
                if op.get("op") == "add_node":
                    body = op.get("node") or {}
                    if body.get("id"):
                        index["ids"].setdefault(body["id"], {"node_type": body.get("node_type"),
                                                             "first_commit": commit["id"],
                                                             "roles_held": []})
        index["source_commits"][ref] = head
    return index


# ============================================================ §6.1  commit_from_ops

def preview_body(ref: str, commit_id: "str | None", snapshot: dict) -> dict:
    return {"schema": "auto-research/narrative-preview-v1", "ref": ref,
            "base_commit_id": commit_id, "generated_at": now_iso(), "snapshot": snapshot}


def refresh_preview(control: Path, ref: str, commit_id: "str | None", snapshot: dict) -> dict:
    return {"path": working_path(control, ref),
            "text": dump_json(preview_body(ref, commit_id, snapshot))}


def idempotency_key(control: Path, turn_uuid: str, manifest_id: str, role: str) -> str:
    """§7: sha256(project|turn_uuid|manifest_id|chronicler)."""
    return sha256_text("|".join([str(control.parent), turn_uuid or "", manifest_id or "", role]))


def find_commit_by_idempotency(control: Path, key: "str | None" = None, *,
                               turn_uuid: "str | None" = None,
                               manifest_id: "str | None" = None) -> "list | None":
    """§6.1 crash recovery: did this turn+manifest already land a commit?

    Public API for the G track's `journal replay`.  The memo written inside the
    commit transaction is the record of truth, so the answer never depends on a
    model regenerating ops.  Matching is by key OR by (turn_uuid, manifest_id) --
    the two tracks hash their idempotency keys over different tuples (§7 adds the
    role), so the pair is the reliable join.
    """
    memo_dir = journal_dir(control) / "idempotency"
    if not memo_dir.exists():
        return None
    for path in sorted(memo_dir.glob("*.json")):
        memo = ros.read_json_file(path, {})
        commit_id = memo.get("commit_id")
        if not commit_id or not (commits_dir(control) / f"{commit_id}.json").exists():
            continue
        if key and path.stem == key:
            return [commit_id]
        pair_match = (turn_uuid and manifest_id
                      and memo.get("turn_uuid") == turn_uuid
                      and memo.get("manifest_id") == manifest_id)
        if pair_match:
            return [commit_id]
    return None


def _memory_delta_item(control: Path, delta: dict) -> "dict | None":
    """§6.1: the same transaction persists the role memory delta when one is given."""
    if not isinstance(delta, dict) or not delta:
        return None
    path = control / "roles" / "chronicler.memory.json"
    memory = ros.read_json_file(path, {})
    memory.update(delta)
    memory["updated_at"] = now_iso()
    text = dump_json(memory)
    if len(text.encode("utf-8")) > 12 * 1024:
        refuse("ROLE_MEMORY_TOO_LARGE", "chronicler.memory.json would exceed 12 KB (§7)")
    return {"path": path, "text": text}


# ============================================================ v3 §2  real-time derivation

# A commit restates basis refs and lineage; `fig-registry` is re-checked with them
# because a figure is `current` FOR a claim, and this commit may have moved it.
GRAPH_RULES_COMMIT = ("narr-basis", "lineage", "fig-registry")
GRAPH_RULES_ALL = None          # None == every rule (a backfill rewrites everything)


def _graph_after(control: Path, rules, reason: str):
    """v3 contract §2: ask the research graph to reconcile itself right after
    THIS command's own transaction has landed.

    Best effort by construction. The graph owns relations; this host owns an
    object. If the graph cannot be reached or the derivation raises, the host
    transaction is already committed and stays committed: the failure becomes
    one line in ``graph/incidents.log`` and never reaches the return code.

    Returns ``{"derived": bool, "added": n, "retracted": m}`` (plus ``error``
    when it failed), or ``None`` when research_graph is not installed at all --
    in which case the caller simply omits the field.
    """
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import research_graph
    except ImportError:
        research_graph = None
    if research_graph is None:
        return None
    try:
        import inspect
        try:
            accepted = inspect.signature(research_graph.derive).parameters
        except (TypeError, ValueError):                    # pragma: no cover
            accepted = {}
        kwargs = {}
        if rules and "rules" in accepted:
            kwargs["rules"] = list(rules)
        if "reason" in accepted:
            kwargs["reason"] = reason
        result = research_graph.derive(Path(control), **kwargs) or {}
        return {"derived": True, "added": int(result.get("added") or 0),
                "retracted": int(result.get("retracted") or 0)}
    except Exception as exc:                               # noqa: BLE001
        try:
            directory = Path(control) / "graph"
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / "incidents.log").open("a", encoding="utf-8",
                                                    newline="\n") as handle:
                handle.write("GRAPH_DERIVE_FAILED %s: %s: %s\n"
                             % (reason, type(exc).__name__, str(exc)[:400]))
        except Exception:                                  # pragma: no cover
            pass
        return {"derived": False, "added": 0, "retracted": 0,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


def commit_from_ops(control: Path, ref: str, ops: list, meta: dict, *,
                    pending_patch_id: "str | None" = None,
                    turn_uuid: "str | None" = None,
                    manifest_id: "str | None" = None) -> dict:
    """THE single write path for snapshots, commits and refs (§0/§6.1).

    Public API used by the G track.  Returns
    {commit_id, kind, receipt_path, flags, ...}.
    """
    require_store(control)
    meta = dict(meta or {})
    turn_uuid = turn_uuid or meta.get("turn_uuid") or "detached"
    manifest_id = manifest_id or meta.get("input_manifest_id") or meta.get("manifest_id") or "none"
    author = dict(meta.get("author") or {})
    author.setdefault("session", ros.current_session_id(control) or "unknown")
    author.setdefault("role", "main")
    origin = meta.get("origin") or ("backfill" if author.get("role") == "backfill" else "live")
    if origin not in ("live", "backfill"):
        refuse("ORIGIN_INVALID", f"{origin!r} not in live|backfill")
    commit_at = meta.get("at") or now_iso()

    with narrative_lock(control):
        journal_replay(control)      # never build on a half-written transaction

        # ---- §6.1 idempotency: the same turn+manifest may only land once
        memo_dir = journal_dir(control) / "idempotency"
        memo = None
        if turn_uuid != "detached":
            key = idempotency_key(control, turn_uuid, manifest_id, author.get("role", "main"))
            memo = memo_dir / f"{key}.json"
            if memo.exists():
                landed = ros.read_json_file(memo, {}).get("commit_id")
                if landed and (commits_dir(control) / f"{landed}.json").exists():
                    return _repair_receipt(control, ref, landed, turn_uuid, manifest_id)

        head = read_ref(control, ref)
        if head is None and ref != "main":
            refuse("UNKNOWN_REF", f"{ref!r} has no head; `branch create` it first")
        old_snapshot = commit_snapshot(control, head) if head else empty_snapshot()

        # ---- §13: a pending patch is consumed exactly once
        patch_item = None
        if pending_patch_id:
            patch_path = pending_dir(control) / f"{pending_patch_id}.json"
            if not patch_path.exists():
                refuse("PENDING_PATCH_NOT_FOUND", pending_patch_id)
            patch = ros.read_json_file(patch_path, {})
            if patch.get("consumed"):
                refuse("PENDING_PATCH_ALREADY_CONSUMED", pending_patch_id)
            if patch.get("base_commit_id") != head:
                refuse("PENDING_PATCH_STALE",
                       f"patch base {patch.get('base_commit_id')} != head {head}")
            ops = patch.get("tree_ops") or []
            ref = patch.get("ref") or ref
            patch = dict(patch, consumed=True, consumed_at=now_iso())
            patch_item = {"path": patch_path, "text": dump_json(patch)}

        if not ops:
            refuse("EMPTY_OPS", "a commit needs at least one tree op "
                                "(no tree change => NARRATIVE_REVIEWED_NO_CHANGE receipt)")

        # ---- apply, then prove the inverse is exact (§3.3)
        new_snapshot, inverse_ops, ctx = apply_ops(old_snapshot, ops, control)
        restored, _, _ = apply_ops(new_snapshot, inverse_ops, control, restore=True)
        if tree_hash(restored) != tree_hash(old_snapshot):
            refuse("INVERSE_OPS_NOT_EXACT",
                   "generated inverse_ops do not restore the previous tree hash")
        assert_valid(new_snapshot, "post-op snapshot")

        parents = [head] if head else []
        parents += [p for p in (meta.get("extra_parents") or []) if p]
        kind, flags, root_identity_moved = determine_kind(old_snapshot, new_snapshot,
                                                          ops, parents, ctx)

        # ---- §3.4 the one hard gate; nothing is written when it fires
        if root_identity_moved and (ctx.set_root_used or ctx.rekeyed):
            approval = (meta.get("approval_refs") or {}).get("overthrow")
            if not resolve_decision(control, approval):
                refuse("OVERTHROW_REQUIRES_USER_APPROVAL",
                       f"replacing the root identity needs approval_refs.overthrow pointing at "
                       f"a user-approved decision record (got {approval!r})")

        decision = resolve_cause(control, kind, dict(meta, origin=origin), ctx,
                                 new_snapshot, commit_at)
        assert_cause_matrix(decision["cause"], decision["forced_status"])
        if decision["cause"] == "forced" and not decision.get("counterfactual"):
            refuse("FORCED_CAUSE_NEEDS_COUNTERFACTUAL", "cause=forced requires a counterfactual")

        proposed = meta.get("proposed_kind")
        if proposed and proposed != kind:
            flags = list(flags) + ["KIND_CORRECTED"]
        for note in decision.get("notes") or []:
            if note.startswith("CAUSE_CORRECTED"):
                flags = list(flags) + ["CAUSE_CORRECTED"]

        snapshot_hash = tree_hash(new_snapshot)
        commit = {
            "schema": COMMIT_SCHEMA,
            "tree": snapshot_hash,
            "parents": parents,
            "ref": ref,
            "author": author,
            "origin": origin,
            "at": commit_at,
            "message": meta.get("message") or "(no message)",
            "kind": kind,
            "cause": decision["cause"],
            "forced_status": decision["forced_status"],
            "trigger_ref": decision.get("trigger_ref"),
            "trigger_class": decision.get("trigger_class"),
            "counterfactual": decision.get("counterfactual"),
            "approval_refs": {"overthrow": (meta.get("approval_refs") or {}).get("overthrow"),
                              "forced_cause": decision["approval_refs"].get("forced_cause")},
            "ops": json.loads(canonical(ops)),
            "inverse_ops": json.loads(canonical(inverse_ops)),
            "flags": sorted(set(flags)),
            "input_manifest_id": meta.get("input_manifest_id") or meta.get("frame_id"),
        }
        commit["id"] = sha_of(commit)
        commit_id = commit["id"]

        if (commits_dir(control) / f"{commit_id}.json").exists():
            return _repair_receipt(control, ref, commit_id, turn_uuid, manifest_id)

        receipt_payload = {"commit_ids": [commit_id], "kind": kind,
                           "cause": decision["cause"],
                           "forced_status": decision["forced_status"],
                           "flags": commit["flags"], "ref": ref, "tree": snapshot_hash}
        plan = [
            {"path": objects_dir(control) / f"{snapshot_hash}.json", "text": dump_json(new_snapshot)},
            {"path": commits_dir(control) / f"{commit_id}.json", "text": dump_json(commit)},
            {"path": refs_dir(control) / ref, "text": commit_id + "\n"},
            refresh_preview(control, ref, commit_id, new_snapshot),
        ]
        memory_item = _memory_delta_item(control, meta.get("memory_delta"))
        if memory_item:
            plan.append(memory_item)
        if patch_item:
            plan.append(patch_item)
        if memo is not None:
            plan.append({"path": memo,
                         "text": dump_json({"commit_id": commit_id, "turn_uuid": turn_uuid,
                                            "manifest_id": manifest_id, "at": now_iso()})})
        transaction = run_transaction(
            control, f"commit-{commit_id[:16]}", plan, note=f"{kind} on {ref}",
            receipt_call={"turn_uuid": turn_uuid, "manifest_id": manifest_id,
                          "kind": "NARRATIVE_COMMITTED", "payload": receipt_payload})
        receipt_target = transaction.get("receipt_path") or \
            str(receipt_path_for(control, turn_uuid, manifest_id))

        # ---- derived layer (deletable, rebuilt from the authoritative commit)
        ros.atomic_write(index_path(control), dump_json(rebuild_index(control)))
        if ref == "main":
            with ros.contextlib_suppress():
                write_card(control)
        log_event(control, {"event": "narrative_committed", "commit": commit_id, "ref": ref,
                            "kind": kind, "cause": decision["cause"],
                            "summary": commit["message"][:160]})
        result = {"ok": True, "commit_id": commit_id, "kind": kind, "cause": decision["cause"],
                  "forced_status": decision["forced_status"], "tree": snapshot_hash,
                  "ref": ref, "flags": commit["flags"], "receipt_path": receipt_target,
                  "notes": decision.get("notes") or []}
        # v3 §2: the commit is landed and receipted; only now does the graph
        # reconcile the relations this snapshot states (basis refs, lineage,
        # and the figure registry, whose `current` set is claim-addressed).
        graph = _graph_after(control, GRAPH_RULES_COMMIT, "narrative:commit")
        if graph is not None:
            result["graph"] = graph
        return result


def _repair_receipt(control: Path, ref: str, commit_id: str,
                    turn_uuid: str, manifest_id: str) -> dict:
    """§6.1: the commit already landed (crash between commit and receipt, or a
    replayed turn).  Backfill whatever is missing; never write a second commit."""
    commit = load_commit(control, commit_id)
    snapshot = load_snapshot(control, commit["tree"])
    plan = []
    if read_ref(control, ref) != commit_id and commit_id in ancestors(control,
                                                                     read_ref(control, ref) or commit_id):
        pass  # head already moved past this commit; leave the ref alone
    elif read_ref(control, ref) is None:
        plan.append({"path": refs_dir(control) / ref, "text": commit_id + "\n"})
    preview = ros.read_json_file(working_path(control, ref), {})
    if preview.get("base_commit_id") != read_ref(control, ref):
        plan.append(refresh_preview(control, ref, read_ref(control, ref) or commit_id,
                                    load_head_snapshot(control, ref) or snapshot))
    run_transaction(control, f"repair-{commit_id[:16]}", plan, note="receipt repair",
                    receipt_call={"turn_uuid": turn_uuid, "manifest_id": manifest_id,
                                  "kind": "NARRATIVE_COMMITTED",
                                  "payload": {"commit_ids": [commit_id], "kind": commit["kind"],
                                              "cause": commit["cause"], "ref": ref,
                                              "tree": commit["tree"], "repaired": True}})
    return {"ok": True, "commit_id": commit_id, "kind": commit["kind"],
            "cause": commit["cause"], "forced_status": commit["forced_status"],
            "ref": ref, "flags": sorted(set(commit.get("flags") or [])),
            "receipt_path": str(receipt_path_for(control, turn_uuid, manifest_id)),
            "status": "ALREADY_COMMITTED_RECEIPT_REPAIRED"}


# ============================================================ §0  init

def cmd_init(args) -> int:
    control = ros.locate_control(Path(args.root))
    if not getattr(args, "title", None):
        try:
            args.title = (ros.load_state(control) or {}).get("title") or "narrative tree"
        except Exception:
            args.title = "narrative tree"
    if ndir(control).exists() and read_ref(control, "main"):
        ros.die(f"narrative store already initialised at {ndir(control)}", 1)
    for sub in ("objects", "commits", "refs/heads", "branches", "tags", "working",
                "annotations", "incidents", "index", "journal", "pending", "decisions",
                "backfill/adjudications", "backfill/frames"):
        (ndir(control) / sub).mkdir(parents=True, exist_ok=True)
    receipts_dir(control).mkdir(parents=True, exist_ok=True)
    if not config_path(control).exists():
        ros.atomic_write(config_path(control), dump_json(load_config(control)))
    registry = load_branch_registry(control)
    registry["branches"].setdefault("main", {
        "created_from": {"ref": None, "commit": None},
        "disposition_history": [{"disposition": "OPEN", "basis_refs": [],
                                 "reason": "trunk", "by": args.session or "init",
                                 "at": now_iso(), "source_commit_id": None}]})
    ros.atomic_write(registry_path(control), dump_json(registry))

    root_node = {
        "id": args.root_id, "node_type": "narrative",
        "identity_key": {"role_hint": "north_star", "thesis": args.thesis or args.title},
        "title": args.title, "summary": args.summary or "", "statement": args.thesis or args.title,
        "state": {"epistemic": "PENDING", "narrative": "ACTIVE"},
    }
    meta = {"message": args.message or f"genesis: {args.title}",
            "author": {"session": args.session or "init", "role": "main"},
            "at": args.at or now_iso(), "origin": "live",
            "approval_refs": {"overthrow": None, "forced_cause": None}}
    ops = [{"op": "add_node", "node": root_node, "parent": None}]
    if args.venue:
        ops.append({"op": "patch_meta", "fields": {"venue": args.venue}})
    result = commit_from_ops(control, "main", ops, meta,
                             turn_uuid=args.turn_uuid, manifest_id=args.manifest_id)
    result["narrative_dir"] = str(ndir(control))
    return emit(result)


# ============================================================ §4  derived events

def _detach_records(commit: dict) -> dict:
    """Which nodes left the tree in this commit, and on what stated basis."""
    out = {}
    for op in commit.get("ops") or []:
        if op.get("op") == "detach_node":
            meta = op.get("disposition_meta") or {}
            out[op.get("id")] = {"disposition": op.get("narrative_disposition"),
                                 "epistemic": op.get("epistemic"),
                                 "basis_refs": list(meta.get("basis_refs") or []),
                                 "reason": meta.get("reason"), "target": meta.get("target")}
        elif op.get("op") == "split_node" and (op.get("origin_disposition") or {}):
            disposition = op["origin_disposition"]
            out[op.get("id")] = {"disposition": disposition.get("narrative_disposition"),
                                 "epistemic": disposition.get("epistemic"),
                                 "basis_refs": list(disposition.get("basis_refs") or []),
                                 "reason": "semantic split", "target": None}
        elif op.get("op") == "merge_nodes":
            into = op.get("into")
            into_id = into.get("id") if isinstance(into, dict) else into
            for source in op.get("ids") or []:
                if source != into_id:
                    out[source] = {"disposition": "MERGED", "epistemic": None,
                                   "basis_refs": [], "reason": op.get("reason"),
                                   "target": into_id}
    return out


def derive_history(control: Path, ref: str = "main") -> dict:
    """Replay the commit chain once and read off every §4 derived event.

    Nothing here is stored authoritatively -- LEVER_DEBT and friends are always
    recomputed from commits, so a deleted cache is never a lost verdict.
    """
    chain = commit_chain(control, ref)
    roles_ever, role_history = {}, []
    demoted_at, gone_at, restored, reversals = {}, {}, [], []
    touches = {}
    previous = empty_snapshot()

    for commit in chain:
        snapshot = load_snapshot(control, commit["tree"])
        prev_roles = previous.get("role_assignments") or {}
        cur_roles = snapshot.get("role_assignments") or {}
        for role, value in cur_roles.items():
            if value:
                roles_ever.setdefault(value, set()).add(role)
            if prev_roles.get(role) != value:
                role_history.append({"commit": commit["id"], "at": commit["at"], "role": role,
                                     "from": prev_roles.get(role), "to": value,
                                     "kind": commit["kind"], "cause": commit["cause"]})
        for op in commit.get("ops") or []:
            for nid in (op.get("id"), op.get("new_parent"), op.get("parent")):
                if nid:
                    touches.setdefault(nid, []).append(commit["id"])
            if op.get("op") == "add_node":
                nid = (op.get("node") or {}).get("id")
                if nid:
                    touches.setdefault(nid, []).append(commit["id"])

        detached = _detach_records(commit)
        for nid, record in detached.items():
            prior = previous["nodes"].get(nid, {})
            gone_at[nid] = dict(record, commit=commit["id"], at=commit["at"],
                                cause=commit["cause"], kind=commit["kind"],
                                prior_roles=sorted(r for r, v in prev_roles.items() if v == nid),
                                title=prior.get("title", ""),
                                epistemic=record.get("epistemic")
                                or ((prior.get("state") or {}).get("epistemic")),
                                basis_refs=record["basis_refs"] or list(prior.get("basis_refs") or []))

        for nid, node in snapshot["nodes"].items():
            now_state = node.get("state") or {}
            before = previous["nodes"].get(nid)
            before_state = (before or {}).get("state") or {}
            if now_state.get("narrative") == "DEMOTED" and before_state.get("narrative") != "DEMOTED":
                demoted_at[nid] = {"commit": commit["id"], "at": commit["at"],
                                   "cause": commit["cause"], "kind": commit["kind"],
                                   "prior_roles": sorted(r for r, v in prev_roles.items() if v == nid),
                                   "title": node.get("title", "")}
            came_back = before is None and nid in gone_at and gone_at[nid]["commit"] != commit["id"]
            was_sidelined = before_state.get("narrative") in ("DROPPED", "DEMOTED")
            if now_state.get("narrative") == "ACTIVE" and (came_back or was_sidelined):
                source = demoted_at.get(nid) or gone_at.get(nid) or {}
                regained = [r for r, v in cur_roles.items() if v == nid]
                event = {"event": "RESTORED", "node": nid, "at": commit["at"],
                         "commit": commit["id"],
                         "prior_role": (source.get("prior_roles") or [None])[0],
                         "source_commit": source.get("commit"),
                         "reverts_commit": source.get("commit") if regained else None,
                         "regained_roles": regained}
                restored.append(event)
                # ---- ROLE_REVERSAL: back inside 60 days with no new refutation
                if source.get("at") and regained:
                    gap = days_between(source["at"], commit["at"])
                    refuted = any(c.get("trigger_class") in REFUTING_TRIGGERS
                                  and nid in _commit_touched_ids(c)
                                  for c in chain
                                  if parse_ts(source["at"]) < parse_ts(c["at"]) <= parse_ts(commit["at"]))
                    if gap <= ROLE_REVERSAL_DAYS and not refuted:
                        reversals.append({"event": "ROLE_REVERSAL", "node": nid,
                                          "days": round(gap, 1), "down_commit": source["commit"],
                                          "up_commit": commit["id"], "roles": regained,
                                          "at": commit["at"]})
                demoted_at.pop(nid, None)
                gone_at.pop(nid, None)
        previous = snapshot

    head_snapshot = previous if chain else empty_snapshot()
    return {"chain": chain, "head_snapshot": head_snapshot, "roles_ever": roles_ever,
            "role_history": role_history, "demoted_at": demoted_at, "gone_at": gone_at,
            "restored": restored, "role_reversals": reversals, "touches": touches}


def _commit_touched_ids(commit: dict) -> set:
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


def lever_debt(history: dict) -> list:
    """§4 LEVER_DEBT: a lever that is still HELD but was quietly demoted, with
    no killing basis and no successor that actually took its place."""
    head = history["head_snapshot"]
    roles = head.get("role_assignments") or {}
    out = []
    for nid, node in sorted(head["nodes"].items()):
        state = node.get("state") or {}
        if state.get("epistemic") != "HELD" or state.get("narrative") != "DEMOTED":
            continue
        held = set(history["roles_ever"].get(nid, set())) & set(LEVER_ROLES)
        if not held:
            continue
        record = history["demoted_at"].get(nid) or {}
        if record.get("cause") not in ("unforced", "unknown"):
            continue
        if qualifying_basis(node.get("basis_refs")):
            continue
        replacement = None
        for other_id, other in head["nodes"].items():
            other_state = other.get("state") or {}
            if other_state.get("epistemic") != "HELD" or other_state.get("narrative") != "ACTIVE":
                continue
            if not any(e.get("rel") == "replaces" and e.get("of") == nid
                       for e in other.get("lineage") or []):
                continue
            if any(roles.get(role) == other_id for role in held):
                replacement = other_id
                break
        if replacement:
            continue
        out.append({"event": "LEVER_DEBT", "node": nid, "title": node.get("title", ""),
                    "roles_held": sorted(held), "demoted_commit": record.get("commit"),
                    "demoted_at": record.get("at"), "cause": record.get("cause"),
                    "replacement": None})
    return out


def lost_levers(history: dict) -> list:
    """§4 LOST_LEVER: a former headline/flagship dropped with nothing adjudicated."""
    out = []
    for nid, record in sorted(history["gone_at"].items()):
        if record.get("disposition") != "DROPPED":
            continue
        held = set(history["roles_ever"].get(nid, set())) & {"headline", "flagship"}
        if not held:
            continue
        if record.get("epistemic") == "KILLED" or qualifying_basis(record.get("basis_refs")):
            continue
        out.append({"event": "LOST_LEVER", "node": nid, "title": record.get("title", ""),
                    "roles_held": sorted(held), "dropped_commit": record.get("commit"),
                    "dropped_at": record.get("at"), "reason": record.get("reason")})
    return out


def unresolved_nodes(history: dict) -> list:
    """§4 未闭环: DROPPED, not KILLED, and no adjudicating basis."""
    out = []
    for nid, record in sorted(history["gone_at"].items()):
        if record.get("disposition") != "DROPPED":
            continue
        if record.get("epistemic") == "KILLED" or qualifying_basis(record.get("basis_refs")):
            continue
        out.append({"node": nid, "title": record.get("title", ""),
                    "at": record.get("at"), "reason": record.get("reason"),
                    "commit": record.get("commit")})
    return out


def live_bets(snapshot: dict) -> list:
    """§4 projection: PENDING and (load-bearing or flagged) with a resolution condition."""
    bearing = bearing_ids(snapshot)
    out = []
    for nid, node in sorted(snapshot["nodes"].items()):
        state = node.get("state") or {}
        meta = node.get("state_meta") or {}
        if state.get("epistemic") != "PENDING":
            continue
        if not (nid in bearing or meta.get("live_bet")):
            continue
        if not meta.get("resolution_condition"):
            continue
        out.append({"node": nid, "title": node.get("title", ""),
                    "resolution_condition": meta.get("resolution_condition"),
                    "live_bet": bool(meta.get("live_bet")), "bearing": nid in bearing})
    return out


# ============================================================ §5  branches

def active_k_refs(control: Path, branch: str) -> list:
    """A K## task counts as active while it is open or claimed (docs/collab-protocol)."""
    out = []
    directory = control / "collab" / "tasks"
    if not directory.exists():
        return out
    for path in sorted(directory.glob("K*.json")):
        task = ros.read_json_file(path, {})
        if task.get("status") not in ("open", "claimed"):
            continue
        blob = canonical({k: v for k, v in task.items() if k != "status"})
        if task.get("narrative_ref") == branch or f'"{branch}"' in blob or branch in str(task.get("title", "")):
            out.append(task.get("id") or path.stem)
    return out


def branch_report(control: Path) -> list:
    registry = load_branch_registry(control)
    main_head = read_ref(control, "main")
    main_ancestry = ancestors(control, main_head) if main_head else set()
    out = []
    for name, entry in sorted(registry.get("branches", {}).items()):
        if name == "main":
            continue
        head = read_ref(control, name)
        disposition = branch_disposition(entry)
        merged = bool(head) and head in main_ancestry
        tasks = active_k_refs(control, name)
        out.append({
            "branch": name, "head": head, "disposition": disposition,
            "merged_into_main": merged, "active_k_refs": tasks,
            # §5: OPEN + not an ancestor of main + nobody working on it.  The
            # system reports this forever and NEVER auto-drops it.
            "unresolved": disposition == "OPEN" and not merged and not tasks,
            "created_from": entry.get("created_from"),
            "history_len": len(entry.get("disposition_history") or []),
        })
    return out


def append_disposition(control: Path, name: str, disposition: str, *, basis_refs=None,
                       reason: str = "", by: str = "", source_commit_id=None,
                       meta: "dict | None" = None) -> dict:
    """§0/§5: disposition events are their own append-only atomic transaction and
    NEVER touch the tree or a ref."""
    if disposition not in DISPOSITIONS:
        refuse("DISPOSITION_INVALID", f"{disposition!r} not in {list(DISPOSITIONS)}")
    registry = load_branch_registry(control)
    entry = registry.get("branches", {}).get(name)
    if entry is None:
        refuse("BRANCH_NOT_FOUND", name)
    event = {
        "disposition": disposition, "basis_refs": list(basis_refs or []),
        "reason": reason, "by": by or (ros.current_session_id(control) or "unknown"),
        "at": now_iso(), "source_commit_id": source_commit_id}
    if meta:
        # §5: strategy detail (fast-forward vs two-parent merge) rides in meta;
        # the disposition vocabulary itself stays the contract's four values.
        event["meta"] = json.loads(canonical(meta))
    entry.setdefault("disposition_history", []).append(event)
    run_transaction(control, f"disposition-{name}-{uuid.uuid4().hex[:8]}",
                    [{"path": registry_path(control), "text": dump_json(registry)}],
                    note=f"branch {name} -> {disposition}")
    log_event(control, {"event": "narrative_branch_disposition", "branch": name,
                        "disposition": disposition, "summary": reason[:160]})
    return entry


def cmd_branch(args) -> int:
    control = ros.locate_control(Path(args.root))
    require_store(control)
    with narrative_lock(control):
        if args.action == "create":
            return _branch_create(control, args)
        if args.action == "promote":
            return _branch_promote(control, args)
        if args.action == "kill":
            if not args.basis:
                ros.die("branch kill requires --basis (§5: a kill needs a qualifying basis)", 1)
            if not qualifying_basis(args.basis):
                ros.die(f"--basis must name one of {list(KILL_BASIS_KINDS)} "
                        f"as `<kind>:<ref>`", 1)
            append_disposition(control, args.name, "KILLED", basis_refs=args.basis,
                               reason=args.reason or "", by=args.by or "",
                               source_commit_id=read_ref(control, args.name))
            return emit({"ok": True, "branch": args.name, "disposition": "KILLED"})
        if args.action == "drop":
            if not args.reason:
                ros.die("branch drop requires an explicit --reason (§5: never automatic)", 1)
            append_disposition(control, args.name, "DROPPED", reason=args.reason,
                               by=args.by or "", source_commit_id=read_ref(control, args.name))
            return emit({"ok": True, "branch": args.name, "disposition": "DROPPED"})
        if args.action == "list":
            return emit({"ok": True, "branches": branch_report(control)})
    return 2


def _branch_create(control: Path, args) -> int:
    name = args.name
    if not BRANCH_RE.match(name or "") or name == "main":
        ros.die(f"invalid branch name {name!r}", 1)
    if read_ref(control, name):
        ros.die(f"branch {name!r} already exists", 1)
    source_ref, _, source_commit = (args.source or "main").partition("@")
    commit_id = source_commit or read_ref(control, source_ref)
    if not commit_id:
        ros.die(f"cannot resolve --from {args.source!r}", 1)
    load_commit(control, commit_id)
    registry = load_branch_registry(control)
    registry.setdefault("branches", {})[name] = {
        "created_from": {"ref": source_ref, "commit": commit_id},
        # §5 (R8): `branch create` atomically appends the first OPEN event, so
        # "current disposition = last event" is defined from the first moment.
        "disposition_history": [{"disposition": "OPEN", "basis_refs": [],
                                 "reason": args.reason or f"branched from {source_ref}",
                                 "by": args.by or (ros.current_session_id(control) or "unknown"),
                                 "at": now_iso(), "source_commit_id": commit_id}]}
    snapshot = commit_snapshot(control, commit_id)
    run_transaction(control, f"branch-create-{name}-{uuid.uuid4().hex[:8]}", [
        {"path": registry_path(control), "text": dump_json(registry)},
        {"path": refs_dir(control) / name, "text": commit_id + "\n"},
        refresh_preview(control, name, commit_id, snapshot),
    ], note=f"create branch {name}")
    log_event(control, {"event": "narrative_branch_created", "branch": name,
                        "summary": f"from {source_ref}@{commit_id[:12]}"})
    return emit({"ok": True, "branch": name, "head": commit_id, "disposition": "OPEN"})


def merge_snapshots(base: dict, into: dict, other: dict) -> "tuple":
    """§5 promote: auto-merge ONLY disjoint expression fields.  Everything
    else -- parent/order, state, identity, roles, detach, split/merge -- lands
    on the conflict list for a human to adjudicate."""
    conflicts, ops = [], []
    base_nodes, into_nodes, other_nodes = base["nodes"], into["nodes"], other["nodes"]
    for nid in sorted(set(into_nodes) | set(other_nodes) | set(base_nodes)):
        in_base, in_into, in_other = nid in base_nodes, nid in into_nodes, nid in other_nodes
        if in_into != in_other:
            side = "branch" if in_other else "main"
            if in_base or not in_base:
                conflicts.append({"node": nid, "field": "presence",
                                  "detail": f"node exists only on {side} (add/detach)"} )
            continue
        if not in_into:
            continue
        left, right = into_nodes[nid], other_nodes[nid]
        anchor = base_nodes.get(nid, left)
        for field in ("identity_key", "node_type", "state", "state_meta",
                      "basis_refs", "disposition_meta", "lineage", "children"):
            if canonical(left.get(field)) != canonical(right.get(field)):
                conflicts.append({"node": nid, "field": field,
                                  "detail": "structural/state divergence needs adjudication"})
        for field in EXPRESSION_FIELDS:
            left_changed = canonical(left.get(field)) != canonical(anchor.get(field))
            right_changed = canonical(right.get(field)) != canonical(anchor.get(field))
            if right_changed and not left_changed:
                ops.append({"op": "patch_fields", "id": nid, "fields": {field: right.get(field)}})
            elif right_changed and left_changed and \
                    canonical(left.get(field)) != canonical(right.get(field)):
                conflicts.append({"node": nid, "field": field,
                                  "detail": "both sides rewrote this text"})
    if canonical(into.get("role_assignments")) != canonical(other.get("role_assignments")):
        conflicts.append({"node": None, "field": "role_assignments",
                          "detail": "role assignments diverged"})
    if canonical(into.get("meta")) != canonical(other.get("meta")):
        conflicts.append({"node": None, "field": "meta", "detail": "tree meta diverged"})
    if into.get("root") != other.get("root"):
        conflicts.append({"node": None, "field": "root", "detail": "different roots"})
    return ops, conflicts


def _branch_promote(control: Path, args) -> int:
    name = args.name
    target = args.into or "main"
    branch_head = read_ref(control, name)
    target_head = read_ref(control, target)
    if not branch_head:
        ros.die(f"branch {name!r} has no head", 1)
    if not target_head:
        ros.die(f"target ref {target!r} has no head", 1)
    if branch_head == target_head:
        return emit({"ok": True, "branch": name, "strategy": "already-in-sync"})

    if target_head in ancestors(control, branch_head):
        # ---- fast-forward: no divergence, so no merge commit and no new tree
        snapshot = commit_snapshot(control, branch_head)
        run_transaction(control, f"promote-ff-{name}-{uuid.uuid4().hex[:8]}", [
            {"path": refs_dir(control) / target, "text": branch_head + "\n"},
            refresh_preview(control, target, branch_head, snapshot),
        ], note=f"fast-forward {target} to {name}")
        append_disposition(control, name, "MERGED",
                           reason=args.reason or f"promoted into {target} (fast-forward)",
                           by=args.by or "", source_commit_id=branch_head,
                           meta={"strategy": "fast-forward", "into": target,
                                 "merge_commit": None})
        if target == "main":
            with ros.contextlib_suppress():
                write_card(control)
        return emit({"ok": True, "branch": name, "into": target, "strategy": "fast-forward",
                     "commit_id": branch_head})

    shared = ancestors(control, branch_head) & ancestors(control, target_head)
    base_commit = max(shared, key=lambda c: parse_ts(load_commit(control, c)["at"])) if shared else None
    base = commit_snapshot(control, base_commit) if base_commit else empty_snapshot()
    into_snapshot = commit_snapshot(control, target_head)
    other_snapshot = commit_snapshot(control, branch_head)
    if base.get("root") and other_snapshot.get("root") and \
            into_snapshot.get("root") and other_snapshot["root"] != into_snapshot["root"]:
        approval = args.approval
        if not resolve_decision(control, approval):
            ros.die("OVERTHROW_REQUIRES_USER_APPROVAL: promoting a branch with a different "
                    "root into main needs --approval pointing at a user-approved decision "
                    "record (§3.4)", 1)
    ops, conflicts = merge_snapshots(base, into_snapshot, other_snapshot)
    if conflicts:
        print(json.dumps({"ok": False, "error": "MERGE_CONFLICTS_REQUIRE_ADJUDICATION",
                          "branch": name, "into": target, "base_commit": base_commit,
                          "auto_mergeable_ops": ops, "conflicts": conflicts},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    if not ops:
        ros.die("nothing to promote: no expression-field changes and no conflicts", 1)
    meta = {"message": args.message or f"merge branch {name} into {target}",
            "author": {"session": args.by or (ros.current_session_id(control) or "unknown"),
                       "role": "main"},
            "extra_parents": [branch_head], "at": args.at or now_iso(),
            "approval_refs": {"overthrow": args.approval, "forced_cause": None}}
    result = commit_from_ops(control, target, ops, meta,
                             turn_uuid=args.turn_uuid, manifest_id=args.manifest_id)
    append_disposition(control, name, "MERGED",
                       reason=args.reason or f"promoted into {target} (two-parent merge)",
                       by=args.by or "", source_commit_id=result["commit_id"],
                       meta={"strategy": "merge", "into": target,
                             "merge_commit": result["commit_id"],
                             "parents": [target_head, branch_head]})
    result["strategy"] = "merge"
    result["branch"] = name
    return emit(result)


# ============================================================ §8  the current narrative card

def _runtime_control_part(control: Path) -> dict:
    turn = ros.read_json_file(control / "runtime" / "turn.json", {})
    lease = ros.read_json_file(control / "runtime" / "lease.json", {})
    last_receipt = None
    if receipts_dir(control).exists():
        receipts = sorted(receipts_dir(control).glob("*.json"),
                          key=lambda p: p.stat().st_mtime)
        if receipts:
            body = ros.read_json_file(receipts[-1], {})
            last_receipt = {"kind": body.get("kind"), "turn_uuid": body.get("turn_uuid"),
                            "manifest_id": body.get("manifest_id"), "at": body.get("at"),
                            "commit_ids": body.get("commit_ids") or []}
    incidents = []
    incidents_dir = ndir(control) / "incidents"
    if incidents_dir.exists():
        for path in sorted(incidents_dir.glob("*.json")):
            body = ros.read_json_file(path, {})
            if body.get("status") not in ("closed", "resolved"):
                incidents.append({"id": path.stem, "reason": body.get("reason")})
    return {"turn": {"display_id": turn.get("display_id"), "status": turn.get("status"),
                     "turn_uuid": turn.get("turn_uuid")},
            "lease": {"holder_session": lease.get("holder_session"),
                      "expires_at": lease.get("expires_at")},
            "last_receipt": last_receipt, "incidents_open": incidents}


def derive_card(control: Path) -> dict:
    """§8.  Derived ONLY from the committed snapshot at refs/heads/main -- never
    from a preview, so the card can never advertise an unlanded story."""
    require_store(control)
    source_commit = read_ref(control, "main")
    history = derive_history(control, "main")
    snapshot = history["head_snapshot"]
    root = snapshot.get("root")
    root_node = snapshot["nodes"].get(root, {}) if root else {}
    roles = {}
    for role in ROLES:
        value = (snapshot.get("role_assignments") or {}).get(role)
        roles[role] = None if not value else {
            "node": value, "title": snapshot["nodes"].get(value, {}).get("title", ""),
            "state": snapshot["nodes"].get(value, {}).get("state", {})}
    exclusions = [{"node": nid, "reason": record.get("reason"),
                   "disposition": record.get("disposition"),
                   "basis_refs": record.get("basis_refs") or []}
                  for nid, record in sorted(history["gone_at"].items())]
    card = {
        "schema": "auto-research/narrative-card-v1",
        "source_commit_id": source_commit,
        "generated_at": now_iso(),
        "narrative_part": {
            "north_star": root_node.get("statement") or root_node.get("title") or "",
            "root": root,
            "venue": (snapshot.get("meta") or {}).get("venue"),
            "roles": roles,
            "exclusions": exclusions,
            "live_bets": live_bets(snapshot),
            "lever_debt": lever_debt(history),
            "lost_levers": lost_levers(history),
            "unresolved_branches": [b for b in branch_report(control) if b["unresolved"]],
            "restored": history["restored"],
            "role_reversals": history["role_reversals"],
        },
        "control_part": _runtime_control_part(control),
    }
    return card


def card_markdown(card: dict, brief: bool = False) -> str:
    limit = CARD_BRIEF_MAX if brief else CARD_MD_MAX
    part = card["narrative_part"]
    lines = [f"# narrative card @ {str(card.get('source_commit_id'))[:12]}",
             f"north star: {part['north_star']}"]
    if part.get("venue"):
        lines.append(f"venue: {part['venue']}")
    lines.append("")
    lines.append("## roles")
    for role in ROLES:
        value = part["roles"].get(role)
        lines.append(f"- {role}: " + (f"{value['node']} — {value['title']}" if value else "(none)"))
    for title, key, fmt in (
            ("live bets", "live_bets", lambda x: f"- {x['node']} — {x['title']} "
                                                 f"[resolves: {x['resolution_condition']}]"),
            ("lever debt ⚠", "lever_debt", lambda x: f"- ⚠ {x['node']} — {x['title']} "
                                                     f"(was {'/'.join(x['roles_held'])}, "
                                                     f"demoted {x['cause']}, no replacement)"),
            ("lost levers ⚠", "lost_levers", lambda x: f"- ⚠ {x['node']} — {x['title']} "
                                                       f"(was {'/'.join(x['roles_held'])}, dropped "
                                                       f"with no adjudication)"),
            ("unresolved branches", "unresolved_branches",
             lambda x: f"- {x['branch']} @ {str(x['head'])[:12]} (OPEN, not in main, no K##)"),
            ("exclusions", "exclusions",
             lambda x: f"- {x['node']} {x['disposition']}: {x['reason'] or '(no reason)'}"),
    ):
        rows = part.get(key) or []
        if not rows:
            continue
        lines.append("")
        lines.append(f"## {title}")
        for row in rows:
            lines.append(fmt(row))
    control_part = card["control_part"]
    lines.append("")
    lines.append(f"## control · turn {control_part['turn'].get('display_id')} "
                 f"{control_part['turn'].get('status')} · lease "
                 f"{control_part['lease'].get('holder_session')} · last receipt "
                 f"{(control_part.get('last_receipt') or {}).get('kind')}")
    if len(lines) > limit:
        kept = lines[:limit - 1]
        kept.append(f"… +{len(lines) - limit + 1}; FULL:.research-os/narrative/card.json")
        lines = kept
    return "\n".join(lines) + "\n"


def write_card(control: Path) -> dict:
    card = derive_card(control)
    ros.atomic_write(ndir(control) / "card.json", dump_json(card))
    ros.atomic_write(ndir(control) / "card.md", card_markdown(card))
    return card


def cmd_card(args) -> int:
    control = ros.locate_control(Path(args.root))
    require_store(control)
    if args.now:
        set_now(args.now)
    card = write_card(control)
    if args.md:
        print(card_markdown(card, brief=args.brief), end="")
        return 0
    return emit(card)


# ============================================================ §9  trajectory

def trajectory_lines(control: Path, window: int = 30) -> list:
    """<= 40 lines under FIXED per-block budgets; overflow inside a block is
    written as `… +N; FULL:<pointer>` and never steals another block's budget."""
    history = derive_history(control, "main")
    snapshot = history["head_snapshot"]
    chain = history["chain"]
    roles = snapshot.get("role_assignments") or {}
    head = read_ref(control, "main")
    pointer = ".research-os/narrative/card.json"
    out: list = []

    # graph-contract 6: where an edge ledger exists, the last line points at the
    # cross-store coverage view.  It is paid for out of the commit block's own
    # budget, so the whole projection stays inside its 40 lines (contract 9).
    graph_pointer = (control / "graph").exists()

    def block(name: str, header: str, rows: list) -> None:
        budget = TRAJECTORY_BUDGET[name] - (1 if (graph_pointer and name == "graph") else 0)
        out.append(header)
        room = budget - 1
        if len(rows) <= room:
            out.extend(rows)
        else:
            out.extend(rows[:room - 1])
            out.append(f"    … +{len(rows) - (room - 1)}; FULL:{pointer}")

    root = snapshot.get("root")
    root_node = snapshot["nodes"].get(root, {}) if root else {}
    block("current", f"## current @ {str(head)[:12]} · {len(snapshot['nodes'])} nodes", [
        f"    root {root}: {(root_node.get('statement') or root_node.get('title') or '')[:88]}",
        f"    headline={roles.get('headline')} flagship={roles.get('flagship')} "
        f"backbone={roles.get('backbone')}",
        f"    empirical_core={roles.get('empirical_core')} foil={roles.get('foil')} "
        f"venue={(snapshot.get('meta') or {}).get('venue')}",
    ])

    structural = [c for c in chain if c["kind"] in ("restructure", "overthrow")
                  or (c["kind"] == "merge" and c["cause"] != "none")]
    graph_rows = []
    for commit in reversed(chain):
        marker = {"refine": ".", "restructure": "*", "overthrow": "!", "merge": "+"}[commit["kind"]]
        trigger = f" <{commit['trigger_class']}>" if commit.get("trigger_class") else ""
        forced = "" if commit["cause"] == "none" else f" [{commit['cause']}/{commit['forced_status']}]"
        flags = f" {'+'.join(commit.get('flags') or [])}" if commit.get("flags") else ""
        graph_rows.append(f"    {marker} {commit['at'][:10]} {str(commit['id'])[:8]} "
                          f"{commit['kind']}{forced}{trigger}{flags} "
                          f"{commit['message'][:52]}")
    block("graph", f"## graph · {len(chain)} commits ({len(structural)} structural)", graph_rows)

    bets = live_bets(snapshot)
    block("open_loops", f"## open loops · {len(bets)} live bet(s)",
          [f"    {b['node']} {b['title'][:40]} ? {b['resolution_condition'][:44]}" for b in bets])

    hot = sorted(history["touches"].items(), key=lambda kv: (-len(kv[1]), kv[0]))
    hot = [(nid, hits) for nid, hits in hot if nid in snapshot["nodes"]]
    block("hotspots", "## hotspots",
          [f"    {nid} x{len(hits)} {snapshot['nodes'][nid].get('title', '')[:44]}"
           for nid, hits in hot])

    unresolved = [f"    branch {b['branch']} UNRESOLVED_BRANCH (OPEN, not in main, no K##)"
                  for b in branch_report(control) if b["unresolved"]]
    unresolved += [f"    {n['node']} DROPPED without adjudication — {(n['reason'] or '')[:44]}"
                   for n in unresolved_nodes(history)]
    block("unresolved", f"## unresolved · {len(unresolved)}", unresolved)

    structural_n = len(structural)
    unforced_n = len([c for c in structural if c["cause"] == "unforced"])
    unknown_n = len([c for c in structural if c["cause"] == "unknown"])
    headline_changes = len([r for r in history["role_history"]
                            if r["role"] == "headline" and r["from"] is not None])
    last_structural = structural[-1]["at"] if structural else (chain[0]["at"] if chain else now_iso())
    stability = round(days_between(last_structural, now_iso()), 1)
    debts = lever_debt(history)
    losses = lost_levers(history)
    branches = branch_report(control)
    rows = [
        f"    structural={structural_n} unforced={unforced_n} unknown={unknown_n} "
        f"headline_identity_changes={headline_changes}",
        f"    structural_stability_days={stability} (refine does not reset) "
        f"open_branches={len(branches)} (unresolved={len([b for b in branches if b['unresolved']])})",
    ]
    rows += [f"    ⚠ LEVER_DEBT {d['node']} {d['title'][:30]} (was {'/'.join(d['roles_held'])}, "
             f"{d['cause']} demotion, no replacement)" for d in debts]
    rows += [f"    ⚠ LOST_LEVER {l['node']} {l['title'][:30]} (was {'/'.join(l['roles_held'])}, "
             f"dropped unadjudicated)" for l in losses]
    rows += [f"    RESTORED {r['node']} -> {'/'.join(r['regained_roles']) or 'no role'} "
             f"{r['at'][:10]}" for r in history["restored"]]
    rows += [f"    ROLE_REVERSAL {r['node']} down->up in {r['days']}d, no new refutation"
             for r in history["role_reversals"]]
    block("history", f"## history & anomalies (window {window}d)", rows)
    if graph_pointer:
        out.append(f"GRAPH: py {Path(__file__).resolve().parent / 'research_graph.py'} "
                   f"view coverage")
    return out


def cmd_trajectory(args) -> int:
    control = ros.locate_control(Path(args.root))
    require_store(control)
    if args.now:
        set_now(args.now)
    lines = trajectory_lines(control, args.window)
    print("\n".join(lines))
    return 0


# ============================================================ log / diff / blame / revert

def snapshot_diff(old: dict, new: dict) -> list:
    """Node-level, never line-level (§15 assertion 1)."""
    out = []
    old_nodes, new_nodes = old.get("nodes", {}), new.get("nodes", {})
    if old.get("root") != new.get("root"):
        out.append({"op": "set_root", "new_root_id": new.get("root")})
    for nid in sorted(set(new_nodes) - set(old_nodes)):
        out.append({"op": "add_node", "id": nid})
    for nid in sorted(set(old_nodes) - set(new_nodes)):
        out.append({"op": "detach_node", "id": nid})
    for nid in sorted(set(old_nodes) & set(new_nodes)):
        changed = [field for field in ("title", "summary", "statement", "state", "state_meta",
                                       "basis_refs", "disposition_meta", "lineage",
                                       "identity_key")
                   if canonical(old_nodes[nid].get(field)) != canonical(new_nodes[nid].get(field))]
        if changed:
            out.append({"op": "patch_fields", "id": nid, "fields": changed})
        old_parent, new_parent = parent_of(old, nid), parent_of(new, nid)
        old_index = child_index(old, old_parent, nid) if old_parent else None
        new_index = child_index(new, new_parent, nid) if new_parent else None
        if (old_parent, old_index) != (new_parent, new_index):
            out.append({"op": "move_node", "id": nid, "new_parent": new_parent,
                        "index": new_index})
    for role in ROLES:
        before = (old.get("role_assignments") or {}).get(role)
        after = (new.get("role_assignments") or {}).get(role)
        if before != after:
            out.append({"op": "set_role", "role": role, "id": after})
    if canonical(old.get("meta")) != canonical(new.get("meta")):
        keys = sorted(set(old.get("meta") or {}) | set(new.get("meta") or {}))
        out.append({"op": "patch_meta", "fields": [k for k in keys
                                                   if (old.get("meta") or {}).get(k)
                                                   != (new.get("meta") or {}).get(k)]})
    return out


def resolve_commitish(control: Path, value: str) -> str:
    if read_ref(control, value):
        return read_ref(control, value)
    if (commits_dir(control) / f"{value}.json").exists():
        return value
    matches = [p.stem for p in commits_dir(control).glob(f"{value}*.json")]
    if len(matches) == 1:
        return matches[0]
    refuse("COMMITISH_UNRESOLVED", f"{value!r} matches {len(matches)} commits")


def cmd_log(args) -> int:
    control = ros.locate_control(Path(args.root))
    require_store(control)
    chain = commit_chain(control, args.ref)
    rows = []
    for commit in reversed(chain):
        row = {"id": commit["id"], "short": commit["id"][:12], "at": commit["at"],
               "kind": commit["kind"], "cause": commit["cause"],
               "forced_status": commit["forced_status"], "flags": commit.get("flags") or [],
               "message": commit["message"], "author": commit.get("author"),
               "origin": commit.get("origin"), "trigger_class": commit.get("trigger_class")}
        if args.graph:
            row["parents"] = [p[:12] for p in commit.get("parents") or []]
        rows.append(row)
    return emit({"ok": True, "ref": args.ref, "head": read_ref(control, args.ref),
                 "count": len(rows), "commits": rows})


def cmd_diff(args) -> int:
    control = ros.locate_control(Path(args.root))
    require_store(control)
    left = resolve_commitish(control, args.a)
    right = resolve_commitish(control, args.b)
    diff = snapshot_diff(commit_snapshot(control, left), commit_snapshot(control, right))
    return emit({"ok": True, "a": left[:12], "b": right[:12], "count": len(diff), "diff": diff})


def cmd_blame(args) -> int:
    control = ros.locate_control(Path(args.root))
    require_store(control)
    rows = []
    for commit in commit_chain(control, args.ref):
        if args.node in _commit_touched_ids(commit):
            rows.append({"commit": commit["id"][:12], "at": commit["at"], "kind": commit["kind"],
                         "cause": commit["cause"], "author": commit.get("author"),
                         "message": commit["message"]})
    return emit({"ok": True, "node": args.node, "count": len(rows), "commits": rows})


def cmd_revert(args) -> int:
    """§3.3: revert replays the PERSISTED inverse_ops -- it never re-derives them."""
    control = ros.locate_control(Path(args.root))
    require_store(control)
    commit_id = resolve_commitish(control, args.commit)
    commit = load_commit(control, commit_id)
    ops = commit.get("inverse_ops") or []
    if not ops:
        ros.die(f"commit {commit_id[:12]} carries no inverse_ops", 1)
    # §3.3: a committed node is never physically deleted.  The stored inverse of
    # an `add_node` is `delete_uncommitted`, which is legal only inside the
    # uncommitted batch that created it; landing a revert turns it into an
    # explicit, disposed detach so the audit trail keeps the node.
    ops = [op if op.get("op") != "delete_uncommitted" else
           {"op": "detach_node", "id": op["id"], "narrative_disposition": "DROPPED",
            "disposition_meta": {"target": None,
                                 "reason": f"revert of commit {commit_id[:12]}",
                                 "basis_refs": []}}
           for op in ops]
    if not args.commit_now:
        return emit({"ok": True, "reverts": commit_id, "ops": ops,
                     "note": "pass --commit-now --meta-file to land this revert"})
    meta = ros.read_json_file(Path(args.meta_file), {}) if args.meta_file else {}
    meta.setdefault("message", f"revert {commit_id[:12]}: {commit['message'][:60]}")
    result = commit_from_ops(control, args.ref, ops, meta,
                             turn_uuid=args.turn_uuid, manifest_id=args.manifest_id)
    result["reverts"] = commit_id
    return emit(result)


# ============================================================ §14  node op builders

def parse_kv(pairs, what: str = "value") -> dict:
    """`key=value` pairs; the value is parsed as JSON when it looks like JSON,
    so `conditions=["a","b"]` and `polarity=positive` both work."""
    out = {}
    for item in pairs or []:
        if "=" not in item:
            ros.die(f"--{what} expects key=value, got {item!r}", 2)
        key, _, raw = item.partition("=")
        raw = raw.strip()
        try:
            out[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            out[key.strip()] = raw
    return out


def emit_ops(args, ops: list, control: Path, ref: str) -> int:
    """Every `node …` subcommand only GENERATES and VALIDATES ops (§6.1);
    it never writes the authoritative layer."""
    snapshot = load_head_snapshot(control, ref)
    preview, _, _ = apply_ops(snapshot, ops, control)
    errors = validate_snapshot(preview)
    payload = {"ok": not errors, "ref": ref, "ops": ops,
               "would_be_tree": tree_hash(preview), "validation_errors": errors}
    if getattr(args, "out", None):
        ros.atomic_write(Path(args.out), dump_json({"ops": ops}))
        payload["ops_file"] = str(Path(args.out))
    emit(payload)
    return 0 if not errors else 1


def cmd_node(args) -> int:
    control = ros.locate_control(Path(args.root))
    require_store(control)
    ref = args.ref
    action = args.action
    if action == "add":
        if args.node_file:
            node = ros.read_json_file(Path(args.node_file), {})
        else:
            node = {"id": args.id, "node_type": args.node_type,
                    "identity_key": parse_kv(args.identity, "identity"),
                    "title": args.title or "", "summary": args.summary or "",
                    "statement": args.statement or "",
                    "state": {"epistemic": args.epistemic, "narrative": args.narrative},
                    "state_meta": {"resolution_condition": args.resolution_condition,
                                   "live_bet": bool(args.live_bet)},
                    "basis_refs": list(args.basis or []),
                    "lineage": [{"rel": item.split(":", 1)[0], "of": item.split(":", 1)[1]}
                                for item in (args.lineage or []) if ":" in item]}
        ops = [{"op": "add_node", "node": node, "parent": args.parent, "index": args.index}]
    elif action == "patch":
        ops = [{"op": "patch_fields", "id": args.id, "fields": parse_kv(args.set, "set")}]
    elif action == "patch-meta":
        ops = [{"op": "patch_meta", "fields": parse_kv(args.set, "set")}]
    elif action == "move":
        ops = [{"op": "move_node", "id": args.id, "new_parent": args.new_parent,
                "index": args.index}]
    elif action == "detach":
        ops = [{"op": "detach_node", "id": args.id,
                "narrative_disposition": args.disposition, "epistemic": args.epistemic,
                "disposition_meta": {"target": args.target, "reason": args.reason or "",
                                     "basis_refs": list(args.basis or [])}}]
    elif action == "split":
        ops = [{"op": "split_node", "id": args.id, "mode": args.mode,
                "parts": ros.read_json_file(Path(args.parts_file), []),
                "origin_disposition": ros.read_json_file(Path(args.origin_disposition_file), {})
                if args.origin_disposition_file else None}]
    elif action == "merge":
        into = ros.read_json_file(Path(args.into_file), {}) if args.into_file else args.into_id
        ops = [{"op": "merge_nodes", "ids": list(args.ids or []), "into": into,
                "reason": args.reason or ""}]
    elif action == "set-role":
        ops = [{"op": "set_role", "role": args.role, "id": None if args.clear else args.id}]
    elif action == "set-root":
        ops = [{"op": "set_root", "new_root_id": args.new_root_id}]
    elif action == "rekey-identity":
        ops = [{"op": "rekey_identity", "id": args.id, "old_key_hash": args.old_key_hash,
                "new_identity_key": parse_kv(args.identity, "identity"),
                "relation": args.relation, "basis_ref": args.basis_ref,
                "approval_ref": args.approval_ref}]
    else:
        ros.die(f"unknown node action {action!r}", 2)
    return emit_ops(args, ops, control, ref)


def cmd_commit(args) -> int:
    control = ros.locate_control(Path(args.root))
    require_store(control)
    if args.now:
        set_now(args.now)
    ops = []
    if args.ops_file:
        payload = ros.read_json_file(Path(args.ops_file), None)
        if payload is None:
            ros.die(f"ops file not found: {args.ops_file}", 2)
        ops = payload.get("ops") if isinstance(payload, dict) else payload
    elif not args.pending_patch:
        ros.die("commit needs --ops-file or --pending-patch", 2)
    meta = ros.read_json_file(Path(args.meta_file), {}) if args.meta_file else {}
    if args.message:
        meta["message"] = args.message
    result = commit_from_ops(control, args.ref, ops or [], meta,
                             pending_patch_id=args.pending_patch,
                             turn_uuid=args.turn_uuid, manifest_id=args.manifest_id)
    return emit(result)


# ============================================================ §13  NarrativePatch round-trip

def cmd_apply(args) -> int:
    """`apply` produces a PREVIEW and a one-shot pending_patch_id; it is not a
    commit and it never does a three-way merge (§13)."""
    control = ros.locate_control(Path(args.root))
    require_store(control)
    patch = ros.read_json_file(Path(args.patch), None)
    if patch is None:
        ros.die(f"patch file not found: {args.patch}", 2)
    if patch.get("schema") != PATCH_SCHEMA:
        ros.die(f"patch schema must be {PATCH_SCHEMA!r}, got {patch.get('schema')!r}", 1)
    ref = patch.get("ref") or "main"
    head = read_ref(control, ref)
    if patch.get("base_commit_id") != head:
        ros.die(f"PATCH_BASE_STALE: patch is based on {patch.get('base_commit_id')} "
                f"but {ref} is at {head}", 1)
    snapshot = load_head_snapshot(control, ref)
    if patch.get("base_tree_hash") and patch["base_tree_hash"] != tree_hash(snapshot):
        ros.die("PATCH_BASE_TREE_MISMATCH: base_tree_hash does not match the head snapshot", 1)
    with narrative_lock(control):
        preview, _, _ = apply_ops(snapshot, patch.get("tree_ops") or [], control)
        errors = validate_snapshot(preview)
        if errors:
            ros.die("PATCH_WOULD_BE_INVALID: " + "; ".join(errors[:6]), 1)
        patch_id = "pp_" + sha256_text(canonical(patch) + head + now_iso())[:16]
        record = dict(patch, patch_id=patch_id, base_commit_id=head, consumed=False,
                      applied_at=now_iso(), preview_tree_hash=tree_hash(preview))
        run_transaction(control, f"apply-{patch_id}", [
            {"path": pending_dir(control) / f"{patch_id}.json", "text": dump_json(record)},
            refresh_preview(control, ref, head, preview),
        ], note="apply narrative patch")
    return emit({"ok": True, "pending_patch_id": patch_id, "ref": ref,
                 "preview": str(working_path(control, ref)),
                 "preview_tree_hash": tree_hash(preview),
                 "todo_proposals": patch.get("todo_proposals") or [],
                 "note": "commit --pending-patch <id> consumes this exactly once"})


# ============================================================ §10  annotations

def cmd_annotate(args) -> int:
    control = ros.locate_control(Path(args.root))
    require_store(control)
    directory = ndir(control) / "annotations"
    if args.action == "list":
        rows = []
        target = directory / args.node if args.node else directory
        paths = sorted(target.glob("*.json")) if args.node else sorted(directory.glob("*/*.json"))
        for path in paths:
            rows.append(ros.read_json_file(path, {}))
        return emit({"ok": True, "count": len(rows), "annotations": rows})
    commit_id = resolve_commitish(control, args.commit or args.ref)
    snapshot = commit_snapshot(control, commit_id)
    if args.node not in snapshot["nodes"]:
        ros.die(f"node {args.node!r} is not in the snapshot of commit {commit_id[:12]}", 1)
    annotation_id = args.id or f"a{uuid.uuid4().hex[:10]}"
    path = directory / args.node / f"{annotation_id}.json"
    if path.exists():
        # §10: annotations are never overwritten -- supersede instead.
        ros.die(f"annotation {annotation_id} already exists; append a superseding one", 1)
    body = {"schema": "auto-research/narrative-annotation-v1", "annotation_id": annotation_id,
            "node_id": args.node, "commit_id": commit_id, "kind": args.kind,
            "question": args.question, "answer": args.answer,
            "adjudicated": bool(args.answer or args.adjudicated),
            "by": args.by or (ros.current_session_id(control) or "unknown"),
            "at": now_iso(), "supersedes": args.supersedes}
    with narrative_lock(control):
        run_transaction(control, f"annotate-{annotation_id}",
                        [{"path": path, "text": dump_json(body)}], note="annotation")
    return emit({"ok": True, "annotation": body,
                 "trigger_ref": f"annotation:{args.node}/{annotation_id}"})


# ============================================================ §11  frozen tags

def cmd_tag(args) -> int:
    control = ros.locate_control(Path(args.root))
    require_store(control)
    directory = ndir(control) / "tags"
    if args.action == "list":
        return emit({"ok": True, "tags": [ros.read_json_file(p, {})
                                          for p in sorted(directory.glob("*.json"))]})
    name = args.name
    path = directory / f"{name}.json"
    if path.exists():
        # §11: names are neither reusable nor movable.  No CERTIFIED/STALE machine.
        ros.die(f"tag {name!r} already exists and tags never move", 1)
    commit_id = resolve_commitish(control, args.commit or args.ref)
    body = {"schema": "auto-research/narrative-tag-v1", "name": name, "commit_id": commit_id,
            "kind": args.kind, "by": args.by or (ros.current_session_id(control) or "unknown"),
            "at": now_iso(), "note": args.note or "", "approval": args.approval}
    with narrative_lock(control):
        run_transaction(control, f"tag-{name}", [{"path": path, "text": dump_json(body)}],
                        note=f"tag {name}")
    log_event(control, {"event": "narrative_tagged", "tag": name, "commit": commit_id,
                        "summary": args.note or args.kind})
    return emit({"ok": True, "tag": body})


# ============================================================ §14  text renders

def render_axis(snapshot: dict, depth: int) -> list:
    roles = {v: k for k, v in (snapshot.get("role_assignments") or {}).items() if v}
    lines = []

    def walk(nid: str, level: int) -> None:
        node = snapshot["nodes"][nid]
        state = node.get("state") or {}
        badge = f" [{roles[nid]}]" if nid in roles else ""
        flag = "" if state.get("narrative") == "ACTIVE" else f" <{state.get('narrative')}>"
        bet = " ?" if (node.get("state_meta") or {}).get("live_bet") else ""
        lines.append(f"{'  ' * level}{'└ ' if level else ''}{nid}{badge} "
                     f"({state.get('epistemic')}{flag}){bet} {node.get('title', '')}")
        if level + 1 >= depth:
            hidden = len(node.get("children") or [])
            if hidden:
                lines.append(f"{'  ' * (level + 1)}… +{hidden} deeper "
                             f"(rerun with --depth {depth + 2})")
            return
        for child in node.get("children", []):
            walk(child, level + 1)

    if snapshot.get("root"):
        walk(snapshot["root"], 0)
    return lines


def render_reader(snapshot: dict) -> list:
    roles = snapshot.get("role_assignments") or {}
    root = snapshot["nodes"].get(snapshot.get("root"), {})
    lines = [f"NORTH STAR  {root.get('statement') or root.get('title', '')}", ""]
    for role in ROLES:
        value = roles.get(role)
        if not value:
            lines.append(f"{role.upper():<14} (unassigned)")
            continue
        node = snapshot["nodes"][value]
        lines.append(f"{role.upper():<14} {value}  {node.get('title', '')}")
        if node.get("statement"):
            lines.append(f"{'':<14} {node['statement'][:100]}")
    return lines


def render_internal(snapshot: dict) -> list:
    lines = []
    for nid, node in sorted(snapshot["nodes"].items()):
        state = node.get("state") or {}
        lines.append(f"{nid} [{node['node_type']}] {state.get('epistemic')}/"
                     f"{state.get('narrative')} rev={rev_hash(node)[:10]} "
                     f"idk={identity_hash(node)[:10]}")
        lines.append(f"    title: {node.get('title', '')}")
        if node.get("basis_refs"):
            lines.append(f"    basis: {', '.join(node['basis_refs'])}")
        if node.get("lineage"):
            lines.append("    lineage: " + ", ".join(f"{e['rel']}->{e['of']}"
                                                     for e in node["lineage"]))
        if (node.get("disposition_meta") or {}).get("target"):
            lines.append(f"    disposition -> {node['disposition_meta']['target']}: "
                         f"{node['disposition_meta'].get('reason', '')}")
    return lines


def render_todo(control: Path, snapshot: dict, history: dict) -> list:
    lines = ["OPEN LOOPS"]
    for bet in live_bets(snapshot):
        lines.append(f"  ? {bet['node']} {bet['title']} — resolves when "
                     f"{bet['resolution_condition']}")
    debts = lever_debt(history)
    if debts:
        lines.append("LEVER DEBT ⚠")
        for debt in debts:
            lines.append(f"  ⚠ {debt['node']} {debt['title']} (was "
                         f"{'/'.join(debt['roles_held'])}; {debt['cause']} demotion; "
                         f"still HELD; no replacement)")
    losses = lost_levers(history)
    if losses:
        lines.append("LOST LEVERS ⚠")
        for loss in losses:
            lines.append(f"  ⚠ {loss['node']} {loss['title']} (was "
                         f"{'/'.join(loss['roles_held'])}; dropped unadjudicated)")
    branches = [b for b in branch_report(control) if b["unresolved"]]
    if branches:
        lines.append("UNRESOLVED BRANCHES")
        for branch in branches:
            lines.append(f"  · {branch['branch']} — OPEN, not in main, no active K##")
    unclosed = unresolved_nodes(history)
    if unclosed:
        lines.append("DROPPED WITHOUT ADJUDICATION")
        for row in unclosed:
            lines.append(f"  · {row['node']} {row['title']} — {row['reason'] or '(no reason)'}")
    return lines


def render_tiers(snapshot: dict) -> list:
    roles = snapshot.get("role_assignments") or {}
    assigned = {v: k for k, v in roles.items() if v}
    root = snapshot.get("root")
    top = snapshot["nodes"].get(root, {}).get("children", []) if root else []
    lines = ["TIER 0 · root", f"  {root} {snapshot['nodes'].get(root, {}).get('title', '')}",
             "TIER 1 · roles"]
    for role in ROLES:
        value = roles.get(role)
        lines.append(f"  {role}: {value or '(none)'} "
                     f"{snapshot['nodes'].get(value, {}).get('title', '') if value else ''}")
    lines.append("TIER 2 · load-bearing (direct children of root)")
    for nid in top:
        lines.append(f"  {nid} {snapshot['nodes'][nid].get('title', '')}"
                     + (f"  [{assigned[nid]}]" if nid in assigned else ""))
    rest = [n for n in sorted(snapshot["nodes"]) if n != root and n not in top]
    lines.append(f"TIER 3 · supporting ({len(rest)})")
    for nid in rest:
        lines.append(f"  {nid} {snapshot['nodes'][nid].get('title', '')}")
    return lines


def _optional_module(name: str, what: str):
    """Load a sibling module that another track owns.  A thin forward must fail
    loudly and specifically (exit 2, environment error) when the module is not
    installed -- never silently, and never by improvising the feature here."""
    try:
        module = __import__(name)
    except ImportError as exc:
        ros.die(f"{what} lives in {name}.py, which is not installed next to "
                f"narrative.py ({exc}). Install/complete that module, or drop the "
                f"flag that routes here.", 2)
    return sys.modules[name]


def _forward_render_html(control: Path, args) -> int:
    """`render --view axis --html [--out]` -> narrative_render_html (U track).

    narrative.py keeps owning the authoritative + text layers; HTML is a derived
    view, so the forward hands over control/ref/out and nothing else."""
    if args.view != "axis":
        ros.die(f"--html is only defined for --view axis (got {args.view!r})", 2)
    module = _optional_module("narrative_render_html", "HTML rendering")
    render = getattr(module, "render_axis_html", None)
    if not callable(render):
        ros.die("narrative_render_html.py is present but exposes no "
                "render_axis_html(control, ref, out)", 2)
    out = Path(args.out) if args.out else None
    result = render(control, args.ref, out)
    if isinstance(result, dict):
        return emit(dict({"ok": True, "view": "axis", "html": True}, **result))
    if out is None and isinstance(result, str):
        sys.stdout.write(result if result.endswith(chr(10)) else result + chr(10))
        return 0
    return emit({"ok": True, "view": "axis", "html": True,
                 "out": str(out) if out else None})


def _backfill_control(argv: list) -> Path:
    """`--project <root>` out of the forwarded argv, defaulting to the cwd."""
    for index, token in enumerate(argv):
        if token == "--project" and index + 1 < len(argv):
            return ros.locate_control(Path(argv[index + 1]))
        if token.startswith("--project="):
            return ros.locate_control(Path(token.split("=", 1)[1]))
    return ros.locate_control(Path("."))


def forward_backfill(argv: list) -> int:
    """`narrative backfill ...` -> narrative_backfill (D5 track), argv verbatim.

    Intercepted before argparse: everything after `backfill` belongs to the
    other module's own CLI, and argparse would reject its flags as unknown.

    v3 §2: a backfill rewrites a whole history at once, so the graph is derived
    ONCE at the end, over every rule -- not per replayed commit.  The derivation
    is hung on the forwarded module's own `emit`, which it calls after the last
    write, so the host keeps emitting exactly one JSON document.
    """
    module = _optional_module("narrative_backfill", "historical backfill (§12)")
    entry = getattr(module, "main", None)
    if not callable(entry):
        ros.die("narrative_backfill.py is present but exposes no main(argv)", 2)
    verb = "run" if (argv and argv[0].startswith("-")) else (argv[0] if argv else "")
    original = getattr(module, "emit", None)
    if verb not in ("run", "adjudicate") or not callable(original):
        return int(entry(list(argv)) or 0)

    def emit_with_graph(obj):
        if isinstance(obj, dict) and obj.get("ok") is not False:
            with ros.contextlib_suppress():
                graph = _graph_after(_backfill_control(argv), GRAPH_RULES_ALL,
                                     "narrative:backfill")
                if graph is not None:
                    obj = dict(obj, graph=graph)
        return original(obj)

    module.emit = emit_with_graph
    try:
        return int(entry(list(argv)) or 0)
    finally:
        module.emit = original


def cmd_backfill(args) -> int:
    return forward_backfill(list(getattr(args, "argv", None) or []))


def _forward_argv(args) -> list:
    """`<cmd> [root] rest…` -> ['--project', root, *rest]; a first token that
    is not a directory (e.g. `build`) is part of the forwarded argv."""
    argv = list(args.argv or [])
    root = Path(args.root)
    if str(args.root) != "." and not root.exists():
        argv = [str(args.root)] + argv
        root = Path(".")
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if "--project" not in argv:
        argv = ["--project", str(root)] + argv
    return argv


def cmd_literature(args) -> int:
    """`literature [root] build|link|report|status` -> literature_index.main(argv)."""
    module = _optional_module("literature_index", "the literature ledger")
    return int(module.main(_forward_argv(args)) or 0)


def cmd_audit(args) -> int:
    """`audit [root] …` -> narrative_audit.main(argv): is each node part of the story?"""
    module = _optional_module("narrative_audit", "the story-admission audit")
    return int(module.main(_forward_argv(args)) or 0)


def cmd_map(args) -> int:
    """`map [root] render|summary|export|import …` -> research_map.main(argv):
    what the AI did, on one map (changes / evidence / taste overlays)."""
    module = _optional_module("research_map", "the research map")
    return int(module.main(_forward_argv(args)) or 0)


def cmd_taste(args) -> int:
    """`taste [root] add|retire|list|harvest|accept|check …` -> taste_ledger.main(argv)."""
    module = _optional_module("taste_ledger", "the taste ledger")
    return int(module.main(_forward_argv(args)) or 0)


def cmd_serve(args) -> int:
    """`serve` -> narrative_serve.main: loopback page + in-place Q&A that lands
    as §10 annotations.  A thin forward; the module owns the behaviour."""
    control = ros.locate_control(Path(args.root))
    require_store(control)
    module = _optional_module("narrative_serve", "the live page")
    argv = ["--project", str(control), "--ref", args.ref, "--port", str(args.port)]
    if args.model:
        argv += ["--model", args.model]
    if args.no_open:
        argv.append("--no-open")
    return int(module.main(argv) or 0)


def cmd_render(args) -> int:
    control = ros.locate_control(Path(args.root))
    require_store(control)
    if args.html:
        return _forward_render_html(control, args)
    snapshot = load_head_snapshot(control, args.ref)
    if args.view == "axis":
        lines = render_axis(snapshot, args.depth or load_config(control).get("axis_depth")
                            or AXIS_DEFAULT_DEPTH)
    elif args.view == "reader":
        lines = render_reader(snapshot)
    elif args.view == "internal":
        lines = render_internal(snapshot)
    elif args.view == "todo":
        lines = render_todo(control, snapshot, derive_history(control, args.ref))
    else:
        lines = render_tiers(snapshot)
    text = "\n".join(lines) + "\n"
    if args.out:
        ros.atomic_write(Path(args.out), text)
        return emit({"ok": True, "view": args.view, "out": str(Path(args.out)),
                     "lines": len(lines)})
    print(text, end="")
    return 0


# ============================================================ validate / journal

def cmd_validate(args) -> int:
    control = ros.locate_control(Path(args.root))
    require_store(control)
    report = {"ok": True, "refs": {}, "branches": [], "problems": []}
    for ref, head in all_refs(control).items():
        try:
            snapshot = commit_snapshot(control, head)
        except NarrativeError as exc:
            report["problems"].append(f"{ref}: {exc}")
            report["ok"] = False
            continue
        errors = validate_snapshot(snapshot)
        report["refs"][ref] = {"head": head, "tree": tree_hash(snapshot),
                               "nodes": len(snapshot["nodes"]), "errors": errors}
        if errors:
            report["ok"] = False
            report["problems"].extend(f"{ref}: {e}" for e in errors)
        commit = load_commit(control, head)
        if commit["tree"] != tree_hash(snapshot):
            report["ok"] = False
            report["problems"].append(f"{ref}: stored tree hash != recomputed tree hash")
    registry = load_branch_registry(control)
    for name, entry in sorted(registry.get("branches", {}).items()):
        if not entry.get("disposition_history"):
            report["ok"] = False
            report["problems"].append(f"branch {name}: empty disposition_history "
                                      f"(create must append OPEN)")
        report["branches"].append({"branch": name, "disposition": branch_disposition(entry)})
    pending = [p.stem for p in pending_dir(control).glob("*.json")
               if not ros.read_json_file(p, {}).get("consumed")]
    report["pending_patches"] = pending
    unfinished = [p.stem for p in journal_dir(control).glob("*.json")
                  if ros.read_json_file(p, {}).get("status") != "done"]
    if unfinished:
        report["ok"] = False
        report["problems"].append(f"unfinished journal transactions: {unfinished} "
                                  f"(run `narrative journal replay`)")
    emit(report)
    return 0 if report["ok"] else 1


def cmd_journal(args) -> int:
    control = ros.locate_control(Path(args.root))
    require_store(control)
    with narrative_lock(control):
        result = journal_replay(control)
        ros.atomic_write(index_path(control), dump_json(rebuild_index(control)))
    return emit(result)


# ============================================================ self-test (§1.2, §15, §17)

def _quiet(argv) -> "tuple":
    """Run the CLI with stdout captured -- self-test asserts on exit codes."""
    buffer, saved, saved_err = io.StringIO(), sys.stdout, sys.stderr
    sys.stdout, sys.stderr = buffer, io.StringIO()
    try:
        code = main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    finally:
        text = buffer.getvalue()
        sys.stdout, sys.stderr = saved, saved_err
    return code, text


def _project(tmp: Path, name: str) -> Path:
    control = tmp / name / ".research-os"
    control.mkdir(parents=True, exist_ok=True)
    return control


def _init(control: Path, title: str, at: str, venue: str = "RFS") -> dict:
    args = argparse.Namespace(root=control.parent, root_id="N-0", title=title,
                              thesis=title, summary="", venue=venue, message=None,
                              session="W-test", at=at, turn_uuid=None, manifest_id=None)
    buffer, saved = io.StringIO(), sys.stdout
    sys.stdout = buffer
    try:
        cmd_init(args)
    finally:
        sys.stdout = saved
    return json.loads(buffer.getvalue())


def _claim(nid: str, title: str, statement: str = "", **kw) -> dict:
    return {"id": nid, "node_type": kw.get("node_type", "claim"),
            "identity_key": kw.get("identity_key", {
                "subject": kw.get("subject", nid), "target": kw.get("target", "returns"),
                "predicate": kw.get("predicate", f"predicate-of-{nid}"),
                "quantifier": "all", "domain": kw.get("domain", "US equities"),
                "conditions": kw.get("conditions", []), "polarity": "positive"}),
            "title": title, "summary": kw.get("summary", ""),
            "statement": statement or title,
            "state": {"epistemic": kw.get("epistemic", "PENDING"),
                      "narrative": kw.get("narrative", "ACTIVE")},
            "state_meta": {"resolution_condition": kw.get("resolution_condition"),
                           "live_bet": kw.get("live_bet", False)},
            "basis_refs": kw.get("basis_refs", []),
            "lineage": kw.get("lineage", [])}


def _narrative_node(nid: str, title: str) -> dict:
    return {"id": nid, "node_type": "narrative",
            "identity_key": {"role_hint": nid, "thesis": title},
            "title": title, "statement": title,
            "state": {"epistemic": "PENDING", "narrative": "ACTIVE"}}


def _decision(control: Path, slug: str, at: str, touches=()) -> str:
    ros.atomic_write(decisions_dir(control) / f"{slug}.json",
                     dump_json({"user_approved": True, "at": at, "touches": list(touches),
                                "note": "self-test fixture decision record"}))
    return f"decision:{slug}"


def _commit(control: Path, ref: str, ops: list, at: str, message: str, **meta) -> dict:
    payload = {"message": message, "at": at,
               "author": meta.pop("author", {"session": "W-test", "role": "chronicler"})}
    payload.update(meta)
    return commit_from_ops(control, ref, ops, payload)


class _GraphStub:
    """Stands in for research_graph so the CALL SITE is what is under test, not
    the graph.  Records every derive() the host makes."""

    def __init__(self, boom: bool = False):
        self.calls, self.boom = [], boom

    def derive(self, control, rules=None, reason="manual", since=None):
        self.calls.append({"control": Path(control), "rules": list(rules or []),
                           "reason": reason})
        if self.boom:
            raise RuntimeError("derive exploded on purpose")
        return {"added": 3, "retracted": 1}


def _with_graph_stub(stub):
    """Install the stub under the name `_graph_after` imports, and hand back a
    restore callable."""
    saved = sys.modules.get("research_graph")
    sys.modules["research_graph"] = stub
    def restore():
        if saved is None:
            sys.modules.pop("research_graph", None)
        else:
            sys.modules["research_graph"] = saved
    return restore


def _test_graph_derive(tmp: Path):
    """v3 contract §2: a landed commit derives the graph, and a graph that
    fails does NOT unland the commit."""
    control = _project(tmp, "v3-graph")
    _init(control, "v3 derive", "2026-08-25T00:00:00Z")

    stub = _GraphStub()
    restore = _with_graph_stub(stub)
    try:
        result = _commit(control, "main",
                         [{"op": "add_node", "node": _claim("C-1", "first claim"),
                           "parent": "N-0", "index": None}],
                         "2026-08-25T01:00:00Z", "add C-1")
    finally:
        restore()
    assert result["ok"] and result.get("commit_id"), "the commit itself must still land"
    assert result.get("graph") == {"derived": True, "added": 3, "retracted": 1},         f"commit payload carries no graph block: {result.get('graph')!r}"
    assert len(stub.calls) == 1, f"derive called {len(stub.calls)} time(s), expected once"
    call = stub.calls[0]
    assert call["rules"] == list(GRAPH_RULES_COMMIT), call["rules"]
    assert call["reason"] == "narrative:commit", call["reason"]
    assert call["control"] == Path(control), call["control"]
    yield ("commit -> derive(rules=%s, reason=%r); payload carries graph{derived,added,"
           "retracted}" % (list(GRAPH_RULES_COMMIT), "narrative:commit"))

    boom = _GraphStub(boom=True)
    restore = _with_graph_stub(boom)
    try:
        second = _commit(control, "main",
                         [{"op": "add_node", "node": _claim("C-2", "second claim"),
                           "parent": "N-0", "index": None}],
                         "2026-08-25T02:00:00Z", "add C-2")
    finally:
        restore()
    assert second["ok"] and second.get("commit_id"),         "a failing derive must not take the commit down with it"
    assert second["graph"]["derived"] is False and second["graph"]["error"], second["graph"]
    log = control / "graph" / "incidents.log"
    assert log.exists(), "a failed derivation must leave an incident line"
    line = log.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert line.startswith("GRAPH_DERIVE_FAILED narrative:commit:"), line
    yield ("a raising derive leaves the commit landed, reports derived=false and writes "
           "one GRAPH_DERIVE_FAILED line to graph/incidents.log")


def _fingerprint(control: Path) -> str:
    parts = []
    for sub in ("refs", "objects", "commits", "working"):
        base = ndir(control) / sub
        for path in sorted(base.rglob("*")):
            if path.is_file():
                parts.append(f"{path.relative_to(ndir(control)).as_posix()}="
                             f"{sha256_text(path.read_text(encoding='utf-8'))}")
    return sha256_text("\n".join(parts))


# ---------------------------------------------------------------- §1.2 identity cases

def _test_identity_cases() -> list:
    out = []
    # case 1: "the hurdle is not a constant" -> "the hurdle is a functional of the
    # procedure".  predicate moved => new id, lineage replaces|narrows of C-041.
    old = {"subject": "hurdle", "target": "t-statistic", "predicate": "is not a constant",
           "quantifier": "all", "domain": "published anomalies", "conditions": [],
           "polarity": "positive"}
    new = dict(old, predicate="is a functional of the search procedure")
    verdict = identity_diff(old, new, "claim")
    assert verdict["verdict"] == "new_id", verdict
    assert verdict["changed_fields"] == ["predicate"], verdict
    child = _claim("C-042", "functional hurdle", predicate=new["predicate"],
                   lineage=[{"rel": "replaces", "of": "C-041"}])
    assert child["lineage"][0]["rel"] in ("replaces", "narrows")
    out.append("case1 predicate change -> new id + replaces lineage")

    # case 2: one node becomes two.
    structural = split_plan("structural")
    semantic = split_plan("semantic")
    assert structural["origin_kept_in_tree"] and structural["origin_statement_unchanged"]
    assert not structural["requires_origin_disposition"]
    assert semantic["origin_disposed"] and semantic["requires_origin_disposition"]
    assert not semantic["parts_are_children_of_origin"]
    for plan in (structural, semantic):
        assert plan["parts_inherit_lineage"] and not plan["parts_inherit_identity"]
    out.append("case2 structural vs semantic split plans")

    # case 3: the evidence under a claim is replaced.
    claim_key = {"subject": "search", "target": "hurdle", "predicate": "raises",
                 "quantifier": "all", "domain": "US equities", "conditions": [],
                 "polarity": "positive"}
    ev_old = {"estimand": "t-hurdle", "population": "CRSP", "sample_window": "1963-2020",
              "method": "bootstrap", "artifact_ref": "run-1"}
    ev_new = dict(ev_old, method="permutation", artifact_ref="run-2")
    same = evidence_swap_verdict(claim_key, claim_key, ev_old, ev_new)
    assert same["claim_verdict"] == "same_id" and same["rev_hash_changes"], same
    ev_moved = dict(ev_old, population="Compustat", sample_window="1990-2020")
    claim_moved = dict(claim_key, domain="global equities")
    shifted = evidence_swap_verdict(claim_key, claim_moved, ev_old, ev_moved)
    assert shifted["claim_verdict"] == "new_id", shifted
    out.append("case3 evidence swap: same id unless target/domain/conditions moved")
    return out


# ---------------------------------------------------------------- §15 assertion 1

def _test_assertion_1(tmp: Path) -> list:
    control = _project(tmp, "a1")
    genesis = _init(control, "search is priced", "2026-01-01T00:00:00Z")
    a = _commit(control, "main",
                [{"op": "add_node", "node": _claim("C-001", "hurdle claim", "v1 statement"),
                  "parent": "N-0"}],
                "2026-01-02T00:00:00Z", "add C-001")
    rev_before = rev_hash(commit_snapshot(control, a["commit_id"])["nodes"]["C-001"])
    b = _commit(control, "main",
                [{"op": "patch_fields", "id": "C-001", "fields": {"statement": "v2 statement"}}],
                "2026-01-03T00:00:00Z", "refine C-001 statement")
    assert read_ref(control, "main") == b["commit_id"], "head must be B"
    assert b["kind"] == "refine", b
    snapshot_b = commit_snapshot(control, b["commit_id"])
    assert snapshot_b["nodes"]["C-001"]["statement"] == "v2 statement"
    # the current tree comes from the COMMIT, never from the preview
    preview = ros.read_json_file(working_path(control, "main"), {})
    assert preview["base_commit_id"] == b["commit_id"]
    assert tree_hash(snapshot_b) == load_commit(control, b["commit_id"])["tree"]
    diff = snapshot_diff(commit_snapshot(control, a["commit_id"]), snapshot_b)
    assert diff == [{"op": "patch_fields", "id": "C-001", "fields": ["statement"]}], diff
    assert snapshot_b["nodes"]["C-001"]["id"] == "C-001"
    assert rev_hash(snapshot_b["nodes"]["C-001"]) != rev_before, "rev_hash must move"
    assert not validate_snapshot(snapshot_b)
    # hash stability: serialise the same fixture twice
    assert tree_hash(snapshot_b) == tree_hash(json.loads(canonical(snapshot_b)))
    commit_b = load_commit(control, b["commit_id"])
    recomputed = sha_of({k: v for k, v in commit_b.items() if k != "id"})
    assert recomputed == commit_b["id"], "commit id must be a hash of its own body"
    assert genesis["kind"] in KINDS
    return ["assertion 1: refine keeps id, moves rev_hash, node-level diff of exactly one op",
            "hash stability: two serialisations of one fixture agree"]


# ---------------------------------------------------------------- §15 assertion 3

def _test_assertion_3(tmp: Path) -> list:
    control = _project(tmp, "a3")
    _init(control, "root thesis", "2026-01-01T00:00:00Z")
    _commit(control, "main",
            [{"op": "add_node", "node": _claim("C-006", "candidate root"), "parent": "N-0"}],
            "2026-01-02T00:00:00Z", "add C-006")
    before = _fingerprint(control)
    refused = 0
    for _ in range(3):
        try:
            _commit(control, "main", [{"op": "set_root", "new_root_id": "C-006"}],
                    "2026-01-03T00:00:00Z", "replace the root")
        except NarrativeError as exc:
            assert exc.code == "OVERTHROW_REQUIRES_USER_APPROVAL", exc.code
            refused += 1
    assert refused == 3, "the gate must refuse every time"
    assert _fingerprint(control) == before, "refs / snapshots / preview must be untouched"
    code, _ = _quiet(["commit", str(control.parent), "--ref", "main",
                      "--ops-file", str(tmp / "missing.json")])
    assert code == 2
    approval = _decision(control, "root-replacement-approved", "2026-01-03T00:00:00Z")
    landed = _commit(control, "main", [{"op": "set_root", "new_root_id": "C-006"}],
                     "2026-01-04T00:00:00Z", "replace the root (approved)",
                     approval_refs={"overthrow": approval, "forced_cause": None})
    assert landed["kind"] == "overthrow", landed
    assert commit_snapshot(control, landed["commit_id"])["root"] == "C-006"
    assert _fingerprint(control) != before
    return ["assertion 3: unapproved set_root refused 3x with zero side effects; "
            "approved retry lands kind=overthrow"]


# ---------------------------------------------------------------- §15 assertion 2

def _test_assertion_2(tmp: Path) -> list:
    control = _project(tmp, "a2")
    _init(control, "branch discipline", "2026-01-01T00:00:00Z")
    _commit(control, "main",
            [{"op": "add_node", "node": _claim("C-001", "trunk headline"), "parent": "N-0"},
             {"op": "set_role", "role": "headline", "id": "C-001"}],
            "2026-01-02T00:00:00Z", "trunk headline")
    code, out = _quiet(["branch", "create", str(control.parent), "alt-headline",
                        "--from", "main"])
    assert code == 0, out
    assert read_ref(control, "alt-headline") == read_ref(control, "main")
    registry = load_branch_registry(control)
    assert branch_disposition(registry["branches"]["alt-headline"]) == "OPEN"
    assert len(registry["branches"]["alt-headline"]["disposition_history"]) == 1
    _commit(control, "alt-headline",
            [{"op": "add_node", "node": _claim("C-050", "alternative headline"), "parent": "N-0"},
             {"op": "set_role", "role": "headline", "id": "C-050"}],
            "2026-01-05T00:00:00Z", "alt story restructure")
    assert active_k_refs(control, "alt-headline") == []
    seen = set()
    for day in ("2026-01-20", "2026-02-20", "2026-03-20", "2026-04-06"):
        set_now(f"{day}T00:00:00Z")
        report = {b["branch"]: b for b in branch_report(control)}
        seen.add(report["alt-headline"]["disposition"])
        assert report["alt-headline"]["unresolved"] is True
        lines = trajectory_lines(control)
        assert any("UNRESOLVED_BRANCH" in line for line in lines), lines
        assert len(lines) <= 40, len(lines)
    set_now(None)
    assert seen == {"OPEN"}, seen
    assert all(event["disposition"] != "DROPPED"
               for event in load_branch_registry(control)["branches"]["alt-headline"]
               ["disposition_history"]), "the system must never auto-drop"
    return ["assertion 2: branch stays OPEN across 90 days, UNRESOLVED_BRANCH keeps printing, "
            "zero automatic DROPPED"]


# ---------------------------------------------------------------- validator rejections

def _test_validator(tmp: Path) -> list:
    control = _project(tmp, "val")
    _init(control, "validator", "2026-01-01T00:00:00Z")
    base = load_head_snapshot(control, "main")

    illegal = json.loads(canonical(base))
    illegal["nodes"]["C-001"] = _claim("C-001", "impossible", epistemic="KILLED",
                                       narrative="ACTIVE", basis_refs=["refutation:x"])
    illegal["nodes"]["N-0"]["children"].append("C-001")
    assert any("illegal state" in e for e in validate_snapshot(illegal))

    orphan = json.loads(canonical(base))
    orphan["nodes"]["N-0"]["children"].append("C-404")
    assert any("orphan reference" in e for e in validate_snapshot(orphan))

    stranded = json.loads(canonical(base))
    stranded["nodes"]["C-777"] = _claim("C-777", "floating")
    assert any("unreachable from root" in e for e in validate_snapshot(stranded))

    duplicate = json.loads(canonical(base))
    duplicate["nodes"]["C-001"] = _claim("C-001", "child")
    duplicate["nodes"]["C-002"] = _claim("C-002", "second parent")
    duplicate["nodes"]["N-0"]["children"] = ["C-001", "C-002"]
    duplicate["nodes"]["C-002"]["children"] = ["C-001"]
    assert any("parents" in e for e in validate_snapshot(duplicate))

    demoted = json.loads(canonical(base))
    demoted["nodes"]["C-003"] = _claim("C-003", "demoted lever", epistemic="HELD",
                                       narrative="DEMOTED", basis_refs=["evidence:E-1"])
    demoted["nodes"]["C-003"]["disposition_meta"] = {"target": "appendix"}
    demoted["nodes"]["N-0"]["children"].append("C-003")
    demoted["role_assignments"]["flagship"] = "C-003"
    assert any("not ACTIVE" in e for e in validate_snapshot(demoted))

    killed_no_basis = json.loads(canonical(base))
    killed_no_basis["nodes"]["C-004"] = _claim("C-004", "killed", epistemic="KILLED",
                                               narrative="DROPPED")
    killed_no_basis["nodes"]["N-0"]["children"].append("C-004")
    assert any("KILLED without a qualifying basis" in e
               for e in validate_snapshot(killed_no_basis))

    _commit(control, "main",
            [{"op": "add_node", "node": _claim("C-010", "lever", epistemic="HELD",
                                               narrative="ACTIVE", basis_refs=["evidence:E-1"]),
              "parent": "N-0"}],
            "2026-01-02T00:00:00Z", "add lever")
    _commit(control, "main",
            [{"op": "patch_fields", "id": "C-010",
              "fields": {"state": {"narrative": "DEMOTED"},
                         "disposition_meta": {"target": "appendix"}}}],
            "2026-01-03T00:00:00Z", "demote")
    try:
        _commit(control, "main",
                [{"op": "detach_node", "id": "C-010", "narrative_disposition": "DROPPED",
                  "disposition_meta": {"reason": "tidy up"}}],
                "2026-01-04T00:00:00Z", "detach a demoted node")
        raise AssertionError("detaching a DEMOTED node must be refused")
    except NarrativeError as exc:
        assert exc.code == "DEMOTED_NODE_NOT_DETACHABLE", exc.code
    try:
        _commit(control, "main",
                [{"op": "patch_fields", "id": "C-010", "fields": {"identity_key": {}}}],
                "2026-01-05T00:00:00Z", "sneak an identity change through patch_fields")
        raise AssertionError("patch_fields must never touch identity_key")
    except NarrativeError as exc:
        assert exc.code == "PATCH_FIELD_FORBIDDEN", exc.code
    try:
        assert_cause_matrix("forced", "candidate")
        raise AssertionError("the iron law must hold")
    except NarrativeError as exc:
        assert exc.code == "CAUSE_MATRIX_VIOLATION"
    return ["validator: illegal state / orphan / stranded / duplicate parent / DEMOTED role / "
            "KILLED without basis all rejected",
            "ops: DEMOTED not detachable; patch_fields cannot move identity_key; "
            "cause matrix iron law enforced"]


# ---------------------------------------------------------------- §17 seven rounds

def _test_seven_rounds(tmp: Path) -> list:
    control = _project(tmp, "q1-rounds")
    _init(control, "the price of search", "2026-01-05T00:00:00Z")
    notes = []

    _commit(control, "main", [
        {"op": "add_node", "node": _claim("C-001", "hurdle headline", epistemic="HELD",
                                          basis_refs=["evidence:E-004"]), "parent": "N-0"},
        {"op": "add_node", "node": _claim("C-002", "flagship theorem claim", epistemic="HELD",
                                          basis_refs=["evidence:E-004"]), "parent": "N-0"},
        {"op": "add_node", "node": _claim("F-003", "backbone", node_type="theorem",
                                          identity_key={"formal_hash": "h-f003",
                                                        "hypotheses": ["A1"],
                                                        "conclusion": "bound"},
                                          epistemic="HELD", basis_refs=["theorem-failure:none"]),
         "parent": "N-0"},
        {"op": "add_node", "node": _claim("E-004", "empirical core", node_type="evidence",
                                          identity_key={"estimand": "t-hurdle",
                                                        "population": "CRSP",
                                                        "sample_window": "1963-2020",
                                                        "method": "bootstrap",
                                                        "artifact_ref": "run-1"},
                                          epistemic="HELD", basis_refs=["evidence:run-1"]),
         "parent": "N-0"},
        {"op": "add_node", "node": _narrative_node("N-9", "appendix"), "parent": "N-0"},
        {"op": "set_role", "role": "headline", "id": "C-001"},
        {"op": "set_role", "role": "flagship", "id": "C-002"},
        {"op": "set_role", "role": "backbone", "id": "F-003"},
        {"op": "set_role", "role": "empirical_core", "id": "E-004"},
    ], "2026-01-05T01:00:00Z", "set the opening story")

    # ---- round 1: ordinary refinement
    r1 = _commit(control, "main",
                 [{"op": "patch_fields", "id": "C-001",
                   "fields": {"statement": "sharper phrasing of the hurdle claim"}}],
                 "2026-01-06T00:00:00Z", "round 1: refine wording")
    assert (r1["kind"], r1["cause"]) == ("refine", "none"), r1
    notes.append("R1 refine/none")

    # ---- round 2: a cold review that does NOT move the tree
    set_now("2026-01-08T00:00:00Z")
    path = write_receipt(control, "T-r2", "im_r2", "NARRATIVE_REVIEWED_NO_CHANGE",
                         {"reason": "cold review logged as an annotation; story unchanged",
                          "commit_ids": []})
    body = ros.read_json_file(Path(path), {})
    assert body.get("kind") == "NARRATIVE_REVIEWED_NO_CHANGE", body
    assert not body.get("commit_ids"), body
    head_before = read_ref(control, "main")
    # ...but creating a live bet in response IS a tree change, so it must commit
    r2 = _commit(control, "main",
                 [{"op": "add_node",
                   "node": _claim("C-005", "live bet raised by the review", live_bet=True,
                                  resolution_condition="pilot grid returns by 2026-03-01"),
                   "parent": "C-002"}],
                 "2026-01-08T01:00:00Z", "round 2: register the reviewer's live bet")
    assert (r2["kind"], r2["cause"]) == ("refine", "none"), r2
    assert read_ref(control, "main") != head_before
    notes.append("R2 no-change receipt, then a live-bet refine commit")

    # ---- round 3: reactive headline swap under review pressure
    r3 = _commit(control, "main", [
        {"op": "add_node", "node": _claim("C-006", "new headline", epistemic="HELD",
                                          basis_refs=["evidence:E-004"]), "parent": "N-0"},
        {"op": "add_node", "node": _claim("C-007", "new flagship", epistemic="HELD",
                                          basis_refs=["evidence:E-004"]), "parent": "N-0"},
        {"op": "add_node", "node": _claim("F-008", "new backbone", node_type="theorem",
                                          identity_key={"formal_hash": "h-f008",
                                                        "hypotheses": ["A2"],
                                                        "conclusion": "sharper bound"},
                                          epistemic="HELD", basis_refs=["evidence:E-004"]),
         "parent": "N-0"},
        {"op": "set_role", "role": "headline", "id": "C-006"},
        {"op": "set_role", "role": "flagship", "id": "C-007"},
        {"op": "set_role", "role": "backbone", "id": "F-008"},
    ], "2026-01-15T00:00:00Z", "round 3: reactive restructure after the cold review",
        trigger_class="review-pressure")
    assert r3["kind"] == "restructure" and "LARGE_RESTRUCTURE" in r3["flags"], r3
    assert (r3["cause"], r3["forced_status"]) == ("unforced", "none"), r3
    notes.append("R3 restructure/unforced + LARGE_RESTRUCTURE (3 role changes)")

    # ---- round 4: a still-standing flagship is demoted to the appendix
    r4 = _commit(control, "main", [
        {"op": "set_role", "role": "flagship", "id": None},
        {"op": "patch_fields", "id": "C-007",
         "fields": {"state": {"narrative": "DEMOTED"},
                    "disposition_meta": {"target": "N-9",
                                         "reason": "moved to the appendix for space"}}},
        {"op": "move_node", "id": "C-007", "new_parent": "N-9"},
    ], "2026-01-20T00:00:00Z", "round 4: demote the flagship to the appendix",
        trigger_class="user-taste")
    assert r4["kind"] == "restructure" and r4["cause"] == "unforced", r4
    history = derive_history(control, "main")
    debts = lever_debt(history)
    assert [d["node"] for d in debts] == ["C-007"], debts
    assert any("LEVER_DEBT" in line for line in trajectory_lines(control)), "round 4 must flag it"
    notes.append("R4 LEVER_DEBT raised the same round")

    # ---- round 5: three weeks of pure refinement; the debt must persist
    for day, text in (("2026-01-25", "polish A"), ("2026-02-01", "polish B"),
                      ("2026-02-08", "polish C")):
        result = _commit(control, "main",
                         [{"op": "patch_fields", "id": "C-006",
                           "fields": {"summary": text}}],
                         f"{day}T00:00:00Z", f"round 5: {text}")
        assert result["kind"] == "refine", result
    set_now("2026-02-10T00:00:00Z")
    assert [d["node"] for d in lever_debt(derive_history(control, "main"))] == ["C-007"]
    notes.append("R5 three refines: stability clock does not reset the debt")

    # ---- round 6: the demoted flagship is rescued
    r6 = _commit(control, "main", [
        {"op": "patch_fields", "id": "C-007",
         "fields": {"state": {"narrative": "ACTIVE"}, "disposition_meta": {}}},
        {"op": "move_node", "id": "C-007", "new_parent": "N-0"},
        {"op": "set_role", "role": "flagship", "id": "C-007"},
    ], "2026-02-25T00:00:00Z", "round 6: rescue the flagship")
    assert r6["kind"] == "restructure", r6
    history = derive_history(control, "main")
    restored = [e for e in history["restored"] if e["node"] == "C-007"]
    assert restored and restored[0]["prior_role"] == "flagship", history["restored"]
    reversals = [e for e in history["role_reversals"] if e["node"] == "C-007"]
    assert reversals and reversals[0]["days"] <= ROLE_REVERSAL_DAYS, history["role_reversals"]
    assert not lever_debt(history), "a rescued lever clears its debt"
    notes.append(f"R6 RESTORED + ROLE_REVERSAL after {reversals[0]['days']}d (<= 60)")

    # ---- round 7: another cold review closes through the same two paths
    set_now("2026-03-05T00:00:00Z")
    head_before = read_ref(control, "main")
    write_receipt(control, "T-r7", "im_r7", "NARRATIVE_REVIEWED_NO_CHANGE",
                  {"reason": "second cold review, story unchanged", "commit_ids": []})
    assert read_ref(control, "main") == head_before, "a no-change review must not commit"
    lines = trajectory_lines(control)
    assert len(lines) <= 40, len(lines)
    assert any("RESTORED" in line for line in lines)
    assert any("ROLE_REVERSAL" in line for line in lines)
    set_now(None)
    notes.append("R7 closes on the round-2 path; trajectory <= 40 lines")
    return ["§17 seven rounds: " + "; ".join(notes)]


# ---------------------------------------------------------------- §15 assertion 5

def _test_assertion_5(tmp: Path) -> list:
    control = _project(tmp, "q1-fixture")
    _init(control, "Fixture: a seven-round story", "2026-05-01T00:00:00Z")
    _commit(control, "main", [
        {"op": "add_node", "node": _claim("C-001", "old headline: hurdle is a constant",
                                          epistemic="HELD", basis_refs=["evidence:E-004"]),
         "parent": "N-0"},
        {"op": "add_node", "node": _claim("C-002", "flagship: functional hurdle",
                                          epistemic="HELD", basis_refs=["evidence:E-004"]),
         "parent": "N-0"},
        {"op": "add_node", "node": _claim("F-003", "backbone theorem", node_type="theorem",
                                          identity_key={"formal_hash": "h3",
                                                        "hypotheses": ["A1"],
                                                        "conclusion": "k_eff bound"},
                                          epistemic="HELD", basis_refs=["evidence:E-004"]),
         "parent": "N-0"},
        {"op": "add_node", "node": _claim("E-004", "empirical core", node_type="evidence",
                                          identity_key={"estimand": "t-hurdle",
                                                        "population": "CRSP",
                                                        "sample_window": "1963-2020",
                                                        "method": "grid",
                                                        "artifact_ref": "run-1"},
                                          epistemic="HELD", basis_refs=["evidence:run-1"]),
         "parent": "N-0"},
        {"op": "add_node", "node": _narrative_node("N-9", "appendix"), "parent": "N-0"},
        {"op": "set_role", "role": "headline", "id": "C-001"},
        {"op": "set_role", "role": "flagship", "id": "C-002"},
        {"op": "set_role", "role": "backbone", "id": "F-003"},
        {"op": "set_role", "role": "empirical_core", "id": "E-004"},
    ], "2026-05-02T00:00:00Z", "opening the story")

    # ---- a null result forces a restructure (confirmed, counterfactual written)
    approval = _decision(control, "q1-null-result-forced", "2026-05-31T00:00:00Z",
                         touches=["F-003"])
    forced = _commit(control, "main", [
        {"op": "add_node", "node": _claim("F-009", "replacement backbone", node_type="theorem",
                                          identity_key={"formal_hash": "h9",
                                                        "hypotheses": ["A2"],
                                                        "conclusion": "weaker bound"},
                                          epistemic="HELD", basis_refs=["evidence:E-004"]),
         "parent": "N-0"},
        {"op": "set_role", "role": "backbone", "id": "F-009"},
        {"op": "detach_node", "id": "F-003", "narrative_disposition": "DROPPED",
         "epistemic": "KILLED",
         "disposition_meta": {"target": "F-009", "reason": "null result on the pilot grid",
                              "basis_refs": ["null-result:E-004"]}},
    ], "2026-06-01T00:00:00Z", "null result kills the backbone",
        trigger_class="null-result", trigger_ref="decision:q1-null-result-forced",
        counterfactual="without the null result the original backbone would have stayed",
        approval_refs={"overthrow": None, "forced_cause": approval})
    assert (forced["kind"], forced["cause"], forced["forced_status"]) == \
        ("restructure", "forced", "confirmed"), forced

    # ---- review pressure swaps the headline; the old one is dropped unadjudicated
    unforced = _commit(control, "main", [
        {"op": "add_node", "node": _claim("C-010", "new headline: search is priced",
                                          epistemic="HELD", basis_refs=["evidence:E-004"]),
         "parent": "N-0"},
        {"op": "set_role", "role": "headline", "id": "C-010"},
        {"op": "detach_node", "id": "C-001", "narrative_disposition": "DROPPED",
         "disposition_meta": {"target": "C-010",
                              "reason": "reactive swap after the cold review",
                              "basis_refs": []}},
    ], "2026-06-15T00:00:00Z", "reactive headline swap", trigger_class="review-pressure")
    assert (unforced["kind"], unforced["cause"], unforced["forced_status"]) == \
        ("restructure", "unforced", "none"), unforced

    # ---- the still-held flagship is demoted to the appendix
    _commit(control, "main", [
        {"op": "set_role", "role": "flagship", "id": None},
        {"op": "patch_fields", "id": "C-002",
         "fields": {"state": {"narrative": "DEMOTED"},
                    "disposition_meta": {"target": "N-9", "reason": "page budget"}}},
        {"op": "move_node", "id": "C-002", "new_parent": "N-9"},
    ], "2026-07-01T00:00:00Z", "demote the flagship", trigger_class="scope")

    # ---- an open live bet
    _commit(control, "main", [
        {"op": "add_node",
         "node": _claim("C-012", "bet: bit-exact replication clears the gate", live_bet=True,
                        resolution_condition="data access lands by 2026-09-01"),
         "parent": "C-010"}],
        "2026-07-10T00:00:00Z", "register the live bet")

    # ---- an unresolved side line, and a backfill chain with a blind adjudication
    assert _quiet(["branch", "create", str(control.parent), "alt-headline",
                   "--from", "main"])[0] == 0
    _commit(control, "alt-headline", [
        {"op": "add_node", "node": _claim("C-020", "alternative headline"), "parent": "N-0"}],
        "2026-07-12T00:00:00Z", "explore an alternative")
    assert _quiet(["branch", "create", str(control.parent), "backfill-v1",
                   "--from", "main"])[0] == 0
    backfill_approval = _decision(control, "q1-backfill-0811", "2026-08-11T00:00:00Z",
                                  touches=["C-030"])
    b1 = _commit(control, "backfill-v1", [
        {"op": "add_node", "node": _claim("C-030", "historical section 4.3.2"),
         "parent": "N-0"}],
        "2026-08-11T00:00:00Z", "backfill S_1 -> S_2", origin="backfill",
        author={"session": "backfill", "role": "backfill"})
    assert b1["origin"] if "origin" in b1 else True
    assert load_commit(control, b1["commit_id"])["origin"] == "backfill"
    assert load_commit(control, b1["commit_id"])["kind"] in ("refine", "restructure", "overthrow")
    assert (b1["cause"], b1["forced_status"]) == ("unknown", "none"), b1
    b2 = _commit(control, "backfill-v1", [
        {"op": "add_node", "node": _claim("C-031", "historical section 4.3.3"),
         "parent": "N-0"},
        {"op": "set_role", "role": "foil", "id": "C-031"}],
        "2026-08-12T00:00:00Z", "backfill S_2 -> S_3", origin="backfill",
        author={"session": "backfill", "role": "backfill"},
        blind_decision={"cause": "forced"}, approval_ref=backfill_approval,
        approval_refs={"overthrow": None, "forced_cause": backfill_approval},
        counterfactual="the 0812 edit batch would not have happened without the referee note")
    assert (b2["cause"], b2["forced_status"]) == ("forced", "confirmed"), b2
    adjudication = {"frame_id": "S_2", "before_commit": b1["commit_id"],
                    "after_commit_candidate": b2["commit_id"],
                    "blind_decision": {"cause": "forced", "forced_status": "confirmed"},
                    "approval_ref": backfill_approval, "retrospective_notes": [],
                    "source_hashes": {"before_tree": load_commit(control, b1["commit_id"])["tree"],
                                      "after_tree": load_commit(control, b2["commit_id"])["tree"]}}
    ros.atomic_write(ndir(control) / "backfill" / "adjudications" / "S_2.json",
                     dump_json(adjudication))
    stored = ros.read_json_file(ndir(control) / "backfill" / "adjudications" / "S_2.json", {})
    assert stored["source_hashes"]["after_tree"] == \
        load_commit(control, stored["after_commit_candidate"])["tree"], "DAG must agree"

    # ---- readings
    set_now("2026-07-20T00:00:00Z")
    history = derive_history(control, "main")
    snapshot = history["head_snapshot"]
    assert snapshot["root"] == "N-0"
    assert (snapshot["role_assignments"] or {})["headline"] == "C-010"
    assert [d["node"] for d in lever_debt(history)] == ["C-002"], lever_debt(history)
    assert [l["node"] for l in lost_levers(history)] == ["C-001"], lost_levers(history)
    assert [b["node"] for b in live_bets(snapshot)] == ["C-012"], live_bets(snapshot)
    card = derive_card(control)
    assert card["source_commit_id"] == read_ref(control, "main")
    assert card["narrative_part"]["roles"]["headline"]["node"] == "C-010"
    assert len(card_markdown(card).splitlines()) <= CARD_MD_MAX
    assert len(card_markdown(card, brief=True).splitlines()) <= CARD_BRIEF_MAX

    lines = trajectory_lines(control)
    text = "\n".join(lines)
    assert len(lines) <= 40, f"trajectory is {len(lines)} lines"
    for needle in ("forced/confirmed", "null-result", "review-pressure",
                   "LEVER_DEBT", "LOST_LEVER", "UNRESOLVED_BRANCH", "C-012"):
        assert needle in text, f"trajectory is missing {needle}:\n{text}"
    assert "C-010" in text and "N-0" in text
    set_now(None)
    return ["assertion 5: seven-round fixture yields forced/null-result, unforced/review-pressure, "
            "LEVER_DEBT, LOST_LEVER, a live bet, an unresolved branch, a backfill chain and a "
            "blind adjudication consistent with the DAG; trajectory <= 40 lines"]


# ---------------------------------------------------------------- transaction discipline

def _test_transaction(tmp: Path) -> list:
    control = _project(tmp, "tx")
    _init(control, "transactions", "2026-01-01T00:00:00Z")
    ops = [{"op": "add_node", "node": _claim("C-001", "first"), "parent": "N-0"}]
    first = commit_from_ops(control, "main", ops,
                            {"message": "add", "at": "2026-01-02T00:00:00Z"},
                            turn_uuid="T1", manifest_id="im_1")
    assert first["receipt_path"] and Path(first["receipt_path"]).exists()
    # replaying the same turn+manifest must NOT create a second commit
    again = commit_from_ops(control, "main", ops,
                            {"message": "add", "at": "2026-01-02T00:00:00Z"},
                            turn_uuid="T1", manifest_id="im_1")
    assert again["status"] == "ALREADY_COMMITTED_RECEIPT_REPAIRED", again
    assert again["commit_id"] == first["commit_id"]
    assert len(commit_chain(control, "main")) == 2, "genesis + one commit"

    # simulate a crash: the receipt never landed, the journal never completed
    receipt = receipt_path_for(control, "T1", "im_1")
    receipt.unlink()
    journal = next(path for path in sorted(journal_dir(control).glob("commit-*.json"))
                   if (ros.read_json_file(path, {}).get("receipt_call") or {})
                   .get("turn_uuid") == "T1")
    entry = ros.read_json_file(journal, {})
    entry["status"] = "begin"
    ros.atomic_write(journal, dump_json(entry))
    result = journal_replay(control)
    assert result["count"] >= 1 and receipt.exists(), result
    assert len(commit_chain(control, "main")) == 2, "replay must not duplicate the commit"

    # §13 patch round trip
    head = read_ref(control, "main")
    patch = {"schema": PATCH_SCHEMA, "project": str(control.parent), "ref": "main",
             "base_commit_id": head, "base_tree_hash": tree_hash(load_head_snapshot(control)),
             "tree_ops": [{"op": "patch_fields", "id": "C-001",
                           "fields": {"title": "renamed by the UI"}}],
             "todo_proposals": ["K07"], "generated_at": now_iso()}
    patch_file = tmp / "patch.json"
    ros.atomic_write(patch_file, dump_json(patch))
    code, out = _quiet(["apply", str(control.parent), "--patch", str(patch_file)])
    assert code == 0, out
    patch_id = json.loads(out)["pending_patch_id"]
    landed = commit_from_ops(control, "main", [], {"message": "consume the patch",
                                                   "at": "2026-01-03T00:00:00Z"},
                            pending_patch_id=patch_id)
    assert landed["kind"] == "refine"
    assert load_head_snapshot(control)["nodes"]["C-001"]["title"] == "renamed by the UI"
    try:
        commit_from_ops(control, "main", [], {"message": "double spend"},
                        pending_patch_id=patch_id)
        raise AssertionError("a pending patch is one-shot")
    except NarrativeError as exc:
        assert exc.code in ("PENDING_PATCH_ALREADY_CONSUMED", "PENDING_PATCH_STALE"), exc.code

    # inverse ops are exact, and revert replays the persisted ones
    code, out = _quiet(["revert", str(control.parent), landed["commit_id"]])
    assert code == 0 and json.loads(out)["ops"], out
    return ["transaction: idempotency key, ALREADY_COMMITTED_RECEIPT_REPAIRED, journal replay "
            "after a lost receipt, one-shot pending patch"]


# ------------------------------------------------------- §3.3 non-leaf detach

def _test_detach_subtree(tmp: Path) -> list:
    """A hand-written detach of an INTERIOR node must be exactly invertible.

    Regression: the archived bodies kept their children[], so replaying the
    inverse re-inserted every parent->child edge a second time (DUPLICATE_CHILD)
    and the §6.1 transaction refused the commit with INVERSE_OPS_NOT_EXACT.  The
    backfiller worked around it by exploding a subtree into leaf-level detaches;
    it no longer has to.
    """
    control = _project(tmp, "detach-subtree")
    _init(control, "three level tree", "2026-02-01T00:00:00Z")
    _commit(control, "main", [
        {"op": "add_node", "node": _claim("C-101", "A: the branch that goes"), "parent": "N-0"},
        {"op": "add_node", "node": _claim("C-102", "B: first child of A"), "parent": "C-101"},
        {"op": "add_node", "node": _claim("C-103", "C: second child of A"), "parent": "C-101"},
        {"op": "add_node", "node": _claim("C-104", "D: grandchild under B"), "parent": "C-102"},
        {"op": "add_node", "node": _narrative_node("N-8", "sibling that stays"), "parent": "N-0"},
    ], "2026-02-02T00:00:00Z", "build A(B(D),C) next to a sibling")
    before = load_head_snapshot(control)
    before_hash = tree_hash(before)
    assert before["nodes"]["N-0"]["children"] == ["C-101", "N-8"], before["nodes"]["N-0"]

    # ---- the detach itself: one op, three levels, no leaf-by-leaf workaround
    detach = [{"op": "detach_node", "id": "C-101", "narrative_disposition": "DROPPED",
               "disposition_meta": {"reason": "the whole branch leaves the story",
                                    "basis_refs": ["review-finding-with-evidence:R-1"]}}]
    landed = _commit(control, "main", detach, "2026-02-03T00:00:00Z",
                     "detach the interior node A with its whole subtree")
    assert landed["kind"] == "restructure", landed
    after = load_head_snapshot(control)
    assert validate_snapshot(after) == [], validate_snapshot(after)
    assert set(after["nodes"]) == {"N-0", "N-8"}, sorted(after["nodes"])
    assert after["nodes"]["N-0"]["children"] == ["N-8"], after["nodes"]["N-0"]

    # ---- §4: the disposition is recorded on the TOP node only
    commit = load_commit(control, landed["commit_id"])
    records = _detach_records(commit)
    assert set(records) == {"C-101"}, records
    _, _, ctx = apply_ops(before, detach, control)
    cascade = {row["id"]: row for row in ctx.detached}
    assert cascade["C-101"]["disposition"] == "DROPPED"
    assert cascade["C-101"]["disposition_meta"]["reason"].startswith("the whole branch")
    for nid in ("C-102", "C-103", "C-104"):
        assert cascade[nid]["disposition"] is None, cascade[nid]
        assert cascade[nid]["disposition_meta"] == {"detached_with": "C-101"}, cascade[nid]

    # ---- the persisted inverse restores the subtree exactly (bodies AND order)
    inverse = commit["inverse_ops"]
    assert [op["node"]["id"] for op in inverse] == ["C-101", "C-102", "C-104", "C-103"], inverse
    assert all(op["node"]["children"] == [] for op in inverse), "archived bodies carry no edges"
    restored, _, _ = apply_ops(after, inverse, control, restore=True)
    assert tree_hash(restored) == before_hash, "inverse must reproduce the pre-detach tree hash"
    assert canonical(restored) == canonical(before), "inverse must reproduce the bodies too"

    # ---- and `revert` of that commit lands (it replays the persisted inverse)
    code, out = _quiet(["revert", str(control.parent), landed["commit_id"], "--commit-now",
                        "--now", "2026-02-04T00:00:00Z"])
    assert code == 0, out
    reverted = json.loads(out)
    assert reverted["ok"] and reverted["reverts"] == landed["commit_id"], reverted
    back = load_head_snapshot(control)
    assert validate_snapshot(back) == [], validate_snapshot(back)
    assert tree_hash(back) == before_hash, "revert must land the pre-detach tree"
    assert back["nodes"]["C-101"]["children"] == ["C-102", "C-103"], back["nodes"]["C-101"]
    assert back["nodes"]["N-0"]["children"] == ["C-101", "N-8"], back["nodes"]["N-0"]
    return ["non-leaf detach: A(B(D),C) leaves in one op; snapshot stays valid and reachable; "
            "inverse_ops replay to the exact pre-detach tree hash; `revert` lands it back",
            "non-leaf detach: DROPPED disposition + meta only on the top node, "
            "descendants recorded as detached_with"]


# ---------------------------------------------------------------- runner

def cmd_self_test(args) -> int:
    saved_now = _NOW_OVERRIDE
    tmp = Path(tempfile.mkdtemp(prefix="narrative-selftest-"))
    passed, failed = [], []
    suites = [
        ("§1.2 identity adjudication", lambda: _test_identity_cases()),
        ("§15 assertion 1 + hash stability", lambda: _test_assertion_1(tmp)),
        ("§15 assertion 2 (branch never auto-dropped)", lambda: _test_assertion_2(tmp)),
        ("§15 assertion 3 (overthrow gate)", lambda: _test_assertion_3(tmp)),
        ("§2/§4 validator rejections", lambda: _test_validator(tmp)),
        ("§6.1 transaction discipline", lambda: _test_transaction(tmp)),
        ("§3.3 non-leaf detach", lambda: _test_detach_subtree(tmp)),
        ("§17 seven-round script", lambda: _test_seven_rounds(tmp)),
        ("§15 assertion 5 (seven-round fixture)", lambda: _test_assertion_5(tmp)),
        ("v3 §2 real-time derivation", lambda: _test_graph_derive(tmp)),
    ]
    for name, run in suites:
        try:
            for line in run():
                passed.append(f"{name}: {line}")
        except Exception as exc:  # noqa: BLE001 -- a self-test reports, it does not crash
            import traceback
            failed.append({"suite": name, "error": f"{type(exc).__name__}: {exc}",
                           "where": traceback.format_exc().strip().splitlines()[-3:]})
        finally:
            set_now(saved_now)
    if not args.keep:
        with ros.contextlib_suppress():
            shutil.rmtree(tmp, ignore_errors=True)
    report = {"ok": not failed, "passed": len(passed), "failed": len(failed),
              "checks": passed, "failures": failed}
    if args.keep:
        report["tmp"] = str(tmp)
    emit(report)
    return 0 if not failed else 1


# ============================================================ §14  CLI surface

def _turn_args(parser) -> None:
    parser.add_argument("--turn-uuid", dest="turn_uuid",
                        help="G-track turn id (default: detached)")
    parser.add_argument("--manifest-id", dest="manifest_id",
                        help="G-track InputManifest id (default: none)")
    parser.add_argument("--now", help="freeze the clock (tests / backfill)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="narrative.py",
        description="auto-research narrative time tree (contract docs/narrative-contract.md v1.2)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create the narrative store and its genesis commit")
    p.set_defaults(func=cmd_init)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("--root-id", dest="root_id", default="R-0")
    p.add_argument("--title", required=False, default=None)
    p.add_argument("--thesis")
    p.add_argument("--summary")
    p.add_argument("--venue")
    p.add_argument("--message")
    p.add_argument("--session")
    p.add_argument("--at")
    _turn_args(p)

    # ---- node: generate + validate ops only (never writes the authoritative layer)
    node_p = sub.add_parser("node", help="generate/validate tree ops (no writes)")
    node_p.set_defaults(func=cmd_node)
    nsub = node_p.add_subparsers(dest="action", required=True)

    def node_common(target):
        target.add_argument("root", type=Path, nargs="?", default=Path("."))
        target.add_argument("--ref", default="main")
        target.add_argument("--out", help="write {\"ops\": [...]} here for `commit --ops-file`")
        return target

    n = node_common(nsub.add_parser("add", help="add a node"))
    n.add_argument("--id")
    n.add_argument("--node-file", dest="node_file", help="full node JSON (alternative to flags)")
    n.add_argument("--node-type", dest="node_type", choices=NODE_TYPES)
    n.add_argument("--parent")
    n.add_argument("--index", type=int)
    n.add_argument("--title")
    n.add_argument("--summary")
    n.add_argument("--statement")
    n.add_argument("--identity", action="append", metavar="key=value")
    n.add_argument("--epistemic", choices=EPISTEMIC, default="PENDING")
    n.add_argument("--narrative", choices=NARRATIVE, default="ACTIVE")
    n.add_argument("--basis", action="append")
    n.add_argument("--live-bet", dest="live_bet", action="store_true")
    n.add_argument("--resolution-condition", dest="resolution_condition")
    n.add_argument("--lineage", action="append", metavar="rel:node_id")

    n = node_common(nsub.add_parser("patch", help="patch expression / state / basis fields"))
    n.add_argument("--id", required=True)
    n.add_argument("--set", action="append", metavar="field=json", required=True)

    n = node_common(nsub.add_parser("patch-meta", help="patch tree meta (venue => restructure)"))
    n.add_argument("--set", action="append", metavar="key=json", required=True)

    n = node_common(nsub.add_parser("move", help="re-parent a node"))
    n.add_argument("--id", required=True)
    n.add_argument("--new-parent", dest="new_parent", required=True)
    n.add_argument("--index", type=int)

    n = node_common(nsub.add_parser("detach", help="leave the tree with a disposition"))
    n.add_argument("--id", required=True)
    n.add_argument("--disposition", choices=DETACH_DISPOSITIONS, required=True)
    n.add_argument("--epistemic", choices=EPISTEMIC)
    n.add_argument("--target")
    n.add_argument("--reason")
    n.add_argument("--basis", action="append", metavar="kind:ref")

    n = node_common(nsub.add_parser("split", help="structural or semantic split"))
    n.add_argument("--id", required=True)
    n.add_argument("--mode", choices=("structural", "semantic"), required=True)
    n.add_argument("--parts-file", dest="parts_file", required=True)
    n.add_argument("--origin-disposition-file", dest="origin_disposition_file")

    n = node_common(nsub.add_parser("merge", help="merge nodes into one"))
    n.add_argument("--ids", action="append", required=True)
    n.add_argument("--into-id", dest="into_id")
    n.add_argument("--into-file", dest="into_file")
    n.add_argument("--reason")

    n = node_common(nsub.add_parser("set-role", help="assign one of the five roles"))
    n.add_argument("--role", choices=ROLES, required=True)
    n.add_argument("--id")
    n.add_argument("--clear", action="store_true")

    n = node_common(nsub.add_parser("set-root", help="re-root (fires the §3.4 gate)"))
    n.add_argument("--new-root-id", dest="new_root_id", required=True)

    n = node_common(nsub.add_parser("rekey-identity", help="the one approved identity exception"))
    n.add_argument("--id", required=True)
    n.add_argument("--old-key-hash", dest="old_key_hash", required=True)
    n.add_argument("--identity", action="append", metavar="key=value", required=True)
    n.add_argument("--relation", choices=LINEAGE_RELS, default="clarifies")
    n.add_argument("--basis-ref", dest="basis_ref")
    n.add_argument("--approval-ref", dest="approval_ref")

    p = sub.add_parser("commit", help="THE single atomic write path (§6.1)")
    p.set_defaults(func=cmd_commit)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("--ref", default="main")
    p.add_argument("--ops-file", dest="ops_file")
    p.add_argument("--pending-patch", dest="pending_patch")
    p.add_argument("--meta-file", dest="meta_file")
    p.add_argument("--message")
    _turn_args(p)

    br = sub.add_parser("branch", help="side lines (§5)")
    br.set_defaults(func=cmd_branch)
    bsub = br.add_subparsers(dest="action", required=True)
    b = bsub.add_parser("create", help="fork a ref (appends the first OPEN event)")
    b.add_argument("root", type=Path, nargs="?", default=Path("."))
    b.add_argument("name")
    b.add_argument("--from", dest="source", default="main", metavar="REF[@COMMIT]")
    b.add_argument("--reason")
    b.add_argument("--by")
    b = bsub.add_parser("promote", help="fast-forward, or a two-parent merge commit")
    b.add_argument("root", type=Path, nargs="?", default=Path("."))
    b.add_argument("name")
    b.add_argument("--into", default="main")
    b.add_argument("--message")
    b.add_argument("--reason")
    b.add_argument("--by")
    b.add_argument("--at")
    b.add_argument("--approval", help="decision ref, required if the roots differ (§3.4)")
    _turn_args(b)
    b = bsub.add_parser("kill", help="close a branch on a qualifying basis")
    b.add_argument("root", type=Path, nargs="?", default=Path("."))
    b.add_argument("name")
    b.add_argument("--basis", action="append", metavar="kind:ref", required=True)
    b.add_argument("--reason")
    b.add_argument("--by")
    b = bsub.add_parser("drop", help="abandon a branch EXPLICITLY (never automatic)")
    b.add_argument("root", type=Path, nargs="?", default=Path("."))
    b.add_argument("name")
    b.add_argument("--reason", required=True)
    b.add_argument("--by")
    b = bsub.add_parser("list", help="branches with disposition and UNRESOLVED_BRANCH")
    b.add_argument("root", type=Path, nargs="?", default=Path("."))

    p = sub.add_parser("log", help="commit log")
    p.set_defaults(func=cmd_log)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("--ref", default="main")
    p.add_argument("--graph", action="store_true")

    p = sub.add_parser("blame", help="which commits touched a node")
    p.set_defaults(func=cmd_blame)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("node")
    p.add_argument("--ref", default="main")

    p = sub.add_parser("diff", help="node-level diff between two commits (never line-level)")
    p.set_defaults(func=cmd_diff)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("a")
    p.add_argument("b")

    p = sub.add_parser("revert", help="replay a commit's persisted inverse_ops")
    p.set_defaults(func=cmd_revert)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("commit")
    p.add_argument("--ref", default="main")
    p.add_argument("--commit-now", dest="commit_now", action="store_true")
    p.add_argument("--meta-file", dest="meta_file")
    _turn_args(p)

    p = sub.add_parser("trajectory", help="<= 40 lines, fixed per-block budgets (§9)")
    p.set_defaults(func=cmd_trajectory)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("--window", type=int, default=30)
    p.add_argument("--now")

    p = sub.add_parser("render", help="text views (HTML lives in the U track)")
    p.set_defaults(func=cmd_render)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("--view", choices=("axis", "reader", "internal", "todo", "tiers"),
                   default="axis")
    p.add_argument("--ref", default="main")
    p.add_argument("--depth", type=int)
    p.add_argument("--out")
    p.add_argument("--html", action="store_true", help="not implemented here (U track)")

    p = sub.add_parser("card", help="derived narrative card (§8)")
    p.set_defaults(func=cmd_card)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("--md", action="store_true")
    p.add_argument("--brief", action="store_true", help="<= 40 lines for the main controller")
    p.add_argument("--now")

    p = sub.add_parser("annotate", help="append-only Q&A / notes / reviews (§10)")
    p.set_defaults(func=cmd_annotate)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("action", choices=("add", "list"))
    p.add_argument("--node")
    p.add_argument("--commit")
    p.add_argument("--ref", default="main")
    p.add_argument("--kind", choices=("qa", "note", "review"), default="note")
    p.add_argument("--question")
    p.add_argument("--answer")
    p.add_argument("--adjudicated", action="store_true")
    p.add_argument("--id")
    p.add_argument("--by")
    p.add_argument("--supersedes")

    p = sub.add_parser("tag", help="frozen / submitted tags (§11)")
    p.set_defaults(func=cmd_tag)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("action", choices=("freeze", "list"))
    p.add_argument("--name")
    p.add_argument("--kind", choices=("frozen", "submitted"), default="frozen")
    p.add_argument("--commit")
    p.add_argument("--ref", default="main")
    p.add_argument("--approval")
    p.add_argument("--note")
    p.add_argument("--by")

    p = sub.add_parser("apply", help="apply a NarrativePatch -> preview + pending_patch_id (§13)")
    p.set_defaults(func=cmd_apply)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("--patch", required=True)

    p = sub.add_parser("serve", help="live tree page with in-place Q&A (narrative_serve.py)")
    p.set_defaults(func=cmd_serve)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("--ref", default="main")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--model")
    p.add_argument("--no-open", dest="no_open", action="store_true")

    p = sub.add_parser("literature", help="literature ledger + tree links (literature_index.py)")
    p.set_defaults(func=cmd_literature)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("argv", nargs=argparse.REMAINDER,
                   help="build|link|report|status … passed to literature_index.main(argv)")

    p = sub.add_parser("map", help="research map: changes / evidence / taste (research_map.py)")
    p.set_defaults(func=cmd_map)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("argv", nargs=argparse.REMAINDER,
                   help="render|summary|export|import … passed to research_map.main(argv)")

    p = sub.add_parser("taste", help="the researcher's taste rules (taste_ledger.py)")
    p.set_defaults(func=cmd_taste)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("argv", nargs=argparse.REMAINDER,
                   help="add|retire|list|harvest|accept|check … passed to taste_ledger.main(argv)")

    p = sub.add_parser("audit", help="story-admission audit, two checkers (narrative_audit.py)")
    p.set_defaults(func=cmd_audit)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("argv", nargs=argparse.REMAINDER,
                   help="passed verbatim to narrative_audit.main(argv)")

    p = sub.add_parser("backfill", help="historical backfill of an existing project (§12; narrative_backfill.py)")
    p.set_defaults(func=cmd_backfill)
    p.add_argument("argv", nargs=argparse.REMAINDER,
                   help="passed verbatim to narrative_backfill.main(argv)")

    p = sub.add_parser("validate", help="validate every ref's head snapshot and the registry")
    p.set_defaults(func=cmd_validate)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))

    p = sub.add_parser("journal", help="finish transactions that never reached their marker")
    p.set_defaults(func=cmd_journal)
    p.add_argument("root", type=Path, nargs="?", default=Path("."))
    p.add_argument("action", choices=("replay",))

    p = sub.add_parser("self-test", help="run the contract's machine-checkable assertions")
    p.set_defaults(func=cmd_self_test)
    p.add_argument("--keep", action="store_true", help="keep the temp projects for inspection")
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "backfill" and "-h" not in raw[1:] and "--help" not in raw[1:]:
        # §14 thin forward: narrative_backfill owns its own flags.
        try:
            return forward_backfill(raw[1:])
        except NarrativeError as exc:
            print(json.dumps({"ok": False, "error": exc.code, "detail": exc.detail},
                             ensure_ascii=False), file=sys.stderr)
            return 1
    args = build_parser().parse_args(raw)
    try:
        return args.func(args)
    except NarrativeError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "detail": exc.detail},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
