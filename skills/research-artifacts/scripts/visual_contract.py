#!/usr/bin/env python3
"""Initialize, freeze, and validate two-stage research visual contracts."""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # pypdf is optional (declared in ../../requirements.txt): only PDF payload
    import pypdf  # noqa: F401  # checks in validate-delivery and self-test need it.

    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

PYPDF_DEGRADED_NOTE = (
    "pypdf is not installed, so PDF payloads cannot be verified. "
    "init/validate-request/freeze remain fully functional. "
    "Install pypdf (see the plugin's requirements.txt) to restore full "
    "validate-delivery and self-test coverage."
)


PROGRAM_SCHEMA = "research-artifacts/figure-program-v1"
DELIVERY_SCHEMA = "research-artifacts/delivery-manifest-v1"
FREEZE_SCHEMA = "research-artifacts/freeze-v1"
PROMPT_SCHEMA = "research-artifacts/stage-2-prompt-v1"
LOCK_SCHEMA = "research-artifacts/delivery-lock-v1"
TRANSACTION_SCHEMA = "research-artifacts/freeze-transaction-v1"
COMPOSITION_SCHEMA = "research-artifacts/composition-manifest-v1"
STRUCTURE_SNAPSHOT_SCHEMA = "research-artifacts/structure-snapshot-v1"
FIGMA_SOURCE_SCHEMA = "research-artifacts/figma-source-identity-v1"

STAGES = {
    "idea_only", "preliminary_study", "method_taking_shape",
    "experiments_underway", "manuscript_draft", "rejected_or_borderline",
    "final_submission_or_rebuttal",
}
PAPER_TYPES = {
    "theoretical_ml", "empirical_ml", "domain_application",
    "systems_tooling", "survey_position",
}
OWNER_PHASES = {
    "research-discovery", "research-evidence", "research-method",
    "research-experiments", "research-manuscript", "research-review",
    "research-reproducibility", "research-theory-siege",
}
FIGURE_ROLES = {
    "orientation", "explanation", "evidence", "diagnostic", "comparison", "synthesis",
}
FIGURE_TYPES = {
    "benchmark_plot", "ablation_sensitivity", "calibration_uncertainty",
    "heatmap_matrix", "embedding_projection", "method_architecture",
    "algorithm_workflow", "theory_proof_map", "data_provenance_protocol",
    "timeline_version_state", "network_hypergraph", "mechanism_schematic",
    "qualitative_case", "taxonomy_landscape", "result_table",
    "graphical_abstract", "mixed_panel",
}
EXACTNESS = {"E0", "E1", "E2"}
EDITABILITY = {"D0", "D1", "D2", "D3"}
GENERATIVITY = {"G0", "G1", "G2"}
ROUTES = {
    "exact-plot", "formal-graph", "drawio", "figma-design", "figjam-draft",
    "generative-draft", "generative-illustration", "latex-table",
    "deterministic-composite", "native-svg", "tikz", "ppt-native",
}
PLACEMENTS = {"single_column", "double_column", "full_page", "supplement", "cover", "slides"}
FORMATS = {"pdf", "svg", "eps", "tiff", "png", "tex", "drawio", "figma", "pptx"}
PUBLICATION_FORMATS = {"pdf", "svg", "eps", "tiff", "png"}
SPEC_FORMATS = {"json", "md"}
CAPTION_FORMATS = {"md", "tex", "txt"}
REPORT_FORMATS = {"md", "json", "txt", "html", "pdf"}
FIGURE_STATUSES = {
    "planned", "specified", "blocked", "removed",
}
VECTOR_OR_EDITABLE = {"pdf", "svg", "eps", "tex", "drawio", "figma", "pptx"}
NATIVE_EDITABLE_FORMATS = {"svg", "tex", "drawio", "figma", "pptx"}
COMPOSITION_EDITABLE_FORMATS = {"svg", "drawio", "pptx"}
COMPOSITION_EDITORS = {"svg-editor", "drawio", "powerpoint"}
COMPOSITION_MODES = {"assembly", "component-kit", "both"}
NUMERIC_TYPES = {
    "benchmark_plot", "ablation_sensitivity", "calibration_uncertainty",
    "heatmap_matrix", "embedding_projection",
}
GENERATIVE_ROUTES = {"generative-draft", "generative-illustration"}
DRAFT_ONLY_ROUTES = {"figjam-draft", "generative-draft"}
FINAL_DETERMINISTIC_ROUTES = {
    "exact-plot", "formal-graph", "drawio", "figma-design",
    "latex-table", "deterministic-composite", "native-svg", "tikz", "ppt-native",
}
FINAL_NON_GENERATIVE_ROUTES = FINAL_DETERMINISTIC_ROUTES
TYPE_CONTRACTS = {
    "benchmark_plot": ({"E2"}, {"D1", "D2"}, {"G0"}, {"exact-plot", "native-svg"}),
    "ablation_sensitivity": ({"E2"}, {"D1", "D2"}, {"G0"}, {"exact-plot", "native-svg"}),
    "calibration_uncertainty": ({"E2"}, {"D1", "D2"}, {"G0"}, {"exact-plot", "native-svg"}),
    "heatmap_matrix": ({"E2"}, {"D1", "D2"}, {"G0"}, {"exact-plot", "native-svg"}),
    "embedding_projection": ({"E2"}, {"D1", "D2"}, {"G0"}, {"exact-plot", "native-svg"}),
    "method_architecture": (
        {"E1", "E2"}, {"D1", "D2", "D3"}, {"G0", "G1"},
        {"formal-graph", "drawio", "figma-design", "figjam-draft", "generative-draft", "native-svg", "tikz", "ppt-native"},
    ),
    "algorithm_workflow": (
        {"E1", "E2"}, {"D1", "D2", "D3"}, {"G0", "G1"},
        {"formal-graph", "drawio", "figma-design", "figjam-draft", "generative-draft", "native-svg", "tikz", "ppt-native"},
    ),
    "theory_proof_map": (
        {"E2"}, {"D1", "D2", "D3"}, {"G0"},
        {"formal-graph", "drawio", "figma-design", "figjam-draft", "native-svg", "tikz", "ppt-native"},
    ),
    "data_provenance_protocol": (
        {"E2"}, {"D1", "D2", "D3"}, {"G0"},
        {"formal-graph", "drawio", "figma-design", "figjam-draft", "native-svg", "tikz", "ppt-native"},
    ),
    "timeline_version_state": (
        {"E2"}, {"D1", "D2", "D3"}, {"G0"},
        {"formal-graph", "drawio", "figma-design", "figjam-draft", "native-svg", "tikz", "ppt-native"},
    ),
    "network_hypergraph": (
        {"E0", "E1", "E2"}, {"D0", "D1", "D2", "D3"}, {"G0", "G1", "G2"},
        {"formal-graph", "drawio", "figma-design", "generative-draft", "generative-illustration", "deterministic-composite", "native-svg", "tikz", "ppt-native"},
    ),
    "mechanism_schematic": (
        {"E0", "E1", "E2"}, {"D0", "D1", "D2", "D3"}, {"G0", "G1", "G2"},
        {"drawio", "figma-design", "figjam-draft", "generative-draft", "generative-illustration", "deterministic-composite", "native-svg", "ppt-native"},
    ),
    "qualitative_case": (
        {"E2"}, {"D1", "D2", "D3"}, {"G0"},
        {"deterministic-composite", "native-svg", "ppt-native"},
    ),
    "taxonomy_landscape": (
        {"E1", "E2"}, {"D1", "D2", "D3"}, {"G0"},
        {"formal-graph", "drawio", "figma-design", "native-svg", "tikz", "ppt-native"},
    ),
    "result_table": ({"E2"}, {"D1", "D2"}, {"G0"}, {"latex-table"}),
    "graphical_abstract": (
        {"E0", "E1"}, {"D0", "D1", "D2", "D3"}, {"G0", "G1", "G2"},
        {"drawio", "figma-design", "figjam-draft", "generative-draft", "generative-illustration", "deterministic-composite", "native-svg", "ppt-native"},
    ),
    "mixed_panel": (
        {"E0", "E1", "E2"}, {"D1", "D2", "D3"}, {"G0"}, {"deterministic-composite"},
    ),
}
PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9 _/\-]{2,}\]")
HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
PROMPT_START = "---json\n"
PROMPT_END = "\n---\n"
REQUIRED_PROMPT_SECTIONS = {
    "## Role", "## Frozen project inputs", "## Paper-wide visual thesis",
    "## Non-negotiable constraints", "## Required execution sequence",
    "## Required deliverables", "## Completion condition",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def load_json_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value, hashlib.sha256(raw).hexdigest()


def json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json_text(value))
    temp.replace(path)


def resolve_path(raw: str, project_root: Path) -> Path:
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def list_of_strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def normalized_identity(value: Any) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def artifact_format(raw: str) -> str:
    suffix = Path(raw).suffix.lower().lstrip(".")
    return "tiff" if suffix in {"tif", "tiff"} else suffix


def validate_artifact_payload(path: Path, prefix: str, errors: list[str]) -> str:
    snapshot_sha = sha256_file(path)
    if path.stat().st_size < 1:
        errors.append(f"{prefix} is empty")
        return snapshot_sha
    fmt = artifact_format(str(path))
    try:
        if fmt == "pdf":
            if not PYPDF_AVAILABLE:
                errors.append(f"{prefix} PDF check degraded: {PYPDF_DEGRADED_NOTE}")
                return snapshot_sha
            from pypdf import PdfReader

            reader = PdfReader(str(path), strict=True)
            if len(reader.pages) < 1:
                errors.append(f"{prefix} PDF has no pages")
        elif fmt == "svg":
            root = ET.parse(path).getroot()
            graphic_tags = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "image", "use"}
            descendants = {item.tag.split("}")[-1].lower() for item in root.iter()}
            has_canvas = bool(root.get("viewBox")) or (bool(root.get("width")) and bool(root.get("height")))
            if root.tag.split("}")[-1].lower() != "svg" or not has_canvas or not graphic_tags.intersection(descendants):
                errors.append(f"{prefix} is not a non-empty SVG graphic")
        elif fmt == "png":
            from PIL import Image

            with Image.open(path) as image:
                if image.width < 1 or image.height < 1:
                    errors.append(f"{prefix} PNG has invalid dimensions")
                image.verify()
        elif fmt == "tiff":
            from PIL import Image

            with Image.open(path) as image:
                if image.width < 1 or image.height < 1:
                    errors.append(f"{prefix} TIFF has invalid dimensions")
                image.verify()
        elif fmt == "eps":
            with path.open("rb") as handle:
                raw = handle.read(4096)
            if not raw.startswith(b"%!PS-Adobe") or b"%%BoundingBox:" not in raw[:4096]:
                errors.append(f"{prefix} is not a recognizable EPS")
        elif fmt == "drawio":
            root = ET.parse(path).getroot()
            if root.tag not in {"mxfile", "mxGraphModel"}:
                errors.append(f"{prefix} is not a recognizable Draw.io XML source")
        elif fmt == "pptx":
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                required = {"[Content_Types].xml", "ppt/presentation.xml"}
                slides = [name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
                if not required.issubset(names) or not slides:
                    errors.append(f"{prefix} is not a recognizable non-empty PPTX package")
        elif fmt == "json":
            json.loads(path.read_text(encoding="utf-8"))
        elif fmt in {"md", "txt", "tex", "py", "r", "jl", "mjs", "js", "html", "yaml", "yml", "figma"}:
            if not path.read_text(encoding="utf-8").strip():
                errors.append(f"{prefix} contains no substantive text")
    except Exception as exc:
        errors.append(f"{prefix} cannot be decoded as {fmt or 'an artifact'}: {exc}")
    return snapshot_sha


def validate_role_document(
    path: Path | None,
    prefix: str,
    allowed_formats: set[str],
    errors: list[str],
) -> None:
    if path is None:
        return
    fmt = artifact_format(str(path))
    if fmt not in allowed_formats:
        errors.append(f"{prefix} has an inappropriate file type: .{fmt or 'none'}")
        return
    if fmt == "pdf":
        return
    try:
        text = path.read_text(encoding="utf-8").strip()
        if fmt == "json":
            value = json.loads(text)
            if value in ({}, [], "", None):
                errors.append(f"{prefix} JSON has no substantive content")
        elif len(text) < 20:
            errors.append(f"{prefix} has no substantive content")
    except Exception as exc:
        errors.append(f"{prefix} cannot be read as a role document: {exc}")


def add_required(errors: list[str], obj: dict[str, Any], keys: set[str], prefix: str) -> None:
    for key in sorted(keys):
        if key not in obj:
            errors.append(f"{prefix} missing field: {key}")


def project_root_for(program: dict[str, Any], override: str | None) -> Path:
    raw = override or program.get("paper", {}).get("project_root")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("project root is missing; pass --project-root or set paper.project_root")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project root does not exist: {root}")
    return root


def input_paths(program: dict[str, Any]) -> list[str]:
    paper = program.get("paper", {})
    values: list[str] = []
    for key in ("manuscript_paths", "claim_evidence_map_paths"):
        raw_values = paper.get(key, [])
        if isinstance(raw_values, list):
            values.extend(item for item in raw_values if nonempty_string(item))
    for figure in program.get("figures", []):
        if not isinstance(figure, dict):
            continue
        for key in ("source_paths", "data_paths"):
            raw_values = figure.get(key, [])
            if isinstance(raw_values, list):
                values.extend(item for item in raw_values if nonempty_string(item))
        if isinstance(figure.get("panel_plan"), list):
            for panel in figure["panel_plan"]:
                if not isinstance(panel, dict):
                    continue
                for key in ("source_paths", "data_paths"):
                    raw_values = panel.get(key, [])
                    if isinstance(raw_values, list):
                        values.extend(item for item in raw_values if nonempty_string(item))
    return list(dict.fromkeys(values))


def parse_prompt_contract(prompt_path: Path) -> tuple[dict[str, Any], str]:
    text = prompt_path.read_text(encoding="utf-8")
    if not text.startswith(PROMPT_START):
        raise ValueError("Stage-2 prompt must start with ---json machine-readable front matter")
    end = text.find(PROMPT_END, len(PROMPT_START))
    if end < 0:
        raise ValueError("Stage-2 prompt JSON front matter is not closed with ---")
    raw = text[len(PROMPT_START):end]
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Stage-2 prompt front matter must be a JSON object")
    return value, text[end + len(PROMPT_END):]


def validate_prompt_contract(
    contract: dict[str, Any],
    body: str,
    program: dict[str, Any],
    program_path: Path,
    project_root: Path,
) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "request_id", "version", "program_path",
        "freeze_record_path", "output_root", "delivery_manifest_path",
        "figure_ids", "source_manifest", "authorization", "stop_policy",
        "amendment_policy",
    }
    add_required(errors, contract, required, "prompt front matter")
    if contract.get("schema_version") != PROMPT_SCHEMA:
        errors.append("prompt front matter schema_version is unsupported")
    if contract.get("request_id") != program.get("request_id"):
        errors.append("prompt front matter request_id does not match figure program")
    if contract.get("version") != program.get("version"):
        errors.append("prompt front matter version does not match figure program")
    path_pairs = (
        ("program_path", str(program_path)),
        ("freeze_record_path", program.get("phase_1", {}).get("freeze_record_path")),
        ("delivery_manifest_path", program.get("delivery_manifest_path")),
    )
    for key, expected_raw in path_pairs:
        actual_raw = contract.get(key)
        if not nonempty_string(actual_raw) or not nonempty_string(expected_raw):
            errors.append(f"prompt front matter {key} must be a non-empty path")
        elif resolve_path(actual_raw, project_root) != resolve_path(expected_raw, project_root):
            errors.append(f"prompt front matter {key} does not match figure program")
    output_root = contract.get("output_root")
    if not nonempty_string(output_root):
        errors.append("prompt front matter output_root must be a non-empty path")
    else:
        resolved_output = resolve_path(output_root, project_root)
        if not is_within(resolved_output, project_root):
            errors.append("prompt front matter output_root must stay inside the project root")
        elif resolved_output != program_path.parent.resolve():
            errors.append("prompt front matter output_root must contain figure-program.json")
    expected_ids = [
        item.get("figure_id") for item in program.get("figures", [])
        if isinstance(item, dict) and item.get("status") != "removed"
    ]
    if contract.get("figure_ids") != expected_ids:
        errors.append("prompt front matter figure_ids must exactly match retained figures")
    source_manifest = contract.get("source_manifest")
    source_paths: list[str] = []
    if not isinstance(source_manifest, list):
        errors.append("prompt front matter source_manifest must be a list")
    else:
        for index, item in enumerate(source_manifest):
            if not isinstance(item, dict) or not nonempty_string(item.get("path")) or not nonempty_string(item.get("role")):
                errors.append(f"prompt front matter source_manifest[{index}] requires non-empty path and role")
            else:
                source_paths.append(item["path"])
        if len(source_paths) != len(set(source_paths)):
            errors.append("prompt front matter source_manifest contains duplicate paths")
        if set(source_paths) != set(input_paths(program)):
            errors.append("prompt front matter source_manifest must exactly cover frozen inputs")
    authorization = contract.get("authorization")
    if not isinstance(authorization, dict):
        errors.append("prompt front matter authorization must be an object")
    else:
        for key in ("external_writes", "external_uploads", "generative_final"):
            if not nonempty_string(authorization.get(key)):
                errors.append(f"prompt front matter authorization.{key} must be non-empty")
    if not nonempty_string(contract.get("stop_policy")):
        errors.append("prompt front matter stop_policy must be non-empty")
    if contract.get("amendment_policy") != "new_version_on_semantic_change":
        errors.append("prompt front matter amendment_policy must be new_version_on_semantic_change")
    if len(body.strip()) < 500:
        errors.append("Stage-2 prompt body is too short to be independently executable")
    for section in sorted(REQUIRED_PROMPT_SECTIONS):
        if section not in body:
            errors.append(f"Stage-2 prompt is missing required section: {section}")
    return errors


def validate_route_contract(
    errors: list[str],
    prefix: str,
    exactness: Any,
    generativity: Any,
    route: Any,
    fallback: Any,
) -> None:
    if exactness == "E2" and generativity != "G0":
        errors.append(f"{prefix}: E2 requires G0")
    if isinstance(route, str) and route in GENERATIVE_ROUTES and generativity == "G0":
        errors.append(f"{prefix}: G0 cannot use a generative route")
    if generativity == "G1":
        if route != "generative-draft":
            errors.append(f"{prefix}: G1 requires generative-draft as the preferred route")
        if not isinstance(fallback, str) or fallback not in FINAL_DETERMINISTIC_ROUTES:
            errors.append(f"{prefix}: G1 requires a deterministic final rebuild fallback")
    if generativity == "G2":
        if exactness != "E0":
            errors.append(f"{prefix}: G2 is allowed only with E0")
        if route != "generative-illustration":
            errors.append(f"{prefix}: G2 must use generative-illustration")
    if isinstance(route, str) and route in DRAFT_ONLY_ROUTES and (not isinstance(fallback, str) or fallback not in FINAL_DETERMINISTIC_ROUTES):
        errors.append(f"{prefix}: draft-only route requires a deterministic final fallback")


