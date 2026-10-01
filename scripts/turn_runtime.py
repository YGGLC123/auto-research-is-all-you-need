#!/usr/bin/env python3
"""auto-research v2.8 turn runtime (G track): the close-of-turn gate.

Implements narrative-contract.md section 6 (transactions, InputManifest, turn,
the four receipt states, the machine lower bound on narrative_bearing, the
single-writer lease, the SessionStart order, and the two host-reality
clauses), section 7 (idempotency key, chronicler archive/memory) and the
turn / lease / role CLI surface of section 14.

Everything the model could otherwise fudge is computed here, not proposed:

  * the InputManifest and its digest are produced by scanning the project
    (6.2); the model never supplies a digest or a file list;
  * narrative_bearing has a machine lower bound (6.5) that the model may only
    raise (false -> true), never lower;
  * NO_INPUT_CHANGE and ACK_NON_NARRATIVE receipts are written by the CLI
    itself (6.4) -- the two states a chronicler could otherwise claim for free;
  * turn finalize re-scans before it closes, so freezing a manifest and then
    editing more files cannot smuggle the new delta past the gate (6.3);
  * every mutating command heartbeats the single-writer lease, and a second
    session is refused with PROJECT_LEASE_HELD_BY (6.6).

Tree snapshots / commits / refs are NOT written here: turn record-disposition
is an orchestration entry point that calls the one narrative commit
transaction (narrative.commit_from_ops, N track) and then writes the receipt
(6.1).

stdlib-only, Python >= 3.9. Exit codes: 0 ok, 1 contract refusal (gate closed,
lease held, missing approval), 2 environment/usage error.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros  # noqa: E402

NEWLINE = chr(10)

SCHEMA_MANIFEST = "auto-research/input-manifest-v1"
SCHEMA_RECEIPT = "auto-research/turn-receipt-v1"
SCHEMA_TURN = "auto-research/turn-v1"
SCHEMA_LEASE = "auto-research/lease-v1"
SCHEMA_EVENT = "auto-research/input-event-v1"

LEASE_TTL_SECONDS = 15 * 60          # 6.6
MEMORY_MAX_BYTES = 12 * 1024         # 7
ENVELOPE_MAX_BYTES = 8 * 1024        # 7
HYDRATE_MAX_LINES = 40               # 7 / 8
MAX_INPUT_FILE_BYTES = 32 * 1024 * 1024

RECEIPT_KINDS = (
    "NO_INPUT_CHANGE",               # CLI-automatic
    "ACK_NON_NARRATIVE",             # CLI-automatic
    "NARRATIVE_COMMITTED",           # from the one narrative commit transaction
    "NARRATIVE_REVIEWED_NO_CHANGE",  # chronicler reviewed, no tree change
)
AUTO_RECEIPT_KINDS = ("NO_INPUT_CHANGE", "ACK_NON_NARRATIVE")

# 6.5 defaults. Overridable through narrative/config.json.bearing_globs.
DEFAULT_BEARING_GLOBS = [
    "*叙事*", "*narrative*", "decisions.md", "*决策*", "*旗舰*", "*路线*", "*裁决*", "*传递*", "pro_reviews/**",
]
# Event types that make a manifest narrative-bearing on their own (6.5).
BEARING_EVENT_TYPES = {
    "narrative_cli", "claim_state_change", "decision_record",
    "branch_merge", "branch_kill", "branch_promote", "role_change",
}

# Input domain = every non-derived file in the project (6.2). The derived
# domain (.research-os/**) never flows back in; VCS metadata, caches and the
# usual binaries are noise, not narrative input.
EXCLUDED_DIR_NAMES = {
    ".research-os", ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".ipynb_checkpoints",
    ".idea", ".vscode", ".tox", ".eggs", ".cache", "site-packages",
}
EXCLUDED_SUFFIXES = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".exe", ".class", ".o", ".a", ".lib",
    ".zip", ".gz", ".bz2", ".xz", ".tar", ".7z", ".rar", ".whl", ".egg",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".ico", ".svgz", ".webp",
    ".pdf", ".mp4", ".mov", ".avi", ".mkv", ".mp3", ".wav", ".flac",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".bin", ".dat", ".db", ".sqlite", ".sqlite3", ".pkl", ".npy", ".npz", ".parquet",
    ".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ppt", ".aux", ".fdb_latexmk",
}
EXCLUDED_BASENAMES = {".DS_Store", "Thumbs.db", ".lock", "desktop.ini"}


class GateError(Exception):
    """A contract refusal. text is what a human (or a Stop hook) should read."""

    def __init__(self, text, code=1, payload=None):
        super().__init__(text)
        self.text = text
        self.code = code
        self.payload = payload or {}


# ---------------------------------------------------------------- primitives

def canonical(obj) -> str:
    """Section 2 canonical JSON -- the one serialization every digest uses."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return ros.utc_now()


def parse_ts(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def now_dt():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GateError("%s is not valid JSON: %s" % (path, exc), 2)


def write_json(path: Path, obj) -> None:
    ros.atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def append_jsonl(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical(obj) + "\n")


# ---------------------------------------------------------------- layout

def runtime_dir(control: Path) -> Path:
    return control / "runtime"


def turn_path(control: Path) -> Path:
    return runtime_dir(control) / "turn.json"


def lease_path(control: Path) -> Path:
    return runtime_dir(control) / "lease.json"


def base_snapshot_path(control: Path) -> Path:
    return runtime_dir(control) / "base_input_snapshot.json"


def last_close_snapshot_path(control: Path) -> Path:
    return runtime_dir(control) / "last_close_snapshot.json"


def manifests_dir(control: Path) -> Path:
    return runtime_dir(control) / "manifests"


def receipts_dir(control: Path) -> Path:
    return runtime_dir(control) / "receipts"


def journal_dir(control: Path) -> Path:
    return runtime_dir(control) / "journal"


def incidents_dir(control: Path) -> Path:
    return runtime_dir(control) / "incidents"


def flags_dir(control: Path) -> Path:
    return runtime_dir(control) / "flags"


def turns_dir(control: Path) -> Path:
    return runtime_dir(control) / "turns"


def events_path(control: Path) -> Path:
    return runtime_dir(control) / "events.jsonl"


def turn_counter_path(control: Path) -> Path:
    return runtime_dir(control) / "turn_counter.json"


def roles_dir(control: Path) -> Path:
    return control / "roles"


def narrative_config_path(control: Path) -> Path:
    return control / "narrative" / "config.json"


def ensure_runtime(control: Path) -> Path:
    root = runtime_dir(control)
    for path in (root, manifests_dir(control), receipts_dir(control), journal_dir(control),
                 incidents_dir(control), flags_dir(control), turns_dir(control)):
        path.mkdir(parents=True, exist_ok=True)
    return root


def project_root(control: Path) -> Path:
    return control.parent


def resolve_control(target=None) -> Path:
    """Project root / .research-os dir / any path inside the project -> control."""
    if target:
        control = ros.locate_control(Path(target))
        if (control / "state.json").exists():
            return control
        found = ros.find_project_upwards(Path(target))
        if found is None:
            raise GateError("no auto-research project at %s" % target, 2)
        return found
    found = ros.find_project_upwards(Path(os.getcwd()))
    if found is None:
        raise GateError("no auto-research project in this directory or any parent", 2)
    return found


def project_id(control: Path) -> str:
    state = read_json(control / "state.json", {}) or {}
    for key in ("project_id", "id"):
        if state.get(key):
            return str(state[key])
    project = state.get("project")
    if isinstance(project, dict) and project.get("id"):
        return str(project["id"])
    return project_root(control).name


def session_id(control: Path) -> str:
    explicit = os.environ.get("AR_SESSION")
    if explicit:
        return explicit
    try:
        found = ros.current_session_id(control)
    except BaseException:
        found = None
    if found:
        return str(found)
    return "W-" + ros.detect_host()


def idempotency_key(control: Path, turn_uuid, manifest_id, role="chronicler") -> str:
    """Section 7: sha256(project|turn_uuid|manifest_id|role)."""
    return sha256_text("|".join([project_id(control), str(turn_uuid), str(manifest_id), role]))


# ---------------------------------------------------------------- config

def load_narrative_config(control: Path) -> dict:
    cfg = read_json(narrative_config_path(control), {}) or {}
    bearing = cfg.get("bearing_globs")
    if not isinstance(bearing, list) or not bearing:
        bearing = list(DEFAULT_BEARING_GLOBS)
    derived = cfg.get("derived_globs")
    if not isinstance(derived, list):
        derived = []
    return {"bearing_globs": [str(g) for g in bearing], "derived_globs": [str(g) for g in derived],
            "input_scan_mode": cfg.get("input_scan_mode", "metadata")}


def glob_match(rel_path, pattern) -> bool:
    """Path glob with the two conveniences the default patterns assume: a bare
    name matches at any depth, and dir/** matches the whole subtree."""
    name = rel_path.rsplit("/", 1)[-1]
    patterns = [pattern]
    if not pattern.startswith("*"):
        patterns.append("*/" + pattern)
    if pattern.endswith("/**"):
        patterns.append(pattern[:-3])
        patterns.append("*/" + pattern[:-3])
    for pat in patterns:
        if fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(name, pat):
            return True
    return False


def any_glob_match(rel_path, patterns) -> bool:
    for pattern in patterns:
        if glob_match(rel_path, pattern):
            return True
    return False


# ---------------------------------------------------------------- input scan (6.2)

def _scan_snapshot(control: Path):
    """Reuse the existing turn snapshot, not a second cache or hash ledger."""
    turn = read_turn(control) or {}
    paths = []
    if turn.get("status") == "OPEN":
        ids = turn.get("manifest_ids") or []
        if ids:
            paths.append(manifest_snapshot_path(control, ids[-1]))
        paths.append(base_snapshot_path(control))
    paths.append(last_close_snapshot_path(control))
    for path in paths:
        snapshot = read_json(path, None)
        if isinstance(snapshot, dict) and isinstance(snapshot.get("files"), dict):
            return snapshot
    return {}


def _file_signature(stat):
    return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino]


def scan_inputs(control: Path, cfg=None, *, force_hash=False, dirty_paths=()):
    """Return content identities, reusing known digests for unchanged metadata.

    Metadata never enters a content digest. This is change detection, not an
    integrity audit: force_hash or input_scan_mode=content reads every file.
    """
    cfg = cfg or load_narrative_config(control)
    root = project_root(control)
    derived = cfg.get("derived_globs") or []
    previous = _scan_snapshot(control)
    old_files = previous.get("files", {})
    old_stats = previous.get("file_stats", {})
    dirty_paths = set(dirty_paths)
    strict = force_hash or cfg.get("input_scan_mode") == "content"
    files = {}
    mtimes = {}
    file_stats = {}
    hashed = 0
    reused = 0
    skipped_large = []
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in EXCLUDED_DIR_NAMES and not d.endswith(".egg-info"))
        for filename in sorted(filenames):
            if filename in EXCLUDED_BASENAMES or filename.startswith(".tmp-"):
                continue
            path = Path(dirpath) / filename
            if path.suffix.lower() in EXCLUDED_SUFFIXES:
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if derived and any_glob_match(rel, derived):
                continue
            try:
                stat = path.stat()
                if stat.st_size > MAX_INPUT_FILE_BYTES:
                    skipped_large.append(rel)
                    continue
                signature = _file_signature(stat)
                if (not strict and rel not in dirty_paths and rel in old_files
                        and old_stats.get(rel) == signature):
                    files[rel] = old_files[rel]
                    reused += 1
                else:
                    files[rel] = sha256_path(path)
                    hashed += 1
                file_stats[rel] = signature
                mtimes[rel] = int(stat.st_mtime)
            except OSError:
                continue
    return files, {"scanned_files": len(files), "skipped_large": sorted(skipped_large),
                   "mtimes": mtimes, "file_stats": file_stats,
                   "content_hashed": hashed, "digests_reused": reused,
                   "scan_mode": "content" if strict else "metadata"}


def snapshot_delta(before, after):
    """[{path, sha256, kind}] sorted by path; a removal carries the vanished sha."""
    refs = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        if old is None:
            refs.append({"path": path, "sha256": new, "kind": "added"})
        elif new is None:
            refs.append({"path": path, "sha256": old, "kind": "removed"})
        else:
            refs.append({"path": path, "sha256": new, "kind": "modified"})
    return refs


# ---------------------------------------------------------------- events (6.2 / 6.10)

def read_events(control: Path):
    path = events_path(control)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # append-only log: a torn tail must never brick the gate
    return out


def register_event(control: Path, type, payload=None, source="cli"):
    """Register a synthetic input event (6.2 / 6.10) and return its event_id.

    Append-only, lock-free by design: this is called from a PostToolUse hook
    that must never block a tool call, and a single short line append is the
    one write that stays safe without the lease."""
    ensure_runtime(control)
    payload = payload if payload is not None else {}
    body = canonical(payload)
    event_id = "ev_" + sha256_text("|".join([utc_now(), str(type), body, uuid.uuid4().hex]))[:16]
    append_jsonl(events_path(control), {
        "schema": SCHEMA_EVENT, "event_id": event_id, "type": str(type),
        "payload": payload, "payload_sha256": sha256_text(body),
        "at": utc_now(), "source": source, "session": session_id(control),
    })
    return event_id


def consumed_event_ids(control: Path):
    """Every event id already frozen into some manifest -- manifests are the
    consumption record, so no mutable cursor can drift out of sync."""
    consumed = set()
    for path in sorted(manifests_dir(control).glob("im_*.json")):
        if path.name.endswith(".snapshot.json"):
            continue
        data = read_json(path, {}) or {}
        for event in data.get("events") or []:
            if event.get("event_id"):
                consumed.add(event["event_id"])
    return consumed


def pending_events(control: Path):
    consumed = consumed_event_ids(control)
    return [e for e in read_events(control) if e.get("event_id") not in consumed]


# ---------------------------------------------------------------- incidents / flags

def write_incident(control: Path, kind, detail=None):
    ensure_runtime(control)
    incident = {
        "id": "inc_" + uuid.uuid4().hex[:12], "kind": str(kind), "at": utc_now(),
        "session": session_id(control), "detail": detail or {},
    }
    write_json(incidents_dir(control) / ("%s_%s.json" % (incident["at"].replace(":", ""), incident["id"])), incident)
    return incident


def open_incidents(control: Path):
    out = []
    for path in sorted(incidents_dir(control).glob("*.json")):
        data = read_json(path, None)
        if isinstance(data, dict) and not data.get("resolved_at"):
            out.append(data)
    return out


def set_flag(control: Path, name, value=""):
    ensure_runtime(control)
    ros.atomic_write(flags_dir(control) / name, str(value) + "\n")


