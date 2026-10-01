// Render chosen frames of the promo as PNGs, with one bundle and one browser.
//   node video/stills.mjs <outDir> promo-zh-wide:100,400 promo-en-tall:900
// Run from web/research-map (Node 22.18+ loads the .ts override directly).
import path from "node:path";
import { bundle } from "@remotion/bundler";
import { openBrowser, renderStill, selectComposition } from "@remotion/renderer";
import { webpackOverride } from "./webpack-override.ts";

const [outDir, ...jobs] = process.argv.slice(2);
if (!outDir || jobs.length === 0) {
  console.error("usage: node video/stills.mjs <outDir> <compositionId>:<frame>[,<frame>...] ...");
  process.exit(2);
}

const serveUrl = await bundle({
  entryPoint: path.resolve("video/index.ts"),
  publicDir: path.resolve("video/public"),
  webpackOverride,
});
const browser = await openBrowser("chrome");
try {
  for (const job of jobs) {
    const [id, frames] = job.split(":");
    const composition = await selectComposition({ serveUrl, id, puppeteerInstance: browser });
    for (const frame of frames.split(",").map(Number)) {
      const output = path.join(outDir, `${id}-${String(frame).padStart(4, "0")}.png`);
      await renderStill({ composition, serveUrl, frame, output, imageFormat: "png", puppeteerInstance: browser });
      console.log(output);
    }
  }
} finally {
  await browser.close({ silent: true });
}
