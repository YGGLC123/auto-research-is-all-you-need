#!/usr/bin/env python3
"""Route-A rendering backend for auto-research v2.9 (figure-engineering-contract section 5).

Six chart templates, each a pure function of

    (data table, template parameters, venue profile, palette) -> pdf + svg + png

Everything here is stdlib except matplotlib, which is imported lazily so that a
host without it degrades to "Route A unavailable" instead of crashing at import.
scienceplots and cmcrameri are optional refinements: when they are absent the
templates fall back to an in-module approximation and say so
(`style_degraded` / `colormap_degraded` in the returned provenance block).

Determinism: SOURCE_DATE_EPOCH, a fixed svg hashsalt and metadata-stripped
writers make a re-render byte-identical, so `render_hash` only moves when the
figure actually changes.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
from pathlib import Path

TEMPLATE_IDS = ("line-series", "bar-grouped", "scatter-fit",
                "forest-ci", "event-study", "heatmap")

NUMERIC_ROLES = ("x", "y", "lo", "hi", "value", "weight")

# Deterministic rendering: matplotlib honours SOURCE_DATE_EPOCH for pdf/png dates.
# 2010-01-01 rather than 0 -- a zero epoch makes Pillow warn on every png write.
os.environ.setdefault("SOURCE_DATE_EPOCH", "1262304000")

FALLBACK_COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7",
                   "#E69F00", "#56B4E9", "#F0E442", "#000000"]


# ------------------------------------------------------------------ capability probe

def probe_deps() -> dict:
    """python-import probe for the optional rendering capabilities."""
    out = {}
    for name in ("matplotlib", "scienceplots", "cmcrameri", "numpy"):
        try:
            __import__(name)
            out[name] = True
        except Exception:
            out[name] = False
    return out


# ------------------------------------------------------------------ data loading

class DataError(Exception):
    pass


def _to_number(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return None if (isinstance(raw, float) and math.isnan(raw)) else float(raw)
    text = str(raw).strip()
    if text == "" or text.lower() in ("na", "nan", "none", "null", "."):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_records(path) -> list:
    """CSV or JSON -> list of dict records. JSON may be a record list or a
    column dict; a {"records": [...]} / {"rows": [...]} wrapper also works."""
    path = Path(path)
    if not path.exists():
        raise DataError("data file not found: %s" % path)
    if path.suffix.lower() in (".json", ".jsn"):
        blob = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(blob, dict):
            for key in ("records", "rows", "data"):
                if isinstance(blob.get(key), list):
                    blob = blob[key]
                    break
        if isinstance(blob, dict):                       # column-oriented
            keys = list(blob.keys())
            length = max((len(v) for v in blob.values() if isinstance(v, list)), default=0)
            return [{k: (blob[k][i] if isinstance(blob[k], list) and i < len(blob[k]) else None)
                     for k in keys} for i in range(length)]
        if isinstance(blob, list):
            return [r for r in blob if isinstance(r, dict)]
        raise DataError("unsupported JSON shape in %s" % path.name)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build_table(path, columns: dict, types: dict = None) -> dict:
    """Extract the role->series table named by `columns` (role -> column name).

    A role whose column is absent from the mapping falls back to a column
    literally named after the role, so a tidy `x,y,lo,hi` file needs no mapping.

    Role slots that usually carry numbers (x, y, lo, hi, value) are parsed as
    numbers -- unless the column plainly holds names. A heatmap's `y` is a method
    label, not a quantity, and silently coercing it to NaN would collapse the
    whole axis into one empty row; so a numeric slot whose every non-blank value
    fails to parse is kept nominal instead. An explicit `types` mapping
    (role -> quantitative|nominal|...) always wins over that inference.
    """
    records = read_records(path)
    if not records:
        raise DataError("no records in %s" % Path(path).name)
    header = list(records[0].keys())
    resolved, roles, field_types, missing = {}, {}, {}, 0
    for role, column in (columns or {}).items():
        if column in header:
            resolved[role] = column
    for role in ("x", "y", "lo", "hi", "group", "label", "value", "weight"):
        if role not in resolved and role in header:
            resolved[role] = role
    if not resolved:
        raise DataError("none of the mapped columns exist; file has %s" % ", ".join(header))
    for role, column in resolved.items():
        raw_values = [record.get(column) for record in records]
        declared = (types or {}).get(role)
        numeric = role in NUMERIC_ROLES and declared not in ("nominal",)
        parsed = None
        if numeric:
            parsed = [_to_number(v) for v in raw_values]
            non_blank = [v for v in raw_values if v is not None and str(v).strip() != ""]
            if non_blank and not any(n is not None for n in parsed):
                numeric = False          # a numeric slot holding names, not quantities
        if numeric:
            roles[role] = parsed
            field_types[role] = "quantitative"
            missing += sum(1 for n in parsed if n is None)
        else:
            series = [None if v is None or not str(v).strip() else str(v).strip()
                      for v in raw_values]
            roles[role] = series
            field_types[role] = declared or "nominal"
            missing += sum(1 for s in series if s is None)
    return {"roles": roles, "columns": resolved, "field_types": field_types,
            "n": len(records), "missing_cells": missing, "source": str(path),
            "header": header}


def data_hash(table: dict) -> str:
    """Hash of the extracted values only - venue and style never move it."""
    payload = {"roles": {k: table["roles"][k] for k in sorted(table["roles"])},
               "n": table["n"]}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _series(table, role):
    return table["roles"].get(role)


def _groups(table):
    """[(group_label, [row indices])] preserving first-appearance order."""
    labels = _series(table, "group")
    if not labels:
        return [(None, list(range(table["n"])))]
    order, buckets = [], {}
    for index, label in enumerate(labels):
        key = label if label is not None else "n/a"
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(index)
    return [(key, buckets[key]) for key in order]


def _clean_xy(table, indices, xrole="x", yrole="y"):
    xs, ys, keep = _series(table, xrole), _series(table, yrole), []
    for i in indices:
        if xs is not None and xs[i] is None:
            continue
        if ys is not None and ys[i] is None:
            continue
        keep.append(i)
    return keep


# ------------------------------------------------------------------ style

def _mm_to_in(mm):
    return float(mm) / 25.4


def resolve_size(venue: dict, params: dict, width_key: str = "single"):
    widths = venue.get("figure_width_mm") or {}
    width_mm = float(widths.get(width_key) or widths.get("single") or 89.0)
    if params.get("width_mm"):
        width_mm = float(params["width_mm"])
    aspect = float(params.get("aspect") or 0.68)
    return width_mm, width_mm * aspect


def apply_style(venue: dict, deps: dict, params: dict):
    """Install the venue's look. Returns (plt, style_report)."""
    import logging
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Type-42 embedding makes matplotlib subset the font through fontTools, which
    # warns on stderr about the font file's own 1904-epoch head timestamps. That
    # noise would corrupt the JSON these CLIs print, and says nothing about the figure.
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    plt.rcdefaults()
    stack = list(venue.get("style_stack") or [])
    degraded, applied, reason = True, [], "scienceplots not installed"
    if deps.get("scienceplots") and stack:
        try:
            import scienceplots                          # noqa: F401 (registers styles)
            usable = [s for s in stack if s in plt.style.available]
            unknown = [s for s in stack if s not in plt.style.available]
            if usable:
                plt.style.use(usable)
                applied, degraded = usable, bool(unknown)
                reason = ("unknown styles skipped: %s" % ",".join(unknown)) if unknown else ""
        except Exception as exc:                         # pragma: no cover
            reason = "scienceplots failed: %s" % exc
    if degraded and not applied:
        _approximate_science_style(plt)

    font = venue.get("font") or {}
    size = float(font.get("size_pt") or 8)
    family = font.get("family") or "sans-serif"
    stackname = "font.serif" if family == "serif" else "font.sans-serif"
    rc = plt.rcParams
    rc["text.usetex"] = False
    rc["pdf.fonttype"] = 42
    rc["ps.fonttype"] = 42
    rc["svg.fonttype"] = "none"
    rc["svg.hashsalt"] = "auto-research-v2.9"
    rc["font.family"] = family
    if font.get("stack"):
        rc[stackname] = list(font["stack"]) + list(rc[stackname])
    rc["font.size"] = size
    rc["axes.labelsize"] = size
    rc["axes.titlesize"] = size + 1
    rc["xtick.labelsize"] = max(size - 1, 5)
    rc["ytick.labelsize"] = max(size - 1, 5)
    rc["legend.fontsize"] = max(size - 1, 5)
    rc["figure.dpi"] = 100
    rc["savefig.dpi"] = int(venue.get("dpi") or 600)
    # NOT bbox="tight": a tight box crops to content, so the emitted pdf would
    # no longer be the venue's column width. Constrained layout fits the content
    # inside the exact figsize instead, which is what the size QA check asserts.
    rc["savefig.bbox"] = None
    rc["savefig.pad_inches"] = 0.0
    rc["figure.constrained_layout.use"] = True
    rc["figure.constrained_layout.h_pad"] = 0.012
    rc["figure.constrained_layout.w_pad"] = 0.012
    if params.get("grid"):
        rc["axes.grid"] = True
    return plt, {"style_stack_requested": stack, "style_stack_applied": applied,
                 "style_degraded": bool(degraded), "style_degraded_reason": reason}


