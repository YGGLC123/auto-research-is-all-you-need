import { createContext, useContext } from "react";

export type Lang = "zh" | "en";

const zh = {
  app: "研究地图",
  since_label: "你不在的时候",
  since_from: "{date} 以来",
  since_all: "从头算起",
  stale: "有 {n} 个文件比这张地图新，地图可能没跟上",
  filter_all: "全部",
  filter_changed: "改了啥",
  filter_needs: "要你拍板",
  search: "搜索论断",
  search_none: "没有匹配的论断",
  fit: "回到全景",
  zoom_in: "放大",
  zoom_out: "缩小",
  text_bigger: "字大一点",
  text_smaller: "字小一点",
  theme_dark: "换深色",
  theme_light: "换浅色",
  receipt_title: "昨夜小票",
  receipt_toggle: "小票",
  receipt_close: "收起小票",
  receipt_no: "单号",
  receipt_commits: "共 {n} 笔改动",
  receipt_total: "合计",
  receipt_needs: "要你拍板",
  receipt_thanks: "AI 熬夜，你拍板",
  receipt_bye: "谢谢惠顾",
  receipt_quiet: "这段时间故事没动过",
  op_bench: "雪藏",
  op_add_node: "新增",
  op_patch_fields: "改写",
  op_move_node: "挪窝",
  op_detach_node: "删了",
  op_set_role: "换角色",
  op_other: "改动",
  count_added: "新增",
  count_changed: "改写",
  count_moved: "挪窝",
  count_dropped: "删了",
  badge_new: "新增",
  badge_changed: "改写",
  badge_moved: "挪窝了",
  badge_conflict: "被打脸",
  badge_no_evidence: "没证据撑腰",
  badge_no_figure: "缺张图",
  badge_lever: "被雪藏",
  badge_live_bet: "待开奖",
  badge_signed: "你拍板的",
  badge_taste: "踩你雷区",
  ep_HELD: "成立",
  ep_PENDING: "待定",
  ep_REFUTED: "被推翻",
  ep_KILLED: "被推翻",
  nar_ACTIVE: "在主线",
  nar_DEMOTED: "下了主线",
  nar_MERGED: "已并入",
  nar_DROPPED: "被删了",
  nar_KILLED: "被推翻",
  role_headline: "头条",
  role_flagship: "王牌",
  role_backbone: "骨架",
  role_empirical_core: "实证核心",
  role_foil: "陪衬",
  by_main: "AI",
  by_user: "你",
  by_chronicler: "AI 记录员",
  by_backfill: "历史回填",
  kind_refine: "精修",
  kind_restructure: "改结构",
  kind_overthrow: "换主线",
  kind_backfill: "回填",
  cause_none: "小修",
  cause_unforced: "AI 自己选的",
  cause_forced: "被证据逼的",
  cause_candidate: "是不是被逼的，等你确认",
  cause_unknown: "说不清",
  insp_empty_title: "点一张卡片，看它经历了什么",
  insp_empty_body: "红色的是被雪藏的结果，黄色的是踩了你雷区的句子。",
  sec_bench: "被雪藏",
  bench_body: "从没被推翻，也没被哪个新结果取代，却被悄悄挪下了主线。",
  bench_was: "它原来是{roles}",
  bench_question: "留在附录，还是放回主线？这得你拍板。",
  sec_taste: "踩你雷区",
  taste_rule: "你的规则",
  taste_quote: "你的原话",
  taste_found: "AI 写下的",
  sec_changed: "改了什么",
  sec_statement: "正文",
  sec_summary: "简介",
  sec_evidence: "证据",
  sec_commits: "谁、什么时候改的",
  moved_from: "从「{from}」挪到这里",
  evidence_supports: "撑腰的",
  evidence_refutes: "打脸的",
  evidence_figures: "图",
  evidence_none: "还没有证据撑腰",
  live_bet: "待开奖：{cond}",
  signed: "你拍过板：{ref}",
  field_title: "标题",
  field_statement: "正文",
  field_summary: "简介",
  field_state: "状态",
  diff_show_clean: "只看改后",
  diff_show_marks: "看改动痕迹",
  children_stat: "{n} 条",
  dropped_heading: "被删了",
  dropped_reason: "理由：{reason}",
  needs_count: "要你拍板 {n} 项",
  legend: "图例",
  lang_switch: "English",
  close: "关闭",
};

