# Research map: the page and the film

This folder holds the source of two things built from the same React components:

- the research map page, which ships prebuilt as `scripts/research_map_app.html`;
- the promo film in the main README, rendered with [Remotion](https://www.remotion.dev).

Plugin users never need Node: `research_map.py render` fills the prebuilt template with
their project's data. Everything below is for working on the page or the film.

## The page

The page is built with React 19, React Flow 12, Radix primitives, Tailwind CSS 4, lucide icons
and d3-hierarchy. Vite and `vite-plugin-singlefile` compile it into one HTML file of about
0.9 MB with every script, style and font inlined, so a rendered map works offline and makes
no network requests.

```sh
npm install
npm run dev      # the page with the bundled demo data (?demo=en for English)
npm run build    # writes dist/index.html and copies it to scripts/research_map_app.html
```

Commit the rebuilt template together with the source change. Then run
`python scripts/research_map.py --self-test` from the repository root. It checks the
template contract: each placeholder appears exactly once, nothing is loaded from outside, deep
links work, and markup inside a story cannot break out of the data script.

`sample/demo-{zh,en}.json` are the models `research_map.py` builds for the demo project
(`examples/build_demo.py --lang zh|en`). Regenerate them when the demo story or the model
changes.

## The film

```sh
npx remotion studio                                         # preview and scrub
npx remotion render promo-en-wide out/promo-en-wide.mp4     # one cut
node video/stills.mjs out promo-zh-wide:100,700             # single frames as PNG
```

There are four cuts, `promo-{zh,en}-{wide,tall}` (16:9 and 9:16, 60 fps, about 26 s). There is
also `clip-receipt-{zh,en}`: the receipt printing on its own, with no captions, for editors.
The scenes live in `video/scenes/`, the on-screen words in `video/copy.ts`, and the product
labels come from `src/i18n.ts`, so the film and the page use the same vocabulary.

The Chinese headlines are set in Smiley Sans (得意黑, SIL Open Font License 1.1). The font
is not in the repository. Download `SmileySans-Oblique.ttf` from
[atelier-anchor/smiley-sans](https://github.com/atelier-anchor/smiley-sans) into
`video/public/fonts/`. Without it, the Chinese cut falls back to Noto Sans SC.

**Remotion's license.** Remotion is a dev dependency only; neither the plugin nor the map
page uses it. It is free for individuals and for companies of up to three people. Larger
companies need a [company license](https://www.remotion.dev/license) to render the film.
