#!/usr/bin/env python3
"""auto-research role fleet runtime (docs/role-fleet.md v1).

Five long-lived roles collaborate on one research graph through a star
topology: the main controller dispatches, a role proposes, and the CLI --
this file -- is the only thing that writes authoritative state.

What is mechanised here (and therefore cannot be fudged by a model):

  * the registry `.research-os/roles/registry.json` is CLI-only; a role never
    writes its own memory, cursor or receipt (contract 0.1 / 1);
  * every dispatch is registered before it leaves, so an unanswered dispatch
    shows up as DISPATCH_UNANSWERED at the next session start (contract 0.4);
  * one unanswered dispatch per role at a time -- a second one is refused with
    DISPATCH_PENDING (contract 6.3);
  * a receipt is validated (message id, idempotency key, per-kind schema)
    before anything is merged, and a second receipt for the same message id
    returns ALREADY_ANSWERED without merging twice (contract 8.2);
  * memory is merged, never overwritten, and refused above 12 KB (8.3);
  * kind=CHRONICLE_TURN does NOT get a second commit path: the receipt writes
    the proposed ops/meta to temp files outside the project and calls
    turn_runtime.turn_record_disposition, which calls the one narrative
    commit transaction (narrative-contract 6.1).

Two places where narrative-contract.md overrides the role-fleet sketch (the
role-fleet preamble says the narrative contract wins on conflict):

  * CHRONICLE_TURN keeps the chronicler envelope of narrative-contract 7 --
    same message_id, same idempotency_key sha256(project|turn_uuid|
    manifest_id|chronicler) -- because narrative.commit_from_ops memoises on
    exactly that key. The role-fleet key sha256(project|role|kind|turn_uuid|
    manifest_id|k_task) rides along as `role_idempotency_key`;
  * the memory_delta of a CHRONICLE_TURN reply is merged here and is NOT
    forwarded inside the narrative commit meta, so it is merged once, with
    the list/dict semantics of role-fleet 4, not twice.

stdlib-only, Python >= 3.9. Exit codes: 0 ok, 1 contract refusal, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros  # noqa: E402


def _turn_runtime_module():
    """Bind to the ONE turn_runtime object. When turn_runtime.py is the entry
    point it lives in sys.modules as __main__; a plain `import turn_runtime`
    would then load a second copy with its own GateError class, and every
    `except GateError` across the seam would miss."""
    main = sys.modules.get("__main__")
    if Path(getattr(main, "__file__", "") or "").name == "turn_runtime.py":
        sys.modules.setdefault("turn_runtime", main)
        return sys.modules["turn_runtime"]
    import turn_runtime  # noqa: F401
    return sys.modules["turn_runtime"]


tr = _turn_runtime_module()

GateError = tr.GateError
NEWLINE = chr(10)

SCHEMA_REGISTRY = "auto-research/role-registry-v1"
SCHEMA_ENVELOPE = "ROLE_TASK/v1"
SCHEMA_RECEIPT = "auto-research/role-receipt-v1"

HOT_DISPATCH_DAYS = 7            # 1: dispatched within 7 days => hot
MEMORY_STALE_DAYS = 30           # 4: memory untouched for 30 days => stale
BUSY_GRACE_HOURS = 24            # unanswered longer than this reads as stale
MEMORY_MAX_BYTES = tr.MEMORY_MAX_BYTES        # 12 KB (contract 1 / 4)
ENVELOPE_MAX_BYTES = tr.ENVELOPE_MAX_BYTES    # 8 KiB (narrative-contract 7)
HYDRATE_MAX_LINES = tr.HYDRATE_MAX_LINES      # 40 (contract 7)
MEMORY_DIGEST_MAX_LINES = 40                  # 3: inputs.memory_digest budget
MAX_REFS = 16                                 # 3: inputs.refs budget
GRAPH_CONTEXT_MAX_BYTES = 4096
DISPATCH_HISTORY_MAX = 24

RECEIPT_STATUSES = ("ANSWERED", "ANSWERED_NO_CHANGE", "REJECTED")
ROLE_STATUSES = ("dormant", "hydrated", "busy", "stale")
KIND_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
K_TASK_RE = re.compile(r"^K[0-9]{1,4}$")

GENERIC_KIND = {
    "required": [],
    "change_fields": [],
    "objective": ("Answer the question inside the envelope from the refs and your memory. "
                  "Return flags[] and memory_delta{} and nothing else you were not asked for."),
}

# 2: the first five roles. Adjudicator-style agents stay one-shot and are
# deliberately absent from the registry.
ROLES = {
    "chronicler": {
        "title": "chronicler / 史官",
        "profile": "agents/research-chronicler.md",
        "duty": "narrative tree ops proposals; narrative card; drift flags",
        "kinds": {
            "CHRONICLE_TURN": {
                "required": ["proposed_ops", "commit_meta", "flags", "memory_delta"],
                "change_fields": ["proposed_ops", "proposed_edges"],
                "objective": ("Decide whether this input delta changes the narrative tree. "
                              "Return proposed_ops[], commit_meta{proposed_kind, "
                              "cause_proposal, trigger_ref, trigger_class, counterfactual?, "
                              "message}, flags[], memory_delta -- nothing else. You propose; "
                              "the CLI writes."),
            },
        },
    },
    "figure-engineer": {
        "title": "figure-engineer / 图形工程师",
        "profile": "agents/research-figure-engineer.md",
        "duty": "figure request routing, template/component choice, rendered QA readings",
        "kinds": {
            "FIGURE_REQUEST": {
                "required": ["proposed_figure_requests", "routes", "qa_findings", "memory_delta"],
                "change_fields": ["proposed_figure_requests", "routes", "qa_findings", "proposed_edges"],
                "objective": ("Route this figure work. Return proposed_figure_requests[], "
                              "routes[], qa_findings[], memory_delta -- proposals only; the "
                              "CLI and the artifacts skill do the writing."),
            },
        },
    },
    "evidence-steward": {
        "title": "evidence-steward / 证据管家",
        "profile": "agents/research-evidence-steward.md",
        "duty": "literature/citation/data-provenance ledger, novelty collisions, UNVERIFIED marks",
        "kinds": {
            "EVIDENCE_AUDIT": {
                "required": ["ledger_ops", "citation_verdicts", "collisions", "memory_delta"],
                "change_fields": ["ledger_ops", "citation_verdicts", "collisions", "proposed_edges"],
                "objective": ("Audit the evidence base for this delta. Return ledger_ops[], "
                              "citation_verdicts[], collisions[], memory_delta -- proposals "
                              "only. Never mark a source verified you did not open."),
            },
        },
    },
    "experiment-runner": {
        "title": "experiment-runner / 实验管家",
        "profile": "agents/research-experiment-runner.md",
        "duty": "run registration and reconciliation, result -> evidence node proposals",
        "kinds": {
            "RUN_RECONCILE": {
                "required": ["run_ops", "evidence_proposals", "reproduce_requests", "memory_delta"],
                "change_fields": ["run_ops", "evidence_proposals", "reproduce_requests", "proposed_edges"],
                "objective": ("Reconcile the runs behind this delta. Return run_ops[], "
                              "evidence_proposals[], reproduce_requests[], memory_delta -- "
                              "proposals only; a number with no run archive is a finding, "
                              "not a result."),
            },
        },
    },
    "theory-operator": {
        "title": "theory-operator / 理论操作员",
        "profile": "agents/research-theory-operator.md",
        "duty": "siege ledger state, claim five-state proposals, Pro dispatch packet drafts",
        "kinds": {
            "CLAIM_UPDATE": {
                "required": ["claim_ops", "siege_next", "transfer_candidates", "memory_delta"],
                "change_fields": ["claim_ops", "siege_next", "transfer_candidates", "proposed_edges"],
                "objective": ("Update the siege ledger for this delta. Return claim_ops[], "
                              "siege_next[], transfer_candidates[], memory_delta -- proposals "
                              "only. A claim is PROVED or REFUTED only with a checkable "
                              "artifact."),
            },
        },
    },
}
ROLE_ORDER = list(ROLES)

CONSTRAINTS = ("<=30 lines of JSON. You propose; the CLI writes. Never write project state, "
               "never contact another role (a cross-role need is a K task for the main "
               "controller), never fabricate a receipt, a digest or a citation.")


# ---------------------------------------------------------------- layout

def _graph_module():
    """The graph is a bypass layer: if it is not installed, a receipt still
    lands and the proposals are reported instead of silently vanishing."""
    main = sys.modules.get("__main__")
    if Path(getattr(main, "__file__", "") or "").name == "research_graph.py":
        sys.modules.setdefault("research_graph", main)
        return sys.modules["research_graph"]
    import research_graph  # noqa: F401
    return sys.modules["research_graph"]


def pending_edges_summary(control: Path) -> dict:
    """graph-contract 5 / R9 B8.  ``edges`` is the ACTIONABLE count: a park that
    has been retried to exhaustion (``STALE``) or refused under the current
    authorisation policy (``DENIED``) is kept for audit and dropped from every
    nag line, so the session digest reports work someone can actually do."""
    try:
        summary = _graph_module().pending_summary(control)
    except BaseException:
        return {"files": 0, "edges": 0, "actionable": 0, "total": 0,
                "by_role": {}, "by_status": {}}
    summary.setdefault("actionable", summary.get("edges", 0))
    summary.setdefault("total", summary.get("edges", 0))
    summary.setdefault("by_status", {})
    return summary


def _dispose_proposed_edges(control: Path, role: str, message_id: str, proposals) -> dict:
    """graph-contract 5 / R9 B7, from inside the receipt: link what resolves,
    park what does not, refuse what the role does not own.

    ``role`` is the role of the DISPATCH this receipt answers -- read from the
    registry, never from the reply body.  That is the whole point of R9 B7: an
    agent cannot widen its own edge authority by claiming to be someone else,
    because it never gets to name itself here."""
    if not proposals:
        return {"proposed": 0, "linked": [], "pending": [], "denied": [], "invalid": [],
                "pending_file": None, "flags": []}
    try:
        graph = _graph_module()
    except BaseException as exc:
        return {"proposed": len(proposals) if isinstance(proposals, list) else 0,
                "linked": [], "pending": [], "denied": [], "invalid": [],
                "pending_file": None, "flags": ["EDGE_SUBSYSTEM_UNAVAILABLE"],
                "error": "%s: %s" % (type(exc).__name__, exc)}
    try:
        return graph.submit_edges(control, role, message_id, proposals)
    except BaseException as exc:
        return {"proposed": len(proposals) if isinstance(proposals, list) else 0,
                "linked": [], "pending": [], "denied": [], "invalid": [],
                "pending_file": None, "flags": ["EDGE_SUBSYSTEM_UNAVAILABLE"],
                "error": "%s: %s" % (type(exc).__name__, exc)}


def roles_dir(control: Path) -> Path:
    return control / "roles"


def registry_path(control: Path) -> Path:
    return roles_dir(control) / "registry.json"


def receipts_dir(control: Path) -> Path:
    return roles_dir(control) / "receipts"


def memory_path(control: Path, role: str) -> Path:
    return roles_dir(control) / ("%s.memory.json" % role)


def profile_path(role: str) -> Path:
    return tr.plugin_root() / ROLES[role]["profile"].replace("/", os.sep)


def resolve_control(target=None) -> Path:
    return tr.resolve_control(target)


# ---------------------------------------------------------------- registry (1)

def default_entry(role: str) -> dict:
    return {
        "profile": ROLES[role]["profile"],
        "enabled": True,
        "hot": False,
        "memory": "roles/%s.memory.json" % role,
        "memory_updated_at": None,
        "cursor": {"events_seq": 0, "manifest": None},
        "instance": None,
        "last_dispatch": None,
        "dispatch_history": [],
        "open_tasks": [],
        "pending_task_returns": [],
        "status": "dormant",
    }


def load_registry(control: Path) -> dict:
    """Read the registry, creating/repairing the built-in rows in memory.

    Reading never writes: a read-only session (no lease) must still be able to
    print `role status`."""
    data = tr.read_json(registry_path(control), None)
    if not isinstance(data, dict):
        data = {"schema": SCHEMA_REGISTRY, "roles": {}}
    data.setdefault("schema", SCHEMA_REGISTRY)
    roles = data.get("roles")
    if not isinstance(roles, dict):
        roles = {}
    for role in ROLE_ORDER:
        entry = roles.get(role)
        if not isinstance(entry, dict):
            entry = default_entry(role)
        else:
            base = default_entry(role)
            for key, value in base.items():
                entry.setdefault(key, value)
            entry["profile"] = ROLES[role]["profile"]   # the plugin owns the path
        roles[role] = entry
    data["roles"] = roles
    return data


def save_registry(control: Path, registry: dict) -> None:
    registry["schema"] = SCHEMA_REGISTRY
    registry["updated_at"] = tr.utc_now()
    roles_dir(control).mkdir(parents=True, exist_ok=True)
    tr.write_json(registry_path(control), registry)


def require_role(role) -> str:
    if role in ROLES:
        return role
    raise GateError("unknown role %r; the fleet is: %s" % (role, ", ".join(ROLE_ORDER)), 2)


def entry_of(registry: dict, role: str) -> dict:
    return registry["roles"][role]


# ---------------------------------------------------------------- derived state

def _age_days(value):
    if not value:
        return None
    try:
        return (tr.now_dt() - tr.parse_ts(value)).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return None


def memory_updated_at(control: Path, role: str, entry: dict):
    """Registry stamp or file mtime -- whichever is newer. The file matters
    because turn_runtime's legacy `role memory commit` path also writes it."""
    stamps = []
    if entry.get("memory_updated_at"):
        stamps.append(str(entry["memory_updated_at"]))
    path = memory_path(control, role)
    if path.exists():
        try:
            stamps.append(tr.iso(tr.datetime.fromtimestamp(path.stat().st_mtime, tr.timezone.utc)))
        except (OSError, ValueError, AttributeError):
            pass
    if not stamps:
        return None
    return max(stamps)