const en: Record<keyof typeof zh, string> = {
  app: "Research map",
  since_label: "While you were away",
  since_from: "since {date}",
  since_all: "from the start",
  stale: "{n} file(s) are newer than this map. It may lag the work.",
  filter_all: "All",
  filter_changed: "What changed",
  filter_needs: "Needs you",
  search: "Search claims",
  search_none: "No matching claims",
  fit: "Fit to screen",
  zoom_in: "Zoom in",
  zoom_out: "Zoom out",
  text_bigger: "Larger text",
  text_smaller: "Smaller text",
  theme_dark: "Dark mode",
  theme_light: "Light mode",
  receipt_title: "Overnight receipt",
  receipt_toggle: "Receipt",
  receipt_close: "Hide receipt",
  receipt_no: "No.",
  receipt_commits: "{n} commits",
  receipt_total: "Total",
  receipt_needs: "Needs your call",
  receipt_thanks: "The AI pulled the all-nighter. You make the call.",
  receipt_bye: "Thank you, come again",
  receipt_quiet: "Quiet night. The story didn't change.",
  op_bench: "Benched",
  op_add_node: "Added",
  op_patch_fields: "Rewrote",
  op_move_node: "Moved",
  op_detach_node: "Cut",
  op_set_role: "Recast",
  op_other: "Edited",
  count_added: "Added",
  count_changed: "Rewritten",
  count_moved: "Moved",
  count_dropped: "Cut",
  badge_new: "New",
  badge_changed: "Rewritten",
  badge_moved: "Moved",
  badge_conflict: "Called out",
  badge_no_evidence: "No receipts",
  badge_no_figure: "Needs a figure",
  badge_lever: "Benched",
  badge_live_bet: "Open bet",
  badge_signed: "Your call",
  badge_taste: "Not your style",
  ep_HELD: "Holds",
  ep_PENDING: "Pending",
  ep_REFUTED: "Refuted",
  ep_KILLED: "Refuted",
  nar_ACTIVE: "In the story",
  nar_DEMOTED: "Sidelined",
  nar_MERGED: "Merged",
  nar_DROPPED: "Cut",
  nar_KILLED: "Refuted",
  role_headline: "Headline",
  role_flagship: "Flagship",
  role_backbone: "Backbone",
  role_empirical_core: "Empirical core",
  role_foil: "Foil",
  by_main: "AI",
  by_user: "You",
  by_chronicler: "AI scribe",
  by_backfill: "Backfill",
  kind_refine: "Polish",
  kind_restructure: "Restructure",
  kind_overthrow: "New main line",
  kind_backfill: "Backfill",
  cause_none: "Light edit",
  cause_unforced: "AI's choice",
  cause_forced: "Forced by evidence",
  cause_candidate: "Forced? Needs your sign-off",
  cause_unknown: "Unclear",
  insp_empty_title: "Pick a card to see what happened to it",
  insp_empty_body: "Red marks a benched result. Yellow marks a sentence that breaks your taste rules.",
  sec_bench: "Benched",
  bench_body: "Never refuted, never superseded. Sidelined anyway.",
  bench_was: "It used to be the {roles}",
  bench_question: "Keep it in the appendix, or bring it back? Your call.",
  sec_taste: "Not your style",
  taste_rule: "Your rule",
  taste_quote: "Your words",
  taste_found: "What the AI wrote",
  sec_changed: "What changed",
  sec_statement: "Statement",
  sec_summary: "Summary",
  sec_evidence: "Evidence",
  sec_commits: "Who changed it, and when",
  moved_from: "Moved here from “{from}”",
  evidence_supports: "Backed by",
  evidence_refutes: "Pushed back by",
  evidence_figures: "Figures",
  evidence_none: "No receipts yet",
  live_bet: "Open bet: {cond}",
  signed: "Your call: {ref}",
  field_title: "Title",
  field_statement: "Statement",
  field_summary: "Summary",
  field_state: "State",
  diff_show_clean: "Final text only",
  diff_show_marks: "Show the edits",
  children_stat: "{n}",
  dropped_heading: "Cut",
  dropped_reason: "Reason: {reason}",
  needs_count: "{n} need your call",
  legend: "Legend",
  lang_switch: "中文",
  close: "Close",
};

export const DICT = { zh, en };
export type Key = keyof typeof zh;

export function translate(lang: Lang, key: string, vars?: Record<string, string | number>): string {
  const table = DICT[lang] as Record<string, string>;
  let text = table[key] ?? (DICT.en as Record<string, string>)[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) text = text.replace(`{${k}}`, String(v));
  return text;
}

export const LangContext = createContext<Lang>("zh");

export function useT() {
  const lang = useContext(LangContext);
  return (key: Key | string, vars?: Record<string, string | number>) => translate(lang, key, vars);
}

export function useLang(): Lang {
  return useContext(LangContext);
}

// Times are shown in the timezone of the machine that built the map: that is the
// clock the researcher lived through ("02:03 last night"), whoever opens the file.
function shifted(iso: string, tz: number | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  if (tz === null || tz === undefined) return d;
  return new Date(d.getTime() + (tz + d.getTimezoneOffset()) * 60_000);
}

const pad = (n: number) => String(n).padStart(2, "0");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function fmtTime(iso: string, tz?: number | null): string {
  const d = shifted(iso, tz);
  return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : "";
}

export function fmtDate(iso: string, lang: Lang, tz?: number | null): string {
  const d = shifted(iso, tz);
  if (!d) return "";
  return lang === "zh" ? `${d.getMonth() + 1}月${d.getDate()}日` : `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

export function fmtDateTime(iso: string, lang: Lang, tz?: number | null): string {
  const date = fmtDate(iso, lang, tz);
  const time = fmtTime(iso, tz);
  if (!date) return "";
  return lang === "zh" ? `${date} ${time}` : `${date}, ${time}`;
}
