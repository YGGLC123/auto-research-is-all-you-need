# Research Graph Contract v1.1（v3 第 2 步 · 2026-08-25 · **R9 amendments merged**）

固化 `research_graph.py` 已实现并在真实项目/烟测中验证过的旁路，把它从"能用"变成"角色协作的底座"。仍是**辅助追加层**（不是权威叙事层）；晋升规则见 §7。与 narrative-contract / role-fleet 冲突处以本文为准（仅限图相关条款）。

**v1.1 = v1 + [contract-amendments-r9.md](contract-amendments-r9.md) B1–B10 合并入正文。** 核心原则（R9 BIGGEST_OBJECTION）：**"追加一条边"不是简单动作**——同一逻辑关系可能有多个来源、来源会失效、来源会矛盾；本契约因此定义的不是"边"，而是**当前有效关系**。

## 0. 本文的层级已被 v3 取代（2026-08-25）

**本文 §7 的晋升条件已由 3.0.0 直接兑现：边账本不再是辅助追加层，而是[权威关系层](v3-contract.md)。**
本文其余条款（寻址、事件与撤回、八条派生规则、逻辑边与冲突、承重定义、鉴权与 pending 生命周期、投影）**逐条继续有效**，
是 v3 的实现细则；只有"层级"和"晋升"两件事以 [v3-contract.md](v3-contract.md) 为准：

| 本文说 | v3 改成 |
|---|---|
| 边账本是**辅助追加层**（§前言、§2） | **权威关系层**：`graph/edges.jsonl`（事件）+ `graph/objects.json`（对象索引）+ `graph/current.json`（物化的当前有效关系）+ `graph/snapshots/`，写入是**单一事务**（journal → 临时文件 → fsync → 原子重命名），受项目写锁；`hook_guard` 拦截一切手改（[v3-contract §0](v3-contract.md)） |
| §4.1 逻辑边由视图**现算** | 由 `current.json` **物化**；视图、角色上下文、SessionStart 行只读它，不再折叠 jsonl；落后于事件流 ⇒ `GRAPH_REBUILD_REQUIRED` |
| §5 投影三个视图，"HTML 后置" | 六个文本投影（+`conflicts`、`objects`、`node`）+ 四视图单文件 HTML（`graph render --html`，[v3-contract §4](v3-contract.md)） |
| `derive` 由人按需跑 | **实时派生**：各宿主 CLI 在自身事务成功后自动触发对应规则子集（[v3-contract §2](v3-contract.md)） |
| §7 晋升门槛 = 改层的前置条件 | 已达成；`graph promotion-status` 保留为**只读健康度报告**，不再是门槛 |
| 只谈本项目 | 增跨项目**只读**：`graph --all-projects view …` / `find`（[v3-contract §5](v3-contract.md)），永不跨项目写边 |

对象本身仍由各自账本拥有权威（叙事树 / 图实例 / 尝试树 / 资产 / run / claim / 台账 / 决策）——v3 把**关系**收归权威，不是把对象搬进图。

## 1. 寻址（R9 B1）
`ro:<project>:<space>:<id>`；space ∈ {narr, tree, fig, asset, run, claim, ledger, decision}。

- `<project>` = **冻结的 `state.project_id`**。缺失时由首次落账前的 `ensure_project_id()` 生成并写入 state（任何 `graph` 写操作都会触发）；`slug(title)` **仅用于显示**，改标题不得改变已落账地址的含义。
- 短形式 `ro::<space>:<id>` = 当前项目，**落账前展开为完整地址**——账本里永远是完整地址。
- 叙事证据节点一律 `narr:E-*`；**不新增 evidence space**。`E-/D-/R-` 引用在 HEAD 里存在即为 `narr`，否则为 `ledger`。
- `fig` 解析顺序：`.research-os/figures/registry.json`（v2.9 实例）→ FSG `F##.fsg.json`。解析只读；不存在 ⇒ `exists:false, reason`。

## 2. 边与撤回（R9 B2）
`edges.jsonl` append-only，两类事件：

