# SciDraw -- manual import required

Registered as a component source, **not indexed**: `count = 0`.
No entry is fabricated; the index stays empty until a human imports the files.

## Why this source is not cloned

Drawings are uploaded and downloaded one item at a time, each with its own creator and DOI; there is no repository to clone and no bulk endpoint, so an offline index cannot be built without a human download.

## Import steps

1. Download the drawings you need from https://scidraw.io (each item page carries its own creator, DOI and license).
2. Put the SVGs in library/components/scidraw/svg/ (git-ignored).
3. Write library/components/scidraw/svg/manifest.json with, per file, {"license", "author", "source_url"} copied from the item page (the DOI link is the source_url).
4. Run: component_index.py index build --source dir --name scidraw --repo library/components/scidraw/svg

Upstream states **CC-BY-4.0** for the collection (roughly 1500 items), but the per-file license still has to be recorded at import time.
