#!/usr/bin/env python3
"""auto-research literature ledger -- the paper's references as one record,
linked to the narrative tree.

The tree page's 树形 (brace) structure shows, beside every paragraph, the
studies that paragraph rests on; each study opens to a short report -- what
it contributes, where its boundary is relative to this paper, how this paper
uses it -- plus the bibliographic record, verification status and links.
This module builds that layer from what a project already has:

  * `refs.bib`                       -> the bibliographic record (authority);
  * the manuscript `.tex` files      -> which chapter cites which key;
  * a per-chapter priority table     -> the role a study plays in a chapter
    (`## 第 N 章 …` / `1. **key** — … 。role … 重点看: …`);
  * a literature map                 -> DOI / URL and 已核实 / 未核实;
  * the narrative tree               -> author-year mentions in a node's text
    and `lit:<key>` / `bib:<key>` entries in `basis_refs`.

Outputs, both under `.research-os/literature/`:
  ledger.json  {refs: {key: {…, report: {contribution, boundary, use_in_paper}}}}
  links.json   {links: {node_id: [{key, how: chapter|mention|basis, chapter?}]}}

`report` asks the Claude CLI (print mode, no tools) to write the three-line
report for each reference **from the local materials only**; a report is
stored with the model and time, and "材料未记载" is an acceptable line.
The page is a reader of these two files; the evidence steward owns their
content (adjudicated relations still go through the research graph).

    py scripts/literature_index.py --project <root> build [--bib …] [--table …] [--map …] [--tex …]
    py scripts/literature_index.py --project <root> link [--ref main]
    py scripts/literature_index.py --project <root> report [--model …] [--batch 12] [--only-missing]
    py scripts/literature_index.py --project <root> status
    py scripts/literature_index.py --self-test

stdlib-only, Python >= 3.9.  Exit codes: 0 ok, 1 refusal, 2 environment/usage.
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros  # noqa: E402
import narrative as nar  # noqa: E402

LEDGER_SCHEMA = "auto-research/literature-ledger-v1"
LINKS_SCHEMA = "auto-research/literature-links-v1"
DEFAULT_MODEL = "opus"
DISALLOWED_TOOLS = "Bash,PowerShell,Edit,Write,MultiEdit,NotebookEdit,Agent,Task,WebFetch,WebSearch"
MENTION_RE = re.compile(r"([A-Z][A-Za-z\-']+(?:,? (?:and |& )?[A-Z][A-Za-z\-']+){0,3}(?: et al\.)?) \((\d{4}[a-z]?)\)")
CITE_RE = re.compile(r"\\cite[a-zA-Z*]*(?:\[[^\]]*\]){0,2}\{([^}]*)\}")


def lit_dir(control: Path) -> Path:
    return Path(control) / "literature"


# ============================================================ sources

def discover_sources(control: Path, overrides: dict | None = None) -> dict:
    """Find bib / tex / table / map under the project root; overrides win."""
    root = Path(control).parent
    found = {"bib": None, "tex": None, "table": None, "map": None}
    over = overrides or {}
    stored = ros.read_json_file(lit_dir(control) / "sources.json", {}) or {}
    for key in found:
        if over.get(key):
            found[key] = str(over[key])
        elif stored.get(key) and Path(stored[key]).exists():
            found[key] = stored[key]
    skip = {".git", "node_modules", ".research-os", "__pycache__"}
    if not found["bib"]:
        bibs = [p for p in root.rglob("*.bib") if not (set(p.parts) & skip)]
        bibs.sort(key=lambda p: (0 if p.name == "refs.bib" else 1, len(p.parts)))
        if bibs:
            found["bib"] = str(bibs[0])
    if found["bib"] and not found["tex"]:
        found["tex"] = str(Path(found["bib"]).parent)
    if not found["table"]:
        tabs = sorted(p for p in root.glob("*文献优先表*.md"))
        if tabs:
            found["table"] = str(tabs[-1])
    if not found["map"]:
        maps = sorted(p for p in root.glob("*文献地图*") if p.is_dir())
        if maps:
            found["map"] = str(maps[-1])
    return found


# ============================================================ bib

def _split_fields(body: str) -> dict:
    fields, i, n = {}, 0, len(body)
    while i < n:
        m = re.match(r"\s*,?\s*([A-Za-z_\-]+)\s*=\s*", body[i:])
        if not m:
            break
        name = m.group(1).lower()
        i += m.end()
        if i < n and body[i] == "{":
            depth, j = 0, i
            while j < n:
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            value, i = body[i + 1:j], j + 1
        elif i < n and body[i] == '"':
            j = body.find('"', i + 1)
            value, i = body[i + 1:j], j + 1
        else:
            m2 = re.match(r"([^,\n]+)", body[i:])
            value, i = m2.group(1).strip(), i + m2.end()
        fields[name] = _detex(value)
    return fields


_TEX_MAP = {r"\ldots": "…", r"\dots": "…", r"\&": "&", r"\%": "%", r"\$": "$", r"\_": "_", r"\#": "#",
            "``": "“", "''": "”", "---": "—", "--": "–", r"\textendash": "–", r"\textemdash": "—"}


def _detex(value: str) -> str:
    """Bib fields carry LaTeX; the page shows text."""
    s = str(value or "")
    for k, v in _TEX_MAP.items():
        s = s.replace(k, v)
    s = re.sub(r"\\[a-zA-Z]+\s*\{([^{}]*)\}", r"\1", s)     # \emph{x}, \textit{x}, \'{e} -> x
    s = re.sub(r"\\[a-zA-Z]+", "", s)                        # any other macro
    s = s.replace("{", "").replace("}", "").replace("~", " ")
    return re.sub(r"\s+", " ", s).strip()


def parse_bib(path: Path) -> dict:
    """{key: {type, fields…, verified_note}} -- notes are the comment lines
    directly above an entry (the project's own verification convention)."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    out, pending_notes = {}, []
    for line in text.split("\n"):
        pass
    pos = 0
    entries = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        entries.append((m.start(), m.end(), m.group(1).lower(), m.group(2)))
    for idx, (start, end, etype, key) in enumerate(entries):
        if etype in ("comment", "string", "preamble"):
            continue
        depth, j = 1, end
        while j < len(text) and depth:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        fields = _split_fields(text[end:j - 1])
        prev_end = entries[idx - 1][0] if idx else 0
        gap = text[prev_end:start]
        notes = [l.strip("% ").strip() for l in gap.split("\n") if l.strip().startswith("%")]
        notes = [n for n in notes if n and not n.startswith("-----")]
        verified = any(n.lower().startswith("verified") for n in notes)
        unresolved = any("TODO" in n or "UNRESOLVED" in n for n in notes)
        out[key] = dict(fields, type=etype, key=key,
                        verified_note=" ".join(notes)[:300],
                        verified_by_bib=verified and not unresolved)
        pos = j
    return out


def _surnames(author: str) -> list:
    names = [a.strip() for a in re.split(r"\s+and\s+", author or "") if a.strip()]
    out = []
    for nm in names:
        if "," in nm:
            out.append(nm.split(",")[0].strip())
        else:
            out.append(nm.split()[-1] if nm.split() else nm)
    return out


def cite_of(entry: dict) -> str:
    sur = _surnames(entry.get("author") or entry.get("editor") or "")
    year = entry.get("year") or ""
    if not sur:
        return (entry.get("key") or "?") + (" (" + year + ")" if year else "")
    if len(sur) == 1:
        who = sur[0]
    elif len(sur) == 2:
        who = sur[0] + " and " + sur[1]
    elif len(sur) == 3:
        who = sur[0] + ", " + sur[1] + " and " + sur[2]
    else:
        who = sur[0] + " et al."
    return who + " (" + year + ")"


# ============================================================ tex / table / map

def scan_tex_cites(tex_dir: Path) -> dict:
    """{key: sorted chapter indexes}, chapter = 1 + N of secN_*.tex; appendices
    and other files count as chapter 0."""
    out: dict = {}
    for path in sorted(Path(tex_dir).glob("*.tex")):
        m = re.match(r"sec(\d+)", path.name)
        chapter = int(m.group(1)) + 1 if m else 0
        text = "\n".join(l.split("%", 1)[0] for l in path.read_text(encoding="utf-8", errors="replace").split("\n"))
        for cm in CITE_RE.finditer(text):
            for key in cm.group(1).split(","):
                key = key.strip()
                if key:
                    out.setdefault(key, set()).add(chapter)
    return {k: sorted(v) for k, v in out.items()}


def parse_priority_table(path: Path) -> dict:
    """{chapter: [{key, role, focus}]}; the appendix group is chapter 0."""
    chapters: dict = {}
    cur = None
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^##\s+第\s*(\d+)\s*章", line)
        if m:
            cur = int(m.group(1))
            chapters.setdefault(cur, [])
            continue
        if re.match(r"^##\s+附录", line):
            cur = 0
            chapters.setdefault(0, [])
            continue
        m = re.match(r"^\d+\.\s+\*\*([\w:\-]+)\*\*\s+[—\-–]+\s+(.*)$", line)
        if m and cur is not None:
            key, rest = m.group(1), m.group(2)
            head, _, tail = rest.partition("。")
            role, _, focus = tail.partition("重点看")
            chapters[cur].append({"key": key, "role": role.strip("。:： ").strip(),
                                  "focus": focus.strip(":： ").strip(), "head": head.strip()})
    return chapters


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(t or "").lower())