def _approximate_science_style(plt):
    """In-module stand-in for the scienceplots look (thin spines, inward ticks,
    tight margins). Not byte-identical to `science` - that is why it is flagged."""
    plt.rcParams.update({
        "axes.linewidth": 0.5, "grid.linewidth": 0.4, "lines.linewidth": 1.0,
        "lines.markersize": 3.0, "axes.prop_cycle": plt.cycler("color", FALLBACK_COLORS),
        "xtick.direction": "in", "ytick.direction": "in",
        "xtick.major.width": 0.5, "ytick.major.width": 0.5,
        "xtick.minor.width": 0.4, "ytick.minor.width": 0.4,
        "xtick.major.size": 3.0, "ytick.major.size": 3.0,
        "xtick.top": True, "ytick.right": True,
        "legend.frameon": False, "axes.axisbelow": True,
        "figure.autolayout": False,
    })


def resolve_colormap(venue: dict, deps: dict, params: dict):
    name = params.get("colormap") or venue.get("colormap") or "cmc.batlow"
    if name.startswith("cmc.") and deps.get("cmcrameri"):
        try:
            from cmcrameri import cm as cmc
            return getattr(cmc, name.split(".", 1)[1]), name, False, ""
        except Exception as exc:                         # pragma: no cover
            return "viridis", "viridis", True, "cmcrameri lookup failed: %s" % exc
    if name.startswith("cmc."):
        return "viridis", "viridis", True, "cmcrameri not installed"
    return name, name, False, ""


