import { ArrowRightLeft, Crown, Highlighter, PenLine, Plus, ReceiptText, Scissors, Snowflake, Zap, type LucideIcon } from "lucide-react";
import type { Commit, MapModel, MapNode } from "@/data";
import { fmtDateTime, fmtTime, useLang, useT } from "@/i18n";
import { cn } from "./ui";

const OP_ORDER = ["detach_node", "move_node", "add_node", "patch_fields", "set_role"];
const OP_ICON: Record<string, LucideIcon> = {
  bench: Snowflake,
  detach_node: Scissors,
  move_node: ArrowRightLeft,
  add_node: Plus,
  patch_fields: PenLine,
  set_role: Crown,
};

export type ReceiptLine = { commit: Commit; op: string; subject: string; time: string };

export function receiptLines(model: MapModel): ReceiptLine[] {
  const tz = model.tz_offset_minutes;
  const log = model.log ?? [];
  // the commit that benched a result gets its own red line: it is the one to see
  const benchedBy = new Map<string, string>();
  for (const n of model.nodes) if (n.lever?.demoted_commit) benchedBy.set(n.lever.demoted_commit, n.id);
  return log.map((c) => {
    const time = fmtTime(c.at_full || c.at, tz);
    for (const [full, id] of benchedBy) if (full.startsWith(c.id)) return { commit: c, op: "bench", subject: id, time };
    const ops = c.ops ?? [];
    const op = OP_ORDER.find((o) => ops.some((x) => x.op === o)) ?? "other";
    const subject = ops.find((x) => x.op === op)?.id ?? c.nodes?.[0] ?? "";
    return { commit: c, op, subject, time };
  });
}

export function needsList(model: MapModel): { node: MapNode; kind: "lever" | "taste" | "conflict" }[] {
  const out: { node: MapNode; kind: "lever" | "taste" | "conflict" }[] = [];
  for (const n of model.nodes) {
    if (n.badges.lever) out.push({ node: n, kind: "lever" });
    if (n.badges.taste > 0) out.push({ node: n, kind: "taste" });
    if (n.badges.conflict || n.badges.refute > 0) out.push({ node: n, kind: "conflict" });
  }
  const rank = { lever: 0, taste: 1, conflict: 2 };
  return out.sort((a, b) => rank[a.kind] - rank[b.kind]);
}

function Rule({ double }: { double?: boolean }) {
  return (
    <div
      aria-hidden
      className={cn("my-3 h-0 border-t-[1.5px] border-dashed border-line-strong", double && "border-double border-t-[3px]")}
    />
  );
}

/** A tiny barcode drawn from the head commit id: the receipt is literally keyed to the story's state. */
export function Barcode({ id }: { id: string }) {
  const bars = id
    .slice(0, 24)
    .split("")
    .flatMap((ch) => {
      const v = parseInt(ch, 16) || 0;
      return [1 + (v & 1), 1 + ((v >> 1) & 1), 1 + ((v >> 2) & 1), 2 + ((v >> 3) & 1)];
    });
  let x = 0;
  const rects = bars.map((w, i) => {
    const r = i % 2 === 0 ? <rect key={i} x={x} y={0} width={w} height={28} /> : null;
    x += w;
    return r;
  });
  return (
    <svg viewBox={`0 0 ${x} 28`} className="h-7 w-full fill-ink" preserveAspectRatio="none" aria-hidden>
      {rects}
    </svg>
  );
}

