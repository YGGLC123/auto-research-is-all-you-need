#!/usr/bin/env python3
"""auto-research v2 control-plane CLI.

Single-file, stdlib-only. Owns the `.research-os/` project state:
bootstrap / validate / migrate (v1->v2) / update / event / blocker /
probe (capability registry) / context (resume digest) / doctor (package
self-check).

Write discipline: every mutation validates the candidate state first,
then commits via atomic replace. Exit codes: 0 ok, 1 validation/contract
failure, 2 environment or usage error.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_V1 = "research-pipeline-os/v1"
SCHEMA_V1_FAMILY = {SCHEMA_V1, "auto-research-v1/v1"}  # both observed in real projects
SCHEMA_V2 = "auto-research/v2"
PORTFOLIO_SCHEMA = "auto-research/portfolio-v1"

# Keyword heuristics for mapping free-form v1 stages back onto the canonical enum.
STAGE_FALLBACK = [
    (("rebuttal", "submission", "presubmission", "camera", "package"), "final_submission_or_rebuttal"),
    (("reject", "borderline", "rescue"), "rejected_or_borderline"),
    (("draft", "manuscript", "writing"), "manuscript_draft"),
    (("experiment", "benchmark", "run"), "experiments_underway"),
    (("method", "design", "architecture"), "method_taking_shape"),
    (("preliminary", "literature", "evidence", "feasib"), "preliminary_study"),
    (("idea", "topic"), "idea_only"),
]

STAGES = {
    "idea_only", "preliminary_study", "method_taking_shape",
    "experiments_underway", "manuscript_draft", "rejected_or_borderline",
    "final_submission_or_rebuttal",
}
PAPER_TYPES = {
    "theoretical_ml", "empirical_ml", "domain_application",
    "systems_tooling", "survey_position",
}
GATE_STATUSES = {"open", "passed", "failed", "blocked", "waived"}
BLOCKER_TYPES = {"permission", "capability", "user_decision", "external", "technical"}
LOOP_MODES = {"manual", "assisted", "autonomous"}
HOSTS = {"claude-code", "codex", "unknown"}
CAP_STATUSES = {"available", "missing", "degraded", "unprobed", "conversation_probe_required"}

REQUIRED_V2 = {
    "schema_version", "project_id", "title", "status", "stage", "stage_confidence",
    "paper_type", "objective", "active_workstream", "active_skill", "gates", "risks",
    "artifacts", "capability_needs", "capability_status", "authorization", "blockers",
    "loop", "host_log", "next_actions", "updated_at",
}
REQUIRED_V1 = {
    "schema_version", "project_id", "title", "status", "stage", "stage_confidence",
    "paper_type", "objective", "active_workstream", "active_skill", "gates", "risks",
    "artifacts", "capability_needs", "authorization", "next_actions", "updated_at",
}

SECRET_KEY = re.compile(r"(^|_)(token|cookie|password|credential|secret|private_key|api_key)($|_)", re.I)

GATE_PASS_WORDS = {"passed", "pass", "proven", "complete", "completed", "done", "ok", "green", "closed", "verified"}
GATE_FAIL_WORDS = {"failed", "fail", "refuted", "red"}
GATE_BLOCK_WORDS = {"blocked", "waiting", "pending_authorization"}


# ---------------------------------------------------------------- utilities

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:64] or "research-project"


def die(msg: str, code: int = 2) -> "None":
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(code)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib_suppress():
            os.unlink(tmp)
        raise


class contextlib_suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


LOCK_STALE_SECONDS = 120
LOCK_WAIT_SECONDS = 12


class hold_lock:
    """Cross-process write lease on a .research-os dir. Every mutating CLI
    command runs inside this (enforced in main), so two hosts/windows writing
    concurrently queue instead of corrupting. Stale leases are taken over."""

    def __init__(self, control: Path):
        self.path = control / ".lock"
        self.acquired = False

    def __enter__(self):
        import time
        deadline = time.time() + LOCK_WAIT_SECONDS
        payload = json.dumps({"host": detect_host(), "pid": os.getpid(), "ts": utc_now()})
        while True:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                self.acquired = True
                return self
            except FileExistsError:
                try:
                    holder = json.loads(self.path.read_text(encoding="utf-8"))
                    held_at = datetime.fromisoformat(holder.get("ts", "").replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - held_at).total_seconds()
                except (json.JSONDecodeError, ValueError, OSError):
                    holder, age = {}, LOCK_STALE_SECONDS + 1
                if age > LOCK_STALE_SECONDS:
                    with contextlib_suppress():
                        self.path.unlink()
                    continue
                if time.time() > deadline:
                    die(f"write lock held by {holder.get('host')}#{holder.get('pid')} "
                        f"(age {int(age)}s): another session is writing this project; retry shortly", 1)
                time.sleep(0.25)

    def __exit__(self, *exc):
        if self.acquired:
            with contextlib_suppress():
                self.path.unlink()
        return False


def read_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"{path.name} is not valid JSON: {exc}", 1)


def sessions_path(control: Path) -> Path:
    return control / "sessions.json"


def cursors_path(control: Path) -> Path:
    return control / "cursors.json"


def external_session_id() -> str | None:
    return os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CODEX_SESSION_ID") or None


def current_session_id(control: Path) -> str | None:
    """Resolve the calling session's W## id.

    Order: explicit AR_SESSION override -> external-id match -> single-active-
    for-this-host fallback. The fallback closes the headless attribution gap
    found in the usage trial (`claude -p` children see no CLAUDE_SESSION_ID,
    so every event fell to session=None): when exactly one active badge exists
    for this host, attribution is unambiguous. Two concurrent same-host
    sessions stay ambiguous and resolve to None — never guess between badges."""
    explicit = os.environ.get("AR_SESSION")
    if explicit:
        return explicit
    data = read_json_file(sessions_path(control), {"counter": 0, "sessions": []})
    sessions = data.get("sessions", [])
    external = external_session_id()
    if external:
        for entry in sessions:
            if entry.get("external_id") == external and entry.get("status") == "active":
                return entry.get("id")
    host = detect_host()
    active_here = [e for e in sessions if e.get("status") == "active" and e.get("host") == host]
    if len(active_here) == 1:
        return active_here[0].get("id")
    return None


def locate_control(target: Path) -> Path:
    """Resolve a project root / .research-os dir / state.json path to the control dir."""
    target = target.expanduser().resolve()
    if target.is_file():
        return target.parent
    if target.name == ".research-os":
        return target
    return target / ".research-os"


def find_project_upwards(start: Path) -> Path | None:
    cur = start.expanduser().resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".research-os" / "state.json").exists():
            return candidate / ".research-os"
    return None


def load_state(control: Path) -> dict:
    state_path = control / "state.json"
    if not state_path.exists():
        die(f"no state.json under {control}")
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"state.json is not valid JSON: {exc}", 1)
    raise AssertionError  # unreachable


def detect_host(explicit: str = "auto") -> str:
    if explicit != "auto":
        return explicit
    env = os.environ
    if env.get("AR_HOST") in HOSTS:
        return env["AR_HOST"]
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE") or env.get("CLAUDE_PROJECT_DIR") or env.get("CLAUDE_SESSION_ID"):
        return "claude-code"
    if any(key.startswith("CODEX") for key in env):
        return "codex"
    return "unknown"


def find_secret_keys(value, prefix="") -> list:
    hits = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if SECRET_KEY.search(str(key)):
                hits.append(path)
            hits.extend(find_secret_keys(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(find_secret_keys(item, f"{prefix}[{index}]"))
    return hits


# ---------------------------------------------------------------- validation

def validate_events(control: Path) -> list:
    errors = []
    events_path = control / "events.jsonl"
    if events_path.exists():
        for line_no, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict) or "ts" not in event or "event" not in event:
                    errors.append(f"events.jsonl line {line_no} lacks ts/event")
            except json.JSONDecodeError as exc:
                errors.append(f"events.jsonl line {line_no} invalid JSON: {exc.msg}")
    return errors


def validate_state_dict(state: dict, control: Path | None = None) -> tuple:
    """Return (errors, warnings). v1-family states are validated leniently:
    real-world v1 states drifted (free-form stages, >3 next_actions), so enum
    drift is a warning there and only structural damage is an error. v2 is
    strict — we never write drift forward."""
    errors: list = []
    warnings: list = []
    schema = state.get("schema_version")
    is_v1 = schema in SCHEMA_V1_FAMILY
    if is_v1:
        required = REQUIRED_V1
        soft = warnings
    elif schema == SCHEMA_V2:
        required = REQUIRED_V2
        soft = errors
    else:
        return ([f"unsupported schema_version: {schema!r} "
                 f"(expected one of {sorted(SCHEMA_V1_FAMILY)} or {SCHEMA_V2})"], [])

    missing = sorted(required - state.keys())
    if missing:
        # v1-family states in the wild omit fields; migrate fills them.
        (warnings if is_v1 else errors).append("missing fields: " + ", ".join(missing))
    if state.get("stage") not in STAGES:
        soft.append(f"non-canonical stage {state.get('stage')!r}")
    if state.get("paper_type") not in PAPER_TYPES:
        soft.append(f"non-canonical paper_type {state.get('paper_type')!r}")
    confidence = state.get("stage_confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        soft.append("stage_confidence must be between 0 and 1")
    next_actions = state.get("next_actions")
    if not isinstance(next_actions, list):
        errors.append("next_actions must be a list")
    elif len(next_actions) > 3:
        soft.append("next_actions must have at most three entries (overflow belongs in backlog)")
    elif schema == SCHEMA_V2:
        for i, item in enumerate(next_actions):
            if isinstance(item, str):
                continue
            if not isinstance(item, dict) or "action" not in item:
                errors.append(f"next_actions[{i}] must be a string or an object with 'action'")
    authorization = state.get("authorization")
    if not isinstance(authorization, dict):
        errors.append("authorization must be an object")
    elif authorization.get("drive_upload") != "confirm_each_action":
        errors.append("authorization.drive_upload must be confirm_each_action")

    if schema == SCHEMA_V2:
        gates = state.get("gates")
        if not isinstance(gates, dict):
            errors.append("gates must be an object")
        else:
            for gid, gate in gates.items():
                if not isinstance(gate, dict):
                    errors.append(f"gate {gid} must be an object")
                    continue
                if gate.get("status") not in GATE_STATUSES:
                    errors.append(f"gate {gid} has invalid status {gate.get('status')!r}")
                if gate.get("status") == "blocked" and not gate.get("blocker_id"):
                    errors.append(f"gate {gid} is blocked but has no blocker_id")
        blockers = state.get("blockers")
        if not isinstance(blockers, list):
            errors.append("blockers must be a list")
        else:
            seen = set()
            for blocker in blockers:
                if not isinstance(blocker, dict):
                    errors.append("each blocker must be an object")
                    continue
                bid = blocker.get("id")
                if not bid or bid in seen:
                    errors.append(f"blocker id missing or duplicated: {bid!r}")
                seen.add(bid)
                if blocker.get("type") not in BLOCKER_TYPES:
                    errors.append(f"blocker {bid} has invalid type {blocker.get('type')!r}")
                if not blocker.get("description"):
                    errors.append(f"blocker {bid} lacks description")
        loop = state.get("loop")
        if not isinstance(loop, dict) or loop.get("mode") not in LOOP_MODES:
            errors.append("loop.mode must be one of " + "/".join(sorted(LOOP_MODES)))
        else:
            budget = loop.get("stagnation_budget")
            if not isinstance(budget, int) or budget < 1:
                errors.append("loop.stagnation_budget must be a positive integer")
        caps = state.get("capability_status")
        if not isinstance(caps, dict):
            errors.append("capability_status must be an object")
        else:
            for cid, cap in caps.items():
                if not isinstance(cap, dict) or cap.get("status") not in CAP_STATUSES:
                    errors.append(f"capability_status[{cid}] has invalid status")
        if not isinstance(state.get("host_log"), list):
            errors.append("host_log must be a list")
        for key in ("visual_plan", "visual_delivery", "visual_integration"):
            if key in state and not isinstance(state[key], (dict, list)):
                errors.append(f"{key} must be an object or list when present")
        if "backlog" in state and not isinstance(state["backlog"], list):
            errors.append("backlog must be a list when present")
        if "stage_detail" in state and not isinstance(state["stage_detail"], str):
            errors.append("stage_detail must be a string when present")
        versions = state.get("versions", {})
        if not isinstance(versions, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in versions.items()):
            errors.append("versions must be a string->string map when present")
        artifacts = state.get("artifacts")
        if isinstance(artifacts, list):
            for i, art in enumerate(artifacts):
                if isinstance(art, str):
                    continue
                if not isinstance(art, dict) or not art.get("path"):
                    errors.append(f"artifacts[{i}] must be a string or an object with 'path'")
                elif "tags" in art and not isinstance(art["tags"], list):
                    errors.append(f"artifacts[{i}].tags must be a list")

    secret_keys = find_secret_keys(state)
    if secret_keys:
        errors.append("secret-like keys are forbidden: " + ", ".join(secret_keys))
    if control is not None:
        errors.extend(validate_events(control))
    return errors, warnings


def commit_state(control: Path, state: dict) -> None:
    state["updated_at"] = utc_now()
    errors, warnings = validate_state_dict(state, control=None)
    if state.get("schema_version") == SCHEMA_V2:
        errors = errors + warnings  # write discipline: v2 is strict on write
    if errors:
        die("refusing to write invalid state: " + "; ".join(errors), 1)
    atomic_write(control / "state.json", json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def read_events(control: Path) -> list:
    path = control / "events.jsonl"
    events = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def append_event(control: Path, event: dict) -> None:
    """Append with a monotonic seq (cursor anchor) and the caller's session id."""
    existing = read_events(control)
    best = 0
    for entry in existing:
        if isinstance(entry.get("seq"), int):
            best = max(best, entry["seq"])
    if best == 0:
        best = len(existing)
    event = {"ts": utc_now(), "seq": best + 1, **event}
    if "event" not in event:
        die("event requires an 'event' field")
    session = current_session_id(control)
    if session and "session" not in event:
        event["session"] = session
    path = control / "events.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- migrate

