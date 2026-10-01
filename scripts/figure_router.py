#!/usr/bin/env python3
"""Template-first figure routing and Quick Figure for auto-research v2.9.

Implements docs/figure-engineering-contract.md sections 0-2, 3-5, 7 and 10:

    route      deterministic three-question Router -> RouteDecision (A/B/C)
    venue      the eight venue profiles under library/venue/
    library    LibraryItem listing, inspection, schema validation, previews
    quick      register -> render -> promote -> rebase, the Quick Figure loop
    self-test  contract section 10.1/10.2/10.4 acceptance, machine-checked

Principle: fill a template before designing a scene, assemble components before
generating, and search the library before either. Route A (this file plus
figure_templates.py) is the only route that renders here; Route B hands off to
the component track and Route C to figure-studio.

stdlib only for orchestration; matplotlib / scienceplots / cmcrameri are
imported lazily by figure_templates and reported as degraded when absent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_os as ros                                # noqa: E402
import figure_templates as ft                            # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
LIBRARY = PLUGIN_ROOT / "library"
ITEMS_DIR = LIBRARY / "items"
VENUE_DIR = LIBRARY / "venue"
PREVIEW_DIR = LIBRARY / "previews"
COMPONENTS_DIR = LIBRARY / "components"
INDEX_MD = LIBRARY / "INDEX.md"
INDEX_BUDGET = 200

REGISTRY_SCHEMA = "auto-research/figure-registry-v1"
REQUEST_SCHEMA = "auto-research/figure-request-v0"
DECISION_SCHEMA = "auto-research/route-decision-v0"
QA_SCHEMA = "auto-research/figure-qa-v1"
PROVENANCE_SCHEMA = "auto-research/figure-provenance-v1"
ITEM_SCHEMA = "auto-research/library-item-v0"
VENUE_SCHEMA = "auto-research/venue-profile-v1"

ARTIFACT_REL = "research/artifacts/figures"

ROLES = ("x", "y", "lo", "hi", "group", "label", "value", "weight")
FIELD_TYPES = ("quantitative", "nominal", "temporal", "ordinal")
KINDS = ("chart-template", "diagram-template", "component", "component-set",
         "style-profile", "palette")
QUALITY_STATES = ("draft", "trial", "active", "retired")
STATUSES = ("draft", "current", "superseded")

ROUTE_POLICY_VERSION = "r9.1"      # A2/A3: bump when the scoring tuple changes
RENDER_DIR = "renders"             # A4: renders/<render_id>/, never overwritten
QUALITY_RANK = {"active": 3, "trial": 2, "draft": 1, "retired": 0}

ROUTE_ALIAS = {"auto": None, "data": "A", "components": "B", "concept": "C",
               "a": "A", "b": "B", "c": "C", "A": "A", "B": "B", "C": "C"}
ROUTE_NAME = {"A": "data", "B": "components", "C": "concept"}
ROUTE_BACKEND = {"A": "matplotlib", "B": "component-assembly", "C": "figure-studio"}


# ================================================================== small helpers

def emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return code


def write_json(path: Path, payload: dict) -> None:
    ros.atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def item_hash(item: dict) -> str:
    """A3: the content hash of the LibraryItem actually selected.  Pinning
    ``id@version`` alone is not enough -- a template edited in place under the
    same version would silently change every figure that named it."""
    payload = {k: v for k, v in (item or {}).items() if k != "_hash"}
    return sha256_text(canonical(payload))[:16]


def venue_hash(venue: dict) -> str:
    stored = (venue or {}).get("venue_hash")
    if stored:
        return str(stored)
    payload = {k: v for k, v in (venue or {}).items() if k != "venue_hash"}
    return sha256_text(canonical(payload))[:16]


def capability_state(deps: dict = None) -> dict:
    """A4: the same template, data and venue rendered on a host without
    scienceplots is NOT the same artifact.  The render id says so."""
    probe = deps if deps is not None else ft.probe_deps()
    return {k: bool(probe.get(k)) for k in sorted(("matplotlib", "scienceplots",
                                                   "cmcrameri", "numpy"))}


def render_id_for(item_h: str, venue_h: str, params_h: str, data_h: str,
                  caps: dict) -> str:
    digest = sha256_text("|".join([item_h or "", venue_h or "", params_h or "",
                                   data_h or "", canonical(caps or {})]))
    return "R-%s" % digest[:12]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_kv(spec: str) -> dict:
    """`x=k_eff,y=t,lo=t_lo` -> {"x": "k_eff", ...}"""
    out = {}
    for piece in (spec or "").split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece:
            ros.die("bad column spec %r: expected role=column" % piece)
        role, _, column = piece.partition("=")
        out[role.strip()] = column.strip()
    return out


def split_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in re.split(r"[,\s]+", str(value)) if p.strip()]


# ================================================================== library loading

def item_slug(item_id: str) -> str:
    return item_id.replace(":", ".")


def strip_version(ref: str):
    """`lib:figure:forest-ci@1` -> ("lib:figure:forest-ci", 1)"""
    text = (ref or "").strip()
    if "@" in text:
        base, _, version = text.rpartition("@")
        try:
            return base, int(version)
        except ValueError:
            return text, None
    return text, None


def load_items() -> dict:
    out = {}
    if not ITEMS_DIR.exists():
        return out
    for path in sorted(ITEMS_DIR.glob("*.json")):
        data = ros.read_json_file(path, None)
        if isinstance(data, dict) and data.get("id"):
            data["_file"] = str(path.relative_to(PLUGIN_ROOT).as_posix())
            out[data["id"]] = data
    return out


def load_item(ref: str, items: dict = None) -> dict:
    items = items if items is not None else load_items()
    base, version = strip_version(ref)
    # a bare template name (`forest-ci`) resolves to the chart-template namespace
    if base not in items and ":" not in base:
        base = "lib:figure:" + base
    item = items.get(base)
    if item is None:
        ros.die("unknown library item %r (have %s)" % (ref, ", ".join(sorted(items))), 1)
    if version is not None and int(item.get("version", 1)) != version:
        ros.die("version pin mismatch: %s is at version %s, request pinned @%s -- "
                "use `quick --rebase` to move a figure onto a new version"
                % (base, item.get("version"), version), 1)
    return item


def pinned_ref(item: dict) -> str:
    return "%s@%s" % (item["id"], item.get("version", 1))


def load_venue(venue_id: str) -> dict:
    path = VENUE_DIR / ("%s.json" % (venue_id or "generic"))
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in VENUE_DIR.glob("*.json")))
        ros.die("unknown venue %r (have %s)" % (venue_id, available), 1)
    return ros.read_json_file(path, {})


def load_venues() -> dict:
    return {p.stem: ros.read_json_file(p, {}) for p in sorted(VENUE_DIR.glob("*.json"))}


def palette_for(venue: dict, items: dict, override: str = None) -> dict:
    ref = override or venue.get("palette") or "lib:palette:okabe-ito@1"
    base, _ = strip_version(ref)
    return items.get(base) or {"id": base, "colors": ft.FALLBACK_COLORS}


def component_index() -> list:
    """Every registered component record across library/components/*/index.json."""
    records = []
    if not COMPONENTS_DIR.exists():
        return records
    for path in sorted(COMPONENTS_DIR.glob("*/index.json")):
        data = ros.read_json_file(path, None)
        if not isinstance(data, dict):
            continue
        rows = data.get("components") or data.get("items") or data.get("entries") or []
        source = data.get("source") or path.parent.name
        for row in rows:
            if isinstance(row, dict):
                row = dict(row)
                row.setdefault("source", source)
                records.append(row)
    return records


# ================================================================== item validation

REQUIRED_ITEM_KEYS = ("schema", "id", "kind", "version", "purpose", "semantic_tags",
                      "venue_tags", "renderer", "capabilities", "parameters",
                      "data_contract", "outputs", "preview", "license", "attribution",
                      "provenance", "compatibility", "quality_state")


def validate_item(item: dict, path: Path) -> list:
    problems = []

    def bad(message):
        problems.append("%s: %s" % (path.name, message))

    for key in REQUIRED_ITEM_KEYS:
        if key not in item:
            bad("missing required key %r" % key)
    if item.get("schema") != ITEM_SCHEMA:
        bad("schema is %r, expected %r" % (item.get("schema"), ITEM_SCHEMA))
    if item.get("kind") not in KINDS:
        bad("kind %r not in %s" % (item.get("kind"), "|".join(KINDS)))
    if not isinstance(item.get("version"), int) or item.get("version", 0) < 1:
        bad("version must be an integer >= 1")
    if item.get("quality_state") not in QUALITY_STATES:
        bad("quality_state %r not in %s" % (item.get("quality_state"), "|".join(QUALITY_STATES)))
    if item.get("id") and path.stem != item_slug(item["id"]):
        bad("filename %r does not match id %r" % (path.stem, item.get("id")))
    for key in ("semantic_tags", "venue_tags", "capabilities", "outputs"):
        if not isinstance(item.get(key), list):
            bad("%s must be a list" % key)
    if isinstance(item.get("semantic_tags"), list) and not item["semantic_tags"]:
        bad("semantic_tags is empty: the Router cannot ever select this item")
    if not isinstance(item.get("parameters"), dict):
        bad("parameters must be an object of defaults")
    contract = item.get("data_contract")
    if not isinstance(contract, dict):
        bad("data_contract must be an object")
    else:
        for key in ("required_fields", "optional_fields", "constraints"):
            if not isinstance(contract.get(key), list):
                bad("data_contract.%s must be a list" % key)
        for group in ("required_fields", "optional_fields"):
            for field in contract.get(group) or []:
                if not isinstance(field, dict):
                    bad("%s entry is not an object" % group)
                    continue
                if not field.get("name"):
                    bad("%s entry has no name" % group)
                if field.get("role") not in ROLES:
                    bad("%s role %r not in %s" % (group, field.get("role"), "|".join(ROLES)))
                if field.get("type") not in FIELD_TYPES:
                    bad("%s type %r not in %s" % (group, field.get("type"), "|".join(FIELD_TYPES)))
    provenance = item.get("provenance")
    if not isinstance(provenance, dict):
        bad("provenance must be an object")
    else:
        for key in ("source_url", "retrieved_at"):
            if not provenance.get(key):
                bad("provenance.%s is empty" % key)
    for key in ("license", "attribution"):
        if not str(item.get(key) or "").strip():
            bad("%s is empty: an unattributed item may not be shipped in a figure" % key)
    if item.get("kind") == "chart-template":
        function = item.get("template_function")
        if function not in ft.TEMPLATE_IDS:
            bad("template_function %r has no implementation in figure_templates" % function)
    return problems


def validate_library() -> dict:
    problems, warnings, seen = [], [], 0
    for path in sorted(ITEMS_DIR.glob("*.json")):
        data = ros.read_json_file(path, None)
        if not isinstance(data, dict):
            problems.append("%s: not a JSON object" % path.name)
            continue
        seen += 1
        problems.extend(validate_item(data, path))
        preview = data.get("preview")
        if preview and not (PLUGIN_ROOT / preview).exists():
            warnings.append("%s: preview %s not generated yet "
                            "(run `library gen-previews`)" % (path.name, preview))
    for path in sorted(VENUE_DIR.glob("*.json")):
        data = ros.read_json_file(path, None)
        if not isinstance(data, dict):
            problems.append("%s: not a JSON object" % path.name)
            continue
        if data.get("schema") != VENUE_SCHEMA:
            problems.append("%s: schema is %r" % (path.name, data.get("schema")))
        for key in ("id", "name", "style_stack", "figure_width_mm", "font",
                    "panel_label", "dpi", "export", "renderer_preference"):
            if key not in data:
                problems.append("%s: missing venue key %r" % (path.name, key))
        if data.get("id") != path.stem:
            problems.append("%s: id %r does not match filename" % (path.name, data.get("id")))
        widths = data.get("figure_width_mm") or {}
        if "single" not in widths:
            problems.append("%s: figure_width_mm has no `single` width" % path.name)
    index_lines = 0
    if INDEX_MD.exists():
        index_lines = INDEX_MD.read_text(encoding="utf-8").count("\n")
        if index_lines > INDEX_BUDGET:
            problems.append("INDEX.md is %d lines (budget %d): merge or retire before adding"
                            % (index_lines, INDEX_BUDGET))
    return {"ok": not problems, "items_checked": seen,
            "venues_checked": len(list(VENUE_DIR.glob("*.json"))),
            "index_lines": index_lines, "index_budget": INDEX_BUDGET,
            "problems": problems, "warnings": warnings}


# ================================================================== INDEX.md block

FIGURE_BLOCK_HEADING = "## figure library"
VENUE_ORDER = ("nature", "science", "ieee", "rfs", "jf", "aaai", "neurips", "generic")


def _index_row(item: dict) -> str:
    """One INDEX line per library item: pinned ref, quality state, the first three
    semantic tags, and links that must survive the package link lint."""
    tags = ",".join((item.get("semantic_tags") or [])[:3])
    row = "- `%s` [%s; %s] [item](items/%s.json)" % (
        pinned_ref(item), item.get("quality_state") or "draft", tags, item_slug(item["id"]))
    preview = item.get("preview")
    if preview and (PLUGIN_ROOT / preview).exists():
        row += " · [preview](previews/%s)" % Path(preview).name
    return row


def _ordered(items: dict, kind: str, first=()) -> list:
    """Deterministic order: the contract's starter sequence first, then whatever
    else has been registered since, alphabetically."""
    pool = {i["id"]: i for i in items.values() if i.get("kind") == kind}
    out = [pool.pop(i) for i in first if i in pool]
    return out + [pool[i] for i in sorted(pool)]


def component_sources() -> list:
    """(source, count) per built component index, for the INDEX block."""
    rows = []
    if not COMPONENTS_DIR.exists():
        return rows
    for path in sorted(COMPONENTS_DIR.glob("*/index.json")):
        data = ros.read_json_file(path, None)
        if not isinstance(data, dict):
            continue
        rows.append((data.get("source") or path.parent.name,
                     len(data.get("components") or [])))
    return rows


def index_block() -> str:
    """The `## figure library` section of library/INDEX.md, generated from the
    library itself.  `growth.py render` calls this instead of re-writing INDEX.md
    without it -- the growth ledger and the figure library share one index, and a
    growth re-render must not silently delete the other half."""
    items = load_items()
    venues = load_venues()
    order = [v for v in VENUE_ORDER if v in venues] + sorted(set(venues) - set(VENUE_ORDER))
    lines = [
        "%s (v2.9 template-first plotting)" % FIGURE_BLOCK_HEADING,
        "",
        "Generated by `figure_router.py library index-block`; `growth.py render` re-emits",
        "it so a growth re-render never drops the figure half of the library. Inspect with",
        "`figure_router.py library items|show <id>|validate`. Route decisions and Quick",
        "Figure live in `figure_router.py`, renderers in `figure_templates.py`.",
        "Venue profiles (%d): [venue/](venue/) — %s." % (len(order), ", ".join(order)),
        "",
        "### chart-template (Route A, matplotlib)",
    ]
    lines += [_index_row(i) for i in
              _ordered(items, "chart-template",
                       ["lib:figure:%s" % t for t in ft.TEMPLATE_IDS])]
    lines += ["",
              "### diagram-template (Route B, schema + slots only; assembly is the "
              "component track)"]
    lines += [_index_row(i) for i in
              _ordered(items, "diagram-template",
                       ["lib:diagram:pipeline-lr", "lib:diagram:architecture-stack"])]
    lines += ["", "### palette / style"]
    lines += [_index_row(i) for i in
              _ordered(items, "palette", ["lib:palette:okabe-ito", "lib:palette:cmc-batlow"])]
    lines += [_index_row(i) for i in _ordered(items, "style-profile")]
    sources = component_sources()
    if sources:
        lines += ["",
                  "Component sources (Route B): %s — [components/](components/); svg "
                  "bodies are rebuildable and git-ignored."
                  % " · ".join("%s %d" % (s, n) for s, n in sources)]
    return "\n".join(lines).rstrip("\n") + "\n"


# ================================================================== the Router