- `link`：`{schema, event:"link", edge_id: sha256(from|to|kind|polarity|basis|by)[:12], from, to, kind, polarity, basis, by, at, source_commit?, source_fingerprint?, also_from?}`；kind ∈ {depends_on, supported_by(polarity +|-|null), expressed_as, evolved_from}。
- `retract`：`{schema, event:"retract", target_edge_id, event_id, by, basis, at}`。

**旧边行永不原地改写**；`retracted_by` 字段已删除，撤回状态由 retract 事件投影得出（`retracted` / `retraction`）。`unlink` 是 R9 前拼写，只读不写。`link` 要求两端**存在**；`derive` 允许指向已消失的账本行（视图标 `[MISSING]`）。

## 3. 派生规则（确定性、幂等、只读源；`by="derive"`, `basis="derive:<rule>"`）
| 规则 | 源 | 边 |
|---|---|---|
| narr-basis | 叙事节点 `basis_refs` | ledger/asset/narr → narr `supported_by`（HELD +，KILLED −，PENDING 无号） |
| tree-close | `tree.json` 已 close 节点含 claim/result 引用 | tree → narr/claim `supported_by`（见 §3.2） |
| fsg-claim | FSG 元素/面板 `claim_id` | fig → narr `expressed_as`（与 fig-registry 去重，见 §3.3） |
| fig-registry | v2.9 `figures/registry.json` 实例 `claim_ids` 且 status=current | fig → narr `expressed_as` |
| asset-node | 资产索引 `produced_by`/tags 含节点 id | narr → asset `depends_on` |
| run-claim | `runs/` 登记含 `claims[]` 与 `outcome` | run → claim/narr `supported_by±`（见 §3.2） |
| claim-verdict | procedures claim 五态 | claim → narr `supported_by±`（见 §3.2） |
| lineage | 叙事节点 `lineage[{rel, of}]` | new → old `evolved_from`（rel 记入 basis） |

### 3.1 派生对账（R9 B3）
每条规则按 **`source_fingerprint`**（源对象内容 hash）计算本次的 desired set，与该规则**当前 active 的派生边**做 add / retract 对账：

- 源新说的 ⇒ 追加 link；
- 源不再说的 ⇒ 追加 retract（`by="derive"`，basis 写明 "source reconciled"）；
- HELD→KILLED ⇒ 旧 `+` 边被撤销、新 `−` 边被追加（极性变 ⇒ edge_id 变 ⇒ 自动落入这条路径）。

因此 `derive` 幂等**且能收回失效边**；`--dry-run` 报出同样的 add/retract 计数而不写盘。

### 3.2 状态 → 极性映射表（R9 B5，穷尽）
| 表 | `+` | `−` | 无号 | 不产边 |
|---|---|---|---|---|
| run `outcome` | positive, confirmed, success, succeeded, proven, proved, replicated, holds | negative, refuted, failed, failure, disproved, no-go | inconclusive, null, mixed, partial | running, queued, pending, aborted, cancelled，**及一切未列状态** |
| claim 五态 | PROVED / PROVEN | REFUTED | CONDITIONAL, BARRIER, EQUIVALENT_CORE | OPEN, UNRESOLVED，**及一切未列状态** |
| tree 终态 | proven, proved, success | refuted, failed | blocked, abandoned, superseded, inconclusive | 一切未列状态 |

**未列状态不得猜极性**，也不得从 `result` 自由文本里嗅探——规则改为"不产边"，并在 `missing_sources` 里具名说明。

### 3.3 fsg-claim × fig-registry 去重（R9 B5）
同一 `fig → narr` 关系只产**一条**边：**fig-registry 优先**（它是 v2.9 实例账本，知道哪次渲染真的会付印）；被抑制的 fsg-claim 以 `also_from` 追加进该边的 provenance，并在 `derive` 结果的 `deduped[]` 里列出。

## 4. 逻辑边、冲突与承重

### 4.1 逻辑边（R9 B4）
`logical_key = (from, to, kind)`。**视图与计数只按 active logical edges**：

