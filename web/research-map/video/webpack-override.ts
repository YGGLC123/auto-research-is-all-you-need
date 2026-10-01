// Shared by remotion.config.ts (CLI and studio) and stills.mjs (scripted renders).
import path from "node:path";
import type { WebpackOverrideFn } from "@remotion/bundler";
import { enableTailwind } from "@remotion/tailwind-v4";

export const webpackOverride: WebpackOverrideFn = (current) => {
  const withTailwind = enableTailwind(current);
  return {
    ...withTailwind,
    resolve: {
      ...withTailwind.resolve,
      alias: { ...(withTailwind.resolve?.alias ?? {}), "@": path.resolve(process.cwd(), "src") },
    },
  };
};
