#!/usr/bin/env python3
"""component_index.py -- auto-research v2.9 Route B component library.

Indexes external scientific-icon libraries (Bioicons and friends) into a local,
license-aware component index, and assembles indexed components into a
publication-ready SVG through code-placed diagram templates.

Contract: docs/figure-engineering-contract.md sections 6 and 8.
Scope: this file only. It never touches figure_router / figure_templates (X1),
research_graph / role_runtime (Y), or any skill.

stdlib only. UTF-8 in, UTF-8 + LF out.

CLI
  component_index.py index build --source bioicons [--repo PATH]
  component_index.py index build --source dir --name NAME --repo PATH
  component_index.py index build --source reactome|scidraw
  component_index.py index search --tags membrane,cell [--license-allow CC0,MIT,CC-BY]
  component_index.py index attribution --used bioicons:ID,bioicons:ID2 [--out PATH]
  component_index.py index stats [--source bioicons]
  component_index.py assemble --template pipeline-lr --slots A=bioicons:ID ... --out F.svg
  component_index.py self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COMPONENTS_DIR = PLUGIN_ROOT / "library" / "components"
SCHEMA = "auto-research/component-index-v0"

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
SVG_Q = "{%s}" % SVG_NS
XLINK_Q = "{%s}" % XLINK_NS

BIOICONS_REPO_URL = "https://github.com/duerrsimon/bioicons"
BIOICONS_SITE = "https://bioicons.com"

MM2PX = 96.0 / 25.4  # 1 user unit == 1 px == 1/96 in; the width attribute carries mm

# Bioicons stores the license as a directory name and as a metadata token.
# This map is transcription, never inference: an unmapped token stays UNKNOWN.
LICENSE_MAP = {
    "cc-0": "CC0-1.0",
    "cc0": "CC0-1.0",
    "cc-by-3.0": "CC-BY-3.0",
    "cc-by-4.0": "CC-BY-4.0",
    "cc-by-sa-3.0": "CC-BY-SA-3.0",
    "cc-by-sa-4.0": "CC-BY-SA-4.0",
    "bsd": "BSD-3-Clause",
    "mit": "MIT",
    "apache-2.0": "Apache-2.0",
    "public-domain": "CC0-1.0",
}

# Attribution obligations, keyed by normalized license id.
LICENSE_NOTICE = {
    "CC0-1.0": "No attribution required (public-domain dedication); credit kept for provenance.",
    "CC-BY-3.0": "Attribution required: name the creator and the license, and link the source.",
    "CC-BY-4.0": "Attribution required: name the creator and the license, and link the source.",
    "CC-BY-SA-3.0": "Attribution required AND derivatives must be shared under the same license.",
    "CC-BY-SA-4.0": "Attribution required AND derivatives must be shared under the same license.",
    "BSD-3-Clause": "Copyright notice and license text must travel with the artwork.",
    "MIT": "Copyright notice and license text must travel with the artwork.",
    "Apache-2.0": "Copyright notice plus NOTICE must travel with the artwork.",
    "UNKNOWN": "License could not be read from the source. DO NOT ship this component "
               "until the license is resolved at the source URL.",
}

LICENSE_URL = {
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "CC-BY-3.0": "https://creativecommons.org/licenses/by/3.0/",
    "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC-BY-SA-3.0": "https://creativecommons.org/licenses/by-sa/3.0/",
    "CC-BY-SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "BSD-3-Clause": "https://opensource.org/license/bsd-3-clause",
    "MIT": "https://opensource.org/license/mit",
    "Apache-2.0": "https://www.apache.org/licenses/LICENSE-2.0",
}

# --license-allow accepts loose families; expand them to concrete ids.
LICENSE_FAMILY = {
    "CC0": ["CC0-1.0"],
    "CC-BY": ["CC-BY-3.0", "CC-BY-4.0"],
    "CC-BY-SA": ["CC-BY-SA-3.0", "CC-BY-SA-4.0"],
    "BSD": ["BSD-3-Clause"],
    "MIT": ["MIT"],
    "APACHE": ["Apache-2.0"],
}

MANUAL_SOURCES = {
    "reactome": {
        "name": "Reactome Icon Library",
        "site": "https://reactome.org/icon-lib",
        "license_stated_by_source": "CC-BY-4.0",
        "approx_count": 1600,
        "reason": "No public git mirror of the icon set: it is served as a browsable web "
                  "gallery with per-icon downloads behind the site UI. There is nothing to "
                  "clone offline, so nothing is indexed until a human imports it.",
        "steps": [
            "Open https://reactome.org/icon-lib and download the categories you need.",
            "Unpack the SVGs into library/components/reactome/svg/ keeping the category "
            "folders (that path is git-ignored).",
            "Write library/components/reactome/svg/manifest.json mapping each relative svg "
            "path to {\"license\": ..., \"author\": ..., \"source_url\": ...} copied from "
            "the download page. Anything you cannot read stays out of the manifest and is "
            "indexed as UNKNOWN rather than guessed.",
            "Run: component_index.py index build --source dir --name reactome "
            "--repo library/components/reactome/svg",
        ],
    },
    "scidraw": {
        "name": "SciDraw",
        "site": "https://scidraw.io",
        "license_stated_by_source": "CC-BY-4.0",
        "approx_count": 1500,
        "reason": "Drawings are uploaded and downloaded one item at a time, each with its "
                  "own creator and DOI; there is no repository to clone and no bulk "
                  "endpoint, so an offline index cannot be built without a human download.",
        "steps": [
            "Download the drawings you need from https://scidraw.io (each item page carries "
            "its own creator, DOI and license).",
            "Put the SVGs in library/components/scidraw/svg/ (git-ignored).",
            "Write library/components/scidraw/svg/manifest.json with, per file, "
            "{\"license\", \"author\", \"source_url\"} copied from the item page (the DOI "
            "link is the source_url).",
            "Run: component_index.py index build --source dir --name scidraw "
            "--repo library/components/scidraw/svg",
        ],
    },
}


# ---------------------------------------------------------------- io helpers

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def die(msg: str, code: int = 2) -> None:
    sys.stderr.write("component_index: %s\n" % msg)
    raise SystemExit(code)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def write_json(path: Path, obj: Any) -> None:
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def rel_to_plugin(path: Path) -> str:
    try:
        return path.resolve().relative_to(PLUGIN_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


# ---------------------------------------------------------------- svg reading

_LEN_RE = re.compile(r"^\s*([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)\s*([a-z%]*)\s*$")


def parse_length(raw: Any) -> tuple[float | None, str]:
    if not isinstance(raw, str):
        return None, ""
    match = _LEN_RE.match(raw)
    if not match:
        return None, ""
    try:
        return float(match.group(1)), (match.group(2) or "")
    except ValueError:
        return None, ""


def _view_box_numbers(view_box: Any) -> list[float] | None:
    if not isinstance(view_box, str):
        return None
    parts = view_box.replace(",", " ").split()
    if len(parts) != 4:
        return None
    try:
        return [float(p) for p in parts]
    except ValueError:
        return None


def read_svg_geometry(path: Path) -> dict[str, Any]:
    """Return {width, height, units, view_box, ok, error}. Never guesses."""
    out: dict[str, Any] = {"width": None, "height": None, "units": "", "view_box": None,
                           "ok": False, "error": None}
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError) as exc:
        out["error"] = str(exc)
        return out
    out["ok"] = True
    out["view_box"] = root.get("viewBox")
    width, wu = parse_length(root.get("width"))
    height, hu = parse_length(root.get("height"))
    if width is None or height is None:
        vb = _view_box_numbers(out["view_box"])
        if vb:
            width, height, wu, hu = vb[2], vb[3], "", ""
    out["width"], out["height"] = width, height
    out["units"] = wu or hu or ""
    return out


_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokenize(*parts: Any) -> list[str]:
    seen: list[str] = []
    for part in parts:
        if not part:
            continue
        for chunk in _TOKEN_SPLIT.split(str(part)):
            for token in _CAMEL.split(chunk):
                token = token.lower()
                if len(token) >= 2 and token not in seen:
                    seen.append(token)
    return seen


def normalize_license(raw: Any) -> str:
    if not raw:
        return "UNKNOWN"
    key = str(raw).strip().lower()
    if key in LICENSE_MAP:
        return LICENSE_MAP[key]
    exact = str(raw).strip()
    if exact in LICENSE_NOTICE:
        return exact
    return "UNKNOWN"


def expand_license_allow(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    allowed: set[str] = set()
    for raw in values:
        for token in str(raw).split(","):
            token = token.strip()
            if not token:
                continue
            family = LICENSE_FAMILY.get(token.upper())
            if family:
                allowed.update(family)
                continue
            normalized = normalize_license(token)
            allowed.add(normalized if normalized != "UNKNOWN" else token)
    return allowed or None


# ---------------------------------------------------------------- index paths

def components_dir(args: Any) -> Path:
    raw = getattr(args, "components_dir", None)
    return Path(raw).resolve() if raw else DEFAULT_COMPONENTS_DIR


def index_path(root: Path, source: str) -> Path:
    return root / source / "index.json"


def load_index(root: Path, source: str) -> dict[str, Any]:
    path = index_path(root, source)
    if not path.exists():
        die("no index for source '%s' (expected %s) -- run: index build --source %s"
            % (source, path, source))
    return load_json(path)


def available_sources(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "index.json").exists())


def resolve_component_path(entry: dict[str, Any], root: Path) -> Path:
    """Index paths are plugin-relative when possible, absolute otherwise."""
    raw = entry.get("path") or ""
    candidate = Path(raw)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    for base in (PLUGIN_ROOT, root, root.parent, Path.cwd()):
        probe = base / raw
        if probe.exists():
            return probe
    return PLUGIN_ROOT / raw


# ---------------------------------------------------------------- build: bioicons

def git_head(repo: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


BIOICONS_SPARSE = ["/static/icons/**", "/LICENSE", "/CITATION.cff"]


def clone_bioicons(dest: Path) -> None:
    """Shallow, sparse clone: only the icon tree and the upstream license/citation.

    Sparse matters beyond disk. The plugin's own `doctor` link-lints every *.md under
    the plugin root, and an upstream checkout's docs would fail that lint on links that
    are none of our business. Checking out only /static/icons keeps the clone free of
    markdown, so a built index never turns doctor red."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    print("cloning %s (sparse: static/icons) -> %s" % (BIOICONS_REPO_URL, dest))
    steps = [
        ["git", "clone", "--depth", "1", "--no-checkout", BIOICONS_REPO_URL, str(dest)],
        ["git", "-C", str(dest), "sparse-checkout", "init", "--no-cone"],
        ["git", "-C", str(dest), "sparse-checkout", "set"] + BIOICONS_SPARSE,
        ["git", "-C", str(dest), "checkout"],
    ]
    for step in steps:
        result = subprocess.run(step)
        if result.returncode != 0:
            die("git step failed (exit %s): %s" % (result.returncode, " ".join(step[:4])), 3)