def refresh_open_tasks(control: Path, entry: dict):
    """Drop K tasks the collab mailbox has closed. A task file we cannot read
    is kept: the registry never silently forgets an obligation."""
    tasks_dir = control / "collab" / "tasks"
    kept = []
    for kid in entry.get("open_tasks") or []:
        path = tasks_dir / ("%s.json" % kid)
        data = tr.read_json(path, None) if path.exists() else None
        if isinstance(data, dict) and data.get("status") in ("done", "failed", "returned"):
            continue
        kept.append(kid)
    return kept


def live_instance(control: Path, entry: dict):
    """3: an instance lives inside one session. A new session sees null."""
    instance = entry.get("instance")
    if not isinstance(instance, dict):
        return None
    if instance.get("session") != tr.session_id(control):
        return None
    return instance


def role_view(control: Path, role: str, entry: dict) -> dict:
    """Everything derived about one role, computed, never trusted from disk."""
    open_tasks = refresh_open_tasks(control, entry)
    last = entry.get("last_dispatch") if isinstance(entry.get("last_dispatch"), dict) else None
    dispatch_age = _age_days((last or {}).get("at"))
    unanswered = bool(last and not last.get("receipt"))
    hot = bool(entry.get("enabled")) and bool(
        open_tasks or (dispatch_age is not None and dispatch_age <= HOT_DISPATCH_DAYS))
    mem_at = memory_updated_at(control, role, entry)
    mem_age = _age_days(mem_at)
    mem_stale = bool(mem_at) and mem_age is not None and mem_age > MEMORY_STALE_DAYS
    instance = live_instance(control, entry)
    if unanswered and dispatch_age is not None and dispatch_age * 24.0 > BUSY_GRACE_HOURS:
        status = "stale"
    elif unanswered:
        status = "busy"
    elif mem_stale:
        status = "stale"
    elif instance:
        status = "hydrated"
    else:
        status = "dormant"
    profile = profile_path(role)
    flags = []
    if unanswered:
        flags.append("DISPATCH_UNANSWERED")
    if mem_stale:
        flags.append("MEMORY_STALE")
    if not profile.exists():
        flags.append("PROFILE_MISSING")
    if hot and not instance:
        flags.append("REHYDRATE_REQUIRED")
    memory = tr.read_json(memory_path(control, role), {}) or {}
    return {
        "role": role, "title": ROLES[role]["title"], "enabled": bool(entry.get("enabled")),
        "hot": hot, "status": status, "profile": ROLES[role]["profile"],
        "profile_present": profile.exists(),
        "open_tasks": open_tasks, "cursor": entry.get("cursor") or {},
        "instance": instance, "last_dispatch": last, "unanswered": unanswered,
        "dispatch_age_days": None if dispatch_age is None else round(dispatch_age, 2),
        "memory_keys": len(memory),
        "memory_bytes": len(tr.canonical(memory).encode("utf-8")) if memory else 0,
        "memory_updated_at": mem_at,
        "memory_age_days": None if mem_age is None else round(mem_age, 2),
        "memory_stale": mem_stale,
        "pending_task_returns": entry.get("pending_task_returns") or [],
        "flags": flags,
    }


def fleet_view(control: Path) -> dict:
    registry = load_registry(control)
    views = [role_view(control, role, entry_of(registry, role)) for role in ROLE_ORDER]
    return {
        "ok": True, "action": "role-status", "session": tr.session_id(control),
        "registry": str(registry_path(control)),
        "registry_present": registry_path(control).exists(),
        "roles": views,
        "hot": [v["role"] for v in views if v["hot"]],
        "rehydrate_required": [v["role"] for v in views
                               if v["hot"] and "REHYDRATE_REQUIRED" in v["flags"]],
        "dispatch_unanswered": [{"role": v["role"],
                                 "message_id": (v["last_dispatch"] or {}).get("message_id"),
                                 "kind": (v["last_dispatch"] or {}).get("kind"),
                                 "at": (v["last_dispatch"] or {}).get("at")}
                                for v in views if v["unanswered"]],
        "memory_stale": [{"role": v["role"], "age_days": v["memory_age_days"]}
                         for v in views if v["memory_stale"]],
        "profiles_missing": [v["role"] for v in views if not v["profile_present"]],
        "pending_edges": pending_edges_summary(control),
    }


def status_lines(control: Path):
    """The SessionStart digest (contract 4 / 6.1). Quiet when nothing is owed."""
    try:
        view = fleet_view(control)
    except BaseException:
        return []                      # fail-open: a hook must never brick a session
    lines = []
    if view["rehydrate_required"]:
        lines.append("REHYDRATE_REQUIRED: %s" % ", ".join(view["rehydrate_required"]))
        lines.append("  hot = open K task or dispatched within %d days; "
                     "`role hydrate <role>` before dispatching again." % HOT_DISPATCH_DAYS)
    for item in view["dispatch_unanswered"]:
        lines.append("DISPATCH_UNANSWERED %s msg=%s kind=%s at=%s"
                     % (item["role"], item["message_id"], item["kind"], item["at"]))
    if view["dispatch_unanswered"]:
        lines.append("  Each dispatch owes exactly one `role receipt`; until then that role "
                     "refuses a second dispatch.")
    for item in view["memory_stale"]:
        lines.append("ROLE_MEMORY_STALE %s (%s days without an update)"
                     % (item["role"], item["age_days"]))
    pending = view.get("pending_edges") or {}
    actionable = pending.get("actionable", pending.get("edges", 0))
    if actionable:
        stale = (pending.get("by_status") or {}).get("STALE") or 0
        lines.append("PENDING_EDGES %d (%s) -- proposals whose endpoint does not exist yet; "
                     "`graph link --replay-pending` once it does.%s"
                     % (actionable,
                        ", ".join("%s=%d" % kv for kv in sorted(pending.get("by_role", {}).items())),
                        "  [%d stale, not counted]" % stale if stale else ""))
    if view["profiles_missing"] and (view["hot"] or view["dispatch_unanswered"]):
        lines.append("PROFILE_MISSING: %s (hydrate prints a placeholder)"
                     % ", ".join(view["profiles_missing"]))
    return lines


# ---------------------------------------------------------------- list / enable / disable

def role_list(control: Path) -> dict:
    view = fleet_view(control)
    view["action"] = "role-list"
    return view


def role_set_enabled(control: Path, role: str, enabled: bool) -> dict:
    role = require_role(role)
    tr.ensure_lease(control)
    registry = load_registry(control)
    entry = entry_of(registry, role)
    entry["enabled"] = bool(enabled)
    if not enabled:
        entry["instance"] = None
    _persist(control, registry, role, entry)
    return {"ok": True, "action": "role-enable" if enabled else "role-disable",
            "role": role, "enabled": bool(enabled),
            "view": role_view(control, role, entry)}


def _persist(control: Path, registry: dict, role: str, entry: dict) -> None:
    """Write one role row back with its derived fields refreshed."""
    entry["open_tasks"] = refresh_open_tasks(control, entry)
    view = role_view(control, role, entry)
    entry["hot"] = view["hot"]
    entry["status"] = view["status"]
    registry["roles"][role] = entry
    save_registry(control, registry)


# ---------------------------------------------------------------- hydrate (7)

