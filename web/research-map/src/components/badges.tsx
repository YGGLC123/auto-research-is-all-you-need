import {
  ArrowRightLeft,
  CircleHelp,
  Highlighter,
  ImageOff,
  PenLine,
  Plus,
  Snowflake,
  Stamp as StampIcon,
  Ticket,
  Zap,
  type LucideIcon,
} from "lucide-react";
import type { MapNode } from "@/data";
import { useT } from "@/i18n";
import { cn, Tip } from "./ui";

export type BadgeKey =
  | "new"
  | "changed"
  | "moved"
  | "lever"
  | "taste"
  | "conflict"
  | "no_evidence"
  | "no_figure"
  | "live_bet"
  | "signed";

type Tone = "fresh" | "pencil" | "redpen" | "marker" | "clash" | "muted" | "stamp";

export const TONE: Record<Tone, string> = {
  fresh: "text-fresh bg-fresh-soft",
  pencil: "text-pencil bg-pencil-soft",
  redpen: "text-redpen bg-redpen-soft",
  marker: "text-marker-ink bg-marker",
  clash: "text-clash bg-clash-soft",
  muted: "text-ink-2 bg-surface-2",
  stamp: "text-stamp bg-stamp-soft",
};

export const BADGES: { key: BadgeKey; icon: LucideIcon; tone: Tone }[] = [
  { key: "new", icon: Plus, tone: "fresh" },
  { key: "changed", icon: PenLine, tone: "pencil" },
  { key: "moved", icon: ArrowRightLeft, tone: "pencil" },
  { key: "lever", icon: Snowflake, tone: "redpen" },
  { key: "taste", icon: Highlighter, tone: "marker" },
  { key: "conflict", icon: Zap, tone: "clash" },
  { key: "no_evidence", icon: CircleHelp, tone: "muted" },
  { key: "no_figure", icon: ImageOff, tone: "muted" },
  { key: "live_bet", icon: Ticket, tone: "muted" },
  { key: "signed", icon: StampIcon, tone: "stamp" },
];

export function hasBadge(n: MapNode, key: BadgeKey): boolean {
  const b = n.badges;
  switch (key) {
    case "conflict":
      return b.conflict || b.refute > 0;
    case "taste":
      return b.taste > 0;
    default:
      return Boolean(b[key]);
  }
}

export function BadgeIcons({ node, size = "sm" }: { node: MapNode; size?: "sm" | "md" }) {
  const t = useT();
  const on = BADGES.filter((b) => hasBadge(node, b.key));
  if (!on.length) return null;
  return (
    <span className="flex items-center gap-1">
      {on.map(({ key, icon: Icon, tone }) => (
        <Tip key={key} label={t(`badge_${key}`)} side="top">
          <span
            className={cn(
              "inline-flex items-center justify-center rounded-md",
              size === "sm" ? "h-[1.375rem] w-[1.375rem]" : "h-7 w-7",
              TONE[tone],
            )}
            aria-label={t(`badge_${key}`)}
          >
            <Icon className={size === "sm" ? "h-[0.8125rem] w-[0.8125rem]" : "h-4 w-4"} strokeWidth={2.25} />
          </span>
        </Tip>
      ))}
    </span>
  );
}

/** A rubber stamp: only the two things that need the researcher's call get one. */
export function Stamp({ kind, className }: { kind: "lever" | "taste"; className?: string }) {
  const t = useT();
  const Icon = kind === "lever" ? Snowflake : Highlighter;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-[0.35rem] px-1.5 py-[0.1875rem] font-display text-[0.6875rem] font-extrabold uppercase leading-none tracking-[0.06em] shadow-card [font-stretch:88%]",
        kind === "lever"
          ? "-rotate-[4deg] border-[1.5px] border-redpen bg-surface text-redpen"
          : "rotate-[3deg] bg-marker text-marker-ink",
        className,
      )}
    >
      <Icon className="h-3 w-3" strokeWidth={2.5} />
      {t(`badge_${kind}`)}
    </span>
  );
}
