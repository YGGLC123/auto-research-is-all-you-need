#!/usr/bin/env python3
"""Package link linter for auto-research v2.

Walks every Markdown file in the plugin and verifies that

1. every relative Markdown link `[text](path)` resolves to a real file;
2. every `../../scripts/*.py` / `../../docs/*.md` style path mentioned in
   prose or code spans resolves from that file's directory;
3. every fully qualified skill reference (`/auto-research:<skill>` or
   `$auto-research:<skill>`) names an existing skill directory;
4. every SKILL.md has frontmatter whose `name:` matches its directory.

Exit 0 when clean, 1 when any reference is broken. Run by
`research_os.py doctor`; also usable standalone in CI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
REL_PATH = re.compile(r"(?<![\w(./])((?:\.\./)+(?:docs|scripts|references|assets|skills)/[A-Za-z0-9_\-./]+?\.[a-z0-9]+)")
SKILL_REF = re.compile(r"[/$]auto-research:([a-z0-9-]+)")
EXTERNAL = ("http://", "https://", "mailto:", "#")


def frontmatter_field(text: str, field: str) -> str | None:
    if not text.startswith("---"):
        return None
    try:
        block = text.split("---", 2)[1]
    except IndexError:
        return None
    match = re.search(rf"^{field}:\s*(.+)$", block, re.M)
    return match.group(1).strip() if match else None


def lint(plugin_root: Path) -> dict:
    problems: list[dict] = []
    skills_dir = plugin_root / "skills"
    skill_names = {p.name for p in skills_dir.iterdir() if p.is_dir()} if skills_dir.exists() else set()

    md_files = sorted(plugin_root.rglob("*.md"))
    # node_modules: third-party READMEs from the web build, not part of the package
    skip = {"__pycache__", ".git", "node_modules"}
    md_files = [p for p in md_files if not skip.intersection(p.parts)]

    for md in md_files:
        rel_md = md.relative_to(plugin_root).as_posix()
        text = md.read_text(encoding="utf-8")

        targets = set()
        for match in MD_LINK.finditer(text):
            target = match.group(1)
            if target.startswith(EXTERNAL):
                continue
            targets.add(target.split("#", 1)[0])
        for match in REL_PATH.finditer(text):
            targets.add(match.group(1))
        for target in sorted(t for t in targets if t):
            resolved = (md.parent / target).resolve()
            if not resolved.exists():
                problems.append({"file": rel_md, "kind": "broken-path", "ref": target})

        for match in SKILL_REF.finditer(text):
            name = match.group(1)
            if name not in skill_names:
                problems.append({"file": rel_md, "kind": "unknown-skill-ref", "ref": name})

        if md.name == "SKILL.md":
            declared = frontmatter_field(text, "name")
            description = frontmatter_field(text, "description")
            if declared is None or description is None:
                problems.append({"file": rel_md, "kind": "frontmatter", "ref": "missing name/description"})
            elif declared != md.parent.name:
                problems.append({"file": rel_md, "kind": "frontmatter",
                                 "ref": f"name '{declared}' != directory '{md.parent.name}'"})

    return {"ok": not problems, "files_checked": len(md_files), "problems": problems}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", default=None)
    args = parser.parse_args()
    plugin_root = Path(args.plugin_root).resolve() if args.plugin_root else Path(__file__).resolve().parent.parent
    report = lint(plugin_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
