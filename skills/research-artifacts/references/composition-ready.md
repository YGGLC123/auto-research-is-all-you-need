# Composition-Ready Research Figures

Use this reference when a figure must behave as a reusable kit rather than merely remain editable. `D3 composition-ready` is format-independent; verifiable native targets are Draw.io, structured SVG, and PowerPoint with real OOXML groups. Figma may author the design, but a Figma-only cloud file remains D2 until it is handed off to a locally inspectable D3 master.

## Contents

1. D3 contract
2. Component hierarchy
3. Assembly modes
4. Component and connector ownership
5. Backend rules
6. Phase-1 planning contract
7. Phase-2 delivery contract
8. Manual edit and round-trip gate

## 1. D3 contract

`D2 native-editable` means individual text, nodes, and edges can be edited. `D3 composition-ready` additionally guarantees that meaningful modules can be separated, copied, rearranged, replaced, and recombined without reconstructing the figure.

A D3 delivery must provide:

- a native source in Draw.io, Figma, named-group SVG, or PowerPoint;
- nested semantic groups, not a flat pile of objects;
- an assembly, a component kit, or both, as frozen in Phase 1;
- stable component and native-object IDs;
- a structure snapshot and composition manifest;
- a real ungroup/edit/move/copy/reassemble/export test;
- a preview rendered after the edit round trip;
- no whole-figure or whole-slide flattening.

Do not use D3 for every plot. Use it when the reader-facing object has reusable modules, panels, subassemblies, or a likely need for human recomposition.

## 2. Component hierarchy

Keep two or three meaningful ungroup operations between the complete assembly and primitives:

```text
assembly
├─ optional panel
│  ├─ module
│  │  ├─ shape
│  │  ├─ text
│  │  └─ internal connector
│  └─ module
├─ module
└─ interconnect layer
```

Use `panel` for a caption-level subfigure and `module` for a reusable visual component. Never substitute `panel_plan` for a component tree: one panel may contain several modules, and one reusable module may appear in several panels.

One ungroup operation on the assembly must expose complete top-level components. Ungrouping a module must expose its primitives. Avoid wrapper groups that have no semantic or editing purpose.

## 3. Assembly modes

- `assembly`: deliver the complete grouped figure; one ungroup exposes its modules.
- `component-kit`: deliver a component board/library from which a user can assemble variants.
- `both`: deliver the finished assembly and the reusable component board. Prefer this for method families, architecture alternatives, talk slides, and long-lived research programs.

The component board should include each reusable module once in a neutral state, show ports or attachment zones, and keep labels editable. It is a working library, not a decorative legend.

## 4. Component and connector ownership

- A module owns its internal shapes, labels, icons, ports, and internal connectors.
- Cross-module connectors belong to a separate `interconnect` group behind the modules.
- Name ports as stable interfaces such as `encoder.out` and `risk_head.in` when reconnection matters.
- Moving or copying one module must not silently duplicate unrelated cross-module connectors.
- Keep exact plots, photos, scans, or generated E0 illustrations as replaceable atomic assets when native primitives are impractical. Never flatten the full assembly.
- Record every retained flattened asset, its reason, scope, and replacement path in the composition manifest.

## 5. Backend rules

### Draw.io

Use explicit nested `group` cells with stable IDs and parent relationships. Prefer an uncompressed `.drawio` source for structural inspection. Put the finished assembly and component board on named pages or in clearly separated root groups. Export a matching PDF/SVG plus PNG preview.

### Figma

Use named frames/components and instances where reuse is real. Keep assembly frames, component-set frames, modules, primitives, and interconnects separate. Export a local PDF/SVG snapshot and a local node-tree JSON/structure snapshot; a cloud file identity alone does not prove the grouping contract. Copy `assets/figma-source-identity.template.json` to the delivered `.figma` identity path for provenance, then rebuild/export to structured SVG or Draw.io and test that local master. Figma-only delivery is D2 because the validator cannot independently inspect the remote node tree.

### Structured SVG

Use nested, named `<g id="...">` groups. Keep labels as text when font portability permits. Use `<symbol>/<use>` only when the target editor preserves practical editability. Reject a single full-canvas `<image>` as D3.

### PowerPoint

Use native shapes, text, charts, tables, and real nested PowerPoint groups. Keep connectors behind nodes and attached to named endpoints where supported. Put the component board on a separate slide or clearly separated canvas. For generated decks, preserve the plain `.mjs` `@oai/artifact-tool` builder as reproducible source and render every slide for overlap/overflow QA. Do not use `python-pptx` for this route. If the active PowerPoint authoring backend cannot emit or preserve actual nested groups, do not label the PPTX D3; use Draw.io/Figma/structured SVG as the D3 source and treat PPTX as a D2 presentation handoff.

## 6. Phase-1 planning contract

Set `editability: "D3"`, require a PNG preview, and add a `composition_plan`:

```json
{
  "mode": "both",
  "target_editors": ["drawio"],
  "hierarchy": ["assembly", "module", "primitive"],
  "min_top_level_components": 3,
  "max_ungroup_steps_to_primitives": 2,
  "component_board_required": true,
  "top_level_components": [
    {"component_id": "F1.input", "job": "encode inputs", "reusable": true},
    {"component_id": "F1.model", "job": "show the learned mechanism", "reusable": true},
    {"component_id": "F1.output", "job": "show decision outputs", "reusable": true}
  ],
  "allowed_flattening": ["exact plot panels may remain replaceable SVG assets"],
  "locked_elements": ["module names and interface direction"]
}
```

The target editor determines the required primary native format: Draw.io `.drawio`, Figma source identity `.figma`, SVG editor `.svg`, or PowerPoint `.pptx`.

## 7. Phase-2 delivery contract

Add `composition_manifest_path` to the delivery entry. Create it from `assets/composition-manifest.template.json`, and create the editor-readback tree from `assets/structure-snapshot.template.json`. It must record:

- native source and component-board paths;
- a complete local structure snapshot bound to the native source path and SHA-256, with every native group/shape/image ID, its actual parent, kind, component ID, visibility, and bounds;
- assembly root, top-level component order, and full parent-child hierarchy;
- native object IDs and edit/move/reuse flags;
- layer order, any interconnect group, and stable named ports;
- replaceable flattened assets whose `scope` and exact frozen `allowance` are explicit;
- the editor and operations used in the manual edit test, edited native-source path/hash, capture run, completion time, fresh preview path/hash, and their deterministic round-trip binding hash.
- the frozen labels/interfaces/other locked elements rechecked after the edit round trip.

List native sources and component boards in `editable_source_paths`. List the edit-test report in `qa_report_paths`. Include the manifest, snapshot, edit-test report, native sources, and flattened assets in semantic review and the final delivery lock.

Compute `roundtrip_binding_sha256` as SHA-256 over `edited_native_source_sha256|roundtrip_preview_sha256|capture_run_id|completed_at`. The preview must be written after the edited native source, and the QA report after the preview.

## 8. Manual edit and round-trip gate

Test the actual native source in the declared editor:

1. Ungroup the assembly once and confirm the frozen top-level components appear intact.
2. Ungroup one module until its primitives appear within the declared maximum depth.
3. Edit one label without rebuilding the module.
4. Move and copy a top-level component without damaging siblings.
5. When a component board is required, copy one module from it back into the assembly.
6. Reassemble the copied/moved modules and repair only declared interconnects.
7. Export again and compare the preview for clipping, font substitution, connector drift, and semantic changes.

Record exact pass/fail observations. A file that merely opens, or a source containing thousands of independently editable but ungrouped objects, does not pass D3.
