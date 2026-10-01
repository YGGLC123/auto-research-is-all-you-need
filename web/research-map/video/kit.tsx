// Small motion kit for the promo. Every value is a pure function of the frame, as
// Remotion requires; nothing here uses CSS transitions or timers.
import type { LucideIcon } from "lucide-react";
import { type CSSProperties, type ReactNode, useEffect, useLayoutEffect, useState } from "react";
import { continueRender, delayRender, Easing, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { cn } from "@/components/ui";
import type { Lang } from "@/i18n";

/** Fast out, long settle: the entrance curve for everything. */
export const EXPO = Easing.bezier(0.16, 1, 0.3, 1);
/** Symmetric: wipes, pushes, and exits. */
export const IN_OUT = Easing.bezier(0.65, 0, 0.35, 1);

export function tween(
  frame: number,
  [a, b]: [number, number],
  [from, to]: [number, number],
  easing: (t: number) => number = EXPO,
): number {
  return interpolate(frame, [a, b], [from, to], { easing, extrapolateLeft: "clamp", extrapolateRight: "clamp" });
}

export function useLayout() {
  const { width, height } = useVideoConfig();
  return { width, height, wide: width / height > 1.2 };
}

const FACES = [
  "800 64px Archivo",
  "700 64px Archivo",
  "600 64px Archivo",
  "400 16px 'IBM Plex Sans'",
  "500 16px 'IBM Plex Sans'",
  "600 16px 'IBM Plex Sans'",
  "400 16px 'IBM Plex Mono'",
  "500 16px 'IBM Plex Mono'",
];

/** Holds the render until every face is loaded, so no frame is shot in a fallback font. */
export function FontGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [handle] = useState(() => delayRender("Loading fonts"));
  useEffect(() => {
    let alive = true;
    const smiley = new FontFace("Smiley Sans", `url("${staticFile("fonts/SmileySans-Oblique.ttf")}")`, { weight: "100 900" });
    const loads: Promise<unknown>[] = [
      smiley
        .load()
        .then((face) => document.fonts.add(face))
        .catch(() => undefined), // no font file: the zh cut falls back to Noto Sans SC
      ...FACES.map((spec) => document.fonts.load(spec).catch(() => undefined)),
    ];
    Promise.all(loads)
      .then(() => document.fonts.ready)
      .then(() => alive && setReady(true));
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    if (ready) continueRender(handle);
  }, [ready, handle]);
  return ready ? <>{children}</> : null;
}

/** Measure the laid-out DOM once (after fonts), holding the frame until it is known. */
export function useMeasure<T>(measure: () => T | null): T | null {
  const [value, setValue] = useState<T | null>(null);
  const [handle] = useState(() => delayRender("Measuring layout"));
  useLayoutEffect(() => {
    if (value !== null) return;
    const v = measure();
    if (v !== null) setValue(v);
  });
  useEffect(() => {
    if (value !== null) continueRender(handle);
  }, [value, handle]);
  return value;
}

/** Lines rise out of a mask, one after another; on exit they leave upward. */
export function Headline({
  lines,
  enter,
  exit,
  lang,
  size,
  className,
  style,
}: {
  lines: ReactNode[];
  enter: number;
  exit?: number;
  lang: Lang;
  size: number;
  className?: string;
  style?: CSSProperties;
}) {
  const f = useCurrentFrame();
  const zh = lang === "zh";
  return (
    <div
      className={cn(zh ? "font-smiley" : "font-display font-extrabold", className)}
      style={{
        fontSize: size,
        lineHeight: zh ? 1.18 : 1.0,
        letterSpacing: zh ? "0.01em" : "-0.025em",
        fontStretch: zh ? undefined : "78%",
        ...style,
      }}
    >
      {lines.map((line, i) => {
        const a = enter + i * 5;
        const rise = tween(f, [a, a + 30], [0, 1]);
        const leave = exit === undefined ? 0 : tween(f, [exit + i * 3, exit + i * 3 + 18], [0, 1], IN_OUT);
        return (
          // the mask is padded so oblique glyphs and descenders are not clipped
          <div key={i} style={{ overflow: "hidden", padding: "0.06em 0.2em 0.12em 0", margin: "-0.06em -0.2em -0.12em 0" }}>
            <div style={{ transform: `translateY(${(1 - rise) * 112 - leave * 112}%)`, whiteSpace: "nowrap" }}>{line}</div>
          </div>
        );
      })}
    </div>
  );
}

