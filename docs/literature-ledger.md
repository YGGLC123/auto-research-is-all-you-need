# Literature Ledger v1（文献台账 · 树形结构的文献联动 · 2026-08-27）

目标：叙事树的**树形**（括号流程）结构旁边，每一段能看到它依托的研究；每篇研究打开是一份**简短报告**（贡献 / 相对本文的边界 / 本文怎么用它）+ 书目 + 核实状态 + 原文链接 + 反向"依托它的段落"。目录 / 分栏两种结构不承担这一层——它们的责任是结构，不是证据。

## 0. 不变式
1. **一篇文献一条记录，权威源是 `refs.bib`。** 台账 `.research-os/literature/ledger.json`（`auto-research/literature-ledger-v1`）按 bib key 建条；书目字段只从 bib 来，其它材料只能**补充**（角色、备注、链接、核实），不能改书目。
2. **报告只依据本地材料。** `contribution / boundary / use_in_paper` 由证据管家（Claude CLI 打印模式，无工具）根据书目、被引章节、分章角色、读什么、文献地图备注写成；材料不够的行写"材料未记载"。每份报告记 `by / model / at`。页面显示的就是这三行，不显示生成过程。
3. **段落 ↔ 文献的关系分三档**（`links.json`，`auto-research/literature-links-v1`）：`chapter`（该章的 tex 引用或分章优先表列出）、`mention`（段落文字里的作者-年份，按姓氏+年份匹配到 bib）、`basis`（节点 `basis_refs` 里的 `lit:<key>` / `bib:<key>`）。三档都是**读到的关系**，不是裁定；裁定过的"支持 / 反对"仍走研究图的 `supported_by±` 边（graph-contract），页面日后叠加显示。
4. **页面是读者。** 树形结构读 `ledger.json + links.json` 画紫色计数与报告；页面不写这两个文件。重建（`literature build`）保留已有报告；`literature report` 默认只补缺。
5. **核实状态三档**：`verified`（bib 上方的 `% verified …` 注或文献地图"已核实"）、`unverified`（文献地图标"链接未核实"）、`unknown`。页面按字面显示，不升级。

## 1. 来源发现（`literature build`）
- `refs.bib`：项目内第一个 `refs.bib`（或 `--bib`）；tex 目录默认同目录（`secN_*.tex` → 第 N+1 章，其它文件 → 附录组 0）。
- 分章优先表：`*文献优先表*.md`（`## 第 N 章 …` / `1. **key** — …。角色…重点看:…`）。
- 文献地图：`*文献地图*` 目录下的 `.md`（`- 作者 (年). "题目." *刊* … https://doi.org/… (已核实|链接未核实; 备注)`），按 DOI → 题目 → 姓氏+年份匹配到 bib。
- 结果写 `sources.json`，下次沿用；`--bib/--tex/--table/--map` 覆盖。

## 2. CLI（`narrative literature …` 转发到 `scripts/literature_index.py`）
```
literature build [--bib --tex --table --map]     # 书目 + 被引章节 + 角色 + 地图 → ledger.json
literature link  [--ref main]                    # chapter / mention / basis 三档 → links.json
literature report [--model opus] [--batch 12] [--all]   # 只补缺的三行报告
literature status
```

## 3. 页面（树形结构）
- 段落标签右侧紫色数字 = `litOf(id)` 去重后的篇数；点它 = 选中该段并滚到"相关研究"。
- 右栏"相关研究（n）"：作者-年份 · 题目 · 该章角色（无角色时显示"正文提及"或报告的用法行）。点一条 → 文献页：书目、核实状态、被引章节、**贡献 / 边界（相对本文）/ 本文怎么用它**、在这一章的角色与读时看、文献地图备注、DOI/链接、依托它的段落（可跳转）、"问：它对这一段的贡献和边界"（预填问题，回到段落提问）。
- 切到目录 / 分栏或选中别的段落，文献页关闭。

## 4. 责任
- **证据管家**：台账内容（报告、核实、角色）；发现 `unmatched_mentions`（正文提到但 bib 没有的引用）时提出补录或改写。
- **史官**：节点 `basis_refs` 里写 `lit:<key>` 时用 bib key，不写自由文本引用。
- **主控**：回填/大改后跑 `build → link → report`；不手改 `literature/` 下的文件。

## 5. 验收（机器，`literature_index.py --self-test` 16 项）
bib 解析（括号/引号/注释核实）、引用显示（1/2/3/多作者）、tex 引用分章且忽略注释、来源发现、地图匹配与核实状态、三档链接、分批报告与只补缺、重建保留报告、页面负载不泄露原始作者串。
