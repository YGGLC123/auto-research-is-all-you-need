import { AbsoluteFill, Sequence } from "remotion";
import type { MapModel } from "@/data";
import { LangContext, type Lang } from "@/i18n";
import { TooltipProvider } from "@/components/ui";
import en from "../sample/demo-en.json";
import zh from "../sample/demo-zh.json";
import { FontGate, Grain } from "./kit";
import { MAP_EXIT, MapScene } from "./scenes/MapScene";
import { Night } from "./scenes/Night";
import { Outro } from "./scenes/Outro";
import { RECEIPT_EXIT, ReceiptScene } from "./scenes/ReceiptScene";

export const MODELS = { zh: zh as unknown as MapModel, en: en as unknown as MapModel };

// Scenes overlap by 30 frames: the next one wipes or pushes in over the last.
const OVERLAP = 30;
const RECEIPT_FROM = 150;
const MAP_FROM = RECEIPT_FROM + RECEIPT_EXIT;
const OUTRO_FROM = MAP_FROM + MAP_EXIT;
export const SCENES = {
  night: { from: 0, duration: RECEIPT_FROM + OVERLAP },
  receipt: { from: RECEIPT_FROM, duration: RECEIPT_EXIT + OVERLAP },
  map: { from: MAP_FROM, duration: MAP_EXIT + OVERLAP },
  outro: { from: OUTRO_FROM, duration: 420 },
};
export const TOTAL = SCENES.outro.from + SCENES.outro.duration; // 1572 frames, about 26 s at 60 fps
export const FPS = 60;

/** The receipt printing on its own, for editors cutting their own video. */
export const RECEIPT_CLIP = 400;
export function ReceiptClip({ lang }: { lang: Lang }) {
  return (
    <LangContext.Provider value={lang}>
      <TooltipProvider>
        <AbsoluteFill className="video-root bg-bg" lang={lang === "zh" ? "zh-CN" : "en"}>
          <FontGate>
            <ReceiptScene model={MODELS[lang]} lang={lang} clean />
            <Grain opacity={0.045} />
          </FontGate>
        </AbsoluteFill>
      </TooltipProvider>
    </LangContext.Provider>
  );
}

export function Promo({ lang }: { lang: Lang }) {
  const model = MODELS[lang];
  return (
    <LangContext.Provider value={lang}>
      <TooltipProvider>
        <AbsoluteFill className="video-root bg-bg" lang={lang === "zh" ? "zh-CN" : "en"}>
          <FontGate>
            <Sequence name="Night" from={SCENES.night.from} durationInFrames={SCENES.night.duration}>
              <Night model={model} lang={lang} />
            </Sequence>
            <Sequence name="Receipt" from={SCENES.receipt.from} durationInFrames={SCENES.receipt.duration}>
              <ReceiptScene model={model} lang={lang} />
            </Sequence>
            <Sequence name="Map" from={SCENES.map.from} durationInFrames={SCENES.map.duration}>
              <MapScene model={model} lang={lang} />
            </Sequence>
            <Sequence name="Outro" from={SCENES.outro.from} durationInFrames={SCENES.outro.duration}>
              <Outro model={model} lang={lang} />
            </Sequence>
            <Grain opacity={0.045} />
          </FontGate>
        </AbsoluteFill>
      </TooltipProvider>
    </LangContext.Provider>
  );
}
