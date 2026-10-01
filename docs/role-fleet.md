# Role Fleet Contract v1（多分控协作 harness · 2026-08-25）

目标：让大模型以**多个长期角色分控**协作——每个分控有自己的持久状态（档案、记忆、游标、收件箱、回执），由主控经消息**星型**调度，全部在**同一张研究图**上协作。本文是实现契约；与 narrative-contract.md 一致处以后者为准（史官是本契约的第一个实例）。

## 0. 不变式

1. **分控只提议，不写权威状态。**每个角色的输出是一份 ≤30 行的结构化回传；落盘只经 CLI（`role receipt`）。角色连自己的记忆也不直接写。
2. **星型。**角色实例之间没有通道；任何跨角色需要都变成一张 K## 工单，由主控派发。注册表里不存在"角色→角色"的地址。
3. **实例可死，角色不死。**角色 = 持久档案（插件 `agents/<role>.md`）+ 项目记忆（`.research-os/roles/<role>.memory.json` ≤12 KB）+ 游标 + 收件箱。会话实例由主控按需 spawn，SendMessage 只在会话内续聊；新会话按注册表重建。
4. **每次派发都有回执。**派发登记 → 回传 → `role receipt` 校验并落盘（记忆增量、游标推进、K 工单状态）。无回执的派发在下次会话开始时被列为 `DISPATCH_UNANSWERED` 事故。
5. **共享底座是图，不是聊天。**角色之间通过图（叙事树 + 跨图边账本 §5）和 K 工单看见彼此的工作；不通过转述。

## 1. 角色注册表 `.research-os/roles/registry.json`

```json
{"schema":"auto-research/role-registry-v1",
 "roles":{
   "chronicler":{"profile":"agents/research-chronicler.md","enabled":true,"hot":true,
                 "memory":"roles/chronicler.memory.json","cursor":{"events_seq":128,"manifest":"im_…"},
                 "instance":{"session":"W017","agent_id":"a48…","spawned_at":"…"}|null,
                 "last_dispatch":{"message_id":"…","kind":"CHRONICLE_TURN","at":"…","receipt":"…|null"},
                 "open_tasks":["K07"],"status":"dormant|hydrated|busy|stale"},
   "figure-engineer":{…},"evidence-steward":{…},"experiment-runner":{…},"theory-operator":{…}}}
```
- `hot` = 有开放 K 工单或 7 天内有派发 ⇒ 会话开始时列入 `REHYDRATE_REQUIRED`。
- `instance` 只在会话内有效；会话结束即失效（新会话置 null）。
- 注册表只经 CLI 写（`role enable|disable|dispatch|receipt|hydrate|status`）。

## 2. 角色清单（第一批五个；裁判类永远一次性，不入注册表）

| 角色 | 职责（研究期 / 润色期） | 触发 | 回传结构 |
|---|---|---|---|
| chronicler 史官 | 叙事变化→节点操作提议；叙事卡；漂移举旗 / 叙事文稿管家 | 涉及叙事的轮次；"看看历史轨迹"；节点提问 | narrative-contract §7 四字段 |
| figure-engineer 图形工程师 | 图请求路由提议、模板/组件选型、QA 读数 / 出版级修图 | 图任务 K## | `{proposed_figure_requests[], routes[], qa_findings[], memory_delta}` |
| evidence-steward 证据管家 | 文献/引用/数据来源台账维护提议、novelty 撞车预警、UNVERIFIED 标记 | 新引用出现、novelty 复查、投稿前 | `{ledger_ops[], citation_verdicts[], collisions[], memory_delta}` |
| experiment-runner 实验管家 | run 登记/对账提议、结果→证据节点提议、复算需求 | run 完成、结果入稿 | `{run_ops[], evidence_proposals[], reproduce_requests[], memory_delta}` |
| theory-operator 理论操作员 | 攻坚账本状态、claim 五态提议、Pro 派题包草案 | 定理/证明变化 | `{claim_ops[], siege_next[], transfer_candidates[], memory_delta}` |