def profile_digest(role: str, budget: int = 14):
    """Archive digest: headings and bullets only. Generalised from the
    chronicler-only version in turn_runtime (which now delegates here)."""
    path = profile_path(role)
    lines = []
    if not path.exists():
        lines.append("  (%s not installed yet -- placeholder; the role still runs from this "
                     "contract digest)" % ROLES[role]["profile"])
        lines.append("  duty: %s" % ROLES[role]["duty"])
        lines.append("  PROFILE_MISSING")
        return lines
    body = path.read_text(encoding="utf-8", errors="replace")
    kept = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("---"):
            continue
        if stripped.startswith("#") or stripped.startswith("-") or stripped.startswith("*"):
            kept.append(stripped if len(stripped) <= 160 else stripped[:157] + "...")
        if len(kept) >= budget:
            break
    lines.extend("  " + k for k in kept)
    lines.append("  FULL: %s" % path)
    return lines


def memory_digest_lines(control: Path, role: str, budget: int = MEMORY_DIGEST_MAX_LINES):
    memory = tr.read_json(memory_path(control, role), {}) or {}
    size = len(tr.canonical(memory).encode("utf-8")) if memory else 0
    lines = ["PROJECT MEMORY (%d key(s), %d bytes)" % (len(memory), size)]
    keys = sorted(memory)
    for key in keys[:budget - 2]:
        value = memory[key]
        text = value if isinstance(value, str) else tr.canonical(value)
        lines.append("  %s: %s" % (key, text[:110]))
    if len(keys) > budget - 2:
        lines.append("  ... +%d more; FULL: %s" % (len(keys) - (budget - 2), memory_path(control, role)))
    return lines


def role_hydrate(control: Path, role: str) -> dict:
    """Rebuild a role instance after a compaction or a session death:
    archive digest + memory + cursor + open tasks + last receipt. <= 40 lines."""
    role = require_role(role)
    registry = load_registry(control)
    entry = entry_of(registry, role)
    view = role_view(control, role, entry)
    lines = ["ROLE %s -- archive digest%s"
             % (role, "" if view["profile_present"] else " (PROFILE_MISSING)")]
    lines.extend(profile_digest(role))
    lines.append("  Contract: you propose; the CLI writes. Return the fixed fields for your "
                 "kind, <= 30 lines. Never contact another role.")
    lines.extend(memory_digest_lines(control, role, budget=14))

    cursor = view["cursor"] or {}
    lines.append("CURSOR events_seq=%s manifest=%s"
                 % (cursor.get("events_seq"), cursor.get("manifest")))
    if view["open_tasks"]:
        lines.append("OPEN K TASKS: %s" % ", ".join(view["open_tasks"]))
    else:
        lines.append("OPEN K TASKS: none")
    last = view["last_dispatch"] or {}
    if last:
        lines.append("LAST DISPATCH %s kind=%s at=%s receipt=%s"
                     % (last.get("message_id"), last.get("kind"), last.get("at"),
                        last.get("receipt") or "NONE -- DISPATCH_UNANSWERED"))
    else:
        lines.append("LAST DISPATCH: none")
    receipt = last_receipt(control, entry)
    if receipt:
        lines.append("LAST RECEIPT %s status=%s at=%s"
                     % (receipt.get("message_id"), receipt.get("status"), receipt.get("at")))
        for flag in (receipt.get("flags") or [])[:4]:
            lines.append("  flag: %s" % flag)

    if role == "chronicler":
        turn = tr.read_turn(control)
        if turn and turn.get("status") == "OPEN":
            owed = tr.missing_receipts(control, turn)
            lines.append("OPEN TURN %s (%s), manifests=%d, missing receipts=%d"
                         % (turn.get("display_id"), turn.get("turn_uuid"),
                            len(turn.get("manifest_ids") or []), len(owed)))
            for manifest_id in owed[:3]:
                manifest = tr.load_manifest(control, manifest_id)
                lines.append("  %s bearing=%s reasons=%s"
                             % (manifest_id, manifest.get("narrative_bearing"),
                                ", ".join((manifest.get("bearing_reasons") or [])[:3])))
        else:
            lines.append("No OPEN turn; run `turn probe` when work begins.")

    tr.clear_flag(control, "REHYDRATE_REQUIRED.%s" % role)
    if len(lines) > HYDRATE_MAX_LINES:
        extra = len(lines) - (HYDRATE_MAX_LINES - 1)
        lines = lines[:HYDRATE_MAX_LINES - 1] + [
            "... +%d lines; FULL: %s" % (extra, memory_path(control, role))]
    return {"ok": True, "action": "role-hydrate", "role": role,
            "profile_present": view["profile_present"], "status": view["status"],
            "hot": view["hot"], "lines": lines}


def last_receipt(control: Path, entry: dict):
    last = entry.get("last_dispatch") or {}
    message_id = last.get("receipt") or last.get("message_id")
    if not message_id:
        return None
    return tr.read_json(receipts_dir(control) / ("%s.json" % message_id), None)


# ---------------------------------------------------------------- dispatch (3)

def role_idempotency_key(control: Path, role: str, kind: str, turn_uuid, manifest_id, k_task) -> str:
    """3: sha256(project|role|kind|turn_uuid|manifest_id|k_task)."""
    return tr.sha256_text("|".join([tr.project_id(control), role, kind,
                                    str(turn_uuid or ""), str(manifest_id or ""),
                                    str(k_task or "")]))


def kind_spec(role: str, kind: str) -> dict:
    return (ROLES[role].get("kinds") or {}).get(kind) or GENERIC_KIND


def _normalise_refs(refs):
    out = []
    for item in refs or []:
        for part in str(item).split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out[:MAX_REFS]


def _parse_graph_context(raw):
    if raw in (None, ""):
        return {}
    text = raw
    if str(raw).startswith("@"):
        path = Path(str(raw)[1:])
        if not path.exists():
            raise GateError("graph-context file not found: %s" % path, 2)
        text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateError("--graph-context is not valid JSON: %s" % exc, 2)
    if not isinstance(value, dict):
        raise GateError("--graph-context must be a JSON object "
                        "(e.g. {\"nodes\":[...],\"edges\":[...]})", 2)
    if len(tr.canonical(value).encode("utf-8")) > GRAPH_CONTEXT_MAX_BYTES:
        raise GateError("--graph-context is larger than %d bytes; pass a pointer, not a dump"
                        % GRAPH_CONTEXT_MAX_BYTES, 1)
    return value


def _chronicle_context(control: Path):
    """The turn/manifest a CHRONICLE_TURN dispatch binds to. Probing here is
    the same operation the main flow performs anyway (narrative-contract 6.2:
    a mutating narrative path must bind an existing manifest)."""
    tr.turn_probe(control)          # idempotent: open the turn, freeze the delta
    turn = tr.read_turn(control)
    if not turn or turn.get("status") != "OPEN":
        raise GateError("no OPEN turn to chronicle: run `turn probe` first", 1)
    manifest_ids = turn.get("manifest_ids") or []
    if not manifest_ids:
        raise GateError("this turn has no frozen manifest: run `turn probe` first", 1)
    owed = tr.missing_receipts(control, turn)
    manifest_id = owed[-1] if owed else manifest_ids[-1]
    return turn, tr.load_manifest(control, manifest_id)