def build_bioicons(repo: Path, root: Path, source: str = "bioicons") -> dict[str, Any]:
    icons_dir = repo / "static" / "icons"
    if not icons_dir.exists():
        die("not a bioicons checkout: %s missing" % icons_dir)

    meta_path = icons_dir / "icons.json"
    authors_path = icons_dir / "authors.json"
    metadata = load_json(meta_path) if meta_path.exists() else []
    authors = load_json(authors_path) if authors_path.exists() else {}
    if not isinstance(authors, dict):
        authors = {}

    # relpath (posix, no extension) -> absolute file
    on_disk: dict[str, Path] = {}
    for svg in icons_dir.rglob("*.svg"):
        rel = svg.relative_to(icons_dir).as_posix()
        on_disk[rel[:-4]] = svg

    # (category, author, name) -> metadata record.
    # On disk the author is a path segment, so spaces became underscores; matching
    # normalizes both sides (transcription of the same field, not inference).
    def akey(value: Any) -> str:
        return str(value or "").replace(" ", "_")

    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    by_cat_name: dict[tuple[str, str], list[dict[str, Any]]] = {}
    if isinstance(metadata, list):
        for record in metadata:
            if not isinstance(record, dict):
                continue
            category_r = str(record.get("category") or "")
            name_r = str(record.get("name") or "")
            by_key[(category_r, akey(record.get("author")), name_r)] = record
            by_cat_name.setdefault((category_r, name_r), []).append(record)

    components: list[dict[str, Any]] = []
    conflicts = 0
    unreadable = 0
    for rel_id in sorted(on_disk):
        svg_path = on_disk[rel_id]
        parts = rel_id.split("/")
        # layout: <license>/<category>/<author>/<name>
        license_dir = parts[0] if len(parts) >= 4 else ""
        category = parts[1] if len(parts) >= 4 else (parts[0] if len(parts) > 1 else "")
        author = parts[2] if len(parts) >= 4 else ""
        name = parts[-1]

        record = by_key.get((category, akey(author), name))
        if record is None:
            # Fall back to (category, name) only when it resolves uniquely.
            siblings = by_cat_name.get((category, name)) or []
            if len(siblings) == 1:
                record = siblings[0]
        meta_license = normalize_license(record.get("license")) if record else "UNKNOWN"
        dir_license = normalize_license(license_dir)
        if record is None:
            # File on disk with no metadata row: record it, do not invent a license.
            license_id = "UNKNOWN"
            license_source = "absent-from-icons.json"
        elif meta_license == "UNKNOWN":
            license_id = "UNKNOWN"
            license_source = "icons.json token unmapped: %r" % record.get("license")
        elif dir_license != "UNKNOWN" and dir_license != meta_license:
            license_id = "UNKNOWN"
            license_source = "conflict: icons.json=%s dir=%s" % (meta_license, dir_license)
            conflicts += 1
        else:
            license_id = meta_license
            license_source = "icons.json"

        geometry = read_svg_geometry(svg_path)
        if not geometry["ok"]:
            unreadable += 1

        rel_static = "static/icons/%s.svg" % rel_id
        components.append({
            "id": rel_id,
            "name": name,
            "category": category,
            "license": license_id,
            "path": rel_to_plugin(svg_path),
            "width": geometry["width"],
            "height": geometry["height"],
            "source_url": "%s/blob/main/%s" % (BIOICONS_REPO_URL, rel_static),
            "author": author,
            "author_url": (authors.get(author) or authors.get(author.replace("_", " "))
                           or (authors.get(str(record.get("author"))) if record else None)),
            "license_source": license_source,
            "units": geometry["units"],
            "view_box": geometry["view_box"],
            "site_url": "%s/icons/%s.svg" % (BIOICONS_SITE, rel_id),
            "tags": tokenize(category, name, author if len(author) < 24 else ""),
            "svg_parse_ok": geometry["ok"],
        })

    index = {
        "schema": SCHEMA,
        "source": source,
        "source_name": "Bioicons",
        "site": BIOICONS_SITE,
        "acquisition": "clone",
        "generated_at": utc_now(),
        "repo": {
            "url": BIOICONS_REPO_URL,
            "commit": git_head(repo),
            "local_path": rel_to_plugin(repo),
            "rebuild": "git clone --depth 1 %s <repo> && component_index.py index build "
                       "--source bioicons --repo <repo>" % BIOICONS_REPO_URL,
        },
        "license_model": "mixed, per icon (transcribed from static/icons/icons.json; "
                         "unmapped, absent or conflicting entries are recorded as UNKNOWN)",
        "notes": {
            "conflicting_license_rows": conflicts,
            "unparsable_svg": unreadable,
            "metadata_rows": len(by_key),
        },
        "count": len(components),
        "license_distribution": license_distribution(components),
        "components": components,
    }
    write_index(root, source, index)
    return index


