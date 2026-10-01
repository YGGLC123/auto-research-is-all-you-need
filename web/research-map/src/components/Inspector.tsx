import { ArrowRightLeft, Bot, Crown, FileText, Image, MousePointerClick, Scissors, ShieldCheck, ShieldX, Stamp as StampIcon, Ticket, TriangleAlert, UserRound, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { Commit, MapModel, MapNode } from "@/data";
import { churn, redline } from "@/diff";
import { fmtDateTime, useLang, useT } from "@/i18n";
import { BadgeIcons } from "./badges";
import { BenchCallout, TasteCallout } from "./callouts";
import { Button, cn, Pill, Scroll } from "./ui";

function Section({ title, icon, children, className }: { title: string; icon?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("border-t border-line px-5 py-4", className)}>
      <h3 className="mb-2.5 flex items-center gap-1.5 text-[0.6875rem] font-semibold uppercase tracking-[0.12em] text-ink-3">
        {icon}
        {title}
      </h3>
      {children}
    </section>
  );
}

function Redline({ before, after, clean }: { before: string; after: string; clean: boolean }) {
  if (clean) return <p className="text-[0.9375rem] leading-relaxed">{after}</p>;
  const segs = redline(before, after);
  if (churn(segs) > 0.55) {
    // mostly rewritten: the old sentence struck through, the new one under it
    return (
      <div className="space-y-2 text-[0.9375rem] leading-relaxed">
        <p className="text-redpen line-through decoration-redpen decoration-[1.5px] opacity-75">{before}</p>
        <p className="rounded-md border-l-[3px] border-pencil bg-pencil-soft px-2.5 py-1.5 text-ink">{after}</p>
      </div>
    );
  }
  return (
    <p className="text-[0.9375rem] leading-relaxed">
      {segs.map((s, i) =>
        s.kind === "same" ? (
          <span key={i}>{s.text}</span>
        ) : s.kind === "del" ? (
          <del key={i} className="mr-1 text-redpen decoration-redpen decoration-[1.5px] opacity-80">
            {s.text.trim()}
          </del>
        ) : (
          <ins key={i} className="rounded-[2px] bg-pencil-soft px-0.5 text-pencil no-underline [box-decoration-break:clone]">
            {s.text.trim()}
          </ins>
        ),
      )}
    </p>
  );
}

function CommitRow({ c, tz }: { c: Commit; tz?: number | null }) {
  const t = useT();
  const lang = useLang();
  const human = c.by === "by_user";
  return (
    <li className="relative pl-6">
      <span className={cn("absolute left-0 top-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full", human ? "bg-stamp text-white" : "bg-ink text-bg")}>
        {human ? <UserRound className="h-2.5 w-2.5" /> : <Bot className="h-2.5 w-2.5" />}
      </span>
      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[0.75rem] text-ink-3">
        <span className="font-mono text-ink-2">{c.at_full ? fmtDateTime(c.at_full, lang, tz) : c.at}</span>
        <span>·</span>
        <span className="font-medium text-ink-2">{t(c.by)}</span>
        <Pill className="h-5">{t(`kind_${c.kind}`)}</Pill>
        <Pill className={cn("h-5", c.cause === "cause_unforced" && "border-clash/40 text-clash", c.cause === "cause_forced" && "border-pencil/40 text-pencil")}>
          {t(c.cause)}
        </Pill>
      </div>
      <p className="mt-1 text-[0.8125rem] leading-snug">{c.message}</p>
    </li>
  );
}

