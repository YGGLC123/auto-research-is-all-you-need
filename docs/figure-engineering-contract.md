# Figure Engineering Contract v1.1（v2.9 润色期 · 模板优先作图 · 2026-08-25 · **R9 amendments merged**）

实现者直接编码的契约。来源：v2.8 设计稿 §8（外部模型审查 R2 Q6 / R3 定稿）+ 社区资源盘点 + 图形工程师角色（role-fleet §2）。原则：**能模板填就不设计场景图；能组件拼就不 AI 生成；AI 生成前先查库。** 现有 figure-studio（FSG）降为 Route C 后端，不删不改其内部。

**v1.1 = v1 + [contract-amendments-r9.md](contract-amendments-r9.md) A1–A9 合并入正文**（图身份链：谁分配 id、选型钉在哪、渲染物是否可覆盖、边归谁写）。

## 0. 对象与目录
- 项目内：`.research-os/figures/registry.json`（图实例账本，只经 CLI）；产物 `research/artifacts/figures/<figure_id>/`——根目录放 `request.json`、`route.json`、`current_render.json`（指针）、Route C 的 `seed.fsg.json`；**每次渲染落在 `renders/<render_id>/`**（spec、渲染物、qa.json、provenance.json、attribution.md、组件副本）。
- 插件内库 `library/`：`items/<id>.json`（LibraryItem）、`venue/<venue>.json`（期刊档案，带 `version` + `venue_hash`）、`components/<source>/index.json`（组件索引，旁边是 `assets_ok` 标记）、`INDEX.md`（硬顶 200 行）。
- 一切图实例先登记再渲染：**`figure quick --register` 是 skill 的强制第一步**（不依赖 hook）。

### 0.1 图 id 分配（R9 A1）
FigureRequest **默认不带 `figure_id`**。`quick --register` 在 registry 内**原子分配**（registry 自带单调计数器 `figure_seq`，读—分配—写全程持 `hold_lock`）。`--figure-id` **只在 `--import` 下接受**（导入库外已有的图），并做全项目唯一性检查、把计数器推过该号。角色回传的 `proposed_figure_requests[]` 若自带 `figure_id` 一律拒收。

## 1. FigureRequest v0
```json
{"schema":"auto-research/figure-request-v0","intent":"…一句话读者问题…",
 "manuscript_target":{"section":"4.3","claim_ids":["C-057"]},
 "data_refs":[{"path":"research/results/thresholds.csv","columns":{"x":"k_eff","y":"t","lo":"t_lo","hi":"t_hi"}}],
 "semantic_objects":{"required":["threshold-curve"],"optional":["reference-line"]},
 "venue":"rfs","constraints":{"width":"single|double","color":"auto|mono"},
 "route":"auto|data|components|concept"}
```
`semantic_objects` 也接受裸列表（R9 前写法）——**整列视为 `required`**，因为那正是旧请求的原意，悄悄把一半降级会改变它们的路由。

## 2. Router（确定性，纯规则；`route` 可覆盖）
三问按序：
1. **几何能否由结构化数据导出？** `data_refs` 非空且某 LibraryItem(kind=chart-template) 的 `data_contract` **`required_fields` 全部匹配**（角色名 + 声明类型）⇒ **Route A**。缺一个必填角色不是低分，是**取消资格**。
2. **语义是否被已注册组件/图表模板覆盖？** 某 diagram-template 的 `semantic_tags` **100% 覆盖 `required[]`**（`optional[]` 只排序、绝不放行）或组件索引命中率 ≥ 0.6 ⇒ **Route B**。
3. 否则 ⇒ **Route C**（figure-studio；先查库只生成缺失元素）。

**评分元组冻结（R9 A2）**：`(required_coverage, optional_coverage, venue_tag_match, quality_state_rank)` 降序，`item_id` 字典序升序作最后一档 tie-break——同分不看文件系统列目录的顺序，不同机器结果一致。Route A 的语义对象不是闸门（一张表不会因为请求用了模板没打的词就不能画），它与未用到的 optional 角色一起进 `optional_coverage` 排序。`route_policy_version` 随元组变更递增，并记入每个实例。

输出 `RouteDecision{route, reasons[], candidates[{item_id, required_coverage, optional_coverage, venue_tag_match, quality_state_rank, qualifies}], missing[], route_policy_version}`；写入 registry。

## 3. Venue profile（`library/venue/<id>.json`）
`{id, name, version, venue_hash, renderer_preference, style_stack, figure_width_mm, font, palette, panel_label, vector, dpi, export[], colormap}`。首批：nature, science, ieee, rfs, jf, aaai, neurips, generic。**依赖单向**：venue → capability id。`venue_hash` 覆盖档案自身内容（不含该字段），是 render id 的输入之一——换了排版契约就是另一个渲染物。

