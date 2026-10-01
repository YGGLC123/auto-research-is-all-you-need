// The map, in the real app's window. The camera finds the benched result and the
// sentence that breaks your taste; each gets its stamp and its callout.
import { Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import { useMemo } from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { hasChange, needsYou, type MapModel, type MapNode } from "@/data";
import type { Lang } from "@/i18n";
import { place, type Placed } from "@/layout";
import { Stamp } from "@/components/badges";
import { BenchCallout, TasteCallout } from "@/components/callouts";
import { ClaimCard, RootPlate, SectionChip } from "@/components/cards";
import { Header } from "@/components/Header";
import { Button } from "@/components/ui";
import { COPY } from "../copy";
import { Appear, Headline, IN_OUT, Pointer, tween, useLayout, usePointer } from "../kit";

const noop = () => {};

const BENCH_HIT = 124;
const TASTE_HIT = 372;
const GLIDE_1 = 56;
const GLIDE_2 = 300;
const TASTE_CLICK = TASTE_HIT + 36; // the reader clicks the flagged card to open it
export const MAP_EXIT = 570; // the outro wipes in from here

type Cam = { fx: number; fy: number; z: number; ax: number; ay: number };
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const mix = (a: Cam, b: Cam, t: number): Cam => ({
  fx: lerp(a.fx, b.fx, t),
  fy: lerp(a.fy, b.fy, t),
  z: Math.exp(lerp(Math.log(a.z), Math.log(b.z), t)), // zoom feels even in log space
  ax: lerp(a.ax, b.ax, t),
  ay: lerp(a.ay, b.ay, t),
});

/** React Flow's default bezier, right handle to left handle. */
function bezier(sx: number, sy: number, tx: number, ty: number): string {
  const dx = Math.abs(tx - sx) * 0.5;
  return `M${sx},${sy} C${sx + dx},${sy} ${tx - dx},${ty} ${tx},${ty}`;
}

function depthOf(p: Placed): number {
  return p.kind === "root" ? 0 : p.kind === "section" ? 1 : 2;
}

/** The stamp lands hard: big and tilted, then a spring with a little overshoot. */
function useSlam(hit: number) {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: f - (hit - 7), fps, config: { damping: 13, stiffness: 210, mass: 0.75 } });
  return {
    style: {
      opacity: tween(f, [hit - 7, hit - 4], [0, 1]),
      transform: `scale(${interpolate(s, [0, 1], [2.7, 1])}) rotate(${interpolate(s, [0, 1], [-16, 0])}deg)`,
    },
    landed: f >= hit,
    shake: f >= hit && f < hit + 14 ? Math.sin((f - hit) * 1.9) * 5 * Math.exp(-(f - hit) / 4) : 0,
    ripple: tween(f, [hit, hit + 24], [0, 1]),
  };
}