def validate_type_contract(
    errors: list[str],
    prefix: str,
    figure_type: Any,
    exactness: Any,
    editability: Any,
    generativity: Any,
    route: Any,
    fallback: Any,
) -> None:
    contract = TYPE_CONTRACTS.get(figure_type) if isinstance(figure_type, str) else None
    if contract is None:
        return
    allowed_exactness, allowed_editability, allowed_generativity, allowed_routes = contract
    if exactness not in allowed_exactness:
        errors.append(f"{prefix}: {figure_type} does not allow exactness {exactness}")
    if editability not in allowed_editability:
        errors.append(f"{prefix}: {figure_type} does not allow editability {editability}")
    if generativity not in allowed_generativity:
        errors.append(f"{prefix}: {figure_type} does not allow generativity {generativity}")
    if route not in allowed_routes:
        errors.append(f"{prefix}: {figure_type} does not allow preferred_route {route}")
    if nonempty_string(fallback) and fallback not in allowed_routes:
        errors.append(f"{prefix}: {figure_type} does not allow fallback_route {fallback}")
    if exactness in {"E1", "E2"} and editability == "D0":
        errors.append(f"{prefix}: E1/E2 content cannot use D0")
    if figure_type == "network_hypergraph" and exactness in {"E1", "E2"}:
        if generativity != "G0":
            errors.append(f"{prefix}: exact/semantic network_hypergraph content requires G0")
        if route in {"generative-draft", "generative-illustration"}:
            errors.append(f"{prefix}: generation is allowed only for an E0 network_hypergraph metaphor")
    validate_route_contract(errors, prefix, exactness, generativity, route, fallback)


