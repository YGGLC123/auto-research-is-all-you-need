import { AArrowDown, AArrowUp, Languages, Moon, ReceiptText, Search, SunMedium, TriangleAlert } from "lucide-react";
import type { MapModel } from "@/data";
import { fmtDateTime, useLang, useT } from "@/i18n";
import { Button, cn, Segmented, Tip } from "./ui";

export type Filter = "all" | "changed" | "needs";

export function Header({
  model,
  filter,
  onFilter,
  changedCount,
  needsCount,
  query,
  onQuery,
  dark,
  onDark,
  onLang,
  onScale,
  onReceipt,
}: {
  model: MapModel;
  filter: Filter;
  onFilter: (f: Filter) => void;
  changedCount: number;
  needsCount: number;
  query: string;
  onQuery: (q: string) => void;
  dark: boolean;
  onDark: () => void;
  onLang: () => void;
  onScale: (step: 1 | -1) => void;
  onReceipt: () => void;
}) {
  const t = useT();
  const lang = useLang();
  const tz = model.tz_offset_minutes;
  const since = model.since.at ? t("since_from", { date: fmtDateTime(model.since.at, lang, tz) }) : t("since_all");
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-surface px-4 py-2.5 lg:px-5">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-ink font-display text-[0.8125rem] font-extrabold text-bg [font-stretch:80%]" aria-hidden>
          ar
        </span>
        <div className="min-w-0">
          <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-ink-3">{t("app")}</p>
          <h1 className="truncate font-display text-[1rem] font-bold leading-tight [font-stretch:92%]">{model.project}</h1>
        </div>
        <div className="ml-2 hidden items-center gap-2 rounded-full border border-line bg-surface-2 py-1 pl-3 pr-1.5 text-[0.75rem] md:flex">
          <span className="font-semibold text-ink">{t("since_label")}</span>
          <span className="text-ink-2">{since}</span>
          {model.staleness.count > 0 && (
            <Tip label={t("stale", { n: model.staleness.count })}>
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-clash-soft text-clash" aria-label={t("stale", { n: model.staleness.count })}>
                <TriangleAlert className="h-3 w-3" />
              </span>
            </Tip>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" className="xl:hidden" onClick={onReceipt}>
          <ReceiptText className="h-3.5 w-3.5" />
          {t("receipt_toggle")}
        </Button>
        <Segmented<Filter>
          label={t("filter_all")}
          value={filter}
          onChange={onFilter}
          items={[
            { value: "all", label: t("filter_all") },
            { value: "changed", label: t("filter_changed"), count: changedCount },
            { value: "needs", label: t("filter_needs"), count: needsCount, tone: "alarm" },
          ]}
        />
        <label className="relative hidden sm:block">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-3" />
          <input
            type="search"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder={t("search")}
            aria-label={t("search")}
            className="h-9 w-44 rounded-[0.6rem] border border-line bg-surface-2 pl-8 pr-2.5 text-[0.8125rem] text-ink outline-none placeholder:text-ink-3 focus:border-pencil focus:bg-surface"
          />
        </label>
        <div className={cn("flex items-center rounded-[0.6rem] border border-line bg-surface p-0.5")}>
          <Tip label={t("lang_switch")}>
            <Button variant="ghost" size="sm" onClick={onLang} aria-label={t("lang_switch")} className="h-8 px-2">
              <Languages className="h-3.5 w-3.5" />
              <span className="font-mono text-[0.6875rem]">{lang === "zh" ? "EN" : "中"}</span>
            </Button>
          </Tip>
          <Tip label={t("text_smaller")}>
            <Button variant="ghost" size="icon" onClick={() => onScale(-1)} aria-label={t("text_smaller")}>
              <AArrowDown className="h-4 w-4" />
            </Button>
          </Tip>
          <Tip label={t("text_bigger")}>
            <Button variant="ghost" size="icon" onClick={() => onScale(1)} aria-label={t("text_bigger")}>
              <AArrowUp className="h-4 w-4" />
            </Button>
          </Tip>
          <Tip label={dark ? t("theme_light") : t("theme_dark")}>
            <Button variant="ghost" size="icon" onClick={onDark} aria-label={dark ? t("theme_light") : t("theme_dark")}>
              {dark ? <SunMedium className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>
          </Tip>
        </div>
      </div>
    </header>
  );
}
