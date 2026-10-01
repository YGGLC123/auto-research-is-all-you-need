#!/usr/bin/env python3
"""auto-research phase-procedure CLI (P4): claim / run / ledger.

Three procedure groups that turn phase discipline into mechanically enforced
state, reusing research_os for locking, events, and session identity (same-dir
import), exactly like collab.py:

  claim   — formal-claim lifecycle. Each claim is a frozen-then-adjudicated
            spec under research/claims/<id>/, mirrored as a `C` node in the
            attempt tree. A verdict is refused on an unfrozen spec (a verdict
            on a moving target is meaningless); revising a frozen spec bumps
            spec_version and archives the old sha (the audit trail is enforced,
            not requested). Five terminal verdicts: proven / refuted /
            conditional / barrier / equivalent_core.
  run     — experiment-run bookkeeping under .research-os/runs/<run_id>.json.
            This ledger RECORDS runs (register -> launch -> finish) and
            reconciles stale in-flight runs by run_id; it never EXECUTES —
            execution belongs to the executor / compute server.
  ledger  — the four evidence ledgers under research/evidence/<name>.jsonl
            (search-log / evidence / closest-prior / data-provenance) with
            required-key validation and the UNVERIFIED_RECOLLECTION machine
            flag for unverified evidence recollections.

Mutating actions hold ros.hold_lock (via main), except the three that either
shell out to research_os.py's own write lease (claim init/verdict, which call
`tree register`/`tree close`) or run unbounded user commands (claim
regression) — those manage a narrow inner lease themselves, so the outer lease
is never held across a subprocess (which would deadlock) or a long regression
suite (which would be stolen as stale).

stdlib-only. Exit codes: 0 ok, 1 contract refusal, 2 environment/usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros  # noqa: E402

ROS_SCRIPT = str(Path(ros.__file__).resolve())

CLAIM_VERDICTS = {"proven", "refuted", "conditional", "barrier", "equivalent_core"}
VERDICT_TREE_STATUS = {  # claim verdict -> attempt-tree terminal status
    "proven": "proven", "refuted": "refuted",
    "conditional": "inconclusive", "barrier": "inconclusive", "equivalent_core": "inconclusive",
}
VERDICT_PREFIX = {"conditional": "CONDITIONAL: ", "barrier": "BARRIER: ", "equivalent_core": "EQUIVALENT_CORE: "}
RUN_STATUSES = {"registered", "running", "done", "failed"}
LEDGERS = {  # ledger name -> required keys
    "search-log": ("query", "source", "date"),
    "evidence": ("id", "claim", "source", "relation", "confidence"),
    "closest-prior": ("axis", "prior", "risk"),
    "data-provenance": ("source", "license", "coverage"),
}
KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


# ---------------------------------------------------------------- shared helpers


# ============================================================ v3 §2  real-time derivation

GRAPH_RULES_CLAIM_VERDICT = ("claim-verdict",)
GRAPH_RULES_RUN_FINISH = ("run-claim",)


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


def require_project(control: Path) -> None:
    if not (control / "state.json").exists():
        ros.die(f"no project state under {control}; bootstrap first")


def run_ros(*cmd_args) -> dict:
    """Invoke research_os.py as a subprocess — it manages its own write lease,
    so the caller must NOT be holding the lock. Returns parsed stdout JSON."""
    argv = [sys.executable, ROS_SCRIPT, *[str(a) for a in cmd_args]]
    proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        ros.die(f"research_os.py {' '.join(str(a) for a in cmd_args[:2])} failed: {detail}", 1)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        ros.die(f"research_os.py returned non-JSON: {proc.stdout[:200]!r}", 2)
    raise AssertionError


# --- minimal frontmatter parser/writer (no third-party YAML): scalars + inline lists

def parse_frontmatter(text: str) -> tuple:
    """Return (frontmatter_dict, raw_body). Handles `key: value` and `key: [a, b]`."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm: dict = {}
    for line in parts[1].splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            fm[key] = [x.strip() for x in inner.split(",") if x.strip()] if inner else []
        else:
            fm[key] = val
    return fm, parts[2]


def dump_frontmatter(fm: dict, body: str) -> str:
    lines = []
    for key, val in fm.items():
        if isinstance(val, list):
            items = ", ".join(str(x).replace(",", " ").replace("\n", " ").replace("\r", " ") for x in val)
            lines.append(f"{key}: [{items}]")
        else:
            lines.append(f"{key}: {str(val).replace(chr(10), ' ').replace(chr(13), ' ')}")
    return "---\n" + "\n".join(lines) + "\n---" + body