def clear_flag(control: Path, name):
    path = flags_dir(control) / name
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def read_flags(control: Path):
    directory = flags_dir(control)
    if not directory.exists():
        return {}
    out = {}
    for path in sorted(directory.iterdir()):
        if path.is_file():
            try:
                out[path.name] = path.read_text(encoding="utf-8").strip()
            except OSError:
                out[path.name] = ""
    return out


# ---------------------------------------------------------------- lease (6.6)

class _lease_mutex:
    """Short exclusive section around lease.json only. Deliberately NOT the
    project write lease: record-disposition calls into narrative.py, which
    takes its own lock, and a non-reentrant outer lock would deadlock. The
    single-writer discipline the contract asks for is the lease itself."""

    def __init__(self, control: Path):
        self.path = runtime_dir(control) / ".lease.lock"
        self.acquired = False

    def __enter__(self):
        deadline = time.time() + 6.0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self.acquired = True
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = 999
                if age > 60 or time.time() > deadline:
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.acquired:
            try:
                self.path.unlink()
            except OSError:
                pass
        return False


def read_lease(control: Path):
    return read_json(lease_path(control), None)


def lease_is_expired(lease) -> bool:
    if not lease:
        return True
    try:
        return parse_ts(lease.get("expires_at")) <= now_dt()
    except (ValueError, TypeError):
        return True


def _new_lease(session, token=None):
    started = now_dt()
    return {
        "schema": SCHEMA_LEASE, "holder_session": session,
        "holder_token": token or uuid.uuid4().hex,
        "acquired_at": iso(started), "heartbeat_at": iso(started),
        "expires_at": iso(started + timedelta(seconds=LEASE_TTL_SECONDS)),
    }


def acquire_lease(control: Path, force=False, reason=None, session=None):
    """Acquire / heartbeat / take over. Returns (lease, action).

    action in {acquired, heartbeat, recovery_takeover, force_take}. A live
    holder that is not us raises PROJECT_LEASE_HELD_BY (6.6)."""
    ensure_runtime(control)
    me = session or session_id(control)
    with _lease_mutex(control):
        lease = read_lease(control)
        if lease is None:
            action = "acquired"
        elif lease.get("holder_session") == me:
            action = "heartbeat"
        elif lease_is_expired(lease):
            action = "recovery_takeover"
        elif force:
            action = "force_take"
        else:
            raise GateError(
                "PROJECT_LEASE_HELD_BY %s\nThis project is held by another live session "
                "(expires %s). Writes are refused; read-only commands still work. "
                "If that session is truly gone, wait for the lease to expire or run "
                "`lease take --force --reason \"...\"` (recorded as an incident)."
                % (lease.get("holder_session"), lease.get("expires_at")),
                1, {"holder": lease.get("holder_session"), "expires_at": lease.get("expires_at")})
        if action == "heartbeat":
            beat = now_dt()
            lease["heartbeat_at"] = iso(beat)
            lease["expires_at"] = iso(beat + timedelta(seconds=LEASE_TTL_SECONDS))
        else:
            previous = lease
            lease = _new_lease(me)
            if action == "recovery_takeover":
                lease["recovered_from"] = (previous or {}).get("holder_session")
            elif action == "force_take":
                lease["forced_from"] = (previous or {}).get("holder_session")
                lease["force_reason"] = reason
        write_json(lease_path(control), lease)
    if action == "recovery_takeover":
        # An expired holder is a recovery, not a violation: recorded, but this
        # is explicitly NOT the ordinary force incident (6.6).
        write_incident(control, "recovery_takeover",
                       {"previous_holder": lease.get("recovered_from"), "new_holder": me})
    elif action == "force_take":
        write_incident(control, "lease_force_take",
                       {"previous_holder": lease.get("forced_from"), "new_holder": me, "reason": reason})
    return lease, action


def ensure_lease(control: Path):
    """Every mutating command heartbeats the lease automatically (6.6)."""
    return acquire_lease(control)


# ---------------------------------------------------------------- turn state (6.3)

def read_turn(control: Path):
    return read_json(turn_path(control), None)


def open_turn(control: Path):
    """Open a turn: freeze the base input snapshot, allocate the display id."""
    ensure_runtime(control)
    previous = read_turn(control)
    if previous and previous.get("status") == "OPEN":
        return previous
    if previous:  # archive the closed turn so the history stays auditable
        write_json(turns_dir(control) / ("%s.json" % previous.get("turn_uuid")), previous)
    counter = (read_json(turn_counter_path(control), {"counter": 0}) or {}).get("counter", 0) + 1
    write_json(turn_counter_path(control), {"counter": counter})
    session = session_id(control)
    # The base of turn N+1 is the state turn N closed on (or the session start),
    # never "now": anything written between close and this open is a delta.
    last_close = read_json(last_close_snapshot_path(control), None)
    if last_close and isinstance(last_close.get("files"), dict):
        files = last_close["files"]
        diagnostics = {"scanned_files": len(files), "skipped_large": [],
                       "base_from": "last_close_snapshot", "closed_at": last_close.get("at"),
                       "file_stats": last_close.get("file_stats", {})}
    else:
        files, diagnostics = scan_inputs(control)
        diagnostics = dict(diagnostics, base_from="fresh_scan")
    turn_uuid = str(uuid.uuid4())
    write_json(base_snapshot_path(control), {
        "turn_uuid": turn_uuid, "at": utc_now(), "files": files,
        "file_stats": diagnostics.get("file_stats", {}),
        "diagnostics": {"scanned_files": diagnostics["scanned_files"],
                        "skipped_large": diagnostics.get("skipped_large", []),
                        "base_from": diagnostics.get("base_from")},
    })
    turn = {
        "schema": SCHEMA_TURN, "turn_uuid": turn_uuid,
        "display_id": "%s.T%04d" % (session, counter),
        "session": session, "opened_at": utc_now(), "status": "OPEN",
        "base_input_snapshot": str(base_snapshot_path(control).relative_to(control)).replace("\\", "/"),
        "manifest_ids": [],
    }
    write_json(turn_path(control), turn)
    return turn


def require_open_turn(control: Path):
    turn = read_turn(control)
    if not turn or turn.get("status") != "OPEN":
        raise GateError("no OPEN turn: run `turn probe` first", 1)
    return turn


def save_turn(control: Path, turn) -> None:
    write_json(turn_path(control), turn)


# ---------------------------------------------------------------- manifests (6.2)

def manifest_path(control: Path, manifest_id: str) -> Path:
    return manifests_dir(control) / ("%s.json" % manifest_id)


def manifest_snapshot_path(control: Path, manifest_id: str) -> Path:
    return manifests_dir(control) / ("%s.snapshot.json" % manifest_id)


def load_manifest(control: Path, manifest_id: str):
    data = read_json(manifest_path(control, manifest_id), None)
    if data is None:
        raise GateError("unknown manifest %s" % manifest_id, 2)
    return data


def manifest_base_files(control: Path, turn):
    """The file state manifest n-1 froze -- what manifest n is diffed against."""
    manifest_ids = turn.get("manifest_ids") or []
    if manifest_ids:
        snapshot = read_json(manifest_snapshot_path(control, manifest_ids[-1]), None)
        if snapshot is not None:
            return snapshot.get("files", {}), manifest_ids[-1]
    base = read_json(base_snapshot_path(control), {"files": {}}) or {"files": {}}
    return base.get("files", {}), "base_input_snapshot"


def compute_bearing(control: Path, refs, events, cfg=None):
    """Machine lower bound on narrative_bearing (6.5). The model may raise it,
    never lower it."""
    cfg = cfg or load_narrative_config(control)
    reasons = []
    for ref in refs:
        if any_glob_match(ref["path"], cfg["bearing_globs"]):
            reasons.append("bearing_path:%s" % ref["path"])
    for event in events:
        etype = event.get("type")
        if etype in BEARING_EVENT_TYPES:
            reasons.append("bearing_event:%s:%s" % (etype, event.get("event_id")))
        elif etype == "file_write":
            path = str((event.get("payload") or {}).get("path") or "")
            if path and any_glob_match(path, cfg["bearing_globs"]):
                reasons.append("bearing_event_path:%s" % path)
    return (len(reasons) > 0), sorted(set(reasons))


def build_manifest(control: Path, turn, scanned=None, events_raw=None):
    """Freeze the delta since the previous manifest into a new InputManifest.

    The digest formula is fixed by 6.2 and is the only thing a downstream
    consumer may rely on: mtime, diagnostics and event payloads stay out."""
    cfg = load_narrative_config(control)
    if events_raw is None:
        events_raw = pending_events(control)
    if scanned is None:
        dirty = [(event.get("payload") or {}).get("path") for event in events_raw]
        scanned = scan_inputs(control, cfg, dirty_paths=dirty)
    files, diagnostics = scanned
    before, base = manifest_base_files(control, turn)
    refs = snapshot_delta(before, files)
    events = [{"event_id": e.get("event_id"), "type": e.get("type"),
               "payload_sha256": e.get("payload_sha256")} for e in events_raw]
    events.sort(key=lambda e: str(e.get("event_id")))

    digest_refs = [{"path": r["path"], "sha256": r["sha256"], "kind": r["kind"]} for r in refs]
    source_delta_digest = sha256_text(canonical({"refs": digest_refs, "events": events}))
    seq = len(turn.get("manifest_ids") or [])
    manifest_id = "im_" + sha256_text(canonical({
        "turn_uuid": turn["turn_uuid"], "seq": seq, "base": base,
        "source_delta_digest": source_delta_digest,
    }))[:16]

    # A file_write event that named a path carries its event_id onto that ref.
    by_path = {}
    for event in events_raw:
        path = str((event.get("payload") or {}).get("path") or "")
        if path:
            by_path.setdefault(path, event.get("event_id"))
    enriched = []
    for ref in refs:
        item = dict(ref)
        if ref["path"] in by_path:
            item["event_id"] = by_path[ref["path"]]
        enriched.append(item)

    bearing, reasons = compute_bearing(control, refs, events_raw, cfg)
    manifest = {
        "schema": SCHEMA_MANIFEST, "id": manifest_id, "turn_uuid": turn["turn_uuid"],
        "seq": seq, "frozen_at": utc_now(), "base": base,
        "refs": enriched, "events": events, "source_delta_digest": source_delta_digest,
        "narrative_bearing": bearing, "bearing_reasons": reasons,
        "bearing_raised_by_model": False,
        "diagnostics": diagnostics,
    }
    write_json(manifest_path(control, manifest_id), manifest)
    write_json(manifest_snapshot_path(control, manifest_id), {
        "manifest_id": manifest_id, "files": files, "file_stats": diagnostics.get("file_stats", {})})
    turn.setdefault("manifest_ids", []).append(manifest_id)
    save_turn(control, turn)
    return manifest


def manifest_is_empty(manifest) -> bool:
    return not manifest.get("refs") and not manifest.get("events")


def ensure_manifest(control: Path, turn):
    """Freeze a new manifest when there is a delta, or when the turn has none
    yet (a turn with zero manifests could never be audited)."""
    manifest_ids = turn.get("manifest_ids") or []
    events_raw = pending_events(control)
    dirty = [(event.get("payload") or {}).get("path") for event in events_raw]
    scanned = scan_inputs(control, dirty_paths=dirty)
    if manifest_ids:
        before, _ = manifest_base_files(control, turn)
        files, diagnostics = scanned
        if not snapshot_delta(before, files) and not events_raw:
            # A metadata-only touch must not cause another content read on the
            # next probe. Refresh diagnostics without changing manifest identity.
            snapshot_path = manifest_snapshot_path(control, manifest_ids[-1])
            snapshot = read_json(snapshot_path, {}) or {}
            if snapshot.get("file_stats") != diagnostics["file_stats"]:
                snapshot["file_stats"] = diagnostics["file_stats"]
                write_json(snapshot_path, snapshot)
            return load_manifest(control, manifest_ids[-1]), False
    return build_manifest(control, turn, scanned=scanned, events_raw=events_raw), True


# ---------------------------------------------------------------- receipts (6.4)

def receipt_path(control: Path, turn_uuid, manifest_id) -> Path:
    return receipts_dir(control) / ("%s.%s.json" % (turn_uuid, manifest_id))


def read_receipt(control: Path, turn_uuid, manifest_id):
    return read_json(receipt_path(control, turn_uuid, manifest_id), None)


def write_receipt(control: Path, turn_uuid, manifest_id, kind, payload=None):
    """The one receipt writer (6.4). Public API: narrative.py (N track) calls
    this after its commit transaction. ESCAPED is deliberately not a receipt
    kind -- an escape is an incident, never a disposition.

    AR_SELFTEST_CRASH=write_receipt (or arm_crash("write_receipt")) makes the
    next call die like a killed process: the commit has landed, the receipt has
    not. That is 6.8 path 6, and it is only reachable when the caller opts in.
    """
    if kind not in RECEIPT_KINDS:
        raise GateError("invalid receipt kind %r (allowed: %s)" % (kind, ", ".join(RECEIPT_KINDS)), 2)
    if _CRASH_HOOK.get("write_receipt") or os.environ.get("AR_SELFTEST_CRASH") == "write_receipt":
        _CRASH_HOOK["write_receipt"] = False
        os.environ.pop("AR_SELFTEST_CRASH", None)
        raise _SimulatedCrash("simulated crash inside write_receipt (self-test)")
    ensure_runtime(control)
    path = receipt_path(control, turn_uuid, manifest_id)
    existing = read_json(path, None)
    if existing is not None and existing.get("kind") != kind:
        raise GateError(
            "receipt for turn=%s manifest=%s already exists (%s); a manifest gets exactly one "
            "receipt" % (turn_uuid, manifest_id, existing.get("kind")), 1)
    write_json(path, {
        "schema": SCHEMA_RECEIPT, "turn_uuid": turn_uuid, "manifest_id": manifest_id,
        "kind": kind, "at": utc_now(), "by": session_id(control),
        "idempotency_key": idempotency_key(control, turn_uuid, manifest_id),
        "payload": payload or {},
    })
    return path


def auto_receipt(control: Path, turn, manifest):
    """The two CLI-automatic states (6.4): nothing changed, or something
    changed that provably does not touch the narrative."""
    turn_uuid = turn["turn_uuid"]
    if read_receipt(control, turn_uuid, manifest["id"]) is not None:
        return None
    if manifest_is_empty(manifest):
        write_receipt(control, turn_uuid, manifest["id"], "NO_INPUT_CHANGE",
                      {"source_delta_digest": manifest["source_delta_digest"]})
        return "NO_INPUT_CHANGE"
    if not manifest.get("narrative_bearing"):
        write_receipt(control, turn_uuid, manifest["id"], "ACK_NON_NARRATIVE", {
            "changed_paths": [r["path"] for r in manifest.get("refs") or []][:64],
            "changed_count": len(manifest.get("refs") or []),
            "event_count": len(manifest.get("events") or []),
        })
        return "ACK_NON_NARRATIVE"
    return None


