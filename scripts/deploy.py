#!/usr/bin/env python3
"""One-click deploy for the auto-research plugin.

Subcommands:
  install     copy the package into ~/.claude/skills/auto-research (frozen copy),
              replacing any previous install or dev junction; runs doctor on the copy
  uninstall   remove the installed copy (refuses to touch anything it did not install)
  status      compare source vs installed versions
  release     build a self-contained zip under releases/ (loadable via
              `claude --plugin-dir auto-research-<version>.zip`)

Development happens in this repo; nothing goes live until `install` is run.
stdlib-only. Exit codes: 0 ok, 1 refusal, 2 environment error.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

EXCLUDE_DIRS = {".git", "__pycache__", "releases", ".claude"}
MARKER = "INSTALLED.json"
PLUGIN_NAME = "auto-research"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def die(msg: str, code: int = 2):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(code)


def source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def target_root() -> Path:
    return Path.home() / ".claude" / "skills" / PLUGIN_NAME


def read_version(root: Path) -> str:
    manifest = root / ".claude-plugin" / "plugin.json"
    if not manifest.exists():
        die(f"no plugin manifest under {root}")
    return json.loads(manifest.read_text(encoding="utf-8")).get("version", "?")


def is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def iter_package_files(src: Path):
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.is_file():
            yield path, rel


def remove_target(target: Path) -> str:
    if not target.exists() and not is_link(target):
        return "absent"
    if is_link(target):
        os.rmdir(target)  # removes the junction/symlink itself, never the target tree
        return "removed-link"
    if (target / MARKER).exists():
        shutil.rmtree(target)
        return "removed-copy"
    die(f"{target} exists but was not installed by this tool (no {MARKER}); refusing to delete it", 1)
    raise AssertionError


def cmd_install(args) -> int:
    src = source_root()
    version = read_version(src)
    target = target_root()
    removed = remove_target(target)
    copied = 0
    for path, rel in iter_package_files(src):
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        copied += 1
    (target / MARKER).write_text(json.dumps({
        "plugin": PLUGIN_NAME, "version": version, "installed_at": utc_now(),
        "source": str(src), "installed_by": "deploy.py",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    doctor = {"skipped": True}
    if not args.no_doctor:
        proc = subprocess.run(
            [sys.executable, str(target / "scripts" / "research_os.py"), "doctor",
             "--plugin-root", str(target), "--no-probe"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            report = json.loads(proc.stdout)
            doctor = {"ok": report.get("ok"),
                      "failed": [c["name"] for c in report.get("checks", []) if not c.get("ok")]}
        except json.JSONDecodeError:
            doctor = {"ok": False, "raw": (proc.stdout or proc.stderr)[-400:]}
        if not doctor.get("ok"):
            print(json.dumps({"ok": False, "installed": str(target), "version": version,
                              "previous": removed, "files": copied, "doctor": doctor,
                              "note": "installed copy failed doctor; fix and reinstall"},
                             ensure_ascii=False, indent=2))
            return 1
    print(json.dumps({"ok": True, "installed": str(target), "version": version,
                      "previous": removed, "files": copied, "doctor": doctor,
                      "entry": "/auto-research:pilot (new Claude Code session)"},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_uninstall(_args) -> int:
    target = target_root()
    removed = remove_target(target)
    print(json.dumps({"ok": True, "target": str(target), "result": removed}, ensure_ascii=False))
    return 0


def cmd_status(_args) -> int:
    src = source_root()
    target = target_root()
    out = {"source": str(src), "source_version": read_version(src),
           "target": str(target), "installed": False}
    if is_link(target):
        out.update({"installed": True, "mode": "link (dev junction — changes go live instantly)"})
    elif (target / MARKER).exists():
        marker = json.loads((target / MARKER).read_text(encoding="utf-8"))
        out.update({"installed": True, "mode": "copy",
                    "installed_version": marker.get("version"),
                    "installed_at": marker.get("installed_at"),
                    "up_to_date": marker.get("version") == out["source_version"]})
    elif target.exists():
        out.update({"installed": True, "mode": "unmanaged directory (not created by deploy.py)"})
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_release(_args) -> int:
    src = source_root()
    version = read_version(src)
    releases = src / "releases"
    releases.mkdir(exist_ok=True)
    zip_path = releases / f"{PLUGIN_NAME}-{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path, rel in iter_package_files(src):
            bundle.write(path, rel.as_posix())
            count += 1
    print(json.dumps({"ok": True, "zip": str(zip_path), "files": count,
                      "usage": f'claude --plugin-dir "{zip_path}"  (portable: copy the zip anywhere)'},
                     ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="deploy", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("install", help="copy package into ~/.claude/skills and verify")
    p.add_argument("--no-doctor", action="store_true")
    p.set_defaults(func=cmd_install)
    sub.add_parser("uninstall", help="remove the installed copy").set_defaults(func=cmd_uninstall)
    sub.add_parser("status", help="source vs installed").set_defaults(func=cmd_status)
    sub.add_parser("release", help="build portable zip under releases/").set_defaults(func=cmd_release)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