# ---------------------------------------------------------------- claim group

def claim_dir(control: Path, claim_id: str) -> Path:
    return control.parent / "research" / "claims" / claim_id


def load_claim(control: Path, claim_id: str) -> tuple:
    path = claim_dir(control, claim_id) / "spec.md"
    if not path.exists():
        ros.die(f"unknown claim {claim_id!r} (no research/claims/{claim_id}/spec.md)", 1)
    fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return path, fm, body


def save_claim(path: Path, fm: dict, body: str) -> None:
    ros.atomic_write(path, dump_frontmatter(fm, body))


def claim_init(args, control: Path) -> int:
    claim_id = args.id
    if not KEBAB.match(claim_id or ""):
        ros.die(f"--id must be kebab-case (a-z, 0-9, single hyphens): {claim_id!r}", 1)
    path = claim_dir(control, claim_id) / "spec.md"
    if path.exists():
        ros.die(f"claim {claim_id!r} already exists at {path}; use freeze/revise/verdict", 1)
    title = " ".join(args.statement.split())[:60]
    tree_args = ["tree", args.root, "register", "--kind", "claim", "--title", title]
    if args.tree_parent:
        tree_args += ["--parent", args.tree_parent]
    result = run_ros(*tree_args)  # research_os holds/releases its own lease here
    code = result.get("code")
    if not code:
        ros.die(f"tree register returned no code: {result}", 2)
    fm = {"id": claim_id, "status": "open", "spec_version": 1,
          "tree_code": code, "created_at": ros.utc_now()}
    body = "\n\n" + args.statement.strip() + "\n"
    with ros.hold_lock(control):
        save_claim(path, fm, body)
        ros.append_event(control, {"event": "claim_opened", "summary": f"{claim_id} [{code}]: {title}",
                                   "claim": claim_id, "node": code})
    print(json.dumps({"ok": True, "id": claim_id, "tree_code": code, "spec": str(path)}, ensure_ascii=False))
    return 0


def claim_freeze(args, control: Path) -> int:  # under main() lock
    path, fm, body = load_claim(control, args.id)
    if fm.get("frozen_sha"):
        ros.die(f"claim {args.id} is already frozen (sha {str(fm['frozen_sha'])[:12]}); "
                "use `claim revise` to change a frozen spec", 1)
    sha = hashlib.sha256(body.strip().encode("utf-8")).hexdigest()
    fm["frozen_sha"] = sha
    fm["frozen_at"] = ros.utc_now()
    save_claim(path, fm, body)
    print(json.dumps({"ok": True, "id": args.id, "frozen_sha": sha}, ensure_ascii=False))
    return 0


def claim_revise(args, control: Path) -> int:  # under main() lock
    path, fm, body = load_claim(control, args.id)
    try:
        fm["spec_version"] = int(str(fm.get("spec_version", 1))) + 1
    except ValueError:
        fm["spec_version"] = 2
    if fm.get("frozen_sha"):
        history = fm.get("sha_history") or []
        if not isinstance(history, list):
            history = [history]
        history.append(fm["frozen_sha"])
        fm["sha_history"] = history
        fm.pop("frozen_sha", None)
        fm.pop("frozen_at", None)
    save_claim(path, fm, body)
    print(json.dumps({"ok": True, "id": args.id, "spec_version": fm["spec_version"]}, ensure_ascii=False))
    return 0


