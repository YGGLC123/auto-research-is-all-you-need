# v3 Contract v1（统一研究对象图 · 权威化 · 2026-08-25）

**状态：已实现 —— 3.0.0（2026-08-25）。**V3-A（单一事务 + `current.json` + 对象索引 + rebuild/snapshot + hook_guard）、V3-B（实时派生 + 角色图上下文自动注入）、V3-C（四视图 HTML 界面 + 跨项目只读）全部落地；实际 CLI 面见 §9，自测与已知偏差见 §10。本文自此既是契约也是实现说明。

目标：把 v2.8/2.9 建成的旁路（`ro:` 寻址、边账本、派生、视图、角色落边）升级为**一等对象**：研究图对"对象之间的关系"拥有权威；对象本身仍由各自账本（叙事树 / 图实例 / 尝试树 / 资产 / run / claim / 台账 / 决策）拥有权威。与 graph-contract v1.1 冲突处以本文为准；不改叙事契约、角色契约、作图契约。

## 0. 权威划分（三层改四层）【已实现】
| 层 | 权威对象 | 写入口 |
|---|---|---|
| 权威叙事层 | 叙事树快照/提交/refs/支线/标签 | `narrative commit` |
| **权威关系层（新）** | `graph/edges.jsonl`（link/retract 事件）+ `graph/objects.json`（对象索引）+ `graph/current.json`（物化"当前有效关系"）+ `graph/snapshots/` | `graph link|retract|derive|index`——**单一事务**（journal → 临时文件 → fsync → 原子重命名），受项目写锁与租约 |
| 派生缓存层 | card / trajectory / 各渲染 / coverage 缓存 | 可删重建 |
| 辅助追加层 | annotations / incidents / adjudications / pending_edges | append-only |

- `graph/current.json` = 由事件流投影的 **active logical edges**（合并 provenance、冲突标记）+ 对象索引摘要；每次写事务结束后原子重建；所有视图、角色上下文、SessionStart 行只读它，不再扫 jsonl。
- `graph snapshot --label …`：复制 edges.jsonl + objects.json + current.json 到 `graph/snapshots/<ts>_<label>/`；`--keep N` 轮换；里程碑标签不轮换。
- 事件流 schema 版本 `auto-research/graph-events-v2`（v1 事件原样可读）。
- 事务落地：`graph/journal/` 记录未完成事务，崩溃后由 `graph rebuild` 重放；`hook_guard.py` 对 `.research-os/**/graph/**/*.{json,jsonl,log}` 的 Edit/Write/MultiEdit/NotebookEdit 一律 block，并在拒绝理由里指名该用哪条 `research_graph.py` 子命令。

## 1. 对象索引 `graph/objects.json`【已实现】
`{schema:"auto-research/graph-objects-v1", built_at, objects:{"<ro>":{space, id, title, status, fingerprint, source_path, updated_at}}}`
- `graph index build`：全量扫八个空间（复用 `resolve` 的解析器）；`graph index update --space narr|fig|tree|…`：增量，按源文件指纹只重扫变化的空间。
- 对象消失 ⇒ 索引记 `status:"gone"`（不删），视图标 `[GONE]`；派生对账据此撤回边。
- `graph view objects [--space] [--status]`（≤40 行）。

## 2. 实时派生（不再靠人跑）【已实现】
各 CLI 在自身事务**成功之后**调用 `research_graph.derive(control, rules=[...], reason=…)`（同进程 import；失败只记 incident，不回滚宿主事务）：
| 触发 CLI | 规则子集 |
|---|---|
| `narrative commit` | narr-basis、lineage、（若 role/claim 变）fig-registry 复核 |
| `figure_router quick --promote-current` / `--rebase` / `--reroute` | fig-registry |
| `research_os tree close` | tree-close |
| `procedures claim verdict` | claim-verdict |
| `procedures run finish` | run-claim |
| `research_os asset add` | asset-node |
| `narrative backfill` | 全部（backfill 结束时一次） |

实现落点（`grep _graph_after`）：`narrative.py`（commit / backfill）、`figure_router.py`（`quick --promote-current` / `--rebase` / `--reroute`）、`research_os.py`（`tree close` / `asset add`）、`procedures.py`（`claim verdict` / `run finish`）。每处都是宿主事务成功之后的同进程调用，抛错只写 `graph/incidents.log` 的一行 `GRAPH_DERIVE_FAILED`，宿主事务不回滚。
每次派生写 `graph/derive.log`（触发者、规则、add/retract 计数、耗时）；`derive --since <event_id>` 增量模式按对象指纹跳过未变源。