def _panel_label(ax, params, venue):
    text = params.get("panel")
    if not text:
        return
    style = venue.get("panel_label") or "a"
    if style == "A":
        text = str(text).upper()
    elif style == "(a)":
        text = "(%s)" % str(text).lower()
    else:
        text = str(text).lower()
    ax.text(-0.02, 1.04, text, transform=ax.transAxes, fontweight="bold",
            ha="right", va="bottom")


def _finish(ax, params, venue, default_x="", default_y=""):
    ax.set_xlabel(params.get("xlabel") or default_x)
    ax.set_ylabel(params.get("ylabel") or default_y)
    if params.get("title"):
        ax.set_title(params["title"])
    if params.get("logy"):
        ax.set_yscale("log")
    if params.get("logx"):
        ax.set_xscale("log")
    _panel_label(ax, params, venue)


# ------------------------------------------------------------------ templates

def _t_line_series(plt, fig, ax, table, params, venue, colors, deps):
    notes = []
    for index, (label, rows) in enumerate(_groups(table)):
        keep = _clean_xy(table, rows)
        pairs = sorted((table["roles"]["x"][i], table["roles"]["y"][i]) for i in keep)
        if not pairs:
            continue
        color = colors[index % len(colors)]
        ax.plot([p[0] for p in pairs], [p[1] for p in pairs], color=color,
                label=(str(label) if label is not None else None),
                marker=("o" if params.get("marker", True) else None))
        if params.get("band") and _series(table, "lo") and _series(table, "hi"):
            order = sorted(keep, key=lambda i: table["roles"]["x"][i])
            los = [table["roles"]["lo"][i] for i in order]
            his = [table["roles"]["hi"][i] for i in order]
            if all(v is not None for v in los + his):
                ax.fill_between([table["roles"]["x"][i] for i in order], los, his,
                                color=color, alpha=0.18, linewidth=0)
    if params.get("reference_line") is not None:
        ax.axhline(float(params["reference_line"]), color="0.35", linestyle="--", linewidth=0.7)
    _finish(ax, params, venue, table["columns"].get("x", "x"), table["columns"].get("y", "y"))
    if params.get("legend", True) and _series(table, "group"):
        ax.legend(loc=params.get("legend_loc", "best"))
    return notes