def missing_receipts(control: Path, turn):
    out = []
    for manifest_id in turn.get("manifest_ids") or []:
        if read_receipt(control, turn["turn_uuid"], manifest_id) is None:
            out.append(manifest_id)
    return out


# ---------------------------------------------------------------- journal (6.1)

def journal_write(control: Path, entry):
    write_json(journal_dir(control) / ("%s.json" % entry["journal_id"]), entry)
    return entry


def journal_open(control: Path, turn_uuid, manifest_id, ref, ops, meta):
    ensure_runtime(control)
    entry = {
        "journal_id": "jr_" + uuid.uuid4().hex[:12], "kind": "record_disposition",
        "status": "OPEN", "at": utc_now(), "session": session_id(control),
        "turn_uuid": turn_uuid, "manifest_id": manifest_id, "ref": ref,
        "idempotency_key": idempotency_key(control, turn_uuid, manifest_id),
        "ops_sha256": sha256_text(canonical(ops)), "meta": meta, "commit_ids": [],
    }
    return journal_write(control, entry)


def journal_mark(control: Path, entry, status, commit_ids=None):
    entry["status"] = status
    entry["updated_at"] = utc_now()
    if commit_ids is not None:
        entry["commit_ids"] = list(commit_ids)
    return journal_write(control, entry)


def journal_entries(control: Path):
    out = []
    for path in sorted(journal_dir(control).glob("jr_*.json")):
        data = read_json(path, None)
        if isinstance(data, dict):
            out.append(data)
    return out


def _narrative_journal_replay(control: Path):
    """Finish the N track's half-written commit transaction first (6.1).

    The two journals cover different halves of the same crash: narrative.py's
    journal replays the recorded write plan (snapshot/commit/ref/preview/memo)
    and re-delivers the receipt call that rode inside it; this module's journal
    knows which turn+manifest that transaction belonged to. Replaying the N
    track first means the G track only has to notice what is still missing --
    and neither of them ever asks a model to regenerate ops."""
    try:
        narrative = import_narrative()
    except GateError:
        return []
    replay = getattr(narrative, "journal_replay", None)
    if not callable(replay):
        return []
    try:
        return list((replay(control) or {}).get("replayed") or [])
    except Exception:
        return []


def journal_replay(control: Path):
    """6.1: restart after a crash repairs the transaction; the model is never
    asked to regenerate ops. A journal entry that reached COMMITTED means the
    commit landed -- only the receipt is missing."""
    ensure_runtime(control)
    repaired, needs_review = [], []
    entries = journal_entries(control)
    # Which receipts were missing BEFORE any repair ran: that is what makes the
    # difference between "nothing to do" and ALREADY_COMMITTED_RECEIPT_REPAIRED.
    was_missing = set()
    for entry in entries:
        owed = (entry.get("status") != "DONE"
                and read_receipt(control, entry.get("turn_uuid"),
                                 entry.get("manifest_id")) is None)
        if owed:
            was_missing.add((entry.get("turn_uuid"), entry.get("manifest_id")))
    narrative_replayed = _narrative_journal_replay(control) if was_missing else []
    for entry in entries:
        status = entry.get("status")
        if status == "DONE":
            continue
        turn_uuid, manifest_id = entry.get("turn_uuid"), entry.get("manifest_id")
        receipt = read_receipt(control, turn_uuid, manifest_id)
        if status == "OPEN" and receipt is not None and (turn_uuid, manifest_id) in was_missing:
            # The N track's replay finished the transaction and delivered the
            # receipt: the commit had landed, the receipt had not (6.8 path 6).
            commit_ids = (receipt.get("payload") or {}).get("commit_ids") or []
            journal_mark(control, entry, "DONE", commit_ids)
            repaired.append({"journal_id": entry["journal_id"], "manifest_id": manifest_id,
                             "commit_ids": commit_ids,
                             "narrative_transactions": narrative_replayed,
                             "result": "ALREADY_COMMITTED_RECEIPT_REPAIRED"})
            continue
        if status == "COMMITTED":
            if receipt is None:
                write_receipt(control, turn_uuid, manifest_id, "NARRATIVE_COMMITTED", {
                    "commit_ids": entry.get("commit_ids") or [], "ref": entry.get("ref"),
                    "repaired_by": "journal_replay",
                })
                repaired.append({"journal_id": entry["journal_id"], "manifest_id": manifest_id,
                                 "result": "ALREADY_COMMITTED_RECEIPT_REPAIRED"})
            journal_mark(control, entry, "DONE")
        elif status == "OPEN":
            if receipt is not None:
                journal_mark(control, entry, "DONE")
                continue
            commit_ids = _lookup_committed(control, entry)
            if commit_ids is not None:
                write_receipt(control, turn_uuid, manifest_id, "NARRATIVE_COMMITTED", {
                    "commit_ids": commit_ids, "ref": entry.get("ref"), "repaired_by": "journal_replay",
                })
                journal_mark(control, entry, "DONE", commit_ids)
                repaired.append({"journal_id": entry["journal_id"], "manifest_id": manifest_id,
                                 "result": "ALREADY_COMMITTED_RECEIPT_REPAIRED"})
            else:
                journal_mark(control, entry, "ABANDONED")
                needs_review.append({"journal_id": entry["journal_id"], "manifest_id": manifest_id,
                                     "result": "NO_COMMIT_FOUND_REDO_DISPOSITION"})
    return {"repaired": repaired, "needs_review": needs_review}


def _lookup_committed(control: Path, entry):
    """Ask the narrative layer whether this idempotency key already committed.
    Absent that API (N track optional), an OPEN entry stays undecidable."""
    try:
        narrative = import_narrative()
    except GateError:
        return None
    finder = getattr(narrative, "find_commit_by_idempotency", None)
    if finder is None:
        return None
    try:
        found = finder(control, entry.get("idempotency_key"),
                       turn_uuid=entry.get("turn_uuid"), manifest_id=entry.get("manifest_id"))
    except TypeError:
        try:
            found = finder(control, entry.get("idempotency_key"))
        except Exception:
            return None
    except Exception:
        return None
    if not found:
        return None
    return found if isinstance(found, list) else [found]


# ---------------------------------------------------------------- narrative bridge (6.1)

def import_narrative():
    """The one narrative commit transaction lives in narrative.py (N track).
    record-disposition is an orchestration entry point, not a second write
    path -- so if that module is absent, we refuse rather than improvise."""
    try:
        import narrative  # noqa: F401
    except ImportError as exc:
        raise GateError(
            "narrative.py is not available (%s). `turn record-disposition` with --ops-file "
            "routes through the single narrative commit transaction "
            "(narrative.commit_from_ops); it never writes tree/commit/ref itself. "
            "For a review-only turn use `--no-change --reason ...`, which needs no "
            "narrative module." % exc, 2)
    module = sys.modules["narrative"]
    if not callable(getattr(module, "commit_from_ops", None)):
        raise GateError(
            "narrative.py is present but exposes no commit_from_ops(control, ref, ops, meta, "
            "turn_uuid=, manifest_id=): the turn runtime routes tree writes through that one "
            "transaction and will not improvise a second write path. Use "
            "`--no-change --reason ...` for a review-only turn.", 2)
    return module


def _commit_ids_from(result):
    if result is None:
        return []
    if isinstance(result, str):
        return [result]
    if isinstance(result, list):
        return [str(x) for x in result]
    if isinstance(result, dict):
        for key in ("commit_ids", "commits"):
            value = result.get(key)
            if isinstance(value, list):
                return [str(x) for x in value]
        for key in ("commit_id", "id", "commit"):
            if result.get(key):
                return [str(result[key])]
    return []


class _SimulatedCrash(BaseException):
    """Self-test only. Deliberately NOT an Exception: the narrative layer's
    deliver_receipt catches Exception and falls back to writing the receipt
    itself, which would defeat the very 'commit landed, receipt did not'
    scenario 6.8 asks us to regress. A BaseException models the process dying."""


# self-test only: simulate a crash at one of the two dangerous points.
_CRASH_HOOK = {"after_commit": False, "write_receipt": False}


def arm_crash(where):
    """Arm a one-shot simulated crash ('write_receipt' or 'after_commit')."""
    if where not in _CRASH_HOOK:
        raise GateError("unknown crash point %r" % where, 2)
    _CRASH_HOOK[where] = True


# ---------------------------------------------------------------- turn commands (6.3)

def _turn_view(control, turn):
    if not turn:
        return None
    return {
        "turn_uuid": turn.get("turn_uuid"), "display_id": turn.get("display_id"),
        "session": turn.get("session"), "status": turn.get("status"),
        "opened_at": turn.get("opened_at"), "manifest_ids": turn.get("manifest_ids") or [],
    }


def _manifest_view(manifest):
    if not manifest:
        return None
    return {
        "id": manifest.get("id"), "seq": manifest.get("seq"), "base": manifest.get("base"),
        "source_delta_digest": manifest.get("source_delta_digest"),
        "narrative_bearing": manifest.get("narrative_bearing"),
        "bearing_reasons": manifest.get("bearing_reasons") or [],
        "ref_count": len(manifest.get("refs") or []),
        "event_count": len(manifest.get("events") or []),
        "paths": [r["path"] for r in (manifest.get("refs") or [])][:24],
    }


def chronicle_envelope(control, turn, manifest):
    """CHRONICLE_TURN/v1 (section 7): <= 8 KiB, <= 16 source refs, fixed objective."""
    refs = [r["path"] for r in (manifest.get("refs") or [])][:16]
    envelope = {
        "schema": "CHRONICLE_TURN/v1",
        "message_id": "msg_" + sha256_text("|".join([turn["turn_uuid"], manifest["id"]]))[:16],
        "turn_uuid": turn["turn_uuid"], "manifest_id": manifest["id"],
        "project": project_id(control),
        "bearing_reasons": manifest.get("bearing_reasons") or [],
        "source_refs": refs,
        "objective": ("Decide whether this input delta changes the narrative tree. Return "
                      "proposed_ops[], commit_meta{proposed_kind, cause_proposal, trigger_ref, "
                      "trigger_class, counterfactual?, message}, flags[], memory_delta -- "
                      "nothing else. You propose; the CLI writes."),
        "constraints": ("<=30 lines. No card_delta. Never fabricate a receipt or a digest. "
                        "If the tree does not change, say so and the CLI records "
                        "NARRATIVE_REVIEWED_NO_CHANGE."),
        "idempotency_key": idempotency_key(control, turn["turn_uuid"], manifest["id"]),
    }
    body = canonical(envelope)
    while len(body.encode("utf-8")) > ENVELOPE_MAX_BYTES and envelope["source_refs"]:
        envelope["source_refs"] = envelope["source_refs"][:-1]
        envelope["truncated"] = True
        body = canonical(envelope)
    return envelope


def turn_probe(control):
    """Open the turn if needed, freeze the current delta, auto-receipt what the
    CLI may decide by itself, and report what is still owed."""
    ensure_lease(control)
    turn = open_turn(control)
    manifest, fresh = ensure_manifest(control, turn)
    auto = auto_receipt(control, turn, manifest)
    turn = read_turn(control)
    owed = missing_receipts(control, turn)
    return {
        "ok": True, "action": "probe", "turn": _turn_view(control, turn),
        "manifest": _manifest_view(manifest), "manifest_fresh": fresh,
        "auto_receipt": auto, "receipts_missing": owed,
        "next": ("CHRONICLE_REQUIRED" if owed else "READY_TO_FINALIZE"),
        "envelope": chronicle_envelope(control, turn, manifest) if owed else None,
    }


def turn_record_disposition(control, manifest_id, ops_file=None, meta_file=None,
                            no_change=False, reason=None, ref="main", bearing=None):
    """Orchestration entry point (6.3). With tree ops it calls the one narrative
    commit transaction; with --no-change it only writes the review receipt."""
    ensure_lease(control)
    turn = require_open_turn(control)
    turn_uuid = turn["turn_uuid"]
    if manifest_id in (None, "", "current"):
        manifest_ids = turn.get("manifest_ids") or []
        if not manifest_ids:
            raise GateError("this turn has no manifest yet: run `turn probe` first", 1)
        manifest_id = manifest_ids[-1]
    manifest = load_manifest(control, manifest_id)
    if manifest.get("turn_uuid") != turn_uuid:
        raise GateError("manifest %s belongs to turn %s, not the open turn %s"
                        % (manifest_id, manifest.get("turn_uuid"), turn_uuid), 1)

    if bearing is True and not manifest.get("narrative_bearing"):
        # 6.5: the model may only raise the machine lower bound, never lower it.
        manifest["narrative_bearing"] = True
        manifest["bearing_raised_by_model"] = True
        manifest.setdefault("bearing_reasons", []).append("raised_by_model")
        write_json(manifest_path(control, manifest_id), manifest)

    if no_change and not reason:
        raise GateError("--no-change requires --reason (the chronicler must say why the "
                        "tree does not move)", 2)

    existing = read_receipt(control, turn_uuid, manifest_id)
    if existing is not None:
        return {"ok": True, "action": "record-disposition", "result": "ALREADY_RECORDED",
                "receipt": existing, "turn": _turn_view(control, turn)}

    if no_change:
        write_receipt(control, turn_uuid, manifest_id, "NARRATIVE_REVIEWED_NO_CHANGE",
                      {"reason": reason, "commit_ids": []})
        return {"ok": True, "action": "record-disposition",
                "result": "NARRATIVE_REVIEWED_NO_CHANGE", "manifest_id": manifest_id,
                "turn": _turn_view(control, read_turn(control))}

    if not ops_file or not meta_file:
        raise GateError("give either (--ops-file F --meta-file M) or (--no-change --reason R)", 2)
    ops_raw = read_json(Path(ops_file), None)
    if ops_raw is None:
        raise GateError("ops file not found: %s" % ops_file, 2)
    ops = ops_raw.get("ops") if isinstance(ops_raw, dict) else ops_raw
    if not isinstance(ops, list) or not ops:
        raise GateError("ops file must hold a non-empty list of tree ops", 2)
    meta = read_json(Path(meta_file), None)
    if not isinstance(meta, dict):
        raise GateError("meta file must hold a JSON object", 2)

    narrative = import_narrative()
    entry = journal_open(control, turn_uuid, manifest_id, ref, ops, meta)
    try:
        result = narrative.commit_from_ops(control, ref, ops, meta,
                                           turn_uuid=turn_uuid, manifest_id=manifest_id)
    except _SimulatedCrash:
        raise
    except Exception as exc:  # noqa: BLE001
        # A contract refusal from the one write transaction (6.1) must surface as
        # this CLI's own refusal, not as a traceback: the manifest simply keeps
        # owing a disposition, and the journal entry is closed as ABANDONED so
        # `journal replay` does not later mistake it for a landed commit.
        journal_mark(control, entry, "ABANDONED", [])
        code = getattr(exc, "code", None)
        detail = "%s: %s" % (code, getattr(exc, "detail", "")) if code else str(exc)
        text = (("NARRATIVE_COMMIT_REFUSED %s" + NEWLINE +
                 "  ops-file=%s meta-file=%s ref=%s manifest=%s" + NEWLINE +
                 "  Nothing was written: no commit, no ref move, no receipt. Fix the "
                 "ops and record the disposition again.")
                % (detail, ops_file, meta_file, ref, manifest_id))
        raise GateError(text, 1,
                        {"manifest_id": manifest_id, "narrative_error": str(exc)})
    commit_ids = _commit_ids_from(result)
    journal_mark(control, entry, "COMMITTED", commit_ids)
    if _CRASH_HOOK.get("after_commit"):
        _CRASH_HOOK["after_commit"] = False
        raise _SimulatedCrash("simulated crash after commit, before receipt")
    payload = {"commit_ids": commit_ids, "ref": ref}
    flags = []
    proposed_kind = meta.get("proposed_kind") or (meta.get("commit_meta") or {}).get("proposed_kind")
    if isinstance(result, dict):
        payload["kind"] = result.get("kind")
        flags = list(result.get("flags") or [])
        payload["actual_kind"] = result.get("kind")
        payload["cause"] = result.get("cause")
        payload["forced_status"] = result.get("forced_status")
    if proposed_kind:
        payload["proposed_kind"] = proposed_kind
        # 7: the CLI recomputes actual_kind; a chronicler's proposal that
        # disagrees is overridden and the override is named in the receipt.
        corrected = (payload.get("actual_kind")
                     and proposed_kind != payload["actual_kind"]
                     and "KIND_CORRECTED" not in flags)
        if corrected:
            flags.append("KIND_CORRECTED")
    payload["flags"] = sorted(set(flags))
    write_receipt(control, turn_uuid, manifest_id, "NARRATIVE_COMMITTED", payload)
    journal_mark(control, entry, "DONE", commit_ids)
    return {"ok": True, "action": "record-disposition", "result": "NARRATIVE_COMMITTED",
            "manifest_id": manifest_id, "commit_ids": commit_ids,
            "kind": payload.get("actual_kind"), "proposed_kind": proposed_kind,
            "flags": payload["flags"],
            "kind_corrected": "KIND_CORRECTED" in payload["flags"],
            "turn": _turn_view(control, read_turn(control))}