export function MapScene({ model, lang }: { model: MapModel; lang: Lang }) {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { wide, height } = useLayout();
  const c = COPY[lang];

  const placed = useMemo(() => place(model, new Set(), 16), [model]);
  const byId = useMemo(() => new Map(placed.map((p) => [p.node.id, p])), [placed]);
  const benched = placed.find((p) => p.node.badges.lever)!;
  const tasted = placed.find((p) => p.node.badges.taste > 0)!;

  // the window and its canvas
  const WIN = wide ? { x: 740, y: 92, w: 1100, h: 896 } : { x: 44, y: 606, w: 992, h: 1040 };
  const HEADER_H = 56;
  const CW = WIN.w;
  const CH = WIN.h - HEADER_H;
  const PAD = 700; // the tilted plane is oversized so its edges never show

  const minX = Math.min(...placed.map((p) => p.x));
  const maxX = Math.max(...placed.map((p) => p.x + p.w));
  const minY = Math.min(...placed.map((p) => p.y));
  const maxY = Math.max(...placed.map((p) => p.y + p.h));
  const fit = Math.min((CW * 0.88) / (maxX - minX), (CH * 0.86) / (maxY - minY));
  const zFocus = wide ? 1.3 : 2.5;
  const anchor = wide ? { ax: CW * 0.2, ay: CH * 0.42 } : { ax: CW / 2, ay: CH * 0.25 };
  const focusOn = (p: Placed): Cam => ({ fx: p.x + p.w / 2, fy: p.y + p.h / 2, z: zFocus, ...anchor });
  const K0: Cam = { fx: (minX + maxX) / 2, fy: (minY + maxY) / 2, z: fit, ax: CW / 2, ay: CH / 2 };
  const g1 = spring({ frame: f - GLIDE_1, fps, config: { damping: 200 }, durationInFrames: 68 });
  const g2 = spring({ frame: f - GLIDE_2, fps, config: { damping: 200 }, durationInFrames: 68 });
  const cam = g2 > 0 ? mix(focusOn(benched), focusOn(tasted), g2) : mix(K0, focusOn(benched), g1);
  const tx = cam.ax - cam.fx * cam.z;
  const ty = cam.ay - cam.fy * cam.z;
  const tilt = 1 - g1;

  const bench = useSlam(BENCH_HIT);
  const taste = useSlam(TASTE_HIT);
  const focusBench = tween(f, [BENCH_HIT - 6, BENCH_HIT + 16], [0, 1]) * (1 - tween(f, [GLIDE_2 - 8, GLIDE_2 + 16], [0, 1]));
  const focusTaste = tween(f, [TASTE_HIT - 6, TASTE_HIT + 16], [0, 1]);
  const dim = (id: string) =>
    Math.max(
      id === benched.node.id || id === benched.node.parent ? 0 : focusBench,
      id === tasted.node.id ? 0 : focusTaste,
    );

  // before its stamp lands, a card shows nothing of what the stamp will say
  const shown = (n: MapNode): MapNode => {
    if (n.id === benched.node.id && !bench.landed) return { ...n, badges: { ...n.badges, lever: false } };
    if (n.id === tasted.node.id && !taste.landed) return { ...n, badges: { ...n.badges, taste: 0 } };
    return n;
  };

  const order = new Map(placed.map((p, i) => [p.node.id, i]));
  const pop = (p: Placed) => {
    const a = 12 + depthOf(p) * 7 + (order.get(p.node.id) ?? 0) * 1.1;
    return tween(f, [a, a + 20], [0, 1]);
  };

  const edges = placed
    .filter((p) => p.node.parent && byId.has(p.node.parent))
    .map((p, i) => {
      const s = byId.get(p.node.parent!)!;
      const a = 20 + depthOf(p) * 8 + i * 0.8;
      return {
        id: p.node.id,
        d: bezier(s.x + s.w, s.y + s.h / 2, p.x, p.y + p.h / 2),
        color: p.branch,
        drawn: tween(f, [a, a + 26], [0, 1], IN_OUT),
      };
    });
  const movedFrom = benched.node.change?.moved_from?.replace(/…$/, "");
  const oldHome = movedFrom ? placed.find((p) => p.node.id !== benched.node.parent && p.node.title.startsWith(movedFrom)) : undefined;

  const benchIn = tween(f, [BENCH_HIT + 16, BENCH_HIT + 40], [0, 1]);
  const benchOut = tween(f, [GLIDE_2 - 8, GLIDE_2 + 8], [0, 1], IN_OUT);
  const tasteIn = tween(f, [TASTE_CLICK + 2, TASTE_CLICK + 26], [0, 1]);
  const sweep = tween(f, [TASTE_CLICK + 34, TASTE_CLICK + 72], [0, 100], IN_OUT);
  const pointerSize = (wide ? 34 : 44) / zFocus; // the pointer lives in the world; keep its screen size fixed
  const pointer = usePointer(
    [tasted.x + tasted.w + 140, tasted.y + tasted.h + 170],
    [tasted.x + tasted.w * 0.42, tasted.y + tasted.h * 0.62],
    [TASTE_HIT + 4, TASTE_CLICK - 6],
    TASTE_CLICK,
  );
  const demotedBy = model.log?.find((l) => benched.node.lever && benched.node.lever.demoted_commit.startsWith(l.id));

  const enter = tween(f, [0, 30], [0, 1], IN_OUT);
  const calloutBox = (shift: number, opacity: number) =>
    wide
      ? { right: 30, top: CH * 0.45, transform: `translate(${shift * 130}px, -50%)`, opacity }
      : { left: 28, right: 28, bottom: 28, transform: `translateY(${shift * 260}px)`, opacity };
  const calloutZoom = wide ? 1.8 : (CW - 56) / (24 * 16);
  const calloutWidth = wide ? "21rem" : "24rem";
  const floating = "0 1px 2px rgb(22 26 34 / 0.06), 0 24px 60px rgb(22 26 34 / 0.18)";

  const stampAt = (p: Placed, kind: "lever" | "taste", slam: ReturnType<typeof useSlam>) => (
    <div
      className="absolute"
      style={{
        left: p.x + p.w - 8,
        top: p.y - 16.8,
        width: "max-content", // the world layer has no width; without this the stamp wraps per character
        transform: "translateX(-100%)",
        opacity: 1 - 0.64 * dim(p.node.id),
      }}
    >
      <div style={slam.style}>
        <Stamp kind={kind} />
      </div>
    </div>
  );

  return (
    <AbsoluteFill className="bg-bg text-ink" style={{ transform: `translateY(${(1 - enter) * height}px)` }}>
      {/* the window: the real header over a canvas the camera moves across */}
      <div
        className="video-window absolute overflow-hidden rounded-[28px] border border-line bg-surface"
        style={{
          left: WIN.x,
          top: WIN.y,
          width: WIN.w,
          height: WIN.h,
          boxShadow: "0 1px 2px rgb(22 26 34 / 0.06), 0 30px 80px rgb(22 26 34 / 0.16)",
        }}
      >
        <div style={{ height: HEADER_H, overflow: "hidden" }}>
          <Header
            model={model}
            filter="all"
            onFilter={noop}
            changedCount={model.nodes.filter(hasChange).length}
            needsCount={model.nodes.filter(needsYou).length}
            query=""
            onQuery={noop}
            dark={false}
            onDark={noop}
            onLang={noop}
            onScale={noop}
            onReceipt={noop}
          />
        </div>
        <div className="relative overflow-hidden" style={{ height: CH, background: "var(--canvas)" }}>
          <div
            className="absolute"
            style={{
              left: -PAD,
              top: -PAD,
              width: CW + 2 * PAD,
              height: CH + 2 * PAD,
              transform: `perspective(2200px) rotateX(${22 * tilt}deg) rotateZ(${-5 * tilt}deg)`,
              transformOrigin: "50% 50%",
              backgroundImage: "radial-gradient(var(--dot) 1.5px, transparent 1.8px)",
              backgroundSize: `${22 * cam.z}px ${22 * cam.z}px`,
              backgroundPosition: `${PAD + tx}px ${PAD + ty}px`,
            }}
          >
            <div className="absolute" style={{ left: PAD, top: PAD, transform: `translate(${tx}px, ${ty}px) scale(${cam.z})`, transformOrigin: "0 0" }}>
              <svg className="absolute left-0 top-0 overflow-visible" width={1} height={1} aria-hidden>
                {edges.map((e) => (
                  <path
                    key={e.id}
                    d={e.d}
                    fill="none"
                    stroke={e.color}
                    strokeWidth={1.6}
                    strokeOpacity={0.6 * (1 - 0.7 * dim(e.id))}
                    pathLength={1}
                    strokeDasharray="1 1"
                    strokeDashoffset={1 - e.drawn}
                  />
                ))}
                {oldHome && (
                  <path
                    d={bezier(oldHome.x + oldHome.w, oldHome.y + oldHome.h / 2, benched.x, benched.y + benched.h / 2)}
                    fill="none"
                    stroke="var(--pencil)"
                    strokeWidth={1.4}
                    strokeDasharray="5 5"
                    strokeOpacity={0.8 * tween(f, [44, 70], [0, 1]) * (1 - 0.7 * focusTaste)}
                  />
                )}
              </svg>

              {placed.map((p) => {
                const k = pop(p);
                const shake = p.node.id === benched.node.id ? bench.shake : 0;
                return (
                  <div
                    key={p.node.id}
                    className="absolute"
                    style={{
                      left: p.x,
                      top: p.y,
                      opacity: k * (1 - 0.64 * dim(p.node.id)),
                      transform: `translate(${shake}px, ${(1 - k) * 12}px) scale(${0.97 + 0.03 * k})`,
                    }}
                  >
                    {p.kind === "root" ? (
                      <RootPlate node={p.node} />
                    ) : p.kind === "section" ? (
                      <SectionChip node={p.node} branch={p.branch} onToggle={noop} />
                    ) : (
                      <ClaimCard node={shown(p.node)} branch={p.branch} showStamps={false} />
                    )}
                  </div>
                );
              })}

              {bench.ripple > 0 && bench.ripple < 1 && (
                <div
                  className="absolute rounded-[14px] border-[3px] border-redpen"
                  style={{
                    left: benched.x,
                    top: benched.y,
                    width: benched.w,
                    height: benched.h,
                    opacity: 0.7 * (1 - bench.ripple),
                    transform: `scale(${1 + 0.12 * bench.ripple})`,
                  }}
                />
              )}
              {stampAt(benched, "lever", bench)}
              {stampAt(tasted, "taste", taste)}
              {pointer.visible && (
                <Pointer
                  x={pointer.x}
                  y={pointer.y}
                  size={pointerSize}
                  press={pointer.press}
                  ring={pointer.ring}
                  opacity={tween(f, [TASTE_HIT + 4, TASTE_HIT + 12], [0, 1]) * (1 - tween(f, [TASTE_CLICK + 24, TASTE_CLICK + 40], [0, 1]))}
                />
              )}
            </div>
          </div>

          <div className="absolute bottom-4 left-4 flex items-center gap-0.5 rounded-xl border border-line bg-surface p-1 shadow-card">
            <Button variant="ghost" size="icon" tabIndex={-1}>
              <ZoomOut className="h-4 w-4" />
            </Button>
            <Button variant="ghost" size="icon" tabIndex={-1}>
              <ZoomIn className="h-4 w-4" />
            </Button>
            <Button variant="ghost" size="icon" tabIndex={-1}>
              <Maximize2 className="h-4 w-4" />
            </Button>
          </div>

          {benched.node.lever && benchIn > 0 && benchOut < 1 && (
            <div className="absolute" style={calloutBox(1 - benchIn + benchOut * 0.5, benchIn * (1 - benchOut))}>
              <div style={{ zoom: calloutZoom, width: calloutWidth, borderRadius: "0.75rem", boxShadow: floating }}>
                <BenchCallout lever={benched.node.lever} demotedBy={demotedBy} tz={model.tz_offset_minutes} />
              </div>
            </div>
          )}
          {tasted.node.taste[0] && tasteIn > 0 && (
            <div className="absolute" style={calloutBox(1 - tasteIn, tasteIn)}>
              <div style={{ zoom: calloutZoom, width: calloutWidth, borderRadius: "0.75rem", boxShadow: floating, ["--marker-sweep" as string]: `${sweep}%` }}>
                <TasteCallout hit={tasted.node.taste[0]} className="bg-surface" />
              </div>
            </div>
          )}
        </div>
      </div>

      {wide ? (
        <div className="absolute inset-y-0 flex flex-col justify-center" style={{ left: 120, width: 580 }}>
          <div className="relative h-[60px]">
            <Appear at={16} exit={GLIDE_2 - 10} className="absolute left-0 top-0">
              <div style={{ zoom: 2.4 }}>
                <Stamp kind="lever" />
              </div>
            </Appear>
            <Appear at={GLIDE_2 + 18} className="absolute left-0 top-0">
              <div style={{ zoom: 2.4 }}>
                <Stamp kind="taste" />
              </div>
            </Appear>
          </div>
          <div className="relative mt-8">
            <Headline lines={c.bench} enter={22} exit={GLIDE_2 - 8} lang={lang} size={lang === "zh" ? 80 : 68} />
            <Headline lines={c.taste} enter={GLIDE_2 + 24} lang={lang} size={lang === "zh" ? 80 : 68} className="absolute left-0 top-0" />
          </div>
        </div>
      ) : (
        <div className="absolute" style={{ left: 72, right: 72, top: 168 }}>
          <div className="relative h-[66px]">
            <Appear at={16} exit={GLIDE_2 - 10} className="absolute left-0 top-0">
              <div style={{ zoom: 2.6 }}>
                <Stamp kind="lever" />
              </div>
            </Appear>
            <Appear at={GLIDE_2 + 18} className="absolute left-0 top-0">
              <div style={{ zoom: 2.6 }}>
                <Stamp kind="taste" />
              </div>
            </Appear>
          </div>
          <div className="relative mt-8">
            <Headline lines={c.bench} enter={22} exit={GLIDE_2 - 8} lang={lang} size={lang === "zh" ? 92 : 78} />
            <Headline lines={c.taste} enter={GLIDE_2 + 24} lang={lang} size={lang === "zh" ? 92 : 78} className="absolute left-0 top-0" />
          </div>
        </div>
      )}
    </AbsoluteFill>
  );
}
