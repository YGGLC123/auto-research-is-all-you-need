import type { Lang } from "@/i18n";

// On-screen words. Product labels (stamps, receipt, callouts) come from src/i18n.ts,
// so the video and the page never drift apart.
export type Copy = {
  nightEyebrow: string;
  night: string[];
  morning: string[];
  needs: (n: number) => string[];
  bench: string[];
  taste: string[];
  outro: string[];
  thanks: string;
  name: [string, string];
  tagline: string;
  ctaLabel: string;
  cta: string;
  meta: string;
};

export const COPY: Record<Lang, Copy> = {
  zh: {
    nightEyebrow: "凌晨",
    night: ["你在睡觉，", "AI 在改你的论文。"],
    morning: ["早上醒来，", "AI 递来一张小票。"],
    needs: (n) => ["要你拍板的，", `只有 ${n} 件。`],
    bench: ["王牌结果，", "被悄悄雪藏了。"],
    taste: ["踩你雷区的话，", "荧光笔圈出来。"],
    outro: ["AI 熬夜，", "你拍板。"],
    thanks: "谢谢惠顾",
    name: ["auto-research", "is all you need!"],
    tagline: "看清 AI 改了什么。研究，还是你的。",
    ctaLabel: "GitHub 搜",
    cta: "auto-research-is-all-you-need",
    meta: "Claude Code · Codex 插件 · MIT 开源",
  },
  en: {
    nightEyebrow: "Last night",
    night: ["You're asleep.", "Your AI is rewriting", "your paper."],
    morning: ["Next morning,", "it hands you", "a receipt."],
    needs: (n) => [`Only ${n} things`, "need your call."],
    bench: ["Your flagship result?", "Quietly benched."],
    taste: ["Not your style?", "Highlighted."],
    outro: ["The AI pulls", "the all-nighter.", "You make the call."],
    thanks: "Thank you, come again",
    name: ["auto-research", "is all you need!"],
    tagline: "See what your AI changed. The research stays yours.",
    ctaLabel: "",
    cta: "github.com/YGGLC123/auto-research-is-all-you-need",
    meta: "A plugin for Claude Code & Codex · MIT",
  },
};