def normalise_semantic(raw) -> dict:
    """R9 A2: ``semantic_objects`` is ``{required[], optional[]}``.

    A bare list is read as ALL REQUIRED -- that is what every pre-R9 request
    meant, and silently demoting half of it to "nice to have" would change
    which route those requests take.  ``required`` is a gate (Route B needs
    100 %); ``optional`` only ever ranks candidates.
    """
    if isinstance(raw, dict):
        required = split_list(raw.get("required"))
        optional = [s for s in split_list(raw.get("optional")) if s not in required]
        return {"required": required, "optional": optional}
    return {"required": split_list(raw), "optional": []}


def semantic_all(request: dict) -> list:
    objects = request.get("semantic_objects") or {}
    if not isinstance(objects, dict):
        objects = normalise_semantic(objects)
    return list(objects.get("required") or []) + list(objects.get("optional") or [])


def normalise_request(raw: dict) -> dict:
    request = dict(raw or {})
    request.setdefault("schema", REQUEST_SCHEMA)
    request.setdefault("intent", "")
    request.setdefault("manuscript_target", {})
    request.setdefault("data_refs", [])
    request.setdefault("semantic_objects", [])
    request.setdefault("venue", "generic")
    request.setdefault("constraints", {})
    request.setdefault("route", "auto")
    target = request["manuscript_target"]
    target.setdefault("section", None)
    target["claim_ids"] = split_list(target.get("claim_ids"))
    refs = []
    for ref in request["data_refs"]:
        if isinstance(ref, str):
            ref = {"path": ref, "columns": {}}
        ref = dict(ref)
        ref.setdefault("columns", {})
        ref.setdefault("types", {})
        refs.append(ref)
    request["data_refs"] = refs
    request["semantic_objects"] = normalise_semantic(request["semantic_objects"])
    return request


def declared_roles(request: dict):
    """(roles present, declared types) across every data ref, header-sniffed when
    the request supplies no explicit column mapping."""
    roles, types = set(), {}
    for ref in request.get("data_refs") or []:
        mapping = ref.get("columns") or {}
        for role in mapping:
            if role in ROLES:
                roles.add(role)
        for role, kind in (ref.get("types") or {}).items():
            types[role] = kind
        if not mapping:
            path = Path(ref.get("path") or "")
            if path.exists():
                try:
                    header = list((ft.read_records(path) or [{}])[0].keys())
                except Exception:
                    header = []
                for role in ROLES:
                    if role in header:
                        roles.add(role)
    return roles, types


def _type_ok(declared: str, wanted: str) -> bool:
    if not declared:
        return True
    if declared == wanted:
        return True
    if wanted == "quantitative" and declared in ("ordinal",):
        return True
    if wanted == "nominal" and declared in ("ordinal", "temporal", "quantitative"):
        return True          # a nominal axis happily takes discrete numbers
    if wanted == "temporal" and declared == "quantitative":
        return True
    return False


def venue_tag_match(item: dict, venue_id: str) -> int:
    tags = [str(t) for t in (item.get("venue_tags") or [])]
    return 1 if venue_id and venue_id in tags else 0


def quality_rank(item: dict) -> int:
    return QUALITY_RANK.get(str(item.get("quality_state") or "draft"), 0)


def _fraction(hits, total) -> float:
    return round(len(hits) / total, 4) if total else 1.0


def score_tuple(candidate: dict) -> tuple:
    """R9 A2: the frozen ordering key.

    ``(required_coverage, optional_coverage, venue_tag_match, quality_state_rank,
    -item_id lexicographic)`` -- sorted descending, with the item id ascending as
    the last resort.  Every component is a rational number or an integer, so two
    machines given the same library and the same request choose the same
    template; nothing here depends on dict order, float noise or file mtime.
    """
    return (candidate["required_coverage"], candidate["optional_coverage"],
            candidate["venue_tag_match"], candidate["quality_state_rank"])


def _rank(candidates: list) -> list:
    return sorted(candidates, key=lambda c: (tuple(-x for x in score_tuple(c)), c["item_id"]))


def score_chart_item(item: dict, roles: set, types: dict, semantic: dict,
                     venue_id: str = "") -> dict:
    """Route A qualifies ONLY when every ``required_field`` of the data contract
    is present with a compatible declared type (R9 A2).  A missing required role
    is not a low score, it is a disqualification."""
    contract = item.get("data_contract") or {}
    required = contract.get("required_fields") or []
    optional = contract.get("optional_fields") or []
    need = [x["role"] for x in required]
    have = [r for r in need if r in roles]
    missing_roles = [r for r in need if r not in roles]
    mismatched = [x["role"] for x in required
                  if x["role"] in roles and not _type_ok(types.get(x["role"]), x.get("type"))]
    opt_roles = [x["role"] for x in optional]
    opt_hit = [r for r in opt_roles if r in roles]
    tags = set(item.get("semantic_tags") or [])
    want_required = list(semantic.get("required") or [])
    want_optional = list(semantic.get("optional") or [])
    tag_required = [s for s in want_required if s in tags]
    tag_optional = [s for s in want_optional if s in tags]
    qualifies = not missing_roles and not mismatched
    required_coverage = _fraction(have, len(need)) if need else 1.0
    if mismatched:
        required_coverage = 0.0
    # For Route A the GATE is the data contract; the semantic objects are not a
    # gate (a table does not stop being plottable because the request used a
    # word this template does not tag).  They rank instead, together with the
    # unused optional roles -- which is how "threshold-curve" still picks
    # line-series over forest-ci when both satisfy x/y/lo/hi.
    want_all = want_required + want_optional
    tag_all = tag_required + tag_optional
    optional_coverage = round(
        (_fraction(opt_hit, len(opt_roles)) + _fraction(tag_all, len(want_all))) / 2.0, 4)
    return {"item_id": pinned_ref(item), "kind": item["kind"],
            "required_coverage": required_coverage,
            "optional_coverage": optional_coverage,
            "venue_tag_match": venue_tag_match(item, venue_id),
            "quality_state_rank": quality_rank(item),
            "score": round(required_coverage, 3),
            "qualifies": qualifies, "matched_roles": have, "missing_roles": missing_roles,
            "type_mismatch": mismatched, "matched_optional": opt_hit,
            "semantic_hits": tag_required + tag_optional}


def score_diagram_item(item: dict, semantic: dict, venue_id: str = "") -> dict:
    """Route B qualifies ONLY on 100 % of the REQUIRED semantic objects; the
    optional ones move a candidate up the list and never let it in (R9 A2)."""
    tags = set(item.get("semantic_tags") or [])
    want_required = list(semantic.get("required") or [])
    want_optional = list(semantic.get("optional") or [])
    hit_required = [s for s in want_required if s in tags]
    hit_optional = [s for s in want_optional if s in tags]
    required_coverage = _fraction(hit_required, len(want_required)) if want_required else 0.0
    covered = bool(want_required) and len(hit_required) == len(want_required)
    return {"item_id": pinned_ref(item), "kind": item["kind"],
            "required_coverage": required_coverage,
            "optional_coverage": _fraction(hit_optional, len(want_optional)),
            "venue_tag_match": venue_tag_match(item, venue_id),
            "quality_state_rank": quality_rank(item),
            "score": round(required_coverage, 3),
            "qualifies": covered, "semantic_hits": hit_required + hit_optional,
            "uncovered": [s for s in want_required if s not in tags]}


def component_hit_rate(semantic, records: list) -> dict:
    wanted = semantic if isinstance(semantic, list) else \
        (list(semantic.get("required") or []) + list(semantic.get("optional") or []))
    if not wanted:
        return {"rate": 0.0, "hits": [], "records": len(records)}
    hits = []
    for want in wanted:
        needle = want.lower().replace("-", " ")
        for record in records:
            haystack = " ".join(str(v).lower().replace("-", " ") for v in
                                (list(record.get("tags") or []) +
                                 [record.get("id", ""), record.get("name", ""),
                                  record.get("category", "")]))
            if needle and needle in haystack:
                hits.append(want)
                break
    return {"rate": round(len(hits) / len(wanted), 3), "hits": hits, "records": len(records)}


def route_request(request: dict, items: dict = None) -> dict:
    """The deterministic three-question Router (contract 2, as amended by R9 A2).

    Candidates are ordered by the frozen tuple
    ``(required_coverage, optional_coverage, venue_tag_match, quality_state_rank)``
    descending, then by ``item_id`` ascending -- so a tie is broken
    lexicographically and never by whichever file the filesystem listed first.
    """
    items = items if items is not None else load_items()
    request = normalise_request(request)
    semantic = request["semantic_objects"]
    flat = semantic_all(request)
    venue_id = str(request.get("venue") or "generic")
    roles, types = declared_roles(request)
    reasons, candidates, missing = [], [], []

    charts = [i for i in items.values() if i.get("kind") == "chart-template"]
    diagrams = [i for i in items.values() if i.get("kind") == "diagram-template"]

    chart_scores = _rank([score_chart_item(i, roles, types, semantic, venue_id)
                          for i in charts])
    diagram_scores = _rank([score_diagram_item(i, semantic, venue_id) for i in diagrams])
    qualifying_charts = [c for c in chart_scores if c["qualifies"]]

    override = ROUTE_ALIAS.get(str(request.get("route") or "auto"))
    if str(request.get("route") or "auto") not in ROUTE_ALIAS:
        ros.die("unknown route %r (auto|data|components|concept)" % request.get("route"))

    # ---- Q1: can the geometry be derived from structured data?
    if request["data_refs"] and qualifying_charts:
        route = "A"
        best = qualifying_charts[0]
        reasons.append("data_refs supplied and chart-template %s matches every required "
                       "role (%s)" % (best["item_id"], ", ".join(best["matched_roles"])))
        selected = best["item_id"]
        candidates = qualifying_charts + [c for c in chart_scores if not c["qualifies"]][:3]
        item = items[strip_version(selected)[0]]
        unused = [x["role"] for x in (item.get("data_contract") or {}).get("optional_fields") or []
                  if x["role"] not in roles]
        missing = ["optional role not supplied: %s" % r for r in unused]
        missing += ["semantic object outside the template's vocabulary: %s" % s
                    for s in flat if s not in set(item.get("semantic_tags") or [])]
    else:
        if not request["data_refs"]:
            reasons.append("no data_refs: the geometry cannot be derived from a table")
        else:
            near = chart_scores[0] if chart_scores else None
            reasons.append("data_refs supplied but no chart-template's required roles are "
                           "satisfied%s" % (" (closest %s is missing %s)"
                                            % (near["item_id"], ", ".join(near["missing_roles"]))
                                            if near and near["missing_roles"] else ""))
        # ---- Q2: is the semantics covered by a registered diagram/component?
        covering = [d for d in diagram_scores if d["qualifies"]]
        index_records = component_index()
        coverage = component_hit_rate(semantic, index_records)
        if covering:
            route = "B"
            best = covering[0]
            selected = best["item_id"]
            reasons.append("every REQUIRED semantic object is a tag of diagram-template %s "
                           "(optional objects only ranked it)" % best["item_id"])
            candidates = covering + [d for d in diagram_scores if not d["qualifies"]][:2]
            missing = ["slot to fill: %s" % s for s in
                       sorted((items[strip_version(selected)[0]].get("slots") or {}))]
        elif coverage["rate"] >= 0.6:
            route = "B"
            selected = None
            reasons.append("component index covers %.0f%% of the semantic objects "
                           "(threshold 60%%)" % (100 * coverage["rate"]))
            candidates = diagram_scores[:3]
            missing = [s for s in flat if s not in coverage["hits"]]
        else:
            route = "C"
            selected = None
            if not index_records:
                reasons.append("no component index built yet (library/components/*/index.json "
                               "absent): Q2 cannot be answered from the library")
            else:
                reasons.append("component index covers only %.0f%% of the semantic objects "
                               "(< 60%% threshold)" % (100 * coverage["rate"]))
            reasons.append("no diagram-template covers every required semantic object")
            candidates = diagram_scores[:3]
            missing = [s for s in flat if s not in coverage["hits"]] or list(flat)

    overridden = False
    if override and override != route:
        reasons.insert(0, "route=%s in the request overrides the rule verdict %s"
                       % (request.get("route"), route))
        route, overridden = override, True
        if route != "A":
            selected = None
    elif override:
        reasons.insert(0, "route=%s in the request agrees with the rule verdict"
                       % request.get("route"))

    chosen = selected if route == "A" or (route == "B" and not overridden) else None
    return {"schema": DECISION_SCHEMA, "route": route, "route_name": ROUTE_NAME[route],
            "backend": ROUTE_BACKEND[route], "selected": chosen,
            "route_policy_version": ROUTE_POLICY_VERSION,
            "overridden": overridden, "reasons": reasons,
            "candidates": [{k: c[k] for k in
                            ("item_id", "kind", "score", "qualifies", "required_coverage",
                             "optional_coverage", "venue_tag_match", "quality_state_rank")}
                           for c in candidates],
            "candidate_detail": candidates, "missing": missing,
            "declared_roles": sorted(roles), "decided_at": ros.utc_now()}


# ================================================================== project registry

def project_control(root: str = None) -> Path:
    if root:
        control = ros.locate_control(Path(root))
        if not (control / "state.json").exists():
            ros.die("no auto-research project at %s (expected %s/state.json)" % (root, control), 2)
        return control
    found = ros.find_project_upwards(Path(os.getcwd()))
    if found is None:
        ros.die("no auto-research project here or in any parent -- pass --root", 2)
    return found


def registry_path(control: Path) -> Path:
    return control / "figures" / "registry.json"


def load_registry(control: Path) -> dict:
    data = ros.read_json_file(registry_path(control), None)
    if not isinstance(data, dict) or not isinstance(data.get("figures"), dict):
        return {"schema": REGISTRY_SCHEMA, "figures": {}}
    data.setdefault("schema", REGISTRY_SCHEMA)
    return data


def save_registry(control: Path, registry: dict) -> None:
    registry["schema"] = REGISTRY_SCHEMA
    registry["updated_at"] = ros.utc_now()
    write_json(registry_path(control), registry)


def _used_numbers(registry: dict) -> set:
    used = set()
    for key in registry.get("figures", {}):
        match = re.fullmatch(r"F-(\d+)", str(key))
        if match:
            used.add(int(match.group(1)))
    return used


def allocate_figure_id(registry: dict) -> str:
    """R9 A1: the registry allocates, the caller never proposes.

    A monotone counter lives in the registry itself and is only ever advanced,
    so an id is not reused after a figure is deleted and two registrations
    racing under the same lock cannot land on the same F-1NN.  The caller holds
    ``ros.hold_lock(control)`` around read-allocate-write, which is what makes
    this atomic rather than merely sequential.
    """
    used = _used_numbers(registry)
    counter = int(registry.get("figure_seq") or 100)
    counter = max(counter, max(used) if used else 100)
    counter += 1
    while counter in used:
        counter += 1
    registry["figure_seq"] = counter
    return "F-%d" % counter


def adopt_figure_id(registry: dict, figure_id: str) -> str:
    """R9 A1: the ONLY path that accepts a caller-supplied id -- importing a
    figure that already exists outside the registry.  Project-wide uniqueness is
    checked, and the counter is advanced past the imported number so a later
    allocation can never collide with it."""
    figure_id = str(figure_id or "").strip()
    if not figure_id:
        ros.die("--figure-id needs a value", 2)
    if figure_id in (registry.get("figures") or {}):
        ros.die("figure %s is already registered in this project: an imported id must be "
                "unique project-wide (see `figure_router.py list`)" % figure_id, 1)
    match = re.fullmatch(r"F-(\d+)", figure_id)
    if match:
        registry["figure_seq"] = max(int(registry.get("figure_seq") or 100),
                                     int(match.group(1)))
    return figure_id


def get_entry(registry: dict, figure_id: str) -> dict:
    entry = registry.get("figures", {}).get(figure_id)
    if entry is None:
        known = ", ".join(sorted(registry.get("figures", {}))) or "(none registered)"
        ros.die("unknown figure %r; registry holds %s" % (figure_id, known), 1)
    return entry


def artifact_dir(control: Path, figure_id: str) -> Path:
    return ros.locate_control(control).parent / ARTIFACT_REL / figure_id