/** Fade-and-lift for small elements. */
export function Appear({
  at,
  exit,
  dy = 18,
  className,
  style,
  children,
}: {
  at: number;
  exit?: number;
  dy?: number;
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  const f = useCurrentFrame();
  const p = tween(f, [at, at + 24], [0, 1]);
  const q = exit === undefined ? 0 : tween(f, [exit, exit + 16], [0, 1], IN_OUT);
  return (
    <div className={className} style={{ opacity: p * (1 - q), transform: `translateY(${(1 - p) * dy - q * dy}px)`, ...style }}>
      {children}
    </div>
  );
}

/** Same rubber-stamp look as the product's Stamp, in ink. */
export function InkStamp({ icon: Icon, label }: { icon: LucideIcon; label: string }) {
  return (
    <span className="inline-flex -rotate-[3deg] items-center gap-1 rounded-[0.35rem] border-[1.5px] border-ink bg-surface px-1.5 py-[0.1875rem] font-display text-[0.6875rem] font-extrabold uppercase leading-none tracking-[0.06em] text-ink shadow-card [font-stretch:88%]">
      <Icon className="h-3 w-3" strokeWidth={2.5} />
      {label}
    </span>
  );
}

/**
 * The pointer moves on a gentle arc (x and y ease differently) and dips when it
 * clicks. Coordinates are those of the layer it sits in; the tip is at (x, y).
 */
export function usePointer(from: [number, number], to: [number, number], [start, arrive]: [number, number], click: number) {
  const f = useCurrentFrame();
  const t = interpolate(f, [start, arrive], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const x = from[0] + (to[0] - from[0]) * EXPO(t);
  const y = from[1] + (to[1] - from[1]) * Easing.bezier(0.5, 0, 0.2, 1)(t);
  const press = f >= click && f < click + 10 ? Math.sin(((f - click) / 10) * Math.PI) : 0;
  return { x, y, press, ring: tween(f, [click, click + 18], [0, 1]), clicked: f >= click, visible: f >= start };
}

export function Pointer({ x, y, size, press = 0, ring = 0, opacity = 1 }: { x: number; y: number; size: number; press?: number; ring?: number; opacity?: number }) {
  return (
    <>
      {ring > 0 && ring < 1 && (
        <span
          className="absolute rounded-full border-2 border-ink"
          style={{
            left: x - size * 0.9 * ring,
            top: y - size * 0.9 * ring,
            width: size * 1.8 * ring,
            height: size * 1.8 * ring,
            opacity: 0.45 * (1 - ring),
          }}
        />
      )}
      <svg
        className="absolute overflow-visible"
        width={size}
        height={size * 1.5}
        viewBox="0 0 16 24"
        style={{
          left: x,
          top: y,
          opacity,
          transform: `scale(${1 - 0.16 * press})`,
          transformOrigin: "0 0",
          filter: "drop-shadow(0 2px 3px rgb(0 0 0 / 0.28))",
        }}
        aria-hidden
      >
        <path d="M1 1 L1 18.6 L5.4 14.7 L8.3 21.3 L11.3 20 L8.5 13.5 L14.3 13.5 Z" fill="#111" stroke="#fff" strokeWidth={1.5} strokeLinejoin="round" />
      </svg>
    </>
  );
}

/** A fixed paper grain over the whole frame. It never moves, so it costs the encoder little. */
export function Grain({ opacity = 0.05 }: { opacity?: number }) {
  return (
    <svg className="pointer-events-none absolute inset-0 h-full w-full" style={{ opacity, mixBlendMode: "multiply" }} aria-hidden>
      <filter id="paper-grain">
        <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" stitchTiles="stitch" />
        <feColorMatrix type="saturate" values="0" />
      </filter>
      <rect width="100%" height="100%" filter="url(#paper-grain)" />
    </svg>
  );
}