# ---------------------------------------------------------------- build: generic dir

def build_dir(repo: Path, root: Path, source: str) -> dict[str, Any]:
    """Index any folder of SVGs. Licenses come from an optional manifest.json;
    a file the manifest does not cover is indexed as UNKNOWN, never guessed."""
    if not repo.exists():
        die("source folder not found: %s" % repo)
    manifest_path = repo / "manifest.json"
    manifest = load_json(manifest_path) if manifest_path.exists() else {}
    if not isinstance(manifest, dict):
        manifest = {}
    entries = manifest.get("components") if isinstance(manifest.get("components"), dict) else manifest

    components: list[dict[str, Any]] = []
    for svg in sorted(repo.rglob("*.svg"), key=lambda p: p.relative_to(repo).as_posix()):
        rel = svg.relative_to(repo).as_posix()
        record = entries.get(rel) if isinstance(entries, dict) else None
        record = record if isinstance(record, dict) else {}
        geometry = read_svg_geometry(svg)
        parent = svg.parent.relative_to(repo).as_posix()
        name = record.get("name") or svg.stem
        category = record.get("category") or (parent if parent != "." else "uncategorized")
        license_id = normalize_license(record.get("license"))
        components.append({
            "id": rel[:-4],
            "name": name,
            "category": category,
            "license": license_id,
            "path": rel_to_plugin(svg),
            "width": geometry["width"],
            "height": geometry["height"],
            "source_url": record.get("source_url") or manifest.get("source_url") or None,
            "author": record.get("author") or manifest.get("author") or None,
            "author_url": record.get("author_url"),
            "license_source": "manifest.json" if record.get("license") else
                              ("manifest entry without license" if record else "no manifest entry"),
            "units": geometry["units"],
            "view_box": geometry["view_box"],
            "tags": tokenize(category, name, record.get("tags")),
            "svg_parse_ok": geometry["ok"],
        })

    index = {
        "schema": SCHEMA,
        "source": source,
        "source_name": manifest.get("source_name") or source,
        "site": manifest.get("site"),
        "acquisition": "local-folder",
        "generated_at": utc_now(),
        "repo": {"url": manifest.get("source_url"), "commit": git_head(repo),
                 "local_path": rel_to_plugin(repo),
                 "rebuild": "component_index.py index build --source dir --name %s --repo %s"
                            % (source, rel_to_plugin(repo))},
        "license_model": "per file, from manifest.json; uncovered files are UNKNOWN",
        "count": len(components),
        "license_distribution": license_distribution(components),
        "components": components,
    }
    write_index(root, source, index)
    return index


# ---------------------------------------------------------------- build: manual

def build_manual(source: str, root: Path) -> dict[str, Any]:
    spec = MANUAL_SOURCES[source]
    index = {
        "schema": SCHEMA,
        "source": source,
        "source_name": spec["name"],
        "site": spec["site"],
        "acquisition": "manual-import",
        "status": "not-imported",
        "generated_at": utc_now(),
        "why_not_automatic": spec["reason"],
        "license_model": "source states %s for the collection; each imported file must "
                         "still carry its own recorded license" % spec["license_stated_by_source"],
        "approx_upstream_count": spec["approx_count"],
        "import_steps": spec["steps"],
        "count": 0,
        "license_distribution": {},
        "components": [],
    }
    write_index(root, source, index)
    readme = ["# %s -- manual import required" % spec["name"], "",
              "Registered as a component source, **not indexed**: `count = 0`.",
              "No entry is fabricated; the index stays empty until a human imports the files.",
              "", "## Why this source is not cloned", "", spec["reason"], "",
              "## Import steps", ""]
    for i, step in enumerate(spec["steps"], 1):
        readme.append("%d. %s" % (i, step))
    readme += ["", "Upstream states **%s** for the collection (roughly %d items), but the "
               "per-file license still has to be recorded at import time."
               % (spec["license_stated_by_source"], spec["approx_count"]), ""]
    write_text(root / source / "README.md", "\n".join(readme))
    return index