## 3. 角色上下文自动注入【已实现】
`role dispatch` 生成信封时，`inputs.graph_context` 由 CLI 从 `current.json` 填充：refs 中每个可解析对象的 `node` 视图（出入边 ≤8 条/对象，总 ≤40 行）+ 与该角色边类相关的冲突 `EDGE_CONFLICT` 列表 + 该角色的 pending 摘要。角色不需要自己查图。实现：`research_graph.graph_context(control, refs, role)`，由 `role_runtime.role_dispatch` 在未显式传 `--graph-context` 时调用；它永不抛错（失败返回 `{}`），超预算时整块降级为 `{"omitted": …}`，所以一次派发不会死于陈旧投影。

## 4. 研究图界面 `graph render --html [--view argument|coverage|conflicts|objects] --out <file>`【已实现】
单文件、零外链、与叙事轴页同一视觉语言（outline 导线、1px 细线、13/11 两级字号、语义色只在圆点）：
- **argument**：从 headline（或 `--node`）出发的论证链树（主张 → 证据 → run/数据），冲突节点标 `(!)`，每行可选中，右栏显示该对象的出入边与来源；
- **coverage**：A–E 五清单；
- **conflicts**：每个 EDGE_CONFLICT 的双方来源并列，附"提议裁决"按钮 → 导出 decision 草案 JSON（不写状态）；
- **objects**：按空间分组的对象表，`[GONE]` 灰显。
页面内嵌 `current.json`；编辑动作只导出补丁/决策草案（`auto-research/decision-draft-v1`，downloads / 剪贴板），不写状态。

CLI 实况：`--out` **必填**，`--view` 默认 `argument`（页面始终带全四个页签，`--view` 只决定先打开哪个）；`graph render` 不接 `--node`（起点由 headline/承重清单推定），需要指定起点时走独立入口 `py scripts/graph_render_html.py --project <root> --view … --node ro::narr:C-041 --out <file>`。渲染器缺失时 `graph render --html` 报 `GRAPH_RENDER_UNAVAILABLE`（退出码 2），其余 graph 命令不受影响。

## 5. 跨项目【已实现】
- `graph --all-projects view coverage|role-activity|conflicts`：遍历 portfolio 注册表，每项目只读其 `current.json`，输出每项目 ≤5 行的汇总（≤40 行总预算）。
- `graph --all-projects find --kind expressed_as --to-space narr --title-like …`：跨项目找"谁曾用什么图表达过类似主张"——弹药复用入口；结果指向源项目的 `ro:` 完整地址。
- 跨项目只读，永不跨项目写边：某项目的 `current.json` 缺失或落后 ⇒ 该项目被跳过并具名说明，绝不从这里替别人 rebuild。

## 6. 会话与摘要【已实现】
SessionStart 行改读 `current.json`：`GRAPH edges=<active> conflicts=<n> pending=<actionable> gone=<m> derive: last <when> by <trigger>`；若 `current.json` 落后于 edges.jsonl（事件 id 不一致）⇒ 输出 `GRAPH_REBUILD_REQUIRED` 并由主线首步 `graph rebuild`。

## 7. 晋升与版本【已实现】
- 3.0.0 = 本契约实现完成；`promotion-status` 保留为**健康度报告**（不再是门槛）。
- 不做：把对象本身搬进图（各账本仍是对象权威）；跨项目写边；把 `current.json` 当作可手改文件（hook_guard 拦截）。

## 8. 验收（机器）
1. 单一事务：模拟 link 后崩溃 → `graph rebuild` 后 current.json 与事件流一致；hook_guard 拦截对 `graph/*.json` 的 Edit/Write。
2. 对象索引：build 后八空间对象数与 resolve 一致；删一个源对象 → update 标 GONE → derive 撤回其边。
3. 实时派生：`narrative commit` 添加 basis_refs 后无需手跑 derive，`current.json` 已含该边；figure promote-current 同；tree close 同（临时 tree.json）。
4. 角色上下文：dispatch 信封含 `graph_context` 且 ≤40 行；含相关 EDGE_CONFLICT。
5. HTML：四视图渲染、零外链、可选中、冲突页导出决策草案 JSON。
6. 跨项目：注册表中 ≥2 个项目时 `--all-projects view coverage` 输出且 ≤40 行；`find` 命中示例。
7. 全部既有 13 套 + 本模块自测绿；doctor 绿；lint 零断链。