档案文件统一结构：Input / Output（固定字段、≤30 行）/ How to decide / Phase behaviour / Memory（记什么不记什么）/ Never（禁止项：不写状态、不与其它角色通信、不伪造回执）。

## 3. 通用信封 `ROLE_TASK/v1`（CHRONICLE_TURN 是其 kind 之一）

```json
{"schema":"ROLE_TASK/v1","message_id":"msg_…","role":"figure-engineer","kind":"FIGURE_REQUEST|CHRONICLE_TURN|EVIDENCE_AUDIT|RUN_RECONCILE|CLAIM_UPDATE|QUESTION",
 "project":"…","turn_uuid":"…|null","k_task":"K07|null","manifest_id":"…|null",
 "inputs":{"refs":[…≤16 路径/ro: 地址],"graph_context":{"nodes":[…],"edges":[…]},"memory_digest":"≤40 行"},
 "objective":"固定文案（按 kind）","constraints":"≤30 行；只提议；不写；不联系其它角色",
 "idempotency_key":"sha256(project|role|kind|turn_uuid|manifest_id|k_task)"}
```
`role dispatch <role> --kind … [--k-task …] [--refs …]` 由 CLI 生成信封（含记忆摘要与图上下文），登记 `last_dispatch`，打印信封供主控 spawn/SendMessage。

**`inputs.graph_context` 由 CLI 自动填充（v3 起，见 [v3-contract.md](v3-contract.md) §3）。**主控不再自己查图再转述，角色也不需要（更不许）自己查图：`role_runtime.role_dispatch` 在未显式传 `--graph-context` 时调用 `research_graph.graph_context(control, refs, role)`，它从 `graph/current.json` 取——
1. `refs` 里每个可解析对象的 `node` 视图（每对象出入边 ≤8 条）；
2. 与**该角色自己的边类**相关的 `EDGE_CONFLICT`（别人的争议不塞给他）；
3. 该角色的 pending 提议摘要（只数 actionable）。

总预算 ≤40 行；超预算整块降级为 `{"omitted": …}`，信封仍在 8 KiB 内。该函数**永不抛错**：图没装、投影落后、解析失败都返回 `{}` 或在 `flags` 里挂一条 `GRAPH_REBUILD_REQUIRED`，**一次派发不允许死于关系层的状态**。显式传 `--graph-context '<JSON>'` 或 `--graph-context @file` 时以调用者给的为准（自测与回放用）。

## 4. 回传与回执

- 角色回传：JSON（≤30 行渲染），固定字段按 kind；额外 `flags[]` 与 `memory_delta{}`。
- `role receipt <role> --message-id … --file <回传.json>`：校验幂等键与 schema → 落盘 `roles/receipts/<message_id>.json` → 合并 `memory_delta`（≤12 KB，超限拒绝）→ 推进游标 → 若含 K 工单则 `task return` → 若 kind=CHRONICLE_TURN 则调用 narrative 提交路径（不重复实现）。
- 回执状态：`ANSWERED | ANSWERED_NO_CHANGE | REJECTED(reason)`。
- 会话开始：`role status` 列出 hot 角色、未回执派发（`DISPATCH_UNANSWERED`）、记忆过期（>30 天未更新）；hook 输出进入 context digest。

## 5. 共享底座：研究图（**已由 3.0.0 升为权威关系层**，本节保留为角色视角的摘要）

> 层级、事务、对象索引、实时派生、界面与跨项目以 [v3-contract.md](v3-contract.md) 为准；细则（寻址、派生规则、逻辑边与冲突、鉴权与 pending 生命周期）见 [graph-contract.md](graph-contract.md)。对角色而言唯一的变化是：**图上下文由 CLI 送到手上（§3），边照旧只提议**。