export function Receipt({
  model,
  onPick,
  printed,
  showTotals = true,
  showNeeds = true,
  showFooter = true,
  className,
}: {
  model: MapModel;
  onPick?: (id: string) => void;
  /** how many line items are printed so far (video); all when undefined */
  printed?: number;
  showTotals?: boolean;
  showNeeds?: boolean;
  showFooter?: boolean;
  className?: string;
}) {
  const t = useT();
  const lang = useLang();
  const tz = model.tz_offset_minutes;
  const lines = receiptLines(model);
  const shown = printed === undefined ? lines : lines.slice(0, printed);
  const needs = needsList(model);
  const c = model.counts;
  const totals: [string, number][] = [
    ["count_added", c.added ?? 0],
    ["count_changed", c.changed ?? 0],
    ["count_moved", c.moved ?? 0],
    ["count_dropped", c.dropped ?? 0],
  ];
  const range = `${model.since.at ? fmtDateTime(model.since.at, lang, tz) : t("since_all")} → ${fmtDateTime(model.head_at, lang, tz)}`;
  const NEED_ICON = { lever: Snowflake, taste: Highlighter, conflict: Zap };
  const NEED_TONE = {
    lever: "border-[1.5px] border-redpen text-redpen",
    taste: "bg-marker text-marker-ink",
    conflict: "bg-clash-soft text-clash",
  };
  const NEED_LABEL = { lever: "badge_lever", taste: "badge_taste", conflict: "badge_conflict" };

  return (
    <section
      aria-label={t("receipt_title")}
      className={cn(
        "receipt-edge relative bg-receipt px-5 pt-5 pb-8 font-mono text-[0.75rem] leading-[1.55] text-ink shadow-card",
        "rounded-t-xl",
        className,
      )}
    >
      <div className="flex items-center justify-between text-[0.625rem] uppercase tracking-[0.2em] text-ink-3">
        <span className="inline-flex items-center gap-1.5">
          <ReceiptText className="h-3.5 w-3.5" />
          auto-research
        </span>
        <span>
          {t("receipt_no")} {model.head.slice(0, 7).toUpperCase()}
        </span>
      </div>
      <h2 className="mt-3 font-display text-[1.625rem] font-extrabold leading-none tracking-[-0.01em] [font-stretch:86%]">
        {t("receipt_title")}
      </h2>
      <p className="mt-2 line-clamp-2 font-sans text-[0.8125rem] font-medium leading-snug text-ink-2">{model.project}</p>
      <p className="mt-1 text-[0.6875rem] text-ink-3">{range}</p>
      <Rule />

      {lines.length === 0 ? (
        <p className="py-2 text-center text-ink-2">{t("receipt_quiet")}</p>
      ) : (
        <ol className="space-y-1.5">
          {shown.map(({ commit, op, subject, time }) => {
            const Icon = OP_ICON[op] ?? PenLine;
            const bench = op === "bench";
            return (
              <li key={commit.id}>
                <button
                  type="button"
                  onClick={() => subject && onPick?.(subject)}
                  className={cn(
                    "grid w-full grid-cols-[2.6rem_1rem_2.4rem_1fr] items-start gap-x-1.5 rounded-md text-left hover:bg-surface-2 focus-visible:bg-surface-2",
                    bench && "-mx-1.5 w-[calc(100%+0.75rem)] bg-redpen-soft px-1.5 py-1 hover:bg-redpen-soft",
                  )}
                >
                  <span className={bench ? "text-redpen" : "text-ink-3"}>{time}</span>
                  <Icon
                    className={cn(
                      "mt-[0.2rem] h-3.5 w-3.5",
                      bench || op === "detach_node" ? "text-redpen" : op === "add_node" ? "text-fresh" : "text-pencil",
                    )}
                    strokeWidth={bench ? 2.5 : 2}
                  />
                  <span className={cn("font-medium", bench && "text-redpen")}>{subject}</span>
                  {/* the icon already says what kind of edit it was; only the benching is spelled out */}
                  <span className="line-clamp-2 text-ink" title={`${t(`op_${op}`)}: ${commit.message}`}>
                    {bench ? (
                      <span className="font-semibold text-redpen">{t("op_bench")} </span>
                    ) : (
                      <span className="sr-only">{t(`op_${op}`)}: </span>
                    )}
                    {commit.message}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      )}

      {showTotals && (
        <>
          <Rule />
          <div className="flex items-baseline justify-between text-[0.6875rem] uppercase tracking-[0.14em] text-ink-3">
            <span>{t("receipt_total")}</span>
            <span>{t("receipt_commits", { n: lines.length })}</span>
          </div>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
            {totals.map(([key, n]) => (
              <div key={key} className="flex items-baseline justify-between">
                <dt className="text-ink-2">{t(key)}</dt>
                <dd className="font-display text-[1.0625rem] font-bold tabular-nums">×{n}</dd>
              </div>
            ))}
          </dl>
        </>
      )}

      {showNeeds && needs.length > 0 && (
        <>
          <Rule double />
          <div className="flex items-center justify-between">
            <span className="font-display text-[1.0625rem] font-extrabold [font-stretch:90%]">{t("receipt_needs")}</span>
            <span className="inline-flex h-6 min-w-6 items-center justify-center rounded-full bg-redpen px-1.5 font-display text-[0.8125rem] font-bold text-white">
              {needs.length}
            </span>
          </div>
          <ul className="mt-2 space-y-1.5">
            {needs.map(({ node, kind }) => {
              const Icon = NEED_ICON[kind];
              return (
                <li key={`${kind}-${node.id}`}>
                  <button
                    type="button"
                    onClick={() => onPick?.(node.id)}
                    className="flex w-full items-start gap-2 rounded-md p-1 text-left hover:bg-surface-2 focus-visible:bg-surface-2"
                  >
                    <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-[0.3rem] px-1.5 py-[0.125rem] font-display text-[0.6875rem] font-bold", NEED_TONE[kind])}>
                      <Icon className="h-3 w-3" strokeWidth={2.5} />
                      {t(NEED_LABEL[kind])}
                    </span>
                    <span className="line-clamp-2 font-sans text-[0.75rem] leading-snug text-ink-2">
                      <span className="font-mono font-medium text-ink">{node.id}</span> {node.title}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}

      {showFooter && (
        <>
          <Rule />
          <p className="text-center font-display text-[0.875rem] font-bold [font-stretch:92%]">{t("receipt_thanks")}</p>
          <p className="mt-0.5 text-center text-[0.6875rem] uppercase tracking-[0.24em] text-ink-3">{t("receipt_bye")}</p>
          <div className="mx-auto mt-3 w-4/5 opacity-80">
            <Barcode id={model.head} />
          </div>
        </>
      )}
    </section>
  );
}
