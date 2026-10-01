# Visual-System Tokens

Use these as fallbacks and semantic roles, not a fixed brand skin. Derive physical dimensions, font compatibility, and venue limits from the active manuscript first.

## Contents

- Canvas and dimensions
- Typography, stroke, and geometry
- Color and epistemic encodings
- Composition layers and component naming
- Figure-type exceptions
- Naming, export, and accessibility

## Canvas and dimensions

```yaml
canvas:
  background: "#FFFFFF"
  single_column_width: manuscript_columnwidth
  double_column_width: manuscript_textwidth
  outer_margin: 4pt
```

Do not assume every venue uses 85/180 mm. Read `\columnwidth` and `\textwidth` or render a measurement box from the active template.

## Typography at final placement size

```yaml
type:
  family_text: manuscript-compatible sans or venue font
  family_math: manuscript math font
  ordinary_label: 7-9pt
  small_label: venue minimum, never an automatic shrink target
  panel_label: 9-11pt semibold
  title_inside_plot: discouraged
```

Use a controlled hierarchy; do not force every text element to one size. Shorten text, resize the composition, or split panels before shrinking below the venue minimum.

## Stroke and geometry

```yaml
stroke:
  normal: 0.65pt
  emphasis: 1.1pt
  grid: 0.4pt
  minimum: 0.5pt
geometry:
  spacing_unit: 4pt
  node_padding: 8pt
  group_gap: 16-24pt
  corner_radius: restrained_and_consistent
effects:
  shadow: none_by_default
  gradient: none_by_default
  bevel_or_3d: forbidden_for_evidence
```

## Semantic color roles

```yaml
color:
  ink: "#1F2937"
  muted: "#6B7280"
  surface: "#F3F4F6"
  primary: "#0072B2"
  comparator: "#6B7280"
  auxiliary: "#56B4E9"
  beneficial: "#009E73"
  adverse: "#D55E00"
  accent: "#CC79A7"
  uncertainty: "#8E9AAF"
```

- Keep the same semantic role across the paper.
- Baseline is neutral by default, not automatically red.
- Yellow is not a default line/text color on white.
- Add shape, line style, marker, hatching, or direct label redundancy.
- Use sequential/diverging perceptual maps for continuous data; disclose limits and transforms.

## Epistemic and relation encodings

Suggested fallbacks:

- observed/measured: solid outline and direct source label;
- proposed method: primary color, not oversized area;
- comparator/prior method: neutral gray;
- latent/unavailable: dashed outline or patterned surface;
- uncertainty/set: translucent band/region with boundary line;
- failed/refuted/adverse: adverse accent plus symbol/line redundancy;
- data flow: solid arrow;
- temporal order: thin timeline connector;
- logical dependence: labeled formal arrow;
- causal claim: reserved arrow style used only with causal evidence;
- optional/fallback path: dashed connector;
- version/revision: branched or double-line relation with version label.

Never let layout proximity or color imply a relation absent from the legend and source contract.

## Composition layers and component naming

For D3 figures, use a small stable layer stack such as `background -> interconnect -> modules -> labels -> annotations`. Keep cross-module connectors behind module shapes. Name editable objects with semantic IDs rather than tool defaults:

```yaml
composition:
  assembly: F1.assembly
  module: F1.encoder
  primitive: F1.encoder.label
  port: F1.encoder.out
  interconnect: F1.interconnect
  component_board: F1.components
  maximum_ungroup_depth: 2-3
```

Group by editing intent. An assembly group exposes complete modules; a module group exposes primitives. Do not add grouping layers merely to organize the layer panel. Keep reusable modules neutral enough to copy between variants while preserving semantic tokens.

## Per-type exceptions

- Plots may use direct data encodings and perceptual colormaps rather than box fills.
- Architecture/workflow usually benefits from orthogonal connectors.
- Networks and hypergraphs may need curves, bundles, or translucent hyperedge regions; orthogonal routing is not mandatory.
- Theory figures may use manuscript math typography, set contours, and formal line labels.
- Domain schematics may use field-standard activation/inhibition or circuit symbols.
- Graphical abstracts may use limited illustration, depth, or gradients, but they remain orientation rather than evidence.
- Mixed panels inherit tokens but preserve each panel's valid scientific grammar.

## Naming and export

- Stable figure ID in sources, exports, captions, and manifest;
- primary vector/editable source plus lightweight preview;
- version suffix only for semantic revisions or retained alternatives, not every minor render;
- prompts and generated drafts named separately from verified final assets.
