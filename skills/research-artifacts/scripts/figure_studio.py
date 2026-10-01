#!/usr/bin/env python3
"""Figure Studio (M1a) - compile a Figure Scene Graph (FSG) into skeletons,
element prompts, and a QA checklist; key magenta text placeholders back out.

Single-file CLI: stdlib only, plus optional Pillow (imported lazily inside the
`key` subcommand). Owns `research/artifacts/visual-program/fsg/` inside a
project. Mirrors visual_contract.py conventions: argparse subparsers, small
pure functions, atomic temp+replace writes, all I/O UTF-8.

Exit codes: 0 ok (warnings allowed), 1 validation/contract failure,
2 environment/usage/not-implemented, 3 Pillow missing (key only).
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

FSG_VERSION = "0.1"
ARCHETYPES = ("linear-flow", "cyclic", "hierarchical", "dual-stream", "central-hub", "comparison")
COLUMNS = ("single", "double")
SINGLE_MM, DOUBLE_MM, DEFAULT_H_MM = 89.0, 183.0, 90.0

RANKDIR = {"linear-flow": "LR", "dual-stream": "LR", "hierarchical": "TB"}
ARCHETYPE_DESC = {
    "linear-flow": "a single left-to-right pipeline of sequential stages",
    "cyclic": "a closed feedback loop that returns to its starting point",
    "hierarchical": "a top-down tree from one root down to its descendants",
    "dual-stream": "two parallel horizontal tracks compared side by side",
    "central-hub": "one central element with satellites radiating around it",
    "comparison": "two or more panels set side by side for contrast",
}

FID_RE = re.compile(r"^F\d{2}$")
PID_RE = re.compile(r"^P\d{2}$")
EID_RE = re.compile(r"^E\d{2}$")
LID_RE = re.compile(r"^L\d{2}$")
TID_RE = re.compile(r"^T\d{2}$")
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
S_RE = re.compile(r"S(\d+)")

# --- magenta key parameters (spec §6, written into code) -------------------
KEY_R_MIN, KEY_B_MIN, KEY_RB_MAX, KEY_G_MARGIN = 170, 160, 90, 4
KEY_DILATE = 5          # ImageFilter.MaxFilter(5) == 2px, 8-connectivity dilation
KEY_MIN_AREA = 30       # noise floor for components
KEY_ROW_GAP = 60        # horizontal gap tolerance when clustering bars into a base zone
KEY_VBAND_MIN = 0.5     # min vertical-band overlap fraction to treat two zones as same-row
KEY_HGAP_FRAC = 0.25    # default horizontal-merge gap as a fraction of max zone width

# --- external tool discovery (M1b: PATH first, then Windows default install) --
DEFAULT_DOT = r"C:\Program Files\Graphviz\bin\dot.exe"
DEFAULT_INKSCAPE = r"C:\Program Files\Inkscape\bin\inkscape.exe"

# --- codex-exec driver (spec §5 / D_image2_route.md) ------------------------
CODEX_STRIP_ENV = ("CLAUDECODE", "CLAUDE_CODE", "CLAUDE_PROJECT_DIR", "CLAUDE_SESSION_ID")

# --- sandwich prompt building blocks (spec §4, embedded constant) ----------
TEXT_BLOCK = {
    "none": "NO TEXT, NO LETTERS, NO NUMBERS ANYWHERE.",
    "chroma": ("EVERY LABEL POSITION = ONE SOLID RECTANGULAR MAGENTA BAR - a plain filled "
               "rectangle of pure flat magenta. SOLID RECTANGULAR BARS ONLY: never lines, "
               "frames, ribbons, outlines or decorative strokes; each bar a plain filled "
               "rectangle with NO shadow, NO outline, NO gradient. MAGENTA APPEARS NOWHERE ELSE."),
    "plate": ("EVERY LABEL POSITION = ONE EMPTY WHITE PLATE (a blank rounded rectangle) "
              "WITH NO TEXT INSIDE; leave each plate interior pure background white."),
}
FLAT_LINE = "STRICT 2D FLAT VECTOR - NO 3D, NO GRADIENTS, NO SHADOWS, NO GLOW, NO TEXTURE."
# render_grade (user directive 2026-07-20): "premium" buys what the image model is FOR —
# rich dimensional rendering code cannot produce; "flat" keeps the old skeletal minimalism.
PREMIUM_LINE = (
    "PREMIUM RENDERING: high-end editorial science illustration, the polish of a Quanta "
    "Magazine or Nature cover commission. Soft studio lighting with gentle global "
    "illumination, real depth, grounded soft shadows under every object, refined materials "
    "(matte ceramic, frosted glass, brushed-metal accents, smooth paper), restrained smooth "
    "gradients WITHIN the declared palette family. Polished and dimensional - never garish: "
    "NO neon, NO lens flares, NO cartoon outlines, NO clutter, NO photorealistic scenes.")
PREMIUM_MATH_VIZ_LINE = (
    "REPRESENTATION: this is a MATHEMATICAL concept. Its underlying geometry must remain "
    "exactly the stated diagram (curves, thresholds, pools of sample dots, grids, junctions) "
    "- but render that structure as a beautiful, dimensional physical object: a curve as a "
    "sculpted arc, a threshold as a real rail or etched line, sample dots as glossy beads, "
    "cells as small trays. The mathematics stays legible at a glance; the craft is in the "
    "rendering. ABSOLUTELY NO unrelated machines, gadgets or scenery - every object IS a "
    "part of the diagram.")
SALIENCY_LINE = {
    "primary": "Composition: give this element ONE dominant focal shape - it is the visual hero of its panel.",
    "secondary": "Composition: keep this element clear but subordinate to the panel's focal element.",
    "ambient": "Composition: keep this element quiet and peripheral, with light visual weight.",
}
LATITUDE = ("Within these constraints, make bold choices in metaphor, silhouette and "
            "micro-detail; avoid default centered symmetry.")

# --- representation typing (user directive 2026-07-19): physical things get physical
# schematics; mathematical abstractions get algorithm-visualization vocabulary, never
# decorative machinery. ------------------------------------------------------
MATH_VIZ_LINE = (
    "REPRESENTATION: this is a MATHEMATICAL concept, not a physical object. Draw it as a clean "
    "conceptual diagram in the visual language of statistics textbooks and algorithm "
    "visualizations - thin axes, curves, threshold lines, point clouds and sample dots, "
    "grids/matrices, minimal flat marks. ABSOLUTELY NO machines, devices, gadgets, vehicles, "
    "furniture, buildings or 3D objects; no decorative machinery of any kind.")
MATH_VIZ_LATITUDE = (
    "Within these constraints, make bold choices in composition and in the shaping of curves, "
    "marks and spacing; avoid default centered symmetry. Every mark must carry meaning - draw "
    "nothing that does not express the concept.")

# --- compile dot-emission layout tuning (P5) -------------------------------
SALIENCY_SIZE = {"primary": 1.8, "secondary": 1.0, "ambient": 0.7}
BASE_NODE_W_IN, BASE_NODE_H_IN = 1.10, 0.70   # secondary baseline; scaled by saliency

# --- assemble asset trim (P3) ----------------------------------------------
TRIM_BORDER_TOL = 12       # max per-channel border tolerance (matches key halo tol)
TRIM_MARGIN_FRAC = 0.02    # keep a 2% margin around detected content

# --- band-grid layout engine (M4, spec §1) ---------------------------------
# A deterministic, pure-Python template solver for the presentation-grade band
# language: open horizontal bands (one per panel), typographic headers, no panel
# boxes, topology-derived in-band arrangement, one dominant hub. Chosen over the
# graphviz path for banded archetypes with <=4 panels (compile picks; layout.prefer
# overrides). All coordinates are 0-100 normalized canvas coords, y down.
BAND_ARCHETYPES = ("hierarchical", "linear-flow", "dual-stream")
BAND_SIDE_MARGIN = 4.0        # % canvas width reserved left/right of every band
# Vertical budget is physical mm (spec §7c): band heights are summed from slot-floor
# needs, not fractions of the canvas, so the "too short -> suggest a taller canvas"
# arithmetic is a single addition — no scale-invariant binary search.
BAND_GAP_MM = 6.0             # physical inter-band rhythm gap (spec §7.2)
BAND_TOP_MARGIN_MM = 4.0      # physical frame margin above the first band's rule
BAND_BOTTOM_MARGIN_MM = 4.0   # physical frame margin below the last band
BAND_HEADER_MM = 7.0          # physical band-header height (faded number + title strip)
BAND_INNER_PAD_MM = 2.5       # padding above and below the content stack within a band
BAND_AMBIENT_STRIP = 0.16     # right margin strip width (frac of band inner width) for ambient
BAND_SLOT_GUTTER = 0.025      # minimum slot gutter = 2.5% of band inner width (spec §1.5)
BAND_SLOT_FILL = 0.88         # asset contain-fit target within its slot (spec §6.2, raised from 0.75)
BAND_SLOT_W_FRAC = 0.72       # secondary-baseline slot width as a fraction of its cell
BAND_SLOT_H_FRAC = 0.80       # secondary-baseline slot height as a fraction of its slot zone
BAND_ROW_LABEL_FRAC = 0.42    # legacy cap; live text zones now reserve only their stack height
SALIENCY_AREA = {"primary": 1.9, "secondary": 1.0, "ambient": 0.55}  # area factors (spec §1.4)
BAND_TINT_OPACITY = 0.025     # faint alternating band tint (navy 2-3%, spec §2)
BAND_NUMBER_OPACITY = 0.30    # header number opacity (navy 30%, spec §2)
BAND_STROKE_FRAC = 0.0035     # legacy connector stroke; superseded by BAND_EDGE_W (spec §6.3)
BAND_TEXT_STAGGER = 0.6       # odd/even adjacent-slot text-stack offset, in lines (spec §2)
BAND_LINE_ADVANCE = 1.35      # stacked-text line advance (shared with graphviz path)
BAND_CANVAS_W_PX = 1600.0     # SVG user-unit width (the "@1600w" reference in spec §2)

# --- M4b/M4c: visual dominance + edge grading + port routing (spec §6-§7) ---
# Per-slot minimum height floors in PHYSICAL mm, by saliency (spec §7.1): print-honest
# values at 183 mm double-column. Being physical (not a fraction of canvas height) makes
# each band's need scale-variant, so raising the canvas actually relieves the floors and
# the "suggest a taller canvas" arithmetic converges in one step. The hero and its
# supports can never be starved to postage stamps by deep layering.
SLOT_FLOOR_MM = {"primary": 34.0, "secondary": 20.0, "ambient": 12.0}
BAND_ASSET_DOMINANCE = 0.65   # >=65% of each band's content height goes to asset (slot) zones
# Three-class connector stroke weights as a fraction of CANVAS WIDTH (spec §6.3):
BAND_EDGE_W = {"lane": 0.0045, "neutral": 0.0030, "diagnostic": 0.0016}
BAND_DIAG_DASH = "6 5"        # diagnostic dash pattern (scaled by stroke width at draw time)
BAND_DIAG_OPACITY = 0.5       # diagnostic edges drawn at 50% opacity (spec §6.3)
BAND_PORT_SPREAD = 0.20       # multi-edge ports fan out over +-20% of the slot width (spec §6.4)
BAND_RIGHT_CHANNEL = 98.5     # x (0-100) of the right-margin ambient routing channel (spec §6.4)

SELFTEST_DIR = str(Path(tempfile.gettempdir()) / "figure_studio_selftest")


# ---------------------------------------------------------------- utilities

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with io.open(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        Path(tmp).replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise


def write_json(path: Path, obj: dict[str, Any]) -> None:
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------- fsg write lock (P1)

FSG_LOCK_STALE_S = 120     # a lease older than this is presumed dead and taken over
FSG_LOCK_POLL_S = 0.2      # retry poll while another writer holds the lease


class fsg_lock:
    """Cross-process/thread write lease around a read-modify-write of ONE
    *.fsg.json. Parallel `gen` on different elements of one figure would each
    read the whole fsg, mutate a node, and write it back whole — the later
    writer silently clobbering the earlier writer's asset pointer. Every fsg
    mutation now serializes through this lease.

    Mirrors research_os.hold_lock: an O_CREAT|O_EXCL sentinel `<fsg>.lock` with
    pid+timestamp inside, a 120s stale takeover, a 0.2s retry poll, and always
    released in __exit__ (finally). The stale takeover guarantees forward
    progress even if a holder dies mid-write."""

    def __init__(self, fsg_file: Path):
        self.path = Path(str(fsg_file) + ".lock")
        self.acquired = False

    def __enter__(self) -> "fsg_lock":
        payload = json.dumps({"pid": os.getpid(), "ts": utc_now()})
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
                    held_at = datetime.fromisoformat(str(holder.get("ts", "")).replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - held_at).total_seconds()
                except (json.JSONDecodeError, ValueError, OSError):
                    age = FSG_LOCK_STALE_S + 1  # unreadable lease -> presume stale
                if age > FSG_LOCK_STALE_S:
                    with contextlib.suppress(OSError):
                        self.path.unlink()
                    continue
                time.sleep(FSG_LOCK_POLL_S)

    def __exit__(self, *exc) -> bool:
        if self.acquired:
            with contextlib.suppress(OSError):
                self.path.unlink()
        return False


def _locked_merge_write(path: Path, mutate) -> dict[str, Any]:
    """Race-safe read-modify-write: under the fsg lease, re-read the freshest
    on-disk copy, apply `mutate(fresh)` in place, then write atomically. Used
    where concurrent writers touch different parts of one fsg (gen/refine node
    pointers, assemble bboxes) so no writer clobbers another's fields."""
    with fsg_lock(path):
        fresh = load_json(path)
        mutate(fresh)
        write_json(path, fresh)
        return fresh


def _apply_gen_updates(fresh: dict[str, Any], updates: dict[str, dict[str, Any]]) -> None:
    """Merge per-element gen.{generation,asset} into the fresh fsg, touching only
    the named nodes (leaves every other node's pointer as found on disk)."""
    by_id = {n.get("id"): n for n in fresh.get("nodes") or []}
    for eid, upd in updates.items():
        node = by_id.get(eid)
        if node is not None:
            node.setdefault("gen", {}).update(upd)


def vp_dir(root: Path) -> Path:
    return root / "research" / "artifacts" / "visual-program"


def assets_dir(root: Path) -> Path:
    return vp_dir(root) / "assets"


def fsg_dir(root: Path) -> Path:
    return vp_dir(root) / "fsg"


def fsg_path(root: Path, fid: str) -> Path:
    return fsg_dir(root) / f"{fid}.fsg.json"


def compile_dir(root: Path, fid: str) -> Path:
    return fsg_dir(root) / fid


def require_fid(fid: str) -> str:
    if not FID_RE.match(fid or ""):
        raise ValueError(f"--figure-id must match F## (two digits): {fid!r}")
    return fid


def parse_s(value: Any) -> int:
    match = S_RE.match(str(value or "S01"))
    return int(match.group(1)) if match else 1


