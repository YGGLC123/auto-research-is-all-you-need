// Presentational cards. They know nothing about React Flow, so the product video
// can render the very same components frame by frame.
import { ChevronRight, Crown, Plus } from "lucide-react";
import type { MapNode } from "@/data";
import { useT } from "@/i18n";
import { SIZE } from "@/layout";
import { BadgeIcons, Stamp } from "./badges";
import { cn } from "./ui";

type CardState = { selected?: boolean; dimmed?: boolean; hit?: boolean };

export function RootPlate({ node, selected, dimmed }: { node: MapNode } & CardState) {
  const t = useT();
  return (
    <div
      style={{ width: `${SIZE.root.w}rem`, height: `${SIZE.root.h}rem` }}
      className={cn(
        "flex flex-col justify-between rounded-2xl bg-ink p-4 text-bg shadow-card transition-[opacity,box-shadow]",
        selected && "ring-2 ring-pencil ring-offset-2 ring-offset-canvas",
        dimmed && "opacity-30",
      )}
    >
      <span className="font-mono text-[0.625rem] uppercase tracking-[0.18em] opacity-60">{t("app")}</span>
      <span className="line-clamp-3 font-display text-[1.0625rem] font-bold leading-[1.22] [font-stretch:92%]">
        {node.title}
      </span>
    </div>
  );
}

export function SectionChip({
  node,
  branch,
  collapsed,
  onToggle,
  selected,
  dimmed,
}: { node: MapNode; branch: string; collapsed?: boolean; onToggle?: () => void } & CardState) {
  const t = useT();
  const fresh = node.badges.new;
  return (
    <div
      style={{ width: `${SIZE.section.w}rem`, height: `${SIZE.section.h}rem` }}
      className={cn(
        "relative flex items-center gap-2.5 rounded-xl border bg-surface px-3 shadow-card transition-[opacity,box-shadow]",
        fresh ? "border-dashed border-fresh" : "border-line",
        selected && "ring-2 ring-pencil ring-offset-2 ring-offset-canvas",
        dimmed && "opacity-30",
      )}
    >
      <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: branch }} />
      <span className="line-clamp-2 flex-1 font-display text-[0.875rem] font-semibold leading-tight [font-stretch:94%]">
        {node.title}
      </span>
      {node.children.length > 0 && onToggle && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          className="nodrag inline-flex h-6 shrink-0 items-center gap-0.5 rounded-md px-1 font-mono text-[0.6875rem] text-ink-3 hover:bg-surface-2 hover:text-ink"
          aria-label={t("children_stat", { n: node.children.length })}
        >
          {node.children.length}
          <ChevronRight className={cn("h-3.5 w-3.5 transition-transform", !collapsed && "rotate-90")} />
        </button>
      )}
      {fresh && (
        <span className="absolute -top-2 right-3 inline-flex items-center gap-0.5 rounded-full bg-fresh px-1.5 py-[0.125rem] text-[0.625rem] font-semibold text-white">
          <Plus className="h-2.5 w-2.5" strokeWidth={3} />
          {t("badge_new")}
        </span>
      )}
    </div>
  );
}

export function ClaimCard({
  node,
  branch,
  selected,
  dimmed,
  hit,
  showStamps = true,
}: {
  node: MapNode;
  branch: string;
  /** the video lands its own animated stamps */
  showStamps?: boolean;
} & CardState) {
  const t = useT();
  const benched = node.badges.lever;
  const taste = node.badges.taste > 0;
  const off = node.narrative && node.narrative !== "ACTIVE";
  const pending = node.epistemic === "PENDING";
  return (
    <div
      style={{ width: `${SIZE.claim.w}rem`, height: `${SIZE.claim.h}rem` }}
      className={cn(
        "relative flex flex-col overflow-visible rounded-xl border bg-surface pl-3.5 pr-3 pt-2.5 pb-2 shadow-card transition-[opacity,box-shadow]",
        benched ? "border-2 border-redpen bg-redpen-soft" : pending ? "border-dashed border-line-strong" : "border-line",
        selected && "ring-2 ring-pencil ring-offset-2 ring-offset-canvas",
        hit && "ring-2 ring-marker ring-offset-2 ring-offset-canvas",
        dimmed && "opacity-25",
      )}
    >
      <span className="absolute inset-y-2 left-0 w-[3px] rounded-r-full" style={{ background: branch }} />
      <div className="flex h-[1.375rem] items-center gap-1.5 font-mono text-[0.6875rem] text-ink-3">
        <span className="font-medium text-ink-2">{node.id}</span>
        <span>·</span>
        <span>{t(`ep_${node.epistemic}`)}</span>
        {off && (
          <>
            <span>·</span>
            <span className={benched ? "font-medium text-redpen" : ""}>{t(`nar_${node.narrative}`)}</span>
          </>
        )}
        <span className="ml-auto flex items-center gap-1">
          {node.roles.length > 0 && (
            <span className="inline-flex items-center gap-1 rounded-full bg-ink px-1.5 py-[0.1875rem] font-sans text-[0.625rem] font-semibold leading-none text-bg">
              <Crown className="h-2.5 w-2.5" strokeWidth={2.5} />
              {node.roles.map((r) => t(`role_${r}`)).join(" · ")}
            </span>
          )}
          <BadgeIcons node={node} />
        </span>
      </div>
      <p
        className={cn(
          "mt-1.5 line-clamp-2 font-display text-[0.9375rem] font-semibold leading-[1.24] [font-stretch:93%]",
          off && !benched && "text-ink-2",
        )}
      >
        {node.title}
      </p>
      {showStamps && (benched || taste) && (
        <span className="pointer-events-none absolute -top-[1.05rem] right-2 flex flex-row-reverse items-start gap-1.5">
          {benched && <Stamp kind="lever" />}
          {taste && <Stamp kind="taste" />}
        </span>
      )}
      {node.badges.new && !benched && !taste && (
        <span className="absolute -top-[0.7rem] right-3 inline-flex items-center gap-0.5 rounded-full bg-fresh px-1.5 py-[0.125rem] text-[0.625rem] font-semibold text-white">
          <Plus className="h-2.5 w-2.5" strokeWidth={3} />
          {t("badge_new")}
        </span>
      )}
    </div>
  );
}