- **统一寻址** `ro:<project>:<space>:<id>`，space ∈ {narr, tree, fig, asset, run, claim, ledger, decision}；`graph resolve <ro>` 只读解析到文件+对象；不迁移任何编号。
- **跨图边账本** `.research-os/graph/edges.jsonl`（辅助追加层）：`{edge_id, from, to, kind: depends_on|supported_by(+|-)|expressed_as|evolved_from, basis, by(role|main|user), at, source_commit?}`；只经 `graph link` 写；`graph unlink` 追加撤销事件不删。
- **谁写什么边**：史官 → 叙事节点间与 narr→claim；图形工程师 → fig→narr `expressed_as`；实验管家 → run→narr/claim `supported_by±`；证据管家 → ledger→narr `supported_by`；理论操作员 → claim→narr `supported_by` 与 claim 演化。全部经主控 `graph link`。
- **派生器** `graph derive`（确定性、只读源）：tree.json 关闭结果 → supported_by；FSG claim_id → expressed_as；资产 produced_by → artifact；不猜、可重跑、幂等。
- **投影** `graph view argument|coverage|role-activity`：论证链（主张→证据→运行→数据）、覆盖矩阵（无图表达的承重主张 / 不表达任何主张的图 / 无证据的 HELD）、角色活动（各分控最近贡献的边）。≤40 行文本；HTML 后置。

## 6. 会话与主控纪律

0. **整个角色层对用户不可见。**角色名、`K##` 工单、信封、回执、记忆增量、`ro:` 地址、`role status` 面板——一律不出现在给用户的话里；用户不需要知道有几个分控、谁在干什么，也永远不必按格式说话或跑命令。用户只跟主控用自己的话讨论，主控负责把自然语言映射成派发。对外只讲结论与影响（"这轮的故事改动已入账"），且只在有信息量时讲。两个角色提议冲突时（§6.4）由主控裁决；确需用户拍板的，按"一句背景 + 两三个选项 + 推荐项"问，用户回一句话即可。约束全文见 *How the user experiences this*（[skills/pilot/SKILL.md](../skills/pilot/SKILL.md)）。

1. 会话开始：读 `role status` 行；对 `REHYDRATE_REQUIRED` 的 hot 角色，按需 `role hydrate <role>` 并 spawn；记录实例 id（`role instance set`）。
2. 派发：`role dispatch` → spawn 或 SendMessage（同会话已有实例优先续聊）→ 回传 → `role receipt`。主控在同一轮内完成，或把未完成派发留给闸门（CHRONICLE_TURN）/ 事故清单（其它 kind）。
3. 并行：不同角色可同轮并行派发（Workflow 模板 `workflows/role-fanout.md`）；同一角色同一时刻只允许一个未回执派发。
4. 冲突：两个角色对同一节点提议冲突 ⇒ 主控裁决并记 decision；不在角色间协商。

## 7. CLI 面（`scripts/role_runtime.py`，复用 turn_runtime 的锁、回执写入与事件登记）
```
role list | enable <role> | disable <role> | status [--json]
role hydrate <role>                      # 档案摘要 + 记忆 + 游标 + 开放工单（≤40 行）
role dispatch <role> --kind … [--k-task …] [--refs …] [--turn current]
role receipt <role> --message-id … --file …
role instance set <role> --agent-id … | clear
role memory commit <role> --delta-file … | show <role>
role self-test
graph resolve <ro> | link --from … --to … --kind … --basis … | unlink <edge_id> --reason … | derive [--dry-run] | view argument|coverage|role-activity | self-test
```

## 8. 验收（机器）
1. 五角色注册、启用/禁用、状态行可打印；hot 判定正确。
2. `dispatch → receipt` 幂等：同 message_id 二次 receipt 返回 `ALREADY_ANSWERED`，记忆不重复合并。
3. 记忆 >12 KB 拒绝；`memory_delta` 合并而非覆盖。
4. 会话重启后 `role status` 列出 `DISPATCH_UNANSWERED`。
5. `graph derive` 在参考项目上跑出 ≥1 条 supported_by / expressed_as 边（若源缺失则明确报 0 并说明），`graph view coverage` 列出无证据的 HELD 承重主张。
6. 多角色烟测：同一轮并行派发史官 + 图形工程师（真 agent），两份回执落盘，星型（无角色互通）由注册表结构保证。