def normalize_gate(value) -> dict:
    now = utc_now()
    if isinstance(value, dict):
        status = str(value.get("status", "")).lower()
        gate = {
            "status": status if status in GATE_STATUSES else "open",
            "owner_skill": value.get("owner_skill", ""),
            "evidence": value.get("evidence", json.dumps(value, ensure_ascii=False)),
            "updated_at": value.get("updated_at", now),
        }
        if value.get("blocker_id"):
            gate["blocker_id"] = value["blocker_id"]
        if gate["status"] == "blocked" and "blocker_id" not in gate:
            gate["status"] = "open"
            gate["evidence"] += " [migrated: was blocked without blocker_id]"
        return gate
    text = str(value).strip()
    lowered = text.lower()
    if lowered in GATE_STATUSES:
        status = lowered
    elif lowered in GATE_PASS_WORDS or any(w in lowered for w in ("pass", "proven", "complete", "verified")):
        status = "passed"
    elif lowered in GATE_FAIL_WORDS or "fail" in lowered:
        status = "failed"
    elif lowered in GATE_BLOCK_WORDS or "block" in lowered:
        status = "open"  # cannot mint a blocker retroactively; keep visible as open
    else:
        status = "open"
    return {"status": status, "owner_skill": "", "evidence": text, "updated_at": now}


def canonical_stage(raw) -> tuple:
    """Map a possibly free-form v1 stage onto the canonical enum.
    Returns (stage, stage_detail_or_None)."""
    text = str(raw or "").strip()
    if text in STAGES:
        return text, None
    lowered = text.lower()
    for keywords, target in STAGE_FALLBACK:
        if any(word in lowered for word in keywords):
            return target, text
    return "preliminary_study", text or None