def claim_verdict(args, control: Path) -> int:
    if not args.result:
        ros.die("claim verdict requires --result (the adjudication outcome)")
    path, fm, body = load_claim(control, args.id)
    if not fm.get("frozen_sha"):
        ros.die(f"claim {args.id} is not frozen: a verdict on an unfrozen spec is meaningless "
                "(freeze it first, so the adjudicated statement can never move)", 1)
    code = fm.get("tree_code")
    if not code:
        ros.die(f"claim {args.id} has no tree_code to close", 2)
    tree_result = VERDICT_PREFIX.get(args.status, "") + args.result
    close_args = ["tree", args.root, "close", "--code", code,
                  "--status", VERDICT_TREE_STATUS[args.status], "--result", tree_result, "--force"]
    for ev in args.evidence or []:
        close_args += ["--evidence", ev]
    run_ros(*close_args)  # research_os holds/releases its own lease here
    fm["status"] = "adjudicated"
    fm["verdict"] = args.status
    fm["verdict_result"] = args.result
    fm["verdict_at"] = ros.utc_now()
    if args.evidence:
        fm["evidence"] = list(args.evidence)
    with ros.hold_lock(control):
        save_claim(path, fm, body)
        ros.append_event(control, {"event": "claim_adjudicated",
                                   "summary": f"{args.id} [{args.status}]: {str(args.result)[:120]}",
                                   "claim": args.id, "node": code, "verdict": args.status})
    # v3 §2: the adjudicated spec is on disk and the tree node is closed; the
    # graph now signs the claim's edges with the verdict it just acquired.
    graph = _graph_after(control, GRAPH_RULES_CLAIM_VERDICT, "procedures:claim verdict")
    payload = {"ok": True, "id": args.id, "verdict": args.status,
               "tree_node": code, "tree_status": VERDICT_TREE_STATUS[args.status]}
    if graph is not None:
        payload["graph"] = graph
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def claim_regression(args, control: Path) -> int:
    if args.op == "add":
        if not args.cmd:
            ros.die("claim regression add requires --cmd")
        with ros.hold_lock(control):
            path, fm, body = load_claim(control, args.id)
            regs = fm.get("regressions") or []
            if not isinstance(regs, list):
                regs = [regs]
            regs.append(args.cmd)
            fm["regressions"] = regs
            save_claim(path, fm, body)
        print(json.dumps({"ok": True, "id": args.id, "regressions": len(regs)}, ensure_ascii=False))
        return 0
    # op == run — execute WITHOUT the lock (a suite may outlast the stale lease), then lock only the event.
    _path, fm, _body = load_claim(control, args.id)
    regs = fm.get("regressions") or []
    if isinstance(regs, str):
        regs = [regs]
    if not regs:
        print(json.dumps({"ok": True, "id": args.id, "note": "no regressions registered"}, ensure_ascii=False))
        return 0
    cwd = claim_dir(control, args.id)
    for i, cmd in enumerate(regs, 1):
        proc = subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            tail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[-20:])
            with ros.hold_lock(control):
                ros.append_event(control, {"event": "claim_regressions_fail",
                                           "summary": f"{args.id}: cmd #{i} exited {proc.returncode}: {str(cmd)[:80]}",
                                           "claim": args.id})
            print(json.dumps({"ok": False, "id": args.id, "failed_cmd": cmd, "index": i,
                              "returncode": proc.returncode, "stderr_tail": tail}, ensure_ascii=False))
            return 1
    with ros.hold_lock(control):
        ros.append_event(control, {"event": "claim_regressions_pass",
                                   "summary": f"{args.id}: {len(regs)} regression(s) green", "claim": args.id})
    print(json.dumps({"ok": True, "id": args.id, "passed": len(regs)}, ensure_ascii=False))
    return 0


def claim_list(args, control: Path) -> int:
    base = control.parent / "research" / "claims"
    out = []
    if base.exists():
        for spec in sorted(base.glob("*/spec.md")):
            fm, _ = parse_frontmatter(spec.read_text(encoding="utf-8"))
            raw_ver = str(fm.get("spec_version", ""))
            out.append({
                "id": fm.get("id", spec.parent.name),
                "status": fm.get("status"),
                "spec_version": int(raw_ver) if raw_ver.isdigit() else fm.get("spec_version"),
                "frozen": bool(fm.get("frozen_sha")),
                "verdict": fm.get("verdict"),
                "tree_code": fm.get("tree_code"),
            })
    print(json.dumps({"ok": True, "count": len(out), "claims": out}, ensure_ascii=False, indent=2))
    return 0


def cmd_claim(args) -> int:
    control = ros.locate_control(args.root)
    require_project(control)
    return {"init": claim_init, "freeze": claim_freeze, "revise": claim_revise,
            "verdict": claim_verdict, "regression": claim_regression, "list": claim_list}[args.action](args, control)


# ---------------------------------------------------------------- run group

def runs_dir(control: Path) -> Path:
    return control / "runs"


def load_run(control: Path, run_id: str) -> tuple:
    path = runs_dir(control) / f"{run_id}.json"
    if not path.exists():
        ros.die(f"unknown run {run_id!r}", 1)
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        ros.die(f"{run_id}.json invalid JSON: {exc}", 1)
    raise AssertionError


def save_run(control: Path, run: dict) -> None:
    ros.atomic_write(runs_dir(control) / f"{run['run_id']}.json",
                     json.dumps(run, ensure_ascii=False, indent=2) + "\n")