def chronicle_required_text(turn, manifest_id, manifest=None):
    """The one line a blocked Stop must show, verbatim and stable (6.3 / 15.3)."""
    lines = ["CHRONICLE_REQUIRED turn=%s manifest=%s" % (turn.get("turn_uuid"), manifest_id)]
    if manifest is not None:
        reasons = ", ".join((manifest.get("bearing_reasons") or [])[:6]) or "narrative_bearing=true"
        lines.append("  bearing: %s" % reasons)
        paths = [r["path"] for r in (manifest.get("refs") or [])][:8]
        if paths:
            lines.append("  changed: %s" % ", ".join(paths))
    lines.append("  This input delta touches the narrative and has no disposition receipt.")
    lines.append("  Dispatch the chronicler for this manifest, then record the outcome:")
    lines.append("    turn record-disposition --manifest %s --ops-file OPS --meta-file META"
                 % manifest_id)
    lines.append("    turn record-disposition --manifest %s --no-change --reason \"...\""
                 % manifest_id)
    lines.append("  A turn may only be abandoned deliberately: "
                 "turn escape --reason \"...\" --user-approved")
    return "\n".join(lines)


def turn_finalize(control, check_only=False):
    """6.3 two-stage close. Re-scan first: a manifest frozen earlier cannot
    close a turn whose inputs moved after it was frozen."""
    ensure_lease(control)
    turn = read_turn(control)
    if not turn:
        return {"ok": True, "action": "finalize", "result": "NO_TURN"}
    if turn.get("status") != "OPEN":
        return {"ok": True, "action": "finalize", "result": "ALREADY_CLOSED",
                "turn": _turn_view(control, turn)}

    if not check_only:
        journal_replay(control)
    manifest, fresh = ensure_manifest(control, turn)
    auto_receipt(control, turn, manifest)
    turn = read_turn(control)

    owed = missing_receipts(control, turn)
    if owed:
        blocking_id = owed[0]
        blocking = load_manifest(control, blocking_id)
        raise GateError(chronicle_required_text(turn, blocking_id, blocking), 1, {
            "turn_uuid": turn["turn_uuid"], "manifest_id": blocking_id,
            "receipts_missing": owed, "new_manifest_frozen": fresh,
            "envelope": chronicle_envelope(control, turn, blocking),
        })
    if check_only:
        return {"ok": True, "action": "finalize", "result": "READY_TO_CLOSE",
                "turn": _turn_view(control, turn), "check_only": True}

    turn["status"] = "CLOSED"
    turn["closed_at"] = utc_now()
    turn["closed_by"] = session_id(control)
    # Freeze the state we close on: it becomes the base of the next turn.
    _files, _diag = scan_inputs(control)
    write_json(last_close_snapshot_path(control), {"turn_uuid": turn["turn_uuid"], "at": turn["closed_at"],
                                                 "files": _files, "file_stats": _diag.get("file_stats", {})})
    save_turn(control, turn)
    write_json(turns_dir(control) / ("%s.json" % turn["turn_uuid"]), turn)
    clear_flag(control, "RECOVER_TURN_REQUIRED")
    clear_flag(control, "REHYDRATE_REQUIRED.chronicler")
    return {"ok": True, "action": "finalize", "result": "CLOSED",
            "turn": _turn_view(control, turn),
            "receipts": [read_receipt(control, turn["turn_uuid"], m).get("kind")
                         for m in turn.get("manifest_ids") or []]}


def turn_escape(control, reason, user_approved=False):
    """6.4: an escape is an incident, never a receipt."""
    if not user_approved:
        raise GateError("ESCAPE_REQUIRES_USER_APPROVAL: `turn escape` abandons a turn whose "
                        "narrative delta was never dispositioned. Re-run with --user-approved "
                        "only after the user has said so.", 1)
    if not reason:
        raise GateError("--reason is required", 2)
    ensure_lease(control)
    turn = require_open_turn(control)
    owed = missing_receipts(control, turn)
    incident = write_incident(control, "escaped", {
        "turn_uuid": turn["turn_uuid"], "display_id": turn.get("display_id"),
        "manifests_without_receipt": owed, "reason": reason, "user_approved": True,
    })
    turn["status"] = "CLOSED"
    turn["closed_at"] = utc_now()
    turn["escaped"] = True
    _files, _diag = scan_inputs(control)
    write_json(last_close_snapshot_path(control), {"turn_uuid": turn["turn_uuid"], "at": turn["closed_at"],
                                                 "files": _files, "file_stats": _diag.get("file_stats", {}), "escaped": True})
    turn["escape_incident"] = incident["id"]
    save_turn(control, turn)
    write_json(turns_dir(control) / ("%s.json" % turn["turn_uuid"]), turn)
    clear_flag(control, "RECOVER_TURN_REQUIRED")
    clear_flag(control, "REHYDRATE_REQUIRED.chronicler")
    return {"ok": True, "action": "escape", "result": "ESCAPED",
            "incident": incident["id"], "manifests_without_receipt": owed,
            "turn": _turn_view(control, turn)}


def stop_gate(control):
    """What the Stop hook calls: best-effort finalize (6.9). Returns the
    CHRONICLE_REQUIRED text when the turn may not close, else None."""
    try:
        turn = read_turn(control)
    except GateError:
        return None
    if not turn or turn.get("status") != "OPEN":
        return None
    try:
        turn_finalize(control, check_only=False)
        return None
    except GateError as exc:
        return exc.text if exc.code == 1 else None


# ---------------------------------------------------------------- SessionStart (6.7)

def session_start(control):
    """The fixed order of 6.7: OPEN turn -> holder liveness -> takeover if dead
    -> read-only if unclear -> only then recover the turn. A session instance
    may die; the turn does not die with it."""
    ensure_runtime(control)
    turn = read_turn(control)
    has_open_turn = bool(turn and turn.get("status") == "OPEN")
    lease = read_lease(control)
    me = session_id(control)
    lines = []
    result = {"ok": True, "action": "session-start", "session": me,
              "open_turn": turn.get("turn_uuid") if has_open_turn else None,
              "lease_action": None, "read_only": False, "flags": [], "lines": lines}

    try:
        lease, action = acquire_lease(control)
        result["lease_action"] = action
        result["lease_holder"] = lease.get("holder_session")
        if action == "recovery_takeover":
            lines.append("LEASE_RECOVERY_TAKEOVER from %s (previous holder let the 15 min lease "
                         "expire); this session now holds the write lease."
                         % lease.get("recovered_from"))
    except GateError as exc:
        # Holder alive and not us: read-only until it expires or is forced.
        result["read_only"] = True
        result["lease_action"] = "held_by_other"
        result["lease_holder"] = (exc.payload or {}).get("holder")
        lines.append(exc.text.splitlines()[0])
        lines.append("Read-only session: do not write narrative or turn state. "
                     "`lease take --force --reason \"...\"` is an incident, not a shortcut.")
        result["turn_recovery_deferred"] = has_open_turn
        if has_open_turn:
            lines.append("An OPEN turn (%s) exists but cannot be recovered without the lease."
                         % turn.get("display_id") or turn.get("turn_uuid"))
        return result

    if has_open_turn:
        # 6.7: recover the turn only after the lease is in hand.
        set_flag(control, "RECOVER_TURN_REQUIRED", turn["turn_uuid"])
        set_flag(control, "REHYDRATE_REQUIRED.chronicler", turn["turn_uuid"])
        result["flags"] = ["RECOVER_TURN_REQUIRED", "REHYDRATE_REQUIRED.chronicler"]
        lines.append("RECOVER_TURN_REQUIRED %s" % turn["turn_uuid"])
        lines.append("REHYDRATE_REQUIRED: chronicler")
        owed = missing_receipts(control, turn)
        lines.append("  turn %s opened %s by %s; %d manifest(s), %d without a disposition receipt."
                     % (turn.get("display_id"), turn.get("opened_at"), turn.get("session"),
                        len(turn.get("manifest_ids") or []), len(owed)))
        lines.append("  MANDATORY FIRST STEP: `role hydrate chronicler`, then `turn probe`, "
                     "then dispose of every manifest listed as missing.")
        if owed:
            lines.append("  missing: %s" % ", ".join(owed))
        replay = journal_replay(control)
        if replay["repaired"] or replay["needs_review"]:
            result["journal_replay"] = replay
            lines.append("  journal replay: %d receipt(s) repaired, %d entr(y/ies) need review."
                         % (len(replay["repaired"]), len(replay["needs_review"])))
    else:
        replay = journal_replay(control)
        if replay["repaired"] or replay["needs_review"]:
            result["journal_replay"] = replay
            lines.append("journal replay: %d receipt(s) repaired, %d entr(y/ies) need review."
                         % (len(replay["repaired"]), len(replay["needs_review"])))
        # No open turn: open one NOW so the base is frozen before the model
        # writes anything this session (a base taken at first probe would swallow
        # everything written before it as "already there").
        opened = open_turn(control)
        result["opened_turn"] = opened.get("turn_uuid")
        lines.append("TURN_OPENED %s (base=%s)" % (
            opened.get("display_id"),
            (read_json(base_snapshot_path(control), {}) or {}).get("diagnostics", {}).get("base_from", "?")))

    incidents = open_incidents(control)
    if incidents:
        result["incidents_open"] = len(incidents)
        lines.append("open runtime incidents: %d (latest: %s)"
                     % (len(incidents), incidents[-1].get("kind")))
    return result


# ---------------------------------------------------------------- roles (7)

def chronicler_memory_path(control):
    return roles_dir(control) / "chronicler.memory.json"


def plugin_root():
    return Path(__file__).resolve().parent.parent


def import_role_runtime():
    """The role fleet lives in role_runtime.py (docs/role-fleet.md). Imported
    lazily so the two modules can reference each other without a cycle."""
    try:
        import role_runtime  # noqa: F401
    except ImportError:
        return None
    return sys.modules["role_runtime"]


def role_hydrate(control, role="chronicler"):
    """Rebuild a role instance after a compaction or a session death: archive
    digest + project memory + where the turn stands. <= 40 lines by budget.

    The fleet runtime owns this now (role-fleet 7); this stays as the v2.8
    command surface and delegates to it. The body below is the fallback that
    runs only when role_runtime.py is not installed."""
    fleet = import_role_runtime()
    if fleet is not None:
        return fleet.role_hydrate(control, role)
    if role != "chronicler":
        raise GateError("unknown role %r (only chronicler is defined in v2.8)" % role, 2)
    archive = plugin_root() / "agents" / "research-chronicler.md"
    lines = ["ROLE chronicler -- archive digest"]
    if archive.exists():
        body = archive.read_text(encoding="utf-8", errors="replace")
        kept = []
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("---"):
                continue
            if stripped.startswith("#") or stripped.startswith("-") or stripped.startswith("*"):
                kept.append(stripped)
            if len(kept) >= 14:
                break
        lines.extend("  " + k for k in kept)
        lines.append("  FULL: %s" % archive)
    else:
        lines.append("  (agents/research-chronicler.md not installed yet -- R track)")
    lines.append("  Contract: you propose ops; the CLI writes. Return exactly "
                 "proposed_ops / commit_meta / flags / memory_delta, <= 30 lines.")

    memory = read_json(chronicler_memory_path(control), {}) or {}
    lines.append("PROJECT MEMORY (%d key(s), %d bytes)"
                 % (len(memory), len(canonical(memory).encode("utf-8"))))
    for key in sorted(memory)[:10]:
        value = memory[key]
        text = value if isinstance(value, str) else canonical(value)
        lines.append("  %s: %s" % (key, text[:110]))
    if len(memory) > 10:
        lines.append("  ... +%d more; FULL: %s" % (len(memory) - 10, chronicler_memory_path(control)))

    turn = read_turn(control)
    if turn and turn.get("status") == "OPEN":
        owed = missing_receipts(control, turn)
        lines.append("OPEN TURN %s (%s), manifests=%d, missing receipts=%d"
                     % (turn.get("display_id"), turn.get("turn_uuid"),
                        len(turn.get("manifest_ids") or []), len(owed)))
        for manifest_id in owed[:4]:
            manifest = load_manifest(control, manifest_id)
            lines.append("  %s bearing=%s reasons=%s"
                         % (manifest_id, manifest.get("narrative_bearing"),
                            ", ".join((manifest.get("bearing_reasons") or [])[:3])))
    else:
        lines.append("No OPEN turn; run `turn probe` when work begins.")

    clear_flag(control, "REHYDRATE_REQUIRED.chronicler")
    if len(lines) > HYDRATE_MAX_LINES:
        extra = len(lines) - (HYDRATE_MAX_LINES - 1)
        lines = lines[:HYDRATE_MAX_LINES - 1] + ["... +%d lines; FULL: %s"
                                                 % (extra, chronicler_memory_path(control))]
    return {"ok": True, "action": "role-hydrate", "role": role, "lines": lines}


