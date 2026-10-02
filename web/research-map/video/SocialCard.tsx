// The repository's social preview (1280x640): the name on the left, the bottom of
// the overnight receipt on the right, with the count of what needs your call
// circled in red pen. Upload it under the repository's Settings > Social preview.
import { useRef } from "react";
import { AbsoluteFill } from "remotion";
import { Receipt } from "@/components/Receipt";
import { TooltipProvider } from "@/components/ui";
import { LangContext, type Lang } from "@/i18n";
import { COPY } from "./copy";
import { FontGate, Grain, useMeasure } from "./kit";
import { MODELS } from "./Promo";
import { penLoop } from "./scenes/ReceiptScene";

const S = 1.45; // receipt scale: the whole width and the red pen circle stay inside the card
const RW = 22 * 16;

function Card({ lang }: { lang: Lang }) {
  const c = COPY[lang];
  const ref = useRef<HTMLDivElement>(null);
  const m = useMeasure(() => {
    const section = ref.current?.querySelector<HTMLElement>("section");
    const badge = section?.querySelector<HTMLElement>(".bg-redpen");
    if (!section || !badge) return null;
    let y = 0;
    let x = 0;
    for (let n: HTMLElement | null = badge; n && n !== section; n = n.offsetParent as HTMLElement | null) {
      x += n.offsetLeft;
      y += n.offsetTop;
    }
    return { x, y, w: badge.offsetWidth, h: badge.offsetHeight };
  });
  // the "needs your call" header sits near the top of the card; the rest runs to the torn edge
  const top = m ? 96 - (m.y - 18) * S : 0;
  return (
    <>
      <div className="absolute inset-y-0 flex flex-col justify-center" style={{ left: 72, width: 600 }}>
        <span
          className="inline-flex items-center justify-center bg-ink font-display font-extrabold text-bg"
          style={{ width: 76, height: 76, borderRadius: 20, fontSize: 34, fontStretch: "78%" }}
          aria-hidden
        >
          ar
        </span>
        <div
          className="mt-6 font-display font-extrabold text-ink"
          style={{ fontSize: 76, lineHeight: 0.98, letterSpacing: "-0.025em", fontStretch: "78%" }}
        >
          <div>{c.name[0]}</div>
          <div>
            {c.name[1].replace(/!$/, "")}
            <span className="text-redpen">!</span>
          </div>
        </div>
        <p className="mt-6 font-sans text-ink-2" style={{ fontSize: 28, lineHeight: 1.35, textWrap: "balance" }}>
          {c.tagline}
        </p>
        <p className="mt-5 font-mono uppercase tracking-[0.16em] text-ink-3" style={{ fontSize: 17 }}>
          {c.meta}
        </p>
      </div>
      <div
        className="absolute inset-y-0 overflow-hidden"
        style={{ left: 700, right: 0, maskImage: "linear-gradient(to bottom, transparent 0, #000 90px)" }}
      >
        <div
          ref={ref}
          className="absolute"
          style={{
            left: 280 - RW / 2, // scaled about its top centre, so the centre sits at 280
            top,
            width: RW,
            transform: `scale(${S}) rotate(-3deg)`,
            transformOrigin: "50% 0",
            filter: "drop-shadow(0 18px 30px rgb(22 26 34 / 0.14))",
          }}
        >
          <Receipt model={MODELS[lang]} className="w-[22rem]" />
          {m && (
            <svg className="pointer-events-none absolute left-0 top-0 overflow-visible" width={RW} height={10} aria-hidden>
              <path
                d={penLoop(m.x + m.w / 2, m.y + m.h / 2, m.w / 2 + 15, m.h / 2 + 10)}
                fill="none"
                stroke="var(--redpen)"
                strokeWidth={2.4}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          )}
        </div>
      </div>
    </>
  );
}

export function SocialCard({ lang }: { lang: Lang }) {
  return (
    <LangContext.Provider value={lang}>
      <TooltipProvider>
        <AbsoluteFill className="video-root bg-bg" lang={lang === "zh" ? "zh-CN" : "en"}>
          <FontGate>
            <Card lang={lang} />
            <Grain opacity={0.045} />
          </FontGate>
        </AbsoluteFill>
      </TooltipProvider>
    </LangContext.Provider>
  );
}