def next_run_id(control: Path, pid: str) -> str:
    best = 0
    directory = runs_dir(control)
    if directory.exists():
        for path in directory.glob(f"{pid}-r*.json"):
            match = re.match(rf"^{re.escape(pid)}-r(\d+)$", path.stem)
            if match:
                best = max(best, int(match.group(1)))
    return f"{pid}-r{best + 1:02d}"


def run_register(args, control: Path) -> int:
    run_id = next_run_id(control, args.protocol)
    run = {
        "schema_version": "auto-research/run-v1",
        "run_id": run_id, "protocol": args.protocol, "command": args.command,
        "parent_run_id": args.parent or None, "tree_node": args.node or "",
        "note": args.note or "", "from_session": ros.current_session_id(control),
        "status": "registered", "registered_at": ros.utc_now(),
        "launched_at": None, "finished_at": None, "result": None, "artifacts": [],
    }
    save_run(control, run)
    ros.append_event(control, {"event": "run_registered",
                               "summary": f"{run_id} [{args.protocol}]: {str(args.command)[:100]}",
                               "run": run_id, "node": args.node or ""})
    print(json.dumps({"ok": True, "run_id": run_id, "status": "registered"}, ensure_ascii=False))
    return 0


def run_launch(args, control: Path) -> int:
    path, run = load_run(control, args.id)
    if run.get("status") != "registered":
        ros.die(f"{args.id} is {run.get('status')}; only a registered run can be launched", 1)
    run["status"] = "running"
    run["launched_at"] = ros.utc_now()
    save_run(control, run)
    ros.append_event(control, {"event": "run_launched", "summary": f"{args.id} running", "run": args.id})
    print(json.dumps({"ok": True, "run_id": args.id, "status": "running"}, ensure_ascii=False))
    return 0


def run_finish(args, control: Path) -> int:
    if not args.result:
        ros.die("run finish requires --result")
    path, run = load_run(control, args.id)
    if run.get("status") != "running":
        ros.die(f"{args.id} is {run.get('status')}; only a running run can be finished "
                "(register -> launch -> finish)", 1)
    artifacts = args.artifact or []
    if args.status == "done" and not artifacts:
        ros.die("a done run names at least one --artifact path (disk is the delivery channel)")
    for rel in artifacts:
        if not (control.parent / rel).exists():
            ros.die(f"artifact {rel!r} does not exist under the project root — "
                    "a finished run may not point at nothing", 1)
    run.update({"status": args.status, "finished_at": ros.utc_now(),
                "result": args.result, "artifacts": artifacts})
    save_run(control, run)
    ros.append_event(control, {"event": "run_finished",
                               "summary": f"{args.id} [{args.status}]: {str(args.result)[:100]}",
                               "run": args.id, "status": args.status, "artifacts": artifacts})
    # v3 §2: a settled run is evidence; an in-flight one is not, so this is the
    # first moment `run-claim` may state a supported_by edge for it.
    graph = _graph_after(control, GRAPH_RULES_RUN_FINISH, "procedures:run finish")
    payload = {"ok": True, "run_id": args.id, "status": args.status}
    if graph is not None:
        payload["graph"] = graph
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def run_reconcile(args, control: Path) -> int:
    now = datetime.now(timezone.utc)
    stale = []
    directory = runs_dir(control)
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            try:
                run = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if run.get("status") != "running":
                continue
            launched = run.get("launched_at")
            age_h = None
            if launched:
                try:
                    started = datetime.fromisoformat(str(launched).replace("Z", "+00:00"))
                    age_h = (now - started).total_seconds() / 3600.0
                except ValueError:
                    age_h = None
            if age_h is None or age_h >= args.stale_hours:
                stale.append({"run_id": run.get("run_id"), "protocol": run.get("protocol"),
                              "launched_at": launched,
                              "age_hours": round(age_h, 2) if age_h is not None else None,
                              "command": str(run.get("command", ""))[:120]})
    print(json.dumps({"ok": True, "stale_hours": args.stale_hours, "count": len(stale),
                      "stale_running": stale,
                      "note": "reconcile each by run_id against the executor / compute-server ledger before "
                              "relaunching — do NOT blindly re-run (a running row may still be alive)"},
                     ensure_ascii=False, indent=2))
    return 0


