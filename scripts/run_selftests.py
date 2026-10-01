#!/usr/bin/env python3
"""Run every self-test in the package, one line per suite, one verdict at the end.

Each script owns its own machine-checkable acceptance tests and prints a report;
this runner only knows how each one is invoked, reads the reports, and adds them
up.  stdlib-only; works with whatever interpreter runs it.

    python scripts/run_selftests.py            # all suites
    python scripts/run_selftests.py --quick    # skip the two integration suites
    python scripts/run_selftests.py --json     # machine-readable summary

Exit code 0 only when every suite reports ok and exits 0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S = "scripts"
ART = "skills/research-artifacts/scripts"

# Package health checks are reported, but counted apart from the self-tests.
PACKAGE_CHECKS = {"state CLI doctor", "package links"}

# (name, script path relative to the plugin root, argv, integration?)
SUITES = [
    ("state CLI doctor", f"{S}/research_os.py", ["doctor"], False),
    ("package links", f"{S}/lint_package.py", [], False),
    ("turn runtime", f"{S}/turn_runtime.py", ["self-test"], False),
    ("turn runtime (integration)", f"{S}/turn_runtime.py", ["self-test", "--integration"], True),
    ("role runtime", f"{S}/role_runtime.py", ["role", "self-test"], False),
    ("research graph", f"{S}/research_graph.py", ["self-test"], False),
    ("narrative tree", f"{S}/narrative.py", ["self-test"], False),
    ("narrative audit", f"{S}/narrative_audit.py", ["--self-test"], False),
    ("narrative backfill", f"{S}/narrative_backfill.py", ["self-test"], False),
    ("narrative page", f"{S}/narrative_render_html.py", ["--self-test"], False),
    ("narrative live Q&A", f"{S}/narrative_serve.py", ["--self-test"], False),
    ("graph page", f"{S}/graph_render_html.py", ["--self-test"], False),
    ("research map", f"{S}/research_map.py", ["--self-test"], False),
    ("taste ledger", f"{S}/taste_ledger.py", ["--self-test"], False),
    ("literature ledger", f"{S}/literature_index.py", ["--self-test"], False),
    ("growth library", f"{S}/growth.py", ["self-test"], False),
    ("figure router", f"{S}/figure_router.py", ["self-test"], False),
    ("figure router (integration)", f"{S}/figure_router.py", ["self-test", "--integration"], True),
    ("component index", f"{S}/component_index.py", ["self-test"], False),
    ("visual contract", f"{ART}/visual_contract.py", ["self-test"], False),
    ("figure studio", f"{ART}/figure_studio.py", ["self-test"], False),
]

TEXT_VERDICT = re.compile(r"self-test: (\d+)/(\d+)(?: cases)? passed")


def count_checks(report: dict) -> int:
    """Suites report their size in one of a few shapes; take the first that fits."""
    if isinstance(report.get("steps"), list):
        return len(report["steps"])
    for key in ("checks", "total", "passed", "files_checked"):
        value = report.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, list):
            return len(value)
    summary = report.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("PASS"), int):
        return summary["PASS"] + int(summary.get("FAIL") or 0)
    for key in ("rows", "results", "cases"):
        if isinstance(report.get(key), list):
            return len(report[key])
    return 0


def parse_report(stdout: str):
    """A JSON report, or the one-line text verdict some suites print."""
    text = stdout.strip()
    if not text:
        return None
    if not text.startswith("{"):
        verdict = TEXT_VERDICT.search(text)
        if verdict:
            done, total = int(verdict.group(1)), int(verdict.group(2))
            return {"ok": done == total, "checks": total}
    try:
        report = json.loads(text)
    except json.JSONDecodeError:
        start = text.rfind("\n{")
        try:
            report = json.loads(text[start + 1:] if start >= 0 else text[text.index("{"):])
        except (ValueError, json.JSONDecodeError):
            return None
    if isinstance(report, dict) and report.get("self_test") == "pass":
        report["ok"] = True
    return report


def run_suite(name, script, argv, timeout):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    started = time.time()
    try:
        proc = subprocess.run([sys.executable, str(ROOT / script)] + argv, cwd=str(ROOT),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"suite": name, "ok": False, "skipped": False, "checks": 0, "seconds": timeout,
                "detail": "timed out"}
    report = parse_report(proc.stdout)
    ok = proc.returncode == 0 and isinstance(report, dict) and report.get("ok") is True
    # A suite that reports itself degraded (an optional dependency is missing) is
    # skipped, not failed: the reason is printed and the verdict says so.
    skipped = isinstance(report, dict) and report.get("self_test") == "degraded"
    detail = ""
    if skipped:
        detail = str(report.get("reason") or "degraded")[:300]
    elif not ok:
        failures = (report or {}).get("failures") or (report or {}).get("problems") or []
        detail = json.dumps(failures, ensure_ascii=False)[:300] if failures else \
            (proc.stderr or proc.stdout or "").strip()[-300:]
    return {"suite": name, "ok": ok, "skipped": skipped, "checks": count_checks(report or {}),
            "seconds": round(time.time() - started, 1), "detail": detail}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true", help="skip the integration suites")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    rows = []
    for name, script, suite_argv, integration in SUITES:
        if args.quick and integration:
            continue
        row = run_suite(name, script, suite_argv, args.timeout)
        rows.append(row)
        if not args.json:
            mark = "PASS" if row["ok"] else ("SKIP" if row["skipped"] else "FAIL")
            print(f"{mark}  {name:<28} {row['checks']:>4} checks  {row['seconds']:>6}s"
                  + (f"   {row['detail']}" if row["detail"] else ""), flush=True)
    tests = [r for r in rows if r["suite"] not in PACKAGE_CHECKS]
    total = sum(r["checks"] for r in tests)
    skipped = [r["suite"] for r in rows if r["skipped"]]
    ok = all(r["ok"] or r["skipped"] for r in rows)
    summary = {"ok": ok, "suites": len(tests),
               "failed": sum(1 for r in rows if not (r["ok"] or r["skipped"])),
               "skipped": skipped, "checks": total,
               "package_checks": sum(r["checks"] for r in rows if r["suite"] in PACKAGE_CHECKS),
               "python": sys.version.split()[0], "platform": sys.platform, "rows": rows}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        verdict = "FAILURES" if not ok else ("ALL GREEN" if not skipped else
                                             f"GREEN, {len(skipped)} skipped for a missing optional dependency")
        print(f"\n{verdict}: {len(tests)} self-test suites, {total} checks, plus "
              f"{summary['package_checks']} package checks "
              f"(python {summary['python']}, {sys.platform})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