def role_dispatch(control: Path, role: str, kind: str, k_task=None, refs=None,
                  turn="current", graph_context=None, objective=None) -> dict:
    role = require_role(role)
    kind = str(kind or "").strip().upper()
    if not KIND_RE.match(kind):
        raise GateError("--kind must be an UPPER_SNAKE token (got %r)" % kind, 2)
    if kind == "CHRONICLE_TURN" and role != "chronicler":
        raise GateError("CHRONICLE_TURN belongs to the chronicler: it drives the one narrative "
                        "commit transaction. Role %r cannot be given it." % role, 1)
    if k_task is not None and not K_TASK_RE.match(str(k_task)):
        raise GateError("--k-task must look like K07 (got %r)" % k_task, 2)
    tr.ensure_lease(control)

    registry = load_registry(control)
    entry = entry_of(registry, role)
    if not entry.get("enabled"):
        raise GateError("role %s is disabled; `role enable %s` first" % (role, role), 1)
    last = entry.get("last_dispatch") if isinstance(entry.get("last_dispatch"), dict) else None
    if last and not last.get("receipt"):
        raise GateError(
            ("DISPATCH_PENDING %s" + NEWLINE +
             "  %s already has an unanswered dispatch (msg=%s kind=%s at=%s)." + NEWLINE +
             "  Contract 6.3: one unanswered dispatch per role at a time. Record its receipt "
             "(`role receipt %s --message-id %s --file ...`) before dispatching again.")
            % (role, role, last.get("message_id"), last.get("kind"), last.get("at"),
               role, last.get("message_id")),
            1, {"role": role, "message_id": last.get("message_id")})

    graph = _parse_graph_context(graph_context)
    ref_list = _normalise_refs(refs)
    turn_uuid = None
    manifest_id = None
    bearing_reasons = []
    chronicle = None

    if kind == "CHRONICLE_TURN":
        open_turn, manifest = _chronicle_context(control)
        turn_uuid = open_turn["turn_uuid"]
        manifest_id = manifest["id"]
        bearing_reasons = manifest.get("bearing_reasons") or []
        chronicle = tr.chronicle_envelope(control, open_turn, manifest)
        if not ref_list:
            ref_list = list(chronicle.get("source_refs") or [])
    elif turn not in (None, "", "none"):
        open_turn = tr.read_turn(control)
        if open_turn and open_turn.get("status") == "OPEN":
            turn_uuid = open_turn.get("turn_uuid")
            ids = open_turn.get("manifest_ids") or []
            manifest_id = ids[-1] if ids else None

    if not graph:
        # v3-contract 3: the CLI fills the role's graph context; a role never
        # queries the graph itself and never has to be told how. There is one
        # API for it -- research_graph.graph_context(control, refs, role) --
        # which decides the per-object node views (<=8 edges), the EDGE_CONFLICTs
        # that touch this role's own edge classes, its pending summary and the
        # 40-line/byte budget. It fails open to {} so a dispatch can never die
        # of a stale projection or of an uninstalled graph.
        try:
            graph = _graph_module().graph_context(control, ref_list, role) or {}
        except Exception:
            graph = {}
        if graph and len(tr.canonical(graph).encode("utf-8")) > GRAPH_CONTEXT_MAX_BYTES:
            graph = {"omitted": "graph context over the %d-byte budget"
                                % GRAPH_CONTEXT_MAX_BYTES}

    spec = kind_spec(role, kind)
    role_key = role_idempotency_key(control, role, kind, turn_uuid, manifest_id, k_task)
    seq = int(entry.get("dispatch_seq") or 0) + 1
    if chronicle is not None:
        # narrative-contract 7 wins on conflict (role-fleet preamble): the
        # chronicler keeps its own message id and idempotency key, because
        # narrative.commit_from_ops memoises on exactly that key.
        message_id = chronicle["message_id"]
        idem = chronicle["idempotency_key"]
    else:
        message_id = "msg_" + tr.sha256_text("%s|%d" % (role_key, seq))[:16]
        idem = role_key

    digest_lines = memory_digest_lines(control, role, budget=MEMORY_DIGEST_MAX_LINES)
    envelope = {
        "schema": SCHEMA_ENVELOPE,
        "message_id": message_id,
        "role": role,
        "kind": kind,
        "project": tr.project_id(control),
        "turn_uuid": turn_uuid,
        "k_task": k_task,
        "manifest_id": manifest_id,
        "inputs": {
            "refs": ref_list,
            "graph_context": graph,
            "memory_digest": NEWLINE.join(digest_lines[:MEMORY_DIGEST_MAX_LINES]),
        },
        "objective": objective or spec.get("objective") or GENERIC_KIND["objective"],
        "constraints": CONSTRAINTS,
        "idempotency_key": idem,
        "role_idempotency_key": role_key,
        "reply_fields": list(spec.get("required") or ["flags", "memory_delta"]),
        "reply_to": ("role receipt %s --message-id %s --file <reply.json>" % (role, message_id)),
    }
    if chronicle is not None:
        # The chronicler agent must still see the CHRONICLE_TURN/v1 shape.
        envelope["chronicle_turn"] = {
            "schema": chronicle["schema"], "message_id": chronicle["message_id"],
            "turn_uuid": chronicle["turn_uuid"], "manifest_id": chronicle["manifest_id"],
            "project": chronicle["project"], "bearing_reasons": bearing_reasons,
            "source_refs": chronicle.get("source_refs") or [],
            "objective": chronicle["objective"], "constraints": chronicle["constraints"],
            "idempotency_key": chronicle["idempotency_key"],
        }
        envelope["objective"] = chronicle["objective"]
        envelope["constraints"] = chronicle["constraints"]
    if k_task:
        envelope["inputs"]["k_task_spec"] = _k_task_brief(control, k_task)
    _trim_envelope(envelope)

    now = tr.utc_now()
    record = {"message_id": message_id, "kind": kind, "at": now, "receipt": None,
              "idempotency_key": idem, "role_idempotency_key": role_key,
              "turn_uuid": turn_uuid, "manifest_id": manifest_id, "k_task": k_task,
              "refs": ref_list, "by": tr.session_id(control)}
    repeat = [d for d in (entry.get("dispatch_history") or [])
              if d.get("role_idempotency_key") == role_key]
    if repeat:
        record["repeat_of"] = repeat[-1].get("message_id")
        envelope["repeat_of"] = repeat[-1].get("message_id")
    entry["dispatch_seq"] = seq
    entry["last_dispatch"] = record
    history = list(entry.get("dispatch_history") or [])
    history.append(record)
    entry["dispatch_history"] = history[-DISPATCH_HISTORY_MAX:]
    if k_task and k_task not in (entry.get("open_tasks") or []):
        entry["open_tasks"] = list(entry.get("open_tasks") or []) + [k_task]
    _persist(control, registry, role, entry)
    tr.register_event(control, "role_dispatch",
                      {"role": role, "kind": kind, "message_id": message_id,
                       "k_task": k_task}, source="role_runtime")
    return {"ok": True, "action": "role-dispatch", "role": role, "kind": kind,
            "message_id": message_id, "envelope": envelope,
            "envelope_bytes": len(tr.canonical(envelope).encode("utf-8")),
            "profile": ROLES[role]["profile"],
            "profile_present": profile_path(role).exists()}


def _k_task_brief(control: Path, k_task):
    task = tr.read_json(control / "collab" / "tasks" / ("%s.json" % k_task), None)
    if not isinstance(task, dict):
        return {"id": k_task, "status": "UNKNOWN (no task file)"}
    return {"id": k_task, "title": task.get("title"), "status": task.get("status"),
            "lane": task.get("lane"), "spec": str(task.get("spec") or "")[:600],
            "inputs": (task.get("inputs") or [])[:8]}


def _trim_envelope(envelope: dict) -> None:
    """8 KiB budget (narrative-contract 7): drop refs, then graph context, then
    memory digest lines -- never the objective or the idempotency key."""
    def size():
        return len(tr.canonical(envelope).encode("utf-8"))

    while size() > ENVELOPE_MAX_BYTES and envelope["inputs"]["refs"]:
        envelope["inputs"]["refs"] = envelope["inputs"]["refs"][:-1]
        envelope["truncated"] = True
    if size() > ENVELOPE_MAX_BYTES and envelope["inputs"].get("graph_context"):
        envelope["inputs"]["graph_context"] = {"omitted": "over envelope budget"}
        envelope["truncated"] = True
    while size() > ENVELOPE_MAX_BYTES and envelope["inputs"]["memory_digest"]:
        lines = envelope["inputs"]["memory_digest"].split(NEWLINE)
        if len(lines) <= 1:
            envelope["inputs"]["memory_digest"] = ""
            break
        envelope["inputs"]["memory_digest"] = NEWLINE.join(lines[:-1])
        envelope["truncated"] = True


# ---------------------------------------------------------------- memory (4)

def merge_memory(memory: dict, delta: dict) -> dict:
    """4: merge, never overwrite. Lists append-with-dedupe, dicts merge
    shallowly, null deletes, anything else replaces."""
    out = dict(memory)
    for key, value in delta.items():
        if value is None:
            out.pop(key, None)
            continue
        old = out.get(key)
        if isinstance(old, list) and isinstance(value, list):
            merged = list(old)
            for item in value:
                if item not in merged:
                    merged.append(item)
            out[key] = merged
        elif isinstance(old, dict) and isinstance(value, dict):
            merged = dict(old)
            merged.update(value)
            out[key] = merged
        else:
            out[key] = value
    return out