def validate_composition_plan(
    plan: Any,
    prefix: str,
    primary_formats: Any,
    errors: list[str],
) -> None:
    if not isinstance(plan, dict):
        errors.append(f"{prefix} must be an object for D3 composition-ready work")
        return
    add_required(
        errors,
        plan,
        {
            "mode", "target_editors", "hierarchy", "min_top_level_components",
            "max_ungroup_steps_to_primitives", "component_board_required",
            "top_level_components", "allowed_flattening", "locked_elements",
        },
        prefix,
    )
    mode = plan.get("mode")
    if mode not in COMPOSITION_MODES:
        errors.append(f"{prefix}.mode is invalid")
    editors = plan.get("target_editors")
    if not list_of_strings(editors) or not editors:
        errors.append(f"{prefix}.target_editors must contain at least one editor")
        editors = []
    elif any(editor not in COMPOSITION_EDITORS for editor in editors):
        errors.append(f"{prefix}.target_editors contains an unsupported editor")
    formats = set(primary_formats) if isinstance(primary_formats, list) else set()
    editor_formats = {
        "drawio": "drawio", "svg-editor": "svg", "powerpoint": "pptx",
    }
    for editor in editors:
        required_format = editor_formats.get(editor)
        if required_format and required_format not in formats:
            errors.append(f"{prefix}: target editor {editor} requires .{required_format} in primary_formats")

    hierarchy = plan.get("hierarchy")
    if not list_of_strings(hierarchy) or len(hierarchy) < 3 or len(hierarchy) > 4:
        errors.append(f"{prefix}.hierarchy must contain 3-4 named levels")
        hierarchy = []
    elif tuple(hierarchy) not in {
        ("assembly", "module", "primitive"),
        ("assembly", "panel", "module", "primitive"),
    }:
        errors.append(f"{prefix}.hierarchy must run assembly -> optional panel -> module -> primitive")
    min_components = plan.get("min_top_level_components")
    if not isinstance(min_components, int) or isinstance(min_components, bool) or not 2 <= min_components <= 20:
        errors.append(f"{prefix}.min_top_level_components must be an integer from 2 to 20")
        min_components = 2
    ungroup_steps = plan.get("max_ungroup_steps_to_primitives")
    if not isinstance(ungroup_steps, int) or isinstance(ungroup_steps, bool) or not 2 <= ungroup_steps <= 3:
        errors.append(f"{prefix}.max_ungroup_steps_to_primitives must be 2 or 3")
    elif hierarchy and ungroup_steps != len(hierarchy) - 1:
        errors.append(f"{prefix}.max_ungroup_steps_to_primitives must match the declared hierarchy")
    board_required = plan.get("component_board_required")
    if not isinstance(board_required, bool):
        errors.append(f"{prefix}.component_board_required must be boolean")
    elif mode in {"component-kit", "both"} and board_required is not True:
        errors.append(f"{prefix}: component-kit/both mode requires a component board")

    components = plan.get("top_level_components")
    if not isinstance(components, list):
        errors.append(f"{prefix}.top_level_components must be a list")
        components = []
    elif len(components) < min_components:
        errors.append(f"{prefix}.top_level_components is smaller than min_top_level_components")
    component_ids: set[str] = set()
    for index, component in enumerate(components):
        item_prefix = f"{prefix}.top_level_components[{index}]"
        if not isinstance(component, dict):
            errors.append(f"{item_prefix} must be an object")
            continue
        add_required(errors, component, {"component_id", "job", "reusable"}, item_prefix)
        component_id = component.get("component_id")
        if not isinstance(component_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", component_id):
            errors.append(f"{item_prefix}.component_id is invalid")
        elif component_id in component_ids:
            errors.append(f"{prefix}.top_level_components has duplicate component_id: {component_id}")
        else:
            component_ids.add(component_id)
        if not nonempty_string(component.get("job")):
            errors.append(f"{item_prefix}.job must be non-empty")
        if not isinstance(component.get("reusable"), bool):
            errors.append(f"{item_prefix}.reusable must be boolean")
    for key in ("allowed_flattening", "locked_elements"):
        if not list_of_strings(plan.get(key)):
            errors.append(f"{prefix}.{key} must be a list of non-empty strings")


def native_structure_inventory(path: Path) -> dict[str, Any] | None:
    """Read stable object IDs, parent links, groups, and flattening clues."""
    fmt = artifact_format(str(path))
    inventory: dict[str, Any] = {
        "object_ids": set(), "group_ids": set(), "parent_by_id": {},
        "image_ids": set(), "canvas_by_id": {}, "picture_only_canvases": set(),
        "full_canvas_image": False, "bounds_by_id": {}, "component_id_by_native": {},
        "required_snapshot_ids": set(),
    }
    if fmt == "drawio":
        root = ET.parse(path).getroot()
        for page_index, diagram in enumerate([item for item in root.iter() if item.tag.split("}")[-1] == "diagram"] or [root]):
            canvas = diagram.get("name") or diagram.get("id") or f"page-{page_index + 1}"
            cells = [item for item in diagram.iter() if item.tag.split("}")[-1] == "mxCell"]
            page_non_groups: list[str] = []
            page_images: set[str] = set()
            for item in cells:
                object_id = item.get("id")
                if not nonempty_string(object_id):
                    continue
                inventory["object_ids"].add(object_id)
                inventory["canvas_by_id"][object_id] = canvas
                parent = item.get("parent")
                if nonempty_string(parent):
                    inventory["parent_by_id"][object_id] = parent
                style = (item.get("style") or "").lower().split(";")
                if "group" in style:
                    inventory["group_ids"].add(object_id)
                    inventory["required_snapshot_ids"].add(object_id)
                elif item.get("vertex") == "1" or item.get("edge") == "1":
                    page_non_groups.append(object_id)
                    inventory["required_snapshot_ids"].add(object_id)
                if "image" in style:
                    inventory["image_ids"].add(object_id)
                    page_images.add(object_id)
            if page_non_groups and set(page_non_groups).issubset(page_images):
                inventory["picture_only_canvases"].add(canvas)
        return inventory
    if fmt == "svg":
        root = ET.parse(path).getroot()

        def dimension(raw: str | None, total: float | None) -> float | None:
            if not nonempty_string(raw):
                return None
            value = raw.strip()
            if value.endswith("%") and total is not None:
                try:
                    return float(value[:-1]) * total / 100.0
                except ValueError:
                    return None
            match = re.match(r"[-+]?\d*\.?\d+", value)
            return float(match.group(0)) if match else None

        canvas_width = dimension(root.get("width"), None)
        canvas_height = dimension(root.get("height"), None)
        view_box = root.get("viewBox")
        if view_box:
            parts = re.split(r"[ ,]+", view_box.strip())
            if len(parts) == 4:
                try:
                    canvas_width = canvas_width or float(parts[2])
                    canvas_height = canvas_height or float(parts[3])
                except ValueError:
                    pass

        def walk(node: ET.Element, nearest_parent_id: str | None) -> None:
            tag = node.tag.split("}")[-1].lower()
            object_id = node.get("id") if nonempty_string(node.get("id")) else None
            if object_id:
                inventory["object_ids"].add(object_id)
                if tag in {"g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "image", "use"}:
                    inventory["required_snapshot_ids"].add(object_id)
                inventory["canvas_by_id"][object_id] = "svg-canvas"
                if nearest_parent_id:
                    inventory["parent_by_id"][object_id] = nearest_parent_id
                if tag == "g":
                    inventory["group_ids"].add(object_id)
                if tag == "image":
                    inventory["image_ids"].add(object_id)
            if tag == "image" and canvas_width and canvas_height:
                style_values: dict[str, str] = {}
                for declaration in (node.get("style") or "").split(";"):
                    if ":" in declaration:
                        key, value = declaration.split(":", 1)
                        style_values[key.strip().lower()] = value.strip()
                width = dimension(node.get("width") or style_values.get("width"), canvas_width)
                height = dimension(node.get("height") or style_values.get("height"), canvas_height)
                x = dimension(node.get("x") or style_values.get("x"), canvas_width) or 0.0
                y = dimension(node.get("y") or style_values.get("y"), canvas_height) or 0.0
                if width and height and x <= canvas_width * 0.05 and y <= canvas_height * 0.05 and width >= canvas_width * 0.9 and height >= canvas_height * 0.9:
                    inventory["full_canvas_image"] = True
            next_parent = object_id or nearest_parent_id
            for child in list(node):
                walk(child, next_parent)

        walk(root, None)
        return inventory
    if fmt == "pptx":
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not re.fullmatch(r"ppt/slides/slide\d+\.xml", name):
                    continue
                slide_root = ET.fromstring(archive.read(name))
                slide_objects: list[str] = []
                slide_pictures: set[str] = set()

                def object_name(node: ET.Element) -> str | None:
                    for item in node.iter():
                        if item.tag.split("}")[-1] == "cNvPr" and nonempty_string(item.get("name")):
                            return item.get("name")
                    return None

                def walk(node: ET.Element, parent_group: str | None) -> None:
                    tag = node.tag.split("}")[-1]
                    current_parent = parent_group
                    if tag in {"grpSp", "sp", "cxnSp", "graphicFrame", "pic"}:
                        native_id = object_name(node)
                        if native_id:
                            inventory["object_ids"].add(native_id)
                            inventory["required_snapshot_ids"].add(native_id)
                            inventory["canvas_by_id"][native_id] = name
                            if parent_group:
                                inventory["parent_by_id"][native_id] = parent_group
                            if tag == "grpSp":
                                inventory["group_ids"].add(native_id)
                                current_parent = native_id
                            else:
                                slide_objects.append(native_id)
                                if tag == "pic":
                                    inventory["image_ids"].add(native_id)
                                    slide_pictures.add(native_id)
                    for child in list(node):
                        walk(child, current_parent)

                walk(slide_root, None)
                if slide_objects and set(slide_objects).issubset(slide_pictures):
                    inventory["picture_only_canvases"].add(name)
        return inventory
    return None


def native_is_descendant(native_id: str, ancestor_id: str, parent_by_id: dict[str, str]) -> bool:
    seen: set[str] = set()
    parent = parent_by_id.get(native_id)
    while parent and parent not in seen:
        if parent == ancestor_id:
            return True
        seen.add(parent)
        parent = parent_by_id.get(parent)
    return False


def native_group_depth(root_id: str, group_ids: set[str], parent_by_id: dict[str, str]) -> int:
    depths = [1]
    for group_id in group_ids:
        if group_id == root_id or not native_is_descendant(group_id, root_id, parent_by_id):
            continue
        depth = 1
        parent = parent_by_id.get(group_id)
        seen: set[str] = set()
        while parent and parent not in seen:
            seen.add(parent)
            if parent in group_ids:
                depth += 1
            if parent == root_id:
                break
            parent = parent_by_id.get(parent)
        depths.append(depth)
    return max(depths)


def validate_program(
    program: dict[str, Any],
    program_path: Path,
    project_root: Path,
    *,
    check_paths: bool,
    check_freeze: bool,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not is_within(program_path, project_root):
        errors.append("figure program must stay inside the project root")
    add_required(
        errors,
        program,
        {
            "schema_version", "request_id", "version", "created_at", "updated_at",
            "supersedes", "owner_phase", "paper", "phase_1", "visual_system_path",
            "figure_spec_dir", "delivery_manifest_path", "figures",
        },
        "program",
    )
    if program.get("schema_version") != PROGRAM_SCHEMA:
        errors.append("program.schema_version is unsupported")
    if not isinstance(program.get("request_id"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", program.get("request_id", "")):
        errors.append("program.request_id must be 3-64 lowercase kebab-case characters")
    if not isinstance(program.get("version"), int) or program.get("version", 0) < 1:
        errors.append("program.version must be a positive integer")
    supersedes = program.get("supersedes")
    if supersedes is not None:
        if not isinstance(supersedes, dict):
            errors.append("program.supersedes must be null or an object")
        else:
            add_required(
                errors, supersedes,
                {"relation", "request_id", "version", "freeze_record_path", "freeze_sha256"},
                "program.supersedes",
            )
            if supersedes.get("relation") not in {"same_request_revision", "new_request_replacement"}:
                errors.append("program.supersedes.relation is invalid")
            if not nonempty_string(supersedes.get("request_id")):
                errors.append("program.supersedes.request_id must be non-empty")
            if not isinstance(supersedes.get("version"), int) or supersedes.get("version", 0) < 1:
                errors.append("program.supersedes.version must be a positive integer")
            prior_raw = supersedes.get("freeze_record_path")
            if not nonempty_string(prior_raw):
                errors.append("program.supersedes.freeze_record_path must be non-empty")
            else:
                prior_path = resolve_path(prior_raw, project_root)
                if not is_within(prior_path, project_root):
                    errors.append("program.supersedes.freeze_record_path must stay inside the project root")
                elif check_paths and not prior_path.is_file():
                    errors.append("program.supersedes.freeze_record_path does not exist")
                elif check_paths:
                    if HEX_SHA256.fullmatch(str(supersedes.get("freeze_sha256", ""))) and sha256_file(prior_path) != supersedes.get("freeze_sha256"):
                        errors.append("program.supersedes.freeze_sha256 does not match the prior freeze record")
                    try:
                        prior_freeze = load_json(prior_path)
                        if prior_freeze.get("schema_version") != FREEZE_SCHEMA:
                            errors.append("program.supersedes prior freeze schema is unsupported")
                        if prior_freeze.get("request_id") != supersedes.get("request_id") or prior_freeze.get("version") != supersedes.get("version"):
                            errors.append("program.supersedes metadata does not match the prior freeze record")
                    except Exception as exc:
                        errors.append(f"program.supersedes prior freeze record could not be read: {exc}")
            if not HEX_SHA256.fullmatch(str(supersedes.get("freeze_sha256", ""))):
                errors.append("program.supersedes.freeze_sha256 must be a 64-character lowercase SHA-256")
            if supersedes.get("relation") == "same_request_revision":
                if supersedes.get("request_id") != program.get("request_id"):
                    errors.append("same_request_revision must retain the prior request_id")
                if isinstance(supersedes.get("version"), int) and isinstance(program.get("version"), int) and program.get("version") <= supersedes.get("version"):
                    errors.append("same_request_revision must advance the version")
            if supersedes.get("relation") == "new_request_replacement" and supersedes.get("request_id") == program.get("request_id"):
                errors.append("new_request_replacement must use a new request_id")
    if isinstance(program.get("version"), int) and program.get("version", 0) > 1 and supersedes is None:
        errors.append("program.version > 1 requires supersedes lineage metadata")
    if program.get("owner_phase") not in OWNER_PHASES:
        errors.append("program.owner_phase is invalid")

    paper = program.get("paper")
    if not isinstance(paper, dict):
        errors.append("program.paper must be an object")
        paper = {}
    add_required(
        errors,
        paper,
        {
            "title", "project_root", "stage", "paper_type", "target_venue",
            "manuscript_paths", "claim_evidence_map_paths",
        },
        "paper",
    )
    if paper.get("stage") not in STAGES:
        errors.append("paper.stage is invalid")
    if paper.get("paper_type") not in PAPER_TYPES:
        errors.append("paper.paper_type is invalid")
    if not nonempty_string(paper.get("title")):
        errors.append("paper.title must be non-empty")
    for key in ("manuscript_paths", "claim_evidence_map_paths"):
        if not list_of_strings(paper.get(key)):
            errors.append(f"paper.{key} must be a list of non-empty strings")

    phase = program.get("phase_1")
    if not isinstance(phase, dict):
        errors.append("program.phase_1 must be an object")
        phase = {}
    add_required(
        errors,
        phase,
        {
            "status", "stage_2_prompt_path", "freeze_record_path", "frozen_at",
            "prompt_sha256", "change_policy",
        },
        "phase_1",
    )
    if phase.get("status") not in {"draft", "frozen"}:
        errors.append("phase_1.status is invalid")
    if phase.get("change_policy") != "new_version_on_semantic_change":
        errors.append("phase_1.change_policy must be new_version_on_semantic_change")
    if phase.get("status") == "frozen":
        if not nonempty_string(phase.get("frozen_at")):
            errors.append("frozen phase_1 requires frozen_at")
        if not HEX_SHA256.fullmatch(str(phase.get("prompt_sha256", ""))):
            errors.append("frozen phase_1.prompt_sha256 must be a 64-character lowercase SHA-256")

    output_fields = (
        (phase, "stage_2_prompt_path", "phase_1"),
        (phase, "freeze_record_path", "phase_1"),
        (program, "visual_system_path", "program"),
        (program, "figure_spec_dir", "program"),
        (program, "delivery_manifest_path", "program"),
    )
    for owner, key, prefix in output_fields:
        raw = owner.get(key)
        if not isinstance(raw, str) or not raw.strip():
            errors.append(f"{prefix}.{key} must be a non-empty path")
        elif not is_within(resolve_path(raw, project_root), project_root):
            errors.append(f"{prefix}.{key} must stay inside the project root")

    figures = program.get("figures")
    if not isinstance(figures, list) or not figures:
        errors.append("program.figures must contain at least one figure")
        figures = []
    seen: set[str] = set()
    for index, figure in enumerate(figures):
        prefix = f"figures[{index}]"
        if not isinstance(figure, dict):
            errors.append(f"{prefix} must be an object")
            continue
        add_required(
            errors,
            figure,
            {
                "figure_id", "title", "role", "reader_question", "three_second_takeaway",
                "paper_claim_ids", "figure_type", "exactness", "editability", "generativity",
                "source_paths", "data_paths", "panel_plan", "preferred_route", "fallback_route",
                "target_placement", "primary_formats", "preview_format",
                "editable_source_required", "caption_job", "forbidden_inferences",
                "status", "risks",
            },
            prefix,
        )
        figure_id = figure.get("figure_id")
        if not isinstance(figure_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", figure_id):
            errors.append(f"{prefix}.figure_id is invalid")
        elif figure_id in seen:
            errors.append(f"duplicate figure_id: {figure_id}")
        else:
            seen.add(figure_id)
        for key in ("title", "reader_question", "three_second_takeaway", "caption_job"):
            if not isinstance(figure.get(key), str) or not figure.get(key, "").strip():
                errors.append(f"{prefix}.{key} must be non-empty")
        if not isinstance(figure.get("role"), str) or figure.get("role") not in FIGURE_ROLES:
            errors.append(f"{prefix}.role is invalid")
        if not isinstance(figure.get("figure_type"), str) or figure.get("figure_type") not in FIGURE_TYPES:
            errors.append(f"{prefix}.figure_type is invalid")
        if not isinstance(figure.get("exactness"), str) or figure.get("exactness") not in EXACTNESS:
            errors.append(f"{prefix}.exactness is invalid")
        if not isinstance(figure.get("editability"), str) or figure.get("editability") not in EDITABILITY:
            errors.append(f"{prefix}.editability is invalid")
        if not isinstance(figure.get("generativity"), str) or figure.get("generativity") not in GENERATIVITY:
            errors.append(f"{prefix}.generativity is invalid")
        for key in ("paper_claim_ids", "source_paths", "data_paths", "primary_formats", "forbidden_inferences", "risks"):
            if not list_of_strings(figure.get(key)):
                errors.append(f"{prefix}.{key} must be a list of non-empty strings")
        if not figure.get("paper_claim_ids"):
            errors.append(f"{prefix}.paper_claim_ids must name at least one claim or limitation")
        panels = figure.get("panel_plan")
        if not isinstance(panels, list):
            errors.append(f"{prefix}.panel_plan must be a list")
            panels = []
        elif not panels:
            errors.append(f"{prefix}.panel_plan must contain at least one panel")
        if figure.get("figure_type") == "mixed_panel" and isinstance(panels, list) and len(panels) < 2:
            errors.append(f"{prefix}.mixed_panel must contain at least two panels")
        panel_ids: set[str] = set()
        mixed_exactness_rank = -1
        exactness_rank = {"E0": 0, "E1": 1, "E2": 2}
        for panel_index, panel in enumerate(panels):
            panel_prefix = f"{prefix}.panel_plan[{panel_index}]"
            if not isinstance(panel, dict):
                errors.append(f"{panel_prefix} must be an object")
                continue
            for key in ("panel_id", "job"):
                if not isinstance(panel.get(key), str) or not panel.get(key, "").strip():
                    errors.append(f"{panel_prefix}.{key} must be non-empty")
            panel_id = panel.get("panel_id")
            if isinstance(panel_id, str) and panel_id.strip():
                if panel_id in panel_ids:
                    errors.append(f"{prefix}.panel_plan has duplicate panel_id: {panel_id}")
                panel_ids.add(panel_id)
            if figure.get("figure_type") != "mixed_panel":
                continue
            add_required(
                errors,
                panel,
                {
                    "figure_type", "exactness", "editability", "generativity",
                    "source_paths", "data_paths", "preferred_route", "fallback_route",
                    "primary_formats", "preview_format", "editable_source_required",
                },
                panel_prefix,
            )
            panel_type = panel.get("figure_type")
            panel_exactness = panel.get("exactness")
            panel_editability = panel.get("editability")
            panel_generativity = panel.get("generativity")
            panel_route = panel.get("preferred_route")
            panel_fallback = panel.get("fallback_route")
            if not isinstance(panel_type, str) or panel_type not in FIGURE_TYPES - {"mixed_panel"}:
                errors.append(f"{panel_prefix}.figure_type is invalid or nested mixed_panel")
            if not isinstance(panel_exactness, str) or panel_exactness not in EXACTNESS:
                errors.append(f"{panel_prefix}.exactness is invalid")
            else:
                mixed_exactness_rank = max(mixed_exactness_rank, exactness_rank[panel_exactness])
            if not isinstance(panel_editability, str) or panel_editability not in EDITABILITY:
                errors.append(f"{panel_prefix}.editability is invalid")
            if not isinstance(panel_generativity, str) or panel_generativity not in GENERATIVITY:
                errors.append(f"{panel_prefix}.generativity is invalid")
            if not isinstance(panel_route, str) or panel_route not in ROUTES:
                errors.append(f"{panel_prefix}.preferred_route is invalid")
            if panel_fallback not in ("", None) and (not isinstance(panel_fallback, str) or panel_fallback not in ROUTES):
                errors.append(f"{panel_prefix}.fallback_route is invalid")
            for key in ("source_paths", "data_paths", "primary_formats"):
                if not list_of_strings(panel.get(key)):
                    errors.append(f"{panel_prefix}.{key} must be a list of non-empty strings")
            panel_formats = panel.get("primary_formats", [])
            if isinstance(panel_formats, list) and (not panel_formats or any(item not in FORMATS for item in panel_formats)):
                errors.append(f"{panel_prefix}.primary_formats must use supported formats")
            panel_format_set = set(panel_formats) if isinstance(panel_formats, list) else set()
            panel_has_final = bool(PUBLICATION_FORMATS.intersection(panel_format_set)) or (
                panel_route in {"latex-table", "tikz"} and "tex" in panel_format_set
            )
            if isinstance(panel_formats, list) and panel_formats and not panel_has_final:
                errors.append(f"{panel_prefix}.primary_formats requires at least one publication export format")
            if panel.get("preview_format") not in {"png", "none"}:
                errors.append(f"{panel_prefix}.preview_format must be png or none")
            if not isinstance(panel.get("editable_source_required"), bool):
                errors.append(f"{panel_prefix}.editable_source_required must be boolean")
            if panel_exactness in {"E1", "E2"} and not panel.get("source_paths"):
                errors.append(f"{panel_prefix}: E1/E2 requires source_paths")
            if panel_type in NUMERIC_TYPES and not panel.get("data_paths"):
                errors.append(f"{panel_prefix}: numeric panel requires data_paths")
            if panel_editability == "D2" and panel.get("editable_source_required") is not True:
                errors.append(f"{panel_prefix}: D2 requires editable_source_required=true")
            if panel_editability == "D2" and isinstance(panel_formats, list) and not NATIVE_EDITABLE_FORMATS.intersection(panel_formats):
                errors.append(f"{panel_prefix}: D2 requires a native-editable primary format")
            if panel_editability == "D3":
                if panel.get("editable_source_required") is not True:
                    errors.append(f"{panel_prefix}: D3 requires editable_source_required=true")
                if isinstance(panel_formats, list) and not COMPOSITION_EDITABLE_FORMATS.intersection(panel_formats):
                    errors.append(f"{panel_prefix}: D3 requires a composition-ready native format")
                if panel.get("preview_format") != "png":
                    errors.append(f"{panel_prefix}: D3 requires a PNG preview")
                validate_composition_plan(
                    panel.get("composition_plan"), f"{panel_prefix}.composition_plan", panel_formats, errors,
                )
            elif panel.get("composition_plan") not in (None, {}):
                errors.append(f"{panel_prefix}: composition_plan is reserved for D3")
            if panel_editability == "D1" and isinstance(panel_formats, list) and not VECTOR_OR_EDITABLE.intersection(panel_formats):
                errors.append(f"{panel_prefix}: D1 requires a vector or editable primary format")
            validate_type_contract(
                errors, panel_prefix, panel_type, panel_exactness, panel_editability,
                panel_generativity, panel_route, panel_fallback,
            )
        if not isinstance(figure.get("preferred_route"), str) or figure.get("preferred_route") not in ROUTES:
            errors.append(f"{prefix}.preferred_route is invalid")
        fallback = figure.get("fallback_route")
        if fallback not in ("", None) and (not isinstance(fallback, str) or fallback not in ROUTES):
            errors.append(f"{prefix}.fallback_route is invalid")
        if figure.get("target_placement") not in PLACEMENTS:
            errors.append(f"{prefix}.target_placement is invalid")
        formats = figure.get("primary_formats", [])
        if not isinstance(formats, list) or not formats or any(item not in FORMATS for item in formats):
            errors.append(f"{prefix}.primary_formats must use supported formats")
        format_set = set(formats) if isinstance(formats, list) else set()
        has_final_format = bool(PUBLICATION_FORMATS.intersection(format_set)) or (
            figure.get("preferred_route") in {"latex-table", "tikz"} and "tex" in format_set
        )
        if isinstance(formats, list) and formats and not has_final_format:
            errors.append(f"{prefix}.primary_formats requires at least one publication export format")
        if figure.get("preview_format") not in {"png", "none"}:
            errors.append(f"{prefix}.preview_format must be png or none")
        if not isinstance(figure.get("editable_source_required"), bool):
            errors.append(f"{prefix}.editable_source_required must be boolean")
        if figure.get("status") not in FIGURE_STATUSES:
            errors.append(f"{prefix}.status is invalid")

        exactness = figure.get("exactness")
        editability = figure.get("editability")
        generativity = figure.get("generativity")
        route = figure.get("preferred_route")
        if exactness in {"E1", "E2"} and not figure.get("source_paths"):
            errors.append(f"{prefix}: E1/E2 requires source_paths")
        if figure.get("figure_type") in NUMERIC_TYPES and not figure.get("data_paths"):
            errors.append(f"{prefix}: numeric figure type requires data_paths")
        if editability == "D2" and figure.get("editable_source_required") is not True:
            errors.append(f"{prefix}: D2 requires editable_source_required=true")
        if editability == "D2" and formats and not NATIVE_EDITABLE_FORMATS.intersection(formats):
            errors.append(f"{prefix}: D2 requires a native-editable primary format")
        if editability == "D3":
            if figure.get("editable_source_required") is not True:
                errors.append(f"{prefix}: D3 requires editable_source_required=true")
            if formats and not COMPOSITION_EDITABLE_FORMATS.intersection(formats):
                errors.append(f"{prefix}: D3 requires a composition-ready native format")
            if figure.get("preview_format") != "png":
                errors.append(f"{prefix}: D3 requires a PNG preview")
            validate_composition_plan(
                figure.get("composition_plan"), f"{prefix}.composition_plan", formats, errors,
            )
        elif figure.get("composition_plan") not in (None, {}):
            errors.append(f"{prefix}: composition_plan is reserved for D3")
        if editability == "D1" and formats and not VECTOR_OR_EDITABLE.intersection(formats):
            errors.append(f"{prefix}: D1/D2 requires a vector or editable primary format")
        validate_type_contract(
            errors, prefix, figure.get("figure_type"), exactness, editability,
            generativity, route, fallback,
        )
        if figure.get("figure_type") == "mixed_panel":
            expected_exactness = {0: "E0", 1: "E1", 2: "E2"}.get(mixed_exactness_rank)
            if expected_exactness is not None and exactness != expected_exactness:
                errors.append(f"{prefix}: mixed_panel exactness must equal its strictest panel ({expected_exactness})")
            if editability == "D0":
                errors.append(f"{prefix}: mixed_panel final composition must be D1 or D2")
            if generativity != "G0":
                errors.append(f"{prefix}: mixed_panel final composition requires G0")
            if route != "deterministic-composite":
                errors.append(f"{prefix}: mixed_panel preferred_route must be deterministic-composite")

    if check_paths:
        for raw in input_paths(program):
            path = resolve_path(raw, project_root)
            if not is_within(path, project_root):
                errors.append(f"input path must stay inside the project root: {raw}")
            elif not path.is_file():
                errors.append(f"input path is missing or not a file: {raw}")
        for key in ("stage_2_prompt_path",):
            raw = phase.get(key)
            if isinstance(raw, str) and raw:
                path = resolve_path(raw, project_root)
                if not path.is_file():
                    errors.append(f"{key} is missing: {raw}")
                else:
                    prompt_text = path.read_text(encoding="utf-8")
                    placeholders = sorted(set(PLACEHOLDER.findall(prompt_text)))
                    if placeholders:
                        errors.append("Stage-2 prompt still contains placeholders: " + ", ".join(placeholders[:10]))
                    try:
                        prompt_contract, prompt_body = parse_prompt_contract(path)
                        errors.extend(validate_prompt_contract(
                            prompt_contract, prompt_body, program, program_path, project_root,
                        ))
                    except Exception as exc:
                        errors.append(f"Stage-2 prompt contract is invalid: {exc}")

    if check_freeze and phase.get("status") == "frozen":
        freeze_raw = phase.get("freeze_record_path")
        freeze_path = resolve_path(freeze_raw, project_root) if isinstance(freeze_raw, str) else None
        if freeze_path is None or not freeze_path.is_file():
            errors.append("frozen program is missing freeze record")
        else:
            try:
                freeze = load_json(freeze_path)
                if freeze.get("schema_version") != FREEZE_SCHEMA:
                    errors.append("freeze record schema is unsupported")
                if freeze.get("request_id") != program.get("request_id") or freeze.get("version") != program.get("version"):
                    errors.append("freeze record request/version mismatch")
                freeze_program_raw = freeze.get("program_path")
                if not nonempty_string(freeze_program_raw) or resolve_path(freeze_program_raw, project_root) != program_path:
                    errors.append("freeze record program_path mismatch")
                if freeze.get("frozen_at") != phase.get("frozen_at"):
                    errors.append("freeze record frozen_at mismatch")
                if freeze.get("program_sha256") != sha256_file(program_path):
                    errors.append("figure program changed after freeze")
                prompt_raw = phase.get("stage_2_prompt_path")
                prompt_path = resolve_path(prompt_raw, project_root) if isinstance(prompt_raw, str) else None
                if prompt_path is None or not prompt_path.is_file():
                    errors.append("frozen Stage-2 prompt is missing")
                else:
                    actual_prompt_sha = sha256_file(prompt_path)
                    if freeze.get("prompt_sha256") != actual_prompt_sha:
                        errors.append("Stage-2 prompt changed after freeze")
                    if phase.get("prompt_sha256") != actual_prompt_sha:
                        errors.append("phase_1.prompt_sha256 does not match the frozen prompt")
                    if freeze.get("prompt_sha256") != phase.get("prompt_sha256"):
                        errors.append("freeze record and figure program prompt hashes differ")
                freeze_inputs = freeze.get("inputs")
                if not isinstance(freeze_inputs, list):
                    errors.append("freeze record inputs must be a list")
                    freeze_inputs = []
                expected: dict[str, Any] = {}
                for index, item in enumerate(freeze_inputs):
                    if not isinstance(item, dict) or not nonempty_string(item.get("path")) or not HEX_SHA256.fullmatch(str(item.get("sha256", ""))):
                        errors.append(f"freeze record inputs[{index}] is invalid")
                        continue
                    if item["path"] in expected:
                        errors.append(f"freeze record contains duplicate input path: {item['path']}")
                    expected[item["path"]] = item["sha256"]
                current_inputs = input_paths(program)
                if set(expected) != set(current_inputs):
                    errors.append("freeze record input paths do not exactly match figure program")
                for raw in current_inputs:
                    path = resolve_path(raw, project_root)
                    if path.is_file() and expected.get(raw) != sha256_file(path):
                        errors.append(f"frozen input changed or was not recorded: {raw}")
            except Exception as exc:  # validation must report, not crash
                errors.append(f"freeze record could not be validated: {exc}")
    return errors, warnings


def command_init(args: argparse.Namespace) -> int:
    skill_root = Path(__file__).resolve().parents[1]
    project_root = Path(args.project_root).expanduser().resolve()
    if not project_root.is_dir():
        raise ValueError(f"project root does not exist: {project_root}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", args.request_id):
        raise ValueError("request ID must be 3-64 lowercase kebab-case characters")
    if args.version < 1:
        raise ValueError("version must be a positive integer")
    output = Path(args.output_root)
    output_root = (project_root / output).resolve() if not output.is_absolute() else output.resolve()
    try:
        output_root.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("output root must stay inside the project root") from exc
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"visual program directory is not empty: {output_root}")
    for name in ("figure-specs", "sources", "component-kits", "exports", "previews", "captions", "reviews"):
        (output_root / name).mkdir(parents=True, exist_ok=True)

    program = load_json(skill_root / "assets" / "figure-program.template.json")
    timestamp = now_utc()
    request_id = args.request_id
    relative_root = output_root.relative_to(project_root).as_posix()
    program.update({
        "request_id": request_id,
        "version": args.version,
        "created_at": timestamp,
        "updated_at": timestamp,
        "owner_phase": args.owner_phase,
    })
    program["paper"].update({
        "title": args.paper_title,
        "project_root": str(project_root),
        "stage": args.stage,
        "paper_type": args.paper_type,
        "target_venue": args.venue,
    })
    program["phase_1"].update({
        "stage_2_prompt_path": f"{relative_root}/stage-2-prompt.md",
        "freeze_record_path": f"{relative_root}/freeze.json",
    })
    program["visual_system_path"] = f"{relative_root}/visual-system.md"
    program["figure_spec_dir"] = f"{relative_root}/figure-specs"
    program["delivery_manifest_path"] = f"{relative_root}/delivery-manifest.json"
    program_path = output_root / "figure-program.json"
    write_json(program_path, program)

    prompt_source = skill_root / "references" / "stage-2-master-prompt.md"
    prompt_target = output_root / "stage-2-prompt.md"
    prompt_target.write_text(prompt_source.read_text(encoding="utf-8"), encoding="utf-8")

    delivery = load_json(skill_root / "assets" / "delivery-manifest.template.json")
    delivery.update({
        "request_id": request_id,
        "version": args.version,
        "created_at": timestamp,
        "updated_at": timestamp,
        "delivery_lock_path": f"{relative_root}/delivery-lock.json",
    })
    write_json(output_root / "delivery-manifest.json", delivery)
    print(json.dumps({
        "initialized": True,
        "program": str(program_path),
        "stage_2_prompt": str(prompt_target),
        "delivery_manifest": str(output_root / "delivery-manifest.json"),
    }, ensure_ascii=False, indent=2))
    return 0


def command_validate_request(args: argparse.Namespace) -> int:
    program_path = Path(args.program).expanduser().resolve()
    program = load_json(program_path)
    project_root = project_root_for(program, args.project_root)
    errors, warnings = validate_program(
        program,
        program_path,
        project_root,
        check_paths=args.check_paths,
        check_freeze=True,
    )
    status = "stale" if any("changed after freeze" in item or "frozen input changed" in item for item in errors) else ("valid" if not errors else "invalid")
    print(json.dumps({"valid": not errors, "status": status, "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def apply_freeze_transaction(journal_path: Path, program_path: Path, project_root: Path) -> dict[str, Any]:
    journal = load_json(journal_path)
    if journal.get("schema_version") != TRANSACTION_SCHEMA:
        raise ValueError("freeze transaction journal schema is unsupported")
    targets = journal.get("targets")
    documents = journal.get("documents")
    if not isinstance(targets, dict) or not isinstance(documents, dict):
        raise ValueError("freeze transaction journal is malformed")
    target_paths: dict[str, Path] = {}
    for key in ("program", "manifest", "freeze"):
        raw = targets.get(key)
        document = documents.get(key)
        if not nonempty_string(raw) or not isinstance(document, dict):
            raise ValueError(f"freeze transaction is missing target/document: {key}")
        path = Path(raw).expanduser().resolve()
        if not is_within(path, project_root):
            raise ValueError(f"freeze transaction target escapes project root: {key}")
        target_paths[key] = path
    if target_paths["program"] != program_path or len(set(target_paths.values())) != 3:
        raise ValueError("freeze transaction targets are aliased or bound to another program")
    if journal_path != target_paths["freeze"].with_name(".freeze-transaction.json"):
        raise ValueError("freeze transaction journal is not at the canonical path")
    frozen_program = documents["program"]
    frozen_manifest = documents["manifest"]
    freeze = documents["freeze"]
    document_sha = journal.get("document_sha256")
    if not isinstance(document_sha, dict):
        raise ValueError("freeze transaction document hashes are missing")
    for key, document in documents.items():
        if document_sha.get(key) != sha256_text(json_text(document)):
            raise ValueError(f"freeze transaction embedded document hash mismatch: {key}")
    if freeze.get("schema_version") != FREEZE_SCHEMA:
        raise ValueError("freeze transaction freeze schema is unsupported")
    if frozen_program.get("request_id") != freeze.get("request_id") or frozen_program.get("version") != freeze.get("version"):
        raise ValueError("freeze transaction request/version mismatch")
    if not nonempty_string(freeze.get("program_path")) or resolve_path(freeze["program_path"], project_root) != program_path:
        raise ValueError("freeze transaction freeze program_path mismatch")
    if resolve_path(frozen_program.get("delivery_manifest_path", ""), project_root) != target_paths["manifest"]:
        raise ValueError("freeze transaction manifest target mismatch")
    if resolve_path(frozen_program.get("phase_1", {}).get("freeze_record_path", ""), project_root) != target_paths["freeze"]:
        raise ValueError("freeze transaction freeze target mismatch")
    if frozen_program.get("phase_1", {}).get("status") != "frozen":
        raise ValueError("freeze transaction program is not frozen")
    if frozen_program.get("phase_1", {}).get("frozen_at") != freeze.get("frozen_at"):
        raise ValueError("freeze transaction frozen_at mismatch")
    if frozen_program.get("phase_1", {}).get("prompt_sha256") != freeze.get("prompt_sha256"):
        raise ValueError("freeze transaction prompt hash mismatch")
    if freeze.get("program_sha256") != sha256_text(json_text(frozen_program)):
        raise ValueError("freeze transaction program hash mismatch")
    if frozen_manifest.get("schema_version") != DELIVERY_SCHEMA:
        raise ValueError("freeze transaction manifest schema is unsupported")
    if frozen_manifest.get("request_id") != freeze.get("request_id") or frozen_manifest.get("version") != freeze.get("version"):
        raise ValueError("freeze transaction manifest request/version mismatch")
    if frozen_manifest.get("program_sha256") != freeze.get("program_sha256") or frozen_manifest.get("prompt_sha256") != freeze.get("prompt_sha256"):
        raise ValueError("freeze transaction manifest hash binding mismatch")
    lock_raw = frozen_manifest.get("delivery_lock_path")
    if not nonempty_string(lock_raw) or resolve_path(lock_raw, project_root) != target_paths["manifest"].with_name("delivery-lock.json"):
        raise ValueError("freeze transaction delivery lock path is not canonical")
    current_program_sha = sha256_file(program_path) if program_path.is_file() else None
    allowed_program_hashes = {
        journal.get("original_program_sha256"),
        freeze.get("program_sha256"),
    }
    if current_program_sha not in allowed_program_hashes:
        raise ValueError("cannot recover freeze transaction because figure-program.json changed")
    current_manifest_sha = sha256_file(target_paths["manifest"]) if target_paths["manifest"].is_file() else None
    allowed_manifest_hashes = {
        journal.get("original_manifest_sha256"),
        sha256_text(json_text(frozen_manifest)),
    }
    if current_manifest_sha not in allowed_manifest_hashes:
        raise ValueError("cannot recover freeze transaction because delivery-manifest.json changed")
    if target_paths["freeze"].is_file() and sha256_file(target_paths["freeze"]) != sha256_text(json_text(freeze)):
        raise ValueError("cannot recover freeze transaction because freeze.json conflicts")
    prompt_raw = frozen_program.get("phase_1", {}).get("stage_2_prompt_path")
    prompt_path = resolve_path(prompt_raw, project_root) if nonempty_string(prompt_raw) else None
    if freeze.get("prompt_path") != prompt_raw:
        raise ValueError("freeze transaction prompt path mismatch")
    if prompt_path is None or not prompt_path.is_file() or sha256_file(prompt_path) != freeze.get("prompt_sha256"):
        raise ValueError("cannot recover freeze transaction because the Stage-2 prompt changed")
    freeze_inputs = freeze.get("inputs")
    if not isinstance(freeze_inputs, list):
        raise ValueError("cannot recover malformed freeze transaction inputs")
    recorded_inputs: dict[str, str] = {}
    for item in freeze_inputs:
        if not isinstance(item, dict) or not nonempty_string(item.get("path")) or not HEX_SHA256.fullmatch(str(item.get("sha256", ""))):
            raise ValueError("cannot recover malformed freeze transaction inputs")
        if item["path"] in recorded_inputs:
            raise ValueError("cannot recover freeze transaction with duplicate input paths")
        recorded_inputs[item["path"]] = item["sha256"]
        input_path = resolve_path(item["path"], project_root)
        if not is_within(input_path, project_root) or not input_path.is_file() or sha256_file(input_path) != item.get("sha256"):
            raise ValueError(f"cannot recover freeze transaction because an input changed: {item['path']}")
    if set(recorded_inputs) != set(input_paths(frozen_program)):
        raise ValueError("cannot recover freeze transaction because recorded inputs do not match the program")
    write_json(target_paths["manifest"], documents["manifest"])
    write_json(target_paths["program"], frozen_program)
    write_json(target_paths["freeze"], freeze)  # commit marker is written last
    if sha256_file(prompt_path) != freeze.get("prompt_sha256"):
        raise ValueError("Stage-2 prompt changed while committing the freeze transaction")
    for raw, expected_sha in recorded_inputs.items():
        input_path = resolve_path(raw, project_root)
        if not input_path.is_file() or sha256_file(input_path) != expected_sha:
            raise ValueError(f"input changed while committing the freeze transaction: {raw}")
    if sha256_file(target_paths["program"]) != freeze.get("program_sha256"):
        raise ValueError("figure program changed while committing the freeze transaction")
    if sha256_file(target_paths["manifest"]) != sha256_text(json_text(frozen_manifest)):
        raise ValueError("delivery manifest changed while committing the freeze transaction")
    if sha256_file(target_paths["freeze"]) != sha256_text(json_text(freeze)):
        raise ValueError("freeze record changed while committing the freeze transaction")
    journal_path.unlink(missing_ok=True)
    return freeze


def command_freeze(args: argparse.Namespace) -> int:
    program_path = Path(args.program).expanduser().resolve()
    program, initial_program_sha = load_json_snapshot(program_path)
    project_root = project_root_for(program, args.project_root)
    phase = program.get("phase_1", {})
    freeze_path = resolve_path(phase.get("freeze_record_path", ""), project_root)
    journal_path = freeze_path.with_name(".freeze-transaction.json")
    if journal_path.is_file():
        freeze = apply_freeze_transaction(journal_path, program_path, project_root)
        print(json.dumps({
            "frozen": True,
            "recovered_transaction": True,
            "freeze_record": str(freeze_path),
            "prompt_sha256": freeze.get("prompt_sha256"),
        }, ensure_ascii=False, indent=2))
        return 0
    if phase.get("status") == "frozen":
        errors, warnings = validate_program(
            program, program_path, project_root, check_paths=True, check_freeze=True,
        )
        print(json.dumps({
            "frozen": not errors,
            "already_frozen": not errors,
            "freeze_record": str(freeze_path),
            "prompt_sha256": phase.get("prompt_sha256"),
            "errors": errors,
            "warnings": warnings,
        }, ensure_ascii=False, indent=2))
        return 0 if not errors else 1
    if phase.get("status") != "draft":
        raise ValueError("only a draft request can be frozen; semantic changes require a new version with supersedes metadata")
    if freeze_path.exists():
        raise FileExistsError("freeze record already exists; create a new request/version for semantic changes")
    prompt_path = resolve_path(phase.get("stage_2_prompt_path", ""), project_root)
    delivery_path = resolve_path(program.get("delivery_manifest_path", ""), project_root)
    errors, warnings = validate_program(
        program, program_path, project_root, check_paths=True, check_freeze=False,
    )
    delivery: dict[str, Any] = {}
    initial_manifest_sha: str | None = None
    if not delivery_path.is_file():
        errors.append("canonical delivery manifest is missing before freeze")
    else:
        try:
            delivery, initial_manifest_sha = load_json_snapshot(delivery_path)
            if delivery.get("schema_version") != DELIVERY_SCHEMA:
                errors.append("canonical delivery manifest schema is unsupported")
            if delivery.get("request_id") != program.get("request_id"):
                errors.append("canonical delivery manifest request_id mismatch")
            if delivery.get("version") != program.get("version"):
                errors.append("canonical delivery manifest version mismatch")
            if delivery.get("program_sha256") not in ("", None):
                errors.append("canonical delivery manifest program_sha256 must be empty before freeze")
            if delivery.get("status") != "draft":
                errors.append("canonical delivery manifest must be draft before freeze")
            if delivery.get("prompt_sha256") not in ("", None):
                errors.append("canonical delivery manifest prompt_sha256 must be empty before freeze")
        except Exception as exc:
            errors.append(f"canonical delivery manifest could not be read: {exc}")
    lock_raw = delivery.get("delivery_lock_path") if isinstance(delivery, dict) else None
    lock_path = resolve_path(lock_raw, project_root) if nonempty_string(lock_raw) else None
    canonical_lock_path = delivery_path.with_name("delivery-lock.json")
    if lock_path is None or lock_path != canonical_lock_path:
        errors.append("delivery_lock_path must equal canonical delivery-lock.json beside the manifest")
    control_paths = [program_path, prompt_path, freeze_path, delivery_path, journal_path]
    if lock_path is not None:
        control_paths.append(lock_path)
    if any(not is_within(path, project_root) for path in control_paths):
        errors.append("all managed freeze paths must stay inside the project root")
    if len(set(control_paths)) != len(control_paths):
        errors.append("managed freeze paths must be distinct and may not alias one another")
    control_set = set(control_paths)
    for raw in input_paths(program):
        if resolve_path(raw, project_root) in control_set:
            errors.append(f"frozen scientific input aliases a managed control file: {raw}")
    if sha256_file(program_path) != initial_program_sha:
        errors.append("figure-program.json changed during freeze preparation")
    if initial_manifest_sha is not None and sha256_file(delivery_path) != initial_manifest_sha:
        errors.append("delivery-manifest.json changed during freeze preparation")
    if errors:
        print(json.dumps({"frozen": False, "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
        return 1

    timestamp = now_utc()
    prompt_sha = sha256_file(prompt_path)
    records = [
        {"path": raw, "sha256": sha256_file(resolve_path(raw, project_root))}
        for raw in input_paths(program)
    ]
    frozen_program = copy.deepcopy(program)
    frozen_program["phase_1"].update({
        "status": "frozen", "frozen_at": timestamp, "prompt_sha256": prompt_sha,
    })
    frozen_program["updated_at"] = timestamp
    program_sha = sha256_text(json_text(frozen_program))
    freeze = {
        "schema_version": FREEZE_SCHEMA,
        "request_id": frozen_program["request_id"],
        "version": frozen_program["version"],
        "frozen_at": timestamp,
        "program_path": str(program_path),
        "program_sha256": program_sha,
        "prompt_path": frozen_program["phase_1"]["stage_2_prompt_path"],
        "prompt_sha256": prompt_sha,
        "inputs": records,
    }
    frozen_delivery = copy.deepcopy(delivery)
    frozen_delivery.update({
        "request_id": frozen_program["request_id"],
        "version": frozen_program["version"],
        "program_sha256": program_sha,
        "prompt_sha256": prompt_sha,
        "updated_at": timestamp,
    })
    journal = {
        "schema_version": TRANSACTION_SCHEMA,
        "prepared_at": timestamp,
        "original_program_sha256": initial_program_sha,
        "original_manifest_sha256": initial_manifest_sha,
        "targets": {
            "program": str(program_path),
            "manifest": str(delivery_path),
            "freeze": str(freeze_path),
        },
        "documents": {
            "program": frozen_program,
            "manifest": frozen_delivery,
            "freeze": freeze,
        },
        "document_sha256": {
            "program": sha256_text(json_text(frozen_program)),
            "manifest": sha256_text(json_text(frozen_delivery)),
            "freeze": sha256_text(json_text(freeze)),
        },
    }
    write_json(journal_path, journal)
    applied = apply_freeze_transaction(journal_path, program_path, project_root)
    print(json.dumps({
        "frozen": True,
        "freeze_record": str(freeze_path),
        "prompt_sha256": applied.get("prompt_sha256"),
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))
    return 0


def validate_delivery_file(
    raw: Any,
    prefix: str,
    project_root: Path,
    errors: list[str],
    lock_hashes: dict[Path, str],
) -> Path | None:
    if not nonempty_string(raw):
        errors.append(f"{prefix} must be a non-empty path")
        return None
    path = resolve_path(raw, project_root)
    if not is_within(path, project_root):
        errors.append(f"{prefix} escapes the project root")
        return None
    if not path.is_file():
        errors.append(f"{prefix} does not exist: {raw}")
        return None
    if path in lock_hashes:
        return path
    snapshot_sha = validate_artifact_payload(path, prefix, errors)
    lock_hashes[path] = snapshot_sha
    return path


def snapshot_scientific_input(
    raw: Any,
    prefix: str,
    project_root: Path,
    errors: list[str],
    lock_hashes: dict[Path, str],
) -> Path | None:
    if not nonempty_string(raw):
        errors.append(f"{prefix} must be a non-empty path")
        return None
    path = resolve_path(raw, project_root)
    if not is_within(path, project_root):
        errors.append(f"{prefix} escapes the project root")
        return None
    if not path.is_file():
        errors.append(f"{prefix} does not exist: {raw}")
        return None
    if path.stat().st_size < 1:
        errors.append(f"{prefix} is empty")
        return None
    if path not in lock_hashes:
        lock_hashes[path] = sha256_file(path)
    return path


def validate_composition_manifest(
    raw: Any,
    contract: dict[str, Any],
    record: dict[str, Any],
    prefix: str,
    project_root: Path,
    errors: list[str],
    lock_hashes: dict[Path, str],
    editable_paths: set[Path],
    qa_paths: set[Path],
    preview_paths: set[Path],
) -> tuple[set[Path], set[Path]]:
    document_paths: set[Path] = set()
    asset_paths: set[Path] = set()
    manifest_path = validate_delivery_file(
        raw, f"{prefix}.composition_manifest_path", project_root, errors, lock_hashes,
    )
    if manifest_path is None:
        return document_paths, asset_paths
    document_paths.add(manifest_path)
    try:
        manifest = load_json(manifest_path)
    except Exception as exc:
        errors.append(f"{prefix}.composition_manifest_path cannot be read: {exc}")
        return document_paths, asset_paths
    add_required(
        errors,
        manifest,
        {
            "schema_version", "artifact_id", "mode", "target_editor",
            "native_source_paths", "component_board_paths", "structure_snapshot_path",
            "assembly_root_id", "top_level_component_ids", "hierarchy", "layer_order",
            "interconnect_group_id", "named_ports", "flattened_assets", "manual_edit_test",
        },
        f"{prefix}.composition_manifest",
    )
    if manifest.get("schema_version") != COMPOSITION_SCHEMA:
        errors.append(f"{prefix}.composition_manifest schema is unsupported")
    expected_artifact_id = contract.get("figure_id") or contract.get("panel_id")
    if manifest.get("artifact_id") != expected_artifact_id:
        errors.append(f"{prefix}.composition_manifest artifact_id does not match the frozen contract")
    plan = contract.get("composition_plan") if isinstance(contract.get("composition_plan"), dict) else {}
    plan_hierarchy = plan.get("hierarchy") if isinstance(plan.get("hierarchy"), list) else []
    plan_components = plan.get("top_level_components") if isinstance(plan.get("top_level_components"), list) else []
    plan_editors = plan.get("target_editors") if isinstance(plan.get("target_editors"), list) else []
    plan_locked = plan.get("locked_elements") if isinstance(plan.get("locked_elements"), list) else []
    plan_allowed_flattening = plan.get("allowed_flattening") if isinstance(plan.get("allowed_flattening"), list) else []
    required_depth = plan.get("max_ungroup_steps_to_primitives")
    if not isinstance(required_depth, int) or isinstance(required_depth, bool):
        required_depth = 0
    if manifest.get("mode") != plan.get("mode"):
        errors.append(f"{prefix}.composition_manifest mode does not match composition_plan")
    target_editor = manifest.get("target_editor")
    if target_editor not in COMPOSITION_EDITORS:
        errors.append(f"{prefix}.composition_manifest target_editor is invalid")
    elif target_editor not in plan_editors:
        errors.append(f"{prefix}.composition_manifest target_editor was not frozen in composition_plan")

    native_raw = manifest.get("native_source_paths")
    if not list_of_strings(native_raw) or not native_raw:
        errors.append(f"{prefix}.composition_manifest native_source_paths must be non-empty")
        native_raw = []
    native_paths: list[Path] = []
    for index, item in enumerate(native_raw):
        path = validate_delivery_file(
            item, f"{prefix}.composition_manifest.native_source_paths[{index}]",
            project_root, errors, lock_hashes,
        )
        if path is not None:
            native_paths.append(path)
            if path not in editable_paths:
                errors.append(f"{prefix}.composition_manifest native sources must be listed in editable_source_paths")
            if artifact_format(str(path)) not in COMPOSITION_EDITABLE_FORMATS:
                errors.append(f"{prefix}.composition_manifest native source is not composition-ready")
    editor_format = {
        "drawio": "drawio", "svg-editor": "svg", "powerpoint": "pptx",
    }.get(target_editor)
    if editor_format and not any(artifact_format(str(path)) == editor_format for path in native_paths):
        errors.append(f"{prefix}.composition_manifest target_editor lacks its matching native source")

    board_raw = manifest.get("component_board_paths")
    if not list_of_strings(board_raw):
        errors.append(f"{prefix}.composition_manifest component_board_paths must be a list of paths")
        board_raw = []
    if plan.get("component_board_required") is True and not board_raw:
        errors.append(f"{prefix}.composition_manifest requires a component board")
    for index, item in enumerate(board_raw):
        path = validate_delivery_file(
            item, f"{prefix}.composition_manifest.component_board_paths[{index}]",
            project_root, errors, lock_hashes,
        )
        if path is not None and path not in editable_paths:
            errors.append(f"{prefix}.composition_manifest component boards must be editable sources")

    snapshot_inventory: dict[str, Any] = {
        "object_ids": set(), "group_ids": set(), "parent_by_id": {},
        "image_ids": set(), "canvas_by_id": {}, "picture_only_canvases": set(),
        "full_canvas_image": False, "bounds_by_id": {}, "component_id_by_native": {},
    }
    snapshot: dict[str, Any] = {}
    snapshot_path = validate_delivery_file(
        manifest.get("structure_snapshot_path"),
        f"{prefix}.composition_manifest.structure_snapshot_path",
        project_root, errors, lock_hashes,
    )
    if snapshot_path is not None:
        document_paths.add(snapshot_path)
        try:
            snapshot = load_json(snapshot_path)
        except Exception as exc:
            errors.append(f"{prefix}.composition_manifest structure snapshot cannot be read: {exc}")
            snapshot = {}
        add_required(
            errors, snapshot,
            {
                "schema_version", "artifact_id", "editor", "captured_from_path",
                "captured_from_sha256", "capture_method", "capture_run_id", "nodes",
                "component_board_root_native_object_ids",
            },
            f"{prefix}.composition_manifest.structure_snapshot",
        )
        if snapshot.get("schema_version") != STRUCTURE_SNAPSHOT_SCHEMA:
            errors.append(f"{prefix}.composition_manifest structure snapshot schema is unsupported")
        if snapshot.get("artifact_id") != expected_artifact_id:
            errors.append(f"{prefix}.composition_manifest structure snapshot artifact_id mismatch")
        if snapshot.get("editor") != target_editor:
            errors.append(f"{prefix}.composition_manifest structure snapshot editor mismatch")
        expected_capture_method = {
            "drawio": "drawio-xml", "svg-editor": "svg-dom", "powerpoint": "pptx-ooxml",
        }.get(target_editor)
        if snapshot.get("capture_method") != expected_capture_method:
            errors.append(f"{prefix}.composition_manifest structure snapshot capture_method mismatch")
        if not nonempty_string(snapshot.get("capture_run_id")):
            errors.append(f"{prefix}.composition_manifest structure snapshot capture_run_id must be non-empty")
        if target_editor == "figma":
            figma_paths = [path for path in native_paths if artifact_format(str(path)) == "figma"]
            for figma_path in figma_paths:
                try:
                    figma_identity = load_json(figma_path)
                except Exception as exc:
                    errors.append(f"{prefix}.composition_manifest Figma source identity cannot be read: {exc}")
                    continue
                add_required(
                    errors, figma_identity,
                    {"schema_version", "file_key", "file_url", "capture_run_id", "verified_at"},
                    f"{prefix}.composition_manifest.figma_source",
                )
                if figma_identity.get("schema_version") != FIGMA_SOURCE_SCHEMA:
                    errors.append(f"{prefix}.composition_manifest Figma source schema is unsupported")
                if not nonempty_string(figma_identity.get("file_key")):
                    errors.append(f"{prefix}.composition_manifest Figma file_key must be non-empty")
                if not nonempty_string(figma_identity.get("file_url")) or "figma.com/" not in figma_identity.get("file_url", ""):
                    errors.append(f"{prefix}.composition_manifest Figma file_url is invalid")
                if figma_identity.get("capture_run_id") != snapshot.get("capture_run_id"):
                    errors.append(f"{prefix}.composition_manifest Figma capture_run_id mismatch")
                if not nonempty_string(figma_identity.get("verified_at")):
                    errors.append(f"{prefix}.composition_manifest Figma verified_at must be non-empty")
        captured_path = validate_delivery_file(
            snapshot.get("captured_from_path"),
            f"{prefix}.composition_manifest.structure_snapshot.captured_from_path",
            project_root, errors, lock_hashes,
        )
        if captured_path is not None:
            if captured_path not in native_paths:
                errors.append(f"{prefix}.composition_manifest structure snapshot must bind a native source")
            if snapshot.get("captured_from_sha256") != lock_hashes.get(captured_path):
                errors.append(f"{prefix}.composition_manifest structure snapshot source hash mismatch")
        nodes = snapshot.get("nodes")
        if not isinstance(nodes, list) or len(nodes) < 3:
            errors.append(f"{prefix}.composition_manifest structure snapshot nodes must be substantive")
            nodes = []
        for index, node in enumerate(nodes):
            item_prefix = f"{prefix}.composition_manifest.structure_snapshot.nodes[{index}]"
            if not isinstance(node, dict):
                errors.append(f"{item_prefix} must be an object")
                continue
            add_required(
                errors, node,
                {"native_object_id", "parent_native_object_id", "component_id", "kind", "visible", "bounds"},
                item_prefix,
            )
            native_id = node.get("native_object_id")
            if not nonempty_string(native_id):
                errors.append(f"{item_prefix}.native_object_id must be non-empty")
                continue
            if native_id in snapshot_inventory["object_ids"]:
                errors.append(f"{prefix}.composition_manifest structure snapshot has duplicate native_object_id: {native_id}")
                continue
            snapshot_inventory["object_ids"].add(native_id)
            parent_id = node.get("parent_native_object_id")
            if parent_id is not None and not nonempty_string(parent_id):
                errors.append(f"{item_prefix}.parent_native_object_id must be null or non-empty")
            elif isinstance(parent_id, str):
                snapshot_inventory["parent_by_id"][native_id] = parent_id
            kind = node.get("kind")
            if kind not in {
                "assembly", "panel", "module", "primitive", "interconnect", "port",
                "component-board", "connector", "label", "shape", "image",
            }:
                errors.append(f"{item_prefix}.kind is invalid")
            if kind in {"assembly", "panel", "module", "interconnect", "component-board"}:
                snapshot_inventory["group_ids"].add(native_id)
            if kind == "image":
                snapshot_inventory["image_ids"].add(native_id)
            component_id = node.get("component_id")
            if component_id is not None and not nonempty_string(component_id):
                errors.append(f"{item_prefix}.component_id must be null or non-empty")
            elif isinstance(component_id, str):
                snapshot_inventory["component_id_by_native"][native_id] = component_id
            if not isinstance(node.get("visible"), bool):
                errors.append(f"{item_prefix}.visible must be boolean")
            elif node.get("visible") is not True:
                errors.append(f"{item_prefix}: hidden nodes cannot prove D3 composition")
            bounds = node.get("bounds")
            if (
                not isinstance(bounds, list) or len(bounds) != 4
                or any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in bounds)
                or bounds[2] < 0 or bounds[3] < 0
            ):
                errors.append(f"{item_prefix}.bounds must be [x, y, width, height]")
            else:
                snapshot_inventory["bounds_by_id"][native_id] = bounds
        for native_id, parent_id in snapshot_inventory["parent_by_id"].items():
            if parent_id not in snapshot_inventory["object_ids"]:
                errors.append(f"{prefix}.composition_manifest structure snapshot references missing parent: {parent_id}")
        board_root_ids = snapshot.get("component_board_root_native_object_ids")
        if not list_of_strings(board_root_ids):
            errors.append(f"{prefix}.composition_manifest structure snapshot board roots must be a list")
            board_root_ids = []
        if plan.get("component_board_required") is True and not board_root_ids:
            errors.append(f"{prefix}.composition_manifest structure snapshot lacks a component-board root")
        for board_root_id in board_root_ids:
            if board_root_id not in snapshot_inventory["group_ids"]:
                errors.append(f"{prefix}.composition_manifest structure snapshot board root is not a named group")
        if plan.get("component_board_required") is True:
            for planned_component_id in [item.get("component_id") for item in plan_components if isinstance(item, dict)]:
                board_copy_found = any(
                    snapshot_inventory["component_id_by_native"].get(native_id) == planned_component_id
                    and any(
                        native_is_descendant(native_id, board_root_id, snapshot_inventory["parent_by_id"])
                        for board_root_id in board_root_ids
                    )
                    for native_id in snapshot_inventory["object_ids"]
                )
                if not board_copy_found:
                    errors.append(f"{prefix}.composition_manifest component board lacks reusable copy: {planned_component_id}")

    assembly_root = manifest.get("assembly_root_id")
    if not nonempty_string(assembly_root):
        errors.append(f"{prefix}.composition_manifest assembly_root_id must be non-empty")
    top_level_ids = manifest.get("top_level_component_ids")
    if not list_of_strings(top_level_ids) or len(top_level_ids) < 2 or len(set(top_level_ids)) != len(top_level_ids):
        errors.append(f"{prefix}.composition_manifest top_level_component_ids must contain distinct components")
        top_level_ids = []
    planned_ids = [
        item.get("component_id") for item in plan_components
        if isinstance(item, dict) and nonempty_string(item.get("component_id"))
    ]
    if top_level_ids != planned_ids:
        errors.append(f"{prefix}.composition_manifest top-level components do not match composition_plan")

    hierarchy = manifest.get("hierarchy")
    if not isinstance(hierarchy, list) or not hierarchy:
        errors.append(f"{prefix}.composition_manifest hierarchy must be non-empty")
        hierarchy = []
    entries: dict[str, dict[str, Any]] = {}
    manifest_native_ids: set[str] = set()
    for index, item in enumerate(hierarchy):
        item_prefix = f"{prefix}.composition_manifest.hierarchy[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_prefix} must be an object")
            continue
        add_required(
            errors, item,
            {
                "component_id", "parent_id", "level", "native_object_ids", "reusable",
                "independently_movable", "independently_editable",
            },
            item_prefix,
        )
        component_id = item.get("component_id")
        if not isinstance(component_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", component_id):
            errors.append(f"{item_prefix}.component_id is invalid")
            continue
        if component_id in entries:
            errors.append(f"{prefix}.composition_manifest hierarchy has duplicate component_id: {component_id}")
            continue
        entries[component_id] = item
        if item.get("parent_id") is not None and not nonempty_string(item.get("parent_id")):
            errors.append(f"{item_prefix}.parent_id must be null or non-empty")
        if item.get("level") not in {"assembly", "panel", "module", "primitive", "interconnect"}:
            errors.append(f"{item_prefix}.level is invalid")
        native_ids = item.get("native_object_ids")
        if not list_of_strings(native_ids) or not native_ids:
            errors.append(f"{item_prefix}.native_object_ids must be non-empty")
        else:
            for native_id in native_ids:
                if native_id in manifest_native_ids:
                    errors.append(f"{prefix}.composition_manifest repeats native_object_id: {native_id}")
                manifest_native_ids.add(native_id)
        for key in ("reusable", "independently_movable", "independently_editable"):
            if not isinstance(item.get(key), bool):
                errors.append(f"{item_prefix}.{key} must be boolean")
    root_entry = entries.get(assembly_root) if isinstance(assembly_root, str) else None
    if root_entry is None or root_entry.get("parent_id") is not None or root_entry.get("level") != "assembly":
        errors.append(f"{prefix}.composition_manifest assembly root is invalid")
    for component_id, item in entries.items():
        parent = item.get("parent_id")
        if isinstance(parent, str) and parent not in entries:
            errors.append(f"{prefix}.composition_manifest hierarchy references missing parent: {parent}")
        if component_id in top_level_ids:
            if parent != assembly_root:
                errors.append(f"{prefix}.composition_manifest top-level component {component_id} is not under the assembly root")
            expected_level = plan_hierarchy[1] if len(plan_hierarchy) > 1 else None
            if expected_level and item.get("level") != expected_level:
                errors.append(f"{prefix}.composition_manifest top-level component {component_id} has the wrong hierarchy level")
            if item.get("independently_movable") is not True or item.get("independently_editable") is not True:
                errors.append(f"{prefix}.composition_manifest top-level component {component_id} is not independently editable/movable")
    planned_reuse = {
        item.get("component_id"): item.get("reusable") for item in plan_components
        if isinstance(item, dict)
    }
    for component_id, reusable in planned_reuse.items():
        if reusable is True and entries.get(component_id, {}).get("reusable") is not True:
            errors.append(f"{prefix}.composition_manifest reusable component {component_id} lost reuse support")

    depth_cache: dict[str, int] = {}

    def component_depth(component_id: str, trail: set[str] | None = None) -> int:
        if component_id in depth_cache:
            return depth_cache[component_id]
        trail = set() if trail is None else set(trail)
        if component_id in trail:
            errors.append(f"{prefix}.composition_manifest hierarchy contains a cycle")
            return 0
        trail.add(component_id)
        parent = entries.get(component_id, {}).get("parent_id")
        value = 0 if parent is None else component_depth(parent, trail) + 1 if isinstance(parent, str) and parent in entries else 0
        depth_cache[component_id] = value
        return value

    observed_depth = max((component_depth(component_id) for component_id in entries), default=0)
    if observed_depth != required_depth:
        errors.append(f"{prefix}.composition_manifest hierarchy depth does not match composition_plan")
    if not any(item.get("level") == "primitive" for item in entries.values()):
        errors.append(f"{prefix}.composition_manifest hierarchy has no primitive objects")
    for top_id in top_level_ids:
        has_primitive_descendant = False
        for component_id, item in entries.items():
            if item.get("level") != "primitive" or component_depth(component_id) != required_depth:
                continue
            parent = item.get("parent_id")
            ancestor_seen: set[str] = set()
            while isinstance(parent, str) and parent in entries and parent not in ancestor_seen:
                ancestor_seen.add(parent)
                if parent == top_id:
                    has_primitive_descendant = True
                    break
                parent = entries[parent].get("parent_id")
            if has_primitive_descendant:
                break
        if not has_primitive_descendant:
            errors.append(f"{prefix}.composition_manifest top-level component {top_id} has no primitive descendant")

    layers = manifest.get("layer_order")
    required_layers = ["background", "interconnect", "modules", "labels", "annotations"]
    if layers != required_layers:
        errors.append(f"{prefix}.composition_manifest layer_order must be background -> interconnect -> modules -> labels -> annotations")
    interconnect_id = manifest.get("interconnect_group_id")
    if interconnect_id is not None:
        if not nonempty_string(interconnect_id) or interconnect_id not in entries:
            errors.append(f"{prefix}.composition_manifest interconnect_group_id is invalid")
        elif entries[interconnect_id].get("level") != "interconnect":
            errors.append(f"{prefix}.composition_manifest interconnect group has the wrong level")
    named_ports = manifest.get("named_ports")
    if not isinstance(named_ports, list):
        errors.append(f"{prefix}.composition_manifest named_ports must be a list")
        named_ports = []
    port_ids: set[str] = set()
    port_native_ids: set[str] = set()
    for index, item in enumerate(named_ports):
        item_prefix = f"{prefix}.composition_manifest.named_ports[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_prefix} must be an object")
            continue
        add_required(errors, item, {"port_id", "owner_component_id", "native_object_ids"}, item_prefix)
        port_id = item.get("port_id")
        if not isinstance(port_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", port_id):
            errors.append(f"{item_prefix}.port_id is invalid")
        elif port_id in port_ids:
            errors.append(f"{prefix}.composition_manifest has duplicate port_id: {port_id}")
        else:
            port_ids.add(port_id)
        owner_component_id = item.get("owner_component_id")
        if not isinstance(owner_component_id, str) or owner_component_id not in entries:
            errors.append(f"{item_prefix}.owner_component_id is missing from the hierarchy")
        if not list_of_strings(item.get("native_object_ids")) or not item.get("native_object_ids"):
            errors.append(f"{item_prefix}.native_object_ids must be non-empty")
        else:
            port_native_ids.update(item.get("native_object_ids"))
    if interconnect_id is not None and len(named_ports) < 2:
        errors.append(f"{prefix}.composition_manifest interconnect group requires named ports")

    flattened = manifest.get("flattened_assets")
    if not isinstance(flattened, list):
        errors.append(f"{prefix}.composition_manifest flattened_assets must be a list")
        flattened = []
    if flattened and not plan_allowed_flattening:
        errors.append(f"{prefix}.composition_manifest uses flattened assets not allowed by composition_plan")
    for index, item in enumerate(flattened):
        item_prefix = f"{prefix}.composition_manifest.flattened_assets[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_prefix} must be an object")
            continue
        add_required(errors, item, {"path", "scope", "reason", "allowance", "replaceable"}, item_prefix)
        if item.get("scope") not in {"component", "module", "panel-asset", "plot", "photo", "illustration"}:
            errors.append(f"{item_prefix}.scope is not an allowed atomic flattening scope")
        if not nonempty_string(item.get("reason")):
            errors.append(f"{item_prefix}.reason must be non-empty")
        if item.get("allowance") not in plan_allowed_flattening:
            errors.append(f"{item_prefix}.allowance was not frozen in composition_plan")
        if item.get("replaceable") is not True:
            errors.append(f"{item_prefix}.replaceable must be true")
        path = validate_delivery_file(
            item.get("path"), f"{item_prefix}.path", project_root, errors, lock_hashes,
        )
        if path is not None:
            asset_paths.add(path)

    edit_test = manifest.get("manual_edit_test")
    if not isinstance(edit_test, dict):
        errors.append(f"{prefix}.composition_manifest manual_edit_test must be an object")
        edit_test = {}
    add_required(
        errors, edit_test,
        {
            "status", "editor", "operations", "observed_ungroup_steps_to_primitives",
            "verified_locked_elements", "report_path", "roundtrip_preview_path",
            "roundtrip_preview_sha256", "edited_native_source_path",
            "edited_native_source_sha256", "capture_run_id", "completed_at",
            "roundtrip_binding_sha256",
        },
        f"{prefix}.composition_manifest.manual_edit_test",
    )
    if edit_test.get("status") != "pass":
        errors.append(f"{prefix}.composition_manifest manual edit test must pass")
    if edit_test.get("editor") != target_editor:
        errors.append(f"{prefix}.composition_manifest manual edit test used the wrong editor")
    required_operations = {
        "ungroup-one-level", "edit-text", "move-component", "copy-component",
        "reassemble", "export-roundtrip",
    }
    if plan.get("component_board_required") is True:
        required_operations.add("copy-from-component-board")
    operations = edit_test.get("operations")
    if not list_of_strings(operations) or not required_operations.issubset(set(operations)):
        errors.append(f"{prefix}.composition_manifest manual edit test is incomplete")
    if edit_test.get("observed_ungroup_steps_to_primitives") != required_depth:
        errors.append(f"{prefix}.composition_manifest observed ungroup depth does not match the plan")
    if edit_test.get("verified_locked_elements") != plan_locked:
        errors.append(f"{prefix}.composition_manifest locked elements were not verified exactly")
    if edit_test.get("capture_run_id") != snapshot.get("capture_run_id"):
        errors.append(f"{prefix}.composition_manifest manual edit test capture_run_id mismatch")
    completed_at = edit_test.get("completed_at")
    if not nonempty_string(completed_at):
        errors.append(f"{prefix}.composition_manifest manual edit test completed_at must be non-empty")
    else:
        try:
            datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"{prefix}.composition_manifest manual edit test completed_at is invalid")
    report_path = validate_delivery_file(
        edit_test.get("report_path"), f"{prefix}.composition_manifest.manual_edit_test.report_path",
        project_root, errors, lock_hashes,
    )
    if report_path is not None and report_path not in qa_paths:
        errors.append(f"{prefix}.composition_manifest manual edit test report must be listed in qa_report_paths")
    roundtrip_preview_path = validate_delivery_file(
        edit_test.get("roundtrip_preview_path"),
        f"{prefix}.composition_manifest.manual_edit_test.roundtrip_preview_path",
        project_root, errors, lock_hashes,
    )
    if roundtrip_preview_path is not None:
        if roundtrip_preview_path not in preview_paths:
            errors.append(f"{prefix}.composition_manifest round-trip preview must be listed in preview_paths")
        if edit_test.get("roundtrip_preview_sha256") != lock_hashes.get(roundtrip_preview_path):
            errors.append(f"{prefix}.composition_manifest round-trip preview hash mismatch")
    edited_native_path = validate_delivery_file(
        edit_test.get("edited_native_source_path"),
        f"{prefix}.composition_manifest.manual_edit_test.edited_native_source_path",
        project_root, errors, lock_hashes,
    )
    if edited_native_path is not None:
        if edited_native_path not in native_paths:
            errors.append(f"{prefix}.composition_manifest edited native source must be a native_source_path")
        if edit_test.get("edited_native_source_sha256") != lock_hashes.get(edited_native_path):
            errors.append(f"{prefix}.composition_manifest edited native source hash mismatch")
    if edited_native_path is not None and roundtrip_preview_path is not None:
        expected_binding = sha256_text("|".join([
            lock_hashes.get(edited_native_path, ""),
            lock_hashes.get(roundtrip_preview_path, ""),
            str(edit_test.get("capture_run_id", "")),
            str(completed_at or ""),
        ]))
        if edit_test.get("roundtrip_binding_sha256") != expected_binding:
            errors.append(f"{prefix}.composition_manifest round-trip binding hash mismatch")
        if roundtrip_preview_path.stat().st_mtime_ns < edited_native_path.stat().st_mtime_ns:
            errors.append(f"{prefix}.composition_manifest round-trip preview predates the edited native source")
        if report_path is not None and report_path.stat().st_mtime_ns < roundtrip_preview_path.stat().st_mtime_ns:
            errors.append(f"{prefix}.composition_manifest edit-test report predates the round-trip preview")

    actual_inventories: list[dict[str, Any]] = []
    for path in native_paths:
        try:
            inventory = native_structure_inventory(path)
        except Exception as exc:
            errors.append(f"{prefix}.composition_manifest native structure cannot be inspected: {exc}")
            continue
        if inventory is not None:
            actual_inventories.append(inventory)
    if not actual_inventories and target_editor != "figma":
        errors.append(f"{prefix}.composition_manifest has no locally inspectable native source")
    declared_native_ids = manifest_native_ids | port_native_ids
    missing_snapshot_ids = declared_native_ids - snapshot_inventory["object_ids"]
    if missing_snapshot_ids:
        errors.append(f"{prefix}.composition_manifest native_object_ids are missing from the structure snapshot")
    if actual_inventories:
        actual_ids = set().union(*(inventory["object_ids"] for inventory in actual_inventories))
        required_snapshot_ids = set().union(*(inventory["required_snapshot_ids"] for inventory in actual_inventories))
        if snapshot_inventory["object_ids"] - actual_ids:
            errors.append(f"{prefix}.composition_manifest structure snapshot contains IDs absent from native sources")
        if required_snapshot_ids - snapshot_inventory["object_ids"]:
            errors.append(f"{prefix}.composition_manifest structure snapshot omits native source objects")
        if declared_native_ids - actual_ids:
            errors.append(f"{prefix}.composition_manifest declares IDs absent from native sources")
        actual_image_ids = set().union(*(inventory["image_ids"] for inventory in actual_inventories))
        if actual_image_ids - snapshot_inventory["image_ids"]:
            errors.append(f"{prefix}.composition_manifest structure snapshot misclassifies native images")
        for inventory in actual_inventories:
            for native_id in inventory["required_snapshot_ids"]:
                actual_parent = inventory["parent_by_id"].get(native_id)
                expected_parent = actual_parent if actual_parent in inventory["required_snapshot_ids"] else None
                snapshot_parent = snapshot_inventory["parent_by_id"].get(native_id)
                if snapshot_parent != expected_parent:
                    errors.append(f"{prefix}.composition_manifest structure snapshot parent mismatch for {native_id}")

    binding_inventories = actual_inventories or [snapshot_inventory]
    assembly_native_ids = set(root_entry.get("native_object_ids", [])) if isinstance(root_entry, dict) and isinstance(root_entry.get("native_object_ids"), list) else set()
    assembly_inventory = next(
        (
            inventory for inventory in binding_inventories
            if assembly_native_ids.intersection(inventory["object_ids"])
        ),
        None,
    )
    if assembly_inventory is None:
        errors.append(f"{prefix}.composition_manifest assembly root is absent from native structure")
    else:
        assembly_group_ids = assembly_native_ids.intersection(assembly_inventory["group_ids"])
        if not assembly_group_ids:
            errors.append(f"{prefix}.composition_manifest assembly root is not a native group")
        else:
            assembly_group_id = sorted(assembly_group_ids)[0]
            if native_group_depth(
                assembly_group_id, assembly_inventory["group_ids"], assembly_inventory["parent_by_id"],
            ) != required_depth:
                errors.append(f"{prefix}.composition_manifest native ungroup depth does not exactly match the plan")
            assembly_canvas = assembly_inventory["canvas_by_id"].get(assembly_group_id)
            if assembly_canvas in assembly_inventory["picture_only_canvases"]:
                errors.append(f"{prefix}.composition_manifest assembly canvas is a flattened picture")
            if assembly_inventory.get("full_canvas_image"):
                errors.append(f"{prefix}.composition_manifest native source contains a full-canvas image")

        for component_id, item in entries.items():
            native_ids = item.get("native_object_ids") if isinstance(item.get("native_object_ids"), list) else []
            level = item.get("level")
            if level in {"assembly", "panel", "module", "interconnect"} and not set(native_ids).intersection(assembly_inventory["group_ids"]):
                errors.append(f"{prefix}.composition_manifest grouped component {component_id} is not a native group")
            parent_id = item.get("parent_id")
            if not isinstance(parent_id, str) or parent_id not in entries:
                continue
            parent_native_ids = entries[parent_id].get("native_object_ids") if isinstance(entries[parent_id].get("native_object_ids"), list) else []
            if not any(
                child_native in assembly_inventory["object_ids"]
                and parent_native in assembly_inventory["object_ids"]
                and native_is_descendant(child_native, parent_native, assembly_inventory["parent_by_id"])
                for child_native in native_ids
                for parent_native in parent_native_ids
            ):
                errors.append(f"{prefix}.composition_manifest native grouping does not bind {component_id} to {parent_id}")
        for top_id in top_level_ids:
            top_native_ids = entries.get(top_id, {}).get("native_object_ids", [])
            if assembly_group_ids and not any(
                assembly_inventory["parent_by_id"].get(native_id) in assembly_group_ids
                for native_id in top_native_ids
            ):
                errors.append(f"{prefix}.composition_manifest top-level component {top_id} is not exposed by one native ungroup")
        for port in named_ports:
            if not isinstance(port, dict):
                continue
            owner = entries.get(port.get("owner_component_id"), {}) if isinstance(port.get("owner_component_id"), str) else {}
            owner_native_ids = owner.get("native_object_ids") if isinstance(owner.get("native_object_ids"), list) else []
            native_port_ids = port.get("native_object_ids") if isinstance(port.get("native_object_ids"), list) else []
            if owner_native_ids and native_port_ids and not any(
                native_is_descendant(native_port_id, owner_native_id, assembly_inventory["parent_by_id"])
                for native_port_id in native_port_ids
                for owner_native_id in owner_native_ids
            ):
                errors.append(f"{prefix}.composition_manifest named port {port.get('port_id')} is not owned by its component")

    snapshot_assembly_ids = assembly_native_ids.intersection(snapshot_inventory["object_ids"])
    if snapshot_assembly_ids:
        assembly_bounds = snapshot_inventory["bounds_by_id"].get(sorted(snapshot_assembly_ids)[0])
        if assembly_bounds and assembly_bounds[2] > 0 and assembly_bounds[3] > 0:
            for image_id in snapshot_inventory["image_ids"]:
                image_bounds = snapshot_inventory["bounds_by_id"].get(image_id)
                if not image_bounds:
                    continue
                if (
                    image_bounds[0] <= assembly_bounds[0] + assembly_bounds[2] * 0.05
                    and image_bounds[1] <= assembly_bounds[1] + assembly_bounds[3] * 0.05
                    and image_bounds[2] >= assembly_bounds[2] * 0.9
                    and image_bounds[3] >= assembly_bounds[3] * 0.9
                ):
                    errors.append(f"{prefix}.composition_manifest structure snapshot contains a full-assembly image")
                    break
    return document_paths, asset_paths


def validate_review_record(
    review: Any,
    prefix: str,
    producer_id: str,
    expected_artifacts: set[Path],
    project_root: Path,
    errors: list[str],
    lock_hashes: dict[Path, str],
) -> Path | None:
    if not isinstance(review, dict):
        errors.append(f"{prefix} must be an object")
        return None
    add_required(
        errors, review,
        {"status", "reviewer_id", "report_path", "reviewed_bundle"},
        prefix,
    )
    if review.get("status") != "pass":
        errors.append(f"{prefix}.status must be pass")
    reviewer_id = review.get("reviewer_id")
    if not nonempty_string(reviewer_id):
        errors.append(f"{prefix}.reviewer_id must be non-empty")
    elif reviewer_id != reviewer_id.strip():
        errors.append(f"{prefix}.reviewer_id must not contain surrounding whitespace")
    elif normalized_identity(reviewer_id) == normalized_identity(producer_id):
        errors.append(f"{prefix}.reviewer_id must differ from producer_id")
    report_path = validate_delivery_file(
        review.get("report_path"), f"{prefix}.report_path", project_root, errors, lock_hashes,
    )
    validate_role_document(report_path, f"{prefix}.report_path", REPORT_FORMATS, errors)
    bundle = review.get("reviewed_bundle")
    if not isinstance(bundle, list) or not bundle:
        errors.append(f"{prefix}.reviewed_bundle must be a non-empty list")
        bundle = []
    reviewed_paths: set[Path] = set()
    for index, item in enumerate(bundle):
        item_prefix = f"{prefix}.reviewed_bundle[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_prefix} must be an object")
            continue
        add_required(errors, item, {"path", "sha256"}, item_prefix)
        artifact_path = validate_delivery_file(
            item.get("path"), f"{item_prefix}.path", project_root, errors, lock_hashes,
        )
        claimed_sha = item.get("sha256")
        if not HEX_SHA256.fullmatch(str(claimed_sha or "")):
            errors.append(f"{item_prefix}.sha256 must be a 64-character lowercase SHA-256")
        elif artifact_path is not None and lock_hashes.get(artifact_path) != claimed_sha:
            errors.append(f"{item_prefix}.sha256 does not match the validated artifact snapshot")
        if artifact_path is not None:
            if artifact_path in reviewed_paths:
                errors.append(f"{prefix}.reviewed_bundle contains a duplicate path")
            reviewed_paths.add(artifact_path)
    if reviewed_paths != expected_artifacts:
        errors.append(f"{prefix}.reviewed_bundle must exactly cover the required review bundle")
    return report_path


def validate_delivery_record(
    record: Any,
    contract: dict[str, Any],
    prefix: str,
    producer_id: str,
    project_root: Path,
    errors: list[str],
    lock_hashes: dict[Path, str],
    semantic_context: set[Path],
    visual_context: set[Path],
    *,
    require_spec_caption: bool,
) -> None:
    if not isinstance(record, dict):
        errors.append(f"{prefix} must be an object")
        return
    list_keys = ("source_paths", "editable_source_paths", "export_paths", "preview_paths", "qa_report_paths")
    required = set(list_keys) | {"semantic_review", "visual_review"}
    if require_spec_caption:
        required |= {"spec_path", "caption_path"}
    if contract.get("editability") == "D3":
        required.add("composition_manifest_path")
    add_required(errors, record, required, prefix)
    path_lists: dict[str, list[str]] = {}
    for key in list_keys:
        value = record.get(key)
        if not list_of_strings(value):
            errors.append(f"{prefix}.{key} must be a list of non-empty paths")
        path_lists[key] = [item for item in value if nonempty_string(item)] if isinstance(value, list) else []
    if not path_lists["export_paths"]:
        errors.append(f"{prefix} requires at least one export")
    if not path_lists["qa_report_paths"]:
        errors.append(f"{prefix} requires at least one QA report")
    resolved_lists: dict[str, list[Path]] = {key: [] for key in list_keys}
    for key in list_keys:
        for index, raw in enumerate(path_lists[key]):
            path = validate_delivery_file(
                raw, f"{prefix}.{key}[{index}]", project_root, errors, lock_hashes,
            )
            if path is not None:
                resolved_lists[key].append(path)
    spec_path = caption_path = None
    if require_spec_caption:
        spec_path = validate_delivery_file(
            record.get("spec_path"), f"{prefix}.spec_path", project_root, errors, lock_hashes,
        )
        caption_path = validate_delivery_file(
            record.get("caption_path"), f"{prefix}.caption_path", project_root, errors, lock_hashes,
        )
        validate_role_document(spec_path, f"{prefix}.spec_path", SPEC_FORMATS, errors)
        validate_role_document(caption_path, f"{prefix}.caption_path", CAPTION_FORMATS, errors)
    for index, path in enumerate(resolved_lists["qa_report_paths"]):
        validate_role_document(path, f"{prefix}.qa_report_paths[{index}]", REPORT_FORMATS, errors)

    composition_documents: set[Path] = set()
    composition_assets: set[Path] = set()
    if contract.get("editability") == "D3":
        composition_documents, composition_assets = validate_composition_manifest(
            record.get("composition_manifest_path"), contract, record, prefix,
            project_root, errors, lock_hashes,
            set(resolved_lists["editable_source_paths"]),
            set(resolved_lists["qa_report_paths"]),
            set(resolved_lists["preview_paths"]),
        )

    export_formats = {artifact_format(raw) for raw in path_lists["export_paths"]}
    editable_formats = {artifact_format(raw) for raw in path_lists["editable_source_paths"]}
    required_formats = set(contract.get("primary_formats", []))
    required_exports = required_formats & PUBLICATION_FORMATS
    if contract.get("preferred_route") in {"latex-table", "tikz"} and "tex" in required_formats:
        required_exports.add("tex")
    required_editable = required_formats - PUBLICATION_FORMATS
    if "tex" in required_exports:
        required_editable.discard("tex")
    missing_exports = required_exports - export_formats
    missing_editable = required_editable - editable_formats
    if missing_exports:
        errors.append(f"{prefix} is missing required export formats: {', '.join(sorted(missing_exports))}")
    if missing_editable:
        errors.append(f"{prefix} is missing required editable formats: {', '.join(sorted(missing_editable))}")
    preview_paths = path_lists["preview_paths"]
    if contract.get("preview_format") == "png":
        if not preview_paths:
            errors.append(f"{prefix} requires a PNG preview")
        elif any(artifact_format(raw) != "png" for raw in preview_paths):
            errors.append(f"{prefix}.preview_paths must contain PNG files only")
    if contract.get("editability") == "D1" and not path_lists["source_paths"]:
        errors.append(f"{prefix} requires reproducible source")
    if contract.get("preferred_route") == "ppt-native" and not any(
        artifact_format(raw) == "mjs" for raw in path_lists["source_paths"]
    ):
        errors.append(f"{prefix} ppt-native route requires a plain .mjs builder in source_paths")
    if contract.get("editability") == "D2":
        native_paths = [raw for raw in path_lists["editable_source_paths"] if artifact_format(raw) in NATIVE_EDITABLE_FORMATS]
        if not native_paths:
            errors.append(f"{prefix} requires a native-editable source")
    if contract.get("editability") == "D3":
        composition_paths = [
            raw for raw in path_lists["editable_source_paths"]
            if artifact_format(raw) in COMPOSITION_EDITABLE_FORMATS
        ]
        if not composition_paths:
            errors.append(f"{prefix} requires a composition-ready native source")
    exports = set(resolved_lists["export_paths"])
    previews = set(resolved_lists["preview_paths"])
    contract_sources: set[Path] = set()
    for key in ("source_paths", "data_paths"):
        values = contract.get(key) if isinstance(contract.get(key), list) else []
        for index, raw in enumerate(values):
            path = snapshot_scientific_input(
                raw, f"{prefix}.contract_{key}[{index}]", project_root, errors, lock_hashes,
            )
            if path is not None:
                contract_sources.add(path)
    semantic_bundle = (
        set(exports)
        | semantic_context
        | contract_sources
        | set(resolved_lists["source_paths"])
        | set(resolved_lists["editable_source_paths"])
        | set(resolved_lists["qa_report_paths"])
        | composition_documents
        | composition_assets
    )
    if spec_path is not None:
        semantic_bundle.add(spec_path)
    if caption_path is not None:
        semantic_bundle.add(caption_path)
    visual_bundle = exports | previews | visual_context
    semantic_report = validate_review_record(
        record.get("semantic_review"), f"{prefix}.semantic_review", producer_id,
        semantic_bundle, project_root, errors, lock_hashes,
    )
    visual_report = validate_review_record(
        record.get("visual_review"), f"{prefix}.visual_review", producer_id,
        visual_bundle, project_root, errors, lock_hashes,
    )
    semantic_reviewer = normalized_identity(
        record.get("semantic_review", {}).get("reviewer_id") if isinstance(record.get("semantic_review"), dict) else None
    )
    visual_reviewer = normalized_identity(
        record.get("visual_review", {}).get("reviewer_id") if isinstance(record.get("visual_review"), dict) else None
    )
    if semantic_reviewer and semantic_reviewer == visual_reviewer:
        errors.append(f"{prefix} semantic and visual reviewers must be distinct identities")

    document_roles = [path for path in (spec_path, caption_path, semantic_report, visual_report) if path is not None]
    document_roles.extend(resolved_lists["qa_report_paths"])
    document_roles.extend(composition_documents)
    if len(document_roles) != len(set(document_roles)):
        errors.append(f"{prefix} spec, caption, QA, and review reports must use distinct files")
    artifact_roles = set(
        resolved_lists["source_paths"]
        + resolved_lists["editable_source_paths"]
        + resolved_lists["export_paths"]
        + resolved_lists["preview_paths"]
    )
    artifact_roles |= composition_assets
    overlap = set(document_roles) & artifact_roles
    if overlap:
        errors.append(f"{prefix} role documents must not alias sources, editable files, exports, or previews")


def command_validate_delivery(args: argparse.Namespace) -> int:
    program_path = Path(args.program).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    program, initial_program_sha = load_json_snapshot(program_path)
    manifest, initial_manifest_sha = load_json_snapshot(manifest_path)
    project_root = project_root_for(program, args.project_root)
    phase = program.get("phase_1", {})
    prompt_path = resolve_path(phase.get("stage_2_prompt_path", ""), project_root)
    freeze_path = resolve_path(phase.get("freeze_record_path", ""), project_root)
    control_snapshots = {
        program_path: initial_program_sha,
        manifest_path: initial_manifest_sha,
    }
    if prompt_path.is_file():
        control_snapshots[prompt_path] = sha256_file(prompt_path)
    if freeze_path.is_file():
        control_snapshots[freeze_path] = sha256_file(freeze_path)
    errors, warnings = validate_program(
        program, program_path, project_root, check_paths=True, check_freeze=True,
    )
    if phase.get("status") != "frozen":
        errors.append("delivery validation requires phase_1.status=frozen")
    if not HEX_SHA256.fullmatch(str(phase.get("prompt_sha256", ""))):
        errors.append("delivery validation requires a frozen 64-character prompt_sha256")
    if manifest.get("schema_version") != DELIVERY_SCHEMA:
        errors.append("delivery manifest schema is unsupported")
    if manifest.get("request_id") != program.get("request_id"):
        errors.append("delivery request_id does not match figure program")
    if manifest.get("version") != program.get("version"):
        errors.append("delivery version does not match figure program")
    if manifest.get("program_sha256") != initial_program_sha:
        errors.append("delivery program_sha256 does not match frozen figure program")
    if manifest.get("prompt_sha256") != phase.get("prompt_sha256"):
        errors.append("delivery prompt_sha256 does not match frozen prompt")
    if manifest.get("status") != "verified":
        errors.append("delivery manifest status must be verified")
    producer_id = manifest.get("producer_id")
    if not nonempty_string(producer_id):
        errors.append("delivery producer_id must be non-empty")
        producer_id = ""
    elif producer_id != producer_id.strip():
        errors.append("delivery producer_id must not contain surrounding whitespace")
    expected_manifest_raw = program.get("delivery_manifest_path")
    expected_manifest_path = resolve_path(expected_manifest_raw, project_root) if nonempty_string(expected_manifest_raw) else None
    if manifest_path != expected_manifest_path:
        errors.append("delivery manifest path does not match figure program")
    if not is_within(manifest_path, project_root):
        errors.append("delivery manifest must stay inside the project root")

    lock_hashes: dict[Path, str] = {}
    semantic_context = {path for path in (program_path, prompt_path, freeze_path) if path.is_file()}
    visual_context: set[Path] = set()
    integration = manifest.get("manuscript_integration")
    if not isinstance(integration, dict):
        errors.append("manuscript_integration must be an object")
        integration = {}
    integration_status = integration.get("status")
    if integration_status not in {"pending", "verified", "failed"}:
        errors.append("manuscript_integration.status is invalid")
    if args.require_integration and integration_status != "verified":
        errors.append("manuscript integration is not verified")
    if integration_status == "verified":
        for key in ("manuscript_path", "rendered_path", "qa_report_path"):
            path = validate_delivery_file(
                integration.get(key), f"manuscript_integration.{key}",
                project_root, errors, lock_hashes,
            )
            if key == "rendered_path" and path is not None:
                visual_context.add(path)
            if key == "qa_report_path":
                validate_role_document(path, "manuscript_integration.qa_report_path", REPORT_FORMATS, errors)
    entries = manifest.get("figures")
    if not isinstance(entries, list):
        errors.append("delivery figures must be a list")
        entries = []
    by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not nonempty_string(entry.get("figure_id")):
            errors.append("delivery contains an invalid figure entry")
            continue
        figure_id = entry["figure_id"]
        if figure_id in by_id:
            errors.append(f"delivery contains duplicate figure_id: {figure_id}")
        by_id[figure_id] = entry
    retained = {
        figure.get("figure_id"): figure for figure in program.get("figures", [])
        if isinstance(figure, dict) and figure.get("status") != "removed"
    }
    if set(by_id) != set(retained):
        missing = sorted(set(retained) - set(by_id))
        extra = sorted(set(by_id) - set(retained))
        if missing:
            errors.append("delivery is missing figures: " + ", ".join(missing))
        if extra:
            errors.append("delivery contains out-of-scope figures: " + ", ".join(extra))
    for figure_id, figure in retained.items():
        entry = by_id.get(figure_id)
        if entry is None:
            continue
        prefix = f"delivery figure {figure_id}"
        validate_delivery_record(
            entry, figure, prefix, producer_id, project_root, errors, lock_hashes,
            semantic_context, visual_context,
            require_spec_caption=True,
        )
        panel_entries = entry.get("panels")
        if figure.get("figure_type") == "mixed_panel":
            if not isinstance(panel_entries, list):
                errors.append(f"{prefix}.panels must be a list")
                panel_entries = []
            panels_by_id: dict[str, dict[str, Any]] = {}
            for panel_entry in panel_entries:
                if not isinstance(panel_entry, dict) or not nonempty_string(panel_entry.get("panel_id")):
                    errors.append(f"{prefix}.panels contains an invalid panel")
                    continue
                panel_id = panel_entry["panel_id"]
                if panel_id in panels_by_id:
                    errors.append(f"{prefix}.panels contains duplicate panel_id: {panel_id}")
                panels_by_id[panel_id] = panel_entry
            panel_contracts = {
                panel.get("panel_id"): panel for panel in figure.get("panel_plan", [])
                if isinstance(panel, dict)
            }
            if set(panels_by_id) != set(panel_contracts):
                errors.append(f"{prefix}.panels must exactly match mixed panel_plan IDs")
            for panel_id, panel_contract in panel_contracts.items():
                if panel_id in panels_by_id:
                    validate_delivery_record(
                        panels_by_id[panel_id], panel_contract,
                        f"{prefix} panel {panel_id}", producer_id, project_root,
                        errors, lock_hashes, semantic_context, visual_context,
                        require_spec_caption=False,
                    )
        elif panel_entries not in (None, []):
            errors.append(f"{prefix}.panels is allowed only for mixed_panel")

    lock_raw = manifest.get("delivery_lock_path")
    lock_path = resolve_path(lock_raw, project_root) if nonempty_string(lock_raw) else None
    canonical_lock_path = manifest_path.with_name("delivery-lock.json")
    if lock_path is None or lock_path != canonical_lock_path:
        errors.append("delivery_lock_path must equal canonical delivery-lock.json beside the manifest")
    else:
        managed = {
            program_path,
            manifest_path,
            prompt_path,
            freeze_path,
        } | set(lock_hashes)
        if lock_path in managed:
            errors.append("delivery_lock_path must not alias a managed artifact")
    if errors:
        print(json.dumps({"valid": False, "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
        return 1

    for path in set(control_snapshots).intersection(lock_hashes):
        if control_snapshots[path] != lock_hashes[path]:
            errors.append(f"control file changed before artifact review: {path.relative_to(project_root).as_posix()}")
    for path, snapshot_sha in list(control_snapshots.items()) + list(lock_hashes.items()):
        if not path.is_file() or sha256_file(path) != snapshot_sha:
            errors.append(f"file changed during delivery validation: {path.relative_to(project_root).as_posix()}")
    if errors:
        print(json.dumps({"valid": False, "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
        return 1
    artifact_records = [
        {"path": path.relative_to(project_root).as_posix(), "sha256": lock_hashes[path]}
        for path in sorted(lock_hashes, key=lambda item: item.as_posix().lower())
    ]
    prior_created_at = None
    prior_lock = None
    prior_lock_sha = None
    if lock_path.is_file():
        try:
            prior_lock, prior_lock_sha = load_json_snapshot(lock_path)
            if prior_lock.get("schema_version") != LOCK_SCHEMA:
                raise ValueError("existing delivery-lock.json has another schema")
            if prior_lock.get("request_id") != program.get("request_id") or prior_lock.get("version") != program.get("version"):
                raise ValueError("existing delivery-lock.json belongs to another request/version")
            prior_created_at = prior_lock.get("created_at")
        except Exception as exc:
            print(json.dumps({
                "valid": False,
                "errors": [f"existing delivery lock cannot be overwritten: {exc}"],
                "warnings": warnings,
            }, ensure_ascii=False, indent=2))
            return 1
    delivery_lock = {
        "schema_version": LOCK_SCHEMA,
        "request_id": program["request_id"],
        "version": program["version"],
        "created_at": prior_created_at or now_utc(),
        "program": {
            "path": program_path.relative_to(project_root).as_posix(),
            "sha256": initial_program_sha,
        },
        "prompt": {
            "path": prompt_path.relative_to(project_root).as_posix(),
            "sha256": control_snapshots[prompt_path],
        },
        "freeze_record": {
            "path": freeze_path.relative_to(project_root).as_posix(),
            "sha256": control_snapshots[freeze_path],
        },
        "manifest": {
            "path": manifest_path.relative_to(project_root).as_posix(),
            "sha256": initial_manifest_sha,
        },
        "artifacts": artifact_records,
    }
    if prior_lock is not None:
        if json_text(prior_lock) != json_text(delivery_lock):
            print(json.dumps({
                "valid": False,
                "errors": ["existing delivery lock is immutable and the candidate bundle differs; create a new request/version and fresh reviews"],
                "warnings": warnings,
            }, ensure_ascii=False, indent=2))
            return 1
        if sha256_file(lock_path) != prior_lock_sha:
            print(json.dumps({
                "valid": False,
                "errors": ["existing delivery lock changed during validation"],
                "warnings": warnings,
            }, ensure_ascii=False, indent=2))
            return 1
    else:
        write_json(lock_path, delivery_lock)
        for path, snapshot_sha in list(control_snapshots.items()) + list(lock_hashes.items()):
            if not path.is_file() or sha256_file(path) != snapshot_sha:
                lock_path.unlink(missing_ok=True)
                print(json.dumps({
                    "valid": False,
                    "errors": [f"file changed while sealing delivery: {path}"],
                    "warnings": warnings,
                }, ensure_ascii=False, indent=2))
                return 1
    print(json.dumps({
        "valid": True,
        "errors": [],
        "warnings": warnings,
        "delivery_lock": str(lock_path),
        "delivery_lock_sha256": sha256_file(lock_path),
    }, ensure_ascii=False, indent=2))
    return 0


def command_self_test(_args: argparse.Namespace) -> int:
    if not PYPDF_AVAILABLE:
        print(json.dumps({"self_test": "degraded", "reason": PYPDF_DEGRADED_NOTE}, indent=2))
        return 2
    steps: list[str] = []
    with tempfile.TemporaryDirectory(prefix="visual-contract-") as temp:
        root = Path(temp).resolve()
        (root / "paper").mkdir()
        (root / "research").mkdir()
        (root / "results").mkdir()
        (root / "paper" / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
        (root / "research" / "claims.md").write_text("C-RESULT-1: frozen comparison.\n", encoding="utf-8")
        data_path = root / "results" / "data.csv"
        original_data = "method,mean,stderr\nours,0.81,0.02\nbaseline,0.74,0.03\n"
        data_path.write_text(original_data, encoding="utf-8")

        init_args = argparse.Namespace(
            project_root=str(root), request_id="self-test-visual", paper_title="Self Test",
            stage="manuscript_draft", paper_type="empirical_ml", venue="Test Venue",
            owner_phase="research-manuscript", version=1,
            output_root="research/artifacts/visual-program",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            assert command_init(init_args) == 0
        program_path = root / "research" / "artifacts" / "visual-program" / "figure-program.json"
        program = load_json(program_path)
        program["paper"]["manuscript_paths"] = ["paper/main.tex"]
        program["paper"]["claim_evidence_map_paths"] = ["research/claims.md"]
        figure = {
            "figure_id": "F1", "title": "Primary benchmark", "role": "evidence",
            "reader_question": "How does the method compare with the baseline?",
            "three_second_takeaway": "The frozen comparison includes uncertainty.",
            "paper_claim_ids": ["C-RESULT-1"], "figure_type": "benchmark_plot",
            "exactness": "E2", "editability": "D1", "generativity": "G0",
            "source_paths": ["research/claims.md"], "data_paths": ["results/data.csv"],
            "panel_plan": [{"panel_id": "a", "job": "mean and standard error"}],
            "preferred_route": "exact-plot", "fallback_route": "",
            "target_placement": "single_column", "primary_formats": ["pdf", "svg"],
            "preview_format": "none", "editable_source_required": False,
            "caption_job": "Define comparison and standard-error bars.",
            "forbidden_inferences": ["No causal claim."], "status": "planned", "risks": [],
        }
        program["figures"] = [figure]
        write_json(program_path, program)
        prompt_path = root / "research" / "artifacts" / "visual-program" / "stage-2-prompt.md"
        prompt_path.write_text("# Do something pretty.\n", encoding="utf-8")
        hollow_errors, _ = validate_program(program, program_path, root, check_paths=True, check_freeze=False)
        assert any("prompt" in item.lower() for item in hollow_errors)
        steps.append("hollow-prompt-rejected")

        prompt_contract = {
            "schema_version": PROMPT_SCHEMA,
            "request_id": "self-test-visual",
            "version": 1,
            "program_path": "research/artifacts/visual-program/figure-program.json",
            "freeze_record_path": "research/artifacts/visual-program/freeze.json",
            "output_root": "research/artifacts/visual-program",
            "delivery_manifest_path": "research/artifacts/visual-program/delivery-manifest.json",
            "figure_ids": ["F1"],
            "source_manifest": [
                {"path": raw, "role": "canonical frozen input"} for raw in input_paths(program)
            ],
            "authorization": {
                "external_writes": "not_authorized",
                "external_uploads": "not_authorized",
                "generative_final": "prohibited",
            },
            "stop_policy": "Stop on any claim, data, route, or hash conflict and return to Phase 1.",
            "amendment_policy": "new_version_on_semantic_change",
        }
        prompt_body = """# Frozen Stage-2 Prompt

## Role
Produce the exact benchmark from the named immutable inputs and do not rely on chat memory.

## Frozen project inputs
Use the manuscript, claim ledger, and CSV in the source manifest. Preserve units, comparison membership, and uncertainty.

## Paper-wide visual thesis
The figure must show the complete frozen comparison and its standard-error uncertainty without implying causality.

## Non-negotiable constraints
Never invent values, omit the baseline, change the scale, or use generative pixels. Stop if any input or claim conflicts.

## Fidelity contract
F1 is E2-D1-G0 and must be produced through deterministic plotting code with PDF and SVG outputs.

## Required execution sequence
Audit the sources, write the figure specification, define restrained visual tokens, render exact exports, compare every value, and run independent semantic and visual reviews.

## Required deliverables
Deliver the specification, plotting source, PDF and SVG exports, caption, QA report, two reviewer records, canonical manifest, and delivery lock under the frozen output root.

## Completion condition
Finish only after all values, labels, uncertainty, formats, hashes, reviews, and optional manuscript integration satisfy the frozen contract.
"""
        prompt_path.write_text(
            PROMPT_START + json_text(prompt_contract) + "---\n" + prompt_body,
            encoding="utf-8",
        )
        errors, _ = validate_program(program, program_path, root, check_paths=True, check_freeze=False)
        assert not errors, errors
        steps.append("draft-valid")

        invalid = copy.deepcopy(program)
        invalid["figures"][0].update({
            "exactness": "E0", "editability": "D0", "generativity": "G2",
            "preferred_route": "generative-illustration", "primary_formats": ["png"],
        })
        invalid_errors, _ = validate_program(invalid, program_path, root, check_paths=True, check_freeze=False)
        assert any("benchmark_plot does not allow" in item for item in invalid_errors)
        steps.append("type-edg-negative-valid")

        d3_missing = copy.deepcopy(program)
        d3_missing["figures"][0].update({
            "figure_type": "method_architecture", "exactness": "E1", "editability": "D3",
            "preferred_route": "native-svg", "data_paths": ["results/data.csv"],
            "primary_formats": ["svg", "pdf"], "preview_format": "png",
            "editable_source_required": True,
        })
        d3_missing_errors, _ = validate_program(
            d3_missing, program_path, root, check_paths=True, check_freeze=False,
        )
        assert any("composition_plan" in item for item in d3_missing_errors)
        steps.append("d3-plan-required")

        d3_contract = copy.deepcopy(d3_missing["figures"][0])
        d3_contract["composition_plan"] = {
            "mode": "both",
            "target_editors": ["svg-editor"],
            "hierarchy": ["assembly", "module", "primitive"],
            "min_top_level_components": 2,
            "max_ungroup_steps_to_primitives": 2,
            "component_board_required": True,
            "top_level_components": [
                {"component_id": "F1.input", "job": "input module", "reusable": True},
                {"component_id": "F1.output", "job": "output module", "reusable": True},
            ],
            "allowed_flattening": [],
            "locked_elements": ["module identity"],
        }
        d3_valid = copy.deepcopy(program)
        d3_valid["figures"][0] = d3_contract
        d3_errors, _ = validate_program(d3_valid, program_path, root, check_paths=True, check_freeze=False)
        assert not d3_errors, d3_errors
        steps.append("d3-plan-valid")

        d3_source = root / "research" / "d3.svg"
        d3_source.write_text(
            "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='60'>"
            "<g id='F1.assembly'><g id='F1.input'><rect id='input-box' width='30' height='20'/><text id='input-text'>In</text></g>"
            "<g id='F1.output'><rect id='output-box' x='60' width='30' height='20'/><text id='output-text' x='60'>Out</text></g></g>"
            "<g id='F1.components'><g id='kit-input'><rect id='kit-input-box' width='30' height='20'/></g>"
            "<g id='kit-output'><rect id='kit-output-box' x='60' width='30' height='20'/></g></g></svg>\n",
            encoding="utf-8",
        )
        d3_preview = root / "research" / "d3-preview.png"
        from PIL import Image

        Image.new("RGB", (100, 60), "white").save(d3_preview)
        d3_snapshot = root / "research" / "d3-structure.json"
        d3_snapshot_value = {
            "schema_version": STRUCTURE_SNAPSHOT_SCHEMA,
            "artifact_id": "F1",
            "editor": "svg-editor",
            "captured_from_path": "research/d3.svg",
            "captured_from_sha256": sha256_file(d3_source),
            "capture_method": "svg-dom",
            "capture_run_id": "self-test-svg-readback",
            "nodes": [
                {"native_object_id": "F1.assembly", "parent_native_object_id": None, "component_id": "F1.assembly", "kind": "assembly", "visible": True, "bounds": [0, 0, 100, 30]},
                {"native_object_id": "F1.input", "parent_native_object_id": "F1.assembly", "component_id": "F1.input", "kind": "module", "visible": True, "bounds": [0, 0, 30, 20]},
                {"native_object_id": "input-box", "parent_native_object_id": "F1.input", "component_id": "F1.input", "kind": "shape", "visible": True, "bounds": [0, 0, 30, 20]},
                {"native_object_id": "input-text", "parent_native_object_id": "F1.input", "component_id": "F1.input.label", "kind": "primitive", "visible": True, "bounds": [2, 2, 20, 10]},
                {"native_object_id": "F1.output", "parent_native_object_id": "F1.assembly", "component_id": "F1.output", "kind": "module", "visible": True, "bounds": [60, 0, 30, 20]},
                {"native_object_id": "output-box", "parent_native_object_id": "F1.output", "component_id": "F1.output", "kind": "shape", "visible": True, "bounds": [60, 0, 30, 20]},
                {"native_object_id": "output-text", "parent_native_object_id": "F1.output", "component_id": "F1.output.label", "kind": "primitive", "visible": True, "bounds": [62, 2, 20, 10]},
                {"native_object_id": "F1.components", "parent_native_object_id": None, "component_id": None, "kind": "component-board", "visible": True, "bounds": [0, 30, 100, 30]},
                {"native_object_id": "kit-input", "parent_native_object_id": "F1.components", "component_id": "F1.input", "kind": "module", "visible": True, "bounds": [0, 35, 30, 20]},
                {"native_object_id": "kit-input-box", "parent_native_object_id": "kit-input", "component_id": "F1.input", "kind": "shape", "visible": True, "bounds": [0, 35, 30, 20]},
                {"native_object_id": "kit-output", "parent_native_object_id": "F1.components", "component_id": "F1.output", "kind": "module", "visible": True, "bounds": [60, 35, 30, 20]},
                {"native_object_id": "kit-output-box", "parent_native_object_id": "kit-output", "component_id": "F1.output", "kind": "shape", "visible": True, "bounds": [60, 35, 30, 20]},
            ],
            "component_board_root_native_object_ids": ["F1.components"],
        }
        write_json(d3_snapshot, d3_snapshot_value)
        d3_qa = root / "research" / "d3-composition-qa.md"
        d3_qa.write_text("Ungroup, text edit, move, copy, reassembly, and export round trip all passed.\n", encoding="utf-8")
        d3_completed_at = now_utc()
        d3_roundtrip_binding = sha256_text("|".join([
            sha256_file(d3_source), sha256_file(d3_preview),
            "self-test-svg-readback", d3_completed_at,
        ]))
        d3_manifest_path = root / "research" / "d3-composition.json"
        d3_manifest = {
            "schema_version": COMPOSITION_SCHEMA,
            "artifact_id": "F1",
            "mode": "both",
            "target_editor": "svg-editor",
            "native_source_paths": ["research/d3.svg"],
            "component_board_paths": ["research/d3.svg"],
            "structure_snapshot_path": "research/d3-structure.json",
            "assembly_root_id": "F1.assembly",
            "top_level_component_ids": ["F1.input", "F1.output"],
            "hierarchy": [
                {"component_id": "F1.assembly", "parent_id": None, "level": "assembly", "native_object_ids": ["F1.assembly"], "reusable": False, "independently_movable": True, "independently_editable": True},
                {"component_id": "F1.input", "parent_id": "F1.assembly", "level": "module", "native_object_ids": ["F1.input"], "reusable": True, "independently_movable": True, "independently_editable": True},
                {"component_id": "F1.input.label", "parent_id": "F1.input", "level": "primitive", "native_object_ids": ["input-text"], "reusable": False, "independently_movable": True, "independently_editable": True},
                {"component_id": "F1.output", "parent_id": "F1.assembly", "level": "module", "native_object_ids": ["F1.output"], "reusable": True, "independently_movable": True, "independently_editable": True},
                {"component_id": "F1.output.label", "parent_id": "F1.output", "level": "primitive", "native_object_ids": ["output-text"], "reusable": False, "independently_movable": True, "independently_editable": True},
            ],
            "layer_order": ["background", "interconnect", "modules", "labels", "annotations"],
            "interconnect_group_id": None,
            "named_ports": [],
            "flattened_assets": [],
            "manual_edit_test": {
                "status": "pass", "editor": "svg-editor",
                "operations": ["ungroup-one-level", "edit-text", "move-component", "copy-component", "copy-from-component-board", "reassemble", "export-roundtrip"],
                "observed_ungroup_steps_to_primitives": 2,
                "verified_locked_elements": ["module identity"],
                "report_path": "research/d3-composition-qa.md",
                "roundtrip_preview_path": "research/d3-preview.png",
                "roundtrip_preview_sha256": sha256_file(d3_preview),
                "edited_native_source_path": "research/d3.svg",
                "edited_native_source_sha256": sha256_file(d3_source),
                "capture_run_id": "self-test-svg-readback",
                "completed_at": d3_completed_at,
                "roundtrip_binding_sha256": d3_roundtrip_binding,
            },
        }
        write_json(d3_manifest_path, d3_manifest)
        d3_delivery_errors: list[str] = []
        d3_documents, d3_assets = validate_composition_manifest(
            "research/d3-composition.json", d3_contract, {}, "d3-self-test", root,
            d3_delivery_errors, {}, {d3_source}, {d3_qa}, {d3_preview},
        )
        assert not d3_delivery_errors, d3_delivery_errors
        assert d3_manifest_path in d3_documents and d3_snapshot in d3_documents and not d3_assets
        steps.append("d3-composition-manifest-valid")

        d3_export = root / "research" / "d3-export.svg"
        d3_export.write_text(d3_source.read_text(encoding="utf-8"), encoding="utf-8")
        d3_semantic_report = root / "research" / "d3-semantic.md"
        d3_semantic_report.write_text("Independent semantic review passed the D3 source and component hierarchy.\n", encoding="utf-8")
        d3_visual_report = root / "research" / "d3-visual.md"
        d3_visual_report.write_text("Independent visual review passed the D3 export and round-trip preview.\n", encoding="utf-8")
        d3_delivery_contract = copy.deepcopy(d3_contract)
        d3_delivery_contract["primary_formats"] = ["svg"]

        def local_bundle(paths: list[Path]) -> list[dict[str, str]]:
            return [
                {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}
                for path in paths
            ]

        d3_semantic_bundle = [
            d3_export, d3_source, d3_qa, d3_manifest_path, d3_snapshot,
            root / "research" / "claims.md", data_path,
        ]
        d3_record = {
            "source_paths": [],
            "editable_source_paths": ["research/d3.svg"],
            "export_paths": ["research/d3-export.svg"],
            "preview_paths": ["research/d3-preview.png"],
            "qa_report_paths": ["research/d3-composition-qa.md"],
            "composition_manifest_path": "research/d3-composition.json",
            "semantic_review": {
                "status": "pass", "reviewer_id": "d3-semantic-reviewer",
                "report_path": "research/d3-semantic.md",
                "reviewed_bundle": local_bundle(d3_semantic_bundle),
            },
            "visual_review": {
                "status": "pass", "reviewer_id": "d3-visual-reviewer",
                "report_path": "research/d3-visual.md",
                "reviewed_bundle": local_bundle([d3_export, d3_preview]),
            },
        }
        d3_record_errors: list[str] = []
        d3_lock_hashes: dict[Path, str] = {}
        validate_delivery_record(
            d3_record, d3_delivery_contract, "d3-record", "d3-producer", root,
            d3_record_errors, d3_lock_hashes, set(), set(), require_spec_caption=False,
        )
        assert not d3_record_errors, d3_record_errors
        assert d3_manifest_path in d3_lock_hashes and d3_snapshot in d3_lock_hashes
        steps.append("d3-delivery-record-valid")

        flat_source = root / "research" / "d3-flat.svg"
        flat_source.write_text(
            "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='60'>"
            "<image id='whole-canvas' href='preview.png' style='x:0;y:0;width:100%;height:100%'/>"
            "<g id='F1.assembly'><g id='F1.input'><rect id='input-text' width='0' height='0'/></g>"
            "<g id='F1.output'><rect id='output-text' width='0' height='0'/></g></g>"
            "<g id='F1.components'><g id='kit-input'><rect id='kit-input-box' width='0' height='0'/></g>"
            "<g id='kit-output'><rect id='kit-output-box' width='0' height='0'/></g></g></svg>\n",
            encoding="utf-8",
        )
        flat_snapshot_path = root / "research" / "d3-flat-structure.json"
        flat_snapshot = copy.deepcopy(d3_snapshot_value)
        flat_snapshot["captured_from_path"] = "research/d3-flat.svg"
        flat_snapshot["captured_from_sha256"] = sha256_file(flat_source)
        flat_snapshot["nodes"].append(
            {"native_object_id": "whole-canvas", "parent_native_object_id": None, "component_id": None, "kind": "image", "visible": True, "bounds": [0, 0, 100, 60]}
        )
        write_json(flat_snapshot_path, flat_snapshot)
        flat_manifest = copy.deepcopy(d3_manifest)
        flat_manifest["native_source_paths"] = ["research/d3-flat.svg"]
        flat_manifest["component_board_paths"] = ["research/d3-flat.svg"]
        flat_manifest["structure_snapshot_path"] = "research/d3-flat-structure.json"
        write_json(d3_manifest_path, flat_manifest)
        flat_errors: list[str] = []
        validate_composition_manifest(
            "research/d3-composition.json", d3_contract, {}, "d3-flat-test", root,
            flat_errors, {}, {flat_source}, {d3_qa}, {d3_preview},
        )
        assert any("full-canvas" in item or "full-assembly" in item for item in flat_errors)
        write_json(d3_manifest_path, d3_manifest)
        steps.append("d3-flat-source-rejected")

        malformed_manifest = copy.deepcopy(d3_manifest)
        malformed_manifest["hierarchy"][1]["parent_id"] = []
        write_json(d3_manifest_path, malformed_manifest)
        malformed_errors: list[str] = []
        validate_composition_manifest(
            "research/d3-composition.json", d3_contract, {}, "d3-malformed-test", root,
            malformed_errors, {}, {d3_source}, {d3_qa}, {d3_preview},
        )
        assert any("parent_id" in item for item in malformed_errors)
        write_json(d3_manifest_path, d3_manifest)
        steps.append("d3-malformed-manifest-rejected")

        poisoned = copy.deepcopy(program)
        poisoned["figures"][0]["source_paths"] = ["research/claims.md", ""]
        poisoned_errors, _ = validate_program(poisoned, program_path, root, check_paths=True, check_freeze=False)
        assert any("non-empty strings" in item for item in poisoned_errors)
        steps.append("blank-input-rejected")

        mixed = copy.deepcopy(program)
        mixed_figure = mixed["figures"][0]
        mixed_figure.update({
            "figure_type": "mixed_panel", "preferred_route": "deterministic-composite",
            "fallback_route": "", "exactness": "E2", "editability": "D1",
            "generativity": "G0",
            "panel_plan": [
                {
                    "panel_id": "a", "job": "exact benchmark", "figure_type": "benchmark_plot",
                    "exactness": "E2", "editability": "D1", "generativity": "G0",
                    "source_paths": ["research/claims.md"], "data_paths": ["results/MISSING.csv"],
                    "preferred_route": "exact-plot", "fallback_route": "",
                    "primary_formats": ["pdf", "svg"], "preview_format": "none",
                    "editable_source_required": False,
                },
                {
                    "panel_id": "b", "job": "illustrative context", "figure_type": "mechanism_schematic",
                    "exactness": "E0", "editability": "D0", "generativity": "G2",
                    "source_paths": [], "data_paths": [],
                    "preferred_route": "generative-illustration", "fallback_route": "",
                    "primary_formats": ["png"], "preview_format": "png",
                    "editable_source_required": False,
                },
            ],
        })
        mixed_errors, _ = validate_program(mixed, program_path, root, check_paths=True, check_freeze=False)
        assert any("results/MISSING.csv" in item for item in mixed_errors)
        steps.append("mixed-panel-input-rejected")

        stable_write_json = write_json
        failure_triggered = False

        def flaky_write_json(path: Path, value: dict[str, Any]) -> None:
            nonlocal failure_triggered
            if (
                not failure_triggered
                and path.resolve() == program_path
                and value.get("phase_1", {}).get("status") == "frozen"
            ):
                failure_triggered = True
                raise OSError("simulated transaction interruption")
            stable_write_json(path, value)

        globals()["write_json"] = flaky_write_json
        try:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    command_freeze(argparse.Namespace(program=str(program_path), project_root=None))
                raise AssertionError("simulated freeze interruption did not occur")
            except OSError as exc:
                assert "simulated transaction interruption" in str(exc)
        finally:
            globals()["write_json"] = stable_write_json
        assert (program_path.parent / ".freeze-transaction.json").is_file()
        with contextlib.redirect_stdout(io.StringIO()):
            assert command_freeze(argparse.Namespace(program=str(program_path), project_root=None)) == 0
        steps.append("freeze-transaction-recovered")
        frozen = load_json(program_path)
        errors, _ = validate_program(frozen, program_path, root, check_paths=True, check_freeze=True)
        assert not errors, errors
        steps.append("freeze-valid")

        data_path.write_text(original_data.replace("0.81", "0.82"), encoding="utf-8")
        stale_errors, _ = validate_program(frozen, program_path, root, check_paths=True, check_freeze=True)
        assert any("frozen input changed" in item for item in stale_errors)
        data_path.write_text(original_data, encoding="utf-8")
        steps.append("stale-detected")

        visual_root = root / "research" / "artifacts" / "visual-program"
        files = {
            "figure-specs/F1.json": "{\"figure_id\": \"F1\", \"checks\": [\"exact values and uncertainty\"]}\n",
            "sources/F1.py": "SOURCE = 'results/data.csv'\n",
            "exports/F1.svg": "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'><rect width='10' height='10'/></svg>\n",
            "captions/F1.md": "Exact benchmark comparison with standard-error uncertainty.\n",
            "reviews/F1-qa.md": "All plotted values, units, uncertainty, and formats pass source comparison.\n",
            "reviews/F1-semantic.md": "Independent semantic review passed all claims, values, and caption checks.\n",
            "reviews/F1-visual.md": "Independent visual review passed final-size hierarchy and legibility checks.\n",
        }
        for relative, content in files.items():
            path = visual_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        from pypdf import PdfWriter

        pdf_writer = PdfWriter()
        pdf_writer.add_blank_page(width=100, height=100)
        with (visual_root / "exports" / "F1.pdf").open("wb") as handle:
            pdf_writer.write(handle)
        manifest_path = visual_root / "delivery-manifest.json"
        manifest = load_json(manifest_path)
        manifest["status"] = "verified"
        manifest["producer_id"] = "producer-self-test"
        spec_artifact = "research/artifacts/visual-program/figure-specs/F1.json"
        caption_artifact = "research/artifacts/visual-program/captions/F1.md"
        pdf_artifact = "research/artifacts/visual-program/exports/F1.pdf"
        svg_artifact = "research/artifacts/visual-program/exports/F1.svg"

        def bundle_entry(raw: str) -> dict[str, str]:
            return {"path": raw, "sha256": sha256_file(resolve_path(raw, root))}

        semantic_paths = [
            "research/artifacts/visual-program/figure-program.json",
            "research/artifacts/visual-program/stage-2-prompt.md",
            "research/artifacts/visual-program/freeze.json",
            "research/claims.md",
            "results/data.csv",
            "research/artifacts/visual-program/sources/F1.py",
            "research/artifacts/visual-program/reviews/F1-qa.md",
            spec_artifact,
            caption_artifact,
            pdf_artifact,
            svg_artifact,
        ]

        manifest["figures"] = [{
            "figure_id": "F1",
            "spec_path": "research/artifacts/visual-program/figure-specs/F1.json",
            "source_paths": ["research/artifacts/visual-program/sources/F1.py"],
            "editable_source_paths": [],
            "export_paths": [
                pdf_artifact,
                svg_artifact,
            ],
            "preview_paths": [],
            "caption_path": "research/artifacts/visual-program/captions/F1.md",
            "qa_report_paths": ["research/artifacts/visual-program/reviews/F1-qa.md"],
            "semantic_review": {
                "status": "pass", "reviewer_id": "semantic-reviewer-self-test",
                "report_path": "research/artifacts/visual-program/reviews/F1-semantic.md",
                "reviewed_bundle": [bundle_entry(raw) for raw in semantic_paths],
            },
            "visual_review": {
                "status": "pass", "reviewer_id": "visual-reviewer-self-test",
                "report_path": "research/artifacts/visual-program/reviews/F1-visual.md",
                "reviewed_bundle": [bundle_entry(pdf_artifact), bundle_entry(svg_artifact)],
            },
        }]
        write_json(manifest_path, manifest)

        incomplete = copy.deepcopy(manifest)
        incomplete["figures"][0]["export_paths"] = [svg_artifact]
        write_json(manifest_path, incomplete)
        with contextlib.redirect_stdout(io.StringIO()):
            assert command_validate_delivery(argparse.Namespace(
                program=str(program_path), manifest=str(manifest_path),
                project_root=None, require_integration=False,
            )) == 1
        steps.append("partial-format-rejected")

        poisoned_delivery = copy.deepcopy(manifest)
        poisoned_delivery["figures"][0]["qa_report_paths"] = [""]
        write_json(manifest_path, poisoned_delivery)
        with contextlib.redirect_stdout(io.StringIO()):
            assert command_validate_delivery(argparse.Namespace(
                program=str(program_path), manifest=str(manifest_path),
                project_root=None, require_integration=False,
            )) == 1
        steps.append("blank-delivery-path-rejected")

        write_json(manifest_path, manifest)
        with contextlib.redirect_stdout(io.StringIO()):
            assert command_validate_delivery(argparse.Namespace(
                program=str(program_path), manifest=str(manifest_path),
                project_root=None, require_integration=False,
            )) == 0
        assert (visual_root / "delivery-lock.json").is_file()
        steps.append("delivery-valid")

        with contextlib.redirect_stdout(io.StringIO()):
            assert command_validate_delivery(argparse.Namespace(
                program=str(program_path), manifest=str(manifest_path),
                project_root=None, require_integration=False,
            )) == 0
        steps.append("delivery-lock-idempotent")

        changed_pdf = PdfWriter()
        changed_pdf.add_blank_page(width=101, height=100)
        with (visual_root / "exports" / "F1.pdf").open("wb") as handle:
            changed_pdf.write(handle)
        with contextlib.redirect_stdout(io.StringIO()):
            assert command_validate_delivery(argparse.Namespace(
                program=str(program_path), manifest=str(manifest_path),
                project_root=None, require_integration=False,
            )) == 1
        steps.append("sealed-delivery-drift-rejected")
    print(json.dumps({"self_test": "pass", "steps": steps}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Scaffold a visual program inside a project")
    init.add_argument("project_root")
    init.add_argument("--request-id", required=True)
    init.add_argument("--paper-title", required=True)
    init.add_argument("--stage", choices=sorted(STAGES), required=True)
    init.add_argument("--paper-type", choices=sorted(PAPER_TYPES), required=True)
    init.add_argument("--venue", default="")
    init.add_argument("--owner-phase", choices=sorted(OWNER_PHASES), default="research-manuscript")
    init.add_argument("--version", type=int, default=1)
    init.add_argument("--output-root", default="research/artifacts/visual-program")
    init.set_defaults(func=command_init)

    validate = sub.add_parser("validate-request", help="Validate a draft or frozen visual request")
    validate.add_argument("program")
    validate.add_argument("--project-root")
    validate.add_argument("--check-paths", action="store_true")
    validate.set_defaults(func=command_validate_request)

    freeze = sub.add_parser("freeze", help="Freeze a valid Phase-1 request and its inputs")
    freeze.add_argument("program")
    freeze.add_argument("--project-root")
    freeze.set_defaults(func=command_freeze)

    delivery = sub.add_parser("validate-delivery", help="Validate Phase-2 delivery against the frozen request")
    delivery.add_argument("program")
    delivery.add_argument("manifest")
    delivery.add_argument("--project-root")
    delivery.add_argument("--require-integration", action="store_true")
    delivery.set_defaults(func=command_validate_delivery)

    self_test = sub.add_parser("self-test", help="Run isolated positive and negative contract tests")
    self_test.set_defaults(func=command_self_test)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