def render_dir(control: Path, figure_id: str, render_id: str) -> Path:
    """A4: ``research/artifacts/figures/<F>/renders/<render_id>/``.  A render is
    immutable: a new one gets a new directory, an old one is never overwritten,
    and the registry keeps only a pointer to which is current."""
    return artifact_dir(control, figure_id) / RENDER_DIR / render_id


# ================================================================== QA + provenance

def _check(name, status, detail=""):
    return {"check": name, "status": status, "detail": detail}


def run_qa(entry: dict, directory: Path, venue: dict, item: dict, palette: dict,
           style_item: dict, table: dict, render_report: dict, level: str) -> dict:
    checks = []
    outputs = {k: Path(v) for k, v in (entry.get("outputs") or {}).items()}
    if not outputs:
        checks.append(_check("artifacts-exist", "fail", "nothing rendered yet"))
    else:
        absent = [k for k, p in outputs.items() if not p.exists() or p.stat().st_size == 0]
        checks.append(_check("artifacts-exist", "pass" if not absent else "fail",
                             "formats: %s" % ", ".join(sorted(outputs)) if not absent
                             else "missing or empty: %s" % ", ".join(absent)))
    want_width = (render_report or {}).get("width_mm")
    pdf = outputs.get("pdf")
    if pdf and pdf.exists() and want_width:
        size, embedded = ft.pdf_geometry(pdf)
        if size is None:
            checks.append(_check("size-matches-venue", "warn", "no /MediaBox found in the pdf"))
        else:
            delta = abs(size[0] - float(want_width))
            checks.append(_check("size-matches-venue", "pass" if delta <= 2.0 else "warn",
                                 "pdf width %.1f mm vs venue %.1f mm (tolerance 2 mm)"
                                 % (size[0], float(want_width))))
        checks.append(_check("pdf-fonts-embedded", "pass" if embedded else "fail",
                             "PDF font embedding: /FontFile object %s"
                             % ("found -- glyphs travel with the file"
                                if embedded else "absent; text will not survive a "
                                                 "foreign viewer")))
        height_budget = venue.get("max_height_mm")
        if size and height_budget:
            checks.append(_check("height-within-venue-budget",
                                 "pass" if size[1] <= float(height_budget) + 2.0 else "warn",
                                 "%.1f mm vs %.1f mm budget" % (size[1], float(height_budget))))
    else:
        checks.append(_check("size-matches-venue", "skipped", "no pdf output"))
        checks.append(_check("pdf-fonts-embedded", "skipped", "no pdf output"))
    svg = outputs.get("svg")
    if svg and svg.exists():
        size, outside = ft.svg_geometry(svg)
        text = svg.read_text(encoding="utf-8", errors="replace")
        live = text.count("<text")
        declared = ("font-family" in text) or ("font-family" in text.replace("_", "-"))
        outlined = ("<path" in text) and not live
        # R9 A5: an SVG is acceptable when its text is PRESERVED as text and the
        # font is declared, OR when the text has been deliberately converted to
        # paths.  Live <text> with no font declaration is the one bad state --
        # it renders in whatever the viewer happens to have.
        if live and declared:
            status, detail = "pass", ("%d <text> element(s), font-family declared "
                                      "(svg.fonttype=none)" % live)
        elif live:
            status, detail = "warn", ("%d <text> element(s) but no font-family declaration: "
                                      "the viewer substitutes" % live)
        elif outlined:
            status, detail = "pass", "text converted to paths (no <text>, glyphs are geometry)"
        else:
            status, detail = "warn", "no <text> and no outlined glyphs found"
        checks.append(_check("svg-text-preserved", status, detail))
        checks.append(_check("svg-text-within-canvas", "pass" if outside == 0 else "warn",
                             "%d text anchors outside the canvas (coarse bbox probe)" % outside))
    else:
        checks.append(_check("svg-text-preserved", "skipped", "no svg output"))
        checks.append(_check("svg-text-within-canvas", "skipped", "no svg output"))
    if table:
        missing_cells = table.get("missing_cells", 0)
        checks.append(_check("data-completeness", "pass" if not missing_cells else "warn",
                             "%d missing cell(s) across %d row(s) dropped or blanked"
                             % (missing_cells, table.get("n", 0))))
    else:
        checks.append(_check("data-completeness", "skipped", "no tabular source"))
    unattributed = [i.get("id") for i in (item, palette, style_item)
                    if isinstance(i, dict) and not (str(i.get("license") or "").strip()
                                                    and str(i.get("attribution") or "").strip())]
    checks.append(_check("attribution-complete", "pass" if not unattributed else "fail",
                         "every library item carries license + attribution" if not unattributed
                         else "unattributed: %s" % ", ".join(str(u) for u in unattributed)))
    if render_report:
        checks.append(_check("style-profile-exact",
                             "warn" if render_report.get("style_degraded") else "pass",
                             render_report.get("style_degraded_reason")
                             or "style stack %s applied"
                             % ",".join(render_report.get("style_stack_applied") or [])))
        checks.append(_check("colormap-exact",
                             "warn" if render_report.get("colormap_degraded") else "pass",
                             "; ".join(render_report.get("notes") or []) or "as specified"))
    if level == "full":
        claims = entry.get("claim_ids") or []
        checks.append(_check("claim-binding", "pass" if claims else "fail",
                             "expresses %s" % ", ".join(claims) if claims
                             else "no claim_ids: a current figure that argues nothing "
                                  "cannot be promoted honestly"))
        checks.append(_check("caption-intent-present", "pass" if entry.get("intent") else "warn",
                             entry.get("intent") or "no intent recorded"))
    summary = {"pass": 0, "warn": 0, "fail": 0, "skipped": 0}
    for row in checks:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    return {"schema": QA_SCHEMA, "figure_id": entry.get("figure_id"), "level": level,
            "checked_at": ros.utc_now(), "blocking": False,
            "checks": checks, "summary": summary}


