// The receipt's own sign-off, then the name. Same words as the receipt footer:
// the video ends where the product ends.
import { AbsoluteFill, useCurrentFrame } from "remotion";
import type { MapModel } from "@/data";
import type { Lang } from "@/i18n";
import { Barcode } from "@/components/Receipt";
import { COPY } from "../copy";
import { Appear, Headline, IN_OUT, tween, useLayout } from "../kit";

export const OUTRO_LOCKUP = 150;

function Lockup({ lang, big }: { lang: Lang; big: boolean }) {
  const c = COPY[lang];
  const L = OUTRO_LOCKUP;
  return (
    <div>
      <Appear at={L}>
        <span
          className="inline-flex items-center justify-center bg-ink font-display font-extrabold text-bg"
          style={{ width: big ? 116 : 104, height: big ? 116 : 104, borderRadius: 28, fontSize: big ? 50 : 46, fontStretch: "78%" }}
          aria-hidden
        >
          ar
        </span>
      </Appear>
      <Headline
        lines={[c.name[0], <>{c.name[1].replace(/!$/, "")}<span className="text-redpen">!</span></>]}
        enter={L + 6}
        lang="en"
        size={big ? 92 : 80}
        className="mt-7 text-ink"
      />
      <Appear at={L + 22} className="mt-7">
        <p className={lang === "zh" ? "font-sans font-medium text-ink-2" : "font-sans text-ink-2"} style={{ fontSize: big ? 38 : 32, lineHeight: 1.35 }}>
          {c.tagline}
        </p>
      </Appear>
      <Appear at={L + 34} className="mt-9">
        <div className="flex flex-wrap items-center gap-3" style={{ fontSize: big ? 30 : 26 }}>
          {c.ctaLabel && <span className="font-sans font-semibold text-ink">{c.ctaLabel}</span>}
          <span className="rounded-xl border-2 border-ink bg-surface px-4 py-2 font-mono font-medium text-ink">{c.cta}</span>
        </div>
      </Appear>
      <Appear at={L + 44} className="mt-6">
        <p className="font-mono uppercase tracking-[0.16em] text-ink-3" style={{ fontSize: big ? 22 : 20 }}>
          {c.meta}
        </p>
      </Appear>
    </div>
  );
}

export function Outro({ model, lang }: { model: MapModel; lang: Lang }) {
  const f = useCurrentFrame();
  const { wide } = useLayout();
  const c = COPY[lang];
  const wipe = tween(f, [0, 30], [0, 1], IN_OUT);
  const bars = tween(f, [62, 104], [0, 1], IN_OUT);
  const rule = tween(f, [OUTRO_LOCKUP - 14, OUTRO_LOCKUP + 20], [0, 1], IN_OUT);

  const signOff = (size: number, barWidth: number) => (
    <div>
      <Headline lines={c.outro} enter={24} lang={lang} size={size} className="text-ink" />
      <div className="mt-12" style={{ width: barWidth }}>
        <div style={{ clipPath: `inset(0 ${(1 - bars) * 100}% 0 0)` }}>
          <Barcode id={model.head} />
        </div>
        <Appear at={92} dy={10}>
          <p className="mt-4 font-mono uppercase tracking-[0.34em] text-ink-2" style={{ fontSize: wide ? 24 : 28 }}>
            {c.thanks}
          </p>
        </Appear>
      </div>
    </div>
  );

  return (
    <AbsoluteFill className="bg-bg text-ink" style={{ clipPath: `inset(${(1 - wipe) * 100}% 0 0 0)` }}>
      {wide ? (
        <>
          <div className="absolute inset-y-0 flex flex-col justify-center" style={{ left: 120, width: 760 }}>
            {signOff(lang === "zh" ? 128 : 92, 380)}
          </div>
          <div
            className="absolute border-l-[3px] border-dashed border-line-strong"
            style={{ left: 958, top: 200, height: 680, transform: `scaleY(${rule})`, transformOrigin: "50% 0" }}
          />
          <div className="absolute inset-y-0 flex flex-col justify-center" style={{ left: 1040, right: 100 }}>
            <Lockup lang={lang} big={false} />
          </div>
        </>
      ) : (
        <>
          <div className="absolute" style={{ left: 72, right: 72, top: 210 }}>
            {signOff(lang === "zh" ? 150 : 104, 520)}
          </div>
          <div
            className="absolute border-t-[3px] border-dashed border-line-strong"
            style={{ left: 72, right: 72, top: 900, transform: `scaleX(${rule})`, transformOrigin: "0 50%" }}
          />
          <div className="absolute" style={{ left: 72, right: 72, top: 980 }}>
            <Lockup lang={lang} big />
          </div>
        </>
      )}
    </AbsoluteFill>
  );
}
