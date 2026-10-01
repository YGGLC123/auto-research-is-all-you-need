# Component library (Route B)

Local, license-aware indexes of external scientific-icon collections, plus the code
that places indexed components into a figure. Contract:
[../../docs/figure-engineering-contract.md](../../docs/figure-engineering-contract.md) §6.

Tool: [`scripts/component_index.py`](../../scripts/component_index.py) — stdlib only.

## What is tracked and what is not

| Path | Tracked | Why |
|---|---|---|
| `<source>/index.json` | yes | the contract artifact: one row per component with its license |
| `<source>/README.md` | yes | how to rebuild or import that source |
| `attribution.md` | yes (when generated for a figure) | the credit the paper must carry |
| `<source>/repo/` | **no** (git-ignored) | upstream clone; rebuild with one command |
| `<source>/svg/` | **no** (git-ignored) | manually imported svg bodies |

An index row records `path` relative to the plugin root. Because the bodies are
git-ignored, `assemble` on a fresh checkout fails with an explicit "rebuild the source"
message rather than producing a broken figure.

## Sources

| source | acquisition | count | license model |
|---|---|---|---|
| `bioicons` | `git clone --depth 1 https://github.com/duerrsimon/bioicons` | 2830 | mixed, per icon (CC0 / CC-BY / CC-BY-SA / MIT / BSD) |
| `reactome` | manual import | 0 (not imported) | source states CC-BY-4.0 for the collection |
| `scidraw` | manual import | 0 (not imported) | source states CC-BY-4.0; per item DOI + creator |

Reactome and SciDraw are **registered but empty**. Neither has a repository to clone,
so nothing is indexed until a human downloads the files; the index is not populated with
invented rows. `<source>/README.md` carries the import steps, and once the files are on
disk the generic folder indexer takes over:

```
component_index.py index build --source dir --name reactome --repo library/components/reactome/svg
```

## Rebuild Bioicons

```
git clone --depth 1 https://github.com/duerrsimon/bioicons library/components/bioicons/repo
py scripts/component_index.py index build --source bioicons
```

`index build --source bioicons` clones by itself if `repo/` is absent. The indexed commit
is recorded in `index.json` under `repo.commit`, so an index can be reproduced exactly.

## License discipline

Licenses are **transcribed, never inferred**. For Bioicons the authority is the upstream
`static/icons/icons.json` row for that icon. A file gets `license: "UNKNOWN"` when

- it has no metadata row upstream, or
- its license token is not in the transcription map, or
- the metadata license and the directory it sits in disagree.

`UNKNOWN` is a blocking state: `index attribution` prints a BLOCKING banner and
`assemble` prints a warning naming every UNKNOWN component. Resolve at the source URL
before the figure ships.

## Everyday commands

```
component_index.py index search --tags neural,network --license-allow CC0,MIT,CC-BY
component_index.py index stats
component_index.py assemble --template pipeline-lr \
    --slots A=bioicons:<id> B=bioicons:<id> C=bioicons:<id> \
    --labels A="Imaging" B="GPU cluster" C="Neural estimator" \
    --title "…" --attribution attribution.md --out figure.svg
component_index.py index attribution --used bioicons:<id>,bioicons:<id> --figure F-101
component_index.py self-test
```

Assembly output is a single self-contained SVG: components embedded as nested `<svg>`
(ids namespaced per slot so `url(#…)` references cannot collide), connectors drawn in
code at 1 px, and text kept in its own `layer-text` group. Any component that pulls an
external resource is refused rather than embedded.