def parse_literature_map(map_dir: Path) -> list:
    """Every entry line of every map file: authors/year/title/venue, URL,
    verified flag, note -- unmatched to bib yet."""
    rows = []
    for path in sorted(Path(map_dir).glob("*.md")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^\s*(?:-|\d+\.)\s+(.*\((\d{4}[a-z]?)\)\.?\s*[\"“]([^\"”]+)[\"”].*)$", line)
            if not m:
                continue
            whole, year, title = m.group(1), m.group(2), m.group(3)
            url = re.search(r"https?://\S+", whole)
            doi = re.search(r"10\.\d{4,9}/[^\s)\]]+", whole)
            verified = "已核实" in whole and "未核实" not in whole
            note = ""
            nm = re.search(r"\(([^()]*(?:核实)[^()]*)\)\s*$", whole)
            if nm:
                note = nm.group(1)
            rows.append({"file": path.name, "year": year, "title": title.strip(),
                         "authors": whole.split("(" + year)[0].strip(" ,"),
                         "url": url.group(0).rstrip(".,;)") if url else None,
                         "doi": doi.group(0).rstrip(".,;)") if doi else None,
                         "verified": verified, "note": note})
    return rows


def match_map_rows(refs: dict, rows: list) -> int:
    """Attach map rows to refs by DOI, then title, then surname+year."""
    by_doi = {str(r.get("doi") or "").lower(): k for k, r in refs.items() if r.get("doi")}
    by_title = {_norm_title(r.get("title")): k for k, r in refs.items() if r.get("title")}
    hits = 0
    for row in rows:
        key = None
        if row.get("doi") and row["doi"].lower() in by_doi:
            key = by_doi[row["doi"].lower()]
        if not key and _norm_title(row["title"]) in by_title:
            key = by_title[_norm_title(row["title"])]
        if not key:
            sur = re.split(r"[ ,]", row["authors"])[0].lower()
            for k, r in refs.items():
                if r.get("year") == row["year"] and sur and sur in " ".join(_surnames(r.get("authors_raw") or "")).lower():
                    key = k
                    break
        if not key:
            continue
        ref = refs[key]
        ref.setdefault("map", []).append({"file": row["file"], "note": row["note"],
                                          "url": row["url"], "verified": row["verified"]})
        if row["url"] and not ref.get("url"):
            ref["url"] = row["url"]
        if row["doi"] and not ref.get("doi"):
            ref["doi"] = row["doi"]
        if row["verified"]:
            ref["verified"] = {"status": "verified", "by": "literature-map", "note": row["note"]}
        elif ref.get("verified", {}).get("status") != "verified":
            ref["verified"] = {"status": "unverified", "by": "literature-map", "note": row["note"] or "链接未核实"}
        hits += 1
    return hits


# ============================================================ build / link

def build(control: Path, overrides: dict | None = None) -> dict:
    control = Path(control)
    src = discover_sources(control, overrides)
    if not src.get("bib"):
        ros.die("no .bib found under the project (pass --bib)", 2)
    bib = parse_bib(Path(src["bib"]))
    refs: dict = {}
    for key, e in bib.items():
        doi = e.get("doi")
        url = e.get("url") or (("https://doi.org/" + doi) if doi else None)
        refs[key] = {"key": key, "cite": cite_of(e), "authors_raw": e.get("author") or e.get("editor") or "",
                     "year": e.get("year") or "", "title": e.get("title") or "",
                     "venue": e.get("journal") or e.get("booktitle") or e.get("publisher") or e.get("howpublished") or "",
                     "volume": e.get("volume"), "number": e.get("number"), "pages": e.get("pages"),
                     "doi": doi, "url": url, "type": e.get("type"),
                     "verified": {"status": "verified" if e.get("verified_by_bib") else "unknown",
                                  "by": "bib" if e.get("verified_by_bib") else None,
                                  "note": e.get("verified_note") or ""},
                     "cited_in_chapters": [], "roles": {}, "map": [], "report": None}
    cites = scan_tex_cites(Path(src["tex"])) if src.get("tex") and Path(src["tex"]).exists() else {}
    for key, chapters in cites.items():
        if key in refs:
            refs[key]["cited_in_chapters"] = chapters
    table_hits = 0
    if src.get("table") and Path(src["table"]).exists():
        for chapter, rows in parse_priority_table(Path(src["table"])).items():
            for r in rows:
                if r["key"] in refs:
                    refs[r["key"]]["roles"][str(chapter)] = {"role": r["role"], "focus": r["focus"]}
                    table_hits += 1
    map_hits = 0
    if src.get("map") and Path(src["map"]).exists():
        map_hits = match_map_rows(refs, parse_literature_map(Path(src["map"])))
    # keep reports already written
    old = ros.read_json_file(lit_dir(control) / "ledger.json", {}) or {}
    for key, r in (old.get("refs") or {}).items():
        if key in refs and r.get("report"):
            refs[key]["report"] = r["report"]
    ledger = {"schema": LEDGER_SCHEMA, "project": control.parent.name, "built_at": nar.now_iso(),
              "sources": src, "refs": refs}
    lit_dir(control).mkdir(parents=True, exist_ok=True)
    ros.atomic_write(lit_dir(control) / "ledger.json", nar.dump_json(ledger))
    ros.atomic_write(lit_dir(control) / "sources.json", nar.dump_json(src))
    return {"ok": True, "refs": len(refs), "cited": sum(1 for r in refs.values() if r["cited_in_chapters"]),
            "with_role": table_hits, "map_matched": map_hits,
            "verified": sum(1 for r in refs.values() if r["verified"]["status"] == "verified"),
            "reports": sum(1 for r in refs.values() if r["report"]), "sources": src}


def _title(node_id: str, node: dict) -> str:
    t = str(node.get("title") or "")
    if node_id and t.startswith(node_id):
        rest = t[len(node_id):].lstrip(" :：·-—")
        if rest:
            t = rest
    return t


def key_for_mention(refs: dict, author: str, year: str) -> str | None:
    sur = re.split(r"[ ,&]", author.strip())[0].lower()
    cands = [k for k, r in refs.items() if str(r.get("year")) == year.rstrip("abc")
             and sur in " ".join(_surnames(r.get("authors_raw") or "")).lower()]
    return cands[0] if len(cands) == 1 else (cands[0] if cands else None)


def link(control: Path, ref: str = "main") -> dict:
    control = Path(control)
    ledger = ros.read_json_file(lit_dir(control) / "ledger.json", None)
    if not ledger:
        ros.die("no literature ledger yet: run `literature build` first", 1)
    refs = ledger["refs"]
    snapshot = nar.load_head_snapshot(control, ref)
    nodes = snapshot.get("nodes") or {}
    root = snapshot.get("root")
    chapters = [k for k in (nodes.get(root) or {}).get("children") or [] if k in nodes]
    links: dict = {}

    def add(nid, entry):
        rows = links.setdefault(nid, [])
        if not any(r["key"] == entry["key"] and r["how"] == entry["how"] for r in rows):
            rows.append(entry)
    # chapter level: a study cited in chapter N (tex) or listed for it (table)
    for i, cid in enumerate(chapters):
        chapter = i + 1
        for key, r in refs.items():
            if str(chapter) in (r.get("roles") or {}):
                add(cid, {"key": key, "how": "chapter", "chapter": chapter, "role": r["roles"][str(chapter)]["role"]})
            elif chapter in (r.get("cited_in_chapters") or []):
                add(cid, {"key": key, "how": "chapter", "chapter": chapter, "role": ""})
    # node level: mentions in text, explicit basis refs
    unmatched = []
    for nid, node in nodes.items():
        text = " ".join(str(node.get(k) or "") for k in ("title", "summary", "statement"))
        for author, year in MENTION_RE.findall(text):
            key = key_for_mention(refs, author, year)
            if key:
                add(nid, {"key": key, "how": "mention", "text": author + " (" + year + ")"})
            else:
                unmatched.append({"node": nid, "text": author + " (" + year + ")"})
        for b in node.get("basis_refs") or []:
            m = re.match(r"^(?:lit|bib|cite):(\S+)$", str(b))
            if m and m.group(1) in refs:
                add(nid, {"key": m.group(1), "how": "basis"})
    out = {"schema": LINKS_SCHEMA, "project": control.parent.name, "ref": ref,
           "commit": nar.read_ref(control, ref), "built_at": nar.now_iso(),
           "links": links, "unmatched_mentions": unmatched[:200]}
    ros.atomic_write(lit_dir(control) / "links.json", nar.dump_json(out))
    return {"ok": True, "nodes_linked": len(links), "links": sum(len(v) for v in links.values()),
            "unmatched_mentions": len(unmatched)}


# ============================================================ report

REPORT_BRIEF = """你是这篇论文的证据管家。下面每条文献附有本项目手头的全部材料（书目、在哪几章被引、在该章承担的角色、读时看什么、文献地图的备注）。请**只依据这些材料**为每条写一份三行报告，读者是论文作者本人：
- contribution：这篇文献做了什么、贡献是什么（1–2 句）；
- boundary：它的边界在哪——相对本文，它没有做到 / 不覆盖 / 前提不同的地方（1–2 句）；
- use_in_paper：本文用它做什么、在哪一步（1 句）。
材料不足以支持某一行时，该行写"材料未记载"，不要凭印象补写。中文；不出现内部编号。
只输出一个 JSON 对象：{"reports":[{"key":"…","contribution":"…","boundary":"…","use_in_paper":"…"}]}
"""


def _ref_material(r: dict) -> str:
    lines = ["### " + r["key"], "书目: " + r["cite"] + ", \"" + (r.get("title") or "") + "\", " + (r.get("venue") or "") +
             (", " + str(r.get("volume")) if r.get("volume") else "") + (" (" + str(r.get("year")) + ")" if r.get("year") else "")]
    if r.get("cited_in_chapters"):
        lines.append("被引章节: " + ", ".join("第 %s 章" % c if c else "附录" for c in r["cited_in_chapters"]))
    for ch, role in (r.get("roles") or {}).items():
        lines.append("第 %s 章角色: %s" % (ch, role.get("role") or "")
                     + ("；读时看: " + role["focus"] if role.get("focus") else ""))
    for m in r.get("map") or []:
        if m.get("note"):
            lines.append("文献地图备注: " + m["note"])
    return "\n".join(lines)


def _find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    hits = glob.glob(os.path.expandvars("%USERPROFILE%/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe"))
    return sorted(hits, key=lambda p: [int(x) for x in re.findall(r"(\d+)", p)])[-1] if hits else None


class ClaudeWriter:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self.cli = _find_claude()
        self.workdir = Path(tempfile.mkdtemp(prefix="ar-lit-"))

    def __call__(self, prompt: str) -> list:
        if not self.cli:
            raise RuntimeError("claude CLI not found")
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        proc = subprocess.run([self.cli, "-p", "--model", self.model, "--output-format", "text",
                               "--max-turns", "1", "--disallowedTools", DISALLOWED_TOOLS],
                              input=prompt.encode("utf-8"), cwd=str(self.workdir), env=env,
                              capture_output=True, timeout=1800)
        text = proc.stdout.decode("utf-8", "replace")
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("no JSON in writer output: " + proc.stderr.decode("utf-8", "replace")[:200])
        body = json.loads(text[start:end + 1])
        return [r for r in body.get("reports") or [] if isinstance(r, dict) and r.get("key")]


class FakeWriter:
    def __init__(self):
        self.calls = 0

    def __call__(self, prompt: str) -> list:
        self.calls += 1
        keys = re.findall(r"^### (\S+)$", prompt, flags=re.M)
        return [{"key": k, "contribution": "贡献-" + k, "boundary": "边界-" + k, "use_in_paper": "用法-" + k} for k in keys]


def report(control: Path, writer=None, model: str = DEFAULT_MODEL, batch: int = 12,
           only_missing: bool = True, north_star: str = "") -> dict:
    control = Path(control)
    path = lit_dir(control) / "ledger.json"
    ledger = ros.read_json_file(path, None)
    if not ledger:
        ros.die("no literature ledger yet: run `literature build` first", 1)
    refs = ledger["refs"]
    todo = [k for k, r in refs.items() if not (only_missing and r.get("report"))]
    writer = writer or ClaudeWriter(model=model)
    label = model if not isinstance(writer, FakeWriter) else "fake"
    written, errors = 0, []
    if not north_star:
        try:
            snap = nar.load_head_snapshot(control, "main")
            rn = (snap.get("nodes") or {}).get(snap.get("root")) or {}
            north_star = str(rn.get("statement") or rn.get("summary") or rn.get("title") or "")[:600]
        except Exception:
            north_star = ""
    def run_chunk(keys: list) -> int:
        """One writer call; on a malformed reply, split the chunk and retry
        (a long batch is where a stray quote breaks the JSON)."""
        prompt = REPORT_BRIEF + ("\n【本文主线】" + north_star + "\n" if north_star else "") + \
            "\n共 %d 条，每条都要有报告：\n\n" % len(keys) + "\n\n".join(_ref_material(refs[k]) for k in keys)
        try:
            rows = writer(prompt)
        except Exception as exc:
            if len(keys) > 1:
                mid = len(keys) // 2
                return run_chunk(keys[:mid]) + run_chunk(keys[mid:])
            errors.append("%s: %s: %s" % (keys[0], type(exc).__name__, exc))
            return 0
        done = 0
        for row in rows:
            if row["key"] in refs and row["key"] in keys:
                refs[row["key"]]["report"] = {
                    "contribution": str(row.get("contribution") or "").strip()[:600],
                    "boundary": str(row.get("boundary") or "").strip()[:600],
                    "use_in_paper": str(row.get("use_in_paper") or "").strip()[:400],
                    "by": "evidence-steward-live", "model": label, "at": nar.now_iso()}
                done += 1
        ledger["built_at"] = nar.now_iso()
        ros.atomic_write(path, nar.dump_json(ledger))     # save after every chunk
        return done

    for i in range(0, len(todo), batch):
        written += run_chunk(todo[i:i + batch])
    return {"ok": not errors, "requested": len(todo), "written": written, "errors": errors,
            "reports_total": sum(1 for r in refs.values() if r.get("report"))}


def status(control: Path) -> dict:
    control = Path(control)
    ledger = ros.read_json_file(lit_dir(control) / "ledger.json", None)
    links = ros.read_json_file(lit_dir(control) / "links.json", None)
    if not ledger:
        return {"ok": False, "ledger": False}
    refs = ledger["refs"]
    return {"ok": True, "ledger": True, "refs": len(refs),
            "verified": sum(1 for r in refs.values() if r["verified"]["status"] == "verified"),
            "reports": sum(1 for r in refs.values() if r.get("report")),
            "with_role": sum(1 for r in refs.values() if r.get("roles")),
            "links": (sum(len(v) for v in (links or {}).get("links", {}).values()) if links else 0),
            "nodes_linked": len((links or {}).get("links", {})) if links else 0,
            "sources": ledger.get("sources"), "built_at": ledger.get("built_at")}


def load_for_page(control: Path) -> dict | None:
    """What the tree page embeds: refs (trimmed) + links, or None."""
    control = Path(control)
    ledger = ros.read_json_file(lit_dir(control) / "ledger.json", None)
    if not ledger:
        return None
    links = ros.read_json_file(lit_dir(control) / "links.json", {}) or {}
    refs = {}
    for key, r in ledger["refs"].items():
        refs[key] = {"key": key, "cite": r["cite"], "year": r.get("year"), "title": r.get("title"),
                     "venue": r.get("venue"), "volume": r.get("volume"), "number": r.get("number"),
                     "pages": r.get("pages"), "doi": r.get("doi"), "url": r.get("url"),
                     "verified": r.get("verified"), "roles": r.get("roles") or {},
                     "cited_in_chapters": r.get("cited_in_chapters") or [],
                     "map_notes": [m.get("note") for m in r.get("map") or [] if m.get("note")][:3],
                     "report": r.get("report")}
    return {"schema": LEDGER_SCHEMA, "built_at": ledger.get("built_at"), "refs": refs,
            "links": links.get("links") or {}}


# ============================================================ self-test

FIXTURE_BIB = """% verified 20260816 example.org
@article{hlz2016,
  author = {Campbell R. Harvey and Yan Liu and Heqing Zhu},
  title = {... and the Cross-Section of Expected Returns},
  journal = {Review of Financial Studies}, year = {2016}, volume = {29}, number = {1}, pages = {5--68},
  doi = {10.1093/rfs/hhv059}
}
% TODO-UNRESOLVED: pages
@article{chen2025,
  author = {Chen, Andrew Y.},
  title = {Do t-Statistic Hurdles Need to Be Raised?},
  journal = {Management Science}, year = {2025}
}
@book{henry2009,
  author = {Emeric Henry}, title = {Strategic Disclosure}, publisher = {EJ}, year = {2009}
}
"""
FIXTURE_TABLE = """# 分章文献优先表
## 第 1 章 · 引言
1. **hlz2016** — Harvey, Liu & Zhu (2016), "...", *RFS*。动机证词的原点。重点看:如何外推检验总数。
2. **chen2025** — Chen (2025), "Do t-Statistic Hurdles Need to Be Raised?", *MS*。把动机推到定量。重点看:识别分析。
## 第 2 章 · 判据
1. **henry2009** — Henry (2009), "Strategic Disclosure", *EJ*。制度提议的先行者。
"""
FIXTURE_MAP = """# 域1
- Harvey, C. R., Y. Liu, and H. Zhu (2016). "... and the Cross-Section of Expected Returns." *RFS* 29(1). https://doi.org/10.1093/rfs/hhv059 (已核实;基准出处)
- Chen, A. Y. (2025). "Do t-Statistic Hurdles Need to Be Raised?" *Management Science*. (链接未核实)
"""
FIXTURE_TEX0 = "Intro \\citep{hlz2016, chen2025} and \\citet{henry2009}. % \\cite{ghost}\n"
FIXTURE_TEX1 = "Criterion \\citep{henry2009}.\n"


def self_test() -> int:
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    tmp = Path(tempfile.mkdtemp(prefix="ar-lit-"))
    try:
        proj = tmp / "proj"
        control = proj / ".research-os"
        control.mkdir(parents=True)
        (proj / "paper").mkdir()
        (proj / "paper" / "refs.bib").write_text(FIXTURE_BIB, encoding="utf-8")
        (proj / "paper" / "sec0_intro.tex").write_text(FIXTURE_TEX0, encoding="utf-8")
        (proj / "paper" / "sec1_criterion.tex").write_text(FIXTURE_TEX1, encoding="utf-8")
        (proj / "_传递_分章文献优先表_x.md").write_text(FIXTURE_TABLE, encoding="utf-8")
        (proj / "_文献地图_x").mkdir()
        (proj / "_文献地图_x" / "域1.md").write_text(FIXTURE_MAP, encoding="utf-8")

        bib = parse_bib(proj / "paper" / "refs.bib")
        check("bib parsed with braces, quotes and notes", set(bib) == {"hlz2016", "chen2025", "henry2009"}
              and bib["hlz2016"]["doi"] == "10.1093/rfs/hhv059" and bib["hlz2016"]["verified_by_bib"]
              and not bib["chen2025"]["verified_by_bib"])
        check("LaTeX in bib fields becomes text", _detex(r"\ldots and the {Cross-Section} of \emph{Expected} Returns --- now")
              == "… and the Cross-Section of Expected Returns — now")
        check("cite display: 3 authors / 1 author / comma-form surname",
              cite_of(bib["hlz2016"]) == "Harvey, Liu and Zhu (2016)" and cite_of(bib["chen2025"]) == "Chen (2025)"
              and cite_of(bib["henry2009"]) == "Henry (2009)")
        cites = scan_tex_cites(proj / "paper")
        check("tex cites -> chapters, comments ignored", cites == {"hlz2016": [1], "chen2025": [1], "henry2009": [1, 2]}, str(cites))

        buffer, saved = io.StringIO(), sys.stdout
        sys.stdout = buffer
        try:
            nar.cmd_init(argparse.Namespace(root=proj, root_id="N-0", title="临时", thesis="给附录阅读建判据",
                                            summary="", venue="RFS", message=None, session="W-l",
                                            at="2026-08-27T00:00:00Z", turn_uuid=None, manifest_id=None))
        finally:
            sys.stdout = saved

        def claim(nid, title, statement, basis=None):
            return {"id": nid, "node_type": "claim",
                    "identity_key": {"subject": nid, "target": "returns", "predicate": "p-" + nid, "quantifier": "all",
                                     "domain": "US equities", "conditions": [], "polarity": "positive"},
                    "title": title, "summary": "", "statement": statement, "basis_refs": basis or [],
                    "state": {"epistemic": "PENDING", "narrative": "ACTIVE"}}
        nar.commit_from_ops(control, "main", [
            {"op": "add_node", "node": claim("C-001", "引言", "为什么必须看附录。"), "parent": "N-0"},
            {"op": "add_node", "node": claim("C-002", "判据", "判据一章。"), "parent": "N-0"},
            {"op": "add_node", "node": claim("C-003", "已有证据只是弱识别", "Chen (2025) 推至定量识别分析。Harvey, Liu and Zhu (2016) 给出基准。"), "parent": "C-001"},
            {"op": "add_node", "node": claim("C-004", "先行者", "制度提议早有出处。", ["lit:henry2009"]), "parent": "C-002"},
        ], {"message": "seed", "at": "2026-08-27T01:00:00Z", "author": {"session": "W-l", "role": "main"},
            "origin": "live", "approval_refs": {"overthrow": None, "forced_cause": None}})

        r = build(control)
        check("build: sources discovered (bib, tex, table, map)", all(r["sources"].get(k) for k in ("bib", "tex", "table", "map")), str(r["sources"]))
        check("build: 3 refs, roles from table, map matched, verified from bib+map",
              r["refs"] == 3 and r["with_role"] == 3 and r["map_matched"] == 2 and r["verified"] == 1, str(r))
        ledger = json.loads((lit_dir(control) / "ledger.json").read_text(encoding="utf-8"))
        chen = ledger["refs"]["chen2025"]
        check("map: unverified link recorded, verified stays verified for hlz",
              chen["verified"]["status"] == "unverified" and ledger["refs"]["hlz2016"]["verified"]["status"] == "verified"
              and ledger["refs"]["hlz2016"]["url"] == "https://doi.org/10.1093/rfs/hhv059")
        check("roles carry role + focus", ledger["refs"]["hlz2016"]["roles"]["1"]["focus"] == "如何外推检验总数。"
              or ledger["refs"]["hlz2016"]["roles"]["1"]["focus"].startswith("如何外推"))

        l = link(control, "main")
        links = json.loads((lit_dir(control) / "links.json").read_text(encoding="utf-8"))["links"]
        check("link: chapter-level from table/tex, mention-level from text, basis-level from basis_refs",
              {x["key"] for x in links["C-001"]} == {"hlz2016", "chen2025", "henry2009"}
              and {x["key"] for x in links["C-003"] if x["how"] == "mention"} == {"chen2025", "hlz2016"}
              and any(x["how"] == "basis" and x["key"] == "henry2009" for x in links["C-004"]), str(links))
        check("link: chapter role text travels with the link",
              any(x.get("role") == "动机证词的原点" for x in links["C-001"]))

        fw = FakeWriter()
        rep = report(control, writer=fw, batch=2)
        check("report: batched, all written, saved with provenance", fw.calls == 2 and rep["written"] == 3
              and json.loads((lit_dir(control) / "ledger.json").read_text(encoding="utf-8"))["refs"]["chen2025"]["report"]["by"] == "evidence-steward-live")
        rep2 = report(control, writer=fw, batch=2)
        check("report: only-missing is a no-op afterwards", rep2["requested"] == 0 and fw.calls == 2)
        r2 = build(control)
        check("rebuild keeps reports", r2["reports"] == 3)
        page = load_for_page(control)
        check("page payload: refs + links, report included, authors_raw not leaked",
              page and "hlz2016" in page["refs"] and page["refs"]["hlz2016"]["report"]["contribution"] == "贡献-hlz2016"
              and "authors_raw" not in page["refs"]["hlz2016"] and page["links"]["C-003"])
        st = status(control)
        check("status counts", st["refs"] == 3 and st["reports"] == 3 and st["nodes_linked"] == 4, str(st))
        prompt = REPORT_BRIEF + _ref_material(ledger["refs"]["hlz2016"])
        check("report brief: materials-only, three lines, JSON-only", "材料未记载" in prompt and "boundary" in prompt
              and "第 1 章角色" in prompt and "只输出一个 JSON" in prompt)
        check("claude CLI discoverable (informational)", True, str(bool(_find_claude())))
    except Exception as exc:  # pragma: no cover
        check("self-test ran to the end", False, "%s: %s" % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [c for c in checks if not c["ok"]]
    print(json.dumps({"ok": not failed, "checks": len(checks), "failed": len(failed),
                      "rows": checks if failed else [c["check"] for c in checks]}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


# ============================================================ CLI

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="literature_index.py", description="literature ledger + tree links")
    parser.add_argument("--project")
    parser.add_argument("--self-test", dest="self_test", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    b = sub.add_parser("build")
    b.add_argument("--bib"); b.add_argument("--tex"); b.add_argument("--table"); b.add_argument("--map")
    l = sub.add_parser("link")
    l.add_argument("--ref", default="main")
    rp = sub.add_parser("report")
    rp.add_argument("--model", default=DEFAULT_MODEL)
    rp.add_argument("--batch", type=int, default=12)
    rp.add_argument("--all", action="store_true", help="rewrite every report, not only missing ones")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.project or not args.cmd:
        ros.die("--project and a subcommand (build|link|report|status) are required", 2)
    control = ros.locate_control(Path(args.project))
    try:
        if args.cmd == "build":
            out = build(control, {"bib": args.bib, "tex": args.tex, "table": args.table, "map": args.map})
        elif args.cmd == "link":
            out = link(control, args.ref)
        elif args.cmd == "report":
            out = report(control, model=args.model, batch=args.batch, only_missing=not args.all)
        else:
            out = status(control)
    except nar.NarrativeError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "detail": exc.detail}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