def _write_memory(control: Path, role: str, memory: dict) -> int:
    body = json.dumps(memory, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    size = len(body.encode("utf-8"))
    if size > MEMORY_MAX_BYTES:
        raise GateError("merged memory would be %d bytes; the budget is %d (contract 1). Drop or "
                        "summarise keys before committing." % (size, MEMORY_MAX_BYTES), 1)
    roles_dir(control).mkdir(parents=True, exist_ok=True)
    ros.atomic_write(memory_path(control, role), body)
    return size


def apply_memory_delta(control: Path, registry: dict, role: str, entry: dict, delta):
    if not isinstance(delta, dict) or not delta:
        return {"applied": False, "bytes": None, "keys": []}
    raw = tr.canonical(delta).encode("utf-8")
    if len(raw) > MEMORY_MAX_BYTES:
        raise GateError("memory delta is %d bytes; the budget is %d (contract 4). Compress it: "
                        "the role memory is a working set, not an archive."
                        % (len(raw), MEMORY_MAX_BYTES), 1)
    memory = tr.read_json(memory_path(control, role), {}) or {}
    merged = merge_memory(memory, delta)
    size = _write_memory(control, role, merged)
    entry["memory_updated_at"] = tr.utc_now()
    return {"applied": True, "bytes": size, "keys": sorted(merged)}


def memory_commit(control: Path, role: str, delta_file: str) -> dict:
    """The manual path (7) -- same merge rules as a receipt."""
    role = require_role(role)
    tr.ensure_lease(control)
    path = Path(delta_file)
    if not path.exists():
        raise GateError("delta file not found: %s" % delta_file, 2)
    raw = path.read_bytes()
    if len(raw) > MEMORY_MAX_BYTES:
        raise GateError("memory delta is %d bytes; the budget is %d (contract 4). Compress it: "
                        "the role memory is a working set, not an archive."
                        % (len(raw), MEMORY_MAX_BYTES), 1)
    try:
        delta = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GateError("delta file is not valid UTF-8 JSON: %s" % exc, 2)
    if isinstance(delta, dict) and isinstance(delta.get("memory_delta"), dict):
        delta = delta["memory_delta"]
    if not isinstance(delta, dict):
        raise GateError("memory delta must be a JSON object (keys with null delete)", 2)
    registry = load_registry(control)
    entry = entry_of(registry, role)
    memory = tr.read_json(memory_path(control, role), {}) or {}
    merged = merge_memory(memory, delta)
    size = _write_memory(control, role, merged)
    entry["memory_updated_at"] = tr.utc_now()
    _persist(control, registry, role, entry)
    return {"ok": True, "action": "role-memory-commit", "role": role,
            "keys": sorted(merged), "bytes": size, "path": str(memory_path(control, role))}


def memory_show(control: Path, role: str) -> dict:
    role = require_role(role)
    memory = tr.read_json(memory_path(control, role), {}) or {}
    entry = entry_of(load_registry(control), role)
    return {"ok": True, "action": "role-memory-show", "role": role,
            "path": str(memory_path(control, role)),
            "bytes": len(tr.canonical(memory).encode("utf-8")) if memory else 0,
            "updated_at": memory_updated_at(control, role, entry),
            "memory": memory}


# ---------------------------------------------------------------- instance (1 / 6.1)

def instance_set(control: Path, role: str, agent_id: str) -> dict:
    role = require_role(role)
    tr.ensure_lease(control)
    registry = load_registry(control)
    entry = entry_of(registry, role)
    entry["instance"] = {"session": tr.session_id(control), "agent_id": str(agent_id),
                         "spawned_at": tr.utc_now()}
    _persist(control, registry, role, entry)
    return {"ok": True, "action": "role-instance-set", "role": role,
            "instance": entry["instance"]}


def instance_clear(control: Path, role: str) -> dict:
    role = require_role(role)
    tr.ensure_lease(control)
    registry = load_registry(control)
    entry = entry_of(registry, role)
    entry["instance"] = None
    _persist(control, registry, role, entry)
    return {"ok": True, "action": "role-instance-clear", "role": role}


# ---------------------------------------------------------------- receipt (4)

def _find_dispatch(entry: dict, message_id: str):
    last = entry.get("last_dispatch") if isinstance(entry.get("last_dispatch"), dict) else None
    if last and last.get("message_id") == message_id:
        return last, True
    for record in reversed(entry.get("dispatch_history") or []):
        if record.get("message_id") == message_id:
            return record, False
    return None, False


def _validate_reply(role: str, kind: str, reply):
    """Per-kind fixed fields; an undeclared kind still owes flags[] +
    memory_delta{} (contract 4)."""
    problems = []
    if not isinstance(reply, dict):
        return ["reply must be a JSON object"], {}
    body = reply
    if isinstance(reply.get("reply"), dict):
        body = reply["reply"]
    spec = kind_spec(role, kind)
    required = list(spec.get("required") or [])
    for field in ("flags", "memory_delta"):
        if field not in required:
            required.append(field)
    for field in required:
        if field not in body:
            problems.append("missing required field %r for kind %s" % (field, kind))
    if "flags" in body and not isinstance(body["flags"], list):
        problems.append("flags must be a list")
    if "memory_delta" in body and not isinstance(body["memory_delta"], dict):
        problems.append("memory_delta must be an object")
    for field in spec.get("change_fields") or []:
        if field in body and not isinstance(body[field], list):
            if field == "proposed_ops" and body[field] is None:
                continue
            problems.append("%s must be a list" % field)
    if kind == "CHRONICLE_TURN":
        if "commit_meta" in body and not isinstance(body["commit_meta"], dict):
            problems.append("commit_meta must be an object")
        if "card_delta" in body:
            problems.append("card_delta is not a chronicler output "
                            "(narrative-contract 7): the card is derived")
    return problems, body


def _write_role_receipt(control: Path, payload: dict, suffix="") -> Path:
    receipts_dir(control).mkdir(parents=True, exist_ok=True)
    name = payload["message_id"] + (suffix or "") + ".json"
    path = receipts_dir(control) / name
    tr.write_json(path, payload)
    return path


def _has_change(role: str, kind: str, body: dict) -> bool:
    spec = kind_spec(role, kind)
    fields = spec.get("change_fields") or []
    if not fields:
        return bool(body.get("flags"))
    return any(body.get(field) for field in fields)


def _return_k_task(control: Path, k_task: str, role: str, message_id: str,
                   status: str, receipt_rel: str):
    """Close the K task through the existing collab CLI (contract 4). If the
    mailbox refuses (not claimed here, already returned, CLI absent) the
    obligation is recorded as pending, never silently dropped."""
    script = tr.plugin_root() / "scripts" / "collab.py"
    if not script.exists():
        return {"status": "pending", "reason": "collab.py not installed"}
    task = tr.read_json(control / "collab" / "tasks" / ("%s.json" % k_task), None)
    if not isinstance(task, dict):
        return {"status": "pending", "reason": "no task file for %s" % k_task}
    if task.get("status") in ("done", "failed", "returned"):
        return {"status": "already_closed", "task_status": task.get("status")}
    digest = ("role %s answered %s (%s); proposals only -- the CLI merged memory and advanced "
              "the cursor." % (role, message_id, status))
    argv = [sys.executable, str(script), "return", str(tr.project_root(control)),
            "--id", k_task, "--digest", digest, "--artifact", receipt_rel]
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              cwd=str(tr.project_root(control)))
    except OSError as exc:
        return {"status": "pending", "reason": "collab.py could not be run: %s" % exc}
    if proc.returncode == 0:
        return {"status": "returned"}
    return {"status": "pending",
            "reason": (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()[:240]}


def role_receipt(control: Path, role: str, message_id: str, reply_file: str,
                 ref: str = "main") -> dict:
    role = require_role(role)
    tr.ensure_lease(control)
    registry = load_registry(control)
    entry = entry_of(registry, role)
    dispatch, is_last = _find_dispatch(entry, message_id)
    if dispatch is None:
        raise GateError(
            "UNKNOWN_MESSAGE_ID %s for role %s; the registry has no such dispatch. "
            "A receipt may only answer a registered dispatch (contract 0.4)."
            % (message_id, role), 1)

    existing = tr.read_json(receipts_dir(control) / ("%s.json" % message_id), None)
    if existing is not None:
        return {"ok": True, "action": "role-receipt", "role": role,
                "message_id": message_id, "result": "ALREADY_ANSWERED",
                "status": existing.get("status"), "receipt": existing,
                "memory_merged": False}

    path = Path(reply_file)
    if not path.exists():
        raise GateError("reply file not found: %s" % reply_file, 2)
    try:
        reply = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GateError("reply file is not valid UTF-8 JSON: %s" % exc, 2)

    kind = dispatch.get("kind")
    problems, body = _validate_reply(role, kind, reply)
    claimed = None
    if isinstance(reply, dict):
        claimed = reply.get("idempotency_key") or (body or {}).get("idempotency_key")
    if claimed and claimed not in (dispatch.get("idempotency_key"),
                                   dispatch.get("role_idempotency_key")):
        problems.insert(0, "idempotency_key does not match the dispatch")
    claimed_msg = (reply.get("message_id") if isinstance(reply, dict) else None)
    if claimed_msg and claimed_msg != message_id:
        problems.insert(0, "reply message_id %r != %r" % (claimed_msg, message_id))

    now = tr.utc_now()
    if problems:
        rejected = {
            "schema": SCHEMA_RECEIPT, "message_id": message_id, "role": role, "kind": kind,
            "status": "REJECTED", "at": now, "by": tr.session_id(control),
            "idempotency_key": dispatch.get("idempotency_key"),
            "reasons": problems, "reply_file": str(path),
        }
        # A rejected reply does NOT discharge the dispatch: last_dispatch keeps
        # receipt=null, so the role still shows DISPATCH_UNANSWERED.
        seq = len([p for p in receipts_dir(control).glob("%s.rejected*.json" % message_id)]) \
            if receipts_dir(control).exists() else 0
        _write_role_receipt(control, rejected, suffix=".rejected%d" % (seq + 1))
        raise GateError(
            ("ROLE_RECEIPT_REJECTED %s (%s/%s)" + NEWLINE + "  %s" + NEWLINE +
             "  Nothing was merged: no memory, no cursor, no commit. The dispatch is still "
             "unanswered -- fix the reply and record it again.")
            % (message_id, role, kind, (NEWLINE + "  ").join(problems)),
            1, {"role": role, "message_id": message_id, "reasons": problems})

    payload = {
        "schema": SCHEMA_RECEIPT, "message_id": message_id, "role": role, "kind": kind,
        "at": now, "by": tr.session_id(control),
        "idempotency_key": dispatch.get("idempotency_key"),
        "role_idempotency_key": dispatch.get("role_idempotency_key"),
        "turn_uuid": dispatch.get("turn_uuid"), "manifest_id": dispatch.get("manifest_id"),
        "k_task": dispatch.get("k_task"),
        "flags": list(body.get("flags") or []),
        "reply": {k: v for k, v in body.items() if k != "memory_delta"},
    }
    status = "ANSWERED" if _has_change(role, kind, body) else "ANSWERED_NO_CHANGE"

    # --- CHRONICLE_TURN: no second commit path (narrative-contract 6.1).
    if kind == "CHRONICLE_TURN":
        narrative_result = _chronicle_disposition(control, dispatch, body, ref)
        payload["narrative"] = narrative_result
        if narrative_result.get("result") == "NARRATIVE_REVIEWED_NO_CHANGE":
            status = "ANSWERED_NO_CHANGE"
        elif narrative_result.get("result") in ("NARRATIVE_COMMITTED", "ALREADY_RECORDED"):
            status = "ANSWERED"

    edges_info = _dispose_proposed_edges(control, role, message_id,
                                        body.get("proposed_edges"))
    payload["edges"] = edges_info
    for flag in edges_info.get("flags") or []:
        if flag not in payload["flags"]:
            payload["flags"].append(flag)

    memory_info = apply_memory_delta(control, registry, role, entry,
                                     body.get("memory_delta") or {})
    payload["memory"] = memory_info
    payload["status"] = status

    cursor = {"events_seq": len(tr.read_events(control)),
              "manifest": dispatch.get("manifest_id") or (entry.get("cursor") or {}).get("manifest"),
              "at": now}
    entry["cursor"] = cursor
    payload["cursor"] = cursor

    receipt_path = _write_role_receipt(control, payload)
    rel = receipt_path.relative_to(tr.project_root(control)).as_posix()

    k_task = dispatch.get("k_task")
    if k_task:
        outcome = _return_k_task(control, k_task, role, message_id, status, rel)
        payload["k_task_return"] = outcome
        tr.write_json(receipt_path, payload)
        if outcome.get("status") in ("returned", "already_closed"):
            entry["open_tasks"] = [k for k in (entry.get("open_tasks") or []) if k != k_task]
        else:
            pending = [p for p in (entry.get("pending_task_returns") or [])
                       if p.get("k_task") != k_task]
            pending.append({"k_task": k_task, "message_id": message_id,
                            "reason": outcome.get("reason"), "at": now})
            entry["pending_task_returns"] = pending

    dispatch["receipt"] = message_id
    dispatch["answered_at"] = now
    if is_last:
        entry["last_dispatch"] = dispatch
    else:
        last = entry.get("last_dispatch")
        if isinstance(last, dict) and last.get("message_id") == message_id:
            entry["last_dispatch"] = dispatch
    history = []
    for record in entry.get("dispatch_history") or []:
        if record.get("message_id") == message_id:
            record = dispatch
        history.append(record)
    entry["dispatch_history"] = history
    if isinstance(entry.get("instance"), dict):
        entry["instance"]["last_answered"] = now
    _persist(control, registry, role, entry)
    tr.register_event(control, "role_receipt",
                      {"role": role, "kind": kind, "message_id": message_id,
                       "status": status}, source="role_runtime")
    return {"ok": True, "action": "role-receipt", "role": role, "kind": kind,
            "message_id": message_id, "result": status, "status": status,
            "receipt_path": str(receipt_path), "memory": memory_info,
            "edges": edges_info,
            "narrative": payload.get("narrative"),
            "k_task_return": payload.get("k_task_return"),
            "cursor": cursor, "flags": payload["flags"]}


def _chronicle_disposition(control: Path, dispatch: dict, body: dict, ref: str) -> dict:
    """Write the proposal to temp files OUTSIDE the project (a proposal is not
    a project input) and call the one orchestration entry point."""
    manifest_id = dispatch.get("manifest_id")
    if not manifest_id:
        raise GateError("this CHRONICLE_TURN dispatch has no manifest_id; it cannot be "
                        "dispositioned", 1)
    ops = body.get("proposed_ops") or []
    meta = dict(body.get("commit_meta") or {})
    # The memory delta is merged here with the role-fleet 4 semantics; forwarding
    # it into the narrative commit meta as well would merge it twice.
    meta.pop("memory_delta", None)
    if not ops:
        reason = (meta.get("message") or meta.get("reason")
                  or "chronicler reviewed the delta; the tree does not move")
        result = tr.turn_record_disposition(control, manifest_id, no_change=True,
                                           reason=str(reason)[:300], ref=ref)
        return {"result": result.get("result"), "manifest_id": manifest_id,
                "commit_ids": [], "reason": str(reason)[:300]}
    author = dict(meta.get("author") or {})
    author.setdefault("session", tr.session_id(control))
    author["role"] = "chronicler"
    meta["author"] = author
    meta.setdefault("message", "chronicled turn %s" % manifest_id)
    tmp = Path(tempfile.mkdtemp(prefix="ar-role-chronicle-"))
    try:
        ops_file = tmp / "ops.json"
        meta_file = tmp / "meta.json"
        ros.atomic_write(ops_file, tr.canonical({"ops": ops}) + "\n")
        ros.atomic_write(meta_file, tr.canonical(meta) + "\n")
        result = tr.turn_record_disposition(control, manifest_id, ops_file=str(ops_file),
                                            meta_file=str(meta_file), ref=ref)
    finally:
        try:
            import shutil
            shutil.rmtree(str(tmp), ignore_errors=True)
        except Exception:
            pass
    return {"result": result.get("result"), "manifest_id": manifest_id,
            "commit_ids": result.get("commit_ids") or [],
            "kind": result.get("kind"), "proposed_kind": result.get("proposed_kind"),
            "kind_corrected": result.get("kind_corrected"),
            "flags": result.get("flags") or []}


# ---------------------------------------------------------------- rendering

def render_status(view: dict):
    lines = ["ROLE FLEET (session %s)" % view["session"]]
    for row in view["roles"]:
        marks = []
        if not row["enabled"]:
            marks.append("disabled")
        if row["hot"]:
            marks.append("hot")
        marks.extend(row["flags"])
        lines.append("  %-18s %-9s mem=%dB/%s tasks=%s %s"
                     % (row["role"], row["status"], row["memory_bytes"],
                        ("%.0fd" % row["memory_age_days"]) if row["memory_age_days"] is not None else "-",
                        ",".join(row["open_tasks"]) or "-", " ".join(marks)))
    extra = status_lines_from_view(view)
    lines.extend(extra)
    return lines


def status_lines_from_view(view: dict):
    lines = []
    if view["rehydrate_required"]:
        lines.append("REHYDRATE_REQUIRED: %s" % ", ".join(view["rehydrate_required"]))
    for item in view["dispatch_unanswered"]:
        lines.append("DISPATCH_UNANSWERED %s msg=%s kind=%s at=%s"
                     % (item["role"], item["message_id"], item["kind"], item["at"]))
    for item in view["memory_stale"]:
        lines.append("ROLE_MEMORY_STALE %s (%s days)" % (item["role"], item["age_days"]))
    pending = view.get("pending_edges") or {}
    actionable = pending.get("actionable", pending.get("edges", 0))
    if actionable:
        lines.append("PENDING_EDGES %d in %d file(s)" % (actionable, pending["files"]))
    return lines


# ---------------------------------------------------------------- self-test (8)

class _Report(object):
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append({"name": name, "ok": bool(ok), "detail": str(detail)[:220]})
        return bool(ok)

    @property
    def ok(self):
        return all(row["ok"] for row in self.rows)


def _st_run(script, argv, home, cwd=None):
    cmd = [sys.executable, str(tr.plugin_root() / "scripts" / script)] + [str(a) for a in argv]
    env = dict(os.environ)
    env.update({"AR_SESSION": os.environ.get("AR_SESSION", "W800"),
                "USERPROFILE": str(home), "HOME": str(home),
                "HOMEDRIVE": "", "HOMEPATH": "", "PYTHONIOENCODING": "utf-8"})
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd=str(cwd) if cwd else None, env=env)
    out = proc.stdout.decode("utf-8", "replace")
    parsed = None
    try:
        parsed = json.loads(out)
    except ValueError:
        start = out.find("{")
        if start >= 0:
            try:
                parsed = json.loads(out[start:])
            except ValueError:
                parsed = None
    return proc.returncode, out, proc.stderr.decode("utf-8", "replace"), parsed


