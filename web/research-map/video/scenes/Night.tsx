// 02:03 → 02:41. The clock runs through the night's real commits while you sleep.
import { AbsoluteFill, useCurrentFrame } from "remotion";
import type { MapModel } from "@/data";
import { fmtTime, type Lang } from "@/i18n";
import { COPY } from "../copy";
import { Headline, IN_OUT, tween, useLayout } from "../kit";

const toMinutes = (hhmm: string) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};
const pad = (n: number) => String(n).padStart(2, "0");

const RUN: [number, number] = [6, 128];

export function Night({ model, lang }: { model: MapModel; lang: Lang }) {
  const f = useCurrentFrame();
  const { wide } = useLayout();
  const c = COPY[lang];
  const tz = model.tz_offset_minutes;
  const log = (model.log ?? []).map((x) => ({ id: x.id, time: fmtTime(x.at_full || x.at, tz), message: x.message }));
  const first = toMinutes(log[0]?.time ?? "02:00");
  const last = toMinutes(log[log.length - 1]?.time ?? "02:40");
  const minuteAt = (frame: number) => Math.round(tween(frame, RUN, [first, last], IN_OUT));
  const now = minuteAt(f);
  // a commit lands on the first frame the clock reaches its minute
  const landed = log.map((x) => {
    for (let k = 0; k <= RUN[1]; k++) if (minuteAt(k) >= toMinutes(x.time)) return k;
    return RUN[1];
  });
  const colon = Math.floor(f / 30) % 2 === 0 ? 1 : 0.28;

  const clock = (
    <div
      className="font-display font-extrabold leading-[0.86] text-ink tabular-nums"
      style={{ fontSize: wide ? 232 : 300, fontStretch: "72%", letterSpacing: "-0.02em" }}
    >
      {pad(Math.floor(now / 60))}
      <span style={{ color: "var(--pencil)", opacity: colon }}>:</span>
      {pad(now % 60)}
    </div>
  );

  // frame 0 is the poster (GitHub's autoplay, a feed's cover): it opens on a full screen
  const lines = (
    <ol className="mt-10 space-y-3 font-mono" style={{ fontSize: wide ? 23 : 29 }}>
      {log.map((x, i) => {
        const show = i === 0 ? 1 : tween(f, [landed[i], landed[i] + 16], [0, 1]);
        const newer = landed.filter((k, j) => j > i && k <= f).length;
        const fade = Math.max(0.3, 1 - 0.17 * newer);
        return (
          <li
            key={x.id}
            className="flex gap-5 whitespace-nowrap"
            style={{ opacity: show * fade, transform: `translateY(${(1 - show) * 14}px)` }}
          >
            <span className="text-pencil">{x.time}</span>
            <span className={newer === 0 ? "truncate text-ink" : "truncate text-ink-2"}>{x.message}</span>
          </li>
        );
      })}
    </ol>
  );

  return (
    <AbsoluteFill className="dark bg-bg text-ink">
      <AbsoluteFill
        style={{
          backgroundImage: "radial-gradient(var(--dot) 1.6px, transparent 1.9px)",
          backgroundSize: "30px 30px",
          opacity: 0.9,
        }}
      />
      {/* a laptop's glow in a dark room */}
      <AbsoluteFill
        style={{
          background: wide
            ? "radial-gradient(900px 640px at 66% 48%, rgb(130 162 255 / 0.11), transparent 70%)"
            : "radial-gradient(900px 760px at 50% 52%, rgb(130 162 255 / 0.11), transparent 70%)",
        }}
      />
      <AbsoluteFill style={{ background: "radial-gradient(ellipse at center, transparent 55%, rgb(0 0 0 / 0.45) 100%)" }} />

      {wide ? (
        <>
          <div className="absolute inset-y-0 flex flex-col justify-center" style={{ left: 120, width: 620 }}>
            <p className="font-mono text-[22px] uppercase tracking-[0.28em] text-ink-3">{c.nightEyebrow}</p>
            <Headline lines={c.night} enter={-40} lang={lang} size={lang === "zh" ? 80 : 72} className="mt-7 text-ink" />
          </div>
          <div className="absolute inset-y-0 flex flex-col justify-center" style={{ left: 830, right: 110 }}>
            {clock}
            {lines}
          </div>
        </>
      ) : (
        <>
          <div className="absolute" style={{ left: 72, right: 72, top: 176 }}>
            <p className="font-mono text-[26px] uppercase tracking-[0.28em] text-ink-3">{c.nightEyebrow}</p>
            <Headline lines={c.night} enter={-40} lang={lang} size={lang === "zh" ? 92 : 80} className="mt-7 text-ink" />
          </div>
          <div className="absolute" style={{ left: 72, right: 72, top: 700 }}>
            {clock}
            {lines}
          </div>
        </>
      )}
    </AbsoluteFill>
  );
}
