// The two callouts that ask the researcher for a decision. The inspector shows them;
// the product video animates the very same markup.
import { Highlighter, Snowflake } from "lucide-react";
import type { Commit, Lever, TasteHit } from "@/data";
import { splitMatch } from "@/diff";
import { fmtTime, useLang, useT } from "@/i18n";
import { cn } from "./ui";

export function BenchCallout({
  lever,
  demotedBy,
  tz,
  className,
}: {
  lever: Lever;
  demotedBy?: Commit;
  tz?: number | null;
  className?: string;
}) {
  const t = useT();
  const lang = useLang();
  return (
    <section className={cn("rounded-xl border-2 border-redpen bg-redpen-soft p-4", className)}>
      <h3 className="flex items-center gap-1.5 font-display text-[1.125rem] font-extrabold text-redpen [font-stretch:88%]">
        <Snowflake className="h-4 w-4" strokeWidth={2.5} />
        {t("sec_bench")}
      </h3>
      <p className="mt-1.5 text-[0.9375rem] font-medium leading-snug text-ink">{t("bench_body")}</p>
      <p className="mt-2 text-[0.8125rem] text-ink-2">
        {lever.roles_held.length > 0 && <>{t("bench_was", { roles: lever.roles_held.map((r) => t(`role_${r}`)).join(lang === "zh" ? "、" : ", ") })} · </>}
        {fmtTime(lever.demoted_at, tz)} · {t(demotedBy?.by ?? "by_main")} · {t(`cause_${lever.cause}`)}
      </p>
      <p className="mt-3 border-t border-redpen/25 pt-3 text-[0.875rem] font-semibold text-redpen">{t("bench_question")}</p>
    </section>
  );
}

export function TasteCallout({ hit, className }: { hit: TasteHit; className?: string }) {
  const t = useT();
  const parts = splitMatch(hit.excerpt, hit.match);
  return (
    <section className={cn("rounded-xl border border-line bg-surface-2 p-4", className)}>
      <h3 className="flex items-center gap-1.5 font-display text-[1.125rem] font-extrabold [font-stretch:88%]">
        <span className="marker inline-flex items-center gap-1">
          <Highlighter className="h-4 w-4" strokeWidth={2.5} />
          {t("sec_taste")}
        </span>
      </h3>
      <dl className="mt-2.5 space-y-2 text-[0.875rem] leading-snug">
        <div>
          <dt className="text-[0.6875rem] uppercase tracking-[0.12em] text-ink-3">{t("taste_rule")} · {hit.rule}</dt>
          <dd className="font-medium">{hit.rule_text}</dd>
        </div>
        {hit.quote && (
          <div>
            <dt className="text-[0.6875rem] uppercase tracking-[0.12em] text-ink-3">{t("taste_quote")}</dt>
            <dd className="italic text-ink-2">“{hit.quote}”</dd>
          </div>
        )}
        <div>
          <dt className="text-[0.6875rem] uppercase tracking-[0.12em] text-ink-3">{t("taste_found")}</dt>
          <dd>
            …{parts ? (
              <>
                {parts[0]}
                <mark className="marker font-semibold">{parts[1]}</mark>
                {parts[2]}
              </>
            ) : (
              hit.excerpt
            )}
            …
          </dd>
        </div>
      </dl>
    </section>
  );
}
