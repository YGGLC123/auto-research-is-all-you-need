import { copyFileSync } from "node:fs";
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, type Plugin } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// The build is one self-contained HTML file. research_map.py fills its three
// placeholders (language, title, model JSON) and writes the page; nothing is
// fetched at view time.
const TEMPLATE = resolve(import.meta.dirname, "../../scripts/research_map_app.html");

function publishTemplate(): Plugin {
  return {
    name: "publish-template",
    apply: "build",
    closeBundle() {
      copyFileSync(resolve(import.meta.dirname, "dist/index.html"), TEMPLATE);
      console.log(`template -> ${TEMPLATE}`);
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), viteSingleFile(), publishTemplate()],
  resolve: { alias: { "@": resolve(import.meta.dirname, "src") } },
  build: {
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    chunkSizeWarningLimit: 4000,
  },
});