def _t_bar_grouped(plt, fig, ax, table, params, venue, colors, deps):
    labels_series = _series(table, "label") or _series(table, "x")
    if labels_series is None:
        raise DataError("bar-grouped needs a `label` (or nominal `x`) role")
    categories, seen = [], set()
    for value in labels_series:
        key = _cat(value)
        if key not in seen:
            seen.add(key)
            categories.append(key)
    groups = _groups(table)
    width = float(params.get("bar_width") or 0.8) / max(len(groups), 1)
    for index, (label, rows) in enumerate(groups):
        heights, errs = {}, {}
        lo, hi = _series(table, "lo"), _series(table, "hi")
        for i in rows:
            y = table["roles"]["y"][i]
            if y is None:
                continue
            key = _cat(labels_series[i])
            heights[key] = y
            if lo and hi and lo[i] is not None and hi[i] is not None:
                errs[key] = (y - lo[i], hi[i] - y)
        positions = [j - 0.4 + width * (index + 0.5) for j in range(len(categories))]
        values = [heights.get(c, 0.0) for c in categories]
        yerr = None
        if errs:
            yerr = [[errs.get(c, (0.0, 0.0))[0] for c in categories],
                    [errs.get(c, (0.0, 0.0))[1] for c in categories]]
        ax.bar(positions, values, width=width * 0.92, color=colors[index % len(colors)],
               label=(str(label) if label is not None else None),
               yerr=yerr, capsize=1.5, error_kw={"elinewidth": 0.6})
    ax.set_xticks(range(len(categories)))
    ax.set_xticklabels(categories, rotation=float(params.get("xtick_rotation") or 0),
                       ha=("right" if params.get("xtick_rotation") else "center"))
    if params.get("reference_line") is not None:
        ax.axhline(float(params["reference_line"]), color="0.35", linestyle="--", linewidth=0.7)
    _finish(ax, params, venue, "", table["columns"].get("y", "y"))
    if params.get("legend", True) and _series(table, "group"):
        ax.legend(loc=params.get("legend_loc", "best"))
    return []