## 4. LibraryItem v0（`library/items/<id>.json`）
`{id, kind, version, purpose, semantic_tags[], venue_tags[], renderer, capabilities[], parameters{}, data_contract{required_fields[{name,role,type}], optional_fields[], constraints[]}, outputs[], preview, license, attribution, provenance{}, compatibility, quality_state:"draft|trial|active|retired"}`。

**选型钉死（R9 A3）**：registry 记 `selected_item = <id>@<version>`、**`item_hash`**（该 item 的内容 hash——只钉 `id@version` 不够，同版本被就地改过就会悄悄换掉每一张引用它的图）、`route_policy_version`。**库更新不改变已登记请求的选型**；换模板必须显式 `figure quick --reroute <F> [--template …]`（记 `op:"reroute"` 事件，实例退回 `draft`），或 `quick --rebase <F> --to <item@v>`。渲染时若发现 `item_hash` 漂移，产物照实记录实际用的 hash 并标 `item_drift`。

## 5. Route A 渲染后端（`scripts/figure_router.py`，stdlib 编排 + 可选依赖）
- 后端 `matplotlib`（缺失 ⇒ Route A 不可用，记 blocker）；`scienceplots` / `cmcrameri` 可用则用，否则**记 degraded**。
- 每个 chart-template = 一个 Python 模板函数（`scripts/figure_templates.py`）。
- Vega-Lite：`--emit-vegalite` 额外写 `spec.vl.json`（不渲染）。

### 5.1 不可变 render（R9 A4）
每次渲染生成
```
render_id = "R-" + sha256(item_hash | venue_hash | params_hash | data_hash | capability_state)[:12]
```
产物落 `research/artifacts/figures/<F>/renders/<render_id>/`，**旧渲染永不覆盖**（同样输入 ⇒ 同一目录，天然幂等；换 venue / 换参数 / 换宿主能力 ⇒ 新目录）。registry 的 `current_render` 只存指针，`renders{}` 存每次渲染的读数。`capability_state` 进 id：同一模板同一数据在没装 scienceplots 的机器上渲出来的**不是同一个产物**。

### 5.2 产物与发布（R9 A5）
**总是生成 pdf + svg + png 三种工作产物**；`venue.export[]` 只决定**发布**产物（记为 `published[]`）。付印只要 pdf 的期刊，作者仍然需要 svg 去改、png 去贴幻灯片；渲染时删掉它们会让当初的取舍无从复核。

QA 措辞（机械）：
- **PDF = 字体嵌入检查**（`/FontFile` 在场）。
- **SVG = 文本保留**：`<text>` 仍在**且**有 `font-family` 声明 ⇒ pass；`<text>` 在但无字体声明 ⇒ warn（阅读器会替换字体）；已路径化（无 `<text>`、有 `<path>`）⇒ pass。
- 另有尺寸/越界/缺值/许可归属/风格降级；全部只报不拦，`--promote-current` 时才全查。

## 6. Route B 组件装配
- `scripts/component_index.py`：`index build`（逐图标记录 id/类别/许可/路径/尺寸；未清点的许可记 `UNKNOWN`，绝不猜）、`index search`、`index attribution`、**`index probe`**。
- 装配 `assemble`：代码放置、连接线代码画、文字进独立矢量层（`layer-connectors` / `layer-components` / `layer-text`）。
- 经 Router 落账的入口：`figure_router.py quick --assemble <F> [--slots …] [--labels …]`——只对 route=B 生效；产物同样写进 `renders/<render_id>/`。

### 6.1 组件来源强契约（R9 A7）
每个**已用**组件实例记
`{source_repo, source_commit, asset_id, asset_sha256, license_id, attribution_text, redistributable}`。
- **可再分发**时把实际使用的 SVG 副本放入该 render 目录（`components/<asset_id>.svg`）——上游索引重建后，已付印的图不能悄悄换了原料。
- **任一组件 license 未知 ⇒ 允许 draft 渲染，拒绝 `promote-current`**（`PROMOTE_BLOCKED_UNKNOWN_LICENCE`）。草稿要画出来才知道值不值得画；不知道能不能用的图标不能进正文。
- `component.<source>` 能力探测 = **索引 + 实际资产可读**：`component_index.py index probe --source <s>` 抽样打开索引指向的文件，通过则在 `index.json` 旁写 `assets_ok` 标记（`index build` 自动跑一次），失败则删除标记。能力注册表探 `assets_ok`，不再探 `index.json`。