def run_list(args, control: Path) -> int:
    directory = runs_dir(control)
    runs = []
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            try:
                runs.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    if args.status:
        runs = [r for r in runs if r.get("status") == args.status]
    slim = [{k: r.get(k) for k in ("run_id", "protocol", "status", "tree_node",
                                   "parent_run_id", "launched_at", "finished_at")} for r in runs]
    print(json.dumps({"ok": True, "count": len(slim), "runs": slim}, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args) -> int:
    control = ros.locate_control(args.root)
    require_project(control)
    return {"register": run_register, "launch": run_launch, "finish": run_finish,
            "reconcile": run_reconcile, "list": run_list}[args.action](args, control)


# ---------------------------------------------------------------- ledger group

def ledger_file(control: Path, name: str) -> Path:
    return control.parent / "research" / "evidence" / f"{name}.jsonl"


def ledger_add(args, control: Path) -> int:
    name = args.ledger
    required = LEDGERS[name]
    data = ros.parse_kv(args.data, "--data")
    missing = [k for k in required if k not in data]
    if missing:
        ros.die(f"ledger {name} requires keys {list(required)}; missing: {missing}", 1)
    entry = dict(data)
    entry["ts"] = ros.utc_now()
    session = ros.current_session_id(control)
    if session:
        entry["session"] = session
    # Machine flag echoing the evidence discipline: an unverified recollection is marked, never trusted.
    if name == "evidence" and str(data.get("verified", "0")).strip() == "0":
        entry["flag"] = "UNVERIFIED_RECOLLECTION"
    path = ledger_file(control, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    ros.append_event(control, {"event": "ledger_appended",
                               "summary": (f"{name}: " + ", ".join(f"{k}={data[k]}" for k in required))[:120],
                               "ledger": name})
    print(json.dumps({"ok": True, "ledger": name, "flag": entry.get("flag")}, ensure_ascii=False))
    return 0


def ledger_list(args, control: Path) -> int:
    name = args.ledger
    where = ros.parse_kv(args.where, "--where") if args.where else {}
    path = ledger_file(control, name)
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if all(str(row.get(k)) == v for k, v in where.items()):
                rows.append(row)
    print(json.dumps({"ok": True, "ledger": name, "count": len(rows), "rows": rows},
                     ensure_ascii=False, indent=2))
    return 0


def ledger_render(args, control: Path) -> int:
    ev_dir = control.parent / "research" / "evidence"
    lines = ["# Evidence Ledgers (auto-generated — append via `procedures.py ledger add`, never by hand)", ""]
    for name, required in LEDGERS.items():
        path = ledger_file(control, name)
        rows = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        cols = list(required) + (["verified", "flag"] if name == "evidence" else [])
        lines.append(f"## {name} ({len(rows)} rows)")
        lines.append("")
        if rows:
            lines.append("| " + " | ".join(cols) + " |")
            lines.append("|" + "|".join(["---"] * len(cols)) + "|")
            for row in rows[-5:]:  # bounded: only the last five, full log stays on disk
                lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
            if len(rows) > 5:
                lines.append("")
                lines.append(f"_... {len(rows) - 5} earlier row(s) omitted; full log: research/evidence/{name}.jsonl_")
        else:
            lines.append("_(empty)_")
        lines.append("")
    out = ev_dir / "LEDGERS.md"
    ev_dir.mkdir(parents=True, exist_ok=True)
    ros.atomic_write(out, "\n".join(lines) + "\n")
    print(json.dumps({"ok": True, "rendered": str(out), "ledgers": list(LEDGERS)}, ensure_ascii=False))
    return 0


def cmd_ledger(args) -> int:
    control = ros.locate_control(args.root)
    require_project(control)
    return {"add": ledger_add, "list": ledger_list, "render": ledger_render}[args.action](args, control)


# ---------------------------------------------------------------- parser & main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="procedures", description=__doc__)
    groups = parser.add_subparsers(dest="group", required=True)

    # ---- claim
    claim_p = groups.add_parser("claim", help="formal-claim lifecycle (freeze -> siege -> verdict) + tree C-node mirror")
    claim_p.set_defaults(func=cmd_claim)
    csub = claim_p.add_subparsers(dest="action", required=True)

    c = csub.add_parser("init", help="open a claim spec + register its C node")
    c.add_argument("root", type=Path)
    c.add_argument("--id", required=True, help="stable kebab-case claim id")
    c.add_argument("--statement", required=True, help="the formal assertion to be frozen")
    c.add_argument("--tree-parent", dest="tree_parent", help="parent tree code (e.g. R01) for the C node")

    c = csub.add_parser("freeze", help="hash + freeze the spec (a verdict is allowed only after this)")
    c.add_argument("root", type=Path)
    c.add_argument("--id", required=True)

    c = csub.add_parser("revise", help="bump spec_version, archive old sha, clear frozen (audit trail)")
    c.add_argument("root", type=Path)
    c.add_argument("--id", required=True)

    c = csub.add_parser("verdict", help="adjudicate a FROZEN spec; mirrors to a tree close")
    c.add_argument("root", type=Path)
    c.add_argument("--id", required=True)
    c.add_argument("--status", required=True, choices=sorted(CLAIM_VERDICTS))
    c.add_argument("--result", required=True)
    c.add_argument("--evidence", action="append", help="evidence path / run-id (repeatable)")

    c = csub.add_parser("regression", help="counterexample regressions: add commands, or run them")
    c.add_argument("root", type=Path)
    c.add_argument("op", choices=("add", "run"))
    c.add_argument("--id", required=True)
    c.add_argument("--cmd", help="shell command to append (op=add)")

    c = csub.add_parser("list", help="list all claims as JSON")
    c.add_argument("root", type=Path)

    # ---- run
    run_p = groups.add_parser("run", help="experiment-run bookkeeping (records + reconciles; never executes)")
    run_p.set_defaults(func=cmd_run)
    rsub = run_p.add_subparsers(dest="action", required=True)

    r = rsub.add_parser("register", help="book a run_id for a protocol")
    r.add_argument("root", type=Path)
    r.add_argument("--protocol", required=True)
    r.add_argument("--command", required=True, help="the command the executor will run")
    r.add_argument("--parent", help="parent run_id (retry lineage)")
    r.add_argument("--node", help="attempt-tree node this run serves")
    r.add_argument("--note")

    r = rsub.add_parser("launch", help="mark a registered run as running")
    r.add_argument("root", type=Path)
    r.add_argument("--id", required=True)

    r = rsub.add_parser("finish", help="close a running run (done needs a real artifact on disk)")
    r.add_argument("root", type=Path)
    r.add_argument("--id", required=True)
    r.add_argument("--status", required=True, choices=("done", "failed"))
    r.add_argument("--result", required=True)
    r.add_argument("--artifact", action="append", help="project-relative delivered path (must exist)")

    r = rsub.add_parser("reconcile", help="list stale running runs to reconcile by run_id (no state change)")
    r.add_argument("root", type=Path)
    r.add_argument("--stale-hours", dest="stale_hours", type=float, default=24.0)

    r = rsub.add_parser("list", help="list runs as JSON")
    r.add_argument("root", type=Path)
    r.add_argument("--status", choices=sorted(RUN_STATUSES))

    # ---- ledger
    led_p = groups.add_parser("ledger", help="the four evidence ledgers (structured, key-validated CRUD)")
    led_p.set_defaults(func=cmd_ledger)
    lsub = led_p.add_subparsers(dest="action", required=True)

    l = lsub.add_parser("add", help="append a key-validated row to an evidence ledger")
    l.add_argument("root", type=Path)
    l.add_argument("--ledger", required=True, choices=sorted(LEDGERS))
    l.add_argument("--data", action="append", metavar="key=value", help="repeatable; required keys per ledger")

    l = lsub.add_parser("list", help="list ledger rows (optionally --where key=value)")
    l.add_argument("root", type=Path)
    l.add_argument("--ledger", required=True, choices=sorted(LEDGERS))
    l.add_argument("--where", action="append", metavar="key=value")

    l = lsub.add_parser("render", help="render research/evidence/LEDGERS.md (bounded to last 5 rows/ledger)")
    l.add_argument("root", type=Path)

    return parser


# (group, action) pairs that mutate state. Self-locked ones manage their own
# lease (they shell out to research_os.py or run unbounded user commands), so
# main() must NOT wrap them in the outer lease.
MUTATING = {
    ("claim", "init"), ("claim", "freeze"), ("claim", "revise"),
    ("claim", "verdict"), ("claim", "regression"),
    ("run", "register"), ("run", "launch"), ("run", "finish"),
    ("ledger", "add"), ("ledger", "render"),
}
SELF_LOCKED = {("claim", "init"), ("claim", "verdict"), ("claim", "regression")}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    key = (args.group, getattr(args, "action", None))
    if key in MUTATING and key not in SELF_LOCKED:
        control = ros.locate_control(Path(args.root))
        if control.exists():
            with ros.hold_lock(control):
                return args.func(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