def _ols(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((ys[i] - (intercept + slope * xs[i])) ** 2 for i in range(n))
    return slope, intercept, (1.0 - ss_res / ss_tot if ss_tot else float("nan"))


def _t_scatter_fit(plt, fig, ax, table, params, venue, colors, deps):
    notes = []
    for index, (label, rows) in enumerate(_groups(table)):
        keep = _clean_xy(table, rows)
        xs = [table["roles"]["x"][i] for i in keep]
        ys = [table["roles"]["y"][i] for i in keep]
        if not xs:
            continue
        color = colors[index % len(colors)]
        ax.scatter(xs, ys, s=float(params.get("point_size") or 8), color=color,
                   alpha=float(params.get("alpha") or 0.85), linewidths=0,
                   label=(str(label) if label is not None else None))
        if params.get("fit", "ols") == "ols" and len(xs) >= 2:
            fit = _ols(xs, ys)
            if fit is None:
                notes.append("degenerate x for group %s: no fit" % label)
                continue
            slope, intercept, r2 = fit
            lo_x, hi_x = min(xs), max(xs)
            ax.plot([lo_x, hi_x], [intercept + slope * lo_x, intercept + slope * hi_x],
                    color=color, linewidth=0.9)
            if params.get("show_r2", True):
                ax.annotate("$R^2=%.2f$" % r2, xy=(0.04, 0.94 - 0.07 * index),
                            xycoords="axes fraction", color=color, va="top")
    if params.get("identity_line"):
        lo = min(ax.get_xlim()[0], ax.get_ylim()[0])
        hi = max(ax.get_xlim()[1], ax.get_ylim()[1])
        ax.plot([lo, hi], [lo, hi], color="0.5", linewidth=0.6, linestyle=":")
    _finish(ax, params, venue, table["columns"].get("x", "x"), table["columns"].get("y", "y"))
    if params.get("legend", True) and _series(table, "group"):
        ax.legend(loc=params.get("legend_loc", "best"))
    return notes


def _t_forest_ci(plt, fig, ax, table, params, venue, colors, deps):
    labels, ys = _series(table, "label"), _series(table, "y")
    lo, hi = _series(table, "lo"), _series(table, "hi")
    if labels is None or ys is None or lo is None or hi is None:
        raise DataError("forest-ci needs label, y, lo, hi roles")
    rows = [i for i in range(table["n"]) if ys[i] is not None]
    if params.get("sort"):
        rows.sort(key=lambda i: ys[i])
    positions = list(range(len(rows)))[::-1]
    groups = _series(table, "group")
    keys = [g for g, _ in _groups(table)]
    for slot, i in zip(positions, rows):
        color = colors[0]
        if groups:
            color = colors[keys.index(groups[i] if groups[i] is not None else "n/a") % len(colors)]
        left = ys[i] - lo[i] if lo[i] is not None else 0.0
        right = hi[i] - ys[i] if hi[i] is not None else 0.0
        ax.errorbar([ys[i]], [slot], xerr=[[left], [right]], fmt="o",
                    color=color, markersize=3.0, elinewidth=0.8, capsize=1.6)
    ax.set_yticks(positions)
    ax.set_yticklabels([_cat(labels[i]) for i in rows])
    ax.set_ylim(-0.7, len(rows) - 0.3)
    reference = params.get("reference", 0.0)
    if reference is not None:
        ax.axvline(float(reference), color="0.35", linestyle="--", linewidth=0.7)
    _finish(ax, params, venue, table["columns"].get("y", "estimate"), "")
    return []


def _t_event_study(plt, fig, ax, table, params, venue, colors, deps):
    notes = []
    lo, hi = _series(table, "lo"), _series(table, "hi")
    for index, (label, rows) in enumerate(_groups(table)):
        keep = sorted(_clean_xy(table, rows), key=lambda i: table["roles"]["x"][i])
        if not keep:
            continue
        xs = [table["roles"]["x"][i] for i in keep]
        ys = [table["roles"]["y"][i] for i in keep]
        color = colors[index % len(colors)]
        ax.plot(xs, ys, color=color, marker="o", markersize=2.6,
                label=(str(label) if label is not None else None))
        if lo and hi and all(lo[i] is not None and hi[i] is not None for i in keep):
            if params.get("band", True):
                ax.fill_between(xs, [lo[i] for i in keep], [hi[i] for i in keep],
                                color=color, alpha=0.18, linewidth=0)
            else:
                ax.errorbar(xs, ys,
                            yerr=[[ys[j] - lo[i] for j, i in enumerate(keep)],
                                  [hi[i] - ys[j] for j, i in enumerate(keep)]],
                            fmt="none", ecolor=color, elinewidth=0.7, capsize=1.4)
        else:
            notes.append("no complete lo/hi pair: confidence band omitted")
    event_time = params.get("event_time", 0)
    if event_time is not None:
        ax.axvline(float(event_time), color="0.35", linestyle="--", linewidth=0.7)
    ax.axhline(float(params.get("reference", 0.0)), color="0.6", linewidth=0.5)
    _finish(ax, params, venue, table["columns"].get("x", "event time"),
            table["columns"].get("y", "effect"))
    if params.get("legend", True) and _series(table, "group"):
        ax.legend(loc=params.get("legend_loc", "best"))
    return notes


def _sort_key(text):
    number = _to_number(text)
    return (0, number, "") if number is not None else (1, 0.0, str(text))


def _cat(value):
    """Category label for an axis level: 3.0 prints as `3`, not `3.0`."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _t_heatmap(plt, fig, ax, table, params, venue, colors, deps):
    xs, ys, values = _series(table, "x"), _series(table, "y"), _series(table, "value")
    if xs is None or ys is None or values is None:
        raise DataError("heatmap needs x, y and value roles")
    xcats, ycats = [], []
    for value in xs:
        if _cat(value) not in xcats:
            xcats.append(_cat(value))
    for value in ys:
        if _cat(value) not in ycats:
            ycats.append(_cat(value))
    if params.get("sort_axes", True):
        xcats = sorted(xcats, key=_sort_key)
        ycats = sorted(ycats, key=_sort_key)
    grid = [[float("nan")] * len(xcats) for _ in ycats]
    for i in range(table["n"]):
        if values[i] is None:
            continue
        grid[ycats.index(_cat(ys[i]))][xcats.index(_cat(xs[i]))] = values[i]
    cmap, cmap_name, cmap_degraded, cmap_reason = resolve_colormap(venue, deps, params)
    image = ax.imshow(grid, cmap=cmap, aspect="auto", origin="lower", interpolation="nearest")
    ax.set_xticks(range(len(xcats)))
    ax.set_xticklabels(xcats, rotation=float(params.get("xtick_rotation") or 0),
                       ha=("right" if params.get("xtick_rotation") else "center"))
    ax.set_yticks(range(len(ycats)))
    ax.set_yticklabels(ycats)
    bar = fig.colorbar(image, ax=ax, fraction=0.045, pad=0.03)
    if params.get("colorbar_label"):
        bar.set_label(params["colorbar_label"])
    bar.outline.set_linewidth(0.5)
    if params.get("annotate"):
        for r, row in enumerate(grid):
            for c, cell in enumerate(row):
                if cell == cell:                          # not NaN
                    ax.text(c, r, format(cell, params.get("annotate_format", ".2f")),
                            ha="center", va="center",
                            fontsize=max(plt.rcParams["font.size"] - 2, 4))
    _finish(ax, params, venue, table["columns"].get("x", "x"), table["columns"].get("y", "y"))
    return ["colormap degraded to %s (%s)" % (cmap_name, cmap_reason)] if cmap_degraded else []


TEMPLATES = {"line-series": _t_line_series, "bar-grouped": _t_bar_grouped,
             "scatter-fit": _t_scatter_fit, "forest-ci": _t_forest_ci,
             "event-study": _t_event_study, "heatmap": _t_heatmap}

DEFAULT_ASPECT = {"line-series": 0.68, "bar-grouped": 0.68, "scatter-fit": 0.80,
                  "forest-ci": 0.85, "event-study": 0.62, "heatmap": 0.80}


# ------------------------------------------------------------------ render entry point

def render(template_id: str, table: dict, params: dict, venue: dict, palette: dict,
           outdir, basename: str = "figure", exports=None) -> dict:
    """Render one chart template. Returns a provenance/QA-ready dict."""
    if template_id not in TEMPLATES:
        raise DataError("unknown chart template %r (have %s)"
                        % (template_id, ", ".join(sorted(TEMPLATES))))
    deps = probe_deps()
    if not deps["matplotlib"]:
        raise DataError("matplotlib not installed - Route A unavailable")
    params = dict(params or {})
    params.setdefault("aspect", DEFAULT_ASPECT[template_id])
    plt, style_report = apply_style(venue, deps, params)
    colors = list((palette or {}).get("colors") or FALLBACK_COLORS)
    if (venue.get("constraints") or {}).get("color") == "mono" or params.get("mono"):
        colors = ["#000000", "#555555", "#999999", "#cccccc"]
    width_mm, height_mm = resolve_size(venue, params, params.get("width") or "single")
    fig, ax = plt.subplots(figsize=(_mm_to_in(width_mm), _mm_to_in(height_mm)),
                           layout="constrained")
    outputs = {}
    try:
        notes = TEMPLATES[template_id](plt, fig, ax, table, params, venue, colors, deps) or []
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        for fmt in list(exports or venue.get("export") or ["pdf", "svg", "png"]):
            target = outdir / ("%s.%s" % (basename, fmt))
            metadata = {"pdf": {"CreationDate": None, "Producer": None, "Creator": None},
                        "svg": {"Date": None},
                        "png": {"Software": None}}.get(fmt)
            fig.savefig(str(target), format=fmt, metadata=metadata,
                        dpi=int(venue.get("dpi") or 600))
            outputs[fmt] = target
    finally:
        plt.close(fig)
    report = {"template": template_id, "outputs": {k: str(v) for k, v in outputs.items()},
              "width_mm": width_mm, "height_mm": height_mm, "notes": notes,
              "palette": (palette or {}).get("id"), "colors_used": colors[:8], "deps": deps}
    report.update(style_report)
    report["colormap_degraded"] = any("colormap degraded" in n for n in notes)
    return report


# ------------------------------------------------------------------ Vega-Lite export

VL_TYPE = {"x": "quantitative", "y": "quantitative", "lo": "quantitative",
           "hi": "quantitative", "value": "quantitative",
           "group": "nominal", "label": "nominal"}


def vegalite_spec(template_id: str, table: dict, params: dict, venue: dict,
                  palette: dict, data_path: str) -> dict:
    """A portable Vega-Lite v5 spec for the same chart (spec only; not rendered)."""
    columns = table["columns"]
    types = dict(VL_TYPE)
    types.update(table.get("field_types") or {})     # what the data actually turned out to be
    types.update(params.get("vl_types") or {})
    widths = venue.get("figure_width_mm") or {}
    width_pt = float(widths.get(params.get("width") or "single") or 89.0) * 72.0 / 25.4
    colors = list((palette or {}).get("colors") or FALLBACK_COLORS)
    font = venue.get("font") or {}
    size = float(font.get("size_pt") or 8)

    def field(role, override_type=None):
        return {"field": columns[role], "type": override_type or types.get(role, "quantitative")}

    encoding = {}
    if "x" in columns:
        encoding["x"] = field("x", "nominal" if template_id == "heatmap" else None)
    if "y" in columns:
        encoding["y"] = field("y", "nominal" if template_id == "heatmap" else None)
    if "group" in columns:
        encoding["color"] = dict(field("group", "nominal"), scale={"range": colors[:8]})
    mark = {"line-series": {"type": "line", "point": True},
            "bar-grouped": {"type": "bar"},
            "scatter-fit": {"type": "point", "filled": True},
            "forest-ci": {"type": "point", "filled": True},
            "event-study": {"type": "line", "point": True},
            "heatmap": {"type": "rect"}}[template_id]
    if template_id in ("bar-grouped", "forest-ci") and "label" in columns:
        encoding = {"y": field("label", "nominal"), "x": field("y")}
        if "group" in columns:
            encoding["color"] = dict(field("group", "nominal"), scale={"range": colors[:8]})
    if template_id == "heatmap" and "value" in columns:
        encoding["color"] = dict(field("value"),
                                 scale={"scheme": params.get("vl_scheme", "viridis")})
    layers = [{"mark": mark, "encoding": {k: v for k, v in encoding.items() if v}}]
    if template_id in ("forest-ci", "event-study") and "lo" in columns and "hi" in columns:
        if template_id == "forest-ci":
            band = {"mark": {"type": "rule"},
                    "encoding": {"y": field("label", "nominal"),
                                 "x": field("lo"), "x2": {"field": columns["hi"]}}}
        else:
            band = {"mark": {"type": "area", "opacity": 0.2},
                    "encoding": {"x": field("x"), "y": field("lo"),
                                 "y2": {"field": columns["hi"]}}}
        layers.insert(0, band)
    return {"$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "description": params.get("title") or ("auto-research %s" % template_id),
            "data": {"url": data_path,
                     "format": {"type": "csv" if str(data_path).lower().endswith(".csv")
                                else "json"}},
            "width": round(width_pt * 0.72, 1),
            "height": round(width_pt * 0.72 * float(params.get("aspect")
                                                    or DEFAULT_ASPECT[template_id]), 1),
            "config": {"font": (font.get("stack") or ["Helvetica"])[0],
                       "axis": {"labelFontSize": max(size - 1, 5), "titleFontSize": size},
                       "legend": {"labelFontSize": max(size - 1, 5)},
                       "view": {"stroke": None}},
            "layer": layers}


# ------------------------------------------------------------------ built-in sample data

def _line_sample():
    rows = ["x,y,series"]
    for name, base in (("baseline", 1.0), ("treated", 1.4)):
        for t in range(0, 13):
            rows.append("%d,%.4f,%s" % (t, base + 0.11 * t - 0.004 * t * t, name))
    return "\n".join(rows) + "\n"


def _bar_sample():
    rows = ["model,score,split"]
    for model, values in (("ridge", (0.61, 0.58)), ("boost", (0.74, 0.69)), ("ours", (0.81, 0.78))):
        for value, split in zip(values, ("in-sample", "out-of-sample")):
            rows.append("%s,%.3f,%s" % (model, value, split))
    return "\n".join(rows) + "\n"


def _scatter_sample():
    rows = ["k_eff,t_stat"]
    for i in range(1, 25):
        k = 0.25 * i
        rows.append("%.3f,%.3f" % (k, 1.05 + 0.42 * k + (0.21 if i % 3 == 0 else -0.13)))
    return "\n".join(rows) + "\n"


def _event_sample():
    rows = ["h,coef,lo,hi"]
    for h in range(-6, 13):
        c = 0.0 if h < 0 else 0.32 * (1 - math.exp(-0.5 * h))
        half = 0.12 + 0.01 * abs(h)
        rows.append("%d,%.4f,%.4f,%.4f" % (h, c, c - half, c + half))
    return "\n".join(rows) + "\n"


def _heatmap_sample():
    rows = ["horizon,method,ic"]
    for h in range(1, 7):
        for j, m in enumerate(("ols", "lasso", "rf", "ours")):
            rows.append("%d,%s,%.4f" % (h, m, 0.02 * h + 0.05 * j + 0.01 * ((h * (j + 2)) % 5)))
    return "\n".join(rows) + "\n"


SAMPLES = {
    "line-series": (_line_sample(), {"x": "x", "y": "y", "group": "series"}),
    "bar-grouped": (_bar_sample(), {"label": "model", "y": "score", "group": "split"}),
    "scatter-fit": (_scatter_sample(), {"x": "k_eff", "y": "t_stat"}),
    "forest-ci": ("factor,estimate,lo,hi\n"
                  "size,-0.21,-0.38,-0.04\n"
                  "value,0.34,0.12,0.56\n"
                  "momentum,0.18,-0.02,0.38\n"
                  "profitability,0.29,0.09,0.49\n"
                  "investment,-0.11,-0.27,0.05\n",
                  {"label": "factor", "y": "estimate", "lo": "lo", "hi": "hi"}),
    "event-study": (_event_sample(), {"x": "h", "y": "coef", "lo": "lo", "hi": "hi"}),
    "heatmap": (_heatmap_sample(), {"x": "horizon", "y": "method", "value": "ic"}),
}


def write_samples(directory) -> dict:
    """Write the built-in sample CSVs used by self-test and preview generation."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    out = {}
    for template_id, (text, columns) in SAMPLES.items():
        path = directory / ("sample-%s.csv" % template_id)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        out[template_id] = {"path": path, "columns": columns}
    return out


# ------------------------------------------------------------------ light QA probes

PDF_MEDIABOX = re.compile(rb"/MediaBox\s*\[\s*([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s*\]")
SVG_SIZE = re.compile(r'<svg[^>]*\swidth="([\d.]+)(pt|px|mm|in)?"[^>]*\sheight="([\d.]+)(pt|px|mm|in)?"')
SVG_TEXT = re.compile(r'<text[^>]*\sx="(-?[\d.]+)"[^>]*\sy="(-?[\d.]+)"')
_UNIT_MM = {"pt": 25.4 / 72.0, "px": 25.4 / 96.0, "mm": 1.0, "in": 25.4, None: 25.4 / 72.0}


def pdf_geometry(path):
    """((width_mm, height_mm), has_embedded_font) read straight from the bytes."""
    blob = Path(path).read_bytes()
    match = PDF_MEDIABOX.search(blob)
    size = None
    if match:
        x0, y0, x1, y1 = (float(match.group(i)) for i in range(1, 5))
        size = ((x1 - x0) * 25.4 / 72.0, (y1 - y0) * 25.4 / 72.0)
    return size, bool(re.search(rb"/FontFile[23]?", blob))


def svg_geometry(path):
    """((width_mm, height_mm), out_of_bounds_text_count) - a coarse overflow probe."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    match = SVG_SIZE.search(text)
    if not match:
        return None, 0
    unit_w = _UNIT_MM.get(match.group(2), _UNIT_MM[None])
    unit_h = _UNIT_MM.get(match.group(4), _UNIT_MM[None])
    raw_w, raw_h = float(match.group(1)), float(match.group(3))
    outside = 0
    for hit in SVG_TEXT.finditer(text):
        x, y = float(hit.group(1)), float(hit.group(2))
        if x < -1.0 or x > raw_w + 1.0 or y < -1.0 or y > raw_h + 1.0:
            outside += 1
    return (raw_w * unit_w, raw_h * unit_h), outside
