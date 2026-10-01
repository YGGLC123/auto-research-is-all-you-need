// Remotion renders the promo video from the same React components as the map page.
// Run from web/research-map:  npx remotion studio   |   npx remotion render <id> out/<id>.mp4
import { Config } from "@remotion/cli/config";
import { webpackOverride } from "./video/webpack-override";

Config.setEntryPoint("./video/index.ts");
Config.setPublicDir("./video/public");
// PNG frames and a low CRF keep small UI text crisp after H.264 encoding.
Config.setVideoImageFormat("png");
Config.setCodec("h264");
Config.setCrf(16);
Config.setPixelFormat("yuv420p");
Config.setOverwriteOutput(true);
Config.overrideWebpackConfig(webpackOverride);
