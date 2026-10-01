# Reactome Icon Library -- manual import required

Registered as a component source, **not indexed**: `count = 0`.
No entry is fabricated; the index stays empty until a human imports the files.

## Why this source is not cloned

No public git mirror of the icon set: it is served as a browsable web gallery with per-icon downloads behind the site UI. There is nothing to clone offline, so nothing is indexed until a human imports it.

## Import steps

1. Open https://reactome.org/icon-lib and download the categories you need.
2. Unpack the SVGs into library/components/reactome/svg/ keeping the category folders (that path is git-ignored).
3. Write library/components/reactome/svg/manifest.json mapping each relative svg path to {"license": ..., "author": ..., "source_url": ...} copied from the download page. Anything you cannot read stays out of the manifest and is indexed as UNKNOWN rather than guessed.
4. Run: component_index.py index build --source dir --name reactome --repo library/components/reactome/svg

Upstream states **CC-BY-4.0** for the collection (roughly 1600 items), but the per-file license still has to be recorded at import time.