def license_distribution(components: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in components:
        key = entry.get("license") or "UNKNOWN"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


REDISTRIBUTABLE = ("CC0-1.0", "CC-BY-4.0", "CC-BY-3.0", "MIT", "BSD-3-Clause",
                   "Apache-2.0", "CC-BY-SA-4.0")

ASSETS_MARKER = "assets_ok"


def sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(65536), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def is_redistributable(license_id: Any) -> bool:
    """R9 A7. Conservative on purpose: a licence we did not transcribe, or one
    outside the known-permissive list, is treated as NOT redistributable, so the
    svg body is never copied into a render directory on a guess."""
    key = str(license_id or "").strip()
    return bool(key) and key.upper() != "UNKNOWN" and key in REDISTRIBUTABLE


def component_provenance(source: str, entry: dict[str, Any], root: Path,
                         index: dict[str, Any] | None = None) -> dict[str, Any]:
    """R9 A7: the strong contract every used component instance must record --
    where it came from, at which commit, which exact bytes, under which licence,
    with the attribution line that has to travel with it."""
    index = index if index is not None else load_index(root, source)
    repo = index.get("repo") or {}
    path = resolve_component_path(entry, root)
    licence = entry.get("license") or "UNKNOWN"
    author = entry.get("author") or "unknown creator"
    return {
        "source": source,
        "source_repo": repo.get("url") or index.get("site") or source,
        "source_commit": repo.get("commit"),
        "asset_id": entry.get("id"),
        "asset_path": entry.get("path"),
        "asset_sha256": sha256_file(path) if path.exists() else None,
        "license_id": licence,
        "attribution_text": "%s — %s (%s), %s"
                            % (entry.get("name") or entry.get("id"), author, licence,
                               entry.get("source_url") or index.get("site") or source),
        "redistributable": is_redistributable(licence),
        "source_url": entry.get("source_url"),
        "author": author,
        "license": licence,
    }


def assets_marker_path(root: Path, source: str) -> Path:
    return root / source / ASSETS_MARKER


def probe_assets(root: Path | None = None, source: str = "bioicons",
                 sample: int = 24) -> dict[str, Any]:
    """R9 A7: `component.<source>` means "index AND assets", not "index".

    A tracked index.json with a git-ignored, never-rebuilt svg tree used to
    probe as available and then fail at assembly time. This reads the index and
    actually opens a sample of the files it points at; the verdict is written to
    the `assets_ok` marker beside index.json so a registry probe can be a plain
    file-existence check without re-walking thousands of icons.
    """
    root = root or DEFAULT_COMPONENTS_DIR
    path = index_path(root, source)
    if not path.exists():
        return {"source": source, "ok": False, "index": False, "reason":
                "no index.json for %s (run: index build --source %s)" % (source, source),
                "checked": 0, "readable": 0, "count": 0}
    try:
        index = load_json(path)
    except SystemExit:                                   # pragma: no cover
        return {"source": source, "ok": False, "index": False,
                "reason": "index.json is not readable JSON", "checked": 0,
                "readable": 0, "count": 0}
    components = index.get("components") or []
    if not components:
        return {"source": source, "ok": False, "index": True, "count": 0, "checked": 0,
                "readable": 0,
                "reason": "index has 0 components (manual-import source?)"}
    step = max(1, len(components) // max(sample, 1))
    probed = components[::step][:sample]
    readable, missing = 0, []
    for entry in probed:
        candidate = resolve_component_path(entry, root)
        if candidate.exists() and candidate.stat().st_size > 0:
            readable += 1
        elif len(missing) < 5:
            missing.append(entry.get("id"))
    ok = readable == len(probed)
    return {"source": source, "ok": ok, "index": True, "count": len(components),
            "checked": len(probed), "readable": readable,
            "missing_sample": missing,
            "reason": "" if ok else
                      "%d/%d sampled svg bodies unreadable (git-ignored and not rebuilt?); "
                      "e.g. %s" % (len(probed) - readable, len(probed), ", ".join(missing))}


def write_assets_marker(root: Path, source: str) -> dict[str, Any]:
    """Write (or remove) the `assets_ok` marker so a capability probe is a
    one-line file check instead of a directory walk."""
    verdict = probe_assets(root, source)
    marker = assets_marker_path(root, source)
    if verdict["ok"]:
        write_json(marker, dict(verdict, written_at=utc_now()))
    else:
        try:
            marker.unlink()
        except OSError:
            pass
    return verdict


def write_index(root: Path, source: str, index: dict[str, Any]) -> None:
    """One component per line: keeps a 2800-row index diffable and ~40% smaller."""
    head = {k: v for k, v in index.items() if k != "components"}
    body = json.dumps(head, ensure_ascii=False, indent=1)
    lines = [json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
             for entry in index.get("components") or []]
    nl = chr(10)
    inner = (nl + "  " + ("," + nl + "  ").join(lines) + nl + " ") if lines else ""
    text = (body[:-1].rstrip() + "," + nl + ' "components": [' + inner + "]" + nl + "}" + nl)
    write_text(index_path(root, source), text)


# ---------------------------------------------------------------- search

def iter_components(root: Path, sources: list[str] | None) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for source in (sources or available_sources(root)):
        index = load_index(root, source)
        for entry in index.get("components") or []:
            out.append((source, entry))
    return out


def score_entry(entry: dict[str, Any], wanted: list[str]) -> float:
    if not wanted:
        return 1.0
    haystack = set(entry.get("tags") or [])
    haystack.update(tokenize(entry.get("name"), entry.get("category"), entry.get("id")))
    hits = 0
    for tag in wanted:
        if tag in haystack or any(tag in token for token in haystack):
            hits += 1
    return hits / float(len(wanted))


def resolve_ref(ref: str, root: Path) -> tuple[str, dict[str, Any]]:
    """Resolve 'source:id' (exact) or 'source:name' (unique match)."""
    if ":" not in ref:
        die("component ref must be '<source>:<id>' -- got %r" % ref)
    source, ident = ref.split(":", 1)
    index = load_index(root, source)
    components = index.get("components") or []
    for entry in components:
        if entry.get("id") == ident:
            return source, entry
    matches = [e for e in components
               if e.get("name") == ident or (e.get("id") or "").endswith("/" + ident)]
    if len(matches) == 1:
        return source, matches[0]
    if not matches:
        die("no component %r in source %r" % (ident, source))
    die("ambiguous component %r in %r (%d matches, e.g. %s) -- use the full id"
        % (ident, source, len(matches), matches[0].get("id")))
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- attribution

def render_attribution(refs: list[str], root: Path, figure: str | None) -> str:
    resolved = [resolve_ref(ref, root) for ref in refs]
    by_license: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for source, entry in resolved:
        by_license.setdefault(entry.get("license") or "UNKNOWN", []).append((source, entry))

    lines = ["# Component attribution", "",
             "Generated by `scripts/component_index.py index attribution` on %s." % utc_now()]
    if figure:
        lines.append("Figure: `%s`." % figure)
    lines += ["Components used: **%d** across %d license(s)." % (len(resolved), len(by_license)),
              ""]
    if "UNKNOWN" in by_license:
        lines += ["> **BLOCKING:** %d component(s) below have an unresolved license. "
                  "Resolve each at its source URL before the figure ships."
                  % len(by_license["UNKNOWN"]), ""]

    for license_id in sorted(by_license, key=lambda k: (k == "UNKNOWN", k)):
        url = LICENSE_URL.get(license_id)
        heading = "## %s" % license_id
        if url:
            heading += " ([license text](%s))" % url
        lines += [heading, "", LICENSE_NOTICE.get(license_id, "See the source for terms."), "",
                  "| Component | Creator | Source |", "|---|---|---|"]
        for source, entry in sorted(by_license[license_id],
                                    key=lambda se: (se[0], se[1].get("id") or "")):
            author = entry.get("author") or "(creator not recorded)"
            if entry.get("author_url"):
                author = "[%s](%s)" % (author, entry["author_url"])
            src = entry.get("source_url") or entry.get("site_url") or "(no source url recorded)"
            if src.startswith("http"):
                src = "[link](%s)" % src
            lines.append("| `%s:%s` (%s) | %s | %s |"
                         % (source, entry.get("id"), entry.get("name"), author, src))
        lines.append("")

    lines += ["## Collections", ""]
    seen_sources: list[str] = []
    for source, _entry in resolved:
        if source not in seen_sources:
            seen_sources.append(source)
    for source in seen_sources:
        index = load_index(root, source)
        site = index.get("site") or (index.get("repo") or {}).get("url") or ""
        commit = (index.get("repo") or {}).get("commit")
        line = "- **%s**" % (index.get("source_name") or source)
        if site:
            line += " -- %s" % site
        if commit:
            line += " (indexed at commit `%s`)" % commit[:12]
        lines.append(line)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- svg assembly

def _strip_foreign(element: ET.Element) -> None:
    """Drop non-SVG elements (RDF metadata, sodipodi) and foreign attributes.
    This also removes the external RDF license URLs, keeping the output self-contained."""
    for child in list(element):
        tag = child.tag
        if not isinstance(tag, str) or not tag.startswith(SVG_Q):
            element.remove(child)
            continue
        _strip_foreign(child)
    for key in list(element.attrib):
        if key.startswith("{") and not key.startswith(XLINK_Q):
            del element.attrib[key]


_URL_REF = re.compile(r"url\(\s*#([^)\s]+)\s*\)")


def _prefix_ids(element: ET.Element, prefix: str) -> None:
    ids: set[str] = set()

    def collect(node: ET.Element) -> None:
        node_id = node.get("id")
        if node_id:
            ids.add(node_id)
        for child in node:
            collect(child)

    collect(element)
    if not ids:
        return

    def rewrite(node: ET.Element) -> None:
        for key, value in list(node.attrib.items()):
            if key == "id" and value in ids:
                node.set("id", prefix + value)
                continue
            if not isinstance(value, str):
                continue
            new_value = _URL_REF.sub(
                lambda m: "url(#%s%s)" % (prefix, m.group(1)) if m.group(1) in ids else m.group(0),
                value)
            if key in ("href", XLINK_Q + "href") and value.startswith("#") and value[1:] in ids:
                new_value = "#" + prefix + value[1:]
            if new_value != value:
                node.set(key, new_value)
        for child in node:
            rewrite(child)

    rewrite(element)


def external_refs(element: ET.Element) -> list[str]:
    found: list[str] = []

    def walk(node: ET.Element) -> None:
        for key, value in node.attrib.items():
            if not isinstance(value, str):
                continue
            low = value.strip().lower()
            if key in ("href", XLINK_Q + "href") and (
                    low.startswith("http://") or low.startswith("https://") or low.startswith("//")):
                found.append(value)
            elif key == "style" and ("url(http" in low or "url('http" in low or 'url("http' in low):
                found.append(value)
        for child in node:
            walk(child)

    walk(element)
    return found


def load_component_svg(path: Path, slot: str) -> tuple[ET.Element, list[float]]:
    """Return (cleaned <svg> element, viewBox numbers) for embedding."""
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError) as exc:
        die("cannot parse component svg %s: %s" % (path, exc), 3)
    _strip_foreign(root)
    _prefix_ids(root, "s%s_" % slot)
    refs = external_refs(root)
    if refs:
        die("component %s pulls external resources (%s); refusing to assemble a figure "
            "that is not self-contained" % (path, refs[0]), 3)
    view_box = _view_box_numbers(root.get("viewBox"))
    if not view_box:
        width, _ = parse_length(root.get("width"))
        height, _ = parse_length(root.get("height"))
        view_box = [0.0, 0.0, width or 100.0, height or 100.0]
    return root, view_box


def place_component(root: ET.Element, view_box: list[float],
                    x: float, y: float, w: float, h: float) -> ET.Element:
    nested = ET.Element(SVG_Q + "svg", {
        "x": fnum(x), "y": fnum(y), "width": fnum(w), "height": fnum(h),
        "viewBox": " ".join(fnum(v) for v in view_box),
        "preserveAspectRatio": "xMidYMid meet",
        "overflow": "visible",
    })
    for child in list(root):
        nested.append(child)
    return nested


def fnum(value: float) -> str:
    text = "%.3f" % float(value)
    text = text.rstrip("0").rstrip(".")
    return text or "0"


def wrap_label(text: str, max_chars: int) -> list[str]:
    words = str(text).split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= max_chars:
            current += " " + word
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines[:3]


def make_text(x: float, y: float, content: str, size: float, anchor: str,
              family: str, weight: str = "normal") -> ET.Element:
    node = ET.Element(SVG_Q + "text", {
        "x": fnum(x), "y": fnum(y), "font-family": family, "font-size": fnum(size),
        "text-anchor": anchor, "fill": "#111111",
    })
    if weight != "normal":
        node.set("font-weight", weight)
    node.text = content
    return node


def arrow(x1: float, y1: float, x2: float, y2: float, stroke: float) -> list[ET.Element]:
    """A 1px connector drawn in code: a line plus a solid triangular head."""
    head = max(4.0 * stroke, 4.0)
    angle = math.atan2(y2 - y1, x2 - x1)
    bx, by = x2 - head * math.cos(angle), y2 - head * math.sin(angle)
    perp = angle + math.pi / 2
    hw = head * 0.42
    points = "%s,%s %s,%s %s,%s" % (
        fnum(x2), fnum(y2),
        fnum(bx + hw * math.cos(perp)), fnum(by + hw * math.sin(perp)),
        fnum(bx - hw * math.cos(perp)), fnum(by - hw * math.sin(perp)))
    line = ET.Element(SVG_Q + "line", {
        "x1": fnum(x1), "y1": fnum(y1), "x2": fnum(bx), "y2": fnum(by),
        "stroke": "#333333", "stroke-width": fnum(stroke), "stroke-linecap": "butt",
    })
    tri = ET.Element(SVG_Q + "polygon", {"points": points, "fill": "#333333"})
    return [line, tri]


TEMPLATES = ("pipeline-lr", "architecture-stack")


def assemble_svg(template: str, slots: list[tuple[str, str]], labels: dict[str, str],
                 root: Path, width_mm: float, title: str | None,
                 stroke_px: float, font_pt: float) -> tuple[str, list[dict[str, Any]]]:
    if template not in TEMPLATES:
        die("unknown template %r (have: %s)" % (template, ", ".join(TEMPLATES)))
    if not slots:
        die("--slots is required (e.g. --slots A=bioicons:<id> B=bioicons:<id>)")

    resolved: list[dict[str, Any]] = []
    for slot, ref in slots:
        source, entry = resolve_ref(ref, root)
        path = resolve_component_path(entry, root)
        if not path.exists():
            die("component file missing: %s\n  The svg bodies are git-ignored; rebuild the "
                "source with: index build --source %s" % (path, source), 3)
        node, view_box = load_component_svg(path, slot)
        resolved.append({"slot": slot, "source": source, "entry": entry,
                         "node": node, "view_box": view_box, "path": path})

    font_px = font_pt * 96.0 / 72.0
    canvas_w = width_mm * MM2PX
    margin = 6.0  # px
    title_h = (font_px * 1.6 + 4.0) if title else 0.0

    layers_bg = ET.Element(SVG_Q + "g", {"id": "layer-connectors"})
    layers_art = ET.Element(SVG_Q + "g", {"id": "layer-components"})
    layers_text = ET.Element(SVG_Q + "g", {"id": "layer-text"})
    family = "Helvetica, Arial, 'Nimbus Sans', sans-serif"
    n = len(resolved)

    if template == "pipeline-lr":
        gap = 26.0
        cell_w = (canvas_w - 2 * margin - (n - 1) * gap) / max(n, 1)
        icon_side = min(cell_w, 58.0)
        label_lines = {}
        max_chars = max(int(cell_w / (font_px * 0.52)), 6)
        for item in resolved:
            label_lines[item["slot"]] = wrap_label(
                labels.get(item["slot"], item["entry"].get("name") or item["slot"]), max_chars)
        label_rows = max((len(v) for v in label_lines.values()), default=0)
        label_h = label_rows * font_px * 1.25 + (4.0 if label_rows else 0.0)
        canvas_h = margin + title_h + icon_side + label_h + margin
        icon_top = margin + title_h
        for i, item in enumerate(resolved):
            cx = margin + i * (cell_w + gap) + cell_w / 2.0
            layers_art.append(place_component(item["node"], item["view_box"],
                                              cx - icon_side / 2.0, icon_top,
                                              icon_side, icon_side))
            item["box"] = (cx - icon_side / 2.0, icon_top, icon_side, icon_side)
            ty = icon_top + icon_side + font_px
            for line in label_lines[item["slot"]]:
                layers_text.append(make_text(cx, ty, line, font_px, "middle", family))
                ty += font_px * 1.25
            if i:
                prev = resolved[i - 1]["box"]
                layers_bg.extend(arrow(prev[0] + prev[2] + 4.0, icon_top + icon_side / 2.0,
                                       cx - icon_side / 2.0 - 4.0, icon_top + icon_side / 2.0,
                                       stroke_px))
    else:  # architecture-stack
        band_h = 46.0
        v_gap = 18.0
        icon_side = band_h - 8.0
        canvas_h = margin + title_h + n * band_h + (n - 1) * v_gap + margin
        text_x = margin + icon_side + 10.0
        max_chars = max(int((canvas_w - text_x - margin) / (font_px * 0.52)), 8)
        for i, item in enumerate(resolved):
            top = margin + title_h + i * (band_h + v_gap)
            layers_bg.append(ET.Element(SVG_Q + "rect", {
                "x": fnum(margin), "y": fnum(top),
                "width": fnum(canvas_w - 2 * margin), "height": fnum(band_h),
                "fill": "none", "stroke": "#888888", "stroke-width": fnum(stroke_px),
                "rx": "3"}))
            layers_art.append(place_component(item["node"], item["view_box"],
                                              margin + 4.0, top + 4.0, icon_side, icon_side))
            item["box"] = (margin, top, canvas_w - 2 * margin, band_h)
            lines = wrap_label(labels.get(item["slot"],
                                          item["entry"].get("name") or item["slot"]), max_chars)
            ty = top + band_h / 2.0 - (len(lines) - 1) * font_px * 0.62 + font_px * 0.35
            for line in lines:
                layers_text.append(make_text(text_x, ty, line, font_px, "start", family))
                ty += font_px * 1.25
            if i:
                prev_bottom = top - v_gap
                layers_bg.extend(arrow(canvas_w / 2.0, prev_bottom + 3.0,
                                       canvas_w / 2.0, top - 3.0, stroke_px))

    svg = ET.Element(SVG_Q + "svg", {
        "width": "%smm" % fnum(width_mm),
        "height": "%smm" % fnum(canvas_h / MM2PX),
        "viewBox": "0 0 %s %s" % (fnum(canvas_w), fnum(canvas_h)),
        "version": "1.1",
    })
    desc = ET.SubElement(svg, SVG_Q + "desc")
    desc.text = ("auto-research component assembly | template=%s | slots=%s | generated=%s"
                 % (template, ";".join("%s=%s:%s" % (i["slot"], i["source"], i["entry"].get("id"))
                                       for i in resolved), utc_now()))
    if title:
        layers_text.insert(0, make_text(margin, margin + font_px * 1.15, title,
                                        font_px * 1.15, "start", family, "bold"))
    svg.append(layers_bg)
    svg.append(layers_art)
    svg.append(layers_text)

    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    body = ET.tostring(svg, encoding="unicode")
    manifest = []
    for i in resolved:
        row = component_provenance(i["source"], i["entry"], root)
        row["slot"] = i["slot"]
        row["ref"] = "%s:%s" % (i["source"], i["entry"].get("id"))
        manifest.append(row)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n", manifest


# ---------------------------------------------------------------- commands

def parse_pairs(values: list[str] | None, what: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in values or []:
        if "=" not in raw:
            die("%s must be KEY=VALUE, got %r" % (what, raw))
        key, value = raw.split("=", 1)
        out.append((key.strip(), value.strip()))
    return out


def split_list(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        for token in str(raw).split(","):
            token = token.strip()
            if token:
                out.append(token)
    return out


def cmd_index(args: argparse.Namespace) -> int:
    root = components_dir(args)
    action = args.action

    if action == "build":
        source = args.source
        if source in MANUAL_SOURCES:
            index = build_manual(source, root)
            print("registered %s as manual-import (0 components; see %s)"
                  % (source, rel_to_plugin(root / source / "README.md")))
            for i, step in enumerate(index["import_steps"], 1):
                print("  %d. %s" % (i, step))
            return 0
        if source == "bioicons":
            repo = Path(args.repo).resolve() if args.repo else (root / "bioicons" / "repo")
            if not repo.exists():
                clone_bioicons(repo)
            index = build_bioicons(repo, root)
        elif source == "dir":
            if not args.repo or not args.name:
                die("--source dir requires --repo and --name")
            index = build_dir(Path(args.repo).resolve(), root, args.name)
        else:
            die("unknown source %r (bioicons | dir | %s)" % (source, " | ".join(MANUAL_SOURCES)))
            return 2
        verdict = write_assets_marker(root, index["source"])
        print("indexed %d components -> %s"
              % (index["count"], rel_to_plugin(index_path(root, index["source"]))))
        print("licenses: %s" % json.dumps(index["license_distribution"], ensure_ascii=False))
        print("assets: %s (%d/%d sampled bodies readable)%s"
              % ("OK -> %s" % rel_to_plugin(assets_marker_path(root, index["source"]))
                 if verdict["ok"] else "MISSING (marker not written)",
                 verdict["readable"], verdict["checked"],
                 "" if verdict["ok"] else " -- " + verdict["reason"]))
        return 0

    if action == "probe":
        source = (split_list(args.source) or ["bioicons"])[0]
        verdict = write_assets_marker(root, source)
        print(json.dumps(verdict, ensure_ascii=False, indent=1))
        return 0 if verdict["ok"] else 1

    if action == "search":
        wanted = [t.lower() for t in split_list(args.tags)]
        allowed = expand_license_allow(args.license_allow)
        sources = split_list(args.source) or None
        rows = []
        for source, entry in iter_components(root, sources):
            if allowed and (entry.get("license") or "UNKNOWN") not in allowed:
                continue
            score = score_entry(entry, wanted)
            if score <= 0:
                continue
            rows.append((score, source, entry))
        rows.sort(key=lambda r: (-r[0], r[1], r[2].get("id") or ""))
        rows = rows[:args.limit]
        if args.json:
            print(json.dumps([{"score": round(s, 3), "ref": "%s:%s" % (src, e.get("id")),
                               "name": e.get("name"), "category": e.get("category"),
                               "license": e.get("license")} for s, src, e in rows],
                             ensure_ascii=False, indent=1))
            return 0
        if not rows:
            print("no match (tags=%s, license-allow=%s)"
                  % (",".join(wanted) or "-", ",".join(sorted(allowed)) if allowed else "-"))
            return 1
        print("%-5s  %-14s  %-28s  %s" % ("score", "license", "name", "ref"))
        for score, source, entry in rows:
            print("%-5.2f  %-14s  %-28s  %s:%s"
                  % (score, entry.get("license"), (entry.get("name") or "")[:28],
                     source, entry.get("id")))
        print("-- %d shown" % len(rows))
        return 0

    if action == "attribution":
        refs = split_list(args.used)
        if args.used_file:
            refs += split_list([Path(args.used_file).read_text(encoding="utf-8")
                                .replace("\n", ",")])
        if not refs:
            die("--used (or --used-file) is required")
        text = render_attribution(refs, root, args.figure)
        out = Path(args.out).resolve() if args.out else (root / "attribution.md")
        write_text(out, text)
        print("wrote %s (%d components)" % (rel_to_plugin(out), len(refs)))
        if "BLOCKING" in text:
            print("WARNING: unresolved licenses present -- see the file")
        return 0

    if action == "stats":
        sources = split_list(args.source) or available_sources(root)
        if not sources:
            print("no component sources indexed under %s" % rel_to_plugin(root))
            return 1
        payload = {}
        for source in sources:
            index = load_index(root, source)
            components = index.get("components") or []
            dist = index.get("license_distribution") or license_distribution(components)
            unknown = dist.get("UNKNOWN", 0)
            categories = sorted({e.get("category") or "" for e in components})
            payload[source] = {
                "acquisition": index.get("acquisition"),
                "count": index.get("count", len(components)),
                "categories": len(categories),
                "license_distribution": dist,
                "unknown_license": unknown,
                "unknown_pct": round(100.0 * unknown / len(components), 2) if components else 0.0,
                "unparsable_svg": sum(1 for e in components if e.get("svg_parse_ok") is False),
                "commit": (index.get("repo") or {}).get("commit"),
                "generated_at": index.get("generated_at"),
            }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
            return 0
        for source, stat in payload.items():
            print("[%s] %s | %d components | %d categories | UNKNOWN license: %d (%.2f%%) | "
                  "unparsable svg: %d"
                  % (source, stat["acquisition"], stat["count"], stat["categories"],
                     stat["unknown_license"], stat["unknown_pct"], stat["unparsable_svg"]))
            for license_id, count in (stat["license_distribution"] or {}).items():
                print("    %-14s %5d" % (license_id, count))
        return 0

    die("unknown index action %r" % action)
    return 2


def cmd_assemble(args: argparse.Namespace) -> int:
    root = components_dir(args)
    slots = parse_pairs(args.slots, "--slots")
    labels = dict(parse_pairs(args.labels, "--labels"))
    text, manifest = assemble_svg(args.template, slots, labels, root,
                                  args.width_mm, args.title, args.stroke_px, args.font_pt)
    out = Path(args.out).resolve()
    write_text(out, text)
    print("wrote %s (%d components, template=%s)" % (out, len(manifest), args.template))
    unknown = [m for m in manifest if m["license"] == "UNKNOWN"]
    if unknown:
        print("WARNING: %d component(s) with UNKNOWN license: %s"
              % (len(unknown), ", ".join(m["ref"] for m in unknown)))
    if args.attribution:
        att = Path(args.attribution).resolve()
        write_text(att, render_attribution([m["ref"] for m in manifest], root, str(out.name)))
        print("wrote %s" % att)
    if args.print_manifest:
        print(json.dumps(manifest, ensure_ascii=False, indent=1))
    return 0


# ---------------------------------------------------------------- self-test

MINI_SVGS = {
    "flow/source.svg": (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="40mm" '
        'viewBox="0 0 100 100">'
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#dfe7f5"/>'
        '<stop offset="1" stop-color="#9db4dd"/></linearGradient></defs>'
        '<rect x="8" y="18" width="84" height="64" rx="6" fill="url(#g)" '
        'stroke="#3c4d6b" stroke-width="3"/>'
        '<circle cx="50" cy="50" r="16" fill="#ffffff" stroke="#3c4d6b" stroke-width="3"/>'
        '</svg>\n'),
    "flow/transform.svg": (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="40mm" '
        'viewBox="0 0 100 100">'
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#f6e3d0"/>'
        '<stop offset="1" stop-color="#e0a96d"/></linearGradient></defs>'
        '<polygon points="50,10 90,50 50,90 10,50" fill="url(#g)" stroke="#7a4a16" '
        'stroke-width="3"/>'
        '<path d="M30 50 H70" stroke="#7a4a16" stroke-width="4" fill="none"/>'
        '</svg>\n'),
    "flow/sink.svg": (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="40mm" '
        'viewBox="0 0 100 100">'
        '<g id="body"><circle cx="50" cy="50" r="38" fill="#e6f2e6" stroke="#2f6b34" '
        'stroke-width="3"/>'
        '<path d="M32 52 l12 14 l24 -30" fill="none" stroke="#2f6b34" stroke-width="6"/></g>'
        '</svg>\n'),
}

MINI_MANIFEST = {
    "source_name": "component_index self-test fixture",
    "components": {
        "flow/source.svg": {"name": "data source", "category": "pipeline",
                            "license": "CC0-1.0", "author": "auto-research self-test",
                            "source_url": "https://example.invalid/selftest/source",
                            "tags": "input intake"},
        "flow/transform.svg": {"name": "transform", "category": "pipeline",
                               "license": "MIT", "author": "auto-research self-test",
                               "source_url": "https://example.invalid/selftest/transform",
                               "tags": "estimator model"},
        # third file deliberately has no manifest entry -> must land as UNKNOWN
    },
}


def self_test(_args: argparse.Namespace) -> int:
    results: list[tuple[bool, str]] = []

    def check(ok: bool, name: str, detail: str = "") -> None:
        results.append((bool(ok), name if not detail else "%s -- %s" % (name, detail)))

    tmp = Path(tempfile.mkdtemp(prefix="component_index_selftest_"))
    try:
        src = tmp / "src"
        for rel, body in MINI_SVGS.items():
            write_text(src / rel, body)
        write_text(src / "manifest.json",
                   json.dumps(MINI_MANIFEST, ensure_ascii=False, indent=1) + "\n")
        root = tmp / "components"

        index = build_dir(src, root, "selftest")
        check(index["count"] == 3, "build: 3 components indexed", "got %d" % index["count"])
        check(index_path(root, "selftest").exists(), "build: index.json written")
        licenses = {e["id"]: e["license"] for e in index["components"]}
        check(licenses.get("flow/source") == "CC0-1.0", "build: CC0 license transcribed",
              str(licenses.get("flow/source")))
        check(licenses.get("flow/transform") == "MIT", "build: MIT license transcribed",
              str(licenses.get("flow/transform")))
        check(licenses.get("flow/sink") == "UNKNOWN",
              "build: unmanifested file recorded UNKNOWN (not guessed)",
              str(licenses.get("flow/sink")))
        check(all(e["width"] == 40.0 and e["units"] == "mm" for e in index["components"]),
              "build: svg geometry parsed")
        check(index["license_distribution"].get("UNKNOWN") == 1,
              "build: license distribution counted")

        # R9 A7: the capability means index AND readable assets.
        verdict = write_assets_marker(root, "selftest")
        check(verdict["ok"] and assets_marker_path(root, "selftest").exists(),
              "R9 A7 probe_assets: index + readable bodies writes the assets_ok marker",
              json.dumps(verdict, ensure_ascii=False)[:160])
        hidden = resolve_component_path(index["components"][0], root)
        stash = hidden.with_suffix(".svg.stashed")
        hidden.rename(stash)
        broken = write_assets_marker(root, "selftest")
        stash.rename(hidden)
        check(broken["ok"] is False and not assets_marker_path(root, "selftest").exists()
              and "unreadable" in broken["reason"],
              "R9 A7 probe_assets: an index whose bodies are gone probes FALSE",
              broken["reason"][:160])
        write_assets_marker(root, "selftest")
        prov = component_provenance("selftest", index["components"][1], root)
        check(all(prov.get(k) for k in ("source_repo", "asset_id", "asset_sha256",
                                        "license_id", "attribution_text"))
              and prov["redistributable"] is True,
              "R9 A7: a used component records repo/commit/sha256/licence/attribution",
              json.dumps({k: prov[k] for k in ("license_id", "redistributable")},
                         ensure_ascii=False))
        unknown = [e for e in index["components"] if e["license"] == "UNKNOWN"][0]
        check(component_provenance("selftest", unknown, root)["redistributable"] is False,
              "R9 A7: an UNKNOWN licence is never treated as redistributable")

        wanted = ["estimator"]
        hits = [(score_entry(e, wanted), e) for _s, e in iter_components(root, ["selftest"])]
        top = sorted(hits, key=lambda r: -r[0])[0]
        check(top[0] == 1.0 and top[1]["id"] == "flow/transform",
              "search: tag hit ranks the right component", top[1]["id"])
        allowed = expand_license_allow(["CC0,MIT"])
        permitted = [e for _s, e in iter_components(root, ["selftest"])
                     if e["license"] in (allowed or set())]
        check(len(permitted) == 2, "search: --license-allow CC0,MIT filters to 2",
              "got %d" % len(permitted))

        refs = ["selftest:flow/source", "selftest:flow/transform", "selftest:flow/sink"]
        att = render_attribution(refs, root, "selftest.svg")
        check("CC0-1.0" in att and "MIT" in att, "attribution: license sections present")
        check("BLOCKING" in att and "UNKNOWN" in att,
              "attribution: UNKNOWN license raises a blocking notice")
        att_path = tmp / "attribution.md"
        write_text(att_path, att)
        check(att_path.read_bytes().count(b"\r") == 0, "attribution: LF-only output")

        for template, expect_arrows in (("pipeline-lr", 2), ("architecture-stack", 2)):
            out = tmp / ("assembled_%s.svg" % template)
            text, manifest = assemble_svg(
                template,
                [("A", "selftest:flow/source"), ("B", "selftest:flow/transform"),
                 ("C", "selftest:flow/sink")],
                {"A": "Raw panel", "B": "Threshold estimator", "C": "Certified output"},
                root, 89.0, "Self-test figure", 1.0, 8.0)
            write_text(out, text)
            parsed = ET.fromstring(text)
            nested = parsed.findall(".//" + SVG_Q + "svg")
            check(len(nested) == 3, "%s: 3 components embedded" % template,
                  "got %d" % len(nested))
            polys = parsed.findall(".//" + SVG_Q + "polygon")
            arrowheads = [p for p in polys if p.get("fill") == "#333333"]
            check(len(arrowheads) == expect_arrows,
                  "%s: %d code-drawn connectors" % (template, expect_arrows),
                  "got %d" % len(arrowheads))
            lines = parsed.findall(".//" + SVG_Q + "line")
            check(all(float(l.get("stroke-width")) == 1.0 for l in lines) and lines,
                  "%s: connector stroke is 1px" % template)
            texts = [t.text for t in parsed.findall(".//" + SVG_Q + "text")]
            check("Threshold estimator" in " ".join(t or "" for t in texts),
                  "%s: vector text layer carries the labels" % template)
            check(parsed.find(SVG_Q + "g[@id='layer-text']") is not None
                  and parsed.find(SVG_Q + "g[@id='layer-components']") is not None,
                  "%s: layers separated (components / text / connectors)" % template)
            check(not external_refs(parsed), "%s: no external references" % template)
            check("http://" not in text.replace("http://www.w3.org/2000/svg", "")
                  and "https://" not in text,
                  "%s: no http(s) url anywhere in the output" % template)
            ids = [e.get("id") for e in parsed.iter() if e.get("id")]
            check(len(ids) == len(set(ids)), "%s: no duplicate ids after slot prefixing" % template)
            check("sA_g" in ids and "sB_g" in ids,
                  "%s: colliding component ids were namespaced per slot" % template)
            check(len(manifest) == 3 and manifest[2]["license"] == "UNKNOWN",
                  "%s: assemble manifest carries per-slot license" % template)
            check(out.read_bytes().count(b"\r") == 0, "%s: LF-only svg" % template)

        stats_dist = license_distribution(index["components"])
        check(sum(stats_dist.values()) == 3, "stats: distribution sums to the count")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [name for ok, name in results if not ok]
    for ok, name in results:
        print("%s %s" % ("PASS" if ok else "FAIL", name))
    print("-- self-test: %d/%d passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# ---------------------------------------------------------------- argparse

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="component_index.py",
        description="auto-research v2.9 Route B component index and SVG assembly")
    parser.add_argument("--components-dir", help="override library/components root")
    sub = parser.add_subparsers(dest="command", required=True)

    index_p = sub.add_parser("index", help="build / search / attribute / stat component sources")
    index_p.add_argument("action",
                         choices=["build", "search", "attribution", "stats", "probe"],
                         help="probe: R9 A7 -- check index AND readable svg bodies, then "
                              "write/remove the `assets_ok` marker beside index.json")
    index_p.add_argument("--source", action="append",
                         help="bioicons | dir | reactome | scidraw (repeatable for search/stats)")
    index_p.add_argument("--repo", help="path to the cloned/downloaded source tree")
    index_p.add_argument("--name", help="index name for --source dir")
    index_p.add_argument("--tags", action="append", help="comma-separated search tags")
    index_p.add_argument("--license-allow", action="append",
                         help="comma-separated allowlist, e.g. CC0,MIT,CC-BY")
    index_p.add_argument("--limit", type=int, default=25)
    index_p.add_argument("--used", action="append", help="component refs source:id,source:id")
    index_p.add_argument("--used-file", help="file of component refs, one per line")
    index_p.add_argument("--figure", help="figure id recorded in attribution.md")
    index_p.add_argument("--out", help="output path (attribution)")
    index_p.add_argument("--json", action="store_true")
    index_p.set_defaults(func=cmd_index)

    asm = sub.add_parser("assemble", help="place indexed components into a template SVG")
    asm.add_argument("--template", required=True, choices=list(TEMPLATES))
    asm.add_argument("--slots", nargs="+", required=True, metavar="KEY=SOURCE:ID")
    asm.add_argument("--labels", nargs="*", default=[], metavar="KEY=TEXT")
    asm.add_argument("--out", required=True)
    asm.add_argument("--title")
    asm.add_argument("--width-mm", type=float, default=89.0,
                     help="89 = single column, 183 = double column")
    asm.add_argument("--stroke-px", type=float, default=1.0)
    asm.add_argument("--font-pt", type=float, default=8.0)
    asm.add_argument("--attribution", help="also write attribution.md for the used components")
    asm.add_argument("--print-manifest", action="store_true")
    asm.set_defaults(func=cmd_assemble)

    st = sub.add_parser("self-test", help="offline end-to-end check on a built-in 3-svg source")
    st.set_defaults(func=self_test)
    return parser


def force_utf8_stdio() -> None:
    """Component names carry non-ASCII creators (e.g. 'Simon Duerr' spelled with an
    umlaut). The Windows console defaults to a legacy code page and mangles them."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    args = build_parser().parse_args(argv)
    if getattr(args, "command", None) == "index" and args.action == "build":
        sources = args.source or []
        if len(sources) != 1:
            die("index build takes exactly one --source")
        args.source = sources[0]
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