## 7. Quick Figure（写作时"我要一张图"）
```
figure_router.py quick --register --intent … --claim C-057 --section 4.3 \
    --data research/results/thresholds.csv --columns "x=k_eff,y=t,lo=t_lo,hi=t_hi" \
    --semantic "threshold-curve" [--semantic-optional "reference-line"] \
    --venue rfs --width single                              → F-1NN（registry 分配 id）
figure_router.py quick --register --import --figure-id F-090 …   # 唯一允许自带 id 的路径
figure_router.py quick --render          F-101 [--emit-vegalite] → renders/<R>/ + 轻 QA + provenance
figure_router.py quick --assemble        F-101 [--slots …]       → Route B 组件装配（见 §6）
figure_router.py quick --promote-current F-101 [--render-id R-…] → 全量 QA + registry status=current
figure_router.py quick --promote-to-fsg  F-101                   → 以实例为 seed 进 figure-studio
figure_router.py quick --reroute         F-101 [--template lib:figure:event-study@1]
figure_router.py quick --rebase          F-101 --to lib:figure:event-study@1
figure_router.py quick --register --from-reply <figure-engineer 回传.json>
figure_router.py route|venue|library|list|self-test [--integration]
```
**状态迁移互不重载（R9 A6）**：`--promote-current` 与 `--promote-to-fsg` 是两个命令。R9 前的 `--promote <F> --current|--to-fsg` 保留为**别名**并打印弃用提示——一个命令按兄弟 flag 改变含义，打错一个字就静默执行了另一个迁移。

**边所有权（R9 A8）**：Figure 线**不直接 `graph link`。** `promote-current` 只更新 registry（`status=current`、`claim_ids`、`current_render`）并落盘，随后调用 `research_graph derive`；`expressed_as` 边**唯一**由 Graph 的 `fig-registry` 派生规则产生（graph-contract §3）。一条关系一个所有者、一个可撤销的地方，Figure 命令也就无法断言 Graph 自己不会推出的东西。

入账：`provenance.json{render_id, render_hash, data_hash, params_hash, capability_state, selected_item, item_hash, venue_hash, export_set, published[], component_provenance[], qa_summary}`；registry 状态 `draft|current|superseded`。

## 8. 能力注册表
`renderer.matplotlib`（probe: python import；`on_missing = blocker`）、`style.scienceplots`、`colormap.cmcrameri`、`renderer.vegalite`、**`component.bioicons`（probe: `library/components/bioicons/assets_ok`——索引 + 资产可读，见 §6.1）**、`dsl.plotneuralnet`、`qa.scipilot`（其余 degrade）。

## 9. 角色接线
图形工程师（FIGURE_REQUEST）回传的 `proposed_figure_requests[]`/`routes[]` 由主控经 `figure quick --register --from-reply <file>` 落账（**回传不得自带 figure_id**）；`qa_findings[]` 合并进 qa.json；`proposed_edges[]` 由 role receipt 的边处理落账（graph-contract §6）。

## 10. 验收（机器）
1. `figure_router.py self-test`：Router 三例判定正确；venue 八档案加载（含 `version`/`venue_hash`）；LibraryItem schema 合法且 INDEX ≤200 行。
2. 六个 chart-template 各真实渲染出 pdf+svg+png（matplotlib 缺失时 SKIPPED 而非 PASS）；同一数据换 venue 只改样式（`data_hash` 不变、`render_hash` 变）。
3. 组件索引：Bioicons index.json ≥ 2000 条、每条有 license；search 命中；attribution.md 生成；`index probe` 写/删 `assets_ok`。
4. **端到端 `figure_router.py self-test --integration`**：bootstrap → `narrative init` + claim 节点并**赋 headline 角色**（否则它不承重，coverage 检查是空的）→ `--figure-id` 无 `--import` 被拒 → `register` 分配 F-101、判 A、钉 forest-ci + item_hash → `route` 复核 → `--render` 出 `renders/<R>/` 三产物 + qa + provenance + spec.vl.json（重渲染回到同一 render_id）→ coverage A 段列出该 claim → `--promote-current` 后 `expressed_as` 边**由 derive 落账且 `by=derive`**（pending 判 FAIL）→ `link --replay-pending` 空队列干净空转 → coverage A 段不再列该 claim → Route B 装配出 svg 并回填强契约 provenance（组件体缺失时 SKIPPED）→ Route C 记 `handoff.to=figure-studio`、旧 `--promote --to-fsg` 别名仍可用 → `--reroute` 改钉选型并退回 draft。
5. **R9 A9 两负例**：①多模板同分 ⇒ 按 `item_id` 字典序稳定，正反两种输入顺序结果相同；②许可未知 ⇒ `promote-current` 被拒。
6. doctor 新检查：library items schema、INDEX 预算、venue 档案、组件索引存在性（缺失 = 警告不失败）。
7. `library/INDEX.md` 是共享索引：growth 账本在上、图库段在下；`growth.py render` 经 `figure_router.index_block()` 重新生成图库段，预算行统计整份文件。
