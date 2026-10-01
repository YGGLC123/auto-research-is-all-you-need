# Contract Amendments R9（2026-08-25 · 外部高推理模型审查 NOGO 项 · 对 figure-engineering-contract v1 与 graph-contract v1 的强制修订）

> **状态：已合并（MERGED · 2026-08-25）。**
> A1–A9 已并入 [figure-engineering-contract.md](figure-engineering-contract.md) **v1.1**，
> B1–B10 已并入 [graph-contract.md](graph-contract.md) **v1.1**；两份契约正文现在是权威口径。
> 本文自此**只作审计记录**——记录这些条款是在哪一轮、因为什么被写下的，不再单独作为实现依据。
> 落地代码：`research_graph.py`（B1–B9）、`role_runtime.py`（B7/B8）、`figure_router.py`
> 与 `component_index.py`（A1–A9）；负例见两份契约的验收段（A9 两条、B10 四条）。

本文条款**优先于**两份 v1 契约中的冲突文字；合并回正文后本文保留为审计记录。核心原则（R9 BIGGEST_OBJECTION）：**"追加一条边"不是简单动作**——同一逻辑关系可能有多个来源、来源会失效、来源会矛盾；必须定义"当前有效关系"。

## A. 图身份链（figure-engineering §0–§7）
A1 **ID 分配**：FigureRequest 默认不带 `figure_id`；`figure quick --register` 在项目 registry 内原子分配（计数器 + 写锁）；只有导入旧图允许 `--figure-id`，且做全项目唯一性检查。
A2 **Router 确定性**：`semantic_objects` 拆为 `required[]` / `optional[]`；Route B 必须 `required` 100% 覆盖，`optional` 命中率只作排序；Route A 只在 data_contract 的 `required_fields` 全部匹配时入选。评分元组冻结为 `(required_coverage, optional_coverage, venue_tag_match, quality_state_rank, -item_id 字典序)`，同分按 `item_id` 字典序取小——不同机器结果一致。
A3 **register 钉死选型**：registry 记 `selected_item = <id>@<version>`、`item_hash`、`route_policy_version`；库更新不改变已登记请求的选型；换模板必须显式 `figure reroute <F>`（记事件）。
A4 **不可变 render**：每次渲染生成 `render_id = R-<sha256(item_hash|venue_hash|params_hash|data_hash|capability_state)[:12]>`，产物落 `research/artifacts/figures/<F>/renders/<render_id>/`，**旧渲染永不覆盖**；venue profile 带 `version` 与 `venue_hash`；registry 的 `current_render` 只存指针。
A5 **产物与发布**：总是生成 pdf+svg+png 三种工作产物；`venue.export[]` 只决定发布产物。QA 措辞：PDF = 字体嵌入检查；SVG = 文本保留（`svg.fonttype none`）且字体声明存在，或已路径化。
A6 **状态迁移拆开**：`quick --render <F>`、`promote-current <F> --render-id <R>`、`promote-to-fsg <F>` 三个互不重载的命令。
A7 **组件来源强契约**：每个已用组件实例记 `{source_repo, source_commit, asset_id, asset_sha256, license_id, attribution_text, redistributable}`；可再分发时把实际使用的 SVG 副本放入该 render 目录；**任一组件 license 未知 ⇒ 允许 draft 渲染，拒绝 promote-current**。`component.bioicons` 探测同时检查索引与实际资产可读（repo 或副本目录）。
A8 **边所有权**：Figure 线**不直接** `graph link`；promote-current 只更新 registry（`status=current, claim_ids`）；`expressed_as` 边唯一由 Graph 的 `fig-registry` 派生规则产生（见 B）。
A9 验收补两个负例：多模板同分 ⇒ 字典序稳定；许可未知 ⇒ promote-current 被拒。

## B. 研究图（graph-contract §1–§8）
B1 **寻址闭合**：持久边只写冻结的 `state.project_id`（缺失时由 `narrative init`/`graph` 首次落账前生成并写入 state）；`slug(title)` 仅显示；短地址 `ro::…` 落账前展开为完整地址。叙事证据节点一律 `narr:E-*`，**不新增 evidence space**（派生规则表相应改写）。
B2 **撤回事件 schema**：`{event:"retract", target_edge_id, by, basis, at}` 追加；旧边行永不原地改写；`retracted_by` 字段删除（由 retract 事件投影）。
B3 **派生对账**：每条规则按 `source_fingerprint`（源对象内容 hash 集合）计算 desired set；与该规则当前 active derived set 做 add / retract 对账（HELD→KILLED ⇒ 撤销旧 + 边、追加新 − 边）；`derive` 因而幂等且能收回失效边。
B4 **逻辑边与冲突**：定义 `logical_key = (from, to, kind)`；视图与计数只按 **active logical edges**；同 key 同极性的多来源合并 provenance；异极性显示 `EDGE_CONFLICT`（列出双方来源）；手工边不得覆盖或撤销派生边（只能追加相反断言，形成 CONFLICT 供人裁）。
B5 **去重与映射表**：`fsg-claim` 与 `fig-registry` 对同一 fig→narr 只产一条（优先 fig-registry，fsg-claim 作 provenance 追加）；run `outcome` 映射：`positive/confirmed → +`，`negative/refuted → −`，`inconclusive/null → 无号`，其余 ⇒ 不产边；claim 五态映射：`PROVED +`，`REFUTED −`，`CONDITIONAL/BARRIER/EQUIVALENT_CORE 无号`，`OPEN/UNRESOLVED` 不产边；未列状态**不得猜极性**。
B6 **承重定义**：承重节点 = 当前 root、五角色节点、及其显式 `load_bearing=true` 子节点；coverage 各清单只按 active logical edges 计算。
B7 **鉴权先于端点**：授权单位 `(role, kind, from_space, to_space)`；角色从可信 role receipt 读取，禁止调用者自报；先鉴权再解析端点；越权 ⇒ `DENIED`，**永不进入 pending**。
B8 **pending 生命周期**：`PENDING → RESOLVED | DENIED | STALE`；每条记 `attempts, first_seen, last_attempt, status, authorization_policy_version`；对象登记时定向重试；连续 3 次失败或 30 天无对应对象 ⇒ `STALE`（保留审计，不计入 SessionStart 的 actionable pending）；replay 按**当前**政策再次鉴权。
B9 **晋升门槛机械化**：滚动 30 天内 ≥2 个角色各在 ≥5 个不同自然日写入有效边，且 ≥1 条 decision record 引用 coverage 输出。
B10 验收补四个负例：源删除后旧派生边被 retract；手工/派生异极性 ⇒ EDGE_CONFLICT；pending 变 STALE；replay 再鉴权拒绝。

## C. 落地顺序（**已完成**）
Z 轨（代码）：research_graph（B2–B8）→ role_runtime（B7/B8 的 receipt 与 replay）→ figure_router / component_index（A1–A9）→ 自测补负例 → 九套 + 三新套全绿；然后把本文合并进两份契约正文（版本 v1.1），本文留档。

落地实况（2026-08-25）：research graph 70/70（含 B10 四负例 + B9 门槛只读命令）、role runtime 58/58、
component index 39/39（含 A7 两负例）、figure router 31/31 与 `--integration` 48/48（含 A9 两负例）；
narrative 14、turn runtime 80（+129 integration）、backfill 36、narrative-axis render 43、growth 11；
`research_os.py doctor` 与 `lint_package.py` 绿。
