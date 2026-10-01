# Bioicons — indexed component source

- Upstream: <https://github.com/duerrsimon/bioicons> (site: <https://bioicons.com>)
- Acquisition: shallow clone into `repo/` (git-ignored)
- Indexed: **2830** components across **38** categories
- License model: mixed, recorded per icon

## Rebuild

```
git clone --depth 1 https://github.com/duerrsimon/bioicons library/components/bioicons/repo
py scripts/component_index.py index build --source bioicons
```

The build clones on its own when `repo/` is missing. `index.json` records
`repo.commit`, so an index can be reproduced against the exact upstream state.

## Where the license comes from

Upstream stores the icon list in `repo/static/icons/icons.json`
(`{name, category, license, author}` per icon) and lays the files out as
`static/icons/<license>/<category>/<author>/<name>.svg`. The indexer transcribes the
metadata row and cross-checks it against the directory. Author matching normalizes
spaces to underscores, because the on-disk path segment is the author name with spaces
replaced — the same field, written two ways.

A component is recorded `UNKNOWN` when the metadata row is absent, its license token is
outside the transcription map, or the row and the directory disagree. Nothing is guessed.

## Current distribution

| license | count |
|---|---|
| CC-BY-3.0 | 1374 |
| CC-BY-4.0 | 880 |
| CC0-1.0 | 463 |
| MIT | 39 |
| CC-BY-SA-4.0 | 35 |
| UNKNOWN | 32 |
| CC-BY-SA-3.0 | 4 |
| BSD-3-Clause | 3 |

The 32 UNKNOWN rows are 31 files present on disk with no upstream metadata row (mostly
`.min` / `.drawio` filename variants) and 1 genuine conflict where the metadata says
CC-BY-SA-4.0 and the directory says CC-BY-4.0. Four CC-BY-4.0 files (DBCLS anatomy and
virus drawings) do not parse as XML with the stdlib parser; they are indexed with
`svg_parse_ok: false` and null dimensions, and `assemble` refuses them rather than
emitting a broken figure.

**CC-BY-SA components are contagious**: using one obliges the derivative figure to carry
the same license. `index search --license-allow CC0,MIT,CC-BY` keeps them out by default.