def esc_dot(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def venue_label(style: dict[str, Any]) -> str:
    guide = style.get("venue_guide")
    if isinstance(guide, str) and guide.strip():
        stem = Path(guide).stem.strip()
        if stem:
            return stem.upper() if len(stem) <= 6 else stem
    return "research"


def _article(word: str) -> str:
    """Indefinite article by leading letter (so 'AAAI' -> 'an', 'research' -> 'a')."""
    return "an" if (word or "")[:1].upper() in "AEIOU" else "a"


# ---------------------------------------------------------------- fsg init

def scaffold_fsg(fid: str, message: str, archetype: str, width_mm: float, height_mm: float, column: str) -> dict[str, Any]:
    return {
        "fsg_version": FSG_VERSION,
        "figure": {
            "id": fid, "message": message, "archetype": archetype,
            "canvas": {"width_mm": width_mm, "height_mm": height_mm, "column": column},
            "version": 1, "frozen_sha": None,
        },
        "ontology": {
            "node_types": ["module", "data", "actor", "plot-slot"],
            "edge_types": ["dataflow", "dependency", "contrast", "zoom-in", "stage-flow"],
        },
        "panels": [],
        "nodes": [],
        "edges": [],
        "constraints": [],
        "layout": {"solver": None, "seed": 7, "bboxes": {}},
        "texts": [],
        "style": {
            "language": "flat-vector + bauhaus-reduction + swiss-grid",
            "palette_words": ["deep blue", "warm orange accent", "neutral gray"],
            "palette_hex": {"primary": "#0072B2", "accent": "#E69F00", "neutral": "#666666"},
            "venue_guide": None,
            "anchor_asset": None,
        },
        "compile": {"dirty": [], "hashes": {}, "anchor_spec_version": "S01", "compiled_at": None},
    }


def command_fsg_init(args: argparse.Namespace) -> int:
    root = Path(args.project_root).expanduser().resolve()
    fid = require_fid(args.figure_id)
    if args.archetype not in ARCHETYPES:
        raise ValueError(f"--archetype invalid: {args.archetype}")
    column = args.column or "single"
    width_mm = DOUBLE_MM if column == "double" else SINGLE_MM
    height_mm = DEFAULT_H_MM
    if args.canvas_mm:
        parts = re.split(r"[xX]", args.canvas_mm.strip())
        if len(parts) != 2:
            raise ValueError("--canvas-mm must be WxH, e.g. 183x90")
        width_mm, height_mm = float(parts[0]), float(parts[1])
    path = fsg_path(root, fid)
    if path.exists():
        print(f"refusing to overwrite existing fsg: {path}", file=sys.stderr)
        return 1
    write_json(path, scaffold_fsg(fid, args.message, args.archetype, width_mm, height_mm, column))
    print(f"initialized {path}")
    return 0


# ---------------------------------------------------------------- validate

def validate_fsg(fsg: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(fsg, dict):
        return ["fsg root must be an object"], []
    if fsg.get("fsg_version") != FSG_VERSION:
        errors.append(f"fsg_version must be '{FSG_VERSION}'")
    for key in ("figure", "ontology", "panels", "nodes", "edges", "constraints", "layout", "texts", "style", "compile"):
        if key not in fsg:
            errors.append(f"missing top-level group: {key}")

    figure = fsg.get("figure") or {}
    fid = figure.get("id")
    if not (isinstance(fid, str) and FID_RE.match(fid)):
        errors.append(f"figure.id must match F##: {fid!r}")
    for key in ("message", "archetype", "canvas", "version"):
        if key not in figure:
            errors.append(f"figure.{key} missing")
    if "archetype" in figure and figure.get("archetype") not in ARCHETYPES:
        errors.append(f"figure.archetype invalid: {figure.get('archetype')!r}")

    nodes = fsg.get("nodes") or []
    panels = fsg.get("panels") or []
    edges = fsg.get("edges") or []
    texts = fsg.get("texts") or []

    node_ids = [n.get("id") for n in nodes]
    panel_ids = [p.get("id") for p in panels]
    edge_ids = [e.get("id") for e in edges]
    text_ids = [t.get("id") for t in texts]
    for ids, rx, kind in ((node_ids, EID_RE, "node (E##)"), (panel_ids, PID_RE, "panel (P##)"),
                          (edge_ids, LID_RE, "edge (L##)"), (text_ids, TID_RE, "text (T##)")):
        for i in ids:
            if not (isinstance(i, str) and rx.match(i or "")):
                errors.append(f"id grammar violation for {kind}: {i!r}")

    all_ids = [i for i in (node_ids + panel_ids + edge_ids + text_ids) if isinstance(i, str)]
    for dup in sorted({i for i in all_ids if all_ids.count(i) > 1}):
        errors.append(f"duplicate id: {dup}")

    node_set = {i for i in node_ids if isinstance(i, str)}
    panel_set = {i for i in panel_ids if isinstance(i, str)}
    referenced: set[str] = set()
    primary = 0

    palette = (fsg.get("style") or {}).get("palette_hex") or {}
    for edge in edges:
        for end in ("from", "to"):
            target = edge.get(end)
            if target in node_set:
                referenced.add(target)
            else:
                errors.append(f"edge {edge.get('id')} endpoint {end} missing: {target!r}")
        lane = edge.get("lane")
        if lane is not None and lane not in palette:
            warnings.append(f"edge {edge.get('id')} lane {lane!r} not in style.palette_hex "
                            "(assemble falls back to neutral)")
    for text in texts:
        anchor = text.get("anchor")
        if anchor in node_set or anchor in panel_set or anchor == "E00":
            if anchor in node_set:
                referenced.add(anchor)
        else:
            errors.append(f"text {text.get('id')} anchored to missing element: {anchor!r}")
    for node in nodes:
        inst = node.get("instance_of")
        if inst:
            if inst in node_set:
                referenced.add(inst)
            else:
                errors.append(f"node {node.get('id')} instance_of target missing: {inst!r}")
        if node.get("saliency") == "primary":
            primary += 1
        panel = node.get("panel")
        if panel and panel not in panel_set:
            errors.append(f"node {node.get('id')} panel ref missing: {panel!r}")
    if primary > 1:
        errors.append(f"more than one saliency=primary node (found {primary})")
    for node in nodes:
        nid = node.get("id")
        if nid == "E00":
            continue
        if not node.get("panel") and nid not in referenced:
            errors.append(f"orphan node with no panel and no references: {nid}")

    for pid in sorted(panel_set):
        count = sum(1 for n in nodes if n.get("panel") == pid and n.get("id") != "E00")
        if count > 7:
            warnings.append(f"panel {pid} has {count} nodes (>7 perceptual blocks)")
    elements = [n for n in nodes if n.get("id") != "E00" and not n.get("instance_of")]
    if not 5 <= len(elements) <= 12:
        warnings.append(f"element count {len(elements)} outside 5-12 sweet spot")
    chroma_ct = sum(1 for n in elements if (n.get("gen") or {}).get("text_strategy") == "chroma")
    if elements and chroma_ct > 0.5 * len(elements):
        warnings.append(f"{chroma_ct}/{len(elements)} elements use text_strategy=chroma (>50%); "
                        "chroma is for label plates, not artwork — icon-scale elements should use none")
    no_rep = [n.get("id") for n in elements
              if (n.get("gen") or {}).get("strategy", "raster") == "raster"
              and n.get("representation") not in ("physical-schematic", "math-viz")]
    if no_rep:
        warnings.append("nodes missing representation type (physical-schematic|math-viz): "
                        + ", ".join(no_rep) + " — mathematical concepts must use math-viz, "
                        "not decorative machinery")
    if not (fsg.get("style") or {}).get("venue_guide"):
        warnings.append("style.venue_guide missing")
    return errors, warnings


def print_validate_report(fid: str, fsg: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    nodes = fsg.get("nodes") or []
    elements = [n for n in nodes if n.get("id") != "E00" and not n.get("instance_of")]
    print(f"FSG validate - {fid}")
    print(f"  nodes={len(nodes)} panels={len(fsg.get('panels') or [])} "
          f"edges={len(fsg.get('edges') or [])} texts={len(fsg.get('texts') or [])} "
          f"elements={len(elements)}")
    print(f"HARD ERRORS ({len(errors)}):")
    for item in errors:
        print(f"  - {item}")
    print(f"WARNINGS ({len(warnings)}):")
    for item in warnings:
        print(f"  - {item}")
    print(f"RESULT: {'FAIL' if errors else 'PASS'}")


def command_fsg_validate(args: argparse.Namespace) -> int:
    root = Path(args.project_root).expanduser().resolve()
    fid = require_fid(args.figure_id)
    fsg = load_json(fsg_path(root, fid))
    errors, warnings = validate_fsg(fsg)
    print_validate_report(fid, fsg, errors, warnings)
    return 1 if errors else 0


# ---------------------------------------------------------------- freeze

def figure_canonical_sha(fsg: dict[str, Any]) -> str:
    clone = copy.deepcopy(fsg)
    clone.pop("compile", None)
    if isinstance(clone.get("figure"), dict):
        clone["figure"]["frozen_sha"] = None
    return sha256_text(canonical_bytes(clone).decode("utf-8"))


def freeze_fsg(fsg: dict[str, Any]) -> tuple[str, str]:
    """Return (state, sha). Mutates fsg's figure.frozen_sha / version in place."""
    figure = fsg.setdefault("figure", {})
    new_sha = figure_canonical_sha(fsg)
    old_sha = figure.get("frozen_sha")
    if old_sha is None:
        figure["frozen_sha"] = new_sha
        return "first-freeze", new_sha
    if new_sha != old_sha:
        figure["version"] = int(figure.get("version", 1)) + 1
        bumped = figure_canonical_sha(fsg)
        figure["frozen_sha"] = bumped
        return "bumped", bumped
    return "unchanged", old_sha


def command_fsg_freeze(args: argparse.Namespace) -> int:
    root = Path(args.project_root).expanduser().resolve()
    fid = require_fid(args.figure_id)
    path = fsg_path(root, fid)
    with fsg_lock(path):  # P1: read-freeze-write is one critical section
        fsg = load_json(path)
        errors, _ = validate_fsg(fsg)
        if errors:
            print(f"refusing to freeze {fid}: {len(errors)} hard error(s)", file=sys.stderr)
            for item in errors:
                print(f"  - {item}", file=sys.stderr)
            return 1
        state, sha = freeze_fsg(fsg)
        write_json(path, fsg)
    print(f"freeze {fid}: {state} version={fsg['figure']['version']} frozen_sha={sha[:16]}...")
    return 0


# ---------------------------------------------------------------- compile

def node_hash(node: dict[str, Any], style: dict[str, Any]) -> str:
    gen = node.get("gen") or {}
    payload = [
        node.get("semantic"), node.get("type"), node.get("saliency"),
        node.get("rhythm_group"), node.get("representation"), node.get("viz_idiom"),
        gen.get("strategy"), gen.get("text_strategy"), style,
    ]
    return sha256_text(canonical_bytes(payload).decode("utf-8"))


def anchor_hash(figure: dict[str, Any], style: dict[str, Any], math_viz: bool = False) -> str:
    payload = [figure.get("message"), figure.get("archetype"), style, math_viz]
    return sha256_text(canonical_bytes(payload).decode("utf-8"))


def _spec_step(stored: Any, new_hash: str, sv: str, force: bool) -> tuple[str, bool, bool]:
    """Return (spec_version, do_write, was_bumped) given the stored vs new hash."""
    if stored is None:
        return sv, True, False           # first compile: write at current S
    if stored != new_hash:
        return f"S{parse_s(sv) + 1:02d}", True, True   # inputs changed: bump + write
    return sv, force, False              # unchanged: touch only under --force


def _render_grade(style: dict[str, Any]) -> str:
    return (style.get("render_grade") or "premium").strip().lower()


def _hard_block(text_strategy: str, style: dict[str, Any]) -> str:
    words = ", ".join(style.get("palette_words") or []) or "restrained, high-contrast"
    render_line = FLAT_LINE if _render_grade(style) == "flat" else PREMIUM_LINE
    return ("HARD CONSTRAINTS:\n"
            f"  {TEXT_BLOCK.get(text_strategy, TEXT_BLOCK['none'])}\n"
            f"  {render_line}\n"
            f"  PALETTE: {words}. Generate once, no post-processing.")


def _humanize_language(language: Any) -> str:
    """Turn a 'flat-vector + bauhaus-reduction + swiss-grid' token string into a
    single readable phrase, used exactly once so no token is repeated verbatim."""
    tokens = [tok.strip().replace("-", " ") for tok in str(language or "flat vector").split("+")]
    tokens = [tok for tok in tokens if tok]
    return ", ".join(tokens) or "flat vector"


def _style_para(style: dict[str, Any]) -> str:
    descriptor = _humanize_language(style.get("language"))
    if _render_grade(style) == "flat":
        return (f"Clean, geometric illustration in the spirit of {descriptor}. Restraint is the "
                "aesthetic: abundant negative space, quality through order, dense but not cluttered.")
    return (f"Refined editorial illustration in the spirit of {descriptor}. Order and hierarchy "
            "carry the aesthetic: generous negative space, disciplined palette, rich rendering "
            "on a calm composition - dense but never busy.")


def _save_line(fid: str, eid: str, sv: str) -> str:
    return (f"Save the result as {fid}.{eid}.{sv}.G01.png in the current working directory (copy it "
            "there from wherever the tool saved it), then print the saved absolute path.")


def build_node_prompt(fid: str, node: dict[str, Any], style: dict[str, Any], sv: str) -> str:
    ntype = node.get("type") or "element"
    semantic = (node.get("semantic") or "").strip() or "an unnamed visual element"
    saliency = SALIENCY_LINE.get(node.get("saliency"), SALIENCY_LINE["secondary"])
    if node.get("rhythm_group"):
        saliency += (f" It belongs to rhythm group {node['rhythm_group']}: match the visual "
                     "weight and silhouette of its siblings in that group.")
    venue = venue_label(style)
    representation = node.get("representation") or "physical-schematic"
    viz_idiom = (node.get("viz_idiom") or "").strip()
    sections = [
        f"You are rendering ONE isolated visual element for {_article(venue)} {venue} research figure.",
        (f"This element is a {ntype}: {semantic}. It will be composited by code into a larger "
         "figure; render it ISOLATED, centered on plain white, with generous margins."),
        _hard_block((node.get("gen") or {}).get("text_strategy") or "none", style),
    ]
    if representation == "math-viz":
        sections.append(MATH_VIZ_LINE if _render_grade(style) == "flat" else PREMIUM_MATH_VIZ_LINE)
        if viz_idiom:
            sections.append(f"Render it specifically as: {viz_idiom}.")
        sections += [_style_para(style), saliency, MATH_VIZ_LATITUDE]
    else:
        sections += [_style_para(style), saliency, LATITUDE]
    sections += [
        "Reference the frozen style anchor (E00) for palette and visual language when attached as -i.",
        _save_line(fid, node.get("id"), sv),
    ]
    return "\n\n".join(sections) + "\n"


def build_anchor_prompt(fid: str, figure: dict[str, Any], style: dict[str, Any], sv: str,
                        math_viz: bool = False) -> str:
    archetype = figure.get("archetype") or "linear-flow"
    venue = venue_label(style)
    sections = [
        (f"You are establishing the STYLE ANCHOR frame for {_article(venue)} {venue} research figure - "
         "one reference image that fixes palette, line weight, and visual language for every "
         "element that follows."),
        f"The whole figure must convey, at a glance: {figure.get('message', '').strip()}.",
        f"Overall composition archetype: {archetype} - {ARCHETYPE_DESC.get(archetype, '')}.",
        _hard_block("none", style),
        *([MATH_VIZ_LINE if _render_grade(style) == "flat" else PREMIUM_MATH_VIZ_LINE]
          if math_viz else []),
        _style_para(style),
        (MATH_VIZ_LATITUDE if math_viz else LATITUDE),
        _save_line(fid, "E00", sv),
    ]
    return "\n\n".join(sections) + "\n"


def emit_skeleton_dot(fsg: dict[str, Any]) -> str:
    figure = fsg.get("figure") or {}
    fid = figure.get("id") or "F00"
    archetype = figure.get("archetype") or "linear-flow"
    canvas = figure.get("canvas") or {}
    cw = float(canvas.get("width_mm") or DOUBLE_MM)
    ch = float(canvas.get("height_mm") or DEFAULT_H_MM)
    rankdir = RANKDIR.get(archetype, "LR")
    nodes = fsg.get("nodes") or []
    node_panel = {n.get("id"): n.get("panel") for n in nodes if isinstance(n.get("id"), str)}

    def box_line(node: dict[str, Any], indent: str) -> str:
        # P5: node size is driven by saliency (primary x1.8 / secondary x1.0 /
        # ambient x0.7) so the solver ranks the hero larger; the final bboxes are
        # still whatever dot solves from these input sizes, never hand-written.
        nid = node.get("id")
        factor = SALIENCY_SIZE.get(node.get("saliency"), 1.0)
        w_in = BASE_NODE_W_IN * factor
        h_in = BASE_NODE_H_IN * factor
        parts = [f'label="{esc_dot(nid)}"', f"width={w_in:.2f}", f"height={h_in:.2f}", "fixedsize=true"]
        return f'{indent}"{esc_dot(nid)}" [{", ".join(parts)}];'

    # P5: pull nodes apart (nodesep/ranksep) and derive the drawing size/ratio
    # from the physical canvas so the layout matches the target column geometry.
    canvas_ratio = (ch / cw) if cw else 1.0
    lines = [f"digraph {fid} {{", "  compound=true;", "  splines=ortho;",
             f"  rankdir={rankdir};", "  nodesep=0.6;", '  ranksep="0.8 equally";',
             f'  size="{cw / 25.4:.2f},{ch / 25.4:.2f}";', f"  ratio={canvas_ratio:.4f};",
             "  node [shape=box];", ""]
    placed: set[str] = set()
    panels = sorted(fsg.get("panels") or [], key=lambda p: (p.get("order", 0), str(p.get("id"))))
    for panel in panels:
        pid = panel.get("id")
        lines.append(f"  subgraph cluster_{pid} {{")
        lines.append(f'    label="{esc_dot(panel.get("role", pid))}";')
        for node in sorted((n for n in nodes if n.get("panel") == pid and n.get("id") != "E00"),
                           key=lambda n: str(n.get("id"))):
            lines.append(box_line(node, "    "))
            placed.add(node.get("id"))
        lines.append("  }")
    loose = [n for n in nodes if n.get("id") != "E00" and n.get("id") not in placed]
    for node in sorted(loose, key=lambda n: str(n.get("id"))):
        lines.append(box_line(node, "  "))
    lines.append("")
    for edge in sorted(fsg.get("edges") or [], key=lambda e: str(e.get("id"))):
        frm, to = edge.get("from"), edge.get("to")
        if frm == "E00" or to == "E00":
            continue
        attrs = []
        if edge.get("scope") == "cross_panel":
            if node_panel.get(frm):
                attrs.append(f"ltail=cluster_{node_panel[frm]}")
            if node_panel.get(to):
                attrs.append(f"lhead=cluster_{node_panel[to]}")
        suffix = f' [{", ".join(attrs)}]' if attrs else ""
        lines.append(f'  "{esc_dot(frm)}" -> "{esc_dot(to)}"{suffix};')
    lines.append("}")
    return "\n".join(lines) + "\n"


def solve_bboxes_from_json(dot_json: str) -> dict[str, list[float]]:
    data = json.loads(dot_json)
    bb = str(data.get("bb", "")).split(",")
    if len(bb) != 4:
        return {}
    gx0, gy0, gx1, gy1 = (float(v) for v in bb)
    gw, gh = (gx1 - gx0) or 1.0, (gy1 - gy0) or 1.0
    out: dict[str, list[float]] = {}

    def clamp(v: float) -> float:
        return round(max(0.0, min(100.0, v)), 2)

    for obj in data.get("objects", []):
        name = obj.get("name")
        pos = str(obj.get("pos", "")).split(",")
        if not (isinstance(name, str) and EID_RE.match(name) and len(pos) == 2):
            continue
        try:
            cx, cy = float(pos[0]), float(pos[1])
            w_pt = float(obj.get("width", 0)) * 72.0
            h_pt = float(obj.get("height", 0)) * 72.0
        except (TypeError, ValueError):
            continue
        x0, x1 = cx - w_pt / 2, cx + w_pt / 2
        y0, y1 = cy - h_pt / 2, cy + h_pt / 2
        out[name] = [clamp((x0 - gx0) / gw * 100), clamp((gy1 - y1) / gh * 100),
                     clamp((x1 - gx0) / gw * 100), clamp((gy1 - y0) / gh * 100)]
    return out


def find_dot() -> str | None:
    """Graphviz dot: PATH first, then the Windows default install location."""
    found = shutil.which("dot")
    if found:
        return found
    return DEFAULT_DOT if Path(DEFAULT_DOT).exists() else None


def find_inkscape() -> str | None:
    """Inkscape CLI: PATH first, then the Windows default install location."""
    found = shutil.which("inkscape")
    if found:
        return found
    return DEFAULT_INKSCAPE if Path(DEFAULT_INKSCAPE).exists() else None


def _dot_layout(dot_bin: str, dot_path: Path, out_dir: Path, fsg: dict[str, Any]) -> tuple[bool, int]:
    """Run dot -Tsvg (skeleton.svg) and -Tjson (solved bboxes -> fsg.layout).
    Returns (svg_written, n_bboxes). Shared by compile and assemble."""
    svg_ok, n_bboxes = False, 0
    svg = subprocess.run([dot_bin, "-Tsvg", str(dot_path)], capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    if svg.returncode == 0 and svg.stdout.strip():
        write_text(out_dir / "skeleton.svg", svg.stdout)
        svg_ok = True
    js = subprocess.run([dot_bin, "-Tjson", str(dot_path)], capture_output=True,
                        text=True, encoding="utf-8", errors="replace")
    if js.returncode == 0 and js.stdout.strip():
        bboxes = solve_bboxes_from_json(js.stdout)
        if bboxes:
            layout = fsg.setdefault("layout", {})
            layout["bboxes"] = bboxes
            layout["solver"] = "graphviz-dot"
            n_bboxes = len(bboxes)
    return svg_ok, n_bboxes


# ---------------------------------------------------------------- band-grid layout (M4 §1)

def _choose_solver(figure: dict[str, Any], panels: list[dict[str, Any]], prefer: Any) -> str:
    """Pick the layout solver for compile/assemble. layout.prefer overrides everything;
    otherwise the band-grid template owns the banded archetypes with <=4 panels (spec §1),
    and graphviz-dot keeps every other archetype."""
    if prefer in ("graphviz", "band-grid"):
        return str(prefer)
    archetype = figure.get("archetype")
    if archetype in BAND_ARCHETYPES and len(panels) <= 4:
        return "band-grid"
    return "graphviz"


def _band_in_edges(edges: list[dict[str, Any]], node_ids: list[str]) -> list[tuple[str, str]]:
    ids = set(node_ids)
    return [(e.get("from"), e.get("to")) for e in edges
            if e.get("from") in ids and e.get("to") in ids and e.get("from") != e.get("to")]


def _longest_path_layers(node_ids: list[str], in_edges: list[tuple[str, str]]) -> list[list[str]]:
    """Topology-derived in-band layering (spec §1.1-1.2): longest-path leveling over only
    the in-band edges, then every fan-in target (in-degree >= 2) is sunk onto a dedicated
    layer of its own so it reads as a centered hub. Deterministic — nodes are processed in
    sorted id order and the returned layer list is stable for a fixed input."""
    ids = sorted(node_ids)
    if not ids:
        return []
    idset = set(ids)
    edges = [(f, t) for f, t in in_edges if f in idset and t in idset]
    rank = {n: 0 for n in ids}
    for _ in range(len(ids) + 1):                 # longest-path relaxation (DAG; bounded)
        changed = False
        for f, t in edges:
            if rank[t] < rank[f] + 1:
                rank[t] = rank[f] + 1
                changed = True
        if not changed:
            break
    indeg = {n: 0 for n in ids}
    for _f, t in edges:
        indeg[t] += 1
    hubs = {n for n in ids if indeg[n] >= 2}
    by_rank: dict[int, list[str]] = {}
    for n in ids:
        by_rank.setdefault(rank[n], []).append(n)
    layers: list[list[str]] = []
    for r in sorted(by_rank):
        group = by_rank[r]
        non_hub = [n for n in group if n not in hubs]
        for h in [n for n in group if n in hubs]:
            if non_hub:                            # peers of the hub stay on the upper layer
                layers.append(non_hub)
                non_hub = []
            layers.append([h])                     # each fan-in hub alone, centered (hub mode)
        if non_hub:
            layers.append(non_hub)
    return layers


class BandFloorError(Exception):
    """Raised by compute_band_layout when the summed physical slot-height needs (spec §7.2)
    exceed the fsg's canvas height. Carries suggested_mm = ceil(canvas_needed) — the smallest
    canvas height at which every band's floors fit — so compile can tell the author how to
    fix the fsg instead of silently starving the visuals."""

    def __init__(self, suggested_mm: float, detail: str):
        self.suggested_mm = suggested_mm
        self.detail = detail
        super().__init__(detail)


def _slot_floor_mm(saliency: Any) -> float:
    """Minimum slot height in physical mm, by saliency (spec §7.1)."""
    return SLOT_FLOOR_MM.get(saliency or "secondary", SLOT_FLOOR_MM["secondary"])


def _band_text_reserve_mm(fsg: dict[str, Any]) -> dict[str, float]:
    """Physical height (mm) each node-anchored label stack needs directly below its slot,
    mirroring _bandgrid_text_layout's 1.35-line advance plus one stagger line of headroom.
    Panel-anchored (P##) texts go to the header and reserve nothing here. Returned in mm (not
    a fraction of the canvas) so each band's need is H-independent and the suggested canvas
    height is exact (spec §7.2)."""
    node_ids = {n.get("id") for n in fsg.get("nodes") or []}
    by_anchor: dict[Any, list[dict[str, Any]]] = {}
    for text in fsg.get("texts") or []:
        anchor = text.get("anchor")
        if anchor in node_ids and anchor != "E00":
            by_anchor.setdefault(anchor, []).append(text)
    reserve: dict[str, float] = {}
    for anchor, group in by_anchor.items():
        cyoff, box_bottom, fh0 = 0.0, 0.0, 0.0
        for i, text in enumerate(sorted(group, key=_stack_order_key)):
            fh = float(text.get("size_pt") or 8) * 0.3528       # mm per pt (no canvas division)
            if i == 0:
                cyoff, fh0 = fh * 0.9, fh
            else:
                cyoff += fh * BAND_LINE_ADVANCE
            box_bottom = max(box_bottom, cyoff + fh * 0.35)
        reserve[anchor] = box_bottom + fh0 * BAND_TEXT_STAGGER
    return reserve


def _band_plan_core(fsg: dict[str, Any], height_mm: float) -> dict[str, Any]:
    """One deterministic pass of the band solver. Band heights are summed directly from
    physical slot-floor needs in mm (spec §7.2): per band, need_mm = header + Σ_layer
    max(slot floor over that layer's main nodes) + measured text-strip heights + inner
    padding; inter-band gap is a physical 6 mm. canvas_needed_mm = Σ need + Σ gap + frame
    margins is H-INDEPENDENT, so the too-short remedy is a single addition (no binary
    search). When the canvas is taller than needed the surplus is distributed to bands in
    proportion to their needs and poured entirely into the asset (slot) zones. Horizontal
    placement is byte-identical to the pre-M4c engine — the twin / hub / ambient invariants
    hold; only the vertical budget changed. Never raises — compute_band_layout reads
    canvas_needed_mm and decides."""
    panels = sorted(fsg.get("panels") or [], key=lambda p: (p.get("order", 0), str(p.get("id"))))
    nodes = fsg.get("nodes") or []
    edges = fsg.get("edges") or []
    sal_by_id = {n.get("id"): (n.get("saliency") or "secondary") for n in nodes}
    H = float(height_mm or DEFAULT_H_MM) or DEFAULT_H_MM
    text_reserve = _band_text_reserve_mm(fsg)

    # --- per-band, per-layer physical needs in mm (spec §7.2) ---------------
    panel_nodes: dict[str, list[str]] = {}
    panel_plans: dict[str, list[dict[str, Any]]] = {}
    band_content_mm: list[float] = []
    band_need_mm: list[float] = []
    for panel in panels:
        pid = panel.get("id")
        ids = sorted(n.get("id") for n in nodes if n.get("panel") == pid and n.get("id") != "E00")
        panel_nodes[pid] = ids
        layers = _longest_path_layers(ids, _band_in_edges(edges, ids))
        plans: list[dict[str, Any]] = []
        for layer in layers:
            main = [nid for nid in layer if sal_by_id.get(nid) != "ambient"]
            ambient = [nid for nid in layer if sal_by_id.get(nid) == "ambient"]
            main_floor = max((_slot_floor_mm(sal_by_id.get(nid)) for nid in main), default=0.0)
            main_text = max((text_reserve.get(nid, 0.0) for nid in main), default=0.0)
            amb_need = sum(_slot_floor_mm("ambient") for _ in ambient) \
                + max((text_reserve.get(nid, 0.0) for nid in ambient), default=0.0)
            need = max(main_floor + main_text, amb_need, 0.5)
            plans.append({"main": main, "ambient": ambient, "main_text": main_text, "need": need})
        panel_plans[pid] = plans
        content = sum(p["need"] for p in plans)
        band_content_mm.append(content)
        band_need_mm.append(BAND_HEADER_MM + 2.0 * BAND_INNER_PAD_MM + content)

    n = len(panels)
    canvas_needed = (BAND_TOP_MARGIN_MM + BAND_BOTTOM_MARGIN_MM + sum(band_need_mm)
                     + BAND_GAP_MM * max(0, n - 1))
    surplus = max(0.0, H - canvas_needed)                  # poured entirely into assets (§7.2)
    total_need = sum(band_need_mm) or 1.0

    # --- placement: laid out top-down in mm, normalized to 0-100 at emit ----
    x_left, x_right = BAND_SIDE_MARGIN, 100.0 - BAND_SIDE_MARGIN
    inner_w = x_right - x_left
    gutter = BAND_SLOT_GUTTER * inner_w

    def pct(mm: float) -> float:
        return mm / H * 100.0

    bboxes: dict[str, list[float]] = {}
    slot_cells: dict[str, float] = {}
    bands: list[dict[str, Any]] = []
    dominances: list[float] = []
    y = BAND_TOP_MARGIN_MM
    for idx, panel in enumerate(panels):
        pid = panel.get("id")
        band_extra = surplus * (band_need_mm[idx] / total_need)   # this band's asset surplus
        band_top = y
        header_bot = band_top + BAND_HEADER_MM
        content_top = header_bot + BAND_INNER_PAD_MM
        content_h = band_content_mm[idx] + band_extra
        band_bot = content_top + content_h + BAND_INNER_PAD_MM
        header_bbox = [round(x_left, 2), round(pct(band_top), 2),
                       round(x_right, 2), round(pct(header_bot), 2)]
        band_bbox = [round(x_left, 2), round(pct(band_top), 2),
                     round(x_right, 2), round(pct(band_bot), 2)]

        plans = panel_plans[pid]
        has_ambient = any(sal_by_id.get(nid) == "ambient" for nid in panel_nodes[pid])
        strip_w = BAND_AMBIENT_STRIP * inner_w if has_ambient else 0.0
        main_left, main_right = x_left, x_right - strip_w
        main_w = max(0.5, main_right - main_left)
        content_need = band_content_mm[idx] or 1.0
        asset_sum = 0.0

        row_top = content_top
        for plan in plans:
            layer_extra = band_extra * (plan["need"] / content_need)  # surplus -> asset zone
            row_h = plan["need"] + layer_extra
            text_zone = min(plan["main_text"], row_h * (1.0 - BAND_ASSET_DOMINANCE)) \
                if plan["main"] else 0.0
            slot_zone = max(0.5, row_h - text_zone)
            asset_sum += slot_zone
            main, ambient = plan["main"], plan["ambient"]
            if main:                                       # centered, equal-gutter distribution
                cell_w = main_w / len(main)
                slot_cy = row_top + slot_zone / 2.0
                for ci, nid in enumerate(main):
                    cx = main_left + cell_w * (ci + 0.5)
                    lin = math.sqrt(SALIENCY_AREA.get(sal_by_id.get(nid), 1.0))
                    w = min(BAND_SLOT_W_FRAC * cell_w * lin, max(0.5, cell_w - gutter))
                    bboxes[nid] = [round(cx - w / 2, 2), round(pct(slot_cy - slot_zone / 2), 2),
                                   round(cx + w / 2, 2), round(pct(slot_cy + slot_zone / 2), 2)]
                    slot_cells[nid] = round(cell_w, 2)
            if ambient:                                    # right-margin strip retreat (§1.4/§6.4)
                acell_h = row_h / len(ambient)
                acx = main_right + strip_w / 2.0
                for ai, nid in enumerate(ambient):
                    acy = row_top + acell_h * (ai + 0.5)
                    azone = max(_slot_floor_mm("ambient"), acell_h * 0.82)
                    azone = min(azone, acell_h * 0.94)
                    lin = math.sqrt(SALIENCY_AREA.get("ambient", 0.55))
                    w = min(BAND_SLOT_W_FRAC * strip_w * lin, max(0.5, strip_w - gutter))
                    bboxes[nid] = [round(acx - w / 2, 2), round(pct(acy - azone / 2), 2),
                                   round(acx + w / 2, 2), round(pct(acy + azone / 2), 2)]
                    slot_cells[nid] = round(strip_w, 2)
            row_top += row_h
        dominances.append(asset_sum / content_h if content_h else 1.0)
        bands.append({"panel_id": pid, "band_bbox": band_bbox,
                      "header_bbox": header_bbox, "rule_y": round(pct(band_top), 2)})
        y = band_bot + BAND_GAP_MM

    return {"solver": "band-grid", "bboxes": bboxes, "bands": bands,
            "slot_cells": slot_cells, "canvas_needed_mm": canvas_needed,
            "band_needs_mm": list(zip((p.get("id") for p in panels), band_need_mm)),
            "dominance": min(dominances) if dominances else 1.0}


def compute_band_layout(fsg: dict[str, Any]) -> dict[str, Any]:
    """Deterministic band-grid solver (spec §1 + §6 + §7). One open horizontal band per panel
    (in panel order), band heights summed directly from physical slot-floor needs in mm
    (primary/secondary/ambient), a physical 6 mm inter-band gap, topology-derived in-band
    layering with hub sinking, twin symmetry, ambient nodes retreated into a right margin
    strip, and >=65% of content height kept with the assets. When the summed needs exceed the
    fsg's canvas height it raises BandFloorError carrying suggested_mm = ceil(canvas_needed) —
    one addition, no binary search (spec §7.2) — rather than starving the visuals. Returns
    {solver, bboxes (0-100 slots), bands:[{panel_id, band_bbox, header_bbox, rule_y}],
    slot_cells}."""
    canvas = (fsg.get("figure") or {}).get("canvas") or {}
    H = float(canvas.get("height_mm") or DEFAULT_H_MM) or DEFAULT_H_MM
    plan = _band_plan_core(fsg, H)
    needed = plan["canvas_needed_mm"]
    if H + 1e-6 < needed:                                  # summed slot floors do not fit (§7.2)
        suggested = float(math.ceil(needed))
        fl = SLOT_FLOOR_MM
        per_band = "; ".join(f"{pid} {mm:.0f}mm" for pid, mm in plan["band_needs_mm"])
        detail = (f"bands need {needed:.0f} mm total but canvas height_mm={H:.0f} "
                  f"(slot floors primary {fl['primary']:.0f} / secondary {fl['secondary']:.0f} "
                  f"/ ambient {fl['ambient']:.0f} mm; per band: {per_band})")
        raise BandFloorError(suggested, detail)
    return {"solver": "band-grid", "bboxes": plan["bboxes"], "bands": plan["bands"],
            "slot_cells": plan["slot_cells"]}


def build_checklist(fsg: dict[str, Any]) -> dict[str, Any]:
    figure = fsg.get("figure") or {}
    nodes = fsg.get("nodes") or []
    texts = fsg.get("texts") or []
    per_panel: dict[str, int] = {}
    for node in nodes:
        pid = node.get("panel")
        if pid and node.get("id") != "E00":
            per_panel[pid] = per_panel.get(pid, 0) + 1
    has_chroma = any((n.get("gen") or {}).get("text_strategy") == "chroma" for n in nodes)
    return {
        "figure_id": figure.get("id"),
        "message": figure.get("message"),
        "expected_label_texts": [
            {"id": t.get("id"), "anchor": t.get("anchor"), "content": t.get("content")}
            for t in texts
        ],
        "expected_node_count_per_panel": per_panel,
        "element_count": len([n for n in nodes if n.get("id") != "E00" and not n.get("instance_of")]),
        "palette_hex": (fsg.get("style") or {}).get("palette_hex", {}),
        "density_budget_per_panel": 7,
        "magenta_residue_must_be_zero": has_chroma,
    }


def command_compile(args: argparse.Namespace) -> int:
    root = Path(args.project_root).expanduser().resolve()
    fid = require_fid(args.figure_id)
    path = fsg_path(root, fid)
    with fsg_lock(path):  # P1: the whole compile (read -> prompts/hashes -> write) is atomic
        return _compile_locked(root, fid, path, bool(args.force))


def _compile_locked(root: Path, fid: str, path: Path, force: bool) -> int:
    fsg = load_json(path)
    errors, _ = validate_fsg(fsg)
    if errors:
        print(f"refusing to compile {fid}: {len(errors)} hard error(s)", file=sys.stderr)
        for item in errors:
            print(f"  - {item}", file=sys.stderr)
        return 1

    out_dir = compile_dir(root, fid)
    prompts_dir = out_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    figure = fsg.get("figure") or {}
    style = fsg.get("style") or {}
    compile_block = fsg.setdefault("compile", {})
    hashes = compile_block.setdefault("hashes", {})

    # (a/b) layout solver (spec §1): band-grid template for banded archetypes with <=4
    # panels, else graphviz-dot. layout.prefer overrides. Band-grid is pure Python — no
    # dot is required or run, so a missing Graphviz is NOT a degrade in that mode.
    panels_list = fsg.get("panels") or []
    layout_block = fsg.setdefault("layout", {})
    solver = _choose_solver(figure, panels_list, layout_block.get("prefer"))
    if solver == "band-grid":
        try:
            result = compute_band_layout(fsg)
        except BandFloorError as exc:                     # spec §6.1: never silently starve
            print(f"refusing to compile {fid}: slot-height floors do not fit", file=sys.stderr)
            print(f"  - {exc.detail}", file=sys.stderr)
            print(f"  - raise figure.canvas.height_mm to >= {exc.suggested_mm:.0f} mm "
                  f"(or reduce band depth / element saliency); refusing to starve the visuals",
                  file=sys.stderr)
            return 1
        layout_block["solver"] = result["solver"]
        layout_block["bboxes"] = result["bboxes"]
        layout_block["bands"] = result["bands"]
        layout_block["slot_cells"] = result.get("slot_cells", {})
        print(f"compile {fid}: band-grid layout — {len(result['bands'])} band(s), "
              f"{len(result['bboxes'])} slot(s) (no dot required)")
    else:
        layout_block.pop("bands", None)           # graphviz path carries no bands
        layout_block.pop("slot_cells", None)
        dot_path = out_dir / "skeleton.dot"
        write_text(dot_path, emit_skeleton_dot(fsg))
        print(f"compile {fid}: skeleton.dot written")
        # Same discovery as assemble: PATH first, then the Windows default install location.
        dot_bin = find_dot()
        if dot_bin:
            try:
                svg_ok, n_bboxes = _dot_layout(dot_bin, dot_path, out_dir, fsg)
                if svg_ok:
                    print(f"compile {fid}: skeleton.svg written")
                if n_bboxes:
                    print(f"compile {fid}: solved {n_bboxes} bboxes (layout.solver=graphviz-dot)")
            except Exception as exc:  # noqa: BLE001 - degrade rather than crash
                print(f"!! DEGRADED: dot invocation failed ({exc}); skeleton.svg / bboxes skipped")
        else:
            print("!! DEGRADED: 'dot' (Graphviz) not on PATH -- skeleton.svg and solver bboxes "
                  "SKIPPED. Install Graphviz to enable layout solving. (skeleton.dot, prompts, "
                  "and checklist were still produced.)")

    # (c) per-node prompts + style anchor
    written: list[str] = []
    bumped: list[str] = []
    skipped_instances: list[str] = []
    for node in fsg.get("nodes") or []:
        eid = node.get("id")
        if eid == "E00":
            continue
        if node.get("instance_of"):
            skipped_instances.append(eid)
            continue
        gen = node.setdefault("gen", {})
        new_hash = node_hash(node, style)
        sv, do_write, was_bumped = _spec_step(
            hashes.get(eid), new_hash, gen.get("spec_version") or "S01", force)
        gen["spec_version"] = sv
        hashes[eid] = new_hash
        if was_bumped:
            bumped.append(f"{eid}->{sv}")
        if do_write:
            write_text(prompts_dir / f"{fid}.{eid}.{sv}.md", build_node_prompt(fid, node, style, sv))
            written.append(f"{eid}({sv})")

    # style anchor E00 — inherits math-viz voice when most elements are math-viz
    _rep_els = [n for n in fsg.get("nodes") or []
                if n.get("id") != "E00" and not n.get("instance_of")
                and (n.get("gen") or {}).get("strategy", "raster") == "raster"]
    anchor_math_viz = bool(_rep_els) and sum(
        1 for n in _rep_els if n.get("representation") == "math-viz") * 2 > len(_rep_els)
    anchor_new = anchor_hash(figure, style, anchor_math_viz)
    sv, do_write, was_bumped = _spec_step(
        hashes.get("E00"), anchor_new, compile_block.get("anchor_spec_version") or "S01", force)
    hashes["E00"] = anchor_new
    compile_block["anchor_spec_version"] = sv
    for node in fsg.get("nodes") or []:
        if node.get("id") == "E00":
            node.setdefault("gen", {})["spec_version"] = sv
    if was_bumped:
        bumped.append(f"E00->{sv}")
    if do_write:
        write_text(prompts_dir / f"{fid}.E00.{sv}.md",
                   build_anchor_prompt(fid, figure, style, sv, anchor_math_viz))
        written.append(f"E00({sv})")

    # (d) checklist
    write_json(out_dir / "checklist.json", build_checklist(fsg))

    compile_block["compiled_at"] = utc_now()
    write_json(path, fsg)

    print(f"compile {fid}: prompts written [{' '.join(written) or 'none (all current)'}]")
    if bumped:
        print(f"compile {fid}: spec bumps [{' '.join(bumped)}]")
    if skipped_instances:
        print(f"compile {fid}: instance_of nodes without prompt (ledger note): "
              f"{' '.join(skipped_instances)}")
    print(f"compile {fid}: checklist.json written")
    return 0


# ---------------------------------------------------------------- key (Pillow)

def _pink_mask_bytes(rgb: bytes, count: int) -> bytearray:
    """Wide pink-family key over raw RGB bytes (spec §6). Returns 0/255 mask."""
    mask = bytearray(count)
    for i in range(count):
        base = i * 3
        r, g, b = rgb[base], rgb[base + 1], rgb[base + 2]
        if r > g + KEY_G_MARGIN and b > g + KEY_G_MARGIN and r >= KEY_R_MIN and b >= KEY_B_MIN \
                and abs(r - b) <= KEY_RB_MAX:
            mask[i] = 255
    return mask


def _components(flat: list[int], w: int, h: int) -> list[tuple[int, int, int, int, int]]:
    seen = bytearray(len(flat))
    comps: list[tuple[int, int, int, int, int]] = []
    for start in range(len(flat)):
        if flat[start] == 0 or seen[start]:
            continue
        stack = [start]
        seen[start] = 1
        x0 = x1 = start % w
        y0 = y1 = start // w
        area = 0
        while stack:
            idx = stack.pop()
            area += 1
            cy, cx = divmod(idx, w)
            x0, x1 = min(x0, cx), max(x1, cx)
            y0, y1 = min(y0, cy), max(y1, cy)
            for dy in (-1, 0, 1):
                ny = cy + dy
                if ny < 0 or ny >= h:
                    continue
                base = ny * w
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = cx + dx
                    if 0 <= nx < w:
                        nidx = base + nx
                        if flat[nidx] and not seen[nidx]:
                            seen[nidx] = 1
                            stack.append(nidx)
        comps.append((x0, y0, x1 + 1, y1 + 1, area))
    return comps


def _cluster_zones(comps: list[tuple[int, int, int, int, int]]) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    for x0, y0, x1, y1, _ in sorted(comps, key=lambda c: (c[1], c[0])):
        placed = False
        for zone in zones:
            zx0, zy0, zx1, zy1 = zone["bb"]
            overlap = max(0, min(zy1, y1) - max(zy0, y0))
            min_h = min(zy1 - zy0, y1 - y0) or 1
            if overlap >= 0.4 * min_h and x0 <= zx1 + KEY_ROW_GAP and x1 >= zx0 - KEY_ROW_GAP:
                zone["bb"] = [min(zx0, x0), min(zy0, y0), max(zx1, x1), max(zy1, y1)]
                zone["n"] += 1
                placed = True
                break
        if not placed:
            zones.append({"bb": [x0, y0, x1, y1], "n": 1})
    return sorted(zones, key=lambda z: (z["bb"][1], z["bb"][0]))


def _zone_vband_overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    ay0, ay1, by0, by1 = a["bb"][1], a["bb"][3], b["bb"][1], b["bb"][3]
    overlap = max(0, min(ay1, by1) - max(ay0, by0))
    return overlap / (min(ay1 - ay0, by1 - by0) or 1)


def _zone_hgap(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax0, ax1, bx0, bx1 = a["bb"][0], a["bb"][2], b["bb"][0], b["bb"][2]
    if ax1 < bx0:
        return bx0 - ax1
    if bx1 < ax0:
        return ax0 - bx1
    return 0.0  # horizontally overlapping


def _merge_two_zones(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {"bb": [min(a["bb"][0], b["bb"][0]), min(a["bb"][1], b["bb"][1]),
                   max(a["bb"][2], b["bb"][2]), max(a["bb"][3], b["bb"][3])],
            "n": a["n"] + b["n"]}


def _merge_zones_horizontal(zones: list[dict[str, Any]], merge_gap_px: int | None) -> list[dict[str, Any]]:
    """Merge same-row zones (vertical band overlap >= KEY_VBAND_MIN) whose horizontal
    gap is below the threshold. Default threshold is KEY_HGAP_FRAC * max width of the
    pair; an explicit --merge-gap-px overrides it with an absolute pixel gap."""
    zones = [dict(z) for z in zones]
    changed = True
    while changed and len(zones) > 1:
        changed = False
        for i in range(len(zones)):
            for j in range(i + 1, len(zones)):
                a, b = zones[i], zones[j]
                if _zone_vband_overlap(a, b) < KEY_VBAND_MIN:
                    continue
                if merge_gap_px is not None:
                    threshold = float(merge_gap_px)
                else:
                    threshold = KEY_HGAP_FRAC * max(a["bb"][2] - a["bb"][0], b["bb"][2] - b["bb"][0])
                if _zone_hgap(a, b) < threshold:
                    zones[i] = _merge_two_zones(a, b)
                    zones.pop(j)
                    changed = True
                    break
            if changed:
                break
    return sorted(zones, key=lambda z: (z["bb"][1], z["bb"][0]))


def _merge_zones_to_expect(zones: list[dict[str, Any]], expect: int) -> list[dict[str, Any]]:
    """Greedily merge the nearest same-row pair until <= expect zones remain, or no
    legal (same-row) merge is left."""
    zones = [dict(z) for z in zones]
    while len(zones) > expect:
        best = None
        for i in range(len(zones)):
            for j in range(i + 1, len(zones)):
                if _zone_vband_overlap(zones[i], zones[j]) >= KEY_VBAND_MIN:
                    gap = _zone_hgap(zones[i], zones[j])
                    if best is None or gap < best[0]:
                        best = (gap, i, j)
        if best is None:
            break
        _, i, j = best
        zones[i] = _merge_two_zones(zones[i], zones[j])
        zones.pop(j)
    return sorted(zones, key=lambda z: (z["bb"][1], z["bb"][0]))


def _key_core(img: Any, merge_gap_px: int | None = None, expect: int | None = None) -> dict[str, Any]:
    """Shared magenta key: returns cleaned image, dilated mask, label zones, residue.
    Used by both `key` (CLI) and `assemble` (in-process chroma cleanup)."""
    from PIL import Image, ImageFilter
    w, h = img.size
    mask = _pink_mask_bytes(img.tobytes(), w * h)
    dil = Image.frombytes("L", (w, h), bytes(mask)).filter(ImageFilter.MaxFilter(KEY_DILATE))
    comps = _components(list(dil.tobytes()), w, h)
    big = [c for c in comps if c[4] >= KEY_MIN_AREA]
    zones = _cluster_zones(big)
    zones = _merge_zones_horizontal(zones, merge_gap_px)
    if expect is not None and len(zones) > expect:
        zones = _merge_zones_to_expect(zones, expect)
    cleaned = Image.composite(Image.new("RGB", (w, h), (255, 255, 255)), img, dil)
    residue = sum(_pink_mask_bytes(cleaned.tobytes(), w * h)) // 255
    return {"cleaned": cleaned, "mask": dil, "zones": zones, "residue": residue,
            "components_total": len(comps), "components_big": len(big), "dims": [w, h]}


def command_key(args: argparse.Namespace) -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is required for `key`. Install it with:  py -m pip install Pillow\n"
              "(auto-install is intentionally not performed.)", file=sys.stderr)
        return 3

    src = Path(args.in_png).expanduser().resolve()
    img = Image.open(src).convert("RGB")
    w, h = img.size
    result = _key_core(img, merge_gap_px=getattr(args, "merge_gap_px", None),
                       expect=getattr(args, "expect", None))
    cleaned, dil, zones, residue = (result["cleaned"], result["mask"],
                                    result["zones"], result["residue"])
    Path(args.out_clean).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_mask).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    cleaned.save(str(Path(args.out_clean).expanduser().resolve()))
    dil.save(str(Path(args.out_mask).expanduser().resolve()))

    report = {
        "file": str(src),
        "dims": [w, h],
        "key": {"r_min": KEY_R_MIN, "b_min": KEY_B_MIN, "rb_max_diff": KEY_RB_MAX,
                "g_margin": KEY_G_MARGIN, "dilate_px": 2, "connectivity": 8,
                "merge_gap_px": getattr(args, "merge_gap_px", None),
                "expect": getattr(args, "expect", None)},
        "components_total": result["components_total"],
        "components_ge_min_area": result["components_big"],
        "label_zones": len(zones),
        "zones": [{"bbox": z["bb"], "w": z["bb"][2] - z["bb"][0],
                   "h": z["bb"][3] - z["bb"][1], "glyphs": z["n"]} for z in zones],
        "residue_after_clean_px": residue,
    }
    if args.report:
        write_json(Path(args.report).expanduser().resolve(), report)
    print(f"key {src.name}: dims={w}x{h} components={result['components_total']} "
          f"label_zones={len(zones)} residue_px={residue}")
    for zone in report["zones"]:
        print(f"  zone bbox={zone['bbox']} glyphs={zone['glyphs']}")
    return 0


# ---------------------------------------------------------------- ledger

def command_ledger(args: argparse.Namespace) -> int:
    root = Path(args.project_root).expanduser().resolve()
    directory = fsg_dir(root)
    if args.figure_id:
        require_fid(args.figure_id)
        files = [fsg_path(root, args.figure_id)]
    else:
        files = sorted(directory.glob("*.fsg.json")) if directory.exists() else []

    lines = ["# Figure Ledger", "", f"_Generated {utc_now()}; {len(files)} figure(s)._", ""]
    for path in files:
        if not path.exists():
            continue
        fsg = load_json(path)
        figure = fsg.get("figure") or {}
        fid = figure.get("id") or path.stem.split(".")[0]
        frozen = "yes" if figure.get("frozen_sha") else "no"
        lines += [
            f"## {fid}",
            "",
            f"- **Message**: {figure.get('message', '')}",
            f"- **Archetype**: {figure.get('archetype', '')}  |  **Version**: "
            f"{figure.get('version', '')}  |  **Frozen**: {frozen}",
            "",
            "| id | type | semantic | strategy | text | S | G | asset | instance_of |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        cdir = compile_dir(root, fid)
        for node in sorted(fsg.get("nodes") or [], key=lambda n: str(n.get("id"))):
            gen = node.get("gen") or {}
            semantic = (node.get("semantic") or "").replace("|", "/").replace("\n", " ")
            if len(semantic) > 60:
                semantic = semantic[:57] + "..."
            asset = gen.get("asset")
            asset_cell = "-"
            if asset:
                asset_cell = "yes" if (vp_dir(root) / asset).exists() else "(planned)"
            lines.append(
                f"| {node.get('id')} | {node.get('type', '')} | {semantic} | "
                f"{gen.get('strategy', '')} | {gen.get('text_strategy', '')} | "
                f"{gen.get('spec_version', '')} | {gen.get('generation', '')} | "
                f"{asset_cell} | {node.get('instance_of') or '-'} |"
            )
        edges = sorted(fsg.get("edges") or [], key=lambda e: str(e.get("id")))
        lines += ["", f"**Edges ({len(edges)})**:"]
        for edge in edges:
            scope = f" ({edge.get('scope')})" if edge.get("scope") else ""
            lines.append(f"- {edge.get('id')} {edge.get('type', '')}: "
                         f"{edge.get('from')} -> {edge.get('to')}{scope}")
        adir = assets_dir(root)
        sidecars = sorted(adir.glob(f"{fid}.*.png.json")) if adir.exists() else []
        lines += ["", f"**Generation sidecars ({len(sidecars)})**:"]
        for sidecar in sidecars:
            lines.append(f"- {sidecar.name}")
        lines.append("")

    out_path = directory / "FIGURES.md"
    write_text(out_path, "\n".join(lines) + "\n")
    print(f"ledger: wrote {out_path} ({len(files)} figure(s))")
    return 0


# ---------------------------------------------------------------- driver (gen/refine)

def _plugin_scripts_dir() -> Path:
    # figure_studio.py lives at <plugin>/skills/research-artifacts/scripts/figure_studio.py
    return Path(__file__).resolve().parents[3] / "scripts"


def _codex_env() -> dict[str, str]:
    """Copy os.environ and strip the CLAUDE* vars (D_image2_route.md: their presence
    makes codex exec hang / misbehave in headless mode)."""
    env = dict(os.environ)
    for key in CODEX_STRIP_ENV:
        env.pop(key, None)
    return env


def find_codex() -> str | None:
    """Resolve a launchable codex CLI. On Windows a bare "codex" is an npm shim that
    CreateProcess cannot exec (WinError 2), and shutil.which("codex") may return the
    extensionless bash dispatcher; prefer the vendored native codex.exe, then the .cmd
    shim (runnable when given as a full path), then the POSIX binary."""
    if sys.platform == "win32":
        found = shutil.which("codex.exe")
        if found:
            return found
        npm_root = Path(os.environ.get("APPDATA", "")) / "npm"
        for exe in npm_root.glob(
                "node_modules/@openai/codex/node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe"):
            return str(exe)
        return shutil.which("codex.cmd")
    return shutil.which("codex")


def _codex_gen_args(workdir: Path, prompt_text: str, refine: bool = False,
                    images: list[Path] | None = None) -> list[str]:
    """Plain arg list (no shell) for `codex exec`, per D_image2_route.md.
    gen = low reasoning (mechanical messenger); refine = default reasoning (in-loop critic)."""
    codex_bin = find_codex()
    if not codex_bin:
        raise FileNotFoundError(
            "codex CLI not found (tried codex.exe on PATH, the npm vendored binary, "
            "and codex.cmd) — install/authenticate the codex CLI to use gen/refine")
    args = [codex_bin, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
            "--enable", "image_generation"]
    if not refine:
        args += ["-c", "model_reasoning_effort=low"]
    args += ["--color", "never", "-C", str(workdir), "-o", str(workdir / "last_msg.txt")]
    for img in images or []:
        # --image is variadic (`-i <FILE>...`): the space form would swallow the trailing
        # prompt positional as another file path. The = binding takes exactly one value.
        args.append(f"--image={img}")
    args.append(prompt_text)
    return args


def _locate_generated_png(workdir: Path, since_ts: float) -> Path | None:
    """The workspace copy first (prompt tells codex to copy into cwd); else the newest
    PNG under ~/.codex/generated_images/ (the tool's own output dir)."""
    cands = [p for p in workdir.glob("*.png") if p.stat().st_mtime >= since_ts - 1]
    if cands:
        return max(cands, key=lambda p: p.stat().st_mtime)
    genroot = Path.home() / ".codex" / "generated_images"
    if genroot.exists():
        pngs = [p for p in genroot.rglob("*.png") if p.stat().st_mtime >= since_ts - 1]
        if pngs:
            return max(pngs, key=lambda p: p.stat().st_mtime)
    return None


def _next_generation(adir: Path, fid: str, eid: str, sv: str) -> str:
    """Next G number for (figure, element, spec) — increments over existing files, never reuses."""
    best = 0
    for path in adir.glob(f"{fid}.{eid}.{sv}.G*.png"):
        match = re.search(r"\.G(\d+)\.png$", path.name)
        if match:
            best = max(best, int(match.group(1)))
    return f"G{best + 1:02d}"


def _mock_render(path: Path, node: dict[str, Any], style: dict[str, Any]) -> None:
    """Deterministic in-code PNG (mock mode) — flat shapes; solid magenta bars for chroma
    nodes. Exercises the identical copy/sidecar/fsg post-run path without spending codex."""
    from PIL import Image, ImageDraw
    w, h = 480, 360
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    pal = style.get("palette_hex") or {}

    def rgb(name: str, default: str) -> tuple[int, int, int]:
        val = str(pal.get(name, default)).lstrip("#")
        try:
            return (int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16))
        except (ValueError, IndexError):
            return (100, 100, 100)

    primary, accent, neutral = rgb("primary", "0072B2"), rgb("accent", "E69F00"), rgb("neutral", "666666")
    draw.rectangle([30, 30, w - 30, h - 30], outline=neutral, width=3)
    draw.rectangle([70, 90, 220, 210], fill=primary)
    draw.ellipse([w - 230, 110, w - 80, 250], fill=accent)
    if (node.get("gen") or {}).get("text_strategy") == "chroma":
        draw.rectangle([90, 255, 230, 295], fill=(255, 0, 255))
        draw.rectangle([260, 255, 400, 295], fill=(255, 0, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path))


def _finalize_asset(root: Path, fid: str, node: dict[str, Any], sv: str, src_png: Path,
                    kind: str, prompt_file: Path, prompt_text: str, figure: dict[str, Any],
                    driver_args: list[str], wall_s: float, exit_code: int, mock: bool,
                    book: dict[str, Any], verdict: str | None = None,
                    ref_anchor: str | None = None) -> tuple[str, str]:
    """Copy the produced PNG to the shared asset pool at the next G, write its JSON sidecar,
    and update the node's gen.generation / gen.asset. Never overwrites."""
    adir = assets_dir(root)
    adir.mkdir(parents=True, exist_ok=True)
    eid = node.get("id")
    generation = _next_generation(adir, fid, eid, sv)
    fname = f"{fid}.{eid}.{sv}.{generation}.png"
    shutil.copyfile(str(src_png), str(adir / fname))
    sidecar = {
        "asset": f"assets/{fname}",
        "figure_id": fid, "element_id": eid, "spec_version": sv, "generation": generation,
        "kind": kind,
        "prompt_path": str(prompt_file),
        "prompt_sha256": sha256_text(prompt_text),
        "fsg_version": figure.get("version"),
        "fsg_frozen_sha": figure.get("frozen_sha"),
        "driver": {"args": driver_args, "mock": bool(mock),
                   "reasoning_effort": "high" if kind == "refine" else "low",
                   "ref_anchor": ref_anchor},
        "wall_clock_s": wall_s,
        "exit_code": exit_code,
        "source_path": str(src_png),
        "bookkeeping": book,
        "created_at": utc_now(),
    }
    if verdict is not None:
        sidecar["verdict"] = verdict
    write_json(adir / f"{fname}.json", sidecar)
    gen = node.setdefault("gen", {})
    gen["generation"] = generation
    gen["asset"] = f"assets/{fname}"
    return fname, generation


# ---------------------------------------------------------------- bookkeeping (collab + events)

def _run_pytool(script: str, argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(_plugin_scripts_dir() / script), *argv],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def _book_batch(root: Path, title: str, spec: str) -> dict[str, Any]:
    """Book (post + claim) a K task around a generation batch. Best-effort: any failure
    returns an UNBOOKED marker so the caller can warn-and-continue — never lose a generation."""
    control = root / ".research-os"
    if not control.exists():
        return {"mode": "standalone"}
    try:
        posted = _run_pytool("collab.py", ["post", str(root), "--to", "any", "--lane", "direct",
                                           "--title", title, "--spec", spec])
        if posted.returncode != 0:
            return {"mode": "research-os", "status": "UNBOOKED",
                    "warning": f"collab post rc={posted.returncode}: {(posted.stderr or '').strip()[:200]}"}
        kid = None
        try:
            kid = json.loads(posted.stdout).get("id")
        except (json.JSONDecodeError, AttributeError):
            kid = None
        if not kid:
            return {"mode": "research-os", "status": "UNBOOKED", "warning": "collab post returned no K id"}
        claimed = _run_pytool("collab.py", ["claim", str(root), "--id", kid])
        if claimed.returncode != 0:
            return {"mode": "research-os", "status": "UNBOOKED", "k_task": kid,
                    "warning": f"collab claim rc={claimed.returncode}: {(claimed.stderr or '').strip()[:200]}"}
        return {"mode": "research-os", "status": "claimed", "k_task": kid}
    except OSError as exc:
        return {"mode": "research-os", "status": "UNBOOKED", "warning": f"collab launch failed: {exc}"}


def _return_batch(root: Path, book: dict[str, Any], produced: list[str],
                  rel_artifacts: list[str], event_summary: str,
                  failures: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """Close the K task (collab return, digest = asset list <=30 lines) and append one
    non-reserved research_os `figure_gen` event. Best-effort; assets are already safe.
    P7: a batch that produced NOTHING is closed as `--failed` (with a bounded error
    digest) instead of a done-return that collab would reject for having no artifact —
    which is exactly what used to leave the K hanging claimed."""
    if book.get("mode") != "research-os" or book.get("status") != "claimed":
        return book
    if not produced:
        return _fail_batch(root, book, failures or [("batch", "no assets produced")])
    book = dict(book)
    digest = "figure_gen assets (" + str(len(produced)) + "):\n" + "\n".join(produced[:28])
    try:
        ret = ["return", str(root), "--id", book["k_task"], "--digest", digest]
        for rel in rel_artifacts:
            ret += ["--artifact", rel]
        rr = _run_pytool("collab.py", ret)
        book["return_rc"] = rr.returncode
        if rr.returncode != 0:
            book["warning"] = f"collab return rc={rr.returncode}: {(rr.stderr or '').strip()[:200]}"
        ev = ["event", str(root), "--type", "figure_gen", "--summary", event_summary]
        for rel in rel_artifacts:
            ev += ["--artifact", rel]
        er = _run_pytool("research_os.py", ev)
        book["event_rc"] = er.returncode
        book["status"] = "returned" if rr.returncode == 0 else "return-failed"
    except OSError as exc:
        book["status"] = "return-failed"
        book["warning"] = f"bookkeeping return failed: {exc}"
    return book


def _fail_batch(root: Path, book: dict[str, Any], failures: list[tuple[str, str]]) -> dict[str, Any]:
    """P7: return a claimed K as FAILED with a bounded error digest so a generation
    batch that produced nothing does not hang the task claimed. Best-effort — a
    bookkeeping hiccup here is a loud warning, never fatal (the assets/sidecars on
    disk are the source of truth)."""
    if book.get("mode") != "research-os" or book.get("status") != "claimed":
        return book
    book = dict(book)
    lines = [f"{eid}: {reason}" for eid, reason in failures][:20]
    digest = "figure_gen FAILED (" + str(len(failures)) + "):\n" + "\n".join(lines)
    try:
        rr = _run_pytool("collab.py", ["return", str(root), "--id", book["k_task"],
                                       "--failed", "--digest", digest])
        book["return_rc"] = rr.returncode
        if rr.returncode == 0:
            book["status"] = "failed"
        else:
            book["status"] = "return-failed"
            book["warning"] = f"collab fail-return rc={rr.returncode}: {(rr.stderr or '').strip()[:200]}"
    except OSError as exc:
        book["status"] = "return-failed"
        book["warning"] = f"bookkeeping fail-return failed: {exc}"
    return book


def _write_failed_sidecar(root: Path, fid: str, node: dict[str, Any], sv: str, prompt_file: Path,
                          prompt_text: str, figure: dict[str, Any], driver_args: list[str],
                          reason: str, mock: bool, ref_anchor: str | None) -> str:
    """P7: record a failed generation next to where its asset would have landed —
    `<FID>.<EID>.<S##>.<G##>.png.failed.json` at the next free G. Never touches a real
    asset; the sidecar is the durable evidence that a booked generation failed."""
    adir = assets_dir(root)
    adir.mkdir(parents=True, exist_ok=True)
    eid = node.get("id")
    generation = _next_generation(adir, fid, eid, sv)
    fname = f"{fid}.{eid}.{sv}.{generation}.png"
    write_json(adir / f"{fname}.failed.json", {
        "asset_would_be": f"assets/{fname}",
        "figure_id": fid, "element_id": eid, "spec_version": sv, "generation": generation,
        "status": "failed", "reason": reason,
        "prompt_path": str(prompt_file), "prompt_sha256": sha256_text(prompt_text),
        "fsg_version": figure.get("version"), "fsg_frozen_sha": figure.get("frozen_sha"),
        "driver": {"args": driver_args, "mock": bool(mock), "ref_anchor": ref_anchor},
        "created_at": utc_now(),
    })
    return fname


def _announce_book(book: dict[str, Any], fid: str, cmd: str) -> None:
    mode = book.get("mode")
    if mode == "standalone":
        print(f"{cmd} {fid}: standalone mode - bookkeeping skipped (.research-os absent)")
    elif book.get("status") == "UNBOOKED":
        print(f"!! UNBOOKED {cmd} {fid}: {book.get('warning')} -- continuing; the generation is NOT lost")
    elif book.get("k_task"):
        print(f"{cmd} {fid}: booked {book['k_task']} (research-os)")


def _refresh_ledger(root: Path, fid: str) -> None:
    with contextlib.suppress(Exception):
        with contextlib.redirect_stdout(io.StringIO()):
            command_ledger(argparse.Namespace(project_root=str(root), figure_id=fid))


def _gen_reconcile(root: Path, fid: str, path: Path) -> int:
    """P2: repair every raster node's gen.generation/gen.asset from disk truth in the
    shared asset pool. For each raster (non-instance) node, find the highest G of
    `<FID>.<EID>.<S##>.<G##>.png` at the node's CURRENT spec_version that actually
    exists on disk and point the node at it. Standalone — no codex, no bookkeeping."""
    adir = assets_dir(root)
    repairs: list[tuple[Any, Any, str]] = []

    def _mutate(fresh: dict[str, Any]) -> None:
        for node in fresh.get("nodes") or []:
            eid = node.get("id")
            gen = node.get("gen") or {}
            if node.get("instance_of") or (gen.get("strategy") or "raster") != "raster":
                continue
            sv = gen.get("spec_version") or "S01"
            best_g, best_name = 0, None
            if adir.exists():
                for p in adir.glob(f"{fid}.{eid}.{sv}.G*.png"):
                    match = re.search(r"\.G(\d+)\.png$", p.name)
                    if match and p.exists() and int(match.group(1)) > best_g:
                        best_g, best_name = int(match.group(1)), p.name
            if best_name is None:
                continue  # nothing on disk for this node — leave its pointer untouched
            new_gen, new_asset = f"G{best_g:02d}", f"assets/{best_name}"
            if gen.get("generation") != new_gen or gen.get("asset") != new_asset:
                repairs.append((eid, gen.get("asset"), new_asset))
                node.setdefault("gen", {}).update({"generation": new_gen, "asset": new_asset})

    _locked_merge_write(path, _mutate)
    print(f"gen {fid}: reconcile — {len(repairs)} pointer(s) repaired from disk truth")
    for eid, old, new in repairs:
        print(f"  {eid}: {old} -> {new}")
    return 0


def command_gen(args: argparse.Namespace) -> int:
    root = Path(args.project_root).expanduser().resolve()
    fid = require_fid(args.figure_id)
    path = fsg_path(root, fid)
    if getattr(args, "reconcile", False):
        return _gen_reconcile(root, fid, path)  # P2: standalone pointer repair from disk
    fsg = load_json(path)
    figure = fsg.get("figure") or {}
    style = fsg.get("style") or {}
    cdir = compile_dir(root, fid)
    prompts_dir = cdir / "prompts"
    adir = assets_dir(root)
    adir.mkdir(parents=True, exist_ok=True)
    mock = os.environ.get("AR_FS_MOCK_GEN") == "1"
    mock_fail = os.environ.get("AR_FS_MOCK_FAIL") == "1"  # P7 test hook: force the failure path
    if not args.element and not args.all_pending:
        print("gen: pass --element E##, --all-pending, or --reconcile", file=sys.stderr)
        return 2
    timeout_s = int(getattr(args, "timeout_s", None) or 600)

    # P8: default-ON style anchor — attach E00's asset via -i when it exists on disk.
    want_anchor = getattr(args, "ref_anchor", None)  # None=auto, True/False=explicit
    e00 = next((n for n in fsg.get("nodes") or [] if n.get("id") == "E00"), None)
    anchor_rel = (e00.get("gen") or {}).get("asset") if e00 else None
    anchor_png = (vp_dir(root) / anchor_rel) if anchor_rel else None
    anchor_ok = bool(anchor_png and anchor_png.exists())
    use_anchor = anchor_ok if want_anchor is None else (bool(want_anchor) and anchor_ok)
    if want_anchor and not anchor_ok:
        print(f"gen {fid}: --ref-anchor requested but E00 has no asset on disk; "
              "generating without anchor", file=sys.stderr)

    targets: list[tuple[dict[str, Any], str, str, Path]] = []
    for node in fsg.get("nodes") or []:
        eid = node.get("id")
        if node.get("instance_of"):
            continue
        gen = node.get("gen") or {}
        if (gen.get("strategy") or "raster") in ("plot", "reuse"):
            continue  # data plots -> matplotlib, reuse -> instance_of; not image-gen targets
        sv = gen.get("spec_version") or "S01"
        prompt_file = prompts_dir / f"{fid}.{eid}.{sv}.md"
        if not prompt_file.exists():
            continue
        if args.element:
            if eid == args.element:
                targets.append((node, eid, sv, prompt_file))
        elif not any(adir.glob(f"{fid}.{eid}.{sv}.G*.png")):
            targets.append((node, eid, sv, prompt_file))

    if args.element and not targets:
        print(f"gen {fid}: --element {args.element} has no image-gen prompt at its current S "
              "(instance/plot nodes are not generated)", file=sys.stderr)
        return 2
    if not targets:
        print(f"gen {fid}: nothing to generate (every target already has an asset at its current S)")
        return 0

    book = _book_batch(root, f"figure {fid} gen ({len(targets)} element(s))",
                       f"Generate {len(targets)} isolated image-gen element(s) for figure {fid} via the "
                       "codex-exec gpt-image-2 driver (low reasoning, generate once, no post-processing).")
    _announce_book(book, fid, "gen")

    produced, rel_artifacts, gen_updates, failures = [], [], {}, []
    for node, eid, sv, prompt_file in targets:
        workdir = cdir / "gen_work" / eid
        workdir.mkdir(parents=True, exist_ok=True)
        prompt_text = prompt_file.read_text(encoding="utf-8")
        this_ref = anchor_rel if (use_anchor and eid != "E00") else None  # P8: never anchor E00 to itself
        images = [anchor_png] if this_ref else None
        driver_args = _codex_gen_args(workdir, prompt_text, refine=False,
                                      images=(None if mock else images))  # mock records ref, needs no -i
        started = time.time()
        if mock:
            if mock_fail:  # P7: exercise the failure/backfill path deterministically
                reason = "mock forced failure (nonzero exit, no PNG)"
                _write_failed_sidecar(root, fid, node, sv, prompt_file, prompt_text, figure,
                                      driver_args, reason, mock=True, ref_anchor=this_ref)
                failures.append((eid, reason))
                print(f"!! gen {fid}.{eid}: {reason}", file=sys.stderr)
                continue
            src = workdir / f"{fid}.{eid}.{sv}.mock.png"
            _mock_render(src, node, style)
            exit_code = 0
        else:
            try:
                proc = subprocess.run(driver_args, env=_codex_env(), stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=timeout_s)
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                reason = f"codex timed out after {timeout_s}s"
                _write_failed_sidecar(root, fid, node, sv, prompt_file, prompt_text, figure,
                                      driver_args, reason, mock=False, ref_anchor=this_ref)
                failures.append((eid, reason))
                print(f"!! gen {fid}.{eid}: {reason}", file=sys.stderr)
                continue
            if exit_code != 0:
                tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
                reason = f"codex exit {exit_code}: {tail[:600] or 'no stderr captured'}"
                _write_failed_sidecar(root, fid, node, sv, prompt_file, prompt_text, figure,
                                      driver_args, reason, mock=False, ref_anchor=this_ref)
                failures.append((eid, reason))
                print(f"!! gen {fid}.{eid}: {reason}", file=sys.stderr)
                continue
            src = _locate_generated_png(workdir, started)
            if src is None:
                reason = f"no PNG produced (codex exit {exit_code})"
                _write_failed_sidecar(root, fid, node, sv, prompt_file, prompt_text, figure,
                                      driver_args, reason, mock=False, ref_anchor=this_ref)
                failures.append((eid, reason))
                print(f"!! gen {fid}.{eid}: {reason}", file=sys.stderr)
                continue
        wall = round(time.time() - started, 3)
        fname, generation = _finalize_asset(root, fid, node, sv, src, "gen", prompt_file, prompt_text,
                                             figure, driver_args, wall, exit_code, mock, book,
                                             ref_anchor=this_ref)
        gen_updates[eid] = {"generation": node["gen"]["generation"], "asset": node["gen"]["asset"]}
        produced.append(fname)
        rel_artifacts.append(f"research/artifacts/visual-program/assets/{fname}")
        anchor_note = f", anchor {this_ref}" if this_ref else ""
        print(f"gen {fid}: {eid} {sv} -> {fname}  "
              f"({wall}s, exit {exit_code}{', mock' if mock else ''}{anchor_note})")

    if gen_updates:  # P1: race-safe merge — re-read fresh, touch only our nodes' pointers
        _locked_merge_write(path, lambda fresh: _apply_gen_updates(fresh, gen_updates))
    book = _return_batch(root, book, produced, rel_artifacts,
                         f"gen produced {len(produced)} asset(s) for {fid}", failures=failures)
    if book.get("status") == "return-failed":
        print(f"!! {fid}: bookkeeping return degraded: {book.get('warning')} (assets are safe on disk)")
    _refresh_ledger(root, fid)
    print(f"gen {fid}: {len(produced)} asset(s) generated"
          + (f", {len(failures)} failed" if failures else ""))
    return 0


# ---------------------------------------------------------------- refine (bounded critic)

def _checklist_criteria(checklist: dict[str, Any], node: dict[str, Any]) -> str:
    eid = node.get("id")
    relevant: dict[str, Any] = {
        "palette_hex": checklist.get("palette_hex"),
        "density_budget_per_panel": checklist.get("density_budget_per_panel"),
        "expected_label_texts_for_this_element": [
            t for t in (checklist.get("expected_label_texts") or []) if t.get("anchor") == eid],
    }
    if (node.get("gen") or {}).get("text_strategy") == "chroma":
        relevant["magenta_residue_must_be_zero"] = True
        relevant["magenta_placeholder_rule"] = (
            "every label position is one solid flat magenta bar; magenta appears nowhere else")
    return json.dumps(relevant, ensure_ascii=False, indent=2)


def _refine_prompt(base_prompt: str, criteria: str) -> str:
    return (base_prompt.strip()
            + "\n\n=== REVIEW CRITERIA (verbatim from checklist.json) ===\n" + criteria
            + "\n\n=== BOUNDED CRITIC INSTRUCTION ===\n"
            "Judge the ATTACHED image ONLY against the criteria above. Invent no new criteria.\n"
            "If every criterion passes, reply with exactly this first line: VERDICT:PASS  and DO NOT "
            "generate a new image.\n"
            "Otherwise make ONE edit generation that fixes ONLY the failed criteria, save it as a PNG "
            "in the current working directory, then reply with first line: VERDICT:EDITED  followed by "
            "a judgment of at most 10 lines naming exactly which criteria you fixed.\n")


def _parse_verdict(text: str) -> tuple[str, str]:
    upper = (text or "").upper()
    if "VERDICT:PASS" in upper:
        head = "VERDICT:PASS"
    elif "VERDICT:EDITED" in upper:
        head = "VERDICT:EDITED"
    else:
        head = "VERDICT:UNKNOWN"
    tail = "\n".join((text or "").strip().splitlines()[:11])
    return head, tail


def command_refine(args: argparse.Namespace) -> int:
    root = Path(args.project_root).expanduser().resolve()
    fid = require_fid(args.figure_id)
    path = fsg_path(root, fid)
    fsg = load_json(path)
    figure = fsg.get("figure") or {}
    style = fsg.get("style") or {}
    cdir = compile_dir(root, fid)
    prompts_dir = cdir / "prompts"
    eid = args.element
    if not eid or not EID_RE.match(eid):
        print("refine: --element E## is required", file=sys.stderr)
        return 2
    node = next((n for n in fsg.get("nodes") or [] if n.get("id") == eid), None)
    if node is None:
        print(f"refine {fid}: no node {eid}", file=sys.stderr)
        return 2
    if node.get("instance_of"):
        print(f"refine {fid}: {eid} is an instance_of node (refine its source instead)", file=sys.stderr)
        return 2
    gen = node.get("gen") or {}
    sv = gen.get("spec_version") or "S01"
    cur_rel = gen.get("asset")
    current = (vp_dir(root) / cur_rel) if cur_rel else None
    if not current or not current.exists():
        print(f"refine {fid}: {eid} has no current asset - run `gen` first", file=sys.stderr)
        return 2
    prompt_file = prompts_dir / f"{fid}.{eid}.{sv}.md"
    if not prompt_file.exists():
        print(f"refine {fid}: no compiled prompt {prompt_file.name}", file=sys.stderr)
        return 2
    checklist_path = cdir / "checklist.json"
    checklist = load_json(checklist_path) if checklist_path.exists() else {}
    max_iters = int(args.max_iters or 2)
    mock = ("AR_FS_MOCK_REFINE_VERDICT" in os.environ) or os.environ.get("AR_FS_MOCK_GEN") == "1"

    book = _book_batch(root, f"figure {fid} refine {eid}",
                       f"Bounded high-reasoning critic for {fid}.{eid}: judge the current asset against "
                       "the compiled checklist; at most one edit per iteration; each edit is a new G asset.")
    _announce_book(book, fid, "refine")

    criteria = _checklist_criteria(checklist, node)
    refine_prompt = _refine_prompt(prompt_file.read_text(encoding="utf-8"), criteria)
    produced, rel_artifacts = [], []
    final_verdict = "VERDICT:PASS (no iterations run)"
    for it in range(max_iters):
        workdir = cdir / "refine_work" / eid
        workdir.mkdir(parents=True, exist_ok=True)
        driver_args = _codex_gen_args(workdir, refine_prompt, refine=True, images=[current])
        started = time.time()
        if mock:
            if os.environ.get("AR_FS_MOCK_REFINE_VERDICT", "PASS").upper().startswith("PASS"):
                final_verdict = f"VERDICT:PASS (mock, iter {it + 1})"
                print(f"refine {fid}.{eid}: {final_verdict}")
                break
            src = workdir / f"{fid}.{eid}.{sv}.refine{it + 1}.png"
            _mock_render(src, node, style)
            verdict_head = "VERDICT:EDITED"
            verdict_tail = f"VERDICT:EDITED (mock, iter {it + 1}): re-rendered to satisfy checklist"
            exit_code = 0
        else:
            try:
                proc = subprocess.run(driver_args, env=_codex_env(), stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=int(getattr(args, "timeout_s", None) or 900))
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                print(f"!! refine {fid}.{eid}: codex timed out", file=sys.stderr)
                break
            last_msg = (workdir / "last_msg.txt").read_text(encoding="utf-8", errors="replace") \
                if (workdir / "last_msg.txt").exists() else (proc.stdout or "")
            verdict_head, verdict_tail = _parse_verdict(last_msg)
            if verdict_head == "VERDICT:PASS":
                final_verdict = verdict_tail or "VERDICT:PASS"
                print(f"refine {fid}.{eid}: PASS (iter {it + 1})")
                break
            if verdict_head != "VERDICT:EDITED":
                final_verdict = verdict_tail or "VERDICT:UNKNOWN"
                print(f"!! refine {fid}.{eid}: unparseable verdict, stopping (iter {it + 1})")
                break
            src = _locate_generated_png(workdir, started)
            if src is None:
                print(f"!! refine {fid}.{eid}: verdict EDITED but no PNG produced", file=sys.stderr)
                break
        wall = round(time.time() - started, 3)
        fname, _generation = _finalize_asset(root, fid, node, sv, src, "refine", prompt_file,
                                             refine_prompt, figure, driver_args, wall, exit_code,
                                             mock, book, verdict=verdict_tail)
        produced.append(fname)
        rel_artifacts.append(f"research/artifacts/visual-program/assets/{fname}")
        final_verdict = verdict_tail
        _locked_merge_write(path, lambda fresh: _apply_gen_updates(  # P1: only this node's pointer
            fresh, {eid: {"generation": node["gen"]["generation"], "asset": node["gen"]["asset"]}}))
        current = assets_dir(root) / fname
        print(f"refine {fid}: {eid} -> {fname}  [{verdict_head}] ({wall}s)")

    if produced:
        _locked_merge_write(path, lambda fresh: _apply_gen_updates(
            fresh, {eid: {"generation": node["gen"]["generation"], "asset": node["gen"]["asset"]}}))
        book = _return_batch(root, book, produced, rel_artifacts,
                             f"refine produced {len(produced)} edit(s) for {fid}.{eid}")
        if book.get("status") == "return-failed":
            print(f"!! {fid}: refine bookkeeping return degraded: {book.get('warning')}")
        _refresh_ledger(root, fid)
    head = final_verdict.splitlines()[0] if final_verdict else "VERDICT:PASS"
    print(f"refine {fid}.{eid}: {len(produced)} edit(s); final {head}")
    return 0


# ---------------------------------------------------------------- assemble (lxml)

def _poly_box(poly: Any) -> tuple[float, float, float, float] | None:
    if poly is None:
        return None
    nums: list[float] = []
    for tok in (poly.get("points") or "").replace(",", " ").split():
        try:
            nums.append(float(tok))
        except ValueError:
            continue
    xs, ys = nums[0::2], nums[1::2]
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _data_uri(png_path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(png_path.read_bytes()).decode("ascii")


def _svg_units_per_mm(root_el: Any, figure: dict[str, Any]) -> float:
    width_mm = float((figure.get("canvas") or {}).get("width_mm") or DOUBLE_MM)
    view_box = root_el.get("viewBox")
    if view_box:
        parts = view_box.split()
        if len(parts) == 4:
            with contextlib.suppress(ValueError):
                return float(parts[2]) / width_mm
    match = re.match(r"([\d.]+)", root_el.get("width", "") or "")
    if match:
        with contextlib.suppress(ValueError):
            return float(match.group(1)) / width_mm
    return 72.0 / 25.4


def _prepare_chroma(png_path: Path) -> Path:
    """Key magenta out of a chroma asset, saving the cleaned PNG + zones JSON next to the
    asset. Returns the cleaned PNG path (what assemble embeds)."""
    from PIL import Image
    result = _key_core(Image.open(str(png_path)).convert("RGB"))
    stem = png_path.stem
    cleaned_path = png_path.parent / f"{stem}.cleaned.png"
    result["cleaned"].save(str(cleaned_path))
    write_json(png_path.parent / f"{stem}.zones.json",
               {"zones": [{"bbox": z["bb"], "glyphs": z["n"]} for z in result["zones"]],
                "residue_after_clean_px": result["residue"], "dims": result["dims"]})
    return cleaned_path


def _trim_to_content(png_path: Path) -> Path:
    """P3: crop a generated asset to its content bbox before the contain-fit embed,
    so a generation's own generous margin does not stack with the slot's letterbox
    and shrink the element to a stamp. The border colour is read from the four
    corners; if they agree within TRIM_BORDER_TOL, every pixel within tolerance of
    it is treated as border and cropped away, keeping a TRIM_MARGIN_FRAC margin.
    Returns a sibling `<stem>.trim.png`, or the original path when there is no
    uniform border to trim (or Pillow is unavailable)."""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return png_path
    img = Image.open(str(png_path)).convert("RGB")
    w, h = img.size
    if w < 3 or h < 3:
        return png_path
    corners = [img.getpixel((0, 0)), img.getpixel((w - 1, 0)),
               img.getpixel((0, h - 1)), img.getpixel((w - 1, h - 1))]
    ref = corners[0]
    if any(max(abs(c[k] - ref[k]) for k in range(3)) > TRIM_BORDER_TOL for c in corners[1:]):
        return png_path  # corners disagree -> no single uniform border colour
    diff = ImageChops.difference(img, Image.new("RGB", (w, h), tuple(ref)))
    r, g, b = diff.split()
    maxchan = ImageChops.lighter(ImageChops.lighter(r, g), b)  # per-pixel max channel delta
    mask = maxchan.point(lambda v: 255 if v > TRIM_BORDER_TOL else 0)
    bbox = mask.getbbox()
    if not bbox or bbox == (0, 0, w, h):
        return png_path  # blank, or content already fills the frame
    mx = max(1, round(TRIM_MARGIN_FRAC * w))
    my = max(1, round(TRIM_MARGIN_FRAC * h))
    x0, y0, x1, y1 = bbox
    crop = (max(0, x0 - mx), max(0, y0 - my), min(w, x1 + mx), min(h, y1 + my))
    out = png_path.parent / f"{png_path.stem}.trim.png"
    img.crop(crop).save(str(out))
    return out


def _stack_order_key(text: dict[str, Any]) -> int:
    """P4 below-anchor stacking order: labels (0) before annotations/other (1)."""
    return 0 if text.get("role") == "label" else 1


def _svg_to_pdf(fid: str, out_svg: Path, cdir: Path) -> None:
    """Export the assembled SVG to PDF via the discovered Inkscape (shared by both the
    graphviz and band-grid assemble paths). Degrades loudly — the SVG is a valid
    standalone deliverable when Inkscape is absent or the export fails."""
    inkscape = find_inkscape()
    if inkscape:
        pdf = cdir / f"{fid}.assembled.pdf"
        proc = subprocess.run([inkscape, str(out_svg), "--export-type=pdf",
                               f"--export-filename={pdf}"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode == 0 and pdf.exists():
            print(f"assemble {fid}: {pdf.name} exported via Inkscape")
        else:
            print(f"!! DEGRADED: Inkscape export failed (rc={proc.returncode}); "
                  f"{out_svg.name} remains a valid deliverable")
    else:
        print(f"!! DEGRADED: Inkscape not found (PATH or {DEFAULT_INKSCAPE}); PDF skipped -- "
              f"{out_svg.name} is a valid standalone deliverable")


# ---------------------------------------------------------------- band-grid assemble (M4 §2)

def _parse_band_header(role: Any) -> tuple[str, str, str]:
    """Parse a panel.role like ``"01 DECLARE - subtitle"`` into (number, title, subtitle).
    A role without a leading number or a trailing ``- subtitle`` degrades gracefully."""
    text = str(role or "").strip()
    match = re.match(r"^\s*(\d+)\s+(.+?)(?:\s+[-–—]\s+(.+))?$", text)
    if match:
        return match.group(1), (match.group(2) or "").strip(), (match.group(3) or "").strip()
    return "", text, ""


def _text_dims(text: dict[str, Any], width_mm: float, height_mm: float) -> tuple[float, float]:
    """Rendered line height (0-100 y) and box width (0-100 x) for a text. Shared by the
    band-grid text layout and qa's estimator so both agree on box sizes."""
    size_pt = float(text.get("size_pt") or 8)
    fh = size_pt * 0.3528 / height_mm * 100.0
    fw = size_pt * 0.3528 / width_mm * 100.0
    box_w = min(60.0, len(str(text.get("content") or "")) * 0.6 * fw)
    return fh, box_w


def _bandgrid_slot_order(fsg: dict[str, Any], bboxes: dict[str, list[float]],
                         bands: list[dict[str, Any]]) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Left-to-right slot order per band and each slot's parity (0/1). The parity drives
    the odd/even 0.6-line text stagger (spec §2) that separates adjacent-slot label stacks."""
    order: dict[str, list[str]] = {}
    parity: dict[str, int] = {}
    node_panel = {n.get("id"): n.get("panel") for n in fsg.get("nodes") or []}
    for band in bands:
        pid = band.get("panel_id")
        ids = [nid for nid in bboxes if node_panel.get(nid) == pid and nid != "E00"]
        ids.sort(key=lambda nid: ((bboxes[nid][0] + bboxes[nid][2]) / 2.0, nid))
        order[pid] = ids
        for i, nid in enumerate(ids):
            parity[nid] = i % 2
    return order, parity


def _bandgrid_text_layout(fsg: dict[str, Any], stagger: bool = True) -> list[dict[str, Any]]:
    """Every text's rendered geometry under the band template (spec §2). Node-anchored
    texts stack directly below their slot with a 1.35-line advance and, for odd-index
    adjacent slots, a 0.6-line downward stagger that kills the T07/T08 near-touch;
    panel-anchored (P##) texts become subtitle lines inside the band header. assemble
    emits from this and qa reads the same boxes, so renderer and gate never disagree.
    Each item: {id, anchor, role, content, weight, cx, cy, fh, box[0-100]}."""
    layout = fsg.get("layout") or {}
    bboxes = layout.get("bboxes") or {}
    bands = layout.get("bands") or []
    canvas = (fsg.get("figure") or {}).get("canvas") or {}
    width_mm = float(canvas.get("width_mm") or DOUBLE_MM)
    height_mm = float(canvas.get("height_mm") or DEFAULT_H_MM)
    _order, parity = _bandgrid_slot_order(fsg, bboxes, bands)
    header_by_pid = {b.get("panel_id"): b.get("header_bbox") for b in bands}

    by_anchor: dict[Any, list[dict[str, Any]]] = {}
    for text in fsg.get("texts") or []:
        by_anchor.setdefault(text.get("anchor"), []).append(text)

    out: list[dict[str, Any]] = []

    def emit(text: dict[str, Any], cx: float, cy: float, fh: float, box_w: float) -> None:
        out.append({
            "id": text.get("id"), "anchor": text.get("anchor"), "role": text.get("role"),
            "content": str(text.get("content") or ""), "weight": text.get("weight"),
            "cx": cx, "cy": cy, "fh": fh,
            "box": [max(0.0, cx - box_w / 2), max(0.0, cy - fh * 0.55),
                    min(100.0, cx + box_w / 2), min(100.0, cy + fh * 0.35)],
        })

    for anchor, group in by_anchor.items():
        box = bboxes.get(anchor)
        if box:                                        # node-anchored -> stack below the slot
            cx = (box[0] + box[2]) / 2.0
            slot_bottom = box[3]
            off = BAND_TEXT_STAGGER if (stagger and parity.get(anchor, 0)) else 0.0
            cy = 0.0
            for i, text in enumerate(sorted(group, key=_stack_order_key)):
                fh, box_w = _text_dims(text, width_mm, height_mm)
                cy = (slot_bottom + fh * (0.9 + off)) if i == 0 else (cy + fh * BAND_LINE_ADVANCE)
                emit(text, cx, cy, fh, box_w)
            continue
        header = header_by_pid.get(anchor)             # panel-anchored -> header subtitle lines
        if not header:
            continue
        cx = (header[0] + header[2]) / 2.0
        base = header[3]
        for i, text in enumerate(sorted(group, key=lambda t: str(t.get("id")))):
            fh, box_w = _text_dims(text, width_mm, height_mm)
            cy = base - fh * (0.6 + i * BAND_LINE_ADVANCE)
            emit(text, cx, cy, fh, box_w)
    return out


def _edge_stroke_class(edge: dict[str, Any], palette: dict[str, Any]) -> str:
    """Three connector classes (spec §6.3): a ``diagnostic``-typed edge, a lane-colored edge
    (``lane`` names a palette key), else a neutral dataflow/stage-flow edge."""
    if str(edge.get("type")) == "diagnostic":
        return "diagnostic"
    lane = edge.get("lane")
    if lane and lane in palette:
        return "lane"
    return "neutral"


def _seg_hits_rect(x1: float, y1: float, x2: float, y2: float,
                   rect: tuple[float, float, float, float], eps: float = 0.15) -> bool:
    """True when the axis-aligned segment passes through the rectangle's interior (spec §6.4
    port discipline). The eps inset means a segment that merely grazes an edge or runs along
    a gutter does not count — only a real crossing does. Shared by the router and the qa/self
    -test crossing check so the renderer and the gate agree segment-for-segment."""
    rx0, ry0, rx1, ry1 = rect
    if abs(x1 - x2) < 1e-6:                                # vertical
        if not (rx0 + eps < x1 < rx1 - eps):
            return False
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        return hi > ry0 + eps and lo < ry1 - eps
    if abs(y1 - y2) < 1e-6:                                # horizontal
        if not (ry0 + eps < y1 < ry1 - eps):
            return False
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        return hi > rx0 + eps and lo < rx1 - eps
    return False


def _polyline_clear(points: list[tuple[float, float]],
                    obstacles: list[tuple[float, float, float, float]]) -> bool:
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        for rect in obstacles:
            if _seg_hits_rect(x1, y1, x2, y2, rect):
                return False
    return True


def _route_band_edge(sx: float, sy: float, tx: float, ty: float,
                     obstacles: list[tuple[float, float, float, float]],
                     channel_x: float, prefer_channel: bool, step: float) -> list[tuple[float, float]]:
    """Orthogonal route from a source bottom-port to a target top-port that never lets a
    vertical (or horizontal) segment cross a non-endpoint slot (spec §6.4). Tries a small,
    deterministic candidate set — straight / mid elbow / a jog through a clear vertical lane
    (source column, target column, midline, or the right-margin channel) — and returns the
    first that clears every obstacle. ``step`` is the short port stub before a lateral jog,
    kept small so cross-band jogs land in the inter-band rhythm gap rather than a mid slot.
    Long / diagnostic edges prefer the right channel so they never sweep the main field."""
    sgn = 1.0 if ty >= sy else -1.0
    d = max(6.0, min(step, abs(ty - sy) * 0.45))
    yA, yB = sy + sgn * d, ty - sgn * d
    straight = [(sx, sy), (tx, ty)] if abs(sx - tx) < 1.0 else None
    my = (sy + ty) / 2.0
    elbow = [(sx, sy), (sx, my), (tx, my), (tx, ty)]

    def lane_route(lane: float) -> list[tuple[float, float]]:
        return [(sx, sy), (sx, yA), (lane, yA), (lane, yB), (tx, yB), (tx, ty)]

    lanes = ([channel_x, tx, sx, (sx + tx) / 2.0] if prefer_channel
             else [tx, sx, (sx + tx) / 2.0, channel_x])
    lane_cands = [lane_route(lane) for lane in lanes]
    simple = [c for c in (straight, elbow) if c]
    ordered = (lane_cands + simple) if prefer_channel else (simple + lane_cands)
    for cand in ordered:
        if _polyline_clear(cand, obstacles):
            return cand
    return elbow


def _band_edge_geometry(fsg: dict[str, Any]) -> list[dict[str, Any]]:
    """Every band connector's drawn geometry (spec §6.3-§6.4), in SVG px: routed polyline,
    stroke class + width + color + dash/opacity, arrow direction, and label anchor. Source
    exits bottom-center, target enters top-center; multiple edges at one port fan out over
    +-20% of the slot width; vertical segments jog around non-endpoint slots; long / diagnostic
    edges take the right-margin channel. Both the renderer and the self-test crossing gate read
    this, so what is checked is exactly what is drawn."""
    layout = fsg.get("layout") or {}
    bboxes = layout.get("bboxes") or {}
    bands = layout.get("bands") or []
    canvas = (fsg.get("figure") or {}).get("canvas") or {}
    cw = float(canvas.get("width_mm") or DOUBLE_MM)
    ch = float(canvas.get("height_mm") or DEFAULT_H_MM)
    w_px = BAND_CANVAS_W_PX
    h_px = w_px * ((ch / cw) if cw else 0.5)
    palette = (fsg.get("style") or {}).get("palette_hex") or {}
    ink2 = palette.get("neutral") or "#666666"

    def ax(v: float) -> float:
        return v / 100.0 * w_px

    def ay(v: float) -> float:
        return v / 100.0 * h_px

    channel_x = ax(BAND_RIGHT_CHANNEL)
    node_panel = {n.get("id"): n.get("panel") for n in fsg.get("nodes") or []}
    band_index = {b.get("panel_id"): i for i, b in enumerate(bands)}
    slot_px = {nid: (ax(b[0]), ay(b[1]), ax(b[2]), ay(b[3])) for nid, b in bboxes.items()}

    edges = [e for e in sorted(fsg.get("edges") or [], key=lambda e: str(e.get("id")))
             if e.get("from") != "E00" and e.get("to") != "E00"
             and e.get("from") in bboxes and e.get("to") in bboxes]
    out_groups: dict[Any, list[Any]] = {}
    in_groups: dict[Any, list[Any]] = {}
    for edge in edges:
        out_groups.setdefault(edge.get("from"), []).append(edge.get("id"))
        in_groups.setdefault(edge.get("to"), []).append(edge.get("id"))

    def port_x(box: list[float], group: list[Any], eid: Any) -> float:
        cx = (box[0] + box[2]) / 2.0
        k = len(group)
        if k <= 1:
            return cx
        i = group.index(eid)
        frac = -BAND_PORT_SPREAD + 2.0 * BAND_PORT_SPREAD * i / (k - 1)
        return cx + frac * (box[2] - box[0])

    geoms: list[dict[str, Any]] = []
    for edge in edges:
        eid, frm, to = edge.get("id"), edge.get("from"), edge.get("to")
        bf, bt = bboxes[frm], bboxes[to]
        cls = _edge_stroke_class(edge, palette)
        if cls == "diagnostic":
            color, dashed, opacity = ink2, True, BAND_DIAG_OPACITY
        elif cls == "lane":
            color, dashed, opacity = palette.get(edge.get("lane")) or ink2, False, 1.0
        else:
            color, dashed, opacity = ink2, False, 1.0
        width = BAND_EDGE_W[cls] * w_px
        forward = bt[1] >= bf[1]
        sx = ax(port_x(bf, out_groups.get(frm, [eid]), eid))
        sy = ay(bf[3] if forward else bf[1])              # source bottom-center exit (§6.4)
        tx = ax(port_x(bt, in_groups.get(to, [eid]), eid))
        ty = ay(bt[1] if forward else bt[3])              # target top-center entry (§6.4)
        span = abs(band_index.get(node_panel.get(frm), 0) - band_index.get(node_panel.get(to), 0))
        prefer_channel = (cls == "diagnostic" and span >= 1) or span >= 2
        obstacles = [slot_px[n] for n in slot_px if n not in (frm, to)]
        step = max(6.0, 0.012 * h_px)
        points = _route_band_edge(sx, sy, tx, ty, obstacles, channel_x, prefer_channel, step)
        geoms.append({"id": eid, "points": points, "width": width, "color": color,
                      "dashed": dashed, "opacity": opacity, "forward": forward, "cls": cls,
                      "label_ref": edge.get("label_ref"), "mid": points[len(points) // 2]})
    return geoms


def _render_band_grid(root: Path, fid: str, fsg: dict[str, Any], cdir: Path,
                      style: dict[str, Any], figure: dict[str, Any]) -> int:
    """Render the full presentation-grade band template from the band-grid layout (spec §2):
    open horizontal bands (thin full-width rule + typographic header, no panel boxes), faint
    alternating band tint, floor-sized 88%-fill slots (wide assets expand in width), staggered
    below-slot text stacks, and three-class graded connectors (lane / neutral / dashed
    diagnostic) with disciplined ports and slot-avoiding routes and filled triangular
    arrowheads. Builds the SVG with lxml and exports the PDF via the shared Inkscape path."""
    from lxml import etree

    layout = fsg.get("layout") or {}
    bboxes = layout.get("bboxes") or {}
    bands = layout.get("bands") or []
    if not bands:
        print(f"assemble {fid}: layout.bands missing — recompile in band-grid mode", file=sys.stderr)
        return 1

    canvas = figure.get("canvas") or {}
    cw = float(canvas.get("width_mm") or DOUBLE_MM)
    ch = float(canvas.get("height_mm") or DEFAULT_H_MM)
    w_px = BAND_CANVAS_W_PX
    h_px = w_px * ((ch / cw) if cw else 0.5)
    palette = style.get("palette_hex") or {}
    navy = palette.get("primary") or "#0072B2"
    ink2 = palette.get("neutral") or "#666666"
    stroke_w = BAND_STROKE_FRAC * w_px

    svg_ns, xlink_ns = "http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink"
    svg = etree.Element(f"{{{svg_ns}}}svg", nsmap={None: svg_ns, "xlink": xlink_ns})
    svg.set("width", f"{w_px:.1f}")
    svg.set("height", f"{h_px:.1f}")
    svg.set("viewBox", f"0 0 {w_px:.1f} {h_px:.1f}")

    def q(tag: str) -> str:
        return f"{{{svg_ns}}}{tag}"

    def ax(v: float) -> float:
        return v / 100.0 * w_px

    def ay(v: float) -> float:
        return v / 100.0 * h_px

    def add(parent: Any, tag: str, **attrs: Any) -> Any:
        el = etree.SubElement(parent, q(tag))
        for key, val in attrs.items():
            el.set(key.replace("_", "-"), str(val))
        return el

    add(svg, "rect", x=0, y=0, width=f"{w_px:.1f}", height=f"{h_px:.1f}", fill="#ffffff")

    # bands: alternating tint, full-width rule, typographic header
    for bi, band in enumerate(bands):
        bb = band.get("band_bbox") or [0, 0, 100, 100]
        hb = band.get("header_bbox") or bb
        rule_y = float(band.get("rule_y") or bb[1])
        bx0, by0, bx1, by1 = ax(bb[0]), ay(bb[1]), ax(bb[2]), ay(bb[3])
        if bi % 2 == 1:                                # faint alternating band tint (default on)
            add(svg, "rect", x=f"{bx0:.1f}", y=f"{by0:.1f}", width=f"{bx1 - bx0:.1f}",
                height=f"{by1 - by0:.1f}", fill=navy, fill_opacity=f"{BAND_TINT_OPACITY}")
        add(svg, "line", x1=f"{bx0:.1f}", y1=f"{ay(rule_y):.1f}", x2=f"{bx1:.1f}",
            y2=f"{ay(rule_y):.1f}", stroke=ink2, stroke_width=f"{max(1.0, stroke_w * 0.34):.2f}")
        hy0, hy1 = ay(hb[1]), ay(hb[3])
        header_h = max(1.0, hy1 - hy0)
        num, title, subtitle = _parse_band_header((next(
            (p for p in fsg.get("panels") or [] if p.get("id") == band.get("panel_id")), {})).get("role"))
        title_fs = max(11.0, header_h * 0.34)
        base_y = hy0 + header_h * 0.70
        cursor = ax(hb[0]) + stroke_w
        if num:
            add(svg, "text", x=f"{cursor:.1f}", y=f"{base_y:.1f}",
                font_family="Helvetica, Arial, sans-serif", font_weight="bold",
                font_size=f"{title_fs * 2.2:.1f}", fill=navy,
                fill_opacity=f"{BAND_NUMBER_OPACITY}").text = num
            cursor += title_fs * 2.2 * 0.62 * len(num) + title_fs * 0.45
        add(svg, "text", x=f"{cursor:.1f}", y=f"{base_y:.1f}",
            font_family="Helvetica, Arial, sans-serif", font_weight="bold",
            font_size=f"{title_fs:.1f}", letter_spacing=f"{title_fs * 0.06:.2f}",
            fill=navy).text = title.upper()
        if subtitle:
            add(svg, "text", x=f"{cursor:.1f}", y=f"{base_y + title_fs * 0.95:.1f}",
                font_family="Helvetica, Arial, sans-serif", font_size=f"{title_fs * 0.6:.1f}",
                fill=ink2).text = subtitle

    # slots: contain-fit each asset to 88% of its slot (spec §6.2), no slot box drawn. A wide
    # asset is height-bound at that fill, so its width may expand toward the layer's available
    # cell width (minus the gutter) instead of being crammed into a narrow slot for symmetry.
    from PIL import Image as _PILImage
    slot_cells = layout.get("slot_cells") or {}
    inner_w = 100.0 - 2.0 * BAND_SIDE_MARGIN
    gutter_px = ax(BAND_SLOT_GUTTER * inner_w)
    nodes_by_id = {n.get("id"): n for n in fsg.get("nodes") or []}
    embedded = 0
    for nid in sorted(bboxes):
        node = nodes_by_id.get(nid)
        if node is None:
            continue
        src_node = nodes_by_id.get(node.get("instance_of")) if node.get("instance_of") else node
        if src_node is None:
            continue
        rel = (src_node.get("gen") or {}).get("asset")
        png = (vp_dir(root) / rel) if rel else None
        if not png or not png.exists():
            continue
        if (src_node.get("gen") or {}).get("text_strategy") == "chroma":
            png = _prepare_chroma(png)
        png = _trim_to_content(png)
        box = bboxes[nid]
        sx0, sy0, sx1, sy1 = ax(box[0]), ay(box[1]), ax(box[2]), ay(box[3])
        sw, sh = max(0.0, sx1 - sx0), max(0.0, sy1 - sy0)
        iw, ih = sw * BAND_SLOT_FILL, sh * BAND_SLOT_FILL
        try:
            aw, ah = _PILImage.open(str(png)).size
            aspect = (aw / ah) if ah else 1.0
        except Exception:  # noqa: BLE001 - fall back to plain contain-fit
            aspect = 1.0
        if aspect > (iw / ih if ih else 1.0):             # wide asset: expand width, keep 88% height
            cell_px = ax(slot_cells.get(nid, box[2] - box[0]))
            max_w = max(sw, cell_px - gutter_px)
            iw = min(max_w, ih * aspect)
        cx_px, cy_px = (sx0 + sx1) / 2.0, (sy0 + sy1) / 2.0
        img = add(svg, "image", x=f"{cx_px - iw / 2:.2f}", y=f"{cy_px - ih / 2:.2f}",
                  width=f"{iw:.2f}", height=f"{ih:.2f}", preserveAspectRatio="xMidYMid meet")
        img.set("data-slot", str(nid))
        img.set("data-fill", f"{max(iw / sw if sw else 0.0, ih / sh if sh else 0.0):.3f}")
        uri = _data_uri(png)
        img.set("href", uri)
        img.set(f"{{{xlink_ns}}}href", uri)
        embedded += 1

    # connectors: the protagonist. Three graded classes (lane colored 0.45% / neutral 0.30% /
    # diagnostic 0.16% dashed 50%), disciplined ports, and slot-avoiding routes (spec §6.3-§6.4).
    def arrowhead(tip_x: float, tip_y: float, direction: int, color: str,
                  width: float, opacity: float) -> None:
        a, b = width * 1.7, width * 3.1                # scales with the connector's stroke weight
        pts = f"{tip_x:.1f},{tip_y:.1f} {tip_x - a:.1f},{tip_y - direction * b:.1f} " \
              f"{tip_x + a:.1f},{tip_y - direction * b:.1f}"
        add(svg, "polygon", points=pts, fill=color, stroke="none", fill_opacity=f"{opacity}")

    for geom in _band_edge_geometry(fsg):
        pts = geom["points"]
        color, width, opacity = geom["color"], geom["width"], geom["opacity"]
        d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        path = add(svg, "path", d=d, fill="none", stroke=color, stroke_width=f"{width:.2f}",
                   stroke_linejoin="round", stroke_linecap="round", stroke_opacity=f"{opacity}")
        if geom["dashed"]:
            path.set("stroke-dasharray", f"{width * 2.4:.1f} {width * 1.8:.1f}")
        tip_x, tip_y = pts[-1]
        arrowhead(tip_x, tip_y, 1 if geom["forward"] else -1, color, width, opacity)
        ref = geom["label_ref"]
        if ref:
            txt = next((str(t.get("content")) for t in fsg.get("texts") or []
                        if t.get("id") == ref), str(ref))
            mx, my = geom["mid"]
            add(svg, "text", x=f"{mx:.1f}", y=f"{my - width:.1f}",
                text_anchor="middle", font_family="Helvetica, Arial, sans-serif",
                font_size=f"{max(9.0, width * 3.2):.1f}", fill=ink2).text = txt

    # text layer: staggered below-slot stacks + header subtitles, from the shared layout
    for item in _bandgrid_text_layout(fsg):
        role = item["role"]
        fs = ay(item["fh"])
        el = add(svg, "text", text_anchor="middle", font_family="Helvetica, Arial, sans-serif",
                 x=f"{ax(item['cx']):.1f}", y=f"{ay(item['cy']) + fs * 0.34:.1f}")
        if role == "label":
            el.set("fill", "#111111")
            el.set("font-size", f"{fs:.1f}")
            if item.get("weight") == "bold":
                el.set("font-weight", "bold")
        elif role in ("panel-title", "title"):
            el.set("fill", ink2)
            el.set("font-weight", "bold")
            el.set("font-size", f"{fs * 0.9:.1f}")
        else:
            el.set("fill", ink2)
            el.set("font-size", f"{fs * 0.85:.1f}")
        el.text = item["content"]

    out_svg = cdir / f"{fid}.assembled.svg"
    etree.ElementTree(svg).write(str(out_svg), xml_declaration=True, encoding="utf-8")
    print(f"assemble {fid}: {out_svg.name} written (band-grid; {embedded} asset(s) embedded, "
          f"{len(bands)} band(s), {len(fsg.get('texts') or [])} text anchor(s))")
    _svg_to_pdf(fid, out_svg, cdir)
    return 0


def command_assemble(args: argparse.Namespace) -> int:
    root = Path(args.project_root).expanduser().resolve()
    fid = require_fid(args.figure_id)
    path = fsg_path(root, fid)
    fsg = load_json(path)
    figure = fsg.get("figure") or {}
    style = fsg.get("style") or {}
    cdir = compile_dir(root, fid)
    cdir.mkdir(parents=True, exist_ok=True)

    # band-grid solver renders the full template itself (no skeleton.svg, no dot). The
    # graphviz path below is left untouched (spec §2 / §5: retreat path preserved).
    solver = (fsg.get("layout") or {}).get("solver")
    if solver == "band-grid":
        try:
            from lxml import etree  # noqa: F401
        except ImportError:
            print("assemble requires lxml (py -m pip install lxml)", file=sys.stderr)
            return 2
        return _render_band_grid(root, fid, fsg, cdir, style, figure)

    dot_bin = find_dot()
    svg_path = cdir / "skeleton.svg"
    bboxes = (fsg.get("layout") or {}).get("bboxes") or {}
    if (not svg_path.exists() or not bboxes) and dot_bin:
        dot_path = cdir / "skeleton.dot"
        write_text(dot_path, emit_skeleton_dot(fsg))
        try:
            _svg_ok, n_bboxes = _dot_layout(dot_bin, dot_path, cdir, fsg)
            if n_bboxes:
                solved_layout = fsg.get("layout")
                _locked_merge_write(path, lambda fresh: fresh.__setitem__("layout", solved_layout))
                print(f"assemble {fid}: re-ran layout ({n_bboxes} bboxes solved)")
        except Exception as exc:  # noqa: BLE001
            print(f"!! assemble {fid}: dot layout failed ({exc})", file=sys.stderr)
        bboxes = (fsg.get("layout") or {}).get("bboxes") or {}

    if not svg_path.exists():
        print(f"assemble {fid}: skeleton.svg missing and dot not found (PATH or {DEFAULT_DOT}); "
              "run compile with Graphviz first", file=sys.stderr)
        return 1
    if not bboxes:
        print(f"assemble {fid}: layout.bboxes unsolved; Graphviz is required", file=sys.stderr)
        return 1
    try:
        from lxml import etree
    except ImportError:
        print("assemble requires lxml (py -m pip install lxml)", file=sys.stderr)
        return 2

    svg_ns, xlink_ns = "http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink"

    def q(tag: str) -> str:
        return f"{{{svg_ns}}}{tag}"

    neutral = (style.get("palette_hex") or {}).get("neutral") or "#666666"
    tree = etree.parse(str(svg_path))
    root_el = tree.getroot()
    graph = root_el.find(q("g"))
    if graph is None:
        print(f"assemble {fid}: skeleton.svg has no graph group", file=sys.stderr)
        return 1
    nodes_by_id = {n.get("id"): n for n in fsg.get("nodes") or []}

    # pass 1: harvest solved boxes, strip dot's own text labels, restyle edges to neutral
    node_boxes: dict[str, tuple[float, float, float, float]] = {}
    panel_boxes: dict[str, tuple[float, float, float, float]] = {}
    for group in list(root_el.iter(q("g"))):
        cls = group.get("class")
        title_el = group.find(q("title"))
        title = title_el.text if title_el is not None else None
        if cls == "node" and title:
            poly = group.find(q("polygon"))
            box = _poly_box(poly)
            if box:
                node_boxes[title] = box
            # P3: drop the slot rect entirely — no white placeholder box is
            # painted behind the asset (the trimmed asset stands on its own).
            if poly is not None:
                group.remove(poly)
            for text_el in group.findall(q("text")):
                group.remove(text_el)
        elif cls == "cluster" and title:
            box = _poly_box(group.find(q("polygon")))
            if box:
                panel_boxes[title.replace("cluster_", "")] = box
            for text_el in group.findall(q("text")):
                group.remove(text_el)
        elif cls == "edge":
            for pth in group.findall(q("path")):
                pth.set("stroke", neutral)
                pth.set("fill", "none")
            for pol in group.findall(q("polygon")):
                pol.set("stroke", neutral)
                pol.set("fill", neutral)

    # pass 2a: embed asset PNGs (chroma keyed + cleaned first) at their solved boxes
    embedded = 0
    for eid, box in node_boxes.items():
        node = nodes_by_id.get(eid)
        if node is None:
            continue
        src_node = nodes_by_id.get(node.get("instance_of")) if node.get("instance_of") else node
        if src_node is None:
            continue
        rel = (src_node.get("gen") or {}).get("asset")
        png = (vp_dir(root) / rel) if rel else None
        if not png or not png.exists():
            continue
        if (src_node.get("gen") or {}).get("text_strategy") == "chroma":
            png = _prepare_chroma(png)
        png = _trim_to_content(png)  # P3: crop uniform border before the contain-fit embed
        x0, y0, x1, y1 = box
        image_el = etree.SubElement(graph, q("image"))
        image_el.set("x", f"{x0:.2f}")
        image_el.set("y", f"{y0:.2f}")
        image_el.set("width", f"{max(0.0, x1 - x0):.2f}")
        image_el.set("height", f"{max(0.0, y1 - y0):.2f}")
        image_el.set("preserveAspectRatio", "xMidYMid meet")
        uri = _data_uri(png)
        image_el.set("href", uri)
        image_el.set(f"{{{xlink_ns}}}href", uri)
        embedded += 1

    # pass 2b: vector text layer on top. P4: texts sharing one anchor stack —
    # panel titles upward, then labels and annotations downward (labels first) —
    # with a 1.35x line advance in the same px scale used for the font size, so no
    # two texts of an anchor overlap. qa's estimator (_estimate_text_boxes) mirrors
    # this ordering and advance exactly.
    units_per_mm = _svg_units_per_mm(root_el, figure)
    texts_by_anchor: dict[Any, list[dict[str, Any]]] = {}
    for text in fsg.get("texts") or []:
        texts_by_anchor.setdefault(text.get("anchor"), []).append(text)

    def _fu(text: dict[str, Any]) -> float:
        return float(text.get("size_pt") or 8) * (25.4 / 72.0) * units_per_mm

    def _emit_text(text: dict[str, Any], cx: float, baseline_y: float, font_units: float) -> None:
        role = text.get("role")
        el = etree.SubElement(graph, q("text"))
        el.set("text-anchor", "middle")
        el.set("font-family", "Helvetica, Arial, sans-serif")
        if role == "label":
            el.set("fill", "#111111")
            if text.get("weight") == "bold":
                el.set("font-weight", "bold")
            el.set("font-size", f"{font_units:.2f}")
        elif role in ("panel-title", "title"):
            el.set("fill", neutral)
            el.set("font-weight", "bold")
            el.set("font-size", f"{font_units:.2f}")
        else:  # annotation
            el.set("fill", neutral)
            el.set("font-size", f"{font_units * 0.85:.2f}")
        el.set("x", f"{cx:.2f}")
        el.set("y", f"{baseline_y:.2f}")
        el.text = str(text.get("content") or "")

    for anchor, group_texts in texts_by_anchor.items():
        box = node_boxes.get(anchor) or panel_boxes.get(anchor)
        if not box:
            continue
        x0, y0, x1, y1 = box
        cx = (x0 + x1) / 2
        above = [t for t in group_texts if t.get("role") in ("panel-title", "title")]
        below = sorted((t for t in group_texts if t.get("role") not in ("panel-title", "title")),
                       key=_stack_order_key)
        y = 0.0
        for i, text in enumerate(below):
            fu = _fu(text)
            y = (y1 + fu * 1.1) if i == 0 else (y + fu * 1.35)
            _emit_text(text, cx, y, fu)
        y = 0.0
        for i, text in enumerate(above):
            fu = _fu(text)
            y = (y0 - fu * 0.4) if i == 0 else (y - fu * 1.35)
            _emit_text(text, cx, y, fu)

    out_svg = cdir / f"{fid}.assembled.svg"
    tree.write(str(out_svg), xml_declaration=True, encoding="utf-8")
    print(f"assemble {fid}: {out_svg.name} written ({embedded} asset(s) embedded, "
          f"{len(fsg.get('texts') or [])} text anchor(s))")
    _svg_to_pdf(fid, out_svg, cdir)
    return 0


# ---------------------------------------------------------------- qa (mechanical gate)

def _bbox_overlap_area(a: list[float], b: list[float]) -> float:
    ox = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    oy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ox * oy


def _within(inner: list[float], outer: list[float], tol: float = 0.5) -> bool:
    """True when `inner` sits inside `outer` (0-100 coords) within a small tolerance."""
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def _estimate_text_boxes(texts: list[dict[str, Any]], bboxes: dict[str, list[float]],
                         figure: dict[str, Any]) -> list[tuple[Any, list[float]]]:
    """P4: predict each text's rendered box in 0-100 canvas coords, stacking texts
    that share an anchor exactly as assemble lays them out (panel titles up; then
    labels and annotations down, labels first; 1.35x line advance). Only element
    (E##) anchors carry solved bboxes, so panel-anchored titles are skipped here
    just as they are in assemble over the solved layout. Per-line box height is
    kept below the 1.35 advance, so a correctly stacked anchor never self-overlaps —
    which is what makes the collision gate agree with the renderer."""
    canvas = figure.get("canvas") or {}
    width_mm = float(canvas.get("width_mm") or DOUBLE_MM)
    height_mm = float(canvas.get("height_mm") or DEFAULT_H_MM)
    by_anchor: dict[Any, list[dict[str, Any]]] = {}
    for text in texts:
        by_anchor.setdefault(text.get("anchor"), []).append(text)

    def dims(text: dict[str, Any]) -> tuple[float, float, float]:
        size_pt = float(text.get("size_pt") or 8)
        fh = size_pt * 0.3528 / height_mm * 100.0
        fw = size_pt * 0.3528 / width_mm * 100.0
        box_w = min(60.0, len(str(text.get("content") or "")) * 0.6 * fw)
        return fh, box_w, fh * 1.2  # (line unit, width, box height < 1.35 advance)

    def emit(cx: float, cy: float, box_w: float, box_h: float, tid: Any) -> tuple[Any, list[float]]:
        return (tid, [max(0.0, cx - box_w / 2), max(0.0, cy - box_h / 2),
                      min(100.0, cx + box_w / 2), min(100.0, cy + box_h / 2)])

    out: list[tuple[Any, list[float]]] = []
    for anchor, group in by_anchor.items():
        node_bb = bboxes.get(anchor)
        if not node_bb:
            continue
        cx = (node_bb[0] + node_bb[2]) / 2
        above = [t for t in group if t.get("role") in ("panel-title", "title")]
        below = sorted((t for t in group if t.get("role") not in ("panel-title", "title")),
                       key=_stack_order_key)
        cy = 0.0
        for i, text in enumerate(below):
            fh, box_w, box_h = dims(text)
            cy = (node_bb[3] + box_h * 0.7) if i == 0 else (cy + fh * 1.35)
            out.append(emit(cx, cy, box_w, box_h, text.get("id")))
        cy = 0.0
        for i, text in enumerate(above):
            fh, box_w, box_h = dims(text)
            cy = (node_bb[1] - box_h * 0.7) if i == 0 else (cy - fh * 1.35)
            out.append(emit(cx, cy, box_w, box_h, text.get("id")))
    return out


def _measure_residue(png_path: Path, already_clean: bool) -> int:
    from PIL import Image
    img = Image.open(str(png_path)).convert("RGB")
    if already_clean:
        return sum(_pink_mask_bytes(img.tobytes(), img.size[0] * img.size[1])) // 255
    return _key_core(img)["residue"]


def command_qa(args: argparse.Namespace) -> int:
    root = Path(args.project_root).expanduser().resolve()
    fid = require_fid(args.figure_id)
    fsg = load_json(fsg_path(root, fid))
    figure = fsg.get("figure") or {}
    cdir = compile_dir(root, fid)
    checklist_path = cdir / "checklist.json"
    checklist = load_json(checklist_path) if checklist_path.exists() else {}
    hard: list[str] = []
    warn: list[str] = []
    texts = fsg.get("texts") or []
    nodes = fsg.get("nodes") or []
    bboxes = (fsg.get("layout") or {}).get("bboxes") or {}

    # (a) font floor + label boldness
    for text in texts:
        size_pt = text.get("size_pt")
        try:
            value = float(size_pt)
        except (TypeError, ValueError):
            warn.append(f"text {text.get('id')} has no numeric size_pt")
            value = None
        if value is not None:
            if value < 5:
                hard.append(f"text {text.get('id')} size_pt {value} < 5pt hard floor")
            elif value < 6:
                warn.append(f"text {text.get('id')} size_pt {value} below 6pt body floor")
        if text.get("role") == "label" and text.get("weight") != "bold":
            warn.append(f"label {text.get('id')} is not bold (spec: role labels bold)")

    # (b) bbox overlaps: element boxes, then estimated text boxes. Under the band-grid
    # solver the text estimator is the shared staggered layout (spec §3: estimator mirrors
    # the renderer's stagger exactly), so the gate and the SVG agree line-for-line.
    solver = (fsg.get("layout") or {}).get("solver")
    bands = (fsg.get("layout") or {}).get("bands") or []
    ids = sorted(bboxes)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if _bbox_overlap_area(bboxes[ids[i]], bboxes[ids[j]]) > 0.5:
                hard.append(f"element bbox overlap: {ids[i]} & {ids[j]}")
    if solver == "band-grid":
        band_items = _bandgrid_text_layout(fsg)
        text_boxes = [(it["id"], it["box"]) for it in band_items]
    else:
        band_items = []
        text_boxes = _estimate_text_boxes(texts, bboxes, figure)
    for i in range(len(text_boxes)):
        for j in range(i + 1, len(text_boxes)):
            if _bbox_overlap_area(text_boxes[i][1], text_boxes[j][1]) > 0.5:
                hard.append(f"text box overlap: {text_boxes[i][0]} & {text_boxes[j][0]}")

    # (b2) band-grid only (spec §3): in-band overflow (slot or text past its band_bbox) is
    # hard; header zone overlapping a first-layer slot is hard.
    if solver == "band-grid" and bands:
        node_panel = {n.get("id"): n.get("panel") for n in nodes}
        band_by_pid = {b.get("panel_id"): b for b in bands}
        for nid, box in bboxes.items():
            band = band_by_pid.get(node_panel.get(nid))
            if band and band.get("band_bbox") and not _within(box, band["band_bbox"]):
                hard.append(f"slot {nid} overflows band {band.get('panel_id')} bbox")
        for it in band_items:
            anchor = it["anchor"]
            pid = node_panel.get(anchor) if anchor in bboxes else anchor
            band = band_by_pid.get(pid)
            if band and band.get("band_bbox") and not _within(it["box"], band["band_bbox"]):
                hard.append(f"text {it['id']} overflows band {pid} bbox")
        for band in bands:
            pid = band.get("panel_id")
            hb = band.get("header_bbox")
            if not hb:
                continue
            for nid, box in bboxes.items():
                if node_panel.get(nid) == pid and _bbox_overlap_area(hb, box) > 0.5:
                    hard.append(f"header of band {pid} overlaps first-layer slot {nid}")

    # (c) zero magenta residue in every embedded/cleaned chroma asset
    for node in nodes:
        if node.get("instance_of") or (node.get("gen") or {}).get("text_strategy") != "chroma":
            continue
        rel = (node.get("gen") or {}).get("asset")
        if not rel:
            continue
        raw = vp_dir(root) / rel
        cleaned = raw.parent / f"{raw.stem}.cleaned.png"
        target = cleaned if cleaned.exists() else raw
        if not target.exists():
            hard.append(f"chroma node {node.get('id')} has no asset to check")
            continue
        residue = _measure_residue(target, already_clean=cleaned.exists())
        if residue != 0:
            hard.append(f"chroma asset {node.get('id')} magenta residue {residue}px != 0")

    # (d) density budget (warn)
    per_panel: dict[str, int] = {}
    for node in nodes:
        pid = node.get("panel")
        if pid and node.get("id") != "E00":
            per_panel[pid] = per_panel.get(pid, 0) + 1
    for pid, count in per_panel.items():
        if count > 7:
            warn.append(f"panel {pid} density {count} > 7 perceptual blocks")

    # (e) every non-instance raster node has an asset (hard)
    for node in nodes:
        if node.get("instance_of") or (node.get("gen") or {}).get("strategy") != "raster":
            continue
        rel = (node.get("gen") or {}).get("asset")
        if not (rel and (vp_dir(root) / rel).exists()):
            hard.append(f"raster node {node.get('id')} has no rendered asset")

    # (f) assembled.svg parses and references no missing files
    assembled = cdir / f"{fid}.assembled.svg"
    if not assembled.exists():
        warn.append(f"{assembled.name} not found (run assemble)")
    else:
        try:
            from lxml import etree
            # huge_tree: premium-grade assets embed multi-MB data URIs whose single text
            # node exceeds libxml2's default huge-input guard; the guard is for untrusted
            # input, and this file is our own build product.
            atree = etree.parse(str(assembled), etree.XMLParser(huge_tree=True))
            for el in atree.getroot().iter():
                for attr in ("href", "{http://www.w3.org/1999/xlink}href"):
                    val = el.get(attr)
                    if val and not val.startswith(("data:", "#", "http")):
                        if not (assembled.parent / val).exists():
                            hard.append(f"assembled.svg references missing file: {val}")
        except Exception as exc:  # noqa: BLE001
            hard.append(f"assembled.svg does not parse: {exc}")

    report = {"figure_id": fid, "checked_at": utc_now(),
              "result": "FAIL" if hard else "PASS",
              "hard_failures": hard, "warnings": warn,
              "counts": {"texts": len(texts), "nodes": len(nodes), "panels": len(per_panel)}}
    write_json(cdir / "qa-report.json", report)
    print(f"qa {fid}: {'FAIL' if hard else 'PASS'}  hard={len(hard)} warnings={len(warn)}")
    for item in hard:
        print(f"  HARD  {item}")
    for item in warn:
        print(f"  warn  {item}")
    return 1 if hard else 0


# ---------------------------------------------------------------- self-test

def _synthetic_fsg() -> dict[str, Any]:
    fsg = scaffold_fsg("F02", "Compile policy text into executable, auditable checks.",
                       "linear-flow", DOUBLE_MM, DEFAULT_H_MM, "double")
    # M4c (§7): P01 is a 3-deep chain with a primary hub, so its physical slot-height floors
    # (34 + 20 + 20 mm plus header/text/pads) need a taller canvas than the 90 mm default.
    # 150 mm is the smallest round height at which the summed needs (~148 mm) fit — the spec's
    # own remedy (raise height_mm, never weaken the floors) dogfooded on the synthetic.
    fsg["figure"]["canvas"]["height_mm"] = 150.0
    fsg["style"]["venue_guide"] = "style/aaai.md"
    fsg["style"]["anchor_asset"] = "assets/F02.E00.S01.G01.png"
    fsg["style"]["render_grade"] = "flat"   # legacy asserts pin the flat template
    fsg["panels"] = [
        {"id": "P01", "role": "input side", "order": 1},
        {"id": "P02", "role": "output side", "order": 2},
    ]

    def node(nid, panel, ntype, semantic, saliency, rhythm, text_strategy="none",
             strategy="raster", instance_of=None, representation="physical-schematic",
             viz_idiom=None):
        return {
            "id": nid, "panel": panel, "type": ntype, "semantic": semantic,
            "saliency": saliency, "rhythm_group": rhythm, "instance_of": instance_of,
            "representation": representation, "viz_idiom": viz_idiom,
            "gen": {"strategy": strategy, "text_strategy": text_strategy,
                    "spec_version": "S01", "generation": "G01",
                    "asset": f"assets/{nid}.S01.G01.png"},
        }

    fsg["nodes"] = [
        node("E00", "P01", "module", "style anchor reference", "ambient", "G0"),
        node("E01", "P01", "module", "a compiler that turns policy text into executable checks",
             "primary", "G1"),
        node("E02", "P01", "data", "the raw policy text corpus fed in", "secondary", "G1",
             text_strategy="chroma"),
        node("E03", "P01", "module", "an executable check list produced by the compiler",
             "secondary", "G2", representation="math-viz",
             viz_idiom="a compact matrix of pass/fail cells, one row per policy clause"),
        node("E04", "P02", "actor", "an auditor reviewing the flagged outputs", "secondary", "G2"),
        node("E05", "P02", "plot-slot", "a coverage bar-chart slot for results", "ambient", "G3",
             strategy="plot"),
        node("E06", "P02", "data", "reused policy text corpus", "ambient", "G3",
             strategy="reuse", instance_of="E02"),
    ]
    def edge(lid, etype, frm, to, scope):
        return {"id": lid, "type": etype, "from": frm, "to": to, "scope": scope,
                "routing": "ortho", "render": "code", "label_ref": None}

    def text(tid, anchor, role, content):
        return {"id": tid, "anchor": anchor, "role": role, "content": content,
                "size_pt": 8, "weight": "regular", "max_chars": 25}

    fsg["edges"] = [edge("L01", "dataflow", "E01", "E03", "in_panel"),
                    edge("L02", "dataflow", "E02", "E01", "in_panel"),
                    edge("L03", "stage-flow", "E03", "E04", "cross_panel")]
    fsg["texts"] = [text("T01", "E01", "label", "Contract Compiler"),
                    text("T02", "E03", "label", "Check List"),
                    text("T03", "E04", "label", "Auditor"),
                    text("T04", "P02", "panel-title", "Outputs")]
    return fsg


def _make_test_png(path: Path) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (220, 160), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    magenta = (255, 0, 255)
    for x0, x1 in ((20, 70), (85, 135), (150, 200)):      # row 1 -> one zone
        draw.rectangle([x0, 30, x1, 55], fill=magenta)
    for x0, x1 in ((40, 90), (110, 170)):                 # row 2 -> one zone
        draw.rectangle([x0, 110, x1, 135], fill=magenta)
    draw.rectangle([20, 75, 60, 95], fill=(0, 114, 178))  # blue: must NOT be keyed
    img.save(str(path))


def _make_bar_row_png(path: Path, bars: int, bar_w: int, gap: int) -> None:
    """One row of solid-magenta bars with fixed width/gap (key-merge fixtures)."""
    from PIL import Image, ImageDraw
    width = 40 + bars * bar_w + (bars - 1) * gap
    img = Image.new("RGB", (width, 200), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    x = 20
    for _ in range(bars):
        draw.rectangle([x, 60, x + bar_w, 120], fill=(255, 0, 255))
        x += bar_w + gap
    img.save(str(path))


def command_self_test(_args: argparse.Namespace) -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    base = Path(SELFTEST_DIR)
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    proj = base / "proj"
    proj.mkdir()
    fid = "F02"
    path = fsg_path(proj, fid)

    def run(func, **kw) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return func(argparse.Namespace(**kw))

    # 1. init
    rc = run(command_fsg_init, project_root=str(proj), figure_id=fid,
             message="seed", archetype="linear-flow", canvas_mm=None, column="double")
    check("init", rc == 0 and path.exists(), f"rc={rc}")

    # 2. inject synthetic fsg
    write_json(path, _synthetic_fsg())
    check("inject-synthetic", path.exists())

    # 3. validate: pass with zero warnings
    errs, warns = validate_fsg(load_json(path))
    check("validate-pass-no-warnings", not errs and not warns,
          f"errors={len(errs)} warnings={len(warns)} {errs} {warns}")

    # 4. freeze
    rc = run(command_fsg_freeze, project_root=str(proj), figure_id=fid)
    frozen = load_json(path)["figure"]["frozen_sha"]
    check("freeze", rc == 0 and bool(frozen), f"rc={rc} sha={str(frozen)[:12]}")

    # 5. compile + prompt assertions
    rc = run(command_compile, project_root=str(proj), figure_id=fid, force=False)
    prompts_dir = compile_dir(proj, fid) / "prompts"
    before = {p.name: p.read_text(encoding="utf-8") for p in prompts_dir.glob("*.md")}
    expected = {f"{fid}.E00.S01.md", f"{fid}.E01.S01.md", f"{fid}.E02.S01.md",
                f"{fid}.E03.S01.md", f"{fid}.E04.S01.md", f"{fid}.E05.S01.md"}
    check("compile", rc == 0 and set(before) == expected, f"rc={rc} files={sorted(before)}")
    check("instance_of-no-prompt", f"{fid}.E06.S01.md" not in before, "E06 must be skipped")
    allcaps = all(FLAT_LINE in t and "PALETTE:" in t for t in before.values())
    e01_ok = "NO TEXT" in before.get(f"{fid}.E01.S01.md", "")
    e02_ok = "MAGENTA" in before.get(f"{fid}.E02.S01.md", "")
    check("prompt-allcaps-blocks", allcaps and e01_ok and e02_ok,
          f"flat/palette={allcaps} e01_notext={e01_ok} e02_magenta={e02_ok}")
    no_hex = not any(HEX_RE.search(t) for t in before.values())
    check("prompt-no-hex", no_hex)
    e03_txt = before.get(f"{fid}.E03.S01.md", "")
    e01_txt = before.get(f"{fid}.E01.S01.md", "")
    check("prompt-representation-typing",
          "statistics textbooks" in e03_txt and "pass/fail cells" in e03_txt
          and "metaphor" not in e03_txt and "statistics textbooks" not in e01_txt,
          "math-viz node must get the viz template, physical node must not")
    prem_style = dict(load_json(path).get("style") or {})
    prem_style["render_grade"] = "premium"
    pnode = {"id": "E01", "type": "module", "semantic": "a threshold comparison",
             "saliency": "secondary", "rhythm_group": "G1", "representation": "math-viz",
             "viz_idiom": "a bar crossed by a dashed threshold line",
             "gen": {"text_strategy": "none"}}
    ptxt = build_node_prompt("F99", pnode, prem_style, "S01")
    check("prompt-render-grade-premium",
          "PREMIUM RENDERING" in ptxt and FLAT_LINE not in ptxt
          and "dimensional physical object" in ptxt,
          "premium grade must swap in the premium render + premium math-viz blocks")
    checklist = load_json(compile_dir(proj, fid) / "checklist.json")
    check("checklist-magenta-flag", checklist.get("magenta_residue_must_be_zero") is True)
    # This linear-flow, 2-panel synthetic now takes the band-grid path by default (spec §1):
    # layout.bands are written, no dot is required, and no skeleton.svg is produced. The
    # graphviz retreat path keeps its own coverage in the prefer="graphviz" variant below.
    lay5 = load_json(path).get("layout") or {}
    check("compile-bandgrid-default",
          lay5.get("solver") == "band-grid" and bool(lay5.get("bands"))
          and not (compile_dir(proj, fid) / "skeleton.svg").exists(),
          f"solver={lay5.get('solver')} bands={len(lay5.get('bands') or [])} "
          f"slots={len(lay5.get('bboxes') or {})}")

    # 6. mutate one node semantic -> only its S bumps, only its file changes
    fsg = load_json(path)
    for node in fsg["nodes"]:
        if node["id"] == "E03":
            node["semantic"] = "a MUTATED executable check ledger with new phrasing"
    write_json(path, fsg)
    rc = run(command_compile, project_root=str(proj), figure_id=fid, force=False)
    after = {p.name: p.read_text(encoding="utf-8") for p in prompts_dir.glob("*.md")}
    new_files = set(after) - set(before)
    others_unchanged = all(after.get(n) == before[n] for n in before if not n.startswith(f"{fid}.E03."))
    e03_sv = next((n.get("gen", {}).get("spec_version") for n in load_json(path)["nodes"]
                   if n["id"] == "E03"), None)
    check("mutate-only-e03-bumps", rc == 0 and new_files == {f"{fid}.E03.S02.md"}
          and others_unchanged and e03_sv == "S02",
          f"new={sorted(new_files)} others_same={others_unchanged} e03={e03_sv}")

    # 7. break fsg (dangling anchor) -> hard error
    fsg = load_json(path)
    fsg["texts"][0]["anchor"] = "E99"
    write_json(path, fsg)
    errs2, _ = validate_fsg(fsg)
    check("break-dangling-anchor", bool(errs2) and any("anchor" in e for e in errs2),
          f"errors={errs2}")

    # 8. key on a synthesized PNG (SKIPPED if Pillow absent)
    try:
        import PIL  # noqa: F401
        have_pil = True
    except ImportError:
        have_pil = False
    if have_pil:
        test_png = base / "test.png"
        _make_test_png(test_png)
        rc = run(command_key, in_png=str(test_png), out_clean=str(base / "clean.png"),
                 out_mask=str(base / "mask.png"), report=str(base / "zones.json"))
        zones = load_json(base / "zones.json")
        check("key-zone-count", rc == 0 and zones.get("label_zones") == 2,
              f"rc={rc} zones={zones.get('label_zones')} residue={zones.get('residue_after_clean_px')}")
        check("key-outputs-exist", (base / "clean.png").exists() and (base / "mask.png").exists())
    else:
        check("key-zone-count", True, "SKIPPED (Pillow absent)")
        check("key-outputs-exist", True, "SKIPPED (Pillow absent)")

    # 9. ledger renders
    rc = run(command_ledger, project_root=str(proj), figure_id=None)
    ledger_md = fsg_dir(proj) / "FIGURES.md"
    ledger_text = ledger_md.read_text(encoding="utf-8") if ledger_md.exists() else ""
    check("ledger", rc == 0 and "F02" in ledger_text and "E01" in ledger_text, f"rc={rc}")

    # ================= M1b battery (mock only; never invokes real codex) =========
    for env_key in ("AR_FS_MOCK_GEN", "AR_FS_MOCK_REFINE_VERDICT"):
        os.environ.pop(env_key, None)
    # step 7 left a dangling anchor on disk + step 6 bumped E03 -> restore a clean base
    write_json(path, _synthetic_fsg())
    run(command_fsg_freeze, project_root=str(proj), figure_id=fid)
    run(command_compile, project_root=str(proj), figure_id=fid, force=False)
    adir = assets_dir(proj)

    # 16. prompt polish: 'an AAAI' article + style tokens not doubled verbatim
    e01_prompt = (compile_dir(proj, fid) / "prompts" / f"{fid}.E01.S01.md").read_text(encoding="utf-8")
    low = e01_prompt.lower()
    check("prompt-polish-article-and-tokens",
          "an AAAI" in e01_prompt and "a AAAI" not in e01_prompt
          and low.count("bauhaus") <= 1 and low.count("swiss") <= 1,
          f"article={'an AAAI' in e01_prompt} bauhaus={low.count('bauhaus')} swiss={low.count('swiss')}")

    # 29. P6a: the chroma prompt block carries the hardened RECTANGULAR-bars-only line
    e02_prompt = (compile_dir(proj, fid) / "prompts" / f"{fid}.E02.S01.md").read_text(encoding="utf-8")
    check("chroma-prompt-hard-line",
          "RECTANGULAR" in e02_prompt and "never lines, frames, ribbons" in e02_prompt,
          f"rect={'RECTANGULAR' in e02_prompt} strokes={'never lines, frames, ribbons' in e02_prompt}")

    # 30. P5: the dot emission (graphviz retreat path) sizes the primary node larger than a
    # secondary one. Band-grid is the default now and writes no skeleton.dot, so exercise the
    # emitter directly on the frozen fsg rather than reading a file that no longer exists.
    dot_txt = emit_skeleton_dot(load_json(path))

    def _dot_width(el: str):
        m = re.search(rf'"{el}"\s*\[[^\]]*width=([\d.]+)', dot_txt)
        return float(m.group(1)) if m else None

    w_primary, w_secondary = _dot_width("E01"), _dot_width("E03")
    check("saliency-node-size",
          w_primary is not None and w_secondary is not None and w_primary > w_secondary,
          f"primary={w_primary} secondary={w_secondary}")

    # 31. P6b: validate warns when >50% of counted elements use text_strategy=chroma
    chroma_fsg = _synthetic_fsg()
    for cn in chroma_fsg["nodes"]:
        if cn["id"] in ("E01", "E03"):
            cn["gen"]["text_strategy"] = "chroma"  # E02 already chroma -> 3 of 5 elements
    _, cwarns = validate_fsg(chroma_fsg)
    check("chroma-over-50-warning", any("chroma" in w and ">50%" in w for w in cwarns),
          f"warnings={[w for w in cwarns if 'chroma' in w]}")

    # ===================== M4 band-grid battery (spec §5) ====================
    def _bnode(nid, panel, saliency="secondary", rhythm=None, strategy="raster",
               instance_of=None, text_strategy="none"):
        return {"id": nid, "panel": panel, "type": "module", "semantic": f"node {nid}",
                "saliency": saliency, "rhythm_group": rhythm, "instance_of": instance_of,
                "representation": "physical-schematic", "viz_idiom": None,
                "gen": {"strategy": strategy, "text_strategy": text_strategy,
                        "spec_version": "S01", "generation": "G01", "asset": None}}

    def _bedge(lid, frm, to, lane=None, etype="dataflow"):
        e = {"id": lid, "type": etype, "from": frm, "to": to, "scope": "in_panel",
             "routing": "ortho", "render": "code", "label_ref": None}
        if lane is not None:
            e["lane"] = lane
        return e

    def _bfsg(nodes, edges=None, texts=None, panels=None, column="double", height=DEFAULT_H_MM):
        f = scaffold_fsg("F03", "band grid probe", "linear-flow",
                         DOUBLE_MM if column == "double" else SINGLE_MM, height, column)
        f["style"]["venue_guide"] = "style/aaai.md"
        f["panels"] = panels or [{"id": "P01", "role": "01 ALPHA - lead band", "order": 1}]
        f["nodes"] = nodes
        f["edges"] = edges or []
        f["texts"] = texts or []
        f["layout"].update(compute_band_layout(f))
        return f

    def _cx(box):
        return (box[0] + box[2]) / 2.0

    # 40. band layout is deterministic: identical inputs -> identical bboxes + bands
    det = _synthetic_fsg()
    la, lb = compute_band_layout(det), compute_band_layout(det)
    check("band-layout-deterministic",
          la == lb and la["solver"] == "band-grid" and bool(la["bands"]),
          f"equal={la == lb} bands={len(la['bands'])}")

    # 41. fan-in target (>=2 in-band in-edges) sinks to its own centered hub layer
    hub = _bfsg([_bnode("E01", "P01"), _bnode("E02", "P01"), _bnode("E03", "P01")],
                edges=[_bedge("L01", "E01", "E03"), _bedge("L02", "E02", "E03")])
    hbx = hub["layout"]["bboxes"]
    check("band-fanin-hub-centered",
          abs(_cx(hbx["E03"]) - 50.0) < 0.5
          and hbx["E03"][1] > hbx["E01"][1] + 1 and hbx["E03"][1] > hbx["E02"][1] + 1
          and abs(hbx["E01"][1] - hbx["E02"][1]) < 0.5,
          f"cx={_cx(hbx['E03']):.2f} e3top={hbx['E03'][1]:.1f} e1top={hbx['E01'][1]:.1f}")

    # 42. same rhythm_group twins on one layer -> symmetric left/right columns
    twin = _bfsg([_bnode("E01", "P01", rhythm="G1"), _bnode("E02", "P01", rhythm="G1")])
    tbx = twin["layout"]["bboxes"]
    check("band-twin-symmetry",
          abs(_cx(tbx["E01"]) + _cx(tbx["E02"]) - 100.0) < 0.5
          and abs((tbx["E01"][2] - tbx["E01"][0]) - (tbx["E02"][2] - tbx["E02"][0])) < 0.5,
          f"cx1={_cx(tbx['E01']):.2f} cx2={_cx(tbx['E02']):.2f}")

    # 43. ambient node retreats into the right margin strip (out of the main flow)
    amb = _bfsg([_bnode("E01", "P01", saliency="secondary"),
                 _bnode("E02", "P01", saliency="ambient")])
    abx = amb["layout"]["bboxes"]
    check("band-ambient-right-retreat",
          _cx(abx["E02"]) > 80.0 and _cx(abx["E02"]) > _cx(abx["E01"]) + 20,
          f"main_cx={_cx(abx['E01']):.2f} ambient_cx={_cx(abx['E02']):.2f}")

    # 44. panel-anchored (P##) text lands inside that band's header zone; validate is clean
    hdr = _bfsg([_bnode("E01", "P01")],
                texts=[{"id": "T01", "anchor": "P01", "role": "panel-title", "content": "Lead",
                        "size_pt": 8, "weight": "regular", "max_chars": 25}])
    hdr_items = {it["id"]: it for it in _bandgrid_text_layout(hdr)}
    _, hwarn = validate_fsg(hdr)
    check("band-panel-anchor-in-header",
          "T01" in hdr_items and _within(hdr_items["T01"]["box"],
                                         hdr["layout"]["bands"][0]["header_bbox"], tol=1.0)
          and not any("anchor" in w for w in hwarn),
          f"item={hdr_items.get('T01', {}).get('box')}")

    # 45. qa hard-fails when a slot is shoved outside its band_bbox (spec §3 overflow)
    ov_proj = base / "ov"
    ov_proj.mkdir(exist_ok=True)
    run(command_fsg_init, project_root=str(ov_proj), figure_id=fid, message="seed",
        archetype="linear-flow", canvas_mm=None, column="double")
    ov_fsg = _synthetic_fsg()
    ov_fsg["layout"].update(compute_band_layout(ov_fsg))
    ov_fsg["layout"]["bboxes"]["E01"] = [95.0, 95.0, 140.0, 99.0]  # past the P01 band_bbox
    write_json(fsg_path(ov_proj, fid), ov_fsg)
    rc = run(command_qa, project_root=str(ov_proj), figure_id=fid)
    ov_report = load_json(compile_dir(ov_proj, fid) / "qa-report.json")
    check("band-qa-overflow-hard",
          rc == 1 and any("overflows band" in h for h in ov_report.get("hard_failures", [])),
          f"rc={rc} hard={[h for h in ov_report.get('hard_failures', []) if 'overflow' in h]}")

    # 46. the 0.6-line stagger is load-bearing: adjacent long labels collide WITHOUT it and
    # clear the 0.5-area gate WITH it (spec §2 — the estimator mirrors the renderer's stagger)
    big = "X" * 50
    stg = _bfsg([_bnode("E01", "P01"), _bnode("E02", "P01")],
                texts=[{"id": "T01", "anchor": "E01", "role": "label", "content": big,
                        "size_pt": 8, "weight": "bold", "max_chars": 999},
                       {"id": "T02", "anchor": "E02", "role": "label", "content": big,
                        "size_pt": 8, "weight": "bold", "max_chars": 999}])
    on = {it["id"]: it["box"] for it in _bandgrid_text_layout(stg, stagger=True)}
    off = {it["id"]: it["box"] for it in _bandgrid_text_layout(stg, stagger=False)}
    a_off = _bbox_overlap_area(off["T01"], off["T02"])
    a_on = _bbox_overlap_area(on["T01"], on["T02"])
    check("band-stagger-kills-collision", a_off > 0.5 and a_on <= 0.5 and a_on < a_off,
          f"off={a_off:.3f} on={a_on:.3f}")

    # 47. layout.prefer='graphviz' forces the dot retreat path (keeps graphviz coverage)
    gv_proj = base / "gv"
    gv_proj.mkdir(exist_ok=True)
    run(command_fsg_init, project_root=str(gv_proj), figure_id=fid, message="seed",
        archetype="linear-flow", canvas_mm=None, column="double")
    gv_fsg = _synthetic_fsg()
    gv_fsg["layout"]["prefer"] = "graphviz"
    write_json(fsg_path(gv_proj, fid), gv_fsg)
    run(command_fsg_freeze, project_root=str(gv_proj), figure_id=fid)
    rc = run(command_compile, project_root=str(gv_proj), figure_id=fid, force=False)
    gv_lay = load_json(fsg_path(gv_proj, fid)).get("layout") or {}
    gv_dot = (compile_dir(gv_proj, fid) / "skeleton.dot").exists()
    gv_has_dot = find_dot() is not None
    check("band-prefer-graphviz-retreat",
          rc == 0 and gv_dot and "bands" not in gv_lay
          and (gv_lay.get("solver") == "graphviz-dot" if gv_has_dot else True),
          f"rc={rc} dot={gv_dot} solver={gv_lay.get('solver')} bands={'bands' in gv_lay}")

    # 48. edge lane: a known palette key validates clean; an unknown key warns and assemble
    # falls back to neutral (validate accepts the field either way)
    lane_ok = _bfsg([_bnode("E01", "P01"), _bnode("E02", "P01")],
                    edges=[_bedge("L01", "E01", "E02", lane="accent")])
    lane_bad = copy.deepcopy(lane_ok)
    lane_bad["edges"][0]["lane"] = "nope"
    _, w_ok = validate_fsg(lane_ok)
    _, w_bad = validate_fsg(lane_bad)
    check("band-edge-lane-warns-unknown",
          not any("lane" in w for w in w_ok) and any("lane" in w for w in w_bad),
          f"ok={[w for w in w_ok if 'lane' in w]} bad={[w for w in w_bad if 'lane' in w]}")

    # ===================== M4b/M4c battery (spec §6.5 + §7.4) ================
    # 50. §7.2/§7.4: summed physical slot floors that don't fit make compile HARD-fail and
    # print a suggested taller canvas height; the suggestion is within [1.05x, 3x] of the too
    # -short height and recompiling at exactly that height passes. Dogfooded on the synthetic
    # at 90 mm (its 3-deep primary band starves) — the exact remedy the tool tells authors to
    # apply. The suggestion is now ceil(canvas_needed), a single addition (no binary search).
    fl_proj = base / "floor"
    fl_proj.mkdir(exist_ok=True)
    run(command_fsg_init, project_root=str(fl_proj), figure_id=fid, message="seed",
        archetype="linear-flow", canvas_mm=None, column="double")
    fl_fsg = _synthetic_fsg()
    fl_fsg["figure"]["canvas"]["height_mm"] = 90.0          # too short for the floors
    fl_path = fsg_path(fl_proj, fid)
    write_json(fl_path, fl_fsg)
    run(command_fsg_freeze, project_root=str(fl_proj), figure_id=fid)
    fl_err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(fl_err):
        rc_fl = command_compile(argparse.Namespace(project_root=str(fl_proj), figure_id=fid, force=False))
    fl_msg = fl_err.getvalue()
    fl_match = re.search(r"height_mm to >= (\d+)", fl_msg)
    suggested_h = float(fl_match.group(1)) if fl_match else 0.0
    fl_fixed = load_json(fl_path)
    fl_fixed["figure"]["canvas"]["height_mm"] = suggested_h
    write_json(fl_path, fl_fixed)
    rc_fl2 = run(command_compile, project_root=str(fl_proj), figure_id=fid, force=False)
    fl_lay = (load_json(fl_path).get("layout") or {})
    check("band-floor-hard-error-suggests-height",
          rc_fl == 1 and "floors do not fit" in fl_msg and fl_match is not None
          and 1.05 * 90.0 <= suggested_h <= 3.0 * 90.0        # spec §7.4: sane, not 30x runaway
          and rc_fl2 == 0 and fl_lay.get("solver") == "band-grid",
          f"rc={rc_fl} suggested={suggested_h} in[{1.05 * 90:.0f},{3 * 90:.0f}] recompile={rc_fl2}")

    # 51. §6.3: three graded stroke classes, ordered lane(0.45%) > neutral(0.30%) > diagnostic
    # (0.16%), and the diagnostic edge is dashed at 50% opacity. Ports fan out (+-20% slot width).
    grade = _bfsg([_bnode("E01", "P01"), _bnode("E02", "P01")],
                  edges=[_bedge("L01", "E01", "E02", lane="accent"),
                         _bedge("L02", "E01", "E02"),
                         _bedge("L03", "E01", "E02", etype="diagnostic")])
    gg = {geom["id"]: geom for geom in _band_edge_geometry(grade)}
    src_ports = sorted(round(gg[e]["points"][0][0], 1) for e in ("L01", "L02", "L03"))
    check("band-edge-three-class-widths",
          gg["L01"]["cls"] == "lane" and gg["L02"]["cls"] == "neutral"
          and gg["L03"]["cls"] == "diagnostic"
          and gg["L01"]["width"] > gg["L02"]["width"] > gg["L03"]["width"]
          and gg["L03"]["dashed"] and abs(gg["L03"]["opacity"] - 0.5) < 1e-6
          and src_ports[0] < src_ports[1] < src_ports[2],  # fanned-out ports
          f"w={[round(gg[e]['width'], 2) for e in ('L01', 'L02', 'L03')]} "
          f"dash={gg['L03']['dashed']} ports={src_ports}")

    # 52. §6.4: no vertical/horizontal segment crosses a non-endpoint slot. A skip diagnostic
    # from band 1 to band 3 would run straight through band 2's centered slot; the router sends
    # it down the right-margin channel instead. Assert 0 crossings for EVERY edge, and that the
    # naive elbow WOULD have crossed (the avoidance is load-bearing, not luck).
    _ch3 = 130.0                                          # 3 bands (~116 mm needed) do not fit 90 mm
    cross = _bfsg([_bnode("E01", "P01"), _bnode("E02", "P02"), _bnode("E03", "P03")],
                  edges=[_bedge("L01", "E01", "E02"),
                         _bedge("D1", "E01", "E03", etype="diagnostic")],
                  panels=[{"id": "P01", "role": "01 A - a", "order": 1},
                          {"id": "P02", "role": "02 B - b", "order": 2},
                          {"id": "P03", "role": "03 C - c", "order": 3}],
                  height=_ch3)
    _cbx = cross["layout"]["bboxes"]
    _wpx = BAND_CANVAS_W_PX
    _hpx = _wpx * (_ch3 / DOUBLE_MM)                       # px space matches the fixture height
    _rects = {nid: (b[0] / 100 * _wpx, b[1] / 100 * _hpx, b[2] / 100 * _wpx, b[3] / 100 * _hpx)
              for nid, b in _cbx.items()}
    _eends = {e["id"]: (e["from"], e["to"]) for e in cross["edges"]}

    def _poly_hits(points, rect):
        return any(_seg_hits_rect(x1, y1, x2, y2, rect)
                   for (x1, y1), (x2, y2) in zip(points, points[1:]))

    total_cross = 0
    diag_pts = None
    for geom in _band_edge_geometry(cross):
        frm, to = _eends[geom["id"]]
        if geom["id"] == "D1":
            diag_pts = geom["points"]
        for nid, rect in _rects.items():
            if nid not in (frm, to) and _poly_hits(geom["points"], rect):
                total_cross += 1
    _sx, _sy = diag_pts[0]
    _tx, _ty = diag_pts[-1]
    naive = [(_sx, _sy), (_sx, (_sy + _ty) / 2), (_tx, (_sy + _ty) / 2), (_tx, _ty)]
    naive_would_cross = _poly_hits(naive, _rects["E02"])
    check("band-port-no-slot-crossing",
          total_cross == 0 and naive_would_cross,
          f"crossings={total_cross} naive_crosses={naive_would_cross}")

    # 53. §6.1/§7.3: >=65% of each band's content height stays with the asset (slot) zones —
    # the text bars are compressed to just their stack height so the visuals dominate.
    dom = _band_plan_core(_synthetic_fsg(), 150.0)["dominance"]
    check("band-asset-dominance-65", dom >= BAND_ASSET_DOMINANCE, f"dominance={dom:.3f}")

    if have_pil:
        os.environ["AR_FS_MOCK_GEN"] = "1"
        # 17. gen --all-pending (mock): raster elements get assets + sidecars; plot/instance skipped
        rc = run(command_gen, project_root=str(proj), figure_id=fid, element=None,
                 all_pending=True, timeout_s=600)
        gen_pngs = sorted(p.name for p in adir.glob(f"{fid}.*.png") if ".cleaned." not in p.name)
        sidecars = sorted(adir.glob(f"{fid}.*.png.json"))
        want = [f"{fid}.E0{i}.S01.G01.png" for i in range(5)]  # E00..E04
        e01_asset = next((n.get("gen", {}).get("asset") for n in load_json(path)["nodes"]
                          if n["id"] == "E01"), None)
        check("gen-all-pending-mock", rc == 0 and gen_pngs == want and len(sidecars) == 5
              and e01_asset == "assets/F02.E01.S01.G01.png",
              f"rc={rc} pngs={gen_pngs} sidecars={len(sidecars)} e01={e01_asset}")

        # 18. gen --element (forced): G increments, G01 preserved (never overwrite)
        rc = run(command_gen, project_root=str(proj), figure_id=fid, element="E01",
                 all_pending=False, timeout_s=600)
        e01_gens = sorted(p.name for p in adir.glob(f"{fid}.E01.S01.G*.png"))
        check("gen-g-increments", rc == 0 and e01_gens == [f"{fid}.E01.S01.G01.png",
                                                           f"{fid}.E01.S01.G02.png"],
              f"rc={rc} gens={e01_gens}")

        # 19. refine PASS spends nothing (no new G asset)
        os.environ["AR_FS_MOCK_REFINE_VERDICT"] = "PASS"
        before = len(list(adir.glob(f"{fid}.E01.S01.G*.png")))
        rc = run(command_refine, project_root=str(proj), figure_id=fid, element="E01",
                 max_iters=2, timeout_s=900)
        after = len(list(adir.glob(f"{fid}.E01.S01.G*.png")))
        check("refine-pass-spends-nothing", rc == 0 and after == before,
              f"rc={rc} before={before} after={after}")

        # 20. refine EDITED adds exactly one G (+ sidecar tagged refine)
        os.environ["AR_FS_MOCK_REFINE_VERDICT"] = "EDITED"
        before = len(list(adir.glob(f"{fid}.E01.S01.G*.png")))
        rc = run(command_refine, project_root=str(proj), figure_id=fid, element="E01",
                 max_iters=1, timeout_s=900)
        after = len(list(adir.glob(f"{fid}.E01.S01.G*.png")))
        newest = f"{fid}.E01.S01.G{after:02d}.png"
        side = load_json(adir / f"{newest}.json") if (adir / f"{newest}.json").exists() else {}
        check("refine-edited-adds-g", rc == 0 and after == before + 1
              and side.get("kind") == "refine" and "verdict" in side,
              f"rc={rc} before={before} after={after} kind={side.get('kind')}")
        os.environ.pop("AR_FS_MOCK_REFINE_VERDICT", None)

        # 21. assemble (band-grid): the template renders assembled.svg directly — no
        # skeleton.svg, no dot required (spec §2/§3).
        rc = run(command_assemble, project_root=str(proj), figure_id=fid)
        asm = compile_dir(proj, fid) / f"{fid}.assembled.svg"
        skel = compile_dir(proj, fid) / "skeleton.svg"
        check("assemble-svg-produced",
              rc == 0 and asm.exists() and not skel.exists()
              and (load_json(path).get("layout") or {}).get("solver") == "band-grid",
              f"rc={rc} skeleton={skel.exists()} assembled={asm.exists()}")

        # 22. assembled.svg carries embedded data URIs + the expected vector text
        asm_txt = asm.read_text(encoding="utf-8") if asm.exists() else ""
        check("assemble-datauri-and-text",
              "data:image/png;base64," in asm_txt and "Contract Compiler" in asm_txt
              and "Auditor" in asm_txt and "Outputs" in asm_txt,
              f"datauri={'data:image/png;base64,' in asm_txt}")

        # 23. chroma asset keyed before embedding: cleaned PNG + zones stashed, zero magenta
        cleaned = adir / f"{fid}.E02.S01.G01.cleaned.png"
        zones = adir / f"{fid}.E02.S01.G01.zones.json"
        residue = _measure_residue(cleaned, already_clean=True) if cleaned.exists() else -1
        check("assemble-chroma-cleaned-zero-magenta",
              cleaned.exists() and zones.exists() and residue == 0
              and "ff00ff" not in asm_txt.lower(),
              f"cleaned={cleaned.exists()} zones={zones.exists()} residue={residue}")

        # 24. qa green on the good build
        rc = run(command_qa, project_root=str(proj), figure_id=fid)
        qa_report = load_json(compile_dir(proj, fid) / "qa-report.json")
        check("qa-green", rc == 0 and qa_report.get("result") == "PASS"
              and not qa_report.get("hard_failures"),
              f"rc={rc} hard={qa_report.get('hard_failures')}")

        # 54. §6.2: every embedded asset fills >=0.85 of its slot's binding dimension (88%
        # contain-fit), and a wide asset expands its width past the slot toward the layer's
        # available cell (a narrow slot is not crammed for symmetry).
        from lxml import etree as _et2
        _NS2 = "{http://www.w3.org/2000/svg}"
        _lay = load_json(path).get("layout") or {}
        _bx = _lay.get("bboxes") or {}
        _cv = (load_json(path).get("figure") or {}).get("canvas") or {}
        _wpx2 = BAND_CANVAS_W_PX
        _hpx2 = _wpx2 * (float(_cv.get("height_mm") or DEFAULT_H_MM)
                         / float(_cv.get("width_mm") or DOUBLE_MM))
        _imgs = list(_et2.parse(str(asm)).getroot().iter(_NS2 + "image"))
        _fills = [float(im.get("data-fill") or 0.0) for im in _imgs]
        _expanded = 0
        for im in _imgs:
            _b = _bx.get(im.get("data-slot"))
            if _b and float(im.get("width") or 0.0) > (_b[2] - _b[0]) / 100.0 * _wpx2 + 0.5:
                _expanded += 1
        check("band-asset-fill-88",
              len(_imgs) >= 4 and bool(_fills) and all(f >= 0.85 for f in _fills)
              and _expanded >= 1,
              f"n={len(_imgs)} min_fill={min(_fills) if _fills else None} expanded={_expanded}")

        # 25. qa hard-fails on an injected 4pt text (undersized < 5pt floor)
        broke = load_json(path)
        broke["texts"][0]["size_pt"] = 4
        write_json(path, broke)
        rc = run(command_qa, project_root=str(proj), figure_id=fid)
        qa_report = load_json(compile_dir(proj, fid) / "qa-report.json")
        check("qa-hard-fail-undersized",
              rc == 1 and any("< 5pt" in h for h in qa_report.get("hard_failures", [])),
              f"rc={rc} hard={qa_report.get('hard_failures')}")

        # 26. key default horizontal merge: one row of near-bars collapses to a single zone
        merge_img = base / "merge_default.png"
        _make_bar_row_png(merge_img, bars=3, bar_w=300, gap=70)
        rc = run(command_key, in_png=str(merge_img), out_clean=str(base / "mc.png"),
                 out_mask=str(base / "mm.png"), report=str(base / "mz.json"),
                 merge_gap_px=None, expect=None)
        mzones = load_json(base / "mz.json").get("label_zones")
        check("key-default-horizontal-merge", rc == 0 and mzones == 1, f"rc={rc} zones={mzones}")

        # 27. key --expect greedily merges widely-spaced same-row zones down to N
        expect_img = base / "merge_expect.png"
        _make_bar_row_png(expect_img, bars=4, bar_w=100, gap=220)
        rc = run(command_key, in_png=str(expect_img), out_clean=str(base / "ec.png"),
                 out_mask=str(base / "em.png"), report=str(base / "ez.json"),
                 merge_gap_px=None, expect=2)
        ezones = load_json(base / "ez.json").get("label_zones")
        check("key-expect-merge", rc == 0 and ezones == 2, f"rc={rc} zones={ezones}")

        # 28. real probe (task acceptance): --expect 4 -> exactly 4 zones (skipped if absent)
        probe = Path(os.environ.get("FIGURE_STUDIO_PROBE_PNG", "") or "__no_probe__")
        if probe.exists():
            rc = run(command_key, in_png=str(probe), out_clean=str(base / "pc.png"),
                     out_mask=str(base / "pm.png"), report=str(base / "pz.json"),
                     merge_gap_px=None, expect=4)
            pz = load_json(base / "pz.json").get("label_zones")
            check("key-real-probe-expect-4", rc == 0 and pz == 4, f"rc={rc} zones={pz}")
        else:
            check("key-real-probe-expect-4", True, "SKIPPED (probe image absent)")

        # ===================== M3 patch battery (P1-P8) ==========================
        # 32. P8: with E00's asset now on disk, gen auto-attaches it and records ref_anchor
        rc = run(command_gen, project_root=str(proj), figure_id=fid, element="E04",
                 all_pending=False, timeout_s=600)
        e04_new = sorted((p for p in adir.glob(f"{fid}.E04.S01.G*.png")
                          if re.search(r"\.G\d+\.png$", p.name)), key=lambda p: p.name)[-1]
        side04 = load_json(adir / f"{e04_new.name}.json")
        check("ref-anchor-recorded",
              rc == 0 and side04.get("driver", {}).get("ref_anchor") == "assets/F02.E00.S01.G01.png",
              f"ref={side04.get('driver', {}).get('ref_anchor')}")

        # 33. P3a: trim crops a wide uniform margin so embedded content ratio grows
        from PIL import Image as _Im, ImageDraw as _Dw, ImageChops as _Ch

        def _content_ratio(p: Path) -> float:
            a = _Im.open(str(p)).convert("RGB")
            w, h = a.size
            d = _Ch.difference(a, _Im.new("RGB", (w, h), a.getpixel((0, 0)))).split()
            m = _Ch.lighter(_Ch.lighter(d[0], d[1]), d[2]).point(lambda v: 255 if v > TRIM_BORDER_TOL else 0)
            bb = m.getbbox()
            return ((bb[2] - bb[0]) * (bb[3] - bb[1]) / (w * h)) if bb else 0.0

        trim_src = base / "trim_src.png"
        _tim = _Im.new("RGB", (400, 400), (255, 255, 255))
        _Dw.Draw(_tim).rectangle([180, 180, 220, 220], fill=(0, 114, 178))
        _tim.save(str(trim_src))
        trimmed = _trim_to_content(trim_src)
        tw, th = _Im.open(str(trimmed)).size
        before_r, after_r = _content_ratio(trim_src), _content_ratio(trimmed)
        check("assemble-trim-content-ratio",
              str(trimmed) != str(trim_src) and after_r > before_r and tw < 400 and th < 400,
              f"before={before_r:.3f} after={after_r:.3f} dims={tw}x{th}")

        # 34. P3b: assembled.svg no longer paints white placeholder rects behind assets
        from lxml import etree as _etree
        _NS = "{http://www.w3.org/2000/svg}"
        _atree = _etree.parse(str(asm))
        node_polys = sum(1 for grp in _atree.getroot().iter(f"{_NS}g") if grp.get("class") == "node"
                         for _p in grp.findall(f"{_NS}polygon"))
        check("assemble-no-placeholder-rects", node_polys == 0, f"node_polygons={node_polys}")

        # 35. P4: two texts on one anchor stack without overlap (qa estimator agrees)
        stk = load_json(path)
        for t in stk["texts"]:  # undo step 25's injected 4pt floor break before this gate
            t["size_pt"] = 8
        stk["texts"].append({"id": "T90", "anchor": "E01", "role": "label",
                             "content": "Stacked A", "size_pt": 8, "weight": "bold", "max_chars": 25})
        stk["texts"].append({"id": "T91", "anchor": "E01", "role": "annotation",
                             "content": "Stacked note B", "size_pt": 8, "weight": "regular", "max_chars": 25})
        write_json(path, stk)
        rc = run(command_qa, project_root=str(proj), figure_id=fid)
        qa_stack = load_json(compile_dir(proj, fid) / "qa-report.json")
        stack_overlaps = [h for h in qa_stack.get("hard_failures", []) if "overlap" in h]
        check("stacking-no-overlap", rc == 0 and not stack_overlaps,
              f"rc={rc} overlaps={stack_overlaps}")

        # 36. P2: reconcile repairs a deliberately nulled pointer from disk truth
        rec = load_json(path)
        for n in rec["nodes"]:
            if n["id"] == "E01":
                n["gen"]["asset"], n["gen"]["generation"] = None, None
        write_json(path, rec)
        rc = run(command_gen, project_root=str(proj), figure_id=fid, element=None,
                 all_pending=False, timeout_s=600, reconcile=True)
        e01_disk = sorted(p.name for p in adir.glob(f"{fid}.E01.S01.G*.png")
                          if re.search(r"\.G\d+\.png$", p.name))
        want_ptr = f"assets/{e01_disk[-1]}" if e01_disk else None
        e01_ptr = next((n.get("gen", {}).get("asset") for n in load_json(path)["nodes"]
                        if n["id"] == "E01"), None)
        check("reconcile-repairs-nulled-pointer",
              rc == 0 and e01_ptr is not None and e01_ptr == want_ptr,
              f"ptr={e01_ptr} want={want_ptr}")

        # 37. P1: two concurrent mock-gen writers on ONE fsg keep BOTH asset pointers
        import threading
        race = base / "race"
        race.mkdir(exist_ok=True)
        rpath = fsg_path(race, fid)
        run(command_fsg_init, project_root=str(race), figure_id=fid, message="seed",
            archetype="linear-flow", canvas_mm=None, column="double")
        rfsg = _synthetic_fsg()
        for n in rfsg["nodes"]:
            if n["id"] in ("E01", "E03"):
                n["gen"]["asset"], n["gen"]["generation"] = None, None
        write_json(rpath, rfsg)
        run(command_fsg_freeze, project_root=str(race), figure_id=fid)
        run(command_compile, project_root=str(race), figure_id=fid, force=False)

        def _gen_one(el: str) -> None:
            command_gen(argparse.Namespace(project_root=str(race), figure_id=fid, element=el,
                                           all_pending=False, timeout_s=600, reconcile=False,
                                           ref_anchor=None))

        with contextlib.redirect_stdout(io.StringIO()):
            race_threads = [threading.Thread(target=_gen_one, args=(e,)) for e in ("E01", "E03")]
            for rt in race_threads:
                rt.start()
            for rt in race_threads:
                rt.join()
        rptrs = {n["id"]: (n.get("gen") or {}).get("asset") for n in load_json(rpath)["nodes"]}
        both_ptr = bool(rptrs.get("E01")) and bool(rptrs.get("E03"))
        both_file = bool(both_ptr and (vp_dir(race) / rptrs["E01"]).exists()
                         and (vp_dir(race) / rptrs["E03"]).exists())
        check("concurrent-writer-both-pointers", both_ptr and both_file,
              f"E01={rptrs.get('E01')} E03={rptrs.get('E03')}")

        # 38. P7: a forced generation failure writes a .failed.json sidecar + a loud warning
        os.environ["AR_FS_MOCK_FAIL"] = "1"
        e03_before = [int(m.group(1)) for p in adir.glob(f"{fid}.E03.S01.G*.png")
                      for m in [re.search(r"\.G(\d+)\.png$", p.name)] if m]
        next_g = (max(e03_before) + 1) if e03_before else 1
        errbuf = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errbuf):
            rc = command_gen(argparse.Namespace(project_root=str(proj), figure_id=fid, element="E03",
                                                all_pending=False, timeout_s=600, reconcile=False,
                                                ref_anchor=None))
        os.environ.pop("AR_FS_MOCK_FAIL", None)
        failed_sidecar = adir / f"{fid}.E03.S01.G{next_g:02d}.png.failed.json"
        check("k-failure-backfill-sidecar",
              rc == 0 and failed_sidecar.exists() and "!!" in errbuf.getvalue(),
              f"sidecar={failed_sidecar.exists()} warned={'!!' in errbuf.getvalue()}")

        # 49. spec §5 acceptance: F02-type synthetic, full band-grid chain on mock assets,
        # then qa reports ZERO text-collision hard failures (the staggered layout is clean).
        os.environ["AR_FS_MOCK_GEN"] = "1"
        fc = base / "fullchain"
        fc.mkdir(exist_ok=True)
        run(command_fsg_init, project_root=str(fc), figure_id=fid, message="seed",
            archetype="linear-flow", canvas_mm=None, column="double")
        write_json(fsg_path(fc, fid), _synthetic_fsg())
        run(command_fsg_freeze, project_root=str(fc), figure_id=fid)
        run(command_compile, project_root=str(fc), figure_id=fid, force=False)
        run(command_gen, project_root=str(fc), figure_id=fid, element=None,
            all_pending=True, timeout_s=600)
        rc_a = run(command_assemble, project_root=str(fc), figure_id=fid)
        rc_q = run(command_qa, project_root=str(fc), figure_id=fid)
        fc_qa = load_json(compile_dir(fc, fid) / "qa-report.json")
        fc_overlaps = [h for h in fc_qa.get("hard_failures", []) if "overlap" in h]
        check("band-fullchain-qa-zero-text-collision",
              rc_a == 0 and rc_q == 0 and fc_qa.get("result") == "PASS" and not fc_overlaps,
              f"assemble={rc_a} qa={rc_q} overlaps={fc_overlaps}")
    else:
        for name in ("gen-all-pending-mock", "gen-g-increments", "refine-pass-spends-nothing",
                     "refine-edited-adds-g", "assemble-svg-produced", "assemble-datauri-and-text",
                     "assemble-chroma-cleaned-zero-magenta", "qa-green", "qa-hard-fail-undersized",
                     "key-default-horizontal-merge", "key-expect-merge", "key-real-probe-expect-4",
                     "ref-anchor-recorded", "assemble-trim-content-ratio", "assemble-no-placeholder-rects",
                     "stacking-no-overlap", "reconcile-repairs-nulled-pointer",
                     "concurrent-writer-both-pointers", "k-failure-backfill-sidecar",
                     "band-asset-fill-88", "band-fullchain-qa-zero-text-collision"):
            check(name, True, "SKIPPED (Pillow absent)")
    for env_key in ("AR_FS_MOCK_GEN", "AR_FS_MOCK_REFINE_VERDICT", "AR_FS_MOCK_FAIL"):
        os.environ.pop(env_key, None)

    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        extra = f"  [{detail}]" if detail and (not ok or "SKIP" in detail) else ""
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{extra}")
    print(f"\nself-test: {passed}/{len(results)} cases passed")
    return 0 if passed == len(results) else 1


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    fsg = sub.add_parser("fsg", help="Figure Scene Graph lifecycle (init/validate/freeze)")
    fsg_sub = fsg.add_subparsers(dest="fsg_command", required=True)

    init = fsg_sub.add_parser("init", help="Scaffold an FSG for a figure")
    init.add_argument("project_root")
    init.add_argument("--figure-id", required=True)
    init.add_argument("--message", required=True)
    init.add_argument("--archetype", required=True, choices=list(ARCHETYPES))
    init.add_argument("--canvas-mm", default=None, help="WxH in mm, e.g. 183x90")
    init.add_argument("--column", choices=list(COLUMNS), default="single")
    init.set_defaults(func=command_fsg_init)

    validate = fsg_sub.add_parser("validate", help="Schema + budget checks for one FSG")
    validate.add_argument("project_root")
    validate.add_argument("--figure-id", required=True)
    validate.set_defaults(func=command_fsg_validate)

    freeze = fsg_sub.add_parser("freeze", help="Freeze an FSG (sha + version bump on change)")
    freeze.add_argument("project_root")
    freeze.add_argument("--figure-id", required=True)
    freeze.set_defaults(func=command_fsg_freeze)

    comp = sub.add_parser("compile", help="Compile FSG -> skeleton.dot(+svg) + prompts + checklist")
    comp.add_argument("project_root")
    comp.add_argument("--figure-id", required=True)
    comp.add_argument("--force", action="store_true", help="Rewrite prompts even if unchanged")
    comp.set_defaults(func=command_compile)

    key = sub.add_parser("key", help="Magenta chroma key: cleaned image + mask + label zones")
    key.add_argument("in_png")
    key.add_argument("--out-clean", required=True)
    key.add_argument("--out-mask", required=True)
    key.add_argument("--report", default=None, help="Optional JSON path for detected zones")
    key.add_argument("--merge-gap-px", type=int, default=None,
                     help="Absolute horizontal gap (px) for same-row zone merging "
                          "(default: 0.25 x max zone width)")
    key.add_argument("--expect", type=int, default=None,
                     help="Target zone count: greedily merge nearest same-row zones down to N")
    key.set_defaults(func=command_key)

    ledger = sub.add_parser("ledger", help="Render FIGURES.md across FSGs")
    ledger.add_argument("project_root")
    ledger.add_argument("--figure-id", default=None)
    ledger.set_defaults(func=command_ledger)

    gen = sub.add_parser("gen", help="Drive codex image-gen for pending elements (+ K-task + sidecar)")
    gen.add_argument("project_root")
    gen.add_argument("--figure-id", required=True)
    grp = gen.add_mutually_exclusive_group()
    grp.add_argument("--element", default=None, help="Force-regenerate one element at a new G")
    grp.add_argument("--all-pending", action="store_true",
                     help="Generate every element missing an asset at its current S")
    grp.add_argument("--reconcile", action="store_true",
                     help="Repair every raster node's gen pointer from disk truth (no generation)")
    gen.add_argument("--ref-anchor", dest="ref_anchor", action="store_true", default=None,
                     help="Attach E00's asset via -i (default: ON when E00 has an asset on disk)")
    gen.add_argument("--no-ref-anchor", dest="ref_anchor", action="store_false",
                     help="Do not attach the E00 style anchor")
    gen.add_argument("--timeout-s", type=int, default=600)
    gen.set_defaults(func=command_gen)

    refine = sub.add_parser("refine", help="Bounded high-reasoning in-loop critic for one element")
    refine.add_argument("project_root")
    refine.add_argument("--figure-id", required=True)
    refine.add_argument("--element", required=True)
    refine.add_argument("--max-iters", type=int, default=2)
    refine.add_argument("--timeout-s", type=int, default=900)
    refine.set_defaults(func=command_refine)

    assemble = sub.add_parser("assemble", help="Key + embed assets into skeleton SVG + vector text -> PDF")
    assemble.add_argument("project_root")
    assemble.add_argument("--figure-id", required=True)
    assemble.set_defaults(func=command_assemble)

    qa = sub.add_parser("qa", help="Mechanical QA gate (font/collision/magenta/density/assets)")
    qa.add_argument("project_root")
    qa.add_argument("--figure-id", required=True)
    qa.set_defaults(func=command_qa)

    selftest = sub.add_parser("self-test", help="Run the end-to-end M1a+M1b battery in a temp dir")
    selftest.set_defaults(func=command_self_test)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - uniform CLI error envelope
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