def migrate_v1(state: dict) -> dict:
    out = dict(state)
    out["schema_version"] = SCHEMA_V2
    # Real v1 states omit fields freely; fill every v2-required field.
    title = state.get("title") or state.get("project_id") or "untitled-research-project"
    out.setdefault("title", title)
    out.setdefault("project_id", slugify(str(title)))
    out.setdefault("status", "active")
    out.setdefault("objective", "")
    out.setdefault("active_workstream", "")
    out.setdefault("active_skill", "pilot")
    out.setdefault("risks", [])
    out.setdefault("artifacts", [])
    out.setdefault("capability_needs", [])
    out.setdefault("target_venue", "")
    authorization = out.get("authorization")
    if not isinstance(authorization, dict):
        authorization = {}
    authorization.setdefault("github_scope", None)
    authorization.setdefault("external_library_scope", None)
    authorization["drive_upload"] = "confirm_each_action"
    out["authorization"] = authorization
    stage, detail = canonical_stage(state.get("stage"))
    out["stage"] = stage
    if detail:
        out["stage_detail"] = detail
    if state.get("paper_type") not in PAPER_TYPES:
        out["paper_type"] = "empirical_ml"
        out.setdefault("risks", []).append(
            f"migration: original paper_type {state.get('paper_type')!r} was non-canonical")
    confidence = state.get("stage_confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        out["stage_confidence"] = 0.5
    out["gates"] = {gid: normalize_gate(value) for gid, value in (state.get("gates") or {}).items()}
    out.setdefault("capability_status", {})
    out.setdefault("blockers", [])
    out.setdefault("host_log", [])
    out.setdefault("loop", {
        "mode": "manual", "wakeup_pending": False, "last_tick": None,
        "stagnation_count": 0, "stagnation_budget": 3, "stop_conditions": [],
    })
    actions = []
    for item in state.get("next_actions") or []:
        actions.append(item if isinstance(item, (str, dict)) else str(item))
    out["next_actions"] = actions[:3]
    if len(actions) > 3:
        out["backlog"] = list(out.get("backlog") or []) + actions[3:]
    return out


# ---------------------------------------------------------------- commands

def cmd_bootstrap(args) -> int:
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    control = root / ".research-os"
    control.mkdir(exist_ok=True)
    created = []
    now = utc_now()

    def write_new(path: Path, text: str) -> None:
        if path.exists():
            return
        atomic_write(path, text)
        created.append(path.relative_to(root).as_posix())

    state = {
        "schema_version": SCHEMA_V2,
        "project_id": slugify(args.title),
        "title": args.title,
        "status": "active",
        "stage": args.stage,
        "stage_confidence": 1.0,
        "paper_type": args.paper_type,
        "target_venue": args.target_venue,
        "objective": args.objective,
        "active_workstream": "intake",
        "active_skill": "research-bootstrap",
        "gates": {},
        "risks": [],
        "artifacts": [],
        "capability_needs": [],
        "capability_status": {},
        "authorization": {
            "github_scope": None,
            "external_library_scope": None,
            "drive_upload": "confirm_each_action",
        },
        "blockers": [],
        "loop": {
            "mode": "manual", "wakeup_pending": False, "last_tick": None,
            "stagnation_count": 0, "stagnation_budget": 3, "stop_conditions": [],
        },
        "host_log": [{"host": detect_host(), "last_active": now}],
        "next_actions": ["Complete the intake brief and select the first phase gate."],
        "updated_at": now,
    }
    if not (control / "state.json").exists():
        errors, warnings = validate_state_dict(state)
        if errors or warnings:
            die("bootstrap produced invalid state: " + "; ".join(errors + warnings), 1)
        write_new(control / "state.json", json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        append_event(control, {
            "event": "project_bootstrapped", "stage": args.stage,
            "summary": "Created the auto-research v2 project control plane.",
            "artifacts": created.copy(),
        })
        created.append(".research-os/events.jsonl")
    write_new(control / "decisions.md",
              "# Research Decisions\n\nRecord only durable, dated decisions and their evidence.\n")

    dirs = ["research/evidence", "research/claims", "research/protocols", "research/reviews", "research/notes"]
    if args.layout == "standard":
        dirs += ["paper/figures", "paper/tables", "src", "tests", "configs",
                 "data/raw", "data/processed", "results"]
    for rel in dirs:
        path = root / rel
        if not path.exists():
            path.mkdir(parents=True)
            created.append(rel + "/")

    git_status = "not_requested"
    if args.init_git:
        if (root / ".git").exists():
            git_status = "already_initialized"
        else:
            try:
                proc = subprocess.run(["git", "init", str(root)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                git_status = "initialized" if proc.returncode == 0 else "failed: " + proc.stderr.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                git_status = "failed: " + str(exc)

    portfolio_note = "registered"
    try:  # best-effort: a portfolio hiccup must never fail a bootstrap
        portfolio_upsert(root, state)
    except (Exception, SystemExit) as exc:
        portfolio_note = f"skipped: {exc}"
    print(json.dumps({"ok": True, "root": str(root), "created": created, "git": git_status,
                      "portfolio": portfolio_note},
                     ensure_ascii=False, indent=2))
    return 0


def validate_tree_file(control: Path) -> list:
    errors = []
    path = control / "tree.json"
    if not path.exists():
        return errors
    try:
        tree = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"tree.json invalid JSON: {exc.msg}"]
    if tree.get("schema_version") != TREE_SCHEMA:
        errors.append(f"tree.json unsupported schema {tree.get('schema_version')!r}")
        return errors
    nodes = tree.get("nodes", {})
    for code, node in nodes.items():
        if not valid_code(code):
            errors.append(f"tree node {code!r} violates the code grammar")
        if not isinstance(node, dict):
            errors.append(f"tree node {code} must be an object")
            continue
        if node.get("status") not in NODE_STATUSES:
            errors.append(f"tree node {code} has invalid status {node.get('status')!r}")
        parent = node.get("parent")
        if parent and parent not in nodes:
            errors.append(f"tree node {code} references unknown parent {parent!r}")
        if parent and not code.startswith(parent + "."):
            errors.append(f"tree node {code} does not extend its parent code {parent}")
        if node.get("status") in NODE_TERMINAL and not node.get("result") and node.get("status") not in ("superseded", "abandoned"):
            errors.append(f"tree node {code} closed without a result")
        if node.get("status") == "blocked" and not node.get("blocker_id"):
            errors.append(f"tree node {code} blocked without blocker_id")
    return errors


def cmd_validate(args) -> int:
    control = locate_control(args.target)
    state = load_state(control)
    errors, warnings = validate_state_dict(state, control=control)
    errors.extend(validate_tree_file(control))
    for side in ("sessions.json", "cursors.json"):
        side_path = control / side
        if side_path.exists():
            try:
                json.loads(side_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"{side} invalid JSON: {exc.msg}")
    print(json.dumps({"valid": not errors, "schema": state.get("schema_version"),
                      "state": str(control / "state.json"), "errors": errors,
                      "warnings": warnings},
                     ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def cmd_migrate(args) -> int:
    control = locate_control(args.target)
    state = load_state(control)
    schema = state.get("schema_version")
    if schema == SCHEMA_V2:
        print(json.dumps({"ok": True, "migrated": False, "note": "already v2"}, ensure_ascii=False))
        return 0
    if schema not in SCHEMA_V1_FAMILY:
        die(f"cannot migrate unknown schema {schema!r}", 1)
    migrated = migrate_v1(state)
    errors, warnings = validate_state_dict(migrated)
    if errors or warnings:
        die("migration produced invalid state: " + "; ".join(errors + warnings), 1)
    if args.dry_run:
        print(json.dumps({"ok": True, "migrated": "dry-run", "result": migrated}, ensure_ascii=False, indent=2))
        return 0
    backup = control / "state.v1.bak.json"
    if not backup.exists():
        shutil.copy2(control / "state.json", backup)
    commit_state(control, migrated)
    append_event(control, {"event": "schema_migrated", "summary": f"{schema} -> {SCHEMA_V2}",
                           "artifacts": [".research-os/state.v1.bak.json"]})
    portfolio_note = "registered"
    try:  # best-effort: a portfolio hiccup must never fail a migration
        portfolio_upsert(control.parent, migrated)
    except (Exception, SystemExit) as exc:
        portfolio_note = f"skipped: {exc}"
    print(json.dumps({"ok": True, "migrated": True, "backup": str(backup),
                      "portfolio": portfolio_note}, ensure_ascii=False))
    return 0


def parse_kv(pairs, what) -> dict:
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            die(f"bad {what} {pair!r}: expected key=value")
        key, value = pair.split("=", 1)
        out[key.strip()] = value.strip()
    return out


SETTABLE = {"stage", "paper_type", "status", "objective", "active_workstream",
            "active_skill", "target_venue", "stage_confidence"}


def cmd_update(args) -> int:
    control = locate_control(args.root)
    state = load_state(control)
    if state.get("schema_version") in SCHEMA_V1_FAMILY:
        die("state is v1; run migrate first", 1)
    changed = []

    for key, value in parse_kv(args.set, "--set").items():
        if key not in SETTABLE:
            die(f"--set {key} not allowed; settable: {sorted(SETTABLE)}")
        state[key] = float(value) if key == "stage_confidence" else value
        changed.append(f"set {key}")

    for spec in args.gate or []:
        # id=status[:evidence][@owner_skill][#blocker_id]
        if "=" not in spec:
            die(f"bad --gate {spec!r}: expected id=status[:evidence][@owner][#blocker_id]")
        gid, rest = spec.split("=", 1)
        blocker_id = None
        if "#" in rest:
            rest, blocker_id = rest.rsplit("#", 1)
        owner = ""
        if "@" in rest:
            rest, owner = rest.rsplit("@", 1)
        status, _, evidence = rest.partition(":")
        if status not in GATE_STATUSES:
            die(f"gate status {status!r} invalid; allowed: {sorted(GATE_STATUSES)}")
        gate = state.setdefault("gates", {}).get(gid.strip(), {})
        gate.update({"status": status, "updated_at": utc_now()})
        if evidence:
            gate["evidence"] = evidence
        gate.setdefault("evidence", "")
        if owner:
            gate["owner_skill"] = owner
        gate.setdefault("owner_skill", state.get("active_skill", ""))
        if blocker_id:
            gate["blocker_id"] = blocker_id
        state["gates"][gid.strip()] = gate
        changed.append(f"gate {gid.strip()}={status}")

    if args.clear_next:
        state["next_actions"] = []
        changed.append("cleared next_actions")
    for action in args.next or []:
        owner = None
        if "@" in action:
            action, owner = action.rsplit("@", 1)
        entry = {"action": action.strip()}
        if owner:
            entry["owner_skill"] = owner.strip()
        state.setdefault("next_actions", []).append(entry)
        changed.append("next_action added")
    if len(state.get("next_actions", [])) > 3:
        die("next_actions would exceed 3; use --clear-next or trim first", 1)

    for spec in args.visual or []:
        # visual_plan|visual_delivery|visual_integration=<json object>
        if "=" not in spec:
            die(f"bad --visual {spec!r}: expected visual_key=<json>")
        key, raw = spec.split("=", 1)
        key = key.strip()
        if key not in ("visual_plan", "visual_delivery", "visual_integration"):
            die("--visual key must be visual_plan, visual_delivery, or visual_integration")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            die(f"--visual {key}: value is not valid JSON ({exc.msg})")
        if not isinstance(value, (dict, list)):
            die(f"--visual {key}: value must be a JSON object or list")
        state[key] = value
        changed.append(f"visual {key}")

    for spec in args.version or []:
        # track=label, e.g. manuscript=v12
        if "=" not in spec:
            die(f"bad --version {spec!r}: expected track=label")
        track, label = spec.split("=", 1)
        state.setdefault("versions", {})[track.strip()] = label.strip()
        changed.append(f"version {track.strip()}={label.strip()}")

    if args.loop_mode:
        state.setdefault("loop", {})["mode"] = args.loop_mode
        changed.append(f"loop.mode={args.loop_mode}")
    if args.tick:
        loop = state.setdefault("loop", {})
        loop["last_tick"] = utc_now()
        loop["stagnation_count"] = args.stagnation if args.stagnation is not None else loop.get("stagnation_count", 0)
        changed.append("loop tick")
    if args.wakeup is not None:
        state.setdefault("loop", {})["wakeup_pending"] = bool(int(args.wakeup))
        changed.append(f"wakeup_pending={bool(int(args.wakeup))}")

    host = detect_host(args.host)
    log = state.setdefault("host_log", [])
    for entry in log:
        if entry.get("host") == host:
            entry["last_active"] = utc_now()
            break
    else:
        log.append({"host": host, "last_active": utc_now()})

    if not changed:
        die("update called with nothing to change")
    commit_state(control, state)
    print(json.dumps({"ok": True, "changed": changed}, ensure_ascii=False))
    return 0


RESERVED_EVENT_PREFIXES = ("tree_", "session_", "task_", "run_", "claim_", "ledger_",
                           "blocker_", "asset_", "snapshot_", "schema_")


def cmd_event(args) -> int:
    control = locate_control(args.root)
    state = load_state(control)
    if args.type.startswith(RESERVED_EVENT_PREFIXES):
        die(f"event type {args.type!r} is reserved: those events are minted only by their owning "
            "commands (tree/session/task/run/claim/ledger/blocker/asset/snapshot/migrate). "
            "Narrate with a non-reserved type, e.g. note_* or phase_*", 1)
    event = {"event": args.type, "summary": args.summary, "stage": state.get("stage")}
    if args.artifact:
        event["artifacts"] = args.artifact
    extra = parse_kv(args.data, "--data")
    for key, value in extra.items():
        if key in event:
            die(f"--data key {key} collides with a core event field")
        event[key] = value
    append_event(control, event)
    print(json.dumps({"ok": True, "event": args.type}, ensure_ascii=False))
    return 0


def cmd_blocker(args) -> int:
    control = locate_control(args.root)
    state = load_state(control)
    if state.get("schema_version") in SCHEMA_V1_FAMILY:
        die("state is v1; run migrate first", 1)
    blockers = state.setdefault("blockers", [])
    if args.action == "list":
        print(json.dumps({"ok": True, "blockers": blockers}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "add":
        if not args.type or not args.description:
            die("blocker add requires --type and --description")
        bid = f"blk-{sum(1 for _ in blockers) + 1:03d}"
        existing = {b.get("id") for b in blockers}
        counter = len(blockers) + 1
        while bid in existing:
            counter += 1
            bid = f"blk-{counter:03d}"
        blocker = {
            "id": bid, "type": args.type, "description": args.description,
            "queued_action": args.queued_action or "", "raised_at": utc_now(),
            "resolved_at": None,
        }
        blockers.append(blocker)
        commit_state(control, state)
        append_event(control, {"event": "blocker_raised", "summary": f"{bid}: {args.description}",
                               "blocker_id": bid, "blocker_type": args.type})
        print(json.dumps({"ok": True, "id": bid}, ensure_ascii=False))
        return 0
    if args.action == "resolve":
        if not args.id:
            die("blocker resolve requires --id")
        for blocker in blockers:
            if blocker.get("id") == args.id:
                if blocker.get("resolved_at"):
                    die(f"blocker {args.id} already resolved", 1)
                blocker["resolved_at"] = utc_now()
                if args.note:
                    blocker["resolution_note"] = args.note
                commit_state(control, state)
                append_event(control, {"event": "blocker_resolved", "summary": f"{args.id}: {args.note or 'resolved'}",
                                       "blocker_id": args.id})
                print(json.dumps({"ok": True, "id": args.id}, ensure_ascii=False))
                return 0
        die(f"blocker {args.id} not found", 1)
    die(f"unknown blocker action {args.action}")
    return 2


# ---------------------------------------------------------------- attempt tree

TREE_SCHEMA = "auto-research/tree-v1"
NODE_KINDS = {  # kind -> code letter (the strict coding legend)
    "route": "R", "attempt": "A", "claim": "C",
    "experiment": "E", "verification": "V", "decision": "D",
}
NODE_TERMINAL = {"success", "failed", "proven", "refuted", "blocked",
                 "abandoned", "superseded", "inconclusive"}
NODE_STATUSES = {"registered", "running"} | NODE_TERMINAL
CODE_SEG = re.compile(r"^[A-Z]{1,2}\d{2,}$")
STATUS_GLYPH = {
    "registered": "·", "running": ">", "success": "+", "proven": "+",
    "failed": "x", "refuted": "x", "blocked": "#", "abandoned": "~",
    "superseded": "~", "inconclusive": "?",
}


def tree_path(control: Path) -> Path:
    return control / "tree.json"


def load_tree(control: Path) -> dict:
    path = tree_path(control)
    if not path.exists():
        return {"schema_version": TREE_SCHEMA, "legend": dict(NODE_KINDS), "nodes": {}}
    try:
        tree = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"tree.json is not valid JSON: {exc}", 1)
    if tree.get("schema_version") != TREE_SCHEMA:
        die(f"unsupported tree schema {tree.get('schema_version')!r}", 1)
    return tree


def save_tree(control: Path, tree: dict) -> None:
    atomic_write(tree_path(control), json.dumps(tree, ensure_ascii=False, indent=2) + "\n")
    render_attempts_md(control, tree)


def valid_code(code: str) -> bool:
    return bool(code) and all(CODE_SEG.match(seg) for seg in code.split("."))


def next_code(tree: dict, parent: str, letter: str) -> str:
    prefix = f"{parent}." if parent else ""
    best = 0
    for code in tree["nodes"]:
        if not code.startswith(prefix):
            continue
        rest = code[len(prefix):]
        if "." in rest or not rest.startswith(letter):
            continue
        try:
            best = max(best, int(rest[len(letter):]))
        except ValueError:
            continue
    return f"{prefix}{letter}{best + 1:02d}"


def node_sort_key(code: str):
    key = []
    for seg in code.split("."):
        match = re.match(r"([A-Z]+)(\d+)", seg)
        key.append((match.group(1), int(match.group(2))) if match else (seg, 0))
    return key


def render_attempts_md(control: Path, tree: dict) -> None:
    """Re-render the human master table. Called on every mutation: ATTEMPTS.md
    is always current, so no reader ever depends on conversation memory."""
    nodes = tree.get("nodes", {})
    lines = ["# Attempt Tree (auto-generated — edit via `research_os.py tree`, never by hand)", ""]
    legend = tree.get("legend", NODE_KINDS)
    lines.append("Legend: " + ", ".join(f"`{letter}`={kind}" for kind, letter in sorted(legend.items(), key=lambda kv: kv[1]))
                 + " · glyphs: " + " ".join(f"`{g}`={s}" for s, g in
                                            [("registered", "·"), ("running", ">"), ("success/proven", "+"),
                                             ("failed/refuted", "x"), ("blocked", "#"),
                                             ("abandoned/superseded", "~"), ("inconclusive", "?")]))
    lines.append("")
    lines.append("## Tree")
    lines.append("")
    lines.append("```")
    for code in sorted(nodes, key=node_sort_key):
        node = nodes[code]
        depth = code.count(".")
        glyph = STATUS_GLYPH.get(node.get("status"), "?")
        result = f"  => {node['result']}" if node.get("result") else ""
        lines.append(f"{'  ' * depth}[{glyph}] {code} {node.get('title', '')}{result}")
    lines.append("```")
    lines.append("")
    lines.append("## Master table")
    lines.append("")
    lines.append("| code | kind | title | status | result | evidence | owner | registered | closed |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for code in sorted(nodes, key=node_sort_key):
        node = nodes[code]
        evidence = "; ".join(node.get("evidence") or [])
        lines.append("| " + " | ".join([
            f"`{code}`", node.get("kind", ""),
            str(node.get("title", "")).replace("|", "\\|"),
            node.get("status", ""),
            str(node.get("result", "")).replace("|", "\\|"),
            evidence.replace("|", "\\|"), node.get("owner_skill", ""),
            (node.get("registered_at") or "")[:10], (node.get("closed_at") or "")[:10],
        ]) + " |")
    counts = {}
    for node in nodes.values():
        counts[node.get("status", "?")] = counts.get(node.get("status", "?"), 0) + 1
    lines.append("")
    lines.append("Totals: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "empty"))
    atomic_write(control / "ATTEMPTS.md", "\n".join(lines) + "\n")


# ============================================================ v3 §2  real-time derivation

GRAPH_RULES_TREE_CLOSE = ("tree-close",)
GRAPH_RULES_ASSET = ("asset-node",)


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


def cmd_tree(args) -> int:
    control = locate_control(args.root)
    if not (control / "state.json").exists():
        die(f"no project state under {control}; bootstrap first")
    tree = load_tree(control)
    nodes = tree["nodes"]

    if args.action == "register":
        if not args.kind or args.kind not in NODE_KINDS:
            die(f"--kind required; one of {sorted(NODE_KINDS)}")
        if not args.title:
            die("--title required: a node must say what is being tried")
        parent = (args.parent or "").strip()
        if parent:
            if parent not in nodes:
                die(f"parent {parent!r} is not registered", 1)
            if nodes[parent].get("status") in NODE_TERMINAL and not args.force:
                die(f"parent {parent} is closed ({nodes[parent]['status']}); pass --force to branch off a closed node", 1)
        code = args.code or next_code(tree, parent, NODE_KINDS[args.kind])
        if not valid_code(code):
            die(f"code {code!r} violates the grammar: dot-separated segments of LETTER(S)+2-digit number, e.g. R01.A03")
        if code in nodes:
            die(f"code {code} already registered", 1)
        if parent and not code.startswith(parent + "."):
            die(f"code {code} must extend its parent {parent}")
        node = {
            "code": code, "kind": args.kind, "parent": parent or None,
            "title": args.title, "goal": args.goal or "",
            "owner_skill": args.owner or "", "tags": sorted(set(args.tag or [])),
            "status": "registered", "registered_at": utc_now(),
            "started_at": None, "closed_at": None, "result": "", "evidence": [],
        }
        nodes[code] = node
        save_tree(control, tree)
        append_event(control, {"event": "tree_registered", "summary": f"{code}: {args.title}",
                               "node": code, "kind": args.kind})
        print(json.dumps({"ok": True, "code": code, "node": node}, ensure_ascii=False, indent=2))
        return 0

    if args.action in ("start", "close", "note"):
        graph = None
        code = args.code
        if not code or code not in nodes:
            die(f"unknown node {code!r}; register it first — nothing runs unregistered", 1)
        node = nodes[code]
        if args.action == "start":
            if node["status"] != "registered":
                die(f"{code} is {node['status']}; only a registered node can start", 1)
            node["status"] = "running"
            node["started_at"] = utc_now()
            save_tree(control, tree)
            append_event(control, {"event": "tree_started", "summary": f"{code}: {node['title']}", "node": code})
        elif args.action == "close":
            if args.status not in NODE_TERMINAL:
                die(f"--status required; one of {sorted(NODE_TERMINAL)}")
            if node["status"] not in ("running", "registered"):
                die(f"{code} already closed ({node['status']})", 1)
            if node["status"] == "registered" and not args.force:
                die(f"{code} never started; pass --force to close without running (e.g. superseded)", 1)
            if args.status == "blocked" and not args.blocker_id:
                die("closing as blocked requires --blocker-id (raise the blocker first)")
            if not args.result:
                die("--result required: a closed attempt without a recorded outcome is amnesia")
            node["status"] = args.status
            node["closed_at"] = utc_now()
            node["result"] = args.result
            if args.blocker_id:
                node["blocker_id"] = args.blocker_id
            for item in args.evidence or []:
                node.setdefault("evidence", []).append(item)
            save_tree(control, tree)
            append_event(control, {"event": "tree_closed", "summary": f"{code} [{args.status}]: {args.result}",
                                   "node": code, "status": args.status})
            # v3 §2: the node is closed on disk; the graph turns that terminal
            # status into (or takes back) the relations `tree-close` states.
            graph = _graph_after(control, GRAPH_RULES_TREE_CLOSE, "research_os:tree close")
        else:  # note
            if not args.result and not args.evidence:
                die("note requires --result text and/or --evidence")
            if args.result:
                node["result"] = args.result
            for item in args.evidence or []:
                node.setdefault("evidence", []).append(item)
            save_tree(control, tree)
        payload = {"ok": True, "code": code, "status": node["status"]}
        if args.action == "close" and graph is not None:
            payload["graph"] = graph
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    if args.action == "show":
        render_attempts_md(control, tree)
        print((control / "ATTEMPTS.md").read_text(encoding="utf-8"))
        return 0

    if args.action == "list":
        selected = nodes
        if args.status:
            selected = {c: n for c, n in nodes.items() if n.get("status") == args.status}
        if args.under:
            selected = {c: n for c, n in selected.items()
                        if c == args.under or c.startswith(args.under + ".")}
        print(json.dumps({"ok": True, "count": len(selected),
                          "nodes": [selected[c] for c in sorted(selected, key=node_sort_key)]},
                         ensure_ascii=False, indent=2))
        return 0

    die(f"unknown tree action {args.action}")
    return 2


# ---------------------------------------------------------------- assets & snapshots

def sha256_file(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cmd_asset(args) -> int:
    """The ammo-depot index: artifacts as tagged, versioned, attributable entries."""
    # A cross-project search walks the registry and never touches the anchor
    # project, so it works from anywhere (even outside a v2 project).
    if args.action == "search" and getattr(args, "all_projects", False):
        return asset_search_all(args)
    control = locate_control(args.root)
    state = load_state(control)
    if state.get("schema_version") in SCHEMA_V1_FAMILY:
        die("state is v1; run migrate first", 1)
    artifacts = state.setdefault("artifacts", [])
    if args.action == "search":
        results = filter_assets(artifacts, str(control.parent), state.get("title") or "?",
                                set(args.tag or []), args.id_contains or "")
        print(json.dumps({"ok": True, "scope": "project", "count": len(results),
                          "results": results}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "list":
        entries = [a for a in artifacts if isinstance(a, dict)]
        loose = [a for a in artifacts if isinstance(a, str)]
        if args.tag:
            entries = [a for a in entries if set(args.tag) <= set(a.get("tags") or [])]
        print(json.dumps({"ok": True, "indexed": entries, "unindexed_paths": loose,
                          "count": len(entries)}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "add":
        if not args.path:
            die("asset add requires --path")
        root = control.parent
        target = (root / args.path)
        entry = {
            "id": args.id or slugify(Path(args.path).stem),
            "path": Path(args.path).as_posix(),
            "role": args.role or "",
            "tags": sorted(set(args.tag or [])),
            "produced_by": args.produced_by or state.get("active_skill", ""),
            "status": args.status or "current",
            "added_at": utc_now(),
        }
        if args.version_label:
            entry["version"] = args.version_label
        if args.hash:
            if not target.exists():
                die(f"--hash requested but {target} does not exist")
            entry["sha256"] = sha256_file(target)
        elif not target.exists():
            entry["missing_at_add"] = True
        existing_ids = {a.get("id") for a in artifacts if isinstance(a, dict)}
        if entry["id"] in existing_ids:
            if not args.supersede:
                die(f"asset id {entry['id']!r} exists; pass --supersede to replace (old entry kept as superseded)", 1)
            for art in artifacts:
                if isinstance(art, dict) and art.get("id") == entry["id"] and art.get("status") != "superseded":
                    art["status"] = "superseded"
            entry["id"] = entry["id"] + "-" + utc_now().replace(":", "").replace("-", "")[4:13].lower()
        artifacts.append(entry)
        commit_state(control, state)
        append_event(control, {"event": "asset_indexed", "summary": f"{entry['id']}: {entry['path']}",
                               "asset_id": entry["id"], "tags": entry["tags"]})
        # v3 §2: the asset index is the authority on the object; the graph reads
        # it back and states how the new asset hangs off the rest of the project.
        graph = _graph_after(control, GRAPH_RULES_ASSET, "research_os:asset add")
        payload = {"ok": True, "asset": entry}
        if graph is not None:
            payload["graph"] = graph
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    die(f"unknown asset action {args.action}")
    return 2


def cmd_snapshot(args) -> int:
    """Labeled checkpoint of the control plane (state + events + decisions)."""
    control = locate_control(args.root)
    state = load_state(control)
    label = slugify(args.label)
    stamp = utc_now().replace(":", "").replace("-", "").replace("T", "_").rstrip("Z")
    dest = control / "snapshots" / f"{stamp}_{label}"
    if dest.exists():
        die(f"snapshot {dest} already exists")
    dest.mkdir(parents=True)
    copied = []
    for name in ("state.json", "events.jsonl", "decisions.md", "tree.json", "ATTEMPTS.md", "state.v1.bak.json"):
        src = control / name
        if src.exists():
            shutil.copy2(src, dest / name)
            copied.append(name)
    append_event(control, {"event": "snapshot_created", "summary": f"{label} -> {dest.name}",
                           "artifacts": [f".research-os/snapshots/{dest.name}/"]})
    rotated = []
    if args.keep and args.keep > 0:
        snaps = sorted(p for p in (control / "snapshots").iterdir() if p.is_dir())
        expendable = [p for p in snaps if "milestone-" not in p.name]
        while len(expendable) > args.keep:
            victim = expendable.pop(0)
            shutil.rmtree(victim)
            rotated.append(victim.name)
    print(json.dumps({"ok": True, "snapshot": str(dest), "copied": copied,
                      "rotated_out": rotated}, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- sessions & event cursors

def cmd_session(args) -> int:
    control = locate_control(args.root)
    if not (control / "state.json").exists():
        die(f"no project state under {control}; bootstrap first")
    data = read_json_file(sessions_path(control), {"schema_version": "auto-research/sessions-v1",
                                                   "counter": 0, "sessions": []})
    sessions = data.setdefault("sessions", [])

    def save():
        atomic_write(sessions_path(control), json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    if args.action == "list":
        print(json.dumps({"ok": True, "sessions": sessions}, ensure_ascii=False, indent=2))
        return 0

    if args.action in ("ensure", "start"):
        external = args.external_id or external_session_id()
        host = detect_host(args.host)
        # Badge hygiene: same-host active sessions idle >24h are windows that
        # never closed; suspend them so the single-active fallback in
        # current_session_id stays unambiguous.
        cutoff = datetime.now(timezone.utc).timestamp() - 24 * 3600
        for entry in sessions:
            if entry.get("status") == "active" and entry.get("host") == host:
                try:
                    seen = datetime.fromisoformat(str(entry.get("last_seen", "")).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    seen = 0
                if seen and seen < cutoff:
                    entry["status"] = "suspended"
        if args.action == "ensure" and external:
            for entry in sessions:
                if entry.get("external_id") == external and entry.get("status") == "active":
                    entry["last_seen"] = utc_now()
                    if args.intent:
                        entry["intent"] = args.intent
                    save()
                    print(json.dumps({"ok": True, "id": entry["id"], "existing": True}, ensure_ascii=False))
                    return 0
        data["counter"] = int(data.get("counter", 0)) + 1
        sid = f"W{data['counter']:02d}"
        entry = {
            "id": sid, "host": host, "external_id": external,
            "intent": args.intent or "", "headless": bool(args.headless),
            "started_at": utc_now(), "last_seen": utc_now(), "status": "active",
        }
        sessions.append(entry)
        save()
        append_event(control, {"event": "session_started", "summary": f"{sid} [{host}] {entry['intent']}",
                               "session": sid})
        print(json.dumps({"ok": True, "id": sid, "existing": False}, ensure_ascii=False))
        return 0

    if args.action == "attach":
        if not args.id:
            die("session attach requires --id")
        external = args.external_id or external_session_id()
        for entry in sessions:
            if entry.get("id") == args.id:
                entry.update({"external_id": external, "last_seen": utc_now(), "status": "active"})
                save()
                print(json.dumps({"ok": True, "id": args.id, "attached": True}, ensure_ascii=False))
                return 0
        die(f"session {args.id} not found", 1)

    if args.action == "close":
        if not args.id:
            die("session close requires --id")
        for entry in sessions:
            if entry.get("id") == args.id and entry.get("status") == "active":
                entry.update({"status": "closed", "last_seen": utc_now()})
                save()
                append_event(control, {"event": "session_closed", "summary": args.id, "session": args.id})
                print(json.dumps({"ok": True, "id": args.id, "closed": True}, ensure_ascii=False))
                return 0
        die(f"active session {args.id} not found", 1)

    die(f"unknown session action {args.action}")
    return 2


IMPORTANT_EVENT_TYPES = {"blocker_raised", "blocker_resolved", "tree_closed", "task_posted",
                         "task_returned", "task_failed", "schema_migrated", "loop_stopped",
                         "session_started"}


def unread_events(control: Path, reader: str, types=None):
    events = read_events(control)
    cursors = read_json_file(cursors_path(control), {})
    cursor = int((cursors.get(reader) or {}).get("seq", 0))
    fresh = []
    for index, event in enumerate(events, 1):
        seq = event.get("seq") if isinstance(event.get("seq"), int) else index
        if seq <= cursor:
            continue
        if types and event.get("event") not in types:
            fresh.append((seq, None))
            continue
        fresh.append((seq, event))
    return cursor, fresh


def cmd_events(args) -> int:
    control = locate_control(args.root)
    if args.action == "status":
        reader = args.reader or current_session_id(control) or "anonymous"
        cursor, fresh = unread_events(control, reader)
        counts = {}
        for _seq, event in fresh:
            if event is not None:
                counts[event.get("event", "?")] = counts.get(event.get("event", "?"), 0) + 1
        total = len(read_events(control))
        print(json.dumps({"ok": True, "reader": reader, "total_events": total,
                          "cursor": cursor, "unread": len(fresh),
                          "unread_by_type": dict(sorted(counts.items()))}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "read":
        reader = args.reader or current_session_id(control) or "anonymous"
        types = set(args.type) if args.type else None
        cursor, fresh = unread_events(control, reader, types)
        shown = [event for _seq, event in fresh if event is not None][: args.limit]
        for event in shown:
            print(json.dumps(event, ensure_ascii=False))
        # Cursor advances only on an unfiltered, non-peek read that showed
        # everything: a type-filtered read must not skip the reader past
        # events it never saw.
        truncated = len([e for _s, e in fresh if e is not None]) > len(shown)
        advance = not args.peek and not types and not truncated
        if advance and fresh:
            advanced = max(seq for seq, _e in fresh)
            cursors = read_json_file(cursors_path(control), {})
            cursors[reader] = {"seq": advanced, "updated_at": utc_now()}
            atomic_write(cursors_path(control), json.dumps(cursors, ensure_ascii=False, indent=2) + "\n")
            note = "cursor advanced"
            cursor_now = advanced
        else:
            note = ("peek: cursor not advanced" if args.peek
                    else "type-filtered or truncated: cursor not advanced (rerun without --type / raise --limit to advance)")
            cursor_now = cursor
        print(json.dumps({"ok": True, "reader": reader, "shown": len(shown),
                          "cursor_now": cursor_now, "note": note}, ensure_ascii=False))
        return 0
    if args.action == "replay":
        events = read_events(control)
        for index, event in enumerate(events, 1):
            seq = event.get("seq") if isinstance(event.get("seq"), int) else index
            if args.since and seq <= args.since:
                continue
            if args.type and event.get("event") not in set(args.type):
                continue
            print(json.dumps(event, ensure_ascii=False))
        return 0
    die(f"unknown events action {args.action}")
    return 2


# ---------------------------------------------------------------- probe

def default_registry_path() -> Path:
    return Path(__file__).resolve().parent.parent / "docs" / "capability-registry.json"


def probe_provider(provider: dict) -> str:
    probe = provider.get("probe") or {}
    ptype = probe.get("type", "conversation")
    if ptype == "always":
        return "available"
    if ptype == "command":
        if shutil.which(probe.get("command", provider.get("ref", ""))):
            return "available"
        # A CLI absent from PATH may still live at a registered install location
        # (e.g. the VS Code extension's vendored claude.exe on Windows).
        for pattern in probe.get("fallback_globs") or []:
            expanded = os.path.expandvars(os.path.expanduser(pattern))
            if glob.glob(expanded):
                return "available"
        return "missing"
    if ptype in ("path", "skill-dir"):
        # `{plugin}` = this package's own root, wherever it was installed
        # (skills dir, marketplace cache, or a development checkout).
        plugin = str(Path(__file__).resolve().parent.parent)
        raw = probe.get("path", "").replace("{plugin}", plugin)
        if raw and Path(os.path.expandvars(raw)).expanduser().exists():
            return "available"
        for pattern in probe.get("fallback_globs") or []:
            if glob.glob(os.path.expandvars(os.path.expanduser(pattern.replace("{plugin}", plugin)))):
                return "available"
        return "missing"
    if ptype == "python-import":
        # Probe a Python package the way it will be used: import it under the
        # interpreter this CLI runs in (covers venv/conda prefixes that globs miss).
        module = probe.get("module") or ""
        if not module:
            return "missing"
        try:
            rc = subprocess.run([sys.executable, "-c",
                                 "import importlib,sys; importlib.import_module(sys.argv[1])", module],
                                capture_output=True, timeout=20).returncode
        except Exception:
            return "missing"
        return "available" if rc == 0 else "missing"
    if ptype == "conversation":
        return "conversation_probe_required"
    return "conversation_probe_required"


def cmd_probe(args) -> int:
    registry_path = Path(args.registry) if args.registry else default_registry_path()
    if not registry_path.exists():
        die(f"capability registry not found at {registry_path}")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    capabilities = registry.get("capabilities", [])
    host = detect_host(args.host)
    wanted = set(args.need or [])
    results = {}
    for cap in capabilities:
        cid = cap.get("id")
        if wanted and cid not in wanted:
            continue
        if args.phase and args.phase not in (cap.get("phases") or []):
            continue
        chains = cap.get("providers", {})
        chain = chains.get(host) or []
        if not chain and host == "unknown":
            chain = (chains.get("claude-code") or []) + (chains.get("codex") or [])
        verdict, provider_ref = "missing", None
        for idx, provider in enumerate(chain):
            status = probe_provider(provider)
            if status == "available":
                verdict = "available" if idx == 0 else "degraded"
                provider_ref = provider.get("ref")
                break
            if status == "conversation_probe_required" and verdict != "available":
                verdict, provider_ref = "conversation_probe_required", provider.get("ref")
        results[cid] = {
            "status": verdict, "provider": provider_ref, "host": host,
            "on_missing": (cap.get("on_missing") or {}).get("action", "blocker"),
            "probed_at": utc_now(),
        }
    if args.write_state:
        control = locate_control(Path(args.write_state))
        state = load_state(control)
        if state.get("schema_version") in SCHEMA_V1_FAMILY:
            die("state is v1; run migrate first", 1)
        caps = state.setdefault("capability_status", {})
        for cid, entry in results.items():
            caps[cid] = {k: entry[k] for k in ("status", "provider", "host", "probed_at")}
        commit_state(control, state)
    missing = sorted(cid for cid, r in results.items() if r["status"] == "missing")
    print(json.dumps({"ok": True, "host": host, "results": results, "missing": missing},
                     ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- portfolio (cross-project)

def portfolio_path() -> Path:
    """User-level cross-project registry. Deliberately outside the plugin tree
    so that `deploy install`/`uninstall` never touch it."""
    return Path.home() / ".claude" / "auto-research" / "projects.json"


def load_portfolio() -> dict:
    return read_json_file(portfolio_path(), {"schema_version": PORTFOLIO_SCHEMA, "projects": []})


def save_portfolio(data: dict) -> None:
    atomic_write(portfolio_path(), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def portfolio_upsert(root: Path, state: dict) -> dict:
    """Insert or refresh one project entry, deduped by resolved root."""
    resolved = str(Path(root).expanduser().resolve())
    now = utc_now()
    data = load_portfolio()
    data.setdefault("schema_version", PORTFOLIO_SCHEMA)
    projects = data.setdefault("projects", [])
    for entry in projects:
        if entry.get("root") == resolved:
            entry.update({"title": state.get("title"), "project_id": state.get("project_id"),
                          "last_seen": now})
            save_portfolio(data)
            return entry
    entry = {"root": resolved, "project_id": state.get("project_id"), "title": state.get("title"),
             "registered_at": now, "last_seen": now}
    projects.append(entry)
    save_portfolio(data)
    return entry


def portfolio_register(root: Path) -> dict:
    """Explicit register: the project must already exist (dies otherwise)."""
    control = locate_control(root)
    state = load_state(control)
    return portfolio_upsert(control.parent, state)


def cmd_portfolio(args) -> int:
    if args.action == "register":
        if not args.root:
            die("portfolio register requires a <root>")
        entry = portfolio_register(args.root)
        print(json.dumps({"ok": True, "registered": entry}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "list":
        projects = load_portfolio().get("projects", [])
        print(json.dumps({"ok": True, "count": len(projects), "projects": projects},
                         ensure_ascii=False, indent=2))
        return 0
    if args.action == "scan":
        data = load_portfolio()
        data.setdefault("schema_version", PORTFOLIO_SCHEMA)
        now = utc_now()
        kept, removed = [], []
        for entry in data.get("projects", []):
            root = entry.get("root", "")
            state_path = Path(root) / ".research-os" / "state.json"
            if not state_path.exists():
                removed.append({"root": root, "project_id": entry.get("project_id"),
                                "title": entry.get("title")})
                continue
            try:  # present but unreadable: keep it — scan only prunes vanished projects
                state = json.loads(state_path.read_text(encoding="utf-8"))
                entry["title"] = state.get("title", entry.get("title"))
                entry["project_id"] = state.get("project_id", entry.get("project_id"))
            except (json.JSONDecodeError, OSError):
                pass
            entry["last_seen"] = now
            kept.append(entry)
        data["projects"] = kept
        save_portfolio(data)
        print(json.dumps({"ok": True, "kept": len(kept), "removed": removed},
                         ensure_ascii=False, indent=2))
        return 0
    die(f"unknown portfolio action {args.action}")
    return 2


def project_mini_digest(entry: dict) -> tuple:
    """At most 5 lines summarizing one registered project; returns (lines,
    has_unresolved_blockers). Never raises — a read failure degrades to a
    marked two-line stub so one bad project cannot break `context --all`."""
    root = entry.get("root", "?")
    title = entry.get("title") or entry.get("project_id") or "?"
    header = f"### {title} ({root})"
    state_path = Path(root) / ".research-os" / "state.json"
    if not state_path.exists():
        return [header, "- [missing] no state.json — run `portfolio scan` to prune"], False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [header, f"- [unreadable] {str(exc)[:80]}"], False
    schema = state.get("schema_version")
    if schema in SCHEMA_V1_FAMILY:
        return [header, f"- [v1 — migrate pending] stage={state.get('stage')} schema={schema}"], False
    lines = [header,
             f"- stage={state.get('stage')} | paper_type={state.get('paper_type')} | schema={schema}"]
    gates = state.get("gates") or {}
    open_gates = [g for g, v in gates.items()
                  if isinstance(v, dict) and v.get("status") in ("open", "failed", "blocked")]
    unresolved = [b for b in state.get("blockers") or [] if not b.get("resolved_at")]
    gate_blk = f"- gates: {len(open_gates)} open/failed/blocked | blockers: {len(unresolved)} unresolved"
    if unresolved:
        gate_blk += f" (first `{unresolved[0].get('id')}`: {str(unresolved[0].get('description', ''))[:60]})"
    lines.append(gate_blk)
    actions = state.get("next_actions") or []
    if actions:
        first = actions[0]
        act = first.get("action") if isinstance(first, dict) else first
        lines.append(f"- next: {str(act)[:100]}")
    else:
        lines.append("- next: (none)")
    running = []
    tree_file = Path(root) / ".research-os" / "tree.json"
    if tree_file.exists():
        try:
            running = [c for c, n in (json.loads(tree_file.read_text(encoding="utf-8")).get("nodes") or {}).items()
                       if n.get("status") == "running"]
        except (json.JSONDecodeError, OSError):
            running = []
    run_line = f"- running: {len(running)} tree node(s)"
    if running:
        run_line += f" (first `{sorted(running, key=node_sort_key)[0]}`)"
    lines.append(run_line)
    return lines[:5], bool(unresolved)


def context_all() -> int:
    projects = load_portfolio().get("projects", [])
    blocks, blocked = [], 0
    for entry in projects:
        lines, has_blk = project_mini_digest(entry)
        blocked += 1 if has_blk else 0
        blocks.append("\n".join(lines))
    out = [f"## auto-research portfolio — {len(projects)} project(s), {blocked} with unresolved blockers", ""]
    out.append("\n\n".join(blocks) if blocks
               else "(registry empty — bootstrap a project or `portfolio register <root>` to populate)")
    print("\n".join(out))
    return 0


def asset_condense(art: dict) -> dict:
    out = {"id": art.get("id"), "path": art.get("path"), "role": art.get("role", ""),
           "tags": art.get("tags") or [], "status": art.get("status", "")}
    if art.get("version"):
        out["version"] = art["version"]
    return out


def filter_assets(artifacts, root, title, tags, id_contains) -> list:
    hits = []
    for art in artifacts or []:
        if not isinstance(art, dict):
            continue
        if tags and not tags <= set(art.get("tags") or []):
            continue
        if id_contains and id_contains not in str(art.get("id", "")):
            continue
        hits.append({"project_title": title, "root": root, "asset": asset_condense(art)})
    return hits


def search_project_assets(root, title, tags, id_contains) -> list:
    state_path = Path(root) / ".research-os" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return filter_assets(state.get("artifacts"), root, title, tags, id_contains)


def asset_search_all(args) -> int:
    tags = set(args.tag or [])
    id_contains = args.id_contains or ""
    results = []
    for entry in load_portfolio().get("projects", []):
        results.extend(search_project_assets(
            entry.get("root", ""), entry.get("title") or entry.get("project_id") or "?",
            tags, id_contains))
    print(json.dumps({"ok": True, "scope": "all-projects", "count": len(results),
                      "results": results}, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- context

def cmd_context(args) -> int:
    if getattr(args, "all", False) and not args.root:
        return context_all()
    if args.root:
        control = locate_control(args.root)
        if not (control / "state.json").exists():
            if args.quiet_if_absent:
                return 0
            die(f"no project state under {control}")
    else:
        found = find_project_upwards(Path.cwd())
        if found is None:
            if args.quiet_if_absent:
                return 0
            die("no .research-os found from cwd upwards")
        control = found
    state = load_state(control)
    schema = state.get("schema_version")
    lines = []
    lines.append(f"## auto-research resume digest ({control.parent.name})")
    lines.append(f"- **title**: {state.get('title')}  |  **stage**: {state.get('stage')}"
                 f"  |  **paper_type**: {state.get('paper_type')}  |  schema: {schema}")
    lines.append(f"- **objective**: {state.get('objective')}")
    lines.append(f"- **active**: workstream={state.get('active_workstream')} skill={state.get('active_skill')}")
    if schema in SCHEMA_V1_FAMILY:
        lines.append("- [!] state is v1 — run `research_os.py migrate` before writing.")
        gates = state.get("gates") or {}
        if gates:
            lines.append("- gates (v1 raw): " + json.dumps(gates, ensure_ascii=False))
    else:
        open_gates = {g: v for g, v in (state.get("gates") or {}).items()
                      if isinstance(v, dict) and v.get("status") in ("open", "failed", "blocked")}
        if open_gates:
            lines.append("- **open/failed/blocked gates**:")
            for gid, gate in open_gates.items():
                lines.append(f"  - `{gid}` = {gate.get('status')} (owner: {gate.get('owner_skill') or '?'}) {gate.get('evidence', '')[:160]}")
        unresolved = [b for b in state.get("blockers", []) if not b.get("resolved_at")]
        if unresolved:
            lines.append("- **UNRESOLVED BLOCKERS (must be surfaced, never skipped silently)**:")
            for blocker in unresolved:
                lines.append(f"  - `{blocker['id']}` [{blocker['type']}] {blocker['description']}"
                             + (f" → queued: {blocker['queued_action']}" if blocker.get("queued_action") else ""))
        loop = state.get("loop") or {}
        lines.append(f"- loop: mode={loop.get('mode')} wakeup_pending={loop.get('wakeup_pending')}"
                     f" stagnation={loop.get('stagnation_count')}/{loop.get('stagnation_budget')}")
        versions = state.get("versions") or {}
        if versions:
            lines.append("- **versions (do not trust memory — this map is authoritative)**: "
                         + ", ".join(f"{k}={v}" for k, v in sorted(versions.items())))
        if tree_path(control).exists():
            tree = load_tree(control)
            tnodes = tree.get("nodes", {})
            if tnodes:
                counts = {}
                for node in tnodes.values():
                    counts[node.get("status", "?")] = counts.get(node.get("status", "?"), 0) + 1
                lines.append("- **attempt tree** (full table: .research-os/ATTEMPTS.md): "
                             + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
                running = [c for c in sorted(tnodes, key=node_sort_key) if tnodes[c].get("status") == "running"]
                for code in running:
                    lines.append(f"  - RUNNING `{code}` {tnodes[code].get('title', '')}")
                closed = [c for c in sorted(tnodes, key=node_sort_key)
                          if tnodes[c].get("status") in NODE_TERMINAL and tnodes[c].get("closed_at")]
                closed.sort(key=lambda c: tnodes[c]["closed_at"])
                for code in closed[-3:]:
                    node = tnodes[code]
                    lines.append(f"  - last closed `{code}` [{node['status']}] {node.get('result', '')[:120]}")
        indexed = [a for a in state.get("artifacts", []) if isinstance(a, dict)]
        if indexed:
            current = [a for a in indexed if a.get("status") == "current"]
            tags = {}
            for art in indexed:
                for tag in art.get("tags") or []:
                    tags[tag] = tags.get(tag, 0) + 1
            lines.append(f"- **asset index**: {len(indexed)} indexed ({len(current)} current)"
                         + ("; tags: " + ", ".join(f"{k}×{v}" for k, v in sorted(tags.items())) if tags else ""))
            for art in current[-5:]:
                lines.append(f"  - `{art.get('id')}` {art.get('path')}"
                             + (f" [{art.get('version')}]" if art.get("version") else "")
                             + (f" ({', '.join(art.get('tags') or [])})" if art.get("tags") else ""))
    actions = state.get("next_actions") or []
    if actions:
        lines.append("- **next_actions**:")
        for item in actions:
            if isinstance(item, dict):
                suffix = f" (owner: {item.get('owner_skill')})" if item.get("owner_skill") else ""
                blocked = f" [blocked_by: {item.get('blocked_by')}]" if item.get("blocked_by") else ""
                lines.append(f"  - {item.get('action')}{suffix}{blocked}")
            else:
                lines.append(f"  - {item}")
    # Event catalog for the current reader: never dump the log, show the unread
    # directory (counts by type) plus only the important unread entries.
    reader = current_session_id(control)
    if reader:
        _cursor, fresh = unread_events(control, reader)
        real = [event for _seq, event in fresh if event is not None]
        if real:
            counts = {}
            for event in real:
                counts[event.get("event", "?")] = counts.get(event.get("event", "?"), 0) + 1
            lines.append(f"- **unread events for {reader}**: {len(real)} "
                         f"({', '.join(f'{k}×{v}' for k, v in sorted(counts.items()))})"
                         f" — read incrementally: `events read --reader {reader}`")
            important = [e for e in real if e.get("event") in IMPORTANT_EVENT_TYPES][-5:]
            for event in important:
                lines.append(f"  - [{event.get('seq')}] `{event.get('event')}` {str(event.get('summary', ''))[:140]}")
        else:
            lines.append(f"- unread events for {reader}: none")
    else:
        events = read_events(control)
        if events:
            lines.append("- **[!] NO SESSION BADGE** — writes are attributing to nobody. Run "
                         "`session <root> ensure --intent \"…\"` before any other command.")
            lines.append(f"- **events**: {len(events)} total. Last 3:")
            for event in events[-3:]:
                lines.append(f"  - [{event.get('seq', '?')}] `{event.get('event')}` {str(event.get('summary', ''))[:140]}")
    lines.append(f"- state file: {control / 'state.json'} (updated {state.get('updated_at')})")
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------- doctor

def cmd_doctor(args) -> int:
    plugin_root = Path(args.plugin_root).resolve() if args.plugin_root else Path(__file__).resolve().parent.parent
    report = {"plugin_root": str(plugin_root), "checks": [], "ok": True}

    def check(name: str, ok: bool, detail: str = "") -> None:
        report["checks"].append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            report["ok"] = False

    for manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        path = plugin_root / manifest
        if path.exists():
            try:
                json.loads(path.read_text(encoding="utf-8"))
                check(f"manifest {manifest}", True)
            except json.JSONDecodeError as exc:
                check(f"manifest {manifest}", False, str(exc))
        else:
            check(f"manifest {manifest}", False, "missing")

    registry_path = plugin_root / "docs" / "capability-registry.json"
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            caps = registry.get("capabilities", [])
            bad = [c.get("id") for c in caps
                   if not c.get("id") or not isinstance(c.get("providers"), dict)
                   or (c.get("on_missing") or {}).get("action") not in ("degrade", "blocker", "stop")]
            check("capability-registry.json schema", not bad, f"bad entries: {bad}" if bad else f"{len(caps)} capabilities")
        except json.JSONDecodeError as exc:
            check("capability-registry.json schema", False, str(exc))
    else:
        check("capability-registry.json schema", False, "missing")

    hooks_path = plugin_root / "hooks" / "hooks.json"
    if hooks_path.exists():
        try:
            json.loads(hooks_path.read_text(encoding="utf-8"))
            check("hooks/hooks.json", True)
        except json.JSONDecodeError as exc:
            check("hooks/hooks.json", False, str(exc))
    else:
        check("hooks/hooks.json", False, "missing")

    skills_dir = plugin_root / "skills"
    skill_dirs = sorted(p for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.exists() else []
    check("skills present", bool(skill_dirs), f"{len(skill_dirs)} skills")
    for skill in skill_dirs:
        skill_md = skill / "SKILL.md"
        if not skill_md.exists():
            check(f"skill {skill.name}", False, "SKILL.md missing")
            continue
        text = skill_md.read_text(encoding="utf-8")
        has_front = text.startswith("---") and "name:" in text.split("---", 2)[1] and "description:" in text.split("---", 2)[1]
        check(f"skill {skill.name} frontmatter", has_front)

    script_paths = [
        plugin_root / "scripts" / "research_os.py",
        plugin_root / "scripts" / "lint_package.py",
        plugin_root / "scripts" / "hook_guard.py",
        plugin_root / "scripts" / "hook_stop_guard.py",
        plugin_root / "scripts" / "deploy.py",
        plugin_root / "scripts" / "growth.py",
        plugin_root / "scripts" / "collab.py",
        plugin_root / "scripts" / "procedures.py",
        plugin_root / "scripts" / "narrative.py",
        plugin_root / "scripts" / "turn_runtime.py",
        plugin_root / "scripts" / "role_runtime.py",
        plugin_root / "scripts" / "research_graph.py",
        plugin_root / "scripts" / "narrative_backfill.py",
        plugin_root / "scripts" / "narrative_render_html.py",
        plugin_root / "scripts" / "narrative_serve.py",
        plugin_root / "scripts" / "narrative_audit.py",
        plugin_root / "scripts" / "literature_index.py",
        plugin_root / "scripts" / "graph_render_html.py",
        plugin_root / "scripts" / "research_map.py",
        plugin_root / "scripts" / "taste_ledger.py",
        plugin_root / "scripts" / "figure_router.py",
        plugin_root / "scripts" / "figure_templates.py",
        plugin_root / "scripts" / "component_index.py",
        plugin_root / "scripts" / "hook_session_start.py",
        plugin_root / "scripts" / "hook_post_tool.py",
        plugin_root / "skills" / "research-artifacts" / "scripts" / "visual_contract.py",
        plugin_root / "skills" / "research-artifacts" / "scripts" / "figure_studio.py",
    ]
    for path in script_paths:
        rel = path.relative_to(plugin_root).as_posix()
        if not path.exists():
            check(f"script {rel}", False, "missing")
            continue
        proc = subprocess.run([sys.executable, "-m", "py_compile", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        check(f"script {rel} compiles", proc.returncode == 0, proc.stderr.strip()[:300])

    try:
        import pypdf  # noqa: F401
        check("pypdf importable", True)
    except ImportError:
        check("pypdf importable", True, "WARN: absent — visual_contract validate-delivery/self-test run degraded")

    agents_dir = plugin_root / "agents"
    for agent in ("research-verifier.md", "research-librarian.md", "research-refuter.md", "research-reproducer.md",
                  "research-chronicler.md", "research-figure-engineer.md", "research-evidence-steward.md",
                  "research-experiment-runner.md", "research-theory-operator.md"):
        check(f"agent {agent}", (agents_dir / agent).exists())

    library = plugin_root / "library"
    growth_path = library / "growth.json"
    if growth_path.exists():
        try:
            growth = json.loads(growth_path.read_text(encoding="utf-8"))
            entries = growth.get("entries", {})
            bad = [eid for eid, e in entries.items()
                   if e.get("status") not in ("candidate", "trial", "active", "retired", "superseded")
                   or not e.get("provenance")]
            check("library growth.json", not bad, f"bad entries: {bad}" if bad else f"{len(entries)} entries")
            index = library / "INDEX.md"
            if index.exists():
                index_lines = index.read_text(encoding="utf-8").count("\n")
                check("library INDEX budget", index_lines <= 200, f"{index_lines}/200 lines")
            else:
                check("library INDEX budget", False, "INDEX.md missing — run growth.py render")
            live = {eid for eid, e in entries.items() if e.get("status") in ("candidate", "trial", "active")}
            missing_files = [eid for eid in live
                             if not (library / entries[eid].get("file", "")).exists()]
            check("library entry files", not missing_files,
                  f"ledger entries without files: {missing_files}" if missing_files else f"{len(live)} live entries on disk")
        except json.JSONDecodeError as exc:
            check("library growth.json", False, str(exc))
    else:
        check("library growth.json", True, "no library yet (fresh install)")

    # v2.9 figure library (figure-engineering-contract §10.5): schema of items,
    # venue profiles, component index presence. Missing optional pieces warn.
    items_dir = library / "items"
    if items_dir.exists():
        item_problems = []
        item_files = sorted(items_dir.glob("*.json"))
        for item_file in item_files:
            try:
                item = json.loads(item_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                item_problems.append(f"{item_file.name}: {exc}")
                continue
            required = ("id", "kind", "version", "license", "quality_state")
            missing = [k for k in required if item.get(k) in (None, "")]
            if missing:
                item_problems.append(f"{item_file.name}: missing {missing}")
            if item.get("kind") not in ("chart-template", "diagram-template", "component", "component-set",
                                        "style-profile", "palette"):
                item_problems.append(f"{item_file.name}: unknown kind {item.get('kind')!r}")
            if item.get("kind") == "chart-template" and not (item.get("data_contract") or {}).get("required_fields"):
                item_problems.append(f"{item_file.name}: chart-template without data_contract.required_fields")
        check("figure library items", not item_problems,
              "; ".join(item_problems)[:300] if item_problems else f"{len(item_files)} item(s) valid")
    else:
        check("figure library items", True, "no library/items yet")
    venue_dir = library / "venue"
    if venue_dir.exists():
        venue_bad = []
        venue_files = sorted(venue_dir.glob("*.json"))
        for vf in venue_files:
            try:
                v = json.loads(vf.read_text(encoding="utf-8"))
                if not all(k in v for k in ("id", "style_stack", "figure_width_mm", "export")):
                    venue_bad.append(vf.name)
            except json.JSONDecodeError:
                venue_bad.append(vf.name)
        check("figure venue profiles", not venue_bad,
              f"invalid: {venue_bad}" if venue_bad else f"{len(venue_files)} profile(s)")
    else:
        check("figure venue profiles", True, "no library/venue yet")
    comp_index = library / "components" / "bioicons" / "index.json"
    check("component index (bioicons)", True,
          "present" if comp_index.exists() else "WARN: absent — run component_index.py index build --source bioicons")

    reg_path = portfolio_path()
    if reg_path.exists():
        try:
            reg = json.loads(reg_path.read_text(encoding="utf-8"))
            projects = reg.get("projects", [])
            bad = [p.get("root") for p in projects
                   if not all(p.get(k) for k in ("root", "project_id", "title", "registered_at", "last_seen"))]
            if reg.get("schema_version") != PORTFOLIO_SCHEMA:
                check("portfolio registry", False, f"unexpected schema {reg.get('schema_version')!r}")
            elif bad:
                check("portfolio registry", False, f"entries missing fields: {bad}")
            else:
                check("portfolio registry", True, f"{len(projects)} project(s)")
        except json.JSONDecodeError as exc:
            check("portfolio registry", False, str(exc))
    else:
        check("portfolio registry", True, "no registry yet")

    lint_path = plugin_root / "scripts" / "lint_package.py"
    if lint_path.exists():
        proc = subprocess.run([sys.executable, str(lint_path), "--plugin-root", str(plugin_root)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("link lint", proc.returncode == 0, (proc.stdout or proc.stderr).strip()[-600:])

    if not args.no_probe:
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "probe",
                               "--registry", str(registry_path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            try:
                probe_out = json.loads(proc.stdout)
                missing = probe_out.get("missing", [])
                check("capability probe", True,
                      ("missing on this host: " + ", ".join(missing)) if missing else "all first-choice providers reachable")
            except json.JSONDecodeError:
                check("capability probe", False, "unparseable probe output")
        else:
            check("capability probe", False, proc.stderr.strip()[:300])

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


# ---------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research_os", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("bootstrap", help="create the v2 project control plane (idempotent)")
    p.add_argument("root", type=Path)
    p.add_argument("--title", required=True)
    p.add_argument("--stage", choices=sorted(STAGES), default="idea_only")
    p.add_argument("--paper-type", choices=sorted(PAPER_TYPES), default="empirical_ml")
    p.add_argument("--target-venue", default="")
    p.add_argument("--objective", default="Establish the next defensible research gate.")
    p.add_argument("--layout", choices=("core", "standard"), default="core")
    p.add_argument("--init-git", action="store_true")
    p.set_defaults(func=cmd_bootstrap)

    p = sub.add_parser("validate", help="validate state.json (v1 or v2) and events.jsonl")
    p.add_argument("target", type=Path)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("migrate", help="migrate v1 state to v2 (backs up original)")
    p.add_argument("target", type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("update", help="atomically update state fields/gates/next_actions")
    p.add_argument("root", type=Path)
    p.add_argument("--set", action="append", metavar="key=value")
    p.add_argument("--gate", action="append", metavar="id=status[:evidence][@owner][#blocker]")
    p.add_argument("--next", action="append", metavar="action[@owner_skill]")
    p.add_argument("--clear-next", action="store_true")
    p.add_argument("--version", action="append", metavar="track=label",
                   help="update the version map, e.g. manuscript=v12")
    p.add_argument("--visual", action="append", metavar="visual_key=<json>",
                   help="set visual_plan/visual_delivery/visual_integration (owned by research-artifacts)")
    p.add_argument("--loop-mode", choices=sorted(LOOP_MODES))
    p.add_argument("--tick", action="store_true", help="stamp loop.last_tick")
    p.add_argument("--stagnation", type=int, default=None)
    p.add_argument("--wakeup", choices=("0", "1"), default=None)
    p.add_argument("--host", default="auto")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("event", help="append one event to events.jsonl")
    p.add_argument("root", type=Path)
    p.add_argument("--type", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--artifact", action="append")
    p.add_argument("--data", action="append", metavar="key=value")
    p.set_defaults(func=cmd_event)

    p = sub.add_parser("blocker", help="manage the first-class blocker queue")
    p.add_argument("root", type=Path)
    p.add_argument("action", choices=("add", "resolve", "list"))
    p.add_argument("--type", choices=sorted(BLOCKER_TYPES))
    p.add_argument("--description")
    p.add_argument("--queued-action")
    p.add_argument("--id")
    p.add_argument("--note")
    p.set_defaults(func=cmd_blocker)

    p = sub.add_parser("asset", help="ammo-depot index: tag/version/attribute/search artifacts")
    p.add_argument("root", type=Path)
    p.add_argument("action", choices=("add", "list", "search"))
    p.add_argument("--path")
    p.add_argument("--id")
    p.add_argument("--role")
    p.add_argument("--tag", action="append")
    p.add_argument("--version-label", metavar="LABEL")
    p.add_argument("--produced-by")
    p.add_argument("--status", choices=("current", "superseded", "quarantined", "archived"))
    p.add_argument("--hash", action="store_true", help="record sha256 of the file")
    p.add_argument("--supersede", action="store_true")
    p.add_argument("--all-projects", action="store_true",
                   help="search: walk the portfolio registry instead of only this project")
    p.add_argument("--id-contains", metavar="SUBSTR", help="search: filter by id substring")
    p.set_defaults(func=cmd_asset)

    p = sub.add_parser("portfolio", help="cross-project registry (register/list/scan)")
    p.add_argument("action", choices=("register", "list", "scan"))
    p.add_argument("root", nargs="?", type=Path, help="project root (register only)")
    p.set_defaults(func=cmd_portfolio)

    p = sub.add_parser("tree", help="structured attempt tree: register -> start -> close, master table auto-rendered")
    p.add_argument("root", type=Path)
    p.add_argument("action", choices=("register", "start", "close", "note", "show", "list"))
    p.add_argument("--code", help="node code (auto-assigned on register if omitted)")
    p.add_argument("--parent", help="parent code; omit for a top-level node")
    p.add_argument("--kind", choices=sorted(NODE_KINDS))
    p.add_argument("--title")
    p.add_argument("--goal")
    p.add_argument("--owner")
    p.add_argument("--tag", action="append")
    p.add_argument("--status", help="terminal status for close; filter for list")
    p.add_argument("--result")
    p.add_argument("--evidence", action="append", metavar="PATH_OR_RUN_ID")
    p.add_argument("--blocker-id")
    p.add_argument("--under", help="list filter: subtree root code")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_tree)

    p = sub.add_parser("snapshot", help="labeled checkpoint of the control plane")
    p.add_argument("root", type=Path)
    p.add_argument("--label", required=True)
    p.add_argument("--keep", type=int, default=20,
                   help="rotate: keep at most N non-milestone snapshots (0 = keep all)")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("session", help="numbered work sessions (W##) for cross-host accounting")
    p.add_argument("root", type=Path)
    p.add_argument("action", choices=("ensure", "start", "attach", "close", "list"))
    p.add_argument("--id")
    p.add_argument("--intent")
    p.add_argument("--external-id", help="host session id (defaults to CLAUDE_SESSION_ID/CODEX_SESSION_ID)")
    p.add_argument("--host", default="auto")
    p.add_argument("--headless", action="store_true")
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("events", help="cursor-based incremental log consumption (catalog first, then unread only)")
    p.add_argument("root", type=Path)
    p.add_argument("action", choices=("status", "read", "replay"))
    p.add_argument("--reader", help="cursor owner (defaults to the current W## session)")
    p.add_argument("--type", action="append", help="filter by event type (repeatable)")
    p.add_argument("--peek", action="store_true", help="read without advancing the cursor")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--since", type=int, help="replay only: events with seq greater than this")
    p.set_defaults(func=cmd_events)

    p = sub.add_parser("probe", help="probe capability availability for this host")
    p.add_argument("--phase")
    p.add_argument("--need", action="append")
    p.add_argument("--host", default="auto")
    p.add_argument("--registry")
    p.add_argument("--write-state", metavar="PROJECT_ROOT")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("context", help="print the resume digest (used by SessionStart hook)")
    p.add_argument("root", nargs="?", type=Path)
    p.add_argument("--quiet-if-absent", action="store_true")
    p.add_argument("--all", action="store_true",
                   help="portfolio digest: a <=5-line mini-summary of every registered project")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("doctor", help="self-check the plugin package and host")
    p.add_argument("--plugin-root")
    p.add_argument("--no-probe", action="store_true")
    p.set_defaults(func=cmd_doctor)

    return parser


MUTATING_COMMANDS = {"update", "event", "blocker", "asset", "snapshot", "tree", "migrate", "session"}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    # System-heavy discipline: every mutating command runs under the write lease
    # automatically — concurrency safety is not the model's job.
    lock_target = None
    if args.command in MUTATING_COMMANDS:
        lock_target = getattr(args, "root", None) or getattr(args, "target", None)
    elif args.command == "probe" and getattr(args, "write_state", None):
        lock_target = Path(args.write_state)
    elif args.command == "events" and getattr(args, "action", "") == "read" and not getattr(args, "peek", False):
        lock_target = getattr(args, "root", None)
    if lock_target is not None:
        control = locate_control(Path(lock_target))
        if control.exists():
            with hold_lock(control):
                return args.func(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
