// Morning. The night's work prints out of a receipt printer, line by line; then
// the camera settles on what needs your call and a red pen circles the count.
import { ReceiptText } from "lucide-react";
import { useRef } from "react";
import { AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import type { MapModel } from "@/data";
import { translate, type Lang } from "@/i18n";
import { needsList, Receipt } from "@/components/Receipt";
import { COPY } from "../copy";
import { Appear, Headline, IN_OUT, InkStamp, Pointer, tween, useLayout, useMeasure, usePointer } from "../kit";

const PRINT: [number, number] = [34, 262];
const TEAR = 268;
// then the pointer clicks the benched line, as a reader would, and the map takes over
const POINT: [number, number] = [372, 404];
const CLICK = 410;
export const RECEIPT_EXIT = 432; // the map pushes in from here

type Box = { x: number; y: number; w: number; h: number };
type Measured = { h: number; needsTop: number; needsBottom: number; badge: Box; row: Box };

/** A pen loop that overshoots its start, the way a hand circles a number. */
export function penLoop(cx: number, cy: number, rx: number, ry: number): string {
  const pts: string[] = [];
  const n = 56;
  for (let i = 0; i <= n; i++) {
    const a = (-0.62 + (i / n) * 2.18) * Math.PI;
    const wobble = 1 + 0.05 * Math.sin(i * 0.8) + 0.025 * Math.cos(i * 2.1);
    const grow = 1 + 0.1 * (i / n);
    pts.push(`${(cx + Math.cos(a) * rx * wobble * grow).toFixed(1)},${(cy + Math.sin(a) * ry * wobble * grow).toFixed(1)}`);
  }
  return `M${pts.join(" L")}`;
}

/** `clean`: the receipt alone, centred, no words around it (B-roll for an edit). */
export function ReceiptScene({ model, lang, clean = false }: { model: MapModel; lang: Lang; clean?: boolean }) {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { wide, width, height } = useLayout();
  const c = COPY[lang];
  const ref = useRef<HTMLDivElement>(null);

  const S = wide ? 2.0 : 2.45; // receipt scale while printing
  const RW = 22 * 16; // receipt width at 1x
  const CX = wide && !clean ? 1296 : width / 2;
  const SLOT_Y = wide ? 902 : 1496;
  const Z = wide ? 1.3 : 1.12; // extra zoom on "needs your call" (tall: keep the paper inside the frame)

  const m = useMeasure<Measured>(() => {
    const section = ref.current?.querySelector<HTMLElement>("section");
    const list = section?.querySelector<HTMLElement>("ul");
    const badge = section?.querySelector<HTMLElement>(".bg-redpen");
    const row = list?.querySelector<HTMLElement>("li");
    if (!section || !list || !badge || !row) return null;
    const offset = (node: HTMLElement) => {
      let x = 0;
      let y = 0;
      for (let n: HTMLElement | null = node; n && n !== section; n = n.offsetParent as HTMLElement | null) {
        x += n.offsetLeft;
        y += n.offsetTop;
      }
      return { x, y };
    };
    const b = offset(badge);
    const l = offset(list);
    const r = offset(row);
    return {
      h: section.offsetHeight,
      needsTop: b.y - 6,
      needsBottom: l.y + list.offsetHeight,
      badge: { x: b.x, y: b.y, w: badge.offsetWidth, h: badge.offsetHeight },
      row: { x: r.x, y: r.y, w: row.offsetWidth, h: row.offsetHeight },
    };
  });

  const H = m?.h ?? 0;
  const fed = tween(f, PRINT, [0, H], Easing.bezier(0.3, 0, 0.7, 1));
  const settle = spring({ frame: f - TEAR, fps, config: { damping: 200 }, durationInFrames: 58 });
  const needsMid = m ? (m.needsTop + m.needsBottom) / 2 : 0;
  const targetY = wide ? height * 0.5 : height * 0.55;
  const torn = f >= TEAR;
  const top = torn ? interpolate(settle, [0, 1], [SLOT_Y - H * S, targetY - needsMid * S * Z]) : SLOT_Y - fed * S;
  const scale = torn ? interpolate(settle, [0, 1], [S, S * Z]) : S;
  const rot = torn ? interpolate(settle, [0, 1], [0, -1.4]) : 0;

  const printerAway = tween(f, [TEAR + 2, TEAR + 40], [0, 1], IN_OUT);
  const printing = f >= PRINT[0] && f < PRINT[1];
  const circle = tween(f, [TEAR + 64, TEAR + 88], [0, 1], IN_OUT);

  const row = m?.row ?? { x: 0, y: 0, w: 0, h: 0 };
  const pointer = usePointer([row.x + row.w + 70, row.y + row.h + 150], [row.x + row.w * 0.36, row.y + row.h * 0.55], POINT, CLICK);
  const hover = tween(f, [POINT[1] - 8, POINT[1]], [0, 1]);

  const dawn = clean ? 1 : tween(f, [0, 30], [0, 1], IN_OUT);
  const push = clean ? 0 : tween(f, [RECEIPT_EXIT, RECEIPT_EXIT + 30], [0, 1], IN_OUT);
  const PW = RW * S + 96;

  return (
    <AbsoluteFill
      className="bg-bg text-ink"
      style={{ clipPath: `inset(${(1 - dawn) * 100}% 0 0 0)`, transform: `translateY(${-push * height}px)` }}
    >
      {/* paper: clipped at the slot while printing, free once torn off */}
      <div
        className="absolute inset-x-0 top-0 overflow-hidden"
        style={{
          height: torn ? height : SLOT_Y,
          maskImage: wide
            ? "linear-gradient(to bottom, transparent 0, #000 140px)"
            : "linear-gradient(to bottom, transparent 0, transparent 560px, #000 740px)",
        }}
      >
        <div
          ref={ref}
          className="absolute"
          style={{
            left: CX - RW / 2,
            top,
            width: RW,
            transform: `scale(${scale}) rotate(${rot}deg)`,
            transformOrigin: "50% 0",
            filter: "drop-shadow(0 18px 30px rgb(22 26 34 / 0.12))",
          }}
        >
          <Receipt model={model} className="w-[22rem]" />
          {m && !clean && hover > 0 && (
            <div
              className="absolute rounded-md"
              style={{ left: row.x - 2, top: row.y - 2, width: row.w + 4, height: row.h + 4, background: "var(--surface-2)", mixBlendMode: "multiply", opacity: hover }}
            />
          )}
          {m && (
            <svg className="pointer-events-none absolute left-0 top-0 overflow-visible" width={RW} height={H} aria-hidden>
              <path
                d={penLoop(m.badge.x + m.badge.w / 2, m.badge.y + m.badge.h / 2, m.badge.w / 2 + 15, m.badge.h / 2 + 10)}
                fill="none"
                stroke="var(--redpen)"
                strokeWidth={2.4}
                strokeLinecap="round"
                strokeLinejoin="round"
                pathLength={1}
                strokeDasharray="1 1"
                strokeDashoffset={1 - circle}
              />
            </svg>
          )}
          {m && !clean && pointer.visible && (
            <Pointer x={pointer.x} y={pointer.y} size={13} press={pointer.press} ring={pointer.ring} opacity={tween(f, [POINT[0], POINT[0] + 8], [0, 1])} />
          )}
        </div>
      </div>

      {/* the printer: paper leaves through the slot along its top edge */}
      <div
        className="absolute"
        style={{ left: CX - PW / 2, top: SLOT_Y - 20, width: PW, height: 640, transform: `translateY(${printerAway * 720}px)` }}
      >
        <div className="absolute inset-0 rounded-t-[34px] bg-ink" style={{ boxShadow: "0 -1px 0 rgb(255 255 255 / 0.08) inset" }} />
        <div className="absolute left-10 right-10 top-[16px] h-[6px] rounded-full bg-black/70" />
        <div className="absolute left-10 top-[42px] font-mono text-[17px] uppercase tracking-[0.3em] text-white/30">auto-research</div>
        <div
          className="absolute right-10 top-[44px] h-3 w-3 rounded-full"
          style={{
            background: "#43c99a",
            opacity: printing ? (Math.floor(f / 8) % 2 === 0 ? 1 : 0.35) : 0.5,
            boxShadow: "0 0 12px #43c99a",
          }}
        />
      </div>

      {clean ? null : wide ? (
        <div className="absolute inset-y-0 flex flex-col justify-center" style={{ left: 120, width: 640 }}>
          <Appear at={8}>
            <div style={{ zoom: 2.4 }}>
              <InkStamp icon={ReceiptText} label={translate(lang, "receipt_title")} />
            </div>
          </Appear>
          <div className="relative mt-9">
            <Headline lines={c.morning} enter={12} exit={TEAR - 10} lang={lang} size={lang === "zh" ? 80 : 72} />
            <Headline
              lines={c.needs(needsList(model).length)}
              enter={TEAR + 16}
              lang={lang}
              size={lang === "zh" ? 80 : 72}
              className="absolute left-0 top-0"
            />
          </div>
        </div>
      ) : (
        <div className="absolute" style={{ left: 72, right: 72, top: 168 }}>
          <Appear at={8}>
            <div style={{ zoom: 2.6 }}>
              <InkStamp icon={ReceiptText} label={translate(lang, "receipt_title")} />
            </div>
          </Appear>
          <div className="relative mt-9">
            <Headline lines={c.morning} enter={12} exit={TEAR - 10} lang={lang} size={lang === "zh" ? 92 : 80} />
            <Headline
              lines={c.needs(needsList(model).length)}
              enter={TEAR + 16}
              lang={lang}
              size={lang === "zh" ? 92 : 80}
              className="absolute left-0 top-0"
            />
          </div>
        </div>
      )}
    </AbsoluteFill>
  );
}
