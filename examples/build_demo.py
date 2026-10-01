#!/usr/bin/env python3
"""Build the demo project: a small, entirely made-up paper and one night of AI edits.

    python examples/build_demo.py                 # -> ./demo-project (English)
    python examples/build_demo.py --lang zh       # the same story in Chinese
    python examples/build_demo.py --out /tmp/demo

The paper, the numbers and the sources are fiction.  What is real is the
machinery: every write goes through the plugin's own CLIs, exactly as it would
in a live project, so the resulting map shows what the system records.

Monday evening, the researcher writes the story with the AI and states one rule
of taste.  They look at the map, then go to bed.  Overnight the AI rewrites a
claim, adds a caveat that trips the taste rule, drops a claim that collides
with prior work, and quietly demotes the flagship result to a robustness
appendix.  In the morning the map shows all of it -- and flags the demotion as
benched, because nothing ever refuted that result.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPTS = PLUGIN / "scripts"
ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

CLAIM_KEYS = ("subject", "target", "predicate", "quantifier", "domain", "conditions", "polarity")

# Monday evening and the night after, as UTC; at UTC+10 that is 19:xx on Monday
# and 02:xx on Thursday morning (the page shows times on the builder's clock).
MONDAY = "2026-09-28T09:%02d:00Z"
NIGHT = "2026-09-30T16:%02d:00Z"

STORY = {
    "en": {
        "title": "Do Reviewers Read Appendices?",
        "objective": "Measure whether reviewers engage with appendices, and what it costs authors",
        "ledger": "# Evidence ledger (synthetic demo data)\n\n| id | source | finding |\n|---|---|---|\n"
                  "| E-01 | crawl of 12,000 OpenReview threads, 2018-2025 | median appendix length tripled |\n"
                  "| E-02 | reference parser over all reviews | 11.4% of reviews cite an appendix item |\n"
                  "| E-03 | matched pairs, proofs in body vs appendix | soundness -0.31 (se 0.07) |\n"
                  "| E-04 | review length model | +38% words when the appendix is cited |\n"
                  "| E-05 | placebo: random 'appendix map' on control papers | no change in mentions |\n",
        "decisions": "# Research Decisions\n\n## 2026-09-28 headline is the 11% number\n"
                     "- The 11% mention rate leads the paper (user-approved: headline-11pct).\n",
        "root": ("Reviewers rarely read appendices, and it shows in the scores",
                 "appendix engagement is low and consequential",
                 "A measurement paper: how often reviews engage with appendices, and what that costs authors."),
        "sections": [
            ("N-1", "Motivation: the appendix arms race", "Appendices keep growing; reviewing time does not."),
            ("N-2", "Measurement: do reviews mention the appendix?", "Parse every review for appendix references."),
            ("N-3", "Consequences: what an unread appendix costs", "Scores, rebuttals and what moves them."),
            ("N-4", "What venues could do", "Cheap interventions and how to test them."),
        ],
        "outline": "outline: {}",
        "outline_split": ":",
        "claims": [
            ("C-1", "N-1", "Median appendix length tripled between 2018 and 2025",
             "Across 12,000 OpenReview threads the median appendix grew from 6 to 19 pages.", "HELD", ["E-01"], None),
            ("C-2", "N-1", "Authors move key results to the appendix to fit page limits",
             "A growing share of main theorems are stated in the body but proved only in the appendix.",
             "PENDING", [], None),
            ("C-3", "N-2", "Only 11% of reviews cite anything in the appendix",
             "11.4% of reviews reference a numbered appendix item; most of those are proofs.",
             "HELD", ["E-02", "decision:headline-11pct"], None),
            ("C-4", "N-2", "Appendix mentions cluster in the last two days of the review window",
             "If reviewers read appendices at all, they do it under deadline pressure.", "PENDING", [],
             "timestamps for the 2025 cycle arrive"),
            ("C-5", "N-3", "Papers whose proofs live only in the appendix get lower soundness scores",
             "Matched on topic and length, moving proofs to the appendix costs 0.31 points of soundness.",
             "HELD", ["E-03"], None),
            ("C-6", "N-3", "Rebuttals that point to the appendix recover half a point",
             "When authors quote the appendix in the rebuttal, soundness scores recover about half the gap.",
             "PENDING", [], None),
            ("C-7", "N-4", "A one-page 'appendix map' raises appendix mentions",
             "A structured map of the appendix at the end of the body makes reviewers use it.", "PENDING", [],
             "the randomized pilot with two workshops reports"),
        ],
        "add": "add {}",
        "headline": "headline: the 11% number",
        "flagship": "flagship: the soundness penalty",
        "rule_avoid": ("Never write our own work as a weakness", "Don't hand reviewers a stick to beat us with.",
                       r"\blimitations?\b|\bfail(s|ed|ure)?\b|\bwe do not claim\b"),
        "rule_prefer": ("Every main claim gets a figure", "If it matters, draw it."),
        "c3": ("Only 9% of reviews cite the appendix once duplicates are removed",
               "After removing reviews that quote the same item twice, "
               "9.1% of reviews reference a numbered appendix item.",
               "tighten the measurement claim after de-duplication"),
        "n5": ("Appendix: robustness checks", "Results that hold, but are not the story.", "open a robustness appendix"),
        "c9": ("Reviewers who cite the appendix write 38% longer reviews",
               "Engagement shows up in effort: appendix-citing reviews are 38% longer.",
               "add a cleaner engagement result"),
        "demote": ("reads better as a robustness check",
                   "reframe: the engagement result leads; soundness penalty to robustness"),
        "move": "move the soundness penalty to the appendix",
        "c6": ("Rebuttals that quote the appendix recover about half the gap. "
               "One limitation is that the effect fails to replicate on the 2023 cycle.",
               "add a caveat to the rebuttal result"),
        "drop": ("collides with prior work: Smith & Lee (2025) already measured this",
                 "drop the motivation claim that prior work already covers"),
        "placebo": "placebo: the appendix map changed nothing",
        "tex": "\\section{What an unread appendix costs}\n"
               "Reviewers who cite the appendix write 38\\% longer reviews.\n"
               "% note to self: check the 2023 cycle\n"
               "One limitation of our design is that we cannot observe reading time.\n",
    },
    "zh": {
        "title": "审稿人读附录吗？",
        "objective": "测量审稿人是否真的读附录，以及这让作者付出什么代价",
        "ledger": "# 证据台账（演示用虚构数据）\n\n| 编号 | 来源 | 发现 |\n|---|---|---|\n"
                  "| E-01 | 抓取 2018–2025 年 12,000 个 OpenReview 讨论串 | 附录篇幅中位数涨到三倍 |\n"
                  "| E-02 | 对全部审稿意见做引用解析 | 11.4% 的审稿意见引用了附录条目 |\n"
                  "| E-03 | 配对样本：证明放正文 vs 只放附录 | 严谨性评分低 0.31（标准误 0.07） |\n"
                  "| E-04 | 审稿长度模型 | 引用附录时审稿意见长 38% |\n"
                  "| E-05 | 安慰剂：给对照论文随机加“附录地图” | 引用次数没有变化 |\n",
        # the decision line must name the slug and carry the literal marker user-approved
        "decisions": "# 研究决定\n\n## 2026-09-28 头条用 11% 这个数\n"
                     "- 由 11% 的引用率领衔全文（user-approved: headline-11pct）。\n",
        "root": ("审稿人很少读附录，而这会体现在评分上", "附录参与度低，而且有后果",
                 "一篇测量论文：审稿意见多大程度上涉及附录，以及这让作者付出什么代价。"),
        "sections": [
            ("N-1", "动机：附录军备竞赛", "附录越写越长，审稿时间却没变。"),
            ("N-2", "测量：审稿意见提到附录了吗？", "逐条解析审稿意见里对附录的引用。"),
            ("N-3", "后果：没人读的附录让作者付出什么", "评分、反驳，以及什么能让它们变化。"),
            ("N-4", "会议可以做什么", "低成本的干预，以及怎么检验它们。"),
        ],
        "outline": "大纲：{}",
        "outline_split": "：",
        "claims": [
            ("C-1", "N-1", "附录篇幅中位数在 2018 到 2025 年间涨到三倍",
             "在 12,000 个 OpenReview 讨论串里，附录篇幅中位数从 6 页涨到 19 页。", "HELD", ["E-01"], None),
            ("C-2", "N-1", "作者为了满足页数限制，把关键结果挪进附录",
             "越来越多的主定理在正文里陈述，却只在附录里证明。", "PENDING", [], None),
            ("C-3", "N-2", "只有 11% 的审稿意见引用了附录里的内容",
             "11.4% 的审稿意见引用了编号的附录条目，其中大部分是证明。",
             "HELD", ["E-02", "decision:headline-11pct"], None),
            ("C-4", "N-2", "附录引用集中在审稿期的最后两天",
             "如果审稿人真读附录，也是赶在截止前读。", "PENDING", [], "2025 年的时间戳数据到位"),
            ("C-5", "N-3", "证明只放附录的论文，严谨性评分更低",
             "在主题和篇幅匹配后，把证明挪进附录会让严谨性评分低 0.31 分。", "HELD", ["E-03"], None),
            ("C-6", "N-3", "在反驳里指向附录，能挽回半分",
             "作者在反驳里引用附录时，严谨性评分能挽回大约一半的差距。", "PENDING", [], None),
            ("C-7", "N-4", "一页纸的“附录地图”能提高附录引用",
             "在正文末尾放一张结构化的附录地图，审稿人就会用上附录。", "PENDING", [],
             "两个研讨会的随机化试点出结果"),
        ],
        "add": "新增 {}",
        "headline": "头条：11% 这个数",
        "flagship": "王牌：严谨性扣分",
        "rule_avoid": ("别把自己的工作写成弱点", "别递刀子给审稿人。", r"局限|失败|没能复现|我们不声称"),
        "rule_prefer": ("每个主要结论都要配一张图", "要紧的东西，就画出来。"),
        "c3": ("去重之后，只有 9% 的审稿意见引用附录",
               "去掉重复引用同一条目的审稿意见后，9.1% 的审稿意见引用了编号的附录条目。",
               "去重后收紧测量结论"),
        "n5": ("附录：稳健性检验", "成立，但不是主线的结果。", "新开一个稳健性附录"),
        "c9": ("引用附录的审稿人，审稿意见长 38%",
               "投入程度体现在工作量上：引用附录的审稿意见要长 38%。", "加一个更干净的参与度结果"),
        "demote": ("放进稳健性检验读起来更顺", "重新定框：参与度结果领衔，严谨性扣分移到稳健性部分"),
        "move": "把严谨性扣分挪进附录",
        "c6": ("作者在反驳里引用附录时，严谨性评分能挽回大约一半的差距。"
               "本文的一个局限是，这一效应在 2023 年的数据上没能复现。", "给反驳结果加一句保留意见"),
        "drop": ("撞了前人工作：Smith & Lee (2025) 已经测过", "删掉前人已经覆盖的动机论断"),
        "placebo": "安慰剂：附录地图没有带来任何变化",
        "tex": "\\section{没人读的附录让作者付出什么}\n"
               "引用附录的审稿人，审稿意见要长 38\\%。\n"
               "% 自用备注：查一下 2023 年的数据\n"
               "本文的一个局限是，我们观察不到审稿人的阅读时长。\n",
    },
}


def run(script: str, *argv) -> dict:
    proc = subprocess.run([sys.executable, str(SCRIPTS / script)] + [str(a) for a in argv],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
    if proc.returncode != 0:
        raise SystemExit(f"{script} {' '.join(map(str, argv[:3]))} failed:\n{proc.stdout}\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw": proc.stdout}


class Story:
    def __init__(self, root: Path):
        self.root = root
        self.ops = root / ".demo-ops.json"
        self.meta = root / ".demo-meta.json"

    def node(self, *argv):
        run("narrative.py", "node", argv[0], self.root, *argv[1:], "--out", self.ops)
        return json.loads(self.ops.read_text(encoding="utf-8"))["ops"]

    def commit(self, ops: list, message: str, at: str, by: str):
        self.ops.write_text(json.dumps({"ops": ops}, ensure_ascii=False), encoding="utf-8")
        self.meta.write_text(json.dumps({"message": message, "at": at,
                                         "author": {"role": by, "session": "demo"}},
                                        ensure_ascii=False), encoding="utf-8")
        run("narrative.py", "commit", self.root, "--ops-file", self.ops, "--meta-file", self.meta,
            "--now", at)

    def claim(self, nid, parent, title, statement, epistemic="PENDING", basis=(), bet=None):
        argv = ["add", "--id", nid, "--node-type", "claim", "--parent", parent, "--title", title,
                "--statement", statement, "--epistemic", epistemic]
        for key in CLAIM_KEYS:
            argv += ["--identity", "%s=%s" % (key, nid.lower() if key == "subject" else "-")]
        for ref in basis:
            argv += ["--basis", ref]
        if bet:
            argv += ["--live-bet", "--resolution-condition", bet]
        return self.node(*argv)

    def section(self, nid, parent, title, summary):
        return self.node("add", "--id", nid, "--node-type", "narrative", "--parent", parent,
                         "--title", title, "--summary", summary,
                         "--identity", "role_hint=section", "--identity", "thesis=%s" % nid.lower())


def build(out: Path, lang: str = "en") -> Path:
    s = STORY[lang]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    run("research_os.py", "bootstrap", out, "--title", s["title"],
        "--stage", "manuscript_draft", "--paper-type", "empirical_ml", "--objective", s["objective"])

    # -- evidence on record (fiction), and one decision the researcher signed
    (out / "evidence-ledger.md").write_text(s["ledger"], encoding="utf-8")
    (out / ".research-os" / "decisions.md").write_text(s["decisions"], encoding="utf-8")

    story = Story(out)
    title, thesis, summary = s["root"]
    run("narrative.py", "init", out, "--title", title, "--thesis", thesis, "--summary", summary,
        "--at", MONDAY % 0, "--now", MONDAY % 0)
    for nid, sec_title, sec_summary in s["sections"]:
        ops = story.section(nid, "R-0", sec_title, sec_summary)
        head = sec_title.split(s["outline_split"])[0]
        story.commit(ops, s["outline"].format(head.lower() if lang == "en" else head),
                     MONDAY % (1 + int(nid[-1])), "user")
    for i, (nid, parent, c_title, statement, ep, basis, bet) in enumerate(s["claims"]):
        story.commit(story.claim(nid, parent, c_title, statement, ep, basis, bet), s["add"].format(nid),
                     MONDAY % (10 + i), "user")
    story.commit(story.node("set-role", "--role", "headline", "--id", "C-3"), s["headline"], MONDAY % 20, "user")
    story.commit(story.node("set-role", "--role", "flagship", "--id", "C-5"), s["flagship"], MONDAY % 21, "user")
    text, quote, pattern = s["rule_avoid"]
    run("taste_ledger.py", "--project", out, "add", "--by", "user", "--source-kind", "user",
        "--scope", "story", "--scope", "writing", "--stance", "avoid",
        "--text", text, "--quote", quote, "--pattern", pattern)
    text, quote = s["rule_prefer"]
    run("taste_ledger.py", "--project", out, "add", "--by", "user", "--source-kind", "user",
        "--scope", "figures", "--stance", "prefer", "--text", text, "--quote", quote)
    # the researcher looks at the map before bed: this is "your last view"
    run("research_map.py", "--project", out, "render", "--out", out / ".research-os" / "map" / "monday.html")

    # -- overnight: the AI keeps working
    c3_title, c3_statement, c3_message = s["c3"]
    story.commit(story.node("patch", "--id", "C-3", "--set", "title=" + json.dumps(c3_title, ensure_ascii=False),
                            "--set", "statement=" + json.dumps(c3_statement, ensure_ascii=False)),
                 c3_message, NIGHT % 3, "main")
    n5_title, n5_summary, n5_message = s["n5"]
    story.commit(story.section("N-5", "R-0", n5_title, n5_summary), n5_message, NIGHT % 9, "main")
    c9_title, c9_statement, c9_message = s["c9"]
    story.commit(story.claim("C-9", "N-3", c9_title, c9_statement, "HELD", ["E-04"]), c9_message, NIGHT % 14, "main")
    # one commit: hand the flagship role to C-9 and demote C-5 (the contract wants both at once,
    # and `commit` validates the combined tree -- a single `node patch` would be refused alone)
    reason, message = s["demote"]
    ops = story.node("set-role", "--role", "flagship", "--id", "C-9")
    ops.append({"op": "patch_fields", "id": "C-5",
                "fields": {"state": {"narrative": "DEMOTED"},
                           "disposition_meta": {"target": "N-5", "reason": reason}}})
    story.commit(ops, message, NIGHT % 21, "main")
    story.commit(story.node("move", "--id", "C-5", "--new-parent", "N-5"), s["move"], NIGHT % 22, "main")
    c6_statement, c6_message = s["c6"]
    story.commit(story.node("patch", "--id", "C-6", "--set", "statement=" + json.dumps(c6_statement, ensure_ascii=False)),
                 c6_message, NIGHT % 30, "main")
    drop_reason, drop_message = s["drop"]
    story.commit(story.node("detach", "--id", "C-2", "--disposition", "DROPPED", "--reason", drop_reason),
                 drop_message, NIGHT % 41, "main")
    run("research_graph.py", "link", "--root", out, "--from", "ro::ledger:E-05", "--to", "ro::narr:C-7",
        "--kind", "supported_by", "--polarity", "-", "--basis", s["placebo"])

    # the manuscript the AI also touched overnight
    (out / "paper").mkdir(exist_ok=True)
    (out / "paper" / "sec3_consequences.tex").write_text(s["tex"], encoding="utf-8")
    for leftover in (story.ops, story.meta):
        leftover.unlink(missing_ok=True)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="build the made-up demo project")
    parser.add_argument("--out", default="demo-project")
    parser.add_argument("--lang", choices=sorted(STORY), default="en")
    parser.add_argument("--no-render", dest="no_render", action="store_true")
    args = parser.parse_args(argv)
    out = build(Path(args.out).resolve(), args.lang)
    if not args.no_render:
        page = out / ".research-os" / "map" / "research-map.html"
        result = run("research_map.py", "--project", out, "render", "--out", page)
        print("\n".join(result.get("summary") or []))
        print(f"\nopen {page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