def _st_gate(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False, "", 0
    except GateError as exc:
        return True, exc.text, exc.code


def run_self_test() -> int:
    report = _Report()
    saved_session = os.environ.get("AR_SESSION")
    saved_narrative = sys.modules.get("narrative")
    base = Path(tempfile.mkdtemp(prefix="ar-role-selftest-"))
    home, root, work = base / "home", base / "proj", base / "work"
    for directory in (home, root, work):
        directory.mkdir(parents=True, exist_ok=True)
    control = root / ".research-os"
    os.environ["AR_SESSION"] = "W800"
    try:
        rc, _o, err, data = _st_run("research_os.py", ["bootstrap", root, "--title", "RoleT"], home)
        report.check("setup: research_os bootstrap", rc == 0 and (data or {}).get("ok") is True,
                     err[:200])
        rc, _o, err, data = _st_run("narrative.py",
                                    ["init", root, "--title", "RoleT", "--venue", "RFS"], home)
        report.check("setup: narrative init (genesis commit)",
                     rc == 0 and (data or {}).get("commit_id"), err[:200])

        for name, fn, argv in (
                ("registry", _t_registry, (control, report)),
                ("dispatch/receipt", _t_dispatch_and_receipt, (control, work, report)),
                ("memory", _t_memory_budget, (control, work, report)),
                ("proposed edges", _t_proposed_edges, (control, root, work, home, report)),
                ("chronicle", _t_chronicle_receipt, (control, root, work, report)),
                ("session restart", _t_session_restart, (control, report))):
            try:
                fn(*argv)
            except BaseException as exc:      # a crashing block is one failed row
                report.check("%s block completed" % name, False,
                             "%s: %s" % (type(exc).__name__, exc))
    finally:
        os.environ.pop("AR_SESSION", None)
        if saved_session is not None:
            os.environ["AR_SESSION"] = saved_session
        if saved_narrative is not None:
            sys.modules["narrative"] = saved_narrative
        else:
            sys.modules.pop("narrative", None)
        try:
            import shutil
            shutil.rmtree(str(base), ignore_errors=True)
        except Exception:
            pass
    passed = sum(1 for row in report.rows if row["ok"])
    print(json.dumps({"ok": report.ok, "passed": passed, "total": len(report.rows),
                      "checks": report.rows}, ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


def _t_registry(control, report):
    """8.1: five roles registered, enable/disable, hot judgment, status lines."""
    view = fleet_view(control)
    report.check("1a the five built-in roles are registered",
                 [r["role"] for r in view["roles"]] == ROLE_ORDER and len(ROLE_ORDER) == 5,
                 [r["role"] for r in view["roles"]])
    report.check("1b a fresh fleet is cold: nothing hot, nothing unanswered",
                 not view["hot"] and not view["dispatch_unanswered"], view["hot"])
    report.check("1c missing profiles are reported, not fatal",
                 all(role_view(control, r, entry_of(load_registry(control), r)) is not None
                     for r in ROLE_ORDER), view["profiles_missing"])
    hydrated = role_hydrate(control, "figure-engineer")
    report.check("1d hydrate stays inside the 40-line budget and points at the archive",
                 len(hydrated["lines"]) <= HYDRATE_MAX_LINES
                 and any(ln.startswith("  FULL: ") or "PROFILE_MISSING" in ln
                         for ln in hydrated["lines"]),
                 len(hydrated["lines"]))
    saved = ROLES["figure-engineer"]["profile"]
    ROLES["figure-engineer"]["profile"] = "agents/research-not-installed-yet.md"
    try:
        placeholder = role_hydrate(control, "figure-engineer")
        report.check("1d2 a missing profile hydrates as a placeholder, not an error",
                     any("PROFILE_MISSING" in ln for ln in placeholder["lines"])
                     and placeholder["profile_present"] is False,
                     placeholder["lines"][:3])
        report.check("1d3 status flags the missing profile without failing",
                     "PROFILE_MISSING" in role_view(
                         control, "figure-engineer",
                         entry_of(load_registry(control), "figure-engineer"))["flags"])
    finally:
        ROLES["figure-engineer"]["profile"] = saved
    role_set_enabled(control, "theory-operator", False)
    disabled = role_view(control, "theory-operator",
                         entry_of(load_registry(control), "theory-operator"))
    report.check("1e disable sticks and keeps a disabled role out of hot",
                 disabled["enabled"] is False and disabled["hot"] is False, disabled["status"])
    role_set_enabled(control, "theory-operator", True)
    report.check("1f enable restores it",
                 role_view(control, "theory-operator",
                           entry_of(load_registry(control), "theory-operator"))["enabled"],
                 "")
    instance_set(control, "evidence-steward", "agent-xyz")
    inst = role_view(control, "evidence-steward",
                     entry_of(load_registry(control), "evidence-steward"))["instance"]
    report.check("1g instance set is visible inside this session",
                 (inst or {}).get("agent_id") == "agent-xyz", inst)
    instance_clear(control, "evidence-steward")
    report.check("1h instance clear empties it",
                 role_view(control, "evidence-steward",
                           entry_of(load_registry(control), "evidence-steward"))["instance"] is None)


def _t_dispatch_and_receipt(control, work, report):
    """8.2 + 6.3: envelope, DISPATCH_PENDING, receipt, ALREADY_ANSWERED."""
    result = role_dispatch(control, "figure-engineer", "FIGURE_REQUEST", k_task="K07",
                           refs=["figs/f1.py", "figs/f1.py", "docs/spec.md"],
                           graph_context='{"nodes":["C-001"],"edges":[]}')
    envelope = result["envelope"]
    report.check("2a the envelope is ROLE_TASK/v1 with the contract fields",
                 envelope["schema"] == SCHEMA_ENVELOPE and envelope["role"] == "figure-engineer"
                 and envelope["k_task"] == "K07"
                 and set(envelope["inputs"]) >= {"refs", "graph_context", "memory_digest"}
                 and envelope["idempotency_key"], sorted(envelope))
    report.check("2b refs are de-duplicated and capped at 16",
                 envelope["inputs"]["refs"] == ["figs/f1.py", "docs/spec.md"],
                 envelope["inputs"]["refs"])
    report.check("2c the envelope fits the 8 KiB budget",
                 result["envelope_bytes"] <= ENVELOPE_MAX_BYTES, result["envelope_bytes"])
    report.check("2d the idempotency key is sha256(project|role|kind|turn|manifest|k_task)",
                 envelope["idempotency_key"] == role_idempotency_key(
                     control, "figure-engineer", "FIGURE_REQUEST",
                     envelope["turn_uuid"], envelope["manifest_id"], "K07"),
                 envelope["idempotency_key"][:12])

    raised, text, code = _st_gate(role_dispatch, control, "figure-engineer", "FIGURE_REQUEST")
    report.check("2e a second unanswered dispatch to the same role is refused",
                 raised and code == 1 and "DISPATCH_PENDING" in text,
                 (text or "").splitlines()[0] if text else "")

    message_id = result["message_id"]
    view = fleet_view(control)
    report.check("2f the pending dispatch shows as DISPATCH_UNANSWERED and hot",
                 "figure-engineer" in view["hot"]
                 and [d["role"] for d in view["dispatch_unanswered"]] == ["figure-engineer"],
                 view["dispatch_unanswered"])

    bad = work / "reply_bad.json"
    tr.write_json(bad, {"routes": [], "flags": []})
    raised, text, code = _st_gate(role_receipt, control, "figure-engineer", message_id, str(bad))
    report.check("2g a reply missing fixed fields is REJECTED and merges nothing",
                 raised and code == 1 and "ROLE_RECEIPT_REJECTED" in text, code)
    report.check("2h a rejected reply does not discharge the dispatch",
                 fleet_view(control)["dispatch_unanswered"][0]["message_id"] == message_id)

    good = work / "reply_good.json"
    tr.write_json(good, {
        "proposed_figure_requests": [{"id": "F-1", "kind": "mechanism"}],
        "routes": [{"id": "F-1", "tool": "vector"}],
        "qa_findings": [],
        "flags": ["NEEDS_SOURCE"],
        "memory_delta": {"routes_seen": ["F-1"], "north_star": "price of search"},
        "idempotency_key": envelope["idempotency_key"]})
    receipt = role_receipt(control, "figure-engineer", message_id, str(good))
    report.check("2i a valid reply is ANSWERED and merges memory",
                 receipt["result"] == "ANSWERED" and receipt["memory"]["applied"] is True,
                 receipt["result"])
    report.check("2j the receipt landed under roles/receipts/<message_id>.json",
                 (receipts_dir(control) / ("%s.json" % message_id)).exists())
    report.check("2k the K task return was attempted and recorded",
                 isinstance(receipt.get("k_task_return"), dict)
                 and receipt["k_task_return"].get("status") in
                 ("returned", "already_closed", "pending"),
                 receipt.get("k_task_return"))
    memory_after = tr.read_json(memory_path(control, "figure-engineer"), {})
    again = role_receipt(control, "figure-engineer", message_id, str(good))
    report.check("2l a second receipt for the same message id is ALREADY_ANSWERED",
                 again["result"] == "ALREADY_ANSWERED" and again["memory_merged"] is False,
                 again["result"])
    report.check("2m the memory was not merged twice",
                 tr.read_json(memory_path(control, "figure-engineer"), {}) == memory_after,
                 "")
    report.check("2n the cursor advanced",
                 (role_view(control, "figure-engineer",
                            entry_of(load_registry(control), "figure-engineer"))["cursor"]
                  or {}).get("at") is not None, "")
    report.check("2o answering clears DISPATCH_UNANSWERED",
                 not fleet_view(control)["dispatch_unanswered"], "")

    # a role whose reply changes nothing
    second = role_dispatch(control, "evidence-steward", "EVIDENCE_AUDIT")
    empty = work / "reply_empty.json"
    tr.write_json(empty, {"ledger_ops": [], "citation_verdicts": [], "collisions": [],
                          "flags": [], "memory_delta": {}})
    result2 = role_receipt(control, "evidence-steward", second["message_id"], str(empty))
    report.check("2p an empty proposal is ANSWERED_NO_CHANGE",
                 result2["result"] == "ANSWERED_NO_CHANGE", result2["result"])

    # an undeclared kind only owes flags[] + memory_delta{}
    third = role_dispatch(control, "experiment-runner", "QUESTION")
    generic = work / "reply_generic.json"
    tr.write_json(generic, {"flags": ["ANSWERED_INLINE"], "memory_delta": {"asked": ["q1"]}})
    result3 = role_receipt(control, "experiment-runner", third["message_id"], str(generic))
    report.check("2q an undeclared kind validates on flags[] + memory_delta{} alone",
                 result3["result"] in ("ANSWERED", "ANSWERED_NO_CHANGE"), result3["result"])


def _t_proposed_edges(control, root, work, home, report):
    """graph-contract 5: proposed_edges[] in a receipt -- linked, parked, or
    refused as out-of-column; and the replay that clears the park."""
    import research_graph as rg
    rg.cache_clear()
    node = _tree_root_id(control)
    tr.write_json(control / "runs" / "proto-x1.json",
                  {"schema_version": "auto-research/run-v1", "run_id": "proto-x1",
                   "protocol": "proto", "status": "done", "result": "ok"})

    dispatched = role_dispatch(control, "experiment-runner", "RUN_RECONCILE")
    message_id = dispatched["message_id"]
    reply = work / "reply_edges.json"
    tr.write_json(reply, {
        "run_ops": [], "evidence_proposals": [], "reproduce_requests": [],
        "flags": [], "memory_delta": {},
        "proposed_edges": [
            {"from": "ro::run:proto-x1", "to": "ro::narr:%s" % node,
             "kind": "supported_by", "polarity": "+", "basis": "proto/x1@out.json"},
            {"from": "ro::run:proto-x2", "to": "ro::narr:%s" % node,
             "kind": "supported_by", "polarity": "-", "basis": "proto/x2@not-yet"},
        ]})
    receipt = role_receipt(control, "experiment-runner", message_id, str(reply))
    edges = receipt.get("edges") or {}
    report.check("6a a resolvable proposal is linked at receipt time under the role name",
                 len(edges.get("linked") or []) == 1
                 and len(rg.list_edges(control, by="experiment-runner")) == 1,
                 edges.get("linked"))
    landed = rg.list_edges(control, by="experiment-runner")[0]
    report.check("6b the edge basis carries the message id (contract 5)",
                 message_id in (landed.get("basis") or ""), landed.get("basis"))
    report.check("6c a proposal whose endpoint does not exist is parked, not dropped",
                 len(edges.get("pending") or []) == 1
                 and (edges.get("pending_file") or "").endswith("pending_edges/%s.json" % message_id)
                 and rg.pending_summary(control)["edges"] == 1,
                 edges.get("pending"))
    report.check("6d the receipt on disk records the disposition",
                 ((tr.read_json(receipts_dir(control) / ("%s.json" % message_id), {}) or {})
                  .get("edges") or {}).get("proposed") == 2, "")
    report.check("6e `role status` shows PENDING_EDGES n",
                 any(ln.startswith("PENDING_EDGES 1") for ln in status_lines(control)),
                 status_lines(control))

    denied_dispatch = role_dispatch(control, "figure-engineer", "FIGURE_REQUEST")
    denied_reply = work / "reply_denied.json"
    tr.write_json(denied_reply, {
        "proposed_figure_requests": [], "routes": [], "qa_findings": [],
        "flags": [], "memory_delta": {},
        "proposed_edges": [
            {"from": "ro::run:proto-x1", "to": "ro::narr:%s" % node,
             "kind": "supported_by", "polarity": "+", "basis": "evidence is not mine"}]})
    denied_receipt = role_receipt(control, "figure-engineer", denied_dispatch["message_id"],
                                  str(denied_reply))
    report.check("6f an out-of-column proposal flags EDGE_CLASS_DENIED on the receipt",
                 "EDGE_CLASS_DENIED" in (denied_receipt.get("flags") or []),
                 denied_receipt.get("flags"))
    report.check("6g the denied proposal wrote no edge and no pending file",
                 not rg.list_edges(control, by="figure-engineer")
                 and rg.pending_summary(control)["edges"] == 1,
                 len(rg.list_edges(control, by="figure-engineer")))

    tr.write_json(control / "runs" / "proto-x2.json",
                  {"schema_version": "auto-research/run-v1", "run_id": "proto-x2",
                   "protocol": "proto", "status": "failed", "result": "arrived late"})
    rc, out, err, data = _st_run("research_graph.py",
                                 ["link", "--root", str(root), "--replay-pending"], home)
    report.check("6h `graph link --replay-pending` links what now resolves",
                 rc == 0 and len((data or {}).get("linked") or []) == 1,
                 (err or out)[:180])
    rg.cache_clear()
    report.check("6i the pending park is empty and the status line goes quiet",
                 rg.pending_summary(control)["edges"] == 0
                 and not any(ln.startswith("PENDING_EDGES") for ln in status_lines(control)),
                 rg.pending_summary(control))
    report.check("6j both parked and replayed edges are on the ledger under the role",
                 len(rg.list_edges(control, by="experiment-runner")) == 2,
                 [e["from"] for e in rg.list_edges(control, by="experiment-runner")])


def _t_memory_budget(control, work, report):
    """8.3: >12 KB refused; delta merges, never overwrites."""
    delta = work / "mem_delta.json"
    tr.write_json(delta, {"routes_seen": ["F-2"], "extra": {"a": 1}})
    memory_commit(control, "figure-engineer", str(delta))
    memory = tr.read_json(memory_path(control, "figure-engineer"), {})
    report.check("3a lists append with de-duplication",
                 memory.get("routes_seen") == ["F-1", "F-2"], memory.get("routes_seen"))
    report.check("3b untouched keys survive the merge",
                 memory.get("north_star") == "price of search", sorted(memory))
    tr.write_json(delta, {"extra": {"b": 2}})
    memory_commit(control, "figure-engineer", str(delta))
    memory = tr.read_json(memory_path(control, "figure-engineer"), {})
    report.check("3c dicts merge instead of replacing",
                 memory.get("extra") == {"a": 1, "b": 2}, memory.get("extra"))
    tr.write_json(delta, {"north_star": None})
    memory_commit(control, "figure-engineer", str(delta))
    report.check("3d a null value deletes the key",
                 "north_star" not in (tr.read_json(memory_path(control, "figure-engineer"), {})), "")
    oversized = work / "mem_big.json"
    ros.atomic_write(oversized, json.dumps({"blob": "x" * (MEMORY_MAX_BYTES + 64)}))
    raised, text, code = _st_gate(memory_commit, control, "figure-engineer", str(oversized))
    report.check("3e a delta over 12 KB is refused", raised and code == 1,
                 (text or "").splitlines()[0] if text else "")
    report.check("3f the refusal left the memory intact",
                 tr.read_json(memory_path(control, "figure-engineer"), {}).get("routes_seen")
                 == ["F-1", "F-2"], "")
    shown = memory_show(control, "figure-engineer")
    report.check("3g memory show reports size and stamp",
                 shown["bytes"] > 0 and shown["updated_at"], shown["bytes"])


def _tree_root_id(control):
    """The genesis root id, read from the committed tree (never hard-coded:
    narrative.py owns the id scheme)."""
    head = (control / "narrative" / "refs" / "heads" / "main").read_text(encoding="utf-8").strip()
    commit = tr.read_json(control / "narrative" / "commits" / ("%s.json" % head), {}) or {}
    tree = tr.read_json(control / "narrative" / "objects" / ("%s.json" % commit.get("tree")), {}) or {}
    return tree.get("root")


def _t_chronicle_receipt(control, root, work, report):
    """CHRONICLE_TURN goes through role receipt into the real narrative commit."""
    commits = control / "narrative" / "commits"
    before = len(list(commits.glob("*.json")))
    root_id = _tree_root_id(control)
    tr.turn_probe(control)          # a session opens its turn BEFORE the work happens
    ros.atomic_write(root / "裁决_headline.md", "# 裁决\n\nthe headline becomes functional.\n")
    dispatched = role_dispatch(control, "chronicler", "CHRONICLE_TURN")
    envelope = dispatched["envelope"]
    turn = tr.read_turn(control)
    manifest = tr.load_manifest(control, envelope["manifest_id"])
    native = tr.chronicle_envelope(control, turn, manifest)
    report.check("4a the CHRONICLE_TURN envelope keeps the narrative-contract 7 identity",
                 envelope["message_id"] == native["message_id"]
                 and envelope["idempotency_key"] == native["idempotency_key"]
                 and envelope["chronicle_turn"]["schema"] == "CHRONICLE_TURN/v1",
                 envelope["message_id"])
    report.check("4b the bearing manifest is bound to the dispatch",
                 manifest.get("narrative_bearing") is True
                 and envelope["turn_uuid"] == turn["turn_uuid"],
                 manifest.get("bearing_reasons"))

    reply = work / "reply_chronicle.json"
    node = {"id": "C-001", "node_type": "claim", "title": "the hurdle is a functional",
            "summary": "the hurdle is a functional", "statement": "The hurdle is a functional.",
            "identity_key": {"subject": "hurdle", "target": "search procedure",
                             "predicate": "is-functional", "quantifier": "all",
                             "domain": "supervised search", "conditions": [],
                             "polarity": "positive"},
            "state": {"epistemic": "PENDING", "narrative": "ACTIVE"},
            "state_meta": {"resolution_condition": "the grid closes", "live_bet": True}}
    tr.write_json(reply, {
        "proposed_ops": [{"op": "add_node", "parent": root_id, "node": node},
                         {"op": "set_role", "role": "headline", "id": "C-001"}],
        "commit_meta": {"proposed_kind": "refine", "cause_proposal": "unforced",
                        "trigger_ref": None, "trigger_class": "review-pressure",
                        "message": "the functional claim becomes the headline"},
        "flags": [], "memory_delta": {"last_headline": "C-001"},
        "idempotency_key": envelope["idempotency_key"]})
    receipt = role_receipt(control, "chronicler", envelope["message_id"], str(reply))
    narrative = receipt.get("narrative") or {}
    report.check("4c the receipt drove the real narrative commit transaction",
                 receipt["result"] == "ANSWERED"
                 and narrative.get("result") == "NARRATIVE_COMMITTED"
                 and len(narrative.get("commit_ids") or []) == 1, narrative)
    report.check("4d exactly one new commit landed",
                 len(list(commits.glob("*.json"))) == before + 1,
                 (len(list(commits.glob("*.json"))), before))
    report.check("4e the CLI recomputed the kind (set_role => restructure)",
                 narrative.get("kind") == "restructure" and narrative.get("kind_corrected") is True,
                 narrative.get("kind"))
    turn_receipt = tr.read_receipt(control, turn["turn_uuid"], envelope["manifest_id"])
    report.check("4f the turn receipt is the CLI-built NARRATIVE_COMMITTED",
                 (turn_receipt or {}).get("kind") == "NARRATIVE_COMMITTED",
                 (turn_receipt or {}).get("kind"))
    report.check("4g the chronicler memory merged once",
                 tr.read_json(memory_path(control, "chronicler"), {}).get("last_headline") == "C-001",
                 "")
    again = role_receipt(control, "chronicler", envelope["message_id"], str(reply))
    report.check("4h a repeated CHRONICLE_TURN receipt is ALREADY_ANSWERED with no second commit",
                 again["result"] == "ALREADY_ANSWERED"
                 and len(list(commits.glob("*.json"))) == before + 1,
                 again["result"])


def _t_session_restart(control, report):
    """8.4: after a session restart the fleet reports DISPATCH_UNANSWERED."""
    dispatched = role_dispatch(control, "theory-operator", "CLAIM_UPDATE")
    instance_set(control, "theory-operator", "agent-live")
    os.environ["AR_SESSION"] = "W801"        # a new session
    view = fleet_view(control)
    unanswered = [d["role"] for d in view["dispatch_unanswered"]]
    report.check("5a a new session still sees the unanswered dispatch",
                 unanswered == ["theory-operator"]
                 and view["dispatch_unanswered"][0]["message_id"] == dispatched["message_id"],
                 unanswered)
    report.check("5b instances do not survive the session boundary",
                 role_view(control, "theory-operator",
                           entry_of(load_registry(control), "theory-operator"))["instance"] is None)
    report.check("5c the role is hot and REHYDRATE_REQUIRED",
                 "theory-operator" in view["hot"]
                 and "theory-operator" in view["rehydrate_required"], view["hot"])
    lines = status_lines(control)
    report.check("5d status_lines prints the session-start digest",
                 any(ln.startswith("DISPATCH_UNANSWERED theory-operator") for ln in lines)
                 and any(ln.startswith("REHYDRATE_REQUIRED") for ln in lines), lines[:3])
    os.environ["AR_SESSION"] = "W800"


# ---------------------------------------------------------------- CLI (7)

def emit(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="role_runtime.py",
        description="auto-research role fleet: registry, dispatch, receipt, memory "
                    "(docs/role-fleet.md).")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_root(p):
        p.add_argument("--root", help="project root, .research-os dir, or any path inside the "
                                      "project (default: search upwards from cwd)")
        return p

    p_role = sub.add_parser("role", help="role fleet CLI")
    role_sub = p_role.add_subparsers(dest="action", required=True)

    with_root(role_sub.add_parser("list", help="the fleet and its derived state"))
    p_status = with_root(role_sub.add_parser("status", help="hot / unanswered / stale lines"))
    p_status.add_argument("--json", action="store_true")
    for name in ("enable", "disable"):
        p = with_root(role_sub.add_parser(name, help="%s a role" % name))
        p.add_argument("role")
    p_hyd = with_root(role_sub.add_parser("hydrate", help="rebuild a role instance (<=40 lines)"))
    p_hyd.add_argument("role", nargs="?", default="chronicler")

    p_dis = with_root(role_sub.add_parser("dispatch", help="build and register a ROLE_TASK/v1"))
    p_dis.add_argument("role")
    p_dis.add_argument("--kind", required=True)
    p_dis.add_argument("--k-task", dest="k_task")
    p_dis.add_argument("--refs", action="append",
                       help="repeatable or comma-separated; capped at %d" % MAX_REFS)
    p_dis.add_argument("--turn", default="current", help="'current' (default) or 'none'")
    p_dis.add_argument("--graph-context", dest="graph_context",
                       help="inline JSON object or @file")
    p_dis.add_argument("--objective", help="override the fixed per-kind objective")

    p_rec = with_root(role_sub.add_parser("receipt", help="validate and land a role reply"))
    p_rec.add_argument("role")
    p_rec.add_argument("--message-id", dest="message_id", required=True)
    p_rec.add_argument("--file", dest="file", required=True)
    p_rec.add_argument("--ref", default="main", help="narrative ref for CHRONICLE_TURN")

    p_inst = with_root(role_sub.add_parser("instance", help="session-scoped agent id"))
    inst_sub = p_inst.add_subparsers(dest="instop", required=True)
    p_inst_set = with_root(inst_sub.add_parser("set"))
    p_inst_set.add_argument("role")
    p_inst_set.add_argument("--agent-id", dest="agent_id", required=True)
    p_inst_clear = with_root(inst_sub.add_parser("clear"))
    p_inst_clear.add_argument("role")

    p_mem = p_role_memory(role_sub, with_root)
    del p_mem

    role_sub.add_parser("self-test", help="contract 8 acceptance 1-4 + chronicle + concurrency")
    return parser


def p_role_memory(role_sub, with_root):
    p_mem = role_sub.add_parser("memory", help="role project memory (<=12 KB)")
    mem_sub = p_mem.add_subparsers(dest="memop", required=True)
    p_commit = with_root(mem_sub.add_parser("commit"))
    p_commit.add_argument("role", nargs="?", default="chronicler")
    p_commit.add_argument("--delta-file", dest="delta_file", required=True)
    p_show = with_root(mem_sub.add_parser("show"))
    p_show.add_argument("role", nargs="?", default="chronicler")
    return p_mem


def cmd_role(args) -> int:
    if args.action == "self-test":
        return run_self_test()
    control = resolve_control(getattr(args, "root", None))
    action = args.action
    if action == "list":
        view = role_list(control)
        return emit(view)
    if action == "status":
        view = fleet_view(control)
        if getattr(args, "json", False):
            return emit(view)
        for line in render_status(view):
            print(line)
        return 0
    if action in ("enable", "disable"):
        return emit(role_set_enabled(control, args.role, action == "enable"))
    if action == "hydrate":
        result = role_hydrate(control, args.role)
        for line in result["lines"]:
            print(line)
        return 0
    if action == "dispatch":
        result = role_dispatch(control, args.role, args.kind, k_task=args.k_task,
                               refs=args.refs, turn=args.turn,
                               graph_context=args.graph_context, objective=args.objective)
        return emit(result)
    if action == "receipt":
        return emit(role_receipt(control, args.role, args.message_id, args.file, ref=args.ref))
    if action == "instance":
        if args.instop == "set":
            return emit(instance_set(control, args.role, args.agent_id))
        return emit(instance_clear(control, args.role))
    if action == "memory":
        if args.memop == "commit":
            return emit(memory_commit(control, args.role, args.delta_file))
        return emit(memory_show(control, args.role))
    raise GateError("unknown role action %r" % action, 2)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return cmd_role(args)
    except GateError as exc:
        print(exc.text)
        print(json.dumps({"ok": False, "error": exc.text.splitlines()[0],
                          "code": exc.code, "payload": exc.payload}, ensure_ascii=False),
              file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