- 同 key 同极性的多来源 ⇒ 合并为一条，provenance 累加（`sources = n`）；
- 同 key **异极性** ⇒ 一条标记 `conflict` 的逻辑边，`polarity = null`，双方来源并列显示为 `EDGE_CONFLICT`；争议关系**既不算支持也不算反驳**，等人裁决；
- **手工边不得覆盖或撤销派生边**：对派生边的手工 `retract` 被拒（`DERIVED_EDGE_NOT_MANUALLY_RETRACTABLE`），只能追加相反断言形成 CONFLICT。

### 4.2 承重（R9 B6）
承重节点 = **当前 root** ∪ **五角色节点** ∪ **显式 `load_bearing=true` 的节点**。（R9 前的"root 的直接子节点"让承重集合随树形抖动——插一个分组节点就悄悄换掉了被问责的主张。）coverage 各清单只按 active logical edges 计算。

## 5. 视图（≤40 行）
`argument [--node]`（默认 headline，缺则 root 并列出建议节点；走逻辑边，冲突标 `(!)`）、`coverage`（A 无图表达的承重主张 / B 不表达主张的图 / C HELD 无支持 / D 有 − 边但仍 ACTIVE / **E EDGE_CONFLICT**）、`role-activity [--window 30]`、`node <ro>`（该对象出入边一览）。

## 6. 角色回传中的边（R9 B7 / B8）

### 6.1 鉴权先于端点（B7）
授权单位 = **`(role, kind, from_space, to_space)`**。角色**只能**来自可信 role receipt（`role_runtime` 从 dispatch 登记里读出后传给 `submit_edges`），**禁止调用者自报**：`graph link --by <role>` 被拒（`ROLE_IDENTITY_NOT_SELF_ASSERTED`），CLI 直接写入一律 `by=main`（`main` 不拥有任何边类）。

**先鉴权，再解析端点。** 越权 ⇒ `DENIED`，**永不进入 pending**（否则越权提议会在对象出现后自己落账，且角色能借"存不存在"探知不该看的对象）。授权表带版本 `authorization_policy_version`。

### 6.2 pending 生命周期（B8）
`PENDING → RESOLVED | DENIED | STALE`。每条记 `attempts, first_seen, last_attempt, status, authorization_policy_version`。

- 两端可解析 ⇒ `graph link`（`by=<role>`，`basis` 含 message_id）；
- 否则写 `roles/pending_edges/<message_id>.json`（原因：哪端不存在）；
- `graph link --replay-pending` 按**当前政策再次鉴权**（政策变了就按新的判），并定向重试；
- 连续 3 次失败或 30 天无对应对象 ⇒ `STALE`（保留审计，**不计入 actionable**）；
- `role status` / SessionStart 的 `PENDING_EDGES n` 只数 actionable。

## 7. 晋升规则（v3 第 3 步的门槛；机械判定，R9 B9）——**门槛已于 3.0.0 达成，见 §0**
边账本升为权威层的机械门槛：**滚动 30 天内 ≥2 个角色各在 ≥5 个不同自然日写入有效边，且 ≥1 条 decision record 引用 coverage 输出。**

`graph promotion-status` 是**只读**命令：它报告门槛是否达到，**不改层、不晋升任何东西**。3.0.0 之后它是**健康度报告**——层级已由 v3 契约改定，这条命令再也不是任何东西的闸门。

## 8. 验收（机器）
1. 八条派生规则各有 self-test 正例；在一个含 tree.json 的真实项目与一个含 FSG 的真实项目上 `derive --dry-run` 要么报出 >0 条对应规则的边，要么对每条 0 计数给出**具名缺源说明**——静默 0 不合格（只读；写入只在临时副本）。
2. `role receipt` 携带可解析边 ⇒ 边落账；不可解析 ⇒ pending 并在 status 出现；replay 后清空。
3. `EDGE_CLASS_DENIED` 正例。
4. SessionStart 行在有边账本的项目里出现；无账本静默。
5. **R9 B10 四负例**：①源删除后旧派生边被 retract（且是事件、不是改行）；②手工/派生异极性 ⇒ EDGE_CONFLICT，且手工撤销派生边被拒；③pending 三次失败后变 STALE 且不再计入 actionable；④replay 按当前政策再鉴权并 DENY。
6. 九套 + 本模块自测全绿；doctor 绿。