def role_memory_commit(control, delta_file, role="chronicler"):
    """Merge a memory_delta into the project role memory (7, <= 12 KB).

    Delegates to the fleet runtime (role-fleet 4: lists append, dicts merge,
    null deletes); the body below is the fallback for a plugin without
    role_runtime.py, and overwrites key by key."""
    fleet = import_role_runtime()
    if fleet is not None:
        return fleet.memory_commit(control, role, delta_file)
    if role != "chronicler":
        raise GateError("unknown role %r (only chronicler is defined in v2.8)" % role, 2)
    ensure_lease(control)
    path = Path(delta_file)
    if not path.exists():
        raise GateError("delta file not found: %s" % delta_file, 2)
    raw = path.read_bytes()
    if len(raw) > MEMORY_MAX_BYTES:
        raise GateError("memory delta is %d bytes; the budget is %d (7). Compress it: the role "
                        "memory is a working set, not an archive." % (len(raw), MEMORY_MAX_BYTES), 1)
    try:
        delta = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GateError("delta file is not valid UTF-8 JSON: %s" % exc, 2)
    if isinstance(delta, dict) and isinstance(delta.get("memory_delta"), dict):
        delta = delta["memory_delta"]
    if not isinstance(delta, dict):
        raise GateError("memory delta must be a JSON object (keys with null delete)", 2)

    memory = read_json(chronicler_memory_path(control), {}) or {}
    for key, value in delta.items():
        if value is None:
            memory.pop(key, None)
        else:
            memory[key] = value
    body = json.dumps(memory, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    size = len(body.encode("utf-8"))
    if size > MEMORY_MAX_BYTES:
        raise GateError("merged memory would be %d bytes; the budget is %d (7). Drop or "
                        "summarise keys before committing." % (size, MEMORY_MAX_BYTES), 1)
    roles_dir(control).mkdir(parents=True, exist_ok=True)
    ros.atomic_write(chronicler_memory_path(control), body)
    return {"ok": True, "action": "role-memory-commit", "role": role,
            "keys": sorted(memory), "bytes": size, "path": str(chronicler_memory_path(control))}


# ---------------------------------------------------------------- self-test (6.8 / 15.4)

class _StubNarrative(object):
    """Stands in for the N track during the gate regression: it records that a
    commit happened, so 'commit landed, receipt did not' is reproducible."""

    def __init__(self):
        self.commits = []

    def commit_from_ops(self, control, ref, ops, meta, turn_uuid=None, manifest_id=None):
        commit_id = "c_" + sha256_text(canonical(
            {"ops": ops, "meta": meta, "turn": turn_uuid, "manifest": manifest_id}))[:16]
        self.commits.append(commit_id)
        return {"commit_ids": [commit_id], "kind": meta.get("proposed_kind", "refine"), "flags": []}


class _Report(object):
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append({"name": name, "ok": bool(ok), "detail": str(detail)[:220]})
        return bool(ok)

    @property
    def ok(self):
        return all(row["ok"] for row in self.rows)


def _mk_project(base, name):
    root = Path(base) / name
    (root / ".research-os").mkdir(parents=True, exist_ok=True)
    write_json(root / ".research-os" / "state.json",
               {"schema_version": "auto-research/v2", "project_id": name, "title": name})
    return root / ".research-os"


def _put(control, rel, text):
    path = project_root(control) / rel
    ros.atomic_write(path, text)
    return path


def _gate_text(fn, *a, **kw):
    """Run something that should refuse; return (raised, text, code)."""
    try:
        fn(*a, **kw)
        return False, "", 0
    except GateError as exc:
        return True, exc.text, exc.code


def run_self_test(integration=False):
    import tempfile
    report = _Report()
    saved_session = os.environ.get("AR_SESSION")
    saved_narrative = sys.modules.get("narrative")
    os.environ["AR_SESSION"] = "W001"
    tmp = tempfile.mkdtemp(prefix="ar-turn-selftest-")
    try:
        _t_path1_empty(tmp, report)
        _t_path2_non_narrative(tmp, report)
        _t_path3_missing_receipt(tmp, report)
        _t_path4_reviewed_no_change(tmp, report)
        _t_path5_frozen_then_new_input(tmp, report)
        _t_path6_crash_after_commit(tmp, report)
        _t_path7_session_recovery(tmp, report)
        _t_path8_lease(tmp, report)
        _t_assertion4(tmp, report)
        _t_digest_determinism(tmp, report)
        _t_bearing_lower_bound(tmp, report)
        _t_receipt_kinds(tmp, report)
        _t_escape_and_memory(tmp, report)
        _t_hooks_wiring(report)
        if integration:
            run_integration_test(report)
    finally:
        os.environ.pop("AR_SESSION", None)
        if saved_session is not None:
            os.environ["AR_SESSION"] = saved_session
        if saved_narrative is not None:
            sys.modules["narrative"] = saved_narrative
        else:
            sys.modules.pop("narrative", None)
        _CRASH_HOOK["after_commit"] = False
        _CRASH_HOOK["write_receipt"] = False
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
    passed = sum(1 for row in report.rows if row["ok"])
    print(json.dumps({"ok": report.ok, "passed": passed, "total": len(report.rows),
                      "checks": report.rows}, ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


def _t_path1_empty(tmp, report):
    control = _mk_project(tmp, "p1-empty")
    probe = turn_probe(control)
    report.check("path1 empty input -> NO_INPUT_CHANGE",
                 probe["auto_receipt"] == "NO_INPUT_CHANGE", probe["auto_receipt"])
    report.check("path1 nothing owed", probe["receipts_missing"] == [], probe["receipts_missing"])
    final = turn_finalize(control)
    report.check("path1 finalize closes", final["result"] == "CLOSED", final["result"])


def _t_path2_non_narrative(tmp, report):
    control = _mk_project(tmp, "p2-source")
    turn_probe(control)
    _put(control, "src/app.py", "print(1)\n")
    probe = turn_probe(control)
    report.check("path2 non-narrative -> ACK_NON_NARRATIVE",
                 probe["auto_receipt"] == "ACK_NON_NARRATIVE", probe["auto_receipt"])
    report.check("path2 bearing stays false", probe["manifest"]["narrative_bearing"] is False,
                 probe["manifest"]["bearing_reasons"])
    final = turn_finalize(control)
    report.check("path2 finalize closes", final["result"] == "CLOSED", final["result"])


def _t_path3_missing_receipt(tmp, report):
    control = _mk_project(tmp, "p3-bearing")
    turn_probe(control)
    _put(control, "docs/narrative-plan.md", "# headline moves\n")
    probe = turn_probe(control)
    manifest_id = probe["manifest"]["id"]
    report.check("path3 bearing detected", probe["manifest"]["narrative_bearing"] is True,
                 probe["manifest"]["bearing_reasons"])
    report.check("path3 no auto receipt", probe["auto_receipt"] is None, probe["auto_receipt"])
    report.check("path3 envelope built", (probe.get("envelope") or {}).get("manifest_id") == manifest_id,
                 (probe.get("envelope") or {}).get("idempotency_key"))
    texts = []
    for _ in range(3):
        raised, text, code = _gate_text(turn_finalize, control)
        texts.append((raised, text, code))
    report.check("path3 finalize refuses", all(t[0] and t[2] == 1 for t in texts))
    report.check("path3 CHRONICLE_REQUIRED text",
                 texts[0][1].startswith("CHRONICLE_REQUIRED turn=") and manifest_id in texts[0][1],
                 texts[0][1].splitlines()[0])
    report.check("path3 three Stop checks identical (assertion 3)",
                 texts[0][1] == texts[1][1] == texts[2][1])
    report.check("path3 no receipt written",
                 read_receipt(control, read_turn(control)["turn_uuid"], manifest_id) is None)
    report.check("path3 no incident written", len(open_incidents(control)) == 0)
    report.check("path3 turn still OPEN", read_turn(control)["status"] == "OPEN")
    report.check("path3 stop_gate returns the same text", stop_gate(control) == texts[0][1])


def _t_path4_reviewed_no_change(tmp, report):
    control = _mk_project(tmp, "p4-review")
    turn_probe(control)
    _put(control, "pro_reviews/r1.md", "cold review, no story change\n")
    probe = turn_probe(control)
    manifest_id = probe["manifest"]["id"]
    report.check("path4 pro_reviews is bearing", probe["manifest"]["narrative_bearing"] is True,
                 probe["manifest"]["bearing_reasons"])
    result = turn_record_disposition(control, manifest_id, no_change=True,
                                     reason="review logged as annotation; tree unchanged")
    report.check("path4 -> NARRATIVE_REVIEWED_NO_CHANGE",
                 result["result"] == "NARRATIVE_REVIEWED_NO_CHANGE", result["result"])
    receipt = read_receipt(control, read_turn(control)["turn_uuid"], manifest_id)
    report.check("path4 no fake commit", receipt["payload"]["commit_ids"] == [],
                 receipt["payload"])
    raised, _text, _code = _gate_text(turn_record_disposition, control, manifest_id,
                                      no_change=True, reason=None)
    report.check("path4 --no-change without --reason refused", raised)
    final = turn_finalize(control)
    report.check("path4 finalize closes", final["result"] == "CLOSED", final["result"])


def _t_path5_frozen_then_new_input(tmp, report):
    control = _mk_project(tmp, "p5-frozen")
    turn_probe(control)
    _put(control, "docs/narrative-a.md", "a\n")
    first = turn_probe(control)["manifest"]["id"]
    turn_record_disposition(control, first, no_change=True, reason="no tree change")
    ready = turn_finalize(control, check_only=True)
    report.check("path5 ready before the new input", ready["result"] == "READY_TO_CLOSE", ready)
    _put(control, "docs/narrative-b.md", "b\n")
    raised, text, code = _gate_text(turn_finalize, control)
    turn = read_turn(control)
    manifest_ids = turn["manifest_ids"]
    report.check("path5 frozen manifest cannot close the turn", raised and code == 1, text[:80])
    report.check("path5 a NEW manifest is required", len(manifest_ids) == 3 and manifest_ids[-1] != first,
                 manifest_ids)
    report.check("path5 the block names the new manifest", manifest_ids[-1] in text,
                 text.splitlines()[0])
    turn_record_disposition(control, manifest_ids[-1], no_change=True, reason="still no tree change")
    final = turn_finalize(control)
    report.check("path5 closes after the new manifest is dispositioned",
                 final["result"] == "CLOSED", final["result"])


def _t_path6_crash_after_commit(tmp, report):
    control = _mk_project(tmp, "p6-crash")
    stub = _StubNarrative()
    sys.modules["narrative"] = stub
    turn_probe(control)
    _put(control, "docs/叙事台账.md", "headline: C-057\n")
    probe = turn_probe(control)
    manifest_id = probe["manifest"]["id"]
    report.check("path6 CJK bearing glob matches", probe["manifest"]["narrative_bearing"] is True,
                 probe["manifest"]["bearing_reasons"])
    ops_file = project_root(control) / ".ops.json"
    meta_file = project_root(control) / ".meta.json"
    write_json(ops_file, [{"op": "patch_fields", "id": "C-041", "fields": {"statement": "x"}}])
    write_json(meta_file, {"proposed_kind": "refine", "message": "refine C-041"})
    _CRASH_HOOK["after_commit"] = True
    crashed = False
    try:
        turn_record_disposition(control, manifest_id, ops_file=str(ops_file), meta_file=str(meta_file))
    except _SimulatedCrash:
        crashed = True
    finally:
        _CRASH_HOOK["after_commit"] = False
    turn_uuid = read_turn(control)["turn_uuid"]
    report.check("path6 crash simulated after commit", crashed and len(stub.commits) == 1,
                 stub.commits)
    report.check("path6 receipt missing before replay",
                 read_receipt(control, turn_uuid, manifest_id) is None)
    replay = journal_replay(control)
    report.check("path6 replay repairs the receipt",
                 len(replay["repaired"]) == 1
                 and replay["repaired"][0]["result"] == "ALREADY_COMMITTED_RECEIPT_REPAIRED",
                 replay)
    receipt = read_receipt(control, turn_uuid, manifest_id)
    report.check("path6 repaired receipt is NARRATIVE_COMMITTED with the real commit id",
                 receipt["kind"] == "NARRATIVE_COMMITTED"
                 and receipt["payload"]["commit_ids"] == stub.commits, receipt["payload"])
    report.check("path6 replay does not re-commit", len(stub.commits) == 1, stub.commits)
    report.check("path6 replay is idempotent", journal_replay(control)["repaired"] == [])
    # the ops/meta scratch files are themselves input; dispose of that delta too
    probe2 = turn_probe(control)
    if probe2["receipts_missing"]:
        turn_record_disposition(control, probe2["receipts_missing"][0], no_change=True,
                                reason="scratch files only")
    final = turn_finalize(control)
    report.check("path6 finalize closes after repair", final["result"] == "CLOSED", final["result"])
    sys.modules.pop("narrative", None)
    raised, text, code = _gate_text(import_narrative)
    if raised:
        report.check("path6 absent narrative.py refuses loudly (no second write path)",
                     code == 2 and "commit_from_ops" in text, text[:60])
    else:
        real = sys.modules.get("narrative")
        report.check("path6 real narrative.py exposes commit_from_ops",
                     callable(getattr(real, "commit_from_ops", None)),
                     getattr(real, "__file__", "?"))
    sys.modules.pop("narrative", None)


def _t_path7_session_recovery(tmp, report):
    control = _mk_project(tmp, "p7-recover")
    turn_probe(control)
    _put(control, "docs/narrative-open.md", "open work\n")
    probe = turn_probe(control)
    turn_uuid = read_turn(control)["turn_uuid"]
    result = session_start(control)
    lines = "\n".join(result["lines"])
    report.check("path7 RECOVER_TURN_REQUIRED emitted",
                 ("RECOVER_TURN_REQUIRED %s" % turn_uuid) in lines, lines[:120])
    report.check("path7 REHYDRATE_REQUIRED emitted", "REHYDRATE_REQUIRED: chronicler" in lines)
    flags = read_flags(control)
    report.check("path7 flags set on disk",
                 flags.get("RECOVER_TURN_REQUIRED") == turn_uuid
                 and "REHYDRATE_REQUIRED.chronicler" in flags, sorted(flags))
    report.check("path7 the open manifest is still owed",
                 probe["manifest"]["id"] in missing_receipts(control, read_turn(control)))
    hydrate = role_hydrate(control, "chronicler")
    report.check("path7 hydrate is within the 40-line budget",
                 len(hydrate["lines"]) <= HYDRATE_MAX_LINES, len(hydrate["lines"]))
    report.check("path7 hydrate clears the rehydrate flag",
                 "REHYDRATE_REQUIRED.chronicler" not in read_flags(control))
    turn_record_disposition(control, probe["manifest"]["id"], no_change=True, reason="reviewed")
    turn_finalize(control)
    report.check("path7 finalize clears the recover flag",
                 "RECOVER_TURN_REQUIRED" not in read_flags(control), sorted(read_flags(control)))


def _t_path8_lease(tmp, report):
    control = _mk_project(tmp, "p8-lease")
    os.environ["AR_SESSION"] = "W001"
    turn_probe(control)
    os.environ["AR_SESSION"] = "W002"
    raised, text, code = _gate_text(turn_probe, control)
    report.check("path8 second writer refused",
                 raised and code == 1 and text.startswith("PROJECT_LEASE_HELD_BY W001"),
                 text.splitlines()[0] if text else "")
    start = session_start(control)
    report.check("path8 second session goes read-only", start["read_only"] is True,
                 start.get("lease_action"))
    report.check("path8 read-only session does not steal the turn",
                 start.get("open_turn") is not None and start.get("turn_recovery_deferred") is True)
    lease = read_lease(control)
    lease["expires_at"] = iso(now_dt() - timedelta(minutes=1))
    write_json(lease_path(control), lease)
    probe = turn_probe(control)
    report.check("path8 expired lease -> recovery takeover",
                 read_lease(control)["holder_session"] == "W002", read_lease(control))
    kinds = [i["kind"] for i in open_incidents(control)]
    report.check("path8 recovery takeover recorded", "recovery_takeover" in kinds, kinds)
    report.check("path8 recovery is not a force incident", "lease_force_take" not in kinds, kinds)
    os.environ["AR_SESSION"] = "W003"
    raised, _text, _code = _gate_text(acquire_lease, control)
    report.check("path8 live holder still refuses a third writer", raised)
    _lease, action = acquire_lease(control, force=True, reason="W002 machine died")
    report.check("path8 forced take is recorded as an incident",
                 action == "force_take"
                 and "lease_force_take" in [i["kind"] for i in open_incidents(control)], action)
    os.environ["AR_SESSION"] = "W001"


def _t_assertion4(tmp, report):
    """Contract 15.4: five cases plus the terminal assertion."""
    # (a) no delta
    c_a = _mk_project(tmp, "a4a")
    report.check("15.4a no delta -> NO_INPUT_CHANGE + close",
                 turn_probe(c_a)["auto_receipt"] == "NO_INPUT_CHANGE"
                 and turn_finalize(c_a)["result"] == "CLOSED")
    # (b) non-narrative source only
    c_b = _mk_project(tmp, "a4b")
    turn_probe(c_b)
    _put(c_b, "code/run.py", "x = 1\n")
    report.check("15.4b non-narrative -> ACK_NON_NARRATIVE + close",
                 turn_probe(c_b)["auto_receipt"] == "ACK_NON_NARRATIVE"
                 and turn_finalize(c_b)["result"] == "CLOSED")
    # (c) bearing path, no receipt
    c_c = _mk_project(tmp, "a4c")
    turn_probe(c_c)
    _put(c_c, "decisions.md", "D-1 kill the foil\n")
    turn_probe(c_c)
    raised, text, _ = _gate_text(turn_finalize, c_c)
    report.check("15.4c bearing without receipt -> CHRONICLE_REQUIRED",
                 raised and text.startswith("CHRONICLE_REQUIRED"), text.splitlines()[0] if text else "")
    # (d) frozen, then new input
    c_d = _mk_project(tmp, "a4d")
    turn_probe(c_d)
    _put(c_d, "docs/narrative-1.md", "1\n")
    first = turn_probe(c_d)["manifest"]["id"]
    turn_record_disposition(c_d, first, no_change=True, reason="no change")
    _put(c_d, "docs/narrative-2.md", "2\n")
    raised, text, _ = _gate_text(turn_finalize, c_d)
    ids = read_turn(c_d)["manifest_ids"]
    report.check("15.4d frozen manifest may not finalize; a new one is required",
                 raised and ids[-1] != first and ids[-1] in text, ids)
    # (e) bearing raised but the chronicler sees no tree change
    c_e = _mk_project(tmp, "a4e")
    turn_probe(c_e)
    _put(c_e, "docs/路线选择.md", "route B\n")
    manifest_id = turn_probe(c_e)["manifest"]["id"]
    result = turn_record_disposition(c_e, manifest_id, no_change=True, reason="tree unchanged")
    closed = turn_finalize(c_e)
    report.check("15.4e reviewed-no-change closes with no fake commit",
                 result["result"] == "NARRATIVE_REVIEWED_NO_CHANGE" and closed["result"] == "CLOSED"
                 and read_receipt(c_e, closed["turn"]["turn_uuid"], manifest_id)["payload"]["commit_ids"] == [])
    # terminal assertion: a CLOSED turn has exactly one valid receipt per manifest
    ok = True
    detail = ""
    for control in (c_a, c_b, c_e):
        turn = read_turn(control)
        for manifest_id in turn["manifest_ids"]:
            receipt = read_receipt(control, turn["turn_uuid"], manifest_id)
            if receipt is None or receipt["kind"] not in RECEIPT_KINDS:
                ok = False
                detail = "%s/%s" % (control, manifest_id)
        files = list(receipts_dir(control).glob("%s.*.json" % turn["turn_uuid"]))
        if len(files) != len(turn["manifest_ids"]):
            ok = False
            detail = "%s: %d receipts for %d manifests" % (control, len(files), len(turn["manifest_ids"]))
    report.check("15.4 terminal: one valid receipt per manifest in a CLOSED turn", ok, detail)


def _t_digest_determinism(tmp, report):
    control = _mk_project(tmp, "digest")
    turn = open_turn(control)
    _put(control, "docs/narrative-x.md", "x\n")
    _put(control, "src/b.py", "b\n")

    def digest_now():
        before, _base = manifest_base_files(control, read_turn(control))
        files, _diag = scan_inputs(control)
        refs = [{"path": r["path"], "sha256": r["sha256"], "kind": r["kind"]}
                for r in snapshot_delta(before, files)]
        events = sorted([{"event_id": e["event_id"], "type": e["type"],
                          "payload_sha256": e["payload_sha256"]} for e in pending_events(control)],
                        key=lambda e: e["event_id"])
        return sha256_text(canonical({"refs": refs, "events": events}))

    first, second = digest_now(), digest_now()
    report.check("digest is deterministic across scans", first == second, first[:16])
    register_event(control, "decision_record", {"path": "decisions.md"})
    third = digest_now()
    report.check("events participate in the digest", third != first, third[:16])
    manifest = build_manifest(control, read_turn(control))
    report.check("manifest id has the 6.2 shape",
                 manifest["id"].startswith("im_") and len(manifest["id"]) == 19, manifest["id"])
    expected = "im_" + sha256_text(canonical({
        "turn_uuid": manifest["turn_uuid"], "seq": manifest["seq"], "base": manifest["base"],
        "source_delta_digest": manifest["source_delta_digest"]}))[:16]
    report.check("manifest id follows the 6.2 formula", manifest["id"] == expected, expected)
    report.check("mtime stays out of the digest",
                 "mtimes" in manifest["diagnostics"] and manifest["source_delta_digest"] == third,
                 manifest["source_delta_digest"][:16])
    report.check("the registered event is frozen into the manifest",
                 len(manifest["events"]) == 1 and manifest["events"][0]["type"] == "decision_record")
    report.check("a frozen event is not re-consumed", pending_events(control) == [])


def _t_bearing_lower_bound(tmp, report):
    control = _mk_project(tmp, "bearing")
    cfg = load_narrative_config(control)
    bearing_paths = ["docs/叙事台账.md", "narrative/plan.md", "decisions.md",
                     "notes/旗舰路线.md", "x/裁决记录.md", "pro_reviews/r1.md",
                     "a/b/pro_reviews/r2.md", "papers/q1-narrative-notes.md"]
    plain_paths = ["src/app.py", "data/table.csv", "README.md", "figures/fig1.tex"]
    missed = [p for p in bearing_paths if not any_glob_match(p, cfg["bearing_globs"])]
    false_positives = [p for p in plain_paths if any_glob_match(p, cfg["bearing_globs"])]
    report.check("6.5 default globs catch every bearing path", not missed, missed)
    report.check("6.5 default globs do not over-trigger", not false_positives, false_positives)
    bearing, reasons = compute_bearing(control, [], [{"type": "claim_state_change", "event_id": "ev_1"}])
    report.check("6.5 a claim state change is bearing on its own", bearing, reasons)
    bearing2, _r = compute_bearing(control, [{"path": "src/a.py", "sha256": "x", "kind": "added"}],
                                   [{"type": "file_write", "event_id": "ev_2",
                                     "payload": {"path": "src/a.py"}}])
    report.check("6.5 a plain source write is not bearing", not bearing2)
    # model may raise false -> true, and the raise is recorded
    turn_probe(control)
    _put(control, "src/only.py", "1\n")
    manifest_id = turn_probe(control)["manifest"]["id"]
    turn_record_disposition(control, manifest_id, no_change=True, reason="looked, no change",
                            bearing=True)
    manifest = load_manifest(control, manifest_id)
    report.check("6.5 model can raise bearing false->true",
                 manifest["narrative_bearing"] is True and manifest["bearing_raised_by_model"] is True)


def _t_receipt_kinds(tmp, report):
    control = _mk_project(tmp, "receipts")
    turn = open_turn(control)
    raised = False
    try:
        write_receipt(control, turn["turn_uuid"], "im_x", "ESCAPED", {})
    except GateError:
        raised = True
    report.check("6.4 ESCAPED is not a receipt kind", raised)
    write_receipt(control, turn["turn_uuid"], "im_x", "NO_INPUT_CHANGE", {})
    conflict = False
    try:
        write_receipt(control, turn["turn_uuid"], "im_x", "NARRATIVE_COMMITTED", {})
    except GateError:
        conflict = True
    report.check("6.4 a manifest cannot get a second, different receipt", conflict)


def _t_escape_and_memory(tmp, report):
    control = _mk_project(tmp, "escape")
    turn_probe(control)
    _put(control, "docs/narrative-z.md", "z\n")
    turn_probe(control)
    raised, text, code = _gate_text(turn_escape, control, "user said skip", False)
    report.check("escape without approval refused",
                 raised and code == 1 and "ESCAPE_REQUIRES_USER_APPROVAL" in text)
    result = turn_escape(control, "user said skip", user_approved=True)
    kinds = [i["kind"] for i in open_incidents(control)]
    report.check("escape writes an incident, not a receipt",
                 result["result"] == "ESCAPED" and "escaped" in kinds
                 and result["manifests_without_receipt"], kinds)
    # role memory budget
    delta = project_root(control) / ".delta.json"
    write_json(delta, {"north_star": "price of search", "open": ["C-041"]})
    committed = role_memory_commit(control, str(delta))
    report.check("role memory commit writes the project memory",
                 committed["bytes"] <= MEMORY_MAX_BYTES
                 and chronicler_memory_path(control).exists(), committed["keys"])
    write_json(delta, {"blob": "x" * (MEMORY_MAX_BYTES + 10)})
    raised, text, code = _gate_text(role_memory_commit, control, str(delta))
    report.check("role memory refuses an oversized delta", raised and code == 1,
                 text.splitlines()[0] if text else "")


def _t_hooks_wiring(report):
    """doctor extension (spec): every script hooks.json wires must exist."""
    root = plugin_root()
    hooks_file = root / "hooks" / "hooks.json"
    if not hooks_file.exists():
        report.check("hooks.json present", False, str(hooks_file))
        return
    try:
        hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.check("hooks.json parses", False, str(exc))
        return
    report.check("hooks.json parses", True)
    missing, referenced = [], []
    for _event, entries in (hooks.get("hooks") or {}).items():
        for entry in entries:
            for hook in entry.get("hooks") or []:
                command = str(hook.get("command") or "")
                for token in command.replace('"', " ").split():
                    if "${CLAUDE_PLUGIN_ROOT}" in token and token.endswith(".py"):
                        rel = token.split("${CLAUDE_PLUGIN_ROOT}", 1)[1].lstrip("/\\")
                        referenced.append(rel)
                        if not (root / rel).exists():
                            missing.append(rel)
    report.check("every script referenced by hooks.json exists", not missing,
                 "missing: %s; checked %d" % (missing, len(referenced)))
    for name in ("hook_stop_guard.py", "hook_session_start.py", "hook_post_tool.py", "hook_guard.py"):
        report.check("hooks.json wires %s" % name,
                     any(r.endswith(name) for r in referenced), referenced)



# ---------------------------------------------------------------- N<->G integration (6.8 end-to-end)
#
# The gate regression above runs in-process against a stub narrative: it proves
# the turn runtime's own logic. This suite proves the *seam*: real
# `research_os.py bootstrap`, real `narrative init`, real commits through
# narrative.commit_from_ops, the real Stop hook over stdin, and a real crash
# between the commit and its receipt. Every step is a subprocess, so nothing
# here can pass because two modules happen to share an in-process object.

_SCRIPTS = Path(__file__).resolve().parent


def _integration_env(home, extra=None):
    env = dict(os.environ)
    env["AR_SESSION"] = "W900"
    # Keep the user-level portfolio (Path.home()/.claude/...) out of the test.
    env["USERPROFILE"] = str(home)
    env["HOME"] = str(home)
    env["HOMEDRIVE"] = ""
    env["HOMEPATH"] = ""
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("AR_SELFTEST_CRASH", None)
    env.update(extra or {})
    return env


def _run(script, argv, home, cwd=None, extra_env=None, stdin_text=None):
    """Run one of the plugin's CLIs and return (rc, stdout, stderr, parsed)."""
    import subprocess
    cmd = [sys.executable, str(_SCRIPTS / script)] + [str(a) for a in argv]
    data_in = None if stdin_text is None else stdin_text.encode("utf-8")
    proc = subprocess.run(cmd, input=data_in,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd=str(cwd) if cwd else None,
                          env=_integration_env(home, extra_env))
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
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
    return proc.returncode, out, err, parsed


def _write_file(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    ros.atomic_write(path, text)
    return path


def _count_commits(control):
    directory = control / "narrative" / "commits"
    return len(list(directory.glob("*.json"))) if directory.exists() else 0


def _integration_root_id(control):
    """The genesis root id, read from the committed tree. Hard-coding it here
    is what broke this suite once already: narrative.py owns the id scheme
    (it moved from N-0 to R-0 in v2.8.1) and the fixture must follow it."""
    head_file = control / "narrative" / "refs" / "heads" / "main"
    head = head_file.read_text(encoding="utf-8").strip() if head_file.exists() else ""
    commit = read_json(control / "narrative" / "commits" / ("%s.json" % head), {}) or {}
    tree = read_json(control / "narrative" / "objects" / ("%s.json" % commit.get("tree")), {}) or {}
    return tree.get("root") or "R-0"


def _claim_ops(node_id, title, statement, role=None, parent="R-0"):
    node = {"id": node_id, "node_type": "claim", "title": title,
            "summary": title, "statement": statement,
            "identity_key": {"subject": "hurdle", "target": "search procedure",
                             "predicate": "is-functional", "quantifier": "all",
                             "domain": "supervised search", "conditions": [],
                             "polarity": "positive"},
            "state": {"epistemic": "PENDING", "narrative": "ACTIVE"},
            "state_meta": {"resolution_condition": "the search grid closes",
                           "live_bet": True}}
    ops = [{"op": "add_node", "parent": parent, "node": node}]
    if role:
        ops.append({"op": "set_role", "role": role, "id": node_id})
    return {"ops": ops}


def _probe(home, root):
    rc, out, err, data = _run("turn_runtime.py", ["turn", "probe", "--root", root], home)
    return rc, data or {}, (out + err)


def _receipt_file(control, turn_uuid, manifest_id):
    return control / "runtime" / "receipts" / ("%s.%s.json" % (turn_uuid, manifest_id))


def run_integration_test(report):
    """a-e of the N<->G integration brief, plus the branch-disposition naming."""
    import tempfile
    base = Path(tempfile.mkdtemp(prefix="ar-integration-"))
    home, root, work = base / "home", base / "proj", base / "work"
    for directory in (home, root, work):
        directory.mkdir(parents=True, exist_ok=True)
    control = root / ".research-os"
    try:
        rc, _out, err, data = _run("research_os.py", ["bootstrap", root, "--title", "T"], home)
        report.check("integration setup: research_os bootstrap",
                     rc == 0 and (data or {}).get("ok") is True, err[:200])
        rc, _out, err, data = _run("narrative.py",
                                   ["init", root, "--title", "T", "--venue", "RFS"], home)
        report.check("integration setup: narrative init creates the genesis commit",
                     rc == 0 and (data or {}).get("commit_id"), err[:200])
        base_commits = _count_commits(control)

        _integration_a(report, home, root, work, control, base_commits)
        _integration_b(report, home, root, work, control)
        _integration_c(report, home, root, work, control)
        _integration_d(report, home, root, work, control)
        _integration_e(report, home, root, work, control)
        _integration_f_branch(report, home, root, work, control)
        _integration_final(report, home, root, work, control)
    finally:
        try:
            import shutil
            shutil.rmtree(str(base), ignore_errors=True)
        except Exception:
            pass


def _integration_a(report, home, root, work, control, base_commits):
    """a. bearing input -> chronicler ops -> one commit -> receipt -> finalize."""
    rc, probe, _ = _probe(home, root)
    report.check("a1 first probe opens a turn and self-receipts the empty delta",
                 rc == 0 and probe.get("auto_receipt") == "NO_INPUT_CHANGE"
                 and probe["turn"]["status"] == "OPEN", probe.get("auto_receipt"))

    _write_file(root / "裁决_headline.md",
                "# 裁决\n\nthe headline becomes the functional claim.\n")
    rc, probe, _ = _probe(home, root)
    manifest_id = (probe.get("manifest") or {}).get("id")
    report.check("a2 a bearing_globs hit raises narrative_bearing and owes a receipt",
                 rc == 0 and probe["manifest"]["narrative_bearing"] is True
                 and probe["next"] == "CHRONICLE_REQUIRED"
                 and probe["receipts_missing"] == [manifest_id],
                 probe["manifest"].get("bearing_reasons"))
    report.check("a3 the CHRONICLE_TURN envelope is handed over with the manifest",
                 (probe.get("envelope") or {}).get("manifest_id") == manifest_id
                 and (probe["envelope"].get("idempotency_key") or "") != "",
                 sorted((probe.get("envelope") or {}).keys()))

    # ops/meta live OUTSIDE the project: a chronicler's proposal is not an input.
    ops_file = _write_file(work / "ops_a.json", canonical(
        _claim_ops("C-001", "the hurdle is a functional",
                   "The hurdle is a functional of the procedure, not a constant.",
                   role="headline", parent=_integration_root_id(control))))
    meta_file = _write_file(work / "meta_a.json", canonical({
        "message": "the functional claim becomes the headline",
        "proposed_kind": "refine",          # deliberately wrong: 3.1 must correct it
        "author": {"session": "W900", "role": "chronicler"},
        "trigger_class": "review-pressure",
        "memory_delta": {"last_headline": "C-001"}}))
    rc, out, err, data = _run("turn_runtime.py",
                              ["turn", "record-disposition", "--root", root,
                               "--manifest", manifest_id, "--ops-file", ops_file,
                               "--meta-file", meta_file], home)
    data = data or {}
    report.check("a4 record-disposition returns NARRATIVE_COMMITTED with commit_ids",
                 rc == 0 and data.get("result") == "NARRATIVE_COMMITTED"
                 and len(data.get("commit_ids") or []) == 1, (rc, err[:200]))
    report.check("a5 exactly one commit landed for this manifest",
                 _count_commits(control) == base_commits + 1,
                 (_count_commits(control), base_commits))
    report.check("a6 3.1 recomputes kind: a set_role is restructure, not the proposed refine",
                 data.get("kind") == "restructure" and data.get("proposed_kind") == "refine"
                 and data.get("kind_corrected") is True, data.get("flags"))

    turn_uuid = (data.get("turn") or {}).get("turn_uuid")
    receipt = read_json(_receipt_file(control, turn_uuid, manifest_id), None) or {}
    payload = receipt.get("payload") or {}
    report.check("a7 the receipt is CLI-built: NARRATIVE_COMMITTED + commit_ids + KIND_CORRECTED",
                 receipt.get("kind") == "NARRATIVE_COMMITTED"
                 and payload.get("commit_ids") == data.get("commit_ids")
                 and "KIND_CORRECTED" in (payload.get("flags") or []), payload)
    memory = read_json(control / "roles" / "chronicler.memory.json", {}) or {}
    report.check("a8 the memory_delta rode inside the same commit transaction",
                 memory.get("last_headline") == "C-001", sorted(memory))

    rc, out, err, data = _run("turn_runtime.py", ["turn", "finalize", "--root", root], home)
    report.check("a9 finalize closes the turn once every manifest has a receipt",
                 rc == 0 and (data or {}).get("result") == "CLOSED", (rc, err[:200]))

    rc, out, err, card = _run("narrative.py", ["card", root], home)
    roles = ((card or {}).get("narrative_part") or {}).get("roles") or {}
    report.check("a10 the derived card follows main: headline = C-001",
                 (roles.get("headline") or {}).get("node") == "C-001", roles.get("headline"))


def _integration_b(report, home, root, work, control):
    """b. bearing input the chronicler reviewed without moving the tree."""
    before = _count_commits(control)
    _probe(home, root)                       # open a fresh turn first
    _write_file(root / "research" / "notes" / "plain.md", "a note that touches no story.\n")
    rc, probe, _ = _probe(home, root)
    report.check("b0 a persistent non-narrative change self-receipts ACK_NON_NARRATIVE",
                 probe.get("auto_receipt") == "ACK_NON_NARRATIVE"
                 and probe["manifest"]["narrative_bearing"] is False,
                 (probe.get("auto_receipt"), probe["manifest"].get("paths")))
    _write_file(root / "pro_reviews" / "r1.md", "cold review: the headline is fine.\n")
    rc, probe, _ = _probe(home, root)
    manifest_id = probe["manifest"]["id"]
    report.check("b1 pro_reviews/** is narrative-bearing",
                 probe["manifest"]["narrative_bearing"] is True
                 and probe["next"] == "CHRONICLE_REQUIRED",
                 probe["manifest"].get("bearing_reasons"))
    rc, out, err, data = _run("turn_runtime.py",
                              ["turn", "record-disposition", "--root", root,
                               "--manifest", manifest_id, "--no-change",
                               "--reason", "review logged; the tree does not move"], home)
    report.check("b2 --no-change yields NARRATIVE_REVIEWED_NO_CHANGE",
                 rc == 0 and (data or {}).get("result") == "NARRATIVE_REVIEWED_NO_CHANGE",
                 (rc, err[:200]))
    report.check("b3 no phantom commit was written for a reviewed-no-change turn",
                 _count_commits(control) == before, (_count_commits(control), before))
    turn_uuid = ((data or {}).get("turn") or {}).get("turn_uuid")
    receipt = read_json(_receipt_file(control, turn_uuid, manifest_id), None) or {}
    report.check("b4 the review receipt carries the reason and an empty commit_ids",
                 receipt.get("kind") == "NARRATIVE_REVIEWED_NO_CHANGE"
                 and (receipt.get("payload") or {}).get("commit_ids") == []
                 and (receipt.get("payload") or {}).get("reason"), receipt.get("payload"))
    rc, out, err, data = _run("turn_runtime.py", ["turn", "finalize", "--root", root], home)
    report.check("b5 finalize closes on a review-only receipt",
                 rc == 0 and (data or {}).get("result") == "CLOSED", (rc, err[:200]))


def _integration_c(report, home, root, work, control):
    """c. crash between the landed commit and its receipt (6.8 path 6, real modules)."""
    _probe(home, root)
    _write_file(root / "叙事_c.md", "the statement is sharpened.\n")
    rc, probe, _ = _probe(home, root)
    manifest_id = probe["manifest"]["id"]
    turn_uuid = probe["turn"]["turn_uuid"]
    before = _count_commits(control)

    ops_file = _write_file(work / "ops_c.json", canonical({"ops": [
        {"op": "patch_fields", "id": "C-001",
         "fields": {"statement": "The hurdle is a functional of the procedure (sharpened)."}}]}))
    meta_file = _write_file(work / "meta_c.json", canonical({
        "message": "sharpen C-001", "proposed_kind": "refine",
        "author": {"session": "W900", "role": "chronicler"}}))
    rc, out, err, _ = _run("turn_runtime.py",
                           ["turn", "record-disposition", "--root", root,
                            "--manifest", manifest_id, "--ops-file", ops_file,
                            "--meta-file", meta_file], home,
                           extra_env={"AR_SELFTEST_CRASH": "write_receipt"})
    report.check("c1 the armed crash kills the process after the commit, before the receipt",
                 rc != 0 and "_SimulatedCrash" in (err + out), (rc, err[-160:]))
    after_crash = _count_commits(control)
    report.check("c2 the commit itself landed on disk",
                 after_crash == before + 1, (before, after_crash))
    receipt_file = _receipt_file(control, turn_uuid, manifest_id)
    report.check("c3 the receipt did NOT land", not receipt_file.exists(), str(receipt_file))
    rc, out, err, data = _run("turn_runtime.py",
                              ["turn", "finalize", "--root", root, "--check-only"], home)
    report.check("c4 --check-only diagnoses the missing receipt without repairing it",
                 rc == 1 and out.startswith("CHRONICLE_REQUIRED")
                 and not receipt_file.exists(), (rc, out[:80]))

    rc, out, err, data = _run("turn_runtime.py", ["journal", "replay", "--root", root], home)
    repaired = (data or {}).get("repaired") or []
    report.check("c5 journal replay reports ALREADY_COMMITTED_RECEIPT_REPAIRED once",
                 rc == 0 and len(repaired) == 1
                 and repaired[0]["result"] == "ALREADY_COMMITTED_RECEIPT_REPAIRED", (rc, data))
    report.check("c6 replay repaired the receipt without writing a second commit",
                 _count_commits(control) == after_crash, (_count_commits(control), after_crash))
    receipt = read_json(receipt_file, None) or {}
    report.check("c7 the repaired receipt names the one commit that landed",
                 receipt.get("kind") == "NARRATIVE_COMMITTED"
                 and len((receipt.get("payload") or {}).get("commit_ids") or []) == 1,
                 receipt.get("payload"))
    rc, out, err, data = _run("narrative.py", ["validate", root], home)
    report.check("c8 narrative validate is clean after the replay (no unfinished journal)",
                 rc == 0 and (data or {}).get("ok") is True, (data or {}).get("problems"))
    rc, out, err, data = _run("turn_runtime.py", ["turn", "finalize", "--root", root], home)
    report.check("c9 the recovered turn now closes",
                 rc == 0 and (data or {}).get("result") == "CLOSED", (rc, err[:200]))


def _integration_d(report, home, root, work, control):
    """d. the Stop hook over stdin: blocked without a receipt, silent with one."""
    _probe(home, root)
    _write_file(root / "路线_d.md", "route change under consideration.\n")
    rc, probe, _ = _probe(home, root)
    manifest_id = probe["manifest"]["id"]
    payload = json.dumps({"cwd": str(root), "hook_event_name": "Stop",
                          "stop_hook_active": False}, ensure_ascii=False)
    rc, out, err, data = _run("hook_stop_guard.py", [], home, stdin_text=payload)
    decision = data or {}
    reason = str(decision.get("reason") or "")
    report.check("d1 Stop is blocked with the verbatim CHRONICLE_REQUIRED text",
                 rc == 0 and decision.get("decision") == "block"
                 and reason.startswith("CHRONICLE_REQUIRED turn="), (rc, out[:120]))
    report.check("d2 the block names the manifest that owes a disposition",
                 manifest_id in reason, manifest_id)
    report.check("d3 the hook's stdout is valid UTF-8 even with CJK paths in the delta",
                 "路线_d.md" in reason, out[:160])

    _run("turn_runtime.py", ["turn", "record-disposition", "--root", root,
                             "--manifest", manifest_id, "--no-change",
                             "--reason", "route note only; no tree change"], home)
    rc, out, err, data = _run("hook_stop_guard.py", [], home, stdin_text=payload)
    report.check("d4 with the receipt in place the Stop hook lets the turn end",
                 rc == 0 and out.strip() == "", out[:160])
    turn = read_json(control / "runtime" / "turn.json", {}) or {}
    report.check("d5 the hook's own finalize closed the turn",
                 turn.get("status") == "CLOSED", turn.get("status"))


def _integration_e(report, home, root, work, control):
    """e. chronicler hydrate + the <=12 KB project memory."""
    rc, out, err, _ = _run("turn_runtime.py",
                           ["role", "hydrate", "chronicler", "--root", root], home)
    lines = [line for line in out.splitlines() if line.strip()]
    report.check("e1 role hydrate chronicler prints an archive digest",
                 rc == 0 and lines and lines[0].startswith("ROLE chronicler")
                 and len(lines) <= HYDRATE_MAX_LINES, (rc, len(lines)))
    report.check("e2 the digest carries the project memory written by earlier turns",
                 any("last_headline" in line for line in lines), lines[:3])
    delta = _write_file(work / "delta_e.json", canonical(
        {"open_questions": ["does the grid close the live bet?"],
         "north_star_note": "the hurdle is a functional"}))
    rc, out, err, data = _run("turn_runtime.py",
                              ["role", "memory", "commit", "--root", root,
                               "--delta-file", delta], home)
    data = data or {}
    report.check("e3 role memory commit lands and stays under the 12 KB budget",
                 rc == 0 and data.get("ok") is True
                 and 0 < int(data.get("bytes") or 0) <= MEMORY_MAX_BYTES,
                 (rc, data.get("bytes")))
    memory = read_json(control / "roles" / "chronicler.memory.json", {}) or {}
    report.check("e4 the delta merged into the existing memory, it did not replace it",
                 memory.get("last_headline") == "C-001"
                 and memory.get("open_questions"), sorted(memory))


def _integration_f_branch(report, home, root, work, control):
    """Contract alignment: a promoted branch is MERGED, strategy lives in meta."""
    rc, out, err, data = _run("narrative.py",
                              ["branch", "create", root, "alt-headline",
                               "--from", "main"], home)
    report.check("f1 branch create appends the first OPEN event",
                 rc == 0 and (data or {}).get("disposition") == "OPEN", (rc, err[:160]))
    ops_file = _write_file(work / "ops_f.json", canonical({"ops": [
        {"op": "patch_fields", "id": "C-001",
         "fields": {"summary": "alt-line rewording of the headline"}}]}))
    meta_file = _write_file(work / "meta_f.json", canonical({"message": "alt wording"}))
    rc, out, err, data = _run("narrative.py",
                              ["commit", root, "--ref", "alt-headline",
                               "--ops-file", ops_file, "--meta-file", meta_file], home)
    report.check("f2 a refine lands on the side line",
                 rc == 0 and (data or {}).get("kind") == "refine", (rc, err[:160]))
    rc, out, err, data = _run("narrative.py",
                              ["branch", "promote", root, "alt-headline"], home)
    report.check("f3 promote fast-forwards main",
                 rc == 0 and (data or {}).get("strategy") == "fast-forward", (rc, err[:160]))
    registry = read_json(control / "narrative" / "branches" / "registry.json", {}) or {}
    entry = (registry.get("branches") or {}).get("alt-headline") or {}
    history = entry.get("disposition_history") or []
    last = history[-1] if history else {}
    report.check("f4 the promotion disposition is the contract's MERGED, not PROMOTED",
                 last.get("disposition") == "MERGED",
                 [h.get("disposition") for h in history])
    report.check("f5 fast-forward vs two-parent lives in the event meta, not in a new enum",
                 (last.get("meta") or {}).get("strategy") == "fast-forward"
                 and (last.get("meta") or {}).get("into") == "main", last.get("meta"))
    rc, out, err, data = _run("narrative.py", ["branch", "list", root], home)
    rows = (data or {}).get("branches") or []
    report.check("f6 a merged branch is no longer UNRESOLVED_BRANCH",
                 bool(rows) and all(not row.get("unresolved") for row in rows), rows)


def _integration_final(report, home, root, work, control):
    """Closing invariants: one receipt per manifest, doctor green, thin forwards wired."""
    receipts = control / "runtime" / "receipts"
    bodies = [read_json(p, {}) or {} for p in sorted(receipts.glob("*.json"))]
    per_manifest = {}
    for body in bodies:
        per_manifest.setdefault((body.get("turn_uuid"), body.get("manifest_id")), []).append(body)
    report.check("z1 every manifest has exactly one receipt (6.3 terminal assertion)",
                 bool(bodies) and all(len(v) == 1 for v in per_manifest.values()),
                 dict((k[1], len(v)) for k, v in per_manifest.items() if len(v) != 1))
    kinds = sorted(set(body.get("kind") for body in bodies))
    report.check("z2 all four receipt states were exercised end to end",
                 set(RECEIPT_KINDS) <= set(kinds), kinds)
    rc, out, err, data = _run("research_os.py", ["doctor", "--no-probe"], home)
    report.check("z3 research_os doctor is green",
                 rc == 0 and (data or {}).get("ok") is True,
                 [c.get("name") for c in (data or {}).get("checks") or [] if not c.get("ok")])
    have_backfill = (_SCRIPTS / "narrative_backfill.py").exists()
    rc, out, err, data = _run("narrative.py",
                              ["backfill", "status", "--project", str(root)], home)
    report.check("z4 `narrative backfill` forwards verbatim to narrative_backfill",
                 (rc == 0 if have_backfill
                  else (rc == 2 and "narrative_backfill.py" in (out + err))),
                 (have_backfill, rc, (out + err)[:200]))
    have_html = (_SCRIPTS / "narrative_render_html.py").exists()
    html_out = work / "axis.html"
    rc, out, err, data = _run("narrative.py",
                              ["render", root, "--view", "axis", "--html",
                               "--out", str(html_out)], home)
    if have_html:
        text = ""
        if html_out.exists():
            with io.open(str(html_out), encoding="utf-8") as handle:
                text = handle.read()
        report.check("z5 `narrative render --html` forwards to narrative_render_html",
                     rc == 0 and html_out.exists() and "<html" in text.lower(),
                     (rc, (out + err)[:160]))
    else:
        report.check("z5 `narrative render --html` fails loudly without the U-track module",
                     rc == 2 and "narrative_render_html.py" in (out + err),
                     (rc, (out + err)[:160]))
    rc, out, err, data = _run("narrative.py",
                              ["render", root, "--view", "reader", "--html"], home)
    report.check("z5b --html is refused for a non-axis view",
                 rc == 2 and "view axis" in (out + err), (rc, (out + err)[:160]))
    rc, out, err, data = _run("narrative.py", ["render", root, "--view", "axis"], home)
    report.check("z6 the text axis view still renders from the committed head",
                 rc == 0 and "C-001" in out, out[:120])


# ---------------------------------------------------------------- CLI

def emit(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="turn_runtime.py",
        description="auto-research v2.8 close-of-turn gate: turn / lease / role runtime.")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_root(p):
        p.add_argument("--root", help="project root, .research-os dir, or any path inside "
                                      "the project (default: search upwards from cwd)")
        return p

    p_turn = sub.add_parser("turn", help="turn lifecycle (contract 6.3)")
    turn_sub = p_turn.add_subparsers(dest="action", required=True)
    with_root(turn_sub.add_parser("probe", help="open/refresh the turn and freeze the delta"))
    p_rec = with_root(turn_sub.add_parser("record-disposition",
                                          help="record how a manifest was dispositioned"))
    p_rec.add_argument("--manifest", default="current")
    p_rec.add_argument("--ops-file")
    p_rec.add_argument("--meta-file")
    p_rec.add_argument("--ref", default="main")
    p_rec.add_argument("--no-change", action="store_true",
                       help="chronicler reviewed the delta and the tree does not move")
    p_rec.add_argument("--reason", help="required with --no-change")
    p_rec.add_argument("--bearing", choices=["true"],
                       help="raise narrative_bearing on this manifest (false->true only)")
    p_fin = with_root(turn_sub.add_parser("finalize", help="two-stage close (Stop hook calls "
                                                           "this with no arguments)"))
    p_fin.add_argument("--check-only", action="store_true", help="diagnose without closing")
    p_esc = with_root(turn_sub.add_parser("escape", help="abandon a turn (writes an incident)"))
    p_esc.add_argument("--reason", required=True)
    p_esc.add_argument("--user-approved", action="store_true")
    with_root(turn_sub.add_parser("session-start", help="the 6.7 SessionStart order"))
    with_root(turn_sub.add_parser("status", help="read-only view of turn/lease/receipts"))

    p_lease = sub.add_parser("lease", help="single-writer lease (contract 6.6)")
    lease_sub = p_lease.add_subparsers(dest="action", required=True)
    with_root(lease_sub.add_parser("status"))
    p_take = with_root(lease_sub.add_parser("take"))
    p_take.add_argument("--force", action="store_true")
    p_take.add_argument("--reason")

    p_role = sub.add_parser("role", help="role archive / memory (contract 7)")
    role_sub = p_role.add_subparsers(dest="action", required=True)
    p_hyd = with_root(role_sub.add_parser("hydrate"))
    p_hyd.add_argument("role", nargs="?", default="chronicler")
    p_mem = role_sub.add_parser("memory")
    mem_sub = p_mem.add_subparsers(dest="memop", required=True)
    p_mem_commit = with_root(mem_sub.add_parser("commit"))
    p_mem_commit.add_argument("role", nargs="?", default="chronicler")
    p_mem_commit.add_argument("--delta-file", required=True)

    p_journal = sub.add_parser("journal", help="crash recovery (contract 6.1)")
    journal_sub = p_journal.add_subparsers(dest="action", required=True)
    with_root(journal_sub.add_parser("replay"))
    with_root(journal_sub.add_parser("status"))

    p_event = sub.add_parser("event", help="synthetic input events (contract 6.2 / 6.10)")
    event_sub = p_event.add_subparsers(dest="action", required=True)
    p_ev_reg = with_root(event_sub.add_parser("register"))
    p_ev_reg.add_argument("--type", required=True)
    p_ev_reg.add_argument("--path", help="shorthand for --payload with a single path key")
    p_ev_reg.add_argument("--payload", help="inline JSON object")
    with_root(event_sub.add_parser("list"))

    p_manifest = sub.add_parser("manifest", help="inspect frozen input manifests")
    manifest_sub = p_manifest.add_subparsers(dest="action", required=True)
    p_show = with_root(manifest_sub.add_parser("show"))
    p_show.add_argument("--id", default="current")

    p_self = sub.add_parser("self-test", help="contract 6.8 regression paths + assertion 4")
    p_self.add_argument("--integration", action="store_true",
                        help="also run the N<->G end-to-end suite (real CLIs, real commits, "
                             "real Stop hook, real crash/replay)")
    return parser


def cmd_turn(args):
    control = resolve_control(getattr(args, "root", None))
    if args.action == "probe":
        return emit(turn_probe(control))
    if args.action == "record-disposition":
        return emit(turn_record_disposition(
            control, args.manifest, ops_file=args.ops_file, meta_file=args.meta_file,
            no_change=args.no_change, reason=args.reason, ref=args.ref,
            bearing=(True if args.bearing == "true" else None)))
    if args.action == "finalize":
        return emit(turn_finalize(control, check_only=args.check_only))
    if args.action == "escape":
        return emit(turn_escape(control, args.reason, user_approved=args.user_approved))
    if args.action == "session-start":
        result = session_start(control)
        for line in result.get("lines") or []:
            print(line)
        if not result.get("lines"):
            print(json.dumps({"ok": True, "action": "session-start", "quiet": True}))
        return 0
    turn = read_turn(control)
    return emit({"ok": True, "action": "status", "turn": _turn_view(control, turn),
                 "lease": read_lease(control),
                 "receipts_missing": missing_receipts(control, turn) if turn else [],
                 "flags": read_flags(control), "incidents_open": len(open_incidents(control))})


def cmd_lease(args):
    control = resolve_control(getattr(args, "root", None))
    if args.action == "status":
        lease = read_lease(control)
        return emit({"ok": True, "action": "lease-status", "lease": lease,
                     "expired": lease_is_expired(lease), "me": session_id(control),
                     "held_by_me": bool(lease and lease.get("holder_session") == session_id(control))})
    if args.force and not args.reason:
        raise GateError("--force requires --reason (a forced take is recorded as an incident)", 2)
    lease, action = acquire_lease(control, force=args.force, reason=args.reason)
    return emit({"ok": True, "action": "lease-take", "result": action, "lease": lease})


def cmd_role(args):
    control = resolve_control(getattr(args, "root", None))
    if args.action == "hydrate":
        result = role_hydrate(control, args.role)
        for line in result["lines"]:
            print(line)
        return 0
    return emit(role_memory_commit(control, args.delta_file, args.role))


def cmd_journal(args):
    control = resolve_control(getattr(args, "root", None))
    if args.action == "replay":
        return emit(dict({"ok": True, "action": "journal-replay"}, **journal_replay(control)))
    return emit({"ok": True, "action": "journal-status",
                 "entries": [{"journal_id": e.get("journal_id"), "status": e.get("status"),
                              "manifest_id": e.get("manifest_id"),
                              "commit_ids": e.get("commit_ids")} for e in journal_entries(control)]})


def cmd_event(args):
    control = resolve_control(getattr(args, "root", None))
    if args.action == "list":
        pending = pending_events(control)
        return emit({"ok": True, "action": "event-list", "pending": len(pending),
                     "events": [{"event_id": e.get("event_id"), "type": e.get("type"),
                                 "at": e.get("at"), "payload": e.get("payload")}
                                for e in pending[-40:]]})
    payload = {}
    if args.payload:
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as exc:
            raise GateError("--payload is not valid JSON: %s" % exc, 2)
    if args.path:
        payload["path"] = args.path
    event_id = register_event(control, args.type, payload)
    return emit({"ok": True, "action": "event-register", "event_id": event_id, "type": args.type})


def cmd_manifest(args):
    control = resolve_control(getattr(args, "root", None))
    manifest_id = args.id
    if manifest_id == "current":
        turn = read_turn(control)
        ids = (turn or {}).get("manifest_ids") or []
        if not ids:
            raise GateError("no manifest frozen yet in this project", 1)
        manifest_id = ids[-1]
    manifest = load_manifest(control, manifest_id)
    receipt = read_receipt(control, manifest.get("turn_uuid"), manifest_id)
    return emit({"ok": True, "action": "manifest-show", "manifest": manifest,
                 "receipt": receipt})


DISPATCH = {"turn": cmd_turn, "lease": cmd_lease, "role": cmd_role,
            "journal": cmd_journal, "event": cmd_event, "manifest": cmd_manifest}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.command == "self-test":
        return run_self_test(integration=bool(getattr(args, "integration", False)))
    try:
        return DISPATCH[args.command](args)
    except GateError as exc:
        # Human/hook-readable text on stdout (it is quoted verbatim into a Stop
        # block); machine-readable failure on stderr.
        print(exc.text)
        print(json.dumps({"ok": False, "error": exc.text.splitlines()[0],
                          "code": exc.code, "payload": exc.payload}, ensure_ascii=False),
              file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