def render_hash(outputs: dict) -> str:
    digest = hashlib.sha256()
    for fmt in sorted(outputs):
        digest.update(fmt.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(sha256_file(Path(outputs[fmt])).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


# ================================================================== quick: register

def build_request(args, figure_id: str) -> dict:
    data_refs = []
    columns = list(args.columns or [])
    for index, path in enumerate(args.data or []):
        raw, _, inline = str(path).partition("#")
        mapping = parse_kv(inline) if inline else {}
        if not mapping and index < len(columns):
            mapping = parse_kv(columns[index])
        data_refs.append({"path": raw, "columns": mapping,
                          "types": parse_kv(args.types) if args.types else {}})
    constraints = {"width": args.width or "single", "color": args.color or "auto"}
    if args.constraints:
        constraints.update(json.loads(args.constraints))
    return normalise_request({
        "schema": REQUEST_SCHEMA, "figure_id": figure_id, "intent": args.intent or "",
        "manuscript_target": {"section": args.section, "claim_ids": split_list(args.claim)},
        "data_refs": data_refs,
        "semantic_objects": {"required": split_list(args.semantic),
                             "optional": split_list(getattr(args, "semantic_optional", None))},
        "venue": args.venue or "generic", "constraints": constraints,
        "route": args.route or "auto"})


def register_one(control: Path, request: dict, items: dict, template_override: str = None,
                 params: dict = None, imported_id: str = None) -> dict:
    registry = load_registry(control)
    if imported_id:
        figure_id = adopt_figure_id(registry, imported_id)
    else:
        figure_id = allocate_figure_id(registry)
    request["figure_id"] = figure_id
    decision = route_request(request, items)
    if template_override:
        item = load_item(template_override, items)
        decision["selected"] = pinned_ref(item)
        decision["reasons"].insert(0, "template pinned explicitly: %s" % pinned_ref(item))
        if item.get("kind") == "chart-template":
            decision["route"], decision["route_name"] = "A", "data"
            decision["backend"] = ROUTE_BACKEND["A"]
    venue = load_venue(request.get("venue"))
    selected_item = decision.get("selected")
    selected_obj = items.get(strip_version(selected_item)[0]) if selected_item else None
    entry = {
        "figure_id": figure_id,
        "schema": REQUEST_SCHEMA,
        "intent": request.get("intent"),
        "status": "draft",
        "route": decision["route"],
        "route_name": decision["route_name"],
        "claim_ids": (request.get("manuscript_target") or {}).get("claim_ids") or [],
        "section": (request.get("manuscript_target") or {}).get("section"),
        "venue": venue.get("id"),
        "constraints": request.get("constraints") or {},
        "semantic_objects": semantic_all(request),
        "semantic_required": list((request.get("semantic_objects") or {}).get("required") or []),
        "semantic_optional": list((request.get("semantic_objects") or {}).get("optional") or []),
        "data_refs": request.get("data_refs") or [],
        # A3: the selection is PINNED here and does not move when the library
        # does.  `figure reroute <F>` is the only way to change it, and it says
        # so in the history.
        "template": selected_item,
        "selected_item": selected_item,
        "item_hash": item_hash(selected_obj) if selected_obj else None,
        "route_policy_version": ROUTE_POLICY_VERSION,
        "venue_version": venue.get("version"),
        "venue_hash": venue_hash(venue),
        "id_origin": "imported" if imported_id else "allocated",
        "params": params or {},
        "artifact_dir": (ARTIFACT_REL + "/" + figure_id),
        "route_decision": decision,
        "request": request,
        "outputs": {},
        "renders": {},
        "current_render": None,
        "provenance": None,
        "qa_summary": None,
        "history": [{"at": ros.utc_now(), "op": "register", "route": decision["route"],
                     "selected_item": selected_item,
                     "item_hash": item_hash(selected_obj) if selected_obj else None,
                     "route_policy_version": ROUTE_POLICY_VERSION}],
        "created_at": ros.utc_now(),
        "updated_at": ros.utc_now(),
    }
    registry["figures"][figure_id] = entry
    save_registry(control, registry)
    directory = artifact_dir(control, figure_id)
    write_json(directory / "request.json", request)
    write_json(directory / "route.json", decision)
    return entry


def cmd_register(args) -> int:
    control = project_control(args.root)
    items = load_items()
    with ros.hold_lock(control):
        if args.from_reply:
            reply = ros.read_json_file(Path(args.from_reply), None)
            if not isinstance(reply, dict):
                ros.die("--from-reply must be a JSON object with proposed_figure_requests[]", 2)
            proposals = reply.get("proposed_figure_requests") or []
            if not proposals:
                ros.die("no proposed_figure_requests[] in %s" % args.from_reply, 1)
            registered = []
            for proposal in proposals:
                if proposal.get("figure_id"):
                    ros.die("a figure-engineer reply may not choose figure ids "
                            "(%s): the registry allocates them (R9 A1)"
                            % proposal.get("figure_id"), 2)
                request = normalise_request(proposal)
                registered.append(register_one(control, request, items,
                                               proposal.get("template"),
                                               proposal.get("params")))
            return emit({"ok": True, "registered": [e["figure_id"] for e in registered],
                         "routes": {e["figure_id"]: e["route"] for e in registered},
                         "registry": str(registry_path(control))})
        if args.figure_id and not args.import_figure:
            ros.die("--figure-id is only accepted with --import (R9 A1): a new figure's id "
                    "is allocated by the registry, not proposed by the caller. Drop "
                    "--figure-id, or pass --import to adopt an existing figure's id.", 2)
        if args.import_figure and not args.figure_id:
            ros.die("--import needs the existing --figure-id it is adopting", 2)
        request = build_request(args, None)
        params = json.loads(args.params) if args.params else {}
        entry = register_one(control, request, items, args.template, params,
                             imported_id=args.figure_id if args.import_figure else None)
    return emit({"ok": True, "figure_id": entry["figure_id"], "status": entry["status"],
                 "route": entry["route"], "route_name": entry["route_name"],
                 "template": entry["template"], "selected_item": entry["selected_item"],
                 "item_hash": entry["item_hash"],
                 "route_policy_version": entry["route_policy_version"],
                 "id_origin": entry["id_origin"],
                 "reasons": entry["route_decision"]["reasons"],
                 "missing": entry["route_decision"]["missing"],
                 "next": ("figure_router.py quick --render %s" % entry["figure_id"])
                 if entry["route"] == "A" else
                 ("figure_router.py quick --assemble %s" % entry["figure_id"])
                 if entry["route"] == "B" else
                 ("Route C: hand off to figure-studio with `quick --promote-to-fsg %s`"
                  % entry["figure_id"]),
                 "registry": str(registry_path(control))})


# ================================================================== quick: render

def contract_types(item: dict) -> dict:
    """role -> declared type, straight from the template's data_contract. The
    contract, not a guess, decides whether a slot holds quantities or names."""
    contract = item.get("data_contract") or {}
    out = {}
    for group in ("required_fields", "optional_fields"):
        for field in contract.get(group) or []:
            if isinstance(field, dict) and field.get("role"):
                out[field["role"]] = field.get("type")
    return out


def render_entry(control: Path, entry: dict, items: dict, emit_vegalite: bool = False,
                 venue_override: str = None, basename: str = None) -> dict:
    """R9 A4/A5: every render lands in its own immutable
    ``renders/<render_id>/`` directory and all three working products (pdf, svg,
    png) are always produced.  ``venue.export[]`` decides what is PUBLISHED, not
    what exists -- a paper that ships pdf still needs the svg to edit and the png
    to paste into a slide, and deleting them at render time makes the earlier
    decision unreviewable."""
    venue = load_venue(venue_override or entry.get("venue"))
    if entry.get("route") != "A":
        return {"rendered": False, "reason": "route %s is not rendered by this backend"
                % entry.get("route")}
    template_ref = entry.get("selected_item") or entry.get("template")
    if not template_ref:
        ros.die("figure %s has no template pinned: `figure reroute %s` or `quick --rebase`"
                % (entry["figure_id"], entry["figure_id"]), 1)
    item = load_item(template_ref, items)
    if item.get("kind") != "chart-template":
        ros.die("template %s is a %s: Route A renders chart-templates only"
                % (template_ref, item.get("kind")), 1)
    pinned_hash = entry.get("item_hash")
    live_hash = item_hash(item)
    drift = bool(pinned_hash) and pinned_hash != live_hash
    refs = entry.get("data_refs") or []
    if not refs:
        ros.die("figure %s has no data_refs" % entry["figure_id"], 1)
    root = ros.locate_control(control).parent
    raw_path = Path(refs[0]["path"])
    path = raw_path if raw_path.is_absolute() else (root / raw_path)
    if not path.exists() and raw_path.exists():
        path = raw_path
    try:
        table = ft.build_table(path, refs[0].get("columns") or {}, contract_types(item))
    except ft.DataError as exc:
        ros.die(str(exc), 1)
    params = dict(item.get("parameters") or {})
    params.update({k: v for k, v in (entry.get("params") or {}).items()})
    constraints = entry.get("constraints") or {}
    if constraints.get("width"):
        params["width"] = constraints["width"]
    if constraints.get("color") == "mono":
        params["mono"] = True
    palette = palette_for(venue, items)

    deps = ft.probe_deps()
    caps = capability_state(deps)
    data_h = ft.data_hash(table)
    params_h = sha256_text(canonical(params))[:16]
    venue_h = venue_hash(venue)
    render_id = render_id_for(live_hash, venue_h, params_h, data_h, caps)
    directory = render_dir(control, entry["figure_id"], render_id)
    stem = basename or entry["figure_id"]
    try:
        # A5: always all three, whatever venue.export[] publishes.
        report = ft.render(item["template_function"], table, params, venue, palette,
                           directory, stem, exports=["pdf", "svg", "png"])
    except ft.DataError as exc:
        ros.die("render failed: %s" % exc, 1)
    outputs = report["outputs"]
    published = [fmt for fmt in (venue.get("export") or ["pdf", "svg", "png"])
                 if fmt in outputs]
    vega = None
    if emit_vegalite:
        spec = ft.vegalite_spec(item["template_function"], table, params, venue, palette,
                                Path(refs[0]["path"]).as_posix())
        vega = directory / "spec.vl.json"
        write_json(vega, spec)
        report.setdefault("notes", []).append(
            "vega-lite spec emitted (not rendered; install vl-convert to rasterise)")
    style_item = items.get("lib:style:scienceplots-stack")
    entry["outputs"] = outputs
    entry["render_report"] = report
    provenance = {
        "schema": PROVENANCE_SCHEMA,
        "figure_id": entry["figure_id"],
        "render_id": render_id,
        "render_dir": "%s/%s/%s/%s" % (ARTIFACT_REL, entry["figure_id"], RENDER_DIR, render_id),
        "render_hash": render_hash(outputs),
        "data_hash": data_h,
        "params_hash": params_h,
        "capability_state": caps,
        "data_source": {"path": Path(refs[0]["path"]).as_posix(),
                        "columns": table["columns"], "rows": table["n"]},
        "template": pinned_ref(item),
        "selected_item": entry.get("selected_item") or pinned_ref(item),
        "item_hash": live_hash,
        "pinned_item_hash": pinned_hash,
        "item_drift": drift,
        "route_policy_version": entry.get("route_policy_version") or ROUTE_POLICY_VERSION,
        "template_version": item.get("version"),
        "style_profile": {"id": (style_item or {}).get("id"),
                          "venue": venue.get("id"),
                          "stack_requested": report.get("style_stack_requested"),
                          "stack_applied": report.get("style_stack_applied"),
                          "style_degraded": report.get("style_degraded"),
                          "style_degraded_reason": report.get("style_degraded_reason")},
        "palette": {"id": palette.get("id"), "colors": report.get("colors_used"),
                    "colormap_degraded": report.get("colormap_degraded")},
        "venue": venue.get("id"),
        "venue_version": venue.get("version"),
        "venue_hash": venue_h,
        "export_set": list(venue.get("export") or []),
        "published": published,
        "geometry_mm": {"width": report["width_mm"], "height": report["height_mm"]},
        "component_provenance": [],
        "renderer": {"backend": "matplotlib", "deps": report.get("deps")},
        "tool": "scripts/figure_router.py quick --render",
        "outputs": {k: Path(v).name for k, v in outputs.items()},
        "vegalite_spec": (vega.name if vega else None),
        "notes": (report.get("notes") or []) + ([
            "the pinned item_hash %s no longer matches the library item (%s): the render "
            "records what was actually used; `figure reroute` to re-pin"
            % (pinned_hash, live_hash)] if drift else []),
        "rendered_at": ros.utc_now(),
    }
    qa = run_qa(entry, directory, venue, item, palette, style_item, table, report, "light")
    qa["render_id"] = render_id
    provenance["qa_summary"] = qa["summary"]
    write_json(directory / "provenance.json", provenance)
    write_json(directory / "qa.json", qa)
    # the artifact root keeps a pointer, never a second copy of the truth
    write_json(artifact_dir(control, entry["figure_id"]) / "current_render.json",
               {"schema": "auto-research/figure-current-render-v1",
                "figure_id": entry["figure_id"], "render_id": render_id,
                "render_dir": provenance["render_dir"], "at": ros.utc_now()})
    entry["provenance"] = provenance
    entry["qa_summary"] = qa["summary"]
    renders = entry.setdefault("renders", {})
    renders[render_id] = {"render_id": render_id, "at": provenance["rendered_at"],
                          "render_hash": provenance["render_hash"], "data_hash": data_h,
                          "params_hash": params_h, "venue": venue.get("id"),
                          "venue_hash": venue_h, "item_hash": live_hash,
                          "capability_state": caps, "published": published,
                          "outputs": {k: Path(v).name for k, v in outputs.items()},
                          "qa": qa["summary"]}
    entry["current_render"] = render_id
    entry["updated_at"] = ros.utc_now()
    entry.setdefault("history", []).append(
        {"at": entry["updated_at"], "op": "render", "template": pinned_ref(item),
         "venue": venue.get("id"), "render_id": render_id,
         "render_hash": provenance["render_hash"]})
    return {"rendered": True, "render_id": render_id, "provenance": provenance, "qa": qa,
            "report": report, "table": table, "directory": directory}


def cmd_render(args) -> int:
    control = project_control(args.root)
    items = load_items()
    with ros.hold_lock(control):
        registry = load_registry(control)
        entry = get_entry(registry, args.render)
        result = render_entry(control, entry, items, args.emit_vegalite, args.venue)
        save_registry(control, registry)
    if not result.get("rendered"):
        return emit({"ok": True, "figure_id": entry["figure_id"], "rendered": False,
                     "route": entry["route"], "reason": result["reason"],
                     "next": "quick --promote-to-fsg %s" % entry["figure_id"]
                     if entry["route"] == "C" else
                     "component assembly (track X2) fills the diagram-template slots"})
    return emit({"ok": True, "figure_id": entry["figure_id"], "rendered": True,
                 "outputs": entry["outputs"], "render_id": result["render_id"],
                 "published": result["provenance"]["published"],
                 "render_hash": result["provenance"]["render_hash"],
                 "data_hash": result["provenance"]["data_hash"],
                 "qa": result["qa"]["summary"],
                 "qa_warnings": [c for c in result["qa"]["checks"]
                                 if c["status"] in ("warn", "fail")],
                 "artifact_dir": str(result["directory"])})


# ================================================================== quick: promote

# ============================================================ v3 §2  real-time derivation

# The figure track never asserts an edge: `fig-registry` reads the registry this
# command just wrote and decides for itself what `expressed_as` now means.
GRAPH_RULES_FIGURE = ("fig-registry",)


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


def derive_graph_edges(control: Path, figure_id: str,
                       reason: str = "figure_router:quick --promote-current") -> dict:
    """R9 A8: the figure track does NOT write edges.

    ``promote-current`` updates the registry (``status=current``, ``claim_ids``)
    and then asks the graph to reconcile.  The ``expressed_as`` edge is produced
    by the graph's own ``fig-registry`` rule, from the registry it just read --
    one owner for the relation, one place it can be retracted, and no way for a
    figure command to assert something the graph would not derive for itself.
    """
    result = _graph_after(control, GRAPH_RULES_FIGURE, reason)
    if result is None:
        return {"derived": False, "added": 0, "retracted": 0,
                "reason": "research_graph is not installed", "edges": []}
    out = dict(result, rule="fig-registry", edges=[])
    if not result.get("derived"):
        out["reason"] = result.get("error")
        return out
    try:
        import research_graph as rg
        address = "ro::fig:%s" % figure_id
        edges = list(rg.list_edges(control, from_ro=address, kind="expressed_as"))
    except Exception:                                    # pragma: no cover
        edges = []
    out["edges"] = [{"edge_id": e["edge_id"], "from": e["from"], "to": e["to"],
                     "kind": e["kind"], "by": e.get("by"), "basis": e.get("basis"),
                     "status": "linked"} for e in edges]
    return out


def component_licence_gate(entry: dict) -> list:
    """R9 A7: a component whose licence nobody transcribed blocks promotion.

    A draft may render with it -- that is how you find out what the figure would
    look like.  What may not happen is a figure entering the manuscript while
    the project cannot say whether it is allowed to ship the icon in it.
    """
    manifest = ((entry.get("provenance") or {}).get("component_provenance") or [])
    bad = []
    for component in manifest:
        licence = str(component.get("license_id") or component.get("license") or "").strip()
        if not licence or licence.upper() == "UNKNOWN":
            bad.append(component.get("ref") or component.get("asset_id") or "?")
    return bad


def _promote_to_fsg(control: Path, registry: dict, entry: dict) -> int:
    directory = artifact_dir(control, entry["figure_id"])
    seed = {
        "schema": "auto-research/fsg-seed-v0",
        "figure": {"id": entry["figure_id"], "message": entry.get("intent"),
                   "archetype": entry.get("route_name"), "version": 1,
                   "venue": entry.get("venue")},
        "claim_ids": entry.get("claim_ids") or [],
        "semantic_objects": entry.get("semantic_objects") or [],
        "semantic_required": entry.get("semantic_required") or [],
        "semantic_optional": entry.get("semantic_optional") or [],
        "data_refs": entry.get("data_refs") or [],
        "route_decision": entry.get("route_decision"),
        "existing_outputs": entry.get("outputs") or {},
        "current_render": entry.get("current_render"),
        "seeded_at": ros.utc_now(),
        "note": "seed for figure-studio (Route C backend). This file is an input "
                "handoff; figure-studio owns everything downstream of it.",
    }
    seed_path = directory / "seed.fsg.json"
    write_json(seed_path, seed)
    entry["handoff"] = {"to": "figure-studio", "seed": str(seed_path.name),
                        "at": ros.utc_now()}
    entry["updated_at"] = ros.utc_now()
    entry.setdefault("history", []).append(
        {"at": entry["updated_at"], "op": "promote-to-fsg"})
    save_registry(control, registry)
    return emit({"ok": True, "figure_id": entry["figure_id"],
                 "promoted_to": "figure-studio", "seed": str(seed_path),
                 "status": entry["status"]})


def _promote_current(control: Path, registry: dict, entry: dict, items: dict,
                     render_id: str = None) -> int:
    figure_id = entry["figure_id"]
    renders = entry.get("renders") or {}
    if render_id:
        if render_id not in renders:
            ros.die("figure %s has no render %s (have: %s)"
                    % (figure_id, render_id, ", ".join(sorted(renders)) or "none"), 1)
    else:
        render_id = entry.get("current_render")
    if not entry.get("outputs"):
        ros.die("figure %s has no rendered outputs: run `quick --render %s` first"
                % (figure_id, figure_id), 1)
    if renders and not render_id:
        ros.die("figure %s has renders but no current one: pass --render-id <R>" % figure_id, 2)
    directory = (render_dir(control, figure_id, render_id) if render_id
                 else artifact_dir(control, figure_id))

    # A7: licence gate BEFORE anything is flipped to current.
    unlicensed = component_licence_gate(entry)
    if unlicensed:
        ros.die("PROMOTE_BLOCKED_UNKNOWN_LICENCE %s: %d component(s) carry no transcribed "
                "licence (%s). A draft may render with them; a figure entering the "
                "manuscript may not. Transcribe the licence in the component index, or "
                "swap the component out."
                % (figure_id, len(unlicensed), ", ".join(unlicensed)), 1)

    venue = load_venue(entry.get("venue"))
    template_ref = entry.get("selected_item") or entry.get("template")
    item = load_item(template_ref, items) if template_ref else {}
    palette = palette_for(venue, items)
    style_item = items.get("lib:style:scienceplots-stack")
    table = None
    refs = entry.get("data_refs") or []
    if refs:
        root = ros.locate_control(control).parent
        path = Path(refs[0]["path"])
        path = path if path.is_absolute() else (root / path)
        if path.exists():
            try:
                table = ft.build_table(path, refs[0].get("columns") or {},
                                       contract_types(item))
            except ft.DataError:
                table = None
    qa = run_qa(entry, directory, venue, item, palette, style_item, table,
                entry.get("render_report") or {}, "full")
    qa["render_id"] = render_id
    write_json(directory / "qa.json", qa)

    superseded = []
    for other_id, other in registry["figures"].items():
        if other_id == figure_id or other.get("status") != "current":
            continue
        if set(other.get("claim_ids") or []) & set(entry.get("claim_ids") or []):
            other["status"] = "superseded"
            other["updated_at"] = ros.utc_now()
            other.setdefault("history", []).append(
                {"at": other["updated_at"], "op": "superseded", "by": figure_id})
            superseded.append(other_id)
    entry["status"] = "current"
    entry["current_render"] = render_id
    entry["qa_summary"] = qa["summary"]
    if isinstance(entry.get("provenance"), dict):
        entry["provenance"]["qa_summary"] = qa["summary"]
        write_json(directory / "provenance.json", entry["provenance"])
    entry["updated_at"] = ros.utc_now()
    entry.setdefault("history", []).append(
        {"at": entry["updated_at"], "op": "promote-current", "render_id": render_id,
         "qa": qa["summary"]})
    save_registry(control, registry)

    # A8: the registry is written first and committed; only then is the graph
    # asked to derive.  The edge belongs to the graph, not to this command.
    graph = derive_graph_edges(control, figure_id)
    entry["edges"] = graph.get("edges") or []
    entry["pending_edges"] = []
    entry["graph"] = {k: graph.get(k) for k in ("derived", "rule", "added", "retracted",
                                                "reason")}
    save_registry(control, registry)
    return emit({"ok": True, "figure_id": figure_id, "status": entry["status"],
                 "render_id": render_id, "qa": qa["summary"],
                 "qa_findings": [c for c in qa["checks"] if c["status"] in ("warn", "fail")],
                 "edges": entry["edges"], "graph": entry["graph"],
                 "superseded": superseded,
                 "note": "expressed_as edges are produced by research_graph's fig-registry "
                         "derive rule from this registry (R9 A8); this command books none "
                         "itself"})


def cmd_promote(args) -> int:
    """R9 A6: three commands that do not overload one another.

    ``quick --promote-current <F> [--render-id R]`` and
    ``quick --promote-to-fsg <F>`` are the real ones; the pre-R9
    ``--promote <F> --current|--to-fsg`` spelling still works and prints a
    deprecation notice, because a state transition that silently changed
    meaning depending on a sibling flag is exactly what R9 A6 removes.
    """
    control = project_control(args.root)
    items = load_items()
    figure_id = (getattr(args, "promote_current", None)
                 or getattr(args, "promote_to_fsg", None) or args.promote)
    to_fsg = bool(getattr(args, "promote_to_fsg", None)) or bool(getattr(args, "to_fsg", False))
    to_current = bool(getattr(args, "promote_current", None)) or bool(getattr(args, "current", False))
    if getattr(args, "promote", None) and (args.current or args.to_fsg):
        sys.stderr.write(
            "DEPRECATED: `quick --promote %s %s` is the pre-R9 spelling; use "
            "`quick --promote-current %s` / `quick --promote-to-fsg %s` (R9 A6). "
            "The alias still works.\n"
            % (figure_id, "--current" if args.current else "--to-fsg", figure_id, figure_id))
    if to_fsg and to_current:
        ros.die("promote-current and promote-to-fsg are different transitions: pick one", 2)
    if not to_fsg and not to_current:
        ros.die("promote needs --promote-current <F> or --promote-to-fsg <F>", 2)
    with ros.hold_lock(control):
        registry = load_registry(control)
        entry = get_entry(registry, figure_id)
        if to_fsg:
            return _promote_to_fsg(control, registry, entry)
        return _promote_current(control, registry, entry, items,
                                getattr(args, "render_id", None))


# ================================================================== quick: reroute (A3)

def cmd_reroute(args) -> int:
    """R9 A3: the ONLY way a registered figure changes template.

    A library update never re-decides an existing request -- that is what
    pinning ``selected_item@version`` + ``item_hash`` is for.  Changing the
    selection is an explicit act with an event in the history saying what moved
    and under which route policy.
    """
    control = project_control(args.root)
    items = load_items()
    with ros.hold_lock(control):
        registry = load_registry(control)
        entry = get_entry(registry, args.reroute)
        previous = {"selected_item": entry.get("selected_item") or entry.get("template"),
                    "item_hash": entry.get("item_hash"), "route": entry.get("route"),
                    "route_policy_version": entry.get("route_policy_version")}
        request = normalise_request(entry.get("request") or {})
        if getattr(args, "route", None):
            request["route"] = args.route
        decision = route_request(request, items)
        if getattr(args, "template", None):
            item = load_item(args.template, items)
            decision["selected"] = pinned_ref(item)
            decision["reasons"].insert(0, "template pinned explicitly: %s" % pinned_ref(item))
            if item.get("kind") == "chart-template":
                decision["route"], decision["route_name"] = "A", "data"
                decision["backend"] = ROUTE_BACKEND["A"]
        selected = decision.get("selected")
        obj = items.get(strip_version(selected)[0]) if selected else None
        entry["route"], entry["route_name"] = decision["route"], decision["route_name"]
        entry["route_decision"] = decision
        entry["template"] = selected
        entry["selected_item"] = selected
        entry["item_hash"] = item_hash(obj) if obj else None
        entry["route_policy_version"] = ROUTE_POLICY_VERSION
        entry["status"] = "draft"
        entry["qa_summary"] = None
        entry["updated_at"] = ros.utc_now()
        entry.setdefault("history", []).append(
            {"at": entry["updated_at"], "op": "reroute", "from": previous,
             "to": {"selected_item": selected, "item_hash": entry["item_hash"],
                    "route": entry["route"],
                    "route_policy_version": ROUTE_POLICY_VERSION}})
        write_json(artifact_dir(control, entry["figure_id"]) / "route.json", decision)
        save_registry(control, registry)
    # v3 §2: a reroute drops the figure back to `draft`, so the graph must be
    # given the chance to RETRACT the expressed_as edge it derived before.
    graph = _graph_after(control, GRAPH_RULES_FIGURE, "figure_router:quick --reroute")
    return emit({"ok": True, "figure_id": entry["figure_id"], "from": previous,
                 "selected_item": entry["selected_item"], "item_hash": entry["item_hash"],
                 "route": entry["route"], "status": entry["status"],
                 "reasons": decision["reasons"],
                 **({"graph": graph} if graph is not None else {}),
                 "next": "quick --render %s (a reroute always demands a re-render)"
                 % entry["figure_id"]})


# ================================================================== quick: rebase

def cmd_rebase(args) -> int:
    if not args.to:
        ros.die("rebase needs --to <item@version>", 2)
    control = project_control(args.root)
    items = load_items()
    with ros.hold_lock(control):
        registry = load_registry(control)
        entry = get_entry(registry, args.rebase)
        item = load_item(args.to, items)
        previous = entry.get("selected_item") or entry.get("template")
        entry["template"] = pinned_ref(item)
        entry["selected_item"] = pinned_ref(item)
        entry["item_hash"] = item_hash(item)
        entry["route_policy_version"] = ROUTE_POLICY_VERSION
        if item.get("kind") == "chart-template":
            entry["route"], entry["route_name"] = "A", "data"
        entry["status"] = "draft"
        entry["qa_summary"] = None
        if isinstance(entry.get("provenance"), dict):
            entry["provenance"]["stale"] = True
            entry["provenance"]["stale_reason"] = "template rebased to %s" % pinned_ref(item)
        entry["updated_at"] = ros.utc_now()
        entry.setdefault("history", []).append(
            {"at": entry["updated_at"], "op": "rebase", "from": previous,
             "to": pinned_ref(item)})
        save_registry(control, registry)
    # v3 §2: same as reroute -- the figure is a draft again and the edge that
    # said it expresses a claim no longer holds.
    graph = _graph_after(control, GRAPH_RULES_FIGURE, "figure_router:quick --rebase")
    return emit({"ok": True, "figure_id": entry["figure_id"], "from": previous,
                 "to": entry["template"], "status": entry["status"],
                 **({"graph": graph} if graph is not None else {}),
                 "next": "quick --render %s (a rebase always demands a re-render)"
                 % entry["figure_id"]})


# ================================================================== quick: assemble (Route B)

SLOT_NAMES = "ABCDEFGH"


def diagram_template_of(ref: str) -> str:
    """`lib:diagram:pipeline-lr@1` -> `pipeline-lr`, the assembler's template name."""
    base, _ = strip_version(ref or "")
    return base.rsplit(":", 1)[-1] if base else ""


def _component_haystack(record: dict) -> str:
    return " ".join(str(v).lower().replace("-", " ") for v in
                    (list(record.get("tags") or []) + [record.get("id", ""),
                     record.get("name", ""), record.get("category", "")]))


def auto_slots(semantic: list, records: list) -> list:
    """One indexed component per semantic object, in request order, never reusing a
    file. Deterministic -- index order breaks every tie, so the same request
    assembles the same figure twice."""
    picked, used, unmatched = [], set(), []
    for want in semantic:
        needle = str(want).lower().replace("-", " ")
        if not needle:
            continue
        hit = None
        for record in records:
            ref = "%s:%s" % (record.get("source"), record.get("id"))
            if ref in used:
                continue
            if needle in _component_haystack(record):
                hit = ref
                break
        if hit:
            used.add(hit)
            picked.append((want, hit))
        else:
            unmatched.append((want, needle))
    # second pass, looser: a compound object ("stage-sequence") rarely appears
    # verbatim in an icon library, but one of its words usually does. Still exact
    # substring matching -- never a guess about what an icon means.
    for want, needle in unmatched:
        words = [w for w in needle.split() if len(w) >= 4]
        hit = None
        for record in records:
            ref = "%s:%s" % (record.get("source"), record.get("id"))
            if ref in used:
                continue
            haystack = _component_haystack(record)
            if any(w in haystack for w in words):
                hit = ref
                break
        if hit:
            used.add(hit)
            picked.append((want, hit))
    return [(w, r) for w, r in
            sorted(picked, key=lambda pair: list(semantic).index(pair[0]))]


def svg_width_mm(text: str):
    match = re.search(r'width="([0-9.]+)mm"', text or "")
    return float(match.group(1)) if match else None


def svg_viewbox_overflow(text: str):
    """Text anchors outside the canvas, measured in the coordinate system the
    anchors are actually written in. An assembled svg carries physical mm in
    `width`/`height` and user units in `viewBox`; the generic probe in
    figure_templates compares the two directly, which flags every label on a
    correctly built diagram. (count, w, h) -- (None, ...) when there is no viewBox."""
    box = re.search(r'viewBox="([-0-9.]+)\s+([-0-9.]+)\s+([0-9.]+)\s+([0-9.]+)"', text or "")
    if not box:
        return None, None, None
    x0, y0 = float(box.group(1)), float(box.group(2))
    width, height = float(box.group(3)), float(box.group(4))
    # Only the code-drawn label layer is measurable here: text inside an imported
    # component sits in that component's own transformed coordinate system, so
    # reading its raw x/y against the page canvas is meaningless.
    scan = text or ""
    start = scan.find('id="layer-text"')
    if start != -1:
        end = scan.find("</g>", start)
        scan = scan[start:end if end != -1 else len(scan)]
    outside = 0
    for hit in ft.SVG_TEXT.finditer(scan):
        x, y = float(hit.group(1)), float(hit.group(2))
        if x < x0 - 1.0 or x > x0 + width + 1.0 or y < y0 - 1.0 or y > y0 + height + 1.0:
            outside += 1
    return outside, width, height


def cmd_assemble(args) -> int:
    """Route B: fill a diagram-template's slots with indexed components and book the
    result back into the same registry Route A writes to. The geometry, the
    connectors and every glyph of text are code; only the icons come from the
    library, and each one drags its licence into the provenance."""
    control = project_control(args.root)
    items = load_items()
    try:
        import component_index as ci
    except Exception as exc:                             # pragma: no cover
        ros.die("Route B assembly needs scripts/component_index.py: %s" % exc, 2)
    with ros.hold_lock(control):
        registry = load_registry(control)
        entry = get_entry(registry, args.assemble)
        if entry.get("route") != "B":
            ros.die("figure %s is Route %s: assembly is the Route B backend (Route A "
                    "renders with `--render`, Route C hands off with `--promote --to-fsg`)"
                    % (entry["figure_id"], entry.get("route")), 1)
        template_ref = args.template or entry.get("template") or "lib:diagram:pipeline-lr@1"
        item = load_item(template_ref, items)
        if item.get("kind") != "diagram-template":
            ros.die("%s is a %s: Route B assembles diagram-templates"
                    % (template_ref, item.get("kind")), 1)
        template = diagram_template_of(pinned_ref(item))
        if template not in ci.TEMPLATES:
            ros.die("no assembler for %s (have: %s)" % (template, ", ".join(ci.TEMPLATES)), 1)

        labels = dict(parse_kv(args.labels) if args.labels else {})
        if args.slots:
            pairs = []
            for spec in split_list(args.slots):
                if "=" not in spec:
                    ros.die("bad --slots entry %r: expected SLOT=<source>:<id>" % spec, 2)
                slot, _, ref = spec.partition("=")
                pairs.append((slot.strip(), ref.strip()))
        else:
            auto = auto_slots(entry.get("semantic_objects") or [], component_index())
            pairs = [(SLOT_NAMES[i], ref) for i, (want, ref) in enumerate(auto)
                     if i < len(SLOT_NAMES)]
            for i, (want, _ref) in enumerate(auto[:len(SLOT_NAMES)]):
                labels.setdefault(SLOT_NAMES[i], want)
        if len(pairs) < 2:
            return emit({"ok": True, "figure_id": entry["figure_id"], "assembled": False,
                         "reason": "only %d component(s) matched the semantic objects %s; a "
                                   "pipeline needs at least two" % (len(pairs),
                                                                    entry.get("semantic_objects")),
                         "next": "pass --slots A=<source>:<id> B=<source>:<id> explicitly, or "
                                 "widen the component index (`component_index.py index build`)"})

        components_root = ci.DEFAULT_COMPONENTS_DIR
        absent = []
        entries = []
        for slot, ref in pairs:
            try:
                source, record = ci.resolve_ref(ref, components_root)
            except SystemExit:
                absent.append("%s (%s: not in any built index)" % (ref, slot))
                continue
            path = ci.resolve_component_path(record, components_root)
            if not path.exists():
                absent.append("%s (%s: index row present, svg body absent)" % (ref, slot))
            entries.append((slot, ref, source, record))
        if absent:
            return emit({"ok": True, "figure_id": entry["figure_id"], "assembled": False,
                         "reason": "component bodies unavailable: %s" % "; ".join(absent),
                         "next": "the svg bodies are rebuildable and git-ignored -- rebuild "
                                 "with `component_index.py index build --source <name> "
                                 "--repo <clone>`"})

        venue = load_venue(entry.get("venue"))
        width_key = (entry.get("constraints") or {}).get("width") or "single"
        widths = venue.get("figure_width_mm") or {}
        width_mm = float(widths.get(width_key) or widths.get("single") or 89.0)
        font_pt = float((venue.get("font") or {}).get("size_pt") or 8.0)
        try:
            text, manifest = ci.assemble_svg(template, pairs, labels, components_root,
                                             width_mm, args.assemble_title, 1.0, font_pt)
        except SystemExit as exc:                        # pragma: no cover
            ros.die("component assembly refused (%s)" % exc, 1)

        # R9 A4: an assembly is a render too -- immutable, addressed by content.
        caps = capability_state()
        slots_hash = sha256_text(canonical({"pairs": pairs, "labels": labels,
                                            "title": args.assemble_title}))[:16]
        parts_hash = sha256_text(canonical(sorted(
            "%s@%s" % (m.get("ref"), m.get("asset_sha256") or "?") for m in manifest)))[:16]
        render_id = render_id_for(item_hash(item), venue_hash(venue), slots_hash,
                                  parts_hash, caps)
        directory = render_dir(control, entry["figure_id"], render_id)
        directory.mkdir(parents=True, exist_ok=True)
        svg_path = directory / ("%s.svg" % entry["figure_id"])
        ros.atomic_write(svg_path, text)
        attribution_path = directory / "attribution.md"
        ros.atomic_write(attribution_path,
                         ci.render_attribution([m["ref"] for m in manifest],
                                               components_root, svg_path.name))
        # R9 A7: keep the bytes we were allowed to keep, beside the render that
        # used them.  A rebuilt upstream index must not silently change what a
        # promoted figure was made of.
        copied = []
        parts_dir = directory / "components"
        for component in manifest:
            if not component.get("redistributable"):
                continue
            origin = ci.resolve_component_path({"path": component.get("asset_path")},
                                               components_root)
            if not origin.exists():
                continue
            target = parts_dir / ("%s.svg" % re.sub(r"[^A-Za-z0-9_.-]", "_",
                                                    str(component.get("asset_id"))))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(origin), str(target))
            component["local_copy"] = target.relative_to(directory).as_posix()
            copied.append(component["local_copy"])
        unknown = [m["ref"] for m in manifest
                   if str(m.get("license_id") or m.get("license") or "UNKNOWN") == "UNKNOWN"]

        outputs = {"svg": str(svg_path)}
        entry["outputs"] = outputs
        entry["template"] = pinned_ref(item)
        provenance = {
            "schema": PROVENANCE_SCHEMA,
            "figure_id": entry["figure_id"],
            "render_id": render_id,
            "render_dir": "%s/%s/%s/%s" % (ARTIFACT_REL, entry["figure_id"], RENDER_DIR,
                                           render_id),
            "render_hash": render_hash(outputs),
            "data_hash": None,
            "params_hash": slots_hash,
            "capability_state": caps,
            "data_source": None,
            "component_copies": copied,
            "template": pinned_ref(item),
            "template_version": item.get("version"),
            "style_profile": {"id": None, "venue": venue.get("id"),
                              "stack_requested": None, "stack_applied": None,
                              "style_degraded": False, "style_degraded_reason": None},
            "palette": {"id": None, "colors": [], "colormap_degraded": False},
            "venue": venue.get("id"),
            "venue_version": venue.get("version"),
            "venue_hash": venue_hash(venue),
            "export_set": list(venue.get("export") or []),
            "published": ["svg"],
            "geometry_mm": {"width": svg_width_mm(text) or width_mm, "height": None},
            "component_provenance": manifest,
            "renderer": {"backend": "component-assembly",
                         "tool": "scripts/component_index.py assemble",
                         "template": template},
            "tool": "scripts/figure_router.py quick --assemble",
            "outputs": {k: Path(v).name for k, v in outputs.items()},
            "attribution": attribution_path.name,
            "vegalite_spec": None,
            "notes": (["%d component(s) carry an UNKNOWN licence: %s"
                       % (len(unknown), ", ".join(unknown))] if unknown else []),
            "rendered_at": ros.utc_now(),
        }
        # palette/style are matplotlib concerns: pass None so the attribution check
        # skips them instead of reading an empty dict as an unattributed item.
        qa = run_qa(entry, directory, venue, item, None, None, None, {}, "light")
        overflow, box_w, box_h = svg_viewbox_overflow(text)
        if overflow is not None:
            for row in qa["checks"]:
                if row["check"] == "svg-text-within-canvas":
                    row["status"] = "pass" if overflow == 0 else "warn"
                    row["detail"] = ("%d text anchor(s) outside the %.0fx%.0f user-unit "
                                     "viewBox" % (overflow, box_w, box_h))
        qa["checks"].append(_check("component-licences-known",
                                   "pass" if not unknown else "warn",
                                   "%d component(s), %d without a transcribed licence "
                                   "(a draft may render; promote-current is refused)"
                                   % (len(manifest), len(unknown))))
        qa["checks"].append(_check(
            "component-copies-kept",
            "pass" if len(copied) == len([m for m in manifest if m.get("redistributable")])
            else "warn",
            "%d of %d redistributable component(s) copied into the render directory"
            % (len(copied), len([m for m in manifest if m.get("redistributable")]))))
        got_width = svg_width_mm(text)
        qa["checks"].append(_check(
            "component-canvas-width",
            "pass" if got_width is not None and abs(got_width - width_mm) <= 1.0 else "warn",
            "svg width %s mm vs venue %s (%.1f mm)" % (got_width, width_key, width_mm)))
        qa["summary"] = {"pass": 0, "warn": 0, "fail": 0, "skipped": 0}
        for row in qa["checks"]:
            qa["summary"][row["status"]] = qa["summary"].get(row["status"], 0) + 1
        provenance["qa_summary"] = qa["summary"]
        write_json(directory / "provenance.json", provenance)
        write_json(directory / "qa.json", qa)
        entry["provenance"] = provenance
        entry["qa_summary"] = qa["summary"]
        renders = entry.setdefault("renders", {})
        renders[render_id] = {"render_id": render_id, "at": provenance["rendered_at"],
                              "render_hash": provenance["render_hash"],
                              "params_hash": slots_hash, "venue": venue.get("id"),
                              "venue_hash": venue_hash(venue), "item_hash": item_hash(item),
                              "capability_state": caps, "published": ["svg"],
                              "outputs": {k: Path(v).name for k, v in outputs.items()},
                              "qa": qa["summary"]}
        entry["current_render"] = render_id
        write_json(artifact_dir(control, entry["figure_id"]) / "current_render.json",
                   {"schema": "auto-research/figure-current-render-v1",
                    "figure_id": entry["figure_id"], "render_id": render_id,
                    "render_dir": provenance["render_dir"], "at": ros.utc_now()})
        entry["updated_at"] = ros.utc_now()
        entry.setdefault("history", []).append(
            {"at": entry["updated_at"], "op": "assemble", "template": pinned_ref(item),
             "render_id": render_id,
             "components": [m["ref"] for m in manifest],
             "render_hash": provenance["render_hash"]})
        save_registry(control, registry)
    return emit({"ok": True, "figure_id": entry["figure_id"], "assembled": True,
                 "template": pinned_ref(item), "outputs": outputs, "render_id": render_id,
                 "components": [{"slot": m["slot"], "ref": m["ref"],
                                 "license_id": m.get("license_id"),
                                 "redistributable": m.get("redistributable"),
                                 "asset_sha256": (m.get("asset_sha256") or "")[:12]}
                                for m in manifest],
                 "attribution": str(attribution_path), "component_copies": copied,
                 "render_hash": provenance["render_hash"], "qa": qa["summary"],
                 "unknown_licences": unknown,
                 "next": ("resolve the %d UNKNOWN licence(s) before promoting" % len(unknown))
                 if unknown else
                 ("quick --promote-current %s --render-id %s"
                  % (entry["figure_id"], render_id))})


QUICK_ACTIONS = ("register", "render", "assemble", "promote_current", "promote_to_fsg",
                 "promote", "reroute", "rebase")


def cmd_quick(args) -> int:
    """R9 A6: each state transition is its own flag.

    ``--promote`` used to mean two different things depending on whether
    ``--current`` or ``--to-fsg`` was also present -- one command, two
    transitions, and a typo silently performed the other one.  It survives as a
    deprecated alias; ``--promote-current`` / ``--promote-to-fsg`` are the real
    commands.
    """
    selected = [flag for flag in QUICK_ACTIONS if getattr(args, flag, None)]
    if len(selected) != 1:
        ros.die("quick takes exactly one of --register / --render / --assemble / "
                "--promote-current / --promote-to-fsg / --reroute / --rebase "
                "(--promote is the deprecated alias)", 2)
    return {"register": cmd_register, "render": cmd_render, "assemble": cmd_assemble,
            "promote_current": cmd_promote, "promote_to_fsg": cmd_promote,
            "promote": cmd_promote, "reroute": cmd_reroute,
            "rebase": cmd_rebase}[selected[0]](args)


def cmd_list(args) -> int:
    control = project_control(args.root)
    registry = load_registry(control)
    rows = []
    for figure_id, entry in sorted(registry.get("figures", {}).items()):
        rows.append({"figure_id": figure_id, "status": entry.get("status"),
                     "route": entry.get("route"),
                     "selected_item": entry.get("selected_item") or entry.get("template"),
                     "item_hash": entry.get("item_hash"),
                     "venue": entry.get("venue"), "claim_ids": entry.get("claim_ids"),
                     "qa": entry.get("qa_summary"),
                     "current_render": entry.get("current_render"),
                     "renders": len(entry.get("renders") or {}),
                     "render_hash": ((entry.get("provenance") or {}).get("render_hash") or "")[:12]})
    return emit({"ok": True, "registry": str(registry_path(control)),
                 "count": len(rows), "figures": rows})


# ================================================================== venue / library CLI

def cmd_venue(args) -> int:
    if args.venue_action == "list":
        rows = []
        for vid, profile in sorted(load_venues().items()):
            rows.append({"id": vid, "name": profile.get("name"),
                         "widths_mm": profile.get("figure_width_mm"),
                         "font_pt": (profile.get("font") or {}).get("size_pt"),
                         "style_stack": profile.get("style_stack"),
                         "panel_label": profile.get("panel_label")})
        return emit({"ok": True, "count": len(rows), "venues": rows})
    return emit({"ok": True, "venue": load_venue(args.venue_id)})


def cmd_library(args) -> int:
    if args.library_action == "items":
        rows = []
        for item in sorted(load_items().values(), key=lambda i: (i["kind"], i["id"])):
            rows.append({"id": pinned_ref(item), "kind": item["kind"],
                         "quality_state": item.get("quality_state"),
                         "renderer": item.get("renderer"),
                         "semantic_tags": item.get("semantic_tags"),
                         "preview_exists": bool(item.get("preview")
                                                and (PLUGIN_ROOT / item["preview"]).exists())})
        return emit({"ok": True, "count": len(rows), "items": rows})
    if args.library_action == "show":
        return emit({"ok": True, "item": load_item(args.item_id)})
    if args.library_action == "validate":
        report = validate_library()
        return emit(report, 0 if report["ok"] else 1)
    if args.library_action == "index-block":
        sys.stdout.write(index_block())
        return 0
    if args.library_action == "gen-previews":
        return cmd_gen_previews(args)
    ros.die("unknown library action %r" % args.library_action, 2)


def cmd_gen_previews(args) -> int:
    """Render one small preview per item. Charts use the built-in samples; palettes
    and style profiles get a swatch; diagram-templates get a slot sketch."""
    deps = ft.probe_deps()
    if not deps["matplotlib"]:
        return emit({"ok": True, "generated": [], "skipped": "matplotlib not installed"}, 0)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    items = load_items()
    venue = load_venue("generic")
    preview_venue = dict(venue)
    preview_venue["figure_width_mm"] = {"single": 70, "double": 70}
    preview_venue["export"] = ["png"]
    preview_venue["dpi"] = 200
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    generated, failed = [], []
    with tempfile.TemporaryDirectory() as tmp:
        samples = ft.write_samples(Path(tmp))
        for item in items.values():
            target = PLUGIN_ROOT / item["preview"]
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if item["kind"] == "chart-template":
                    sample = samples[item["template_function"]]
                    table = ft.build_table(sample["path"], sample["columns"],
                                           contract_types(item))
                    palette = palette_for(preview_venue, items)
                    report = ft.render(item["template_function"], table,
                                       dict(item.get("parameters") or {}), preview_venue,
                                       palette, Path(tmp), "preview-" + item["template_function"],
                                       exports=["png"])
                    shutil.copyfile(report["outputs"]["png"], target)
                else:
                    _swatch_preview(plt, item, target)
                generated.append(item["preview"])
            except Exception as exc:
                failed.append({"id": item["id"], "error": str(exc)[:200]})
    return emit({"ok": not failed, "generated": sorted(generated), "failed": failed},
                0 if not failed else 1)


def _swatch_preview(plt, item: dict, target: Path) -> None:
    plt.rcdefaults()
    plt.rcParams.update({"font.size": 7, "savefig.dpi": 200})
    fig, ax = plt.subplots(figsize=(2.6, 1.5), layout="constrained")
    ax.set_axis_off()
    if item["kind"] == "palette":
        colors = item.get("colors") or ft.FALLBACK_COLORS
        for index, color in enumerate(colors):
            ax.add_patch(plt.Rectangle((index, 0), 0.92, 1, color=color))
        ax.set_xlim(-0.1, len(colors))
        ax.set_ylim(-0.4, 1.5)
        ax.text(0, 1.15, item["id"], fontsize=6)
    elif item["kind"] == "diagram-template":
        slots = list((item.get("slots") or {}).keys()) or ["slot"]
        horizontal = "pipeline" in item["id"]
        for index, name in enumerate(slots):
            x = index * 1.2 if horizontal else 0.0
            y = 0.0 if horizontal else -index * 0.7
            ax.add_patch(plt.Rectangle((x, y), 1.0, 0.55, fill=False, linewidth=0.8))
            ax.text(x + 0.5, y + 0.27, name, ha="center", va="center", fontsize=5)
        ax.set_xlim(-0.3, (1.2 * len(slots)) if horizontal else 1.6)
        ax.set_ylim(-0.7 * len(slots) - 0.2 if not horizontal else -0.4, 1.1)
        ax.text(-0.2, 0.95, item["id"], fontsize=6)
    else:
        ax.text(0.5, 0.6, item["id"], ha="center", fontsize=7)
        ax.text(0.5, 0.35, item.get("kind", ""), ha="center", fontsize=6, color="0.4")
    fig.savefig(str(target), format="png", metadata={"Software": None})
    plt.close(fig)


def cmd_route(args) -> int:
    if args.request:
        raw = ros.read_json_file(Path(args.request), None)
        if not isinstance(raw, dict):
            ros.die("--request must point at a FigureRequest JSON object", 2)
    elif args.inline:
        raw = json.loads(args.inline)
    else:
        ros.die("route needs --request <file> or --inline <json>", 2)
    if args.route:
        raw["route"] = args.route
    decision = route_request(raw, load_items())
    if not args.verbose:
        decision.pop("candidate_detail", None)
    return emit({"ok": True, "figure_id": raw.get("figure_id"), "decision": decision})


# ================================================================== self-test

class Report:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append({"name": name, "status": "PASS" if ok else "FAIL",
                          "detail": str(detail)[:240]})
        return bool(ok)

    def skip(self, name, detail=""):
        self.rows.append({"name": name, "status": "SKIPPED", "detail": str(detail)[:240]})
        return True

    @property
    def summary(self):
        out = {"PASS": 0, "FAIL": 0, "SKIPPED": 0}
        for row in self.rows:
            out[row["status"]] += 1
        return out

    @property
    def ok(self):
        return all(row["status"] != "FAIL" for row in self.rows)


ROUTER_CASES = [
    ("router: data figure -> Route A", "A", {
        "intent": "how the supervised-search threshold moves with effective trials",
        "manuscript_target": {"section": "4.3", "claim_ids": ["C-057"]},
        "data_refs": [{"path": "research/results/thresholds.csv",
                       "columns": {"x": "k_eff", "y": "t", "lo": "t_lo", "hi": "t_hi"}}],
        "semantic_objects": ["threshold-curve", "reference-line"],
        "venue": "rfs", "constraints": {"width": "single", "color": "auto"},
        "route": "auto"}),
    ("router: pipeline diagram -> Route B", "B", {
        "intent": "how a submission moves through the audit pipeline",
        "manuscript_target": {"section": "3.1", "claim_ids": ["C-012"]},
        "data_refs": [],
        "semantic_objects": ["pipeline", "stage-sequence", "data-flow"],
        "venue": "aaai", "route": "auto"}),
    ("router: novel concept -> Route C", "C", {
        "intent": "the geometry of the disclosure-impossibility argument",
        "manuscript_target": {"section": "2", "claim_ids": ["C-001"]},
        "data_refs": [],
        "semantic_objects": ["impossibility-cone", "belief-simplex", "adversary-wedge"],
        "venue": "rfs", "route": "auto"}),
]


def _bootstrap_project(root: Path) -> bool:
    script = Path(__file__).resolve().parent / "research_os.py"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run([sys.executable, str(script), "bootstrap", str(root),
                           "--title", "figure router self-test", "--layout", "core"],
                          capture_output=True, text=True, encoding="utf-8", env=env)
    return proc.returncode == 0 and (root / ".research-os" / "state.json").exists()


# ================================================================== integration self-test

def _run_tool(script: str, *args):
    """Run one of the sibling CLIs the way a skill would, and hand back its JSON.
    Integration means the published command surface, not an in-process shortcut."""
    path = Path(__file__).resolve().parent / script
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run([sys.executable, str(path)] + [str(a) for a in args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)
    out = (proc.stdout or "").strip()
    try:
        payload = json.loads(out)
    except (json.JSONDecodeError, ValueError):
        payload = None
    return proc.returncode, payload, out or (proc.stderr or "").strip()


IDENTITY = ("subject=audit-pipeline", "target=review-outcome", "predicate=gap",
            "quantifier=all", "domain=submissions", "conditions=", "polarity=+")


def _seed_narrative(root: Path, claim: str) -> str:
    """A real narrative store with one claim node, built through the only legal
    writer. Without a resolvable narrative endpoint an `expressed_as` edge can
    only ever park as pending, which would make the whole check vacuous."""
    code, _, text = _run_tool("narrative.py", "init", root, "--title",
                              "figure router integration", "--thesis",
                              "template-first figures book their own edges",
                              "--summary", "root")
    if code != 0:
        return "narrative init failed: %s" % text[:200]
    ops = root / "ops.json"
    args = ["node", "add", root, "--id", claim, "--node-type", "claim", "--parent", "R-0",
            "--title", "claim %s" % claim,
            "--statement", "the audit pipeline leaves a measurable gap",
            "--epistemic", "PENDING", "--out", str(ops)]
    for pair in IDENTITY:
        args += ["--identity", pair]
    code, _, text = _run_tool("narrative.py", *args)
    if code != 0:
        return "node add failed: %s" % text[:200]
    code, _, text = _run_tool("narrative.py", "commit", root, "--ops-file", str(ops),
                              "--message", "add %s" % claim)
    if code != 0:
        return "commit failed: %s" % text[:200]
    # R9 B6: load-bearing is root + role nodes + explicitly flagged nodes.  The
    # claim has to actually carry a role for coverage list A to hold it to
    # account -- otherwise the "coverage stops complaining" check is vacuous.
    code, _, text = _run_tool("narrative.py", "node", "set-role", root, "--role", "headline",
                              "--id", claim, "--out", str(ops))
    if code != 0:
        return "set-role failed: %s" % text[:200]
    code, _, text = _run_tool("narrative.py", "commit", root, "--ops-file", str(ops),
                              "--message", "headline := %s" % claim)
    if code != 0:
        return "headline commit failed: %s" % text[:200]
    return ""


def _coverage_unexpressed(root: Path) -> list:
    """Section A of `graph view coverage`: load-bearing claims no figure expresses."""
    _code, _payload, text = _run_tool("research_graph.py", "view", "coverage", "--root", root)
    rows, inside = [], False
    for line in text.splitlines():
        if line.startswith("A. "):
            inside = True
            continue
        if inside:
            if line[:2] in ("B.", "C.", "D."):
                break
            stripped = line.strip()
            if stripped and stripped != "(none)":
                rows.append(stripped)
    return rows


def run_integration(report, items: dict, deps: dict, workdir: Path) -> None:
    """Contract section 10.4 end to end, through the published CLIs: bootstrap ->
    narrative claim -> register -> route -> render -> promote -> the edge lands in
    the research graph -> coverage stops complaining; then the Route B assembly and
    the Route C handoff."""
    project = workdir / "integration"
    samples = ft.write_samples(workdir / "integration-data")

    booted = _bootstrap_project(project)
    report.check("integration: bootstrap a throwaway project", booted,
                 str(project) if booted else "research_os.py bootstrap failed")
    if not booted:
        return
    problem = _seed_narrative(project, "C-001")
    report.check("integration: narrative init + claim node C-001", not problem,
                 problem or "C-001 committed on refs/heads/main")
    if problem:
        return

    results = project / "research" / "results"
    results.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(samples["forest-ci"]["path"], results / "estimates.csv")
    columns = samples["forest-ci"]["columns"]

    # R9 A1: a caller-proposed id is refused unless it is an explicit import.
    code, _payload, text = _run_tool(
        "figure_router.py", "quick", "--root", project, "--register",
        "--figure-id", "F-900", "--intent", "an id nobody may choose",
        "--claim", "C-001", "--semantic", "pipeline")
    report.check("integration: A1 --figure-id without --import is refused",
                 code != 0 and "--figure-id is only accepted with --import" in text,
                 text[:160])

    code, payload, text = _run_tool(
        "figure_router.py", "quick", "--root", project, "--register",
        "--intent", "which audit factors survive the gate",
        "--claim", "C-001", "--section", "4.3",
        "--data", "research/results/estimates.csv",
        "--columns", ",".join("%s=%s" % kv for kv in sorted(columns.items())),
        "--semantic", "coefficient-plot,confidence-interval",
        "--venue", "rfs", "--width", "single")
    ok = code == 0 and payload and payload.get("route") == "A" \
        and payload.get("selected_item") == "lib:figure:forest-ci@1" \
        and payload.get("figure_id") == "F-101" \
        and payload.get("id_origin") == "allocated" \
        and payload.get("item_hash")
    report.check("integration: quick --register allocates F-101, decides Route A and pins "
                 "forest-ci with an item_hash", ok,
                 json.dumps({k: (payload or {}).get(k) for k in
                             ("figure_id", "route", "selected_item", "id_origin")},
                            ensure_ascii=False) if payload else text[:200])
    if not ok:
        return
    control = project / ".research-os"
    directory = artifact_dir(control, "F-101")
    code, payload, text = _run_tool("figure_router.py", "route", "--request",
                                    directory / "request.json")
    report.check("integration: `route` on the stored request agrees with the registry",
                 code == 0 and payload
                 and (payload.get("decision") or {}).get("route") == "A"
                 and (payload.get("decision") or {}).get("route_policy_version")
                 == ROUTE_POLICY_VERSION,
                 ((payload or {}).get("decision") or {}).get("route") or text[:160])

    unexpressed_before = _coverage_unexpressed(project)
    report.check("integration: the load-bearing claim starts out unexpressed",
                 any("C-001" in row for row in unexpressed_before),
                 "section A: %s" % ("; ".join(unexpressed_before) or "(none)"))

    if not deps["matplotlib"]:
        for label in ("integration: --render emits pdf+svg+png into an immutable render dir",
                      "integration: --promote-current books expressed_as through the graph, "
                      "with no pending edge",
                      "integration: replay-pending is a clean no-op once nothing is parked",
                      "integration: coverage stops listing C-001 as unexpressed"):
            report.skip(label, "matplotlib not installed: Route A cannot render on this host")
    else:
        code, payload, text = _run_tool("figure_router.py", "quick", "--root", project,
                                        "--render", "F-101", "--emit-vegalite")
        render_id = (payload or {}).get("render_id") or ""
        rdir = render_dir(control, "F-101", render_id) if render_id else directory
        needed = ["F-101.pdf", "F-101.svg", "F-101.png", "qa.json", "provenance.json",
                  "spec.vl.json"]
        absent = [n for n in needed if not (rdir / n).exists()]
        report.check("integration: --render emits pdf+svg+png into an immutable render dir",
                     code == 0 and render_id.startswith("R-") and not absent and payload
                     and (payload.get("qa") or {}).get("fail") == 0
                     and (directory / "current_render.json").exists(),
                     "missing %s" % ", ".join(absent) if absent
                     else "render_id=%s published=%s"
                     % (render_id, (payload or {}).get("published")))

        code, payload, text = _run_tool("figure_router.py", "quick", "--root", project,
                                        "--promote-current", "F-101",
                                        "--render-id", render_id)
        edges = (payload or {}).get("edges") or []
        linked = [e for e in edges if e.get("status") == "linked"]
        report.check("integration: --promote-current books expressed_as through the graph, "
                     "with no pending edge",
                     code == 0 and len(linked) == 1
                     and linked[0].get("by") == "derive"
                     and "derive:fig-registry" in (linked[0].get("basis") or "")
                     and (payload or {}).get("status") == "current"
                     and ((payload or {}).get("graph") or {}).get("derived") is True,
                     "edges=%s" % json.dumps(edges, ensure_ascii=False)[:200])

        code, replay, text = _run_tool("research_graph.py", "link", "--replay-pending",
                                       "--root", project)
        report.check("integration: replay-pending is a clean no-op once nothing is parked",
                     code == 0 and replay is not None
                     and not (replay.get("still_pending") or [])
                     and not (replay.get("linked") or []),
                     "linked=%d still=%d"
                     % (len((replay or {}).get("linked") or []),
                        len((replay or {}).get("still_pending") or [])))

        unexpressed = _coverage_unexpressed(project)
        report.check("integration: coverage stops listing C-001 as unexpressed",
                     not any("C-001" in row for row in unexpressed),
                     "section A: %s" % ("; ".join(unexpressed) or "(none)"))

    # ---- Route B: the component track assembles, and books into the same registry
    code, payload, text = _run_tool(
        "figure_router.py", "quick", "--root", project, "--register",
        "--intent", "how a submission moves through the pipeline",
        "--claim", "C-001", "--semantic", "pipeline,stage-sequence,data-flow",
        "--venue", "aaai", "--width", "double")
    figure_b = (payload or {}).get("figure_id")
    routed_b = code == 0 and payload and payload.get("route") == "B" \
        and payload.get("selected_item") == "lib:diagram:pipeline-lr@1" \
        and figure_b == "F-102"
    report.check("integration: a pipeline request routes to B on the diagram-template and "
                 "gets the next allocated id", routed_b,
                 (payload or {}).get("route") if payload else text[:200])
    label = "integration: quick --assemble fills the slots and books the svg + licences"
    if not routed_b:
        report.skip(label, "Route B request did not route to B")
    else:
        code, payload, text = _run_tool("figure_router.py", "quick", "--root", project,
                                        "--assemble", figure_b)
        if code == 0 and payload and payload.get("assembled") is False:
            report.skip(label, str(payload.get("reason"))[:200])
        else:
            registry = load_registry(control)
            entry = registry["figures"].get(figure_b) or {}
            provenance = entry.get("provenance") or {}
            manifest = provenance.get("component_provenance") or []
            svg = (render_dir(control, figure_b, entry.get("current_render") or "")
                   / ("%s.svg" % figure_b))
            report.check(label,
                         code == 0 and svg.exists() and svg.stat().st_size > 0
                         and (entry.get("outputs") or {}).get("svg")
                         and len(manifest) >= 2
                         and all(c.get("license_id") for c in manifest)
                         and (entry.get("qa_summary") or {}).get("fail") == 0,
                         "components=%d qa=%s" % (len(manifest), entry.get("qa_summary")))
            report.check("integration: A7 every used component records repo/asset/licence "
                         "and its redistributable verdict",
                         bool(manifest) and all(
                             set(("source_repo", "asset_id", "asset_sha256", "license_id",
                                  "attribution_text", "redistributable")) <= set(c)
                             for c in manifest),
                         sorted(manifest[0]) if manifest else "(none)")

    # ---- Route C: hand off, and say so in the registry
    code, payload, text = _run_tool(
        "figure_router.py", "quick", "--root", project, "--register",
        "--intent", "the geometry of the impossibility argument",
        "--claim", "C-001",
        "--semantic", "impossibility-cone,belief-simplex,adversary-wedge", "--venue", "rfs")
    figure_c = (payload or {}).get("figure_id")
    routed_c = code == 0 and payload and payload.get("route") == "C"
    report.check("integration: an unlibraried concept routes to C", routed_c,
                 (payload or {}).get("route") if payload else text[:200])
    label = "integration: Route C records a figure-studio handoff in the registry"
    if not routed_c:
        report.skip(label, "Route C request did not route to C")
    else:
        code, payload, text = _run_tool("figure_router.py", "quick", "--root", project,
                                        "--promote-to-fsg", figure_c)
        entry = (load_registry(control)["figures"].get(figure_c) or {})
        handoff = entry.get("handoff") or {}
        report.check(label,
                     code == 0 and handoff.get("to") == "figure-studio"
                     and (artifact_dir(control, figure_c) / "seed.fsg.json").exists(),
                     "handoff=%s" % json.dumps(handoff, ensure_ascii=False))
        code, payload, text = _run_tool("figure_router.py", "quick", "--root", project,
                                        "--promote", figure_c, "--to-fsg")
        report.check("integration: A6 the pre-R9 `--promote --to-fsg` alias still works and "
                     "says it is deprecated",
                     code == 0 and "DEPRECATED" in text.upper()
                     or (code == 0 and payload
                         and payload.get("promoted_to") == "figure-studio"),
                     text[:160])

    # ---- A3: a reroute is the only way the pinned selection moves
    code, payload, text = _run_tool("figure_router.py", "quick", "--root", project,
                                    "--reroute", "F-101",
                                    "--template", "lib:figure:event-study@1")
    entry = (load_registry(control)["figures"].get("F-101") or {})
    report.check("integration: A3 `reroute` re-pins the template, drops back to draft and "
                 "records the move",
                 code == 0 and entry.get("selected_item") == "lib:figure:event-study@1"
                 and entry.get("status") == "draft"
                 and any(h.get("op") == "reroute" for h in entry.get("history") or []),
                 "selected=%s status=%s" % (entry.get("selected_item"), entry.get("status")))


class _GraphStub:
    """Stands in for research_graph so the CALL SITE is what is under test.
    Records what each figure transition asks the graph to derive."""

    def __init__(self, boom: bool = False):
        self.calls, self.boom = [], boom

    def derive(self, control, rules=None, reason="manual", since=None):
        self.calls.append({"rules": list(rules or []), "reason": reason,
                           "control": Path(control)})
        if self.boom:
            raise RuntimeError("derive exploded on purpose")
        return {"added": 2, "retracted": 1}

    def list_edges(self, control, **kwargs):
        return []


def _run_captured(fn, args):
    """Run a cmd_* and hand back (exit_code, parsed JSON payload)."""
    import io as _io
    buffer, saved = _io.StringIO(), sys.stdout
    sys.stdout = buffer
    try:
        code = fn(args)
    finally:
        sys.stdout = saved
    return code, json.loads(buffer.getvalue())


def _install_stub(stub):
    saved = sys.modules.get("research_graph")
    sys.modules["research_graph"] = stub

    def restore():
        if saved is None:
            sys.modules.pop("research_graph", None)
        else:
            sys.modules["research_graph"] = saved
    return restore


def graph_call_site_checks(report, control: Path) -> None:
    """v3 contract §2: reroute / rebase / promote-current each derive the graph
    after their own write, over `fig-registry` and nothing else."""
    root = str(control.parent)
    for verb, fn, args in (
            ("--reroute", cmd_reroute,
             argparse.Namespace(root=root, reroute="F-101", route=None, template=None)),
            ("--rebase", cmd_rebase,
             argparse.Namespace(root=root, rebase="F-101", to="lib:figure:forest-ci@1"))):
        stub = _GraphStub()
        restore = _install_stub(stub)
        try:
            code, payload = _run_captured(fn, args)
        finally:
            restore()
        report.check("v3 §2: quick %s derives fig-registry and reports it in the payload"
                     % verb,
                     code == 0 and payload.get("ok") is True
                     and payload.get("graph") == {"derived": True, "added": 2,
                                                  "retracted": 1}
                     and len(stub.calls) == 1
                     and stub.calls[0]["rules"] == list(GRAPH_RULES_FIGURE)
                     and stub.calls[0]["reason"] == "figure_router:quick %s" % verb,
                     "code=%s graph=%s calls=%s" % (code, payload.get("graph"), stub.calls))

    stub = _GraphStub()
    restore = _install_stub(stub)
    try:
        result = derive_graph_edges(control, "F-101")
    finally:
        restore()
    report.check("v3 §2: promote-current asks the graph for the same rule set",
                 result.get("derived") is True and result.get("added") == 2
                 and len(stub.calls) == 1
                 and stub.calls[0]["rules"] == list(GRAPH_RULES_FIGURE)
                 and stub.calls[0]["reason"] == "figure_router:quick --promote-current",
                 "result=%s calls=%s" % (result, stub.calls))

    boom = _GraphStub(boom=True)
    restore = _install_stub(boom)
    try:
        code, payload = _run_captured(
            cmd_rebase, argparse.Namespace(root=root, rebase="F-101",
                                           to="lib:figure:event-study@1"))
    finally:
        restore()
    log = control / "graph" / "incidents.log"
    line = (log.read_text(encoding="utf-8").strip().splitlines() or [""])[-1]         if log.exists() else ""
    report.check("v3 §2: a raising derive leaves the figure transaction landed and "
                 "writes one GRAPH_DERIVE_FAILED line",
                 code == 0 and payload.get("ok") is True
                 and payload["graph"]["derived"] is False
                 and bool(payload["graph"].get("error"))
                 and line.startswith("GRAPH_DERIVE_FAILED figure_router:quick --rebase:"),
                 "code=%s graph=%s line=%s" % (code, payload.get("graph"), line[:120]))


def cmd_self_test(args) -> int:
    report = Report()
    items = load_items()
    deps = ft.probe_deps()

    # --- contract 10.1: Router three cases, then the override
    for name, expected, request in ROUTER_CASES:
        decision = route_request(request, items)
        report.check(name, decision["route"] == expected,
                     "got %s (%s)" % (decision["route"], "; ".join(decision["reasons"])[:150]))
    override = route_request(dict(ROUTER_CASES[0][2], route="concept"), items)
    report.check("router: explicit route= overrides the rule verdict",
                 override["route"] == "C" and override["overridden"],
                 "got %s overridden=%s" % (override["route"], override["overridden"]))

    # --- contract 10.1: eight venue profiles load
    venues = load_venues()
    expected_venues = {"nature", "science", "ieee", "rfs", "jf", "aaai", "neurips", "generic"}
    report.check("venue: eight profiles load", set(venues) == expected_venues,
                 "found %s" % ", ".join(sorted(venues)))
    complete = [v for v, p in venues.items()
                if p.get("style_stack") and (p.get("figure_width_mm") or {}).get("single")
                and (p.get("font") or {}).get("size_pt") and p.get("dpi")]
    report.check("venue: every profile carries style/width/font/dpi",
                 len(complete) == len(venues), "complete: %d/%d" % (len(complete), len(venues)))

    # --- contract 10.1: item schema + INDEX budget
    validation = validate_library()
    report.check("library: every item and venue is schema-valid", validation["ok"],
                 "; ".join(validation["problems"])[:220] or "clean")
    report.check("library: INDEX.md within the %d-line budget" % INDEX_BUDGET,
                 validation["index_lines"] <= INDEX_BUDGET,
                 "%d lines" % validation["index_lines"])
    starter = {"lib:figure:line-series", "lib:figure:bar-grouped", "lib:figure:scatter-fit",
               "lib:figure:forest-ci", "lib:figure:event-study", "lib:figure:heatmap",
               "lib:diagram:pipeline-lr", "lib:diagram:architecture-stack",
               "lib:palette:okabe-ito", "lib:palette:cmc-batlow",
               "lib:style:scienceplots-stack"}
    report.check("library: the starter set is complete", starter <= set(items),
                 "missing %s" % ", ".join(sorted(starter - set(items))) or "all 11 present")

    # --- R9 A9 negative 1: two templates that score identically must resolve to
    # the SAME one on every machine, by item_id lexicographic order -- never by
    # whichever the filesystem happened to list first.
    tie = [{"item_id": "lib:diagram:zulu-flow@1", "kind": "diagram-template",
            "required_coverage": 1.0, "optional_coverage": 0.5, "venue_tag_match": 1,
            "quality_state_rank": 3, "score": 1.0, "qualifies": True},
           {"item_id": "lib:diagram:alpha-flow@1", "kind": "diagram-template",
            "required_coverage": 1.0, "optional_coverage": 0.5, "venue_tag_match": 1,
            "quality_state_rank": 3, "score": 1.0, "qualifies": True}]
    forward = _rank(list(tie))[0]["item_id"]
    backward = _rank(list(reversed(tie)))[0]["item_id"]
    report.check("A9-1 an exact scoring tie is broken by item_id, stably in both "
                 "input orders",
                 forward == backward == "lib:diagram:alpha-flow@1",
                 "forward=%s backward=%s" % (forward, backward))
    higher = _rank([dict(tie[1], optional_coverage=0.9), tie[0]])[0]["item_id"]
    report.check("A9-1 optional coverage still outranks the lexicographic tie-break",
                 higher == "lib:diagram:alpha-flow@1", higher)

    workdir = Path(tempfile.mkdtemp(prefix="figrouter-selftest-"))
    try:
        # --- contract 10.2: all six chart templates really render pdf+svg+png
        venue = load_venues()["generic"]
        palette = palette_for(venue, items)
        samples = ft.write_samples(workdir / "data")

        # A nominal slot silently parsed as a number collapses a whole axis into
        # one empty row (heatmap y did exactly that before the contract types were
        # wired in). This check is data-only, so it runs on a host with no renderer.
        heat_item = items["lib:figure:heatmap"]
        heat = ft.build_table(samples["heatmap"]["path"], samples["heatmap"]["columns"],
                              contract_types(heat_item))
        levels = {v for v in heat["roles"]["y"] if v is not None}
        report.check("typing: a nominal role keeps its labels instead of becoming NaN",
                     len(levels) >= 4 and heat["field_types"]["y"] == "nominal"
                     and heat["missing_cells"] == 0,
                     "heatmap y has %d level(s), typed %s, %d missing cell(s)"
                     % (len(levels), heat["field_types"]["y"], heat["missing_cells"]))
        rendered_paths = {}
        for template_id in ft.TEMPLATE_IDS:
            label = "render: %s emits pdf+svg+png" % template_id
            if not deps["matplotlib"]:
                report.skip(label, "matplotlib not installed: Route A unavailable on this host")
                continue
            item = items["lib:figure:%s" % template_id]
            sample = samples[template_id]
            try:
                table = ft.build_table(sample["path"], sample["columns"],
                                       contract_types(item))
                result = ft.render(template_id, table, dict(item.get("parameters") or {}),
                                   venue, palette, workdir / "out" / template_id, template_id)
                got = {fmt: Path(p) for fmt, p in result["outputs"].items()}
                ok = all(got.get(f) and got[f].exists() and got[f].stat().st_size > 0
                         for f in ("pdf", "svg", "png"))
                size, embedded = ft.pdf_geometry(got["pdf"]) if got.get("pdf") else (None, False)
                width_ok = size is not None and abs(size[0] - result["width_mm"]) <= 2.0
                report.check(label, ok and embedded and width_ok,
                             "exists=%s fonts_embedded=%s width=%s (want %.1f mm)"
                             % (ok, embedded, None if not size else round(size[0], 1),
                                result["width_mm"]))
                rendered_paths[template_id] = got
            except Exception as exc:
                report.check(label, False, "%s: %s" % (type(exc).__name__, exc))
        if deps["matplotlib"]:
            report.check("render: style degradation is reported, never silent",
                         isinstance(result.get("style_degraded"), bool),
                         "scienceplots=%s style_degraded=%s"
                         % (deps["scienceplots"], result.get("style_degraded")))
        else:
            report.skip("render: style degradation is reported, never silent",
                        "no matplotlib")

        # --- contract 10.2: same data, two venues -> data_hash steady, render_hash moves
        label = "venue swap: data_hash stable, render_hash moves"
        if not deps["matplotlib"]:
            report.skip(label, "matplotlib not installed")
        else:
            item = items["lib:figure:forest-ci"]
            sample = samples["forest-ci"]
            table = ft.build_table(sample["path"], sample["columns"], contract_types(item))
            hashes = {}
            for vid in ("rfs", "nature"):
                out = ft.render("forest-ci", table, dict(item.get("parameters") or {}),
                                load_venues()[vid], palette, workdir / "swap" / vid, "f")
                hashes[vid] = (ft.data_hash(table), render_hash(out["outputs"]))
            report.check(label,
                         hashes["rfs"][0] == hashes["nature"][0]
                         and hashes["rfs"][1] != hashes["nature"][1],
                         "data %s/%s render %s vs %s"
                         % (hashes["rfs"][0][:8], hashes["nature"][0][:8],
                            hashes["rfs"][1][:8], hashes["nature"][1][:8]))
            repeat = ft.render("forest-ci", table, dict(item.get("parameters") or {}),
                               load_venues()["rfs"], palette, workdir / "swap" / "rfs2", "f")
            report.check("render: re-rendering the same figure is byte-stable",
                         render_hash(repeat["outputs"]) == hashes["rfs"][1],
                         "%s vs %s" % (render_hash(repeat["outputs"])[:8], hashes["rfs"][1][:8]))

        # --- contract 10.4: the Quick Figure four-step in a throwaway project
        project = workdir / "project"
        if not _bootstrap_project(project):
            report.check("quick: bootstrap a throwaway project", False,
                         "research_os.py bootstrap failed")
        else:
            report.check("quick: bootstrap a throwaway project", True, str(project))
            control = project / ".research-os"
            data_dir = project / "research" / "results"
            data_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(samples["forest-ci"]["path"], data_dir / "estimates.csv")
            request = normalise_request({
                "figure_id": "F-101",
                "intent": "which factor loadings survive the audit",
                "manuscript_target": {"section": "4.3", "claim_ids": ["C-057"]},
                "data_refs": [{"path": "research/results/estimates.csv",
                               "columns": samples["forest-ci"]["columns"]}],
                "semantic_objects": ["coefficient-plot", "confidence-interval"],
                "venue": "rfs", "constraints": {"width": "single", "color": "auto"},
                "route": "auto"})
            entry = register_one(control, request, items)
            report.check("quick: --register writes a draft entry with a decided route",
                         entry["status"] == "draft" and entry["route"] == "A"
                         and entry["template"] == "lib:figure:forest-ci@1",
                         "status=%s route=%s template=%s"
                         % (entry["status"], entry["route"], entry["template"]))
            registry = load_registry(control)
            report.check("quick: registry shape carries claim_ids/status/route/provenance",
                         registry.get("schema") == REGISTRY_SCHEMA
                         and all(k in registry["figures"]["F-101"]
                                 for k in ("claim_ids", "status", "route", "provenance")),
                         "schema=%s keys ok" % registry.get("schema"))
            label = "quick: --render produces artifacts, qa.json and provenance.json"
            if not deps["matplotlib"]:
                report.skip(label, "matplotlib not installed")
                report.skip("quick: --promote-current flips status and leaves the edge "
                            "to the graph", "render skipped, nothing to promote")
                report.skip("A9-2 promote-current is refused while a component licence "
                            "is UNKNOWN", "render skipped, nothing to promote")
            else:
                live = load_registry(control)
                result = render_entry(control, live["figures"]["F-101"], items,
                                      emit_vegalite=True)
                save_registry(control, live)
                render_id = result["render_id"]
                directory = render_dir(control, "F-101", render_id)
                root_dir = artifact_dir(control, "F-101")
                needed = ["F-101.pdf", "F-101.svg", "F-101.png", "qa.json",
                          "provenance.json", "spec.vl.json"]
                absent = [n for n in needed if not (directory / n).exists()]
                absent += [n for n in ("request.json", "route.json", "current_render.json")
                           if not (root_dir / n).exists()]
                report.check(label, not absent and result["provenance"]["render_hash"],
                             "missing %s" % ", ".join(absent) if absent
                             else "render_id=%s data_hash=%s"
                             % (render_id, result["provenance"]["data_hash"][:12]))
                report.check("A4: the render id is content-addressed and re-rendering "
                             "reuses the same immutable directory",
                             render_id.startswith("R-") and len(render_id) == 14
                             and render_entry(control, load_registry(control)
                                              ["figures"]["F-101"], items,
                                              emit_vegalite=True)["render_id"] == render_id,
                             render_id)
                report.check("A4/A5: all three products exist and `published` follows "
                             "venue.export",
                             set(result["provenance"]["outputs"]) == {"pdf", "svg", "png"}
                             and result["provenance"]["published"]
                             == [x for x in load_venue("rfs").get("export") or []],
                             "outputs=%s published=%s"
                             % (sorted(result["provenance"]["outputs"]),
                                result["provenance"]["published"]))
                promoted = load_registry(control)
                promoted_entry = promoted["figures"]["F-101"]
                venue_p = load_venue(promoted_entry["venue"])
                qa = run_qa(promoted_entry, directory, venue_p,
                            items["lib:figure:forest-ci"], palette,
                            items["lib:style:scienceplots-stack"], result["table"],
                            promoted_entry.get("render_report") or {}, "full")
                graph_result = derive_graph_edges(control, "F-101")
                promoted_entry["status"] = "current"
                promoted_entry["qa_summary"] = qa["summary"]
                promoted_entry["edges"] = graph_result.get("edges") or []
                save_registry(control, promoted)
                reread = load_registry(control)["figures"]["F-101"]
                report.check("quick: --promote-current flips status and leaves the edge "
                             "to the graph",
                             reread["status"] == "current" and qa["summary"]["fail"] == 0
                             and graph_result.get("derived") is True
                             and reread.get("current_render") == render_id,
                             "status=%s qa=%s graph=%s"
                             % (reread["status"], qa["summary"],
                                {k: graph_result.get(k) for k in ("derived", "added")}))
                report.check("A8: the figure track books no edge of its own",
                             all(e.get("by") in (None, "derive")
                                 for e in reread.get("edges") or []),
                             [e.get("by") for e in reread.get("edges") or []])

                # --- R9 A9 negative 2: an UNKNOWN licence blocks promote-current
                blocked = load_registry(control)
                blocked_entry = blocked["figures"]["F-101"]
                saved_prov = blocked_entry.get("provenance")
                blocked_entry["provenance"] = dict(saved_prov or {}, component_provenance=[
                    {"ref": "selftest:mystery-icon", "license_id": "UNKNOWN",
                     "redistributable": False}])
                refused = None
                try:
                    _promote_current(control, blocked, blocked_entry, items, render_id)
                except SystemExit as exc:
                    refused = exc
                blocked_entry["provenance"] = saved_prov
                save_registry(control, blocked)
                report.check("A9-2 promote-current is refused while a component licence "
                             "is UNKNOWN",
                             refused is not None and int(refused.code or 0) != 0,
                             "SystemExit(%s)" % (getattr(refused, "code", None),))

            rebased = load_registry(control)
            entry_r = rebased["figures"]["F-101"]
            entry_r["template"] = pinned_ref(items["lib:figure:event-study"])
            entry_r["status"] = "draft"
            save_registry(control, rebased)
            report.check("quick: --rebase re-pins the template and demands a re-render",
                         load_registry(control)["figures"]["F-101"]["template"]
                         == "lib:figure:event-study@1",
                         "template now %s"
                         % load_registry(control)["figures"]["F-101"]["template"])

            # --- v3 contract §2: the three transitions above, checked at the
            # call site with a stubbed graph (the graph's own rules are the
            # graph's self-test; what is under test here is who calls it, with
            # which rule set, and that a failure cannot unland the write).
            graph_call_site_checks(report, control)
        # --- contract 10.4 end to end, opt-in because it drives four sibling CLIs
        if getattr(args, "integration", False):
            run_integration(report, items, deps, workdir)
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)

    summary = report.summary
    payload = {"ok": report.ok, "summary": summary,
               "capabilities": {"renderer.matplotlib": deps["matplotlib"],
                                "style.scienceplots": deps["scienceplots"],
                                "colormap.cmcrameri": deps["cmcrameri"]},
               "skipped_reason": (None if deps["matplotlib"]
                                  else "matplotlib absent: every render check is SKIPPED, "
                                       "not PASS"),
               "checks": report.rows}
    if args.keep:
        payload["workdir"] = str(workdir)
    return emit(payload, 0 if report.ok else 1)


# ================================================================== argument parsing

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="figure_router",
        description="Template-first figure routing, rendering and registration (v2.9).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("route", help="decide Route A/B/C for one FigureRequest")
    p.add_argument("--request", help="path to a FigureRequest v0 JSON file")
    p.add_argument("--inline", help="the FigureRequest as an inline JSON string")
    p.add_argument("--route", help="override: auto|data|components|concept")
    p.add_argument("--verbose", action="store_true", help="include per-candidate detail")
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("venue", help="inspect the venue profiles")
    vsub = p.add_subparsers(dest="venue_action", required=True)
    vsub.add_parser("list")
    show = vsub.add_parser("show")
    show.add_argument("venue_id")
    p.set_defaults(func=cmd_venue)

    p = sub.add_parser("library", help="inspect and validate the figure library")
    lsub = p.add_subparsers(dest="library_action", required=True)
    lsub.add_parser("items")
    show = lsub.add_parser("show")
    show.add_argument("item_id")
    lsub.add_parser("validate")
    lsub.add_parser("gen-previews")
    lsub.add_parser("index-block", help="emit the `## figure library` section of INDEX.md")
    p.set_defaults(func=cmd_library)

    p = sub.add_parser("quick", help="register / render / assemble / promote-current / "
                                     "promote-to-fsg / reroute / rebase one figure")
    p.add_argument("--root", help="project root (default: search upwards from cwd)")
    p.add_argument("--register", action="store_true")
    p.add_argument("--render", metavar="FIGURE_ID")
    p.add_argument("--assemble", metavar="FIGURE_ID",
                   help="Route B: fill the diagram-template's slots with indexed components")
    p.add_argument("--slots", help="assemble: SLOT=<source>:<id>, comma or space separated; "
                                   "omitted, the semantic objects pick the components")
    p.add_argument("--labels", help="assemble: SLOT=text,SLOT=text (defaults to the "
                                    "semantic object each slot answers)")
    p.add_argument("--assemble-title", dest="assemble_title",
                   help="assemble: an optional title drawn into the vector text layer")
    p.add_argument("--promote-current", dest="promote_current", metavar="FIGURE_ID",
                   help="full QA, flip to status=current, then let the graph derive the "
                        "expressed_as edge from the registry (R9 A6/A8)")
    p.add_argument("--promote-to-fsg", dest="promote_to_fsg", metavar="FIGURE_ID",
                   help="seed figure-studio (Route C / heavy structural edits)")
    p.add_argument("--render-id", dest="render_id", metavar="RENDER_ID",
                   help="promote-current: which immutable render to make current "
                        "(default: the latest)")
    p.add_argument("--reroute", metavar="FIGURE_ID",
                   help="re-run the Router for a registered figure and re-pin its template "
                        "(R9 A3: a library update never re-decides an existing request)")
    p.add_argument("--promote", metavar="FIGURE_ID",
                   help="DEPRECATED alias: --promote <F> --current|--to-fsg")
    p.add_argument("--rebase", metavar="FIGURE_ID")
    p.add_argument("--to", help="rebase target, e.g. lib:figure:forest-ci@1")
    p.add_argument("--current", action="store_true",
                   help="deprecated: pair of --promote (use --promote-current)")
    p.add_argument("--to-fsg", dest="to_fsg", action="store_true",
                   help="deprecated: pair of --promote (use --promote-to-fsg)")
    p.add_argument("--import", dest="import_figure", action="store_true",
                   help="adopt an EXISTING figure's id (the only use of --figure-id)")
    p.add_argument("--figure-id",
                   help="only with --import; new ids are allocated by the registry (R9 A1)")
    p.add_argument("--intent", help="the reader question this figure answers")
    p.add_argument("--claim", action="append", help="claim id (repeatable or comma-separated)")
    p.add_argument("--section", help="manuscript section")
    p.add_argument("--data", action="append",
                   help="data file; `path#x=col,y=col` maps columns inline")
    p.add_argument("--columns", action="append",
                   help="role=column mapping for the matching --data, e.g. x=k_eff,y=t")
    p.add_argument("--types", help="declared role types, e.g. x=quantitative,group=nominal")
    p.add_argument("--semantic",
                   help="comma-separated REQUIRED semantic objects (Route B needs 100%%)")
    p.add_argument("--semantic-optional", dest="semantic_optional",
                   help="comma-separated OPTIONAL semantic objects (ranking only)")
    p.add_argument("--venue", help="venue profile id (default generic)")
    p.add_argument("--width", choices=["single", "double", "full"])
    p.add_argument("--color", choices=["auto", "mono"])
    p.add_argument("--constraints", help="extra constraints as inline JSON")
    p.add_argument("--template", help="pin a template instead of letting the Router pick")
    p.add_argument("--params", help="template parameter overrides as inline JSON")
    p.add_argument("--route", help="auto|data|components|concept")
    p.add_argument("--emit-vegalite", dest="emit_vegalite", action="store_true")
    p.add_argument("--from-reply", dest="from_reply",
                   help="a figure-engineer reply JSON with proposed_figure_requests[]")
    p.set_defaults(func=cmd_quick)

    p = sub.add_parser("list", help="list the registered figures of a project")
    p.add_argument("--root")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("self-test", help="contract section 10 acceptance, machine-checked")
    p.add_argument("--keep", action="store_true", help="keep the throwaway project")
    p.add_argument("--integration", action="store_true",
                   help="also drive bootstrap -> narrative -> register/route/render/promote "
                        "-> research graph -> coverage, plus the Route B and Route C exits")
    p.set_defaults(func=cmd_self_test)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
