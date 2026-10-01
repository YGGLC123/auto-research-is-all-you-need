# Third-party notices

This project is MIT-licensed (see [LICENSE](LICENSE)). It includes, adapts, or indexes the
third-party material below. Each item keeps its own license.

## ARIS — Auto-claude-code-research-in-sleep

- Upstream: <https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep>
- What we use:
  - `scripts/evidence_check.py` is copied from ARIS without functional changes; only a
    provenance comment was added.
  - The rules in [docs/claim-audit-protocol.md](docs/claim-audit-protocol.md) and
    [docs/rebuttal-gates.md](docs/rebuttal-gates.md) are adapted from ARIS's
    `paper-claim-audit`, `result-to-claim` and `rebuttal` skills.
- License: MIT

```
MIT License

Copyright (c) 2026 wanshuiyin

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Caveman — `verify-and-stop` skill

- Upstream: <https://github.com/JuliusBrussee/caveman>
- What we use: `skills/verify-and-stop/` is the upstream skill. The plugin's verification policy
  loads it as its "verify, then stop" rule.
- License: MIT, Copyright (c) 2026 Julius Brussee. The full text ships next to the skill in
  [skills/verify-and-stop/LICENSE](skills/verify-and-stop/LICENSE).

## Bioicons

- Upstream: <https://github.com/duerrsimon/bioicons> (site: <https://bioicons.com>)
- What we ship: `library/components/bioicons/index.json` is **metadata only**: icon names,
  categories, sizes, authors, licenses and source URLs, transcribed from upstream. No icon
  artwork is included. `scripts/component_index.py` fetches the icons from upstream when you
  rebuild the index.
- License: set per icon by its author (CC0-1.0, CC-BY-3.0, CC-BY-4.0, CC-BY-SA-3.0/4.0, MIT,
  BSD-3-Clause). The index records each icon's license and author. Before you ship a figure,
  credit the icon as its license requires. An icon recorded as `UNKNOWN` is blocked from
  shipping until its license is resolved at the source.

## Optional tools (not bundled)

When these tools are installed, the plugin detects and uses them. None of them is included
in this repository, and each keeps its own license: matplotlib, SciencePlots, cmcrameri
(Scientific colour maps), vl-convert, Graphviz, Inkscape, PlotNeuralNet, latexmk.

The research map exports to the open [markmap](https://markmap.js.org/),
[JSON Canvas](https://jsoncanvas.org/) and [XMind](https://xmind.app/) file formats. No code
from those projects is included.