## 9. CLI 面（实况，`scripts/research_graph.py`；Windows 用 `py`，POSIX 用 `python3`）

```
graph [--all-projects] <子命令> [--root <项目根|.research-os|项目内任意路径>]

resolve <ro>                                   # 只读：ro 地址 -> {file, object, exists}
link --from … --to … --kind depends_on|supported_by|expressed_as|evolved_from
     [--polarity +|-] [--basis …] [--by main|user] [--replay-pending]
retract <edge_id> --reason …                   # 追加撤回事件；别名 unlink（只读拼写）
list [--from …] [--to …] [--kind …] [--by …] [--include-retracted] [--json]
derive [--dry-run] [--rules narr-basis,fig-registry,…] [--since <event_id>] [--reason …]
rebuild [--reason …]                           # 重放中断事务 + 重投影 current.json
snapshot [--label …] [--keep N] [--milestone] [--list]
index build|update [--space narr|tree|fig|asset|run|claim|ledger|decision] [--force]
find [--kind …] [--from-space …] [--to-space …] [--title-like …] [--all-projects] [--json]
render --html --view argument|coverage|conflicts|objects --out <file>
view argument|coverage|conflicts|objects|role-activity|node [<ro>] [--node …]
     [--space …] [--status present|gone] [--all-projects] [--json]
promotion-status [--json]                      # 只读健康度报告
self-test
```

- `--all-projects` 对 `view coverage|role-activity|conflicts` 与 `find` 有效，每项目 ≤5 行、总 ≤40 行，只读各自 `current.json`。
- `view` 比契约 §4 多两个文本投影：`role-activity`（唯一读事件流的视图——审计"谁断言过什么"必须看见撤回）与 `node <ro>`。
- 独立渲染入口：`py scripts/graph_render_html.py --project <root> [--view …] [--node …] [--out …] | --self-test`（不给 `--out` 时页面写 stdout）。

## 10. 实现状态、自测与已知偏差

**自测（2026-08-25 本机全绿）**：research_graph 111/111（R9 时 70）、graph_render_html 46/46（新）、role_runtime 58/58、
figure_router 35/35（`--integration` 52/52）、component_index 39/39、narrative 16/16、turn_runtime 80/80（`--integration` 129/129）、
narrative_backfill 36/36、narrative_render_html 43/43、growth 11/11；`research_os.py doctor` 绿（已把 `graph_render_html.py` 纳入字节编译清单）、
`lint_package.py` 零断链。

**老项目升级**：注册表中已有 `graph/edges.jsonl` 但无 `current.json` 的项目各跑一次 `graph rebuild`（只写 `graph/`）。
参考项目 44 事件 / 44 条逻辑边 / 0 冲突 / 0 撤回，`graph index build` 索引到 278 个对象（narr 104、asset 81、ledger 50、decision 23、claim 20），
渲染出的研究图页面 74 KB 单文件零外链。

**已知偏差（未修，属 V3-A/V3-C 接口对齐，不在本轮文档轨的改动范围）**：`current.json` 的 `objects` 字段写的是
**计数摘要** `{built_at, total, present, gone, by_space, indexed}`，而 `graph_render_html._norm_objects()` 期望"`ro:` 地址 → 对象记录"的映射。
后果：HTML 的对象页签把那 6 个摘要键当成对象行，边的 69 个真实端点因索引里查不到而全部标 `[MISSING]` 且无标题（覆盖页签不受影响——A–E 清单由
`coverage_report()` 现算）。修法二选一：`ensure_current()` 另写一个 `objects_index`（地址→{space,id,title,status}）字段，
或让 `graph_render_html` 在 `objects` 不是地址映射时回落读 `graph/objects.json`。

**Deviation fixed (2026-08-25, post V3-D):** `graph_render_html.py` now reads the address map from `graph/objects.json` (falls back to an embedded `ro:`-keyed map, ignores the count summary). Reference-project page re-rendered: 278 objects, 44 edges, 0 missing endpoints.