export function Inspector({ model, node, onClose, onPick }: { model: MapModel; node: MapNode | null; onClose?: () => void; onPick: (id: string) => void }) {
  const t = useT();
  const tz = model.tz_offset_minutes;
  const [clean, setClean] = useState(false);
  const byId = new Map(model.nodes.map((n) => [n.id, n]));

  if (!node) {
    return (
      <Scroll className="h-full">
        <div className="px-5 py-6">
          <MousePointerClick className="h-6 w-6 text-ink-3" />
          <h2 className="mt-3 font-display text-[1.25rem] font-bold leading-tight [font-stretch:92%]">{t("insp_empty_title")}</h2>
          <p className="mt-2 text-[0.875rem] leading-relaxed text-ink-2">{t("insp_empty_body")}</p>
        </div>
        {model.dropped.length > 0 && (
          <Section title={t("dropped_heading")} icon={<Scissors className="h-3.5 w-3.5" />}>
            <ul className="space-y-3">
              {model.dropped.map((d) => (
                <li key={d.id} className="text-[0.8125rem] leading-snug">
                  <p>
                    <span className="mr-1.5 font-mono text-[0.75rem] text-ink-3 line-through">{d.id}</span>
                    <span className="text-ink-2 line-through decoration-redpen/60">{d.title}</span>
                  </p>
                  {d.reason && <p className="mt-1 text-ink">{t("dropped_reason", { reason: d.reason })}</p>}
                </li>
              ))}
            </ul>
          </Section>
        )}
        {model.staleness.count > 0 && (
          <Section title={t("stale", { n: model.staleness.count })} icon={<TriangleAlert className="h-3.5 w-3.5" />}>
            <ul className="space-y-1 font-mono text-[0.75rem] text-ink-2">
              {model.staleness.files.map((f) => (
                <li key={f.path}>{f.path}</li>
              ))}
            </ul>
          </Section>
        )}
      </Scroll>
    );
  }

  const ch = node.change;
  const fields = (["title", "statement", "summary"] as const).filter((f) => ch?.before && f in ch.before);
  const stateChanged = ch?.before?.state && ch.after?.state;
  const commits = ch?.commits ?? [];
  const fullCommits = commits.map((c) => model.log?.find((l) => l.id === c.id) ?? c);
  const lever = node.lever;
  const demotedBy = lever ? model.log?.find((l) => lever.demoted_commit.startsWith(l.id) || l.id.startsWith(lever.demoted_commit.slice(0, 12))) : undefined;
  const ev = node.evidence;

  return (
    <Scroll className="h-full">
      <header className="px-5 pt-5 pb-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-[0.75rem] font-medium text-ink-2">{node.id}</span>
            {node.epistemic && <Pill dashed={node.epistemic === "PENDING"}>{t(`ep_${node.epistemic}`)}</Pill>}
            {node.narrative && <Pill className={node.badges.lever ? "border-redpen/50 text-redpen" : ""}>{t(`nar_${node.narrative}`)}</Pill>}
            {node.roles.map((r) => (
              <Pill key={r} className="border-ink bg-ink text-bg">
                <Crown className="h-2.5 w-2.5" />
                {t(`role_${r}`)}
              </Pill>
            ))}
          </div>
          {onClose && (
            <Button variant="ghost" size="icon" onClick={onClose} aria-label={t("close")} className="-mr-2 -mt-1 shrink-0">
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
        <h2 className="mt-3 font-display text-[1.375rem] font-bold leading-[1.18] tracking-[-0.005em] [font-stretch:90%]">{node.title}</h2>
        <div className="mt-3">
          <BadgeIcons node={node} size="md" />
        </div>
      </header>

      {lever && <BenchCallout lever={lever} demotedBy={demotedBy} tz={tz} className="mx-5 mb-4" />}

      {node.taste.map((hit) => (
        <TasteCallout key={hit.rule} hit={hit} className="mx-5 mb-4" />
      ))}

      {ch?.moved_from && (
        <p className="mx-5 mb-4 flex items-start gap-2 text-[0.8125rem] text-ink-2">
          <ArrowRightLeft className="mt-0.5 h-3.5 w-3.5 shrink-0 text-pencil" />
          {t("moved_from", { from: ch.moved_from })}
        </p>
      )}
      {node.live_bet && (
        <p className="mx-5 mb-4 flex items-start gap-2 text-[0.8125rem] text-ink-2">
          <Ticket className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {t("live_bet", { cond: node.live_bet.resolution_condition ?? "" })}
        </p>
      )}
      {node.signed.length > 0 && (
        <p className="mx-5 mb-4 flex items-start gap-2 text-[0.8125rem] text-stamp">
          <StampIcon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {t("signed", { ref: node.signed.join(", ") })}
        </p>
      )}

      {(fields.length > 0 || stateChanged) && (
        <Section title={t("sec_changed")} icon={<FileText className="h-3.5 w-3.5" />}>
          <div className="space-y-3">
            {fields.map((f) => (
              <div key={f}>
                <p className="mb-1 text-[0.6875rem] font-medium text-ink-3">{t(`field_${f}`)}</p>
                <Redline before={ch!.before![f] ?? ""} after={ch!.after![f] ?? ""} clean={clean} />
              </div>
            ))}
            {stateChanged && (
              <div>
                <p className="mb-1 text-[0.6875rem] font-medium text-ink-3">{t("field_state")}</p>
                <p className="text-[0.875rem]">
                  <del className="text-redpen decoration-redpen opacity-80">{t(`nar_${ch!.before!.state!.narrative}`)}</del>
                  <span className="mx-1.5 text-ink-3">→</span>
                  <span className="font-semibold">{t(`nar_${ch!.after!.state!.narrative}`)}</span>
                </p>
              </div>
            )}
          </div>
          {fields.length > 0 && (
            <Button variant="outline" size="sm" className="mt-3" onClick={() => setClean((v) => !v)}>
              {clean ? t("diff_show_marks") : t("diff_show_clean")}
            </Button>
          )}
        </Section>
      )}

      {!fields.includes("statement") && (node.statement || node.summary) && (
        <Section title={node.statement ? t("sec_statement") : t("sec_summary")}>
          <p className="text-[0.9375rem] leading-relaxed text-ink">{node.statement || node.summary}</p>
        </Section>
      )}

      {node.type !== "narrative" && node.id !== model.root && (
        <Section title={t("sec_evidence")} icon={<ShieldCheck className="h-3.5 w-3.5" />}>
          {ev && (ev.supports.length || ev.refutes.length || ev.figures.length) ? (
            <ul className="space-y-1.5 text-[0.8125rem] leading-snug">
              {ev.supports.map((s) => (
                <li key={s} className="flex gap-2">
                  <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-stamp" />
                  <span>{s}</span>
                </li>
              ))}
              {ev.refutes.map((s) => (
                <li key={s} className="flex gap-2 text-clash">
                  <ShieldX className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>{s}</span>
                </li>
              ))}
              {ev.figures.map((s) => (
                <li key={s} className="flex gap-2">
                  <Image className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-3" />
                  <span>{s}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[0.8125rem] text-ink-3">{t("evidence_none")}</p>
          )}
        </Section>
      )}

      {fullCommits.length > 0 && (
        <Section title={t("sec_commits")}>
          <ol className="space-y-3">
            {fullCommits.map((c) => (
              <CommitRow key={c.id} c={c} tz={tz} />
            ))}
          </ol>
        </Section>
      )}

      {node.children.length > 0 && (
        <Section title={t("children_stat", { n: node.children.length })}>
          <ul className="space-y-1">
            {node.children.map((id) => {
              const child = byId.get(id);
              if (!child) return null;
              return (
                <li key={id}>
                  <button type="button" onClick={() => onPick(id)} className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-[0.8125rem] hover:bg-surface-2">
                    <span className="font-mono text-[0.75rem] text-ink-3">{id}</span>
                    <span className="line-clamp-1 flex-1">{child.title}</span>
                    <BadgeIcons node={child} />
                  </button>
                </li>
              );
            })}
          </ul>
        </Section>
      )}
      <div className="h-6" />
    </Scroll>
  );
}
