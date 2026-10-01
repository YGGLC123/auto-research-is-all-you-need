# Narrative Contract v1.2（v2.8 冻结契约 · D1 · 2026-08-25，R8 终审 GO 版）

实现者直接编码的契约。与 v2.8 设计稿冲突处以本文为准。v1.1 = v1 + R7（外部高推理模型终审，NOGO）三条阻断项与 27 条逐节修法；v1.2 = R8（同档复审，GO）三组文字收口：唯一写入链（§0/§6.1/§6.3）、确定性哈希（§6.2）、状态机残余（detach 不含 DEMOTED、branch create 追加 OPEN、merge cause、live-bet 变化必须提交、`turn finalize` 参数统一）。另含两条宿主实况条款（§6.9、§6.10）。

## 0. 三层与唯一写入链

| 层 | 内容 | 规则 |
|---|---|---|
| **权威叙事层** | `objects/`（快照）、`commits/`、`refs/heads/*`、`branches/registry.json`（append-only disposition_history）、`tags/` | **树快照 / commit / ref 只经 `narrative commit` 单一原子事务写入（§6.1）**；`branches/registry.json` 的 disposition 事件与 `tags/` 各走专用 append-only 原子事务（`branch create|promote|kill|drop`、`tag`），**绝不改树/ref**（promote 有分叉时产生的 merge 提交仍经 `narrative commit`） |
| **派生缓存层** | `card.json/card.md`、trajectory、各渲染视图、`index/` | 可删可重建；必带 `source_commit_id`；禁独立编辑 |
| **辅助追加层** | `annotations/`、`incidents/`、`backfill/adjudications/`、`backfill/unmapped.json`、`runtime/`（turn/lease/manifests/receipts/journal） | append-only；不进 tree hash |

写入者只有 `research_os.py`（子模块 `narrative.py`）。史官只**提议**；主控调 CLI；CLI 写权威层并返回回执。对话中的决定在经 decision record / narrative CLI 之前不属于权威状态。

## 1. 节点

```json
{"id":"C-041","node_type":"claim","identity_key":{...},
 "title":"…","summary":"≤2 行","statement":"…",
 "state":{"epistemic":"PENDING|HELD|KILLED","narrative":"ACTIVE|DROPPED|DEMOTED|MERGED"},
 "state_meta":{"resolution_condition":"…（PENDING 且为在飞赌注时必填）","live_bet":false},
 "basis_refs":["E-07"],"disposition_meta":{"target":"…","reason":"…"},
 "lineage":[{"rel":"clarifies|narrows|broadens|replaces|decomposes","of":"C-017"}],
 "children":["C-057","E-07"]}
```
- `id` 正则 `^[A-Z]{1,2}-[0-9]+(\.[0-9]+)*$`（允许章节式点分，如 `N-4.3.2`，供回填沿用叙事台账的节号），永不复用（对象注册表保留全部历史 id）。`node_type` 不可变。
- **什么才是节点（3.0.4 起，准入规则）**：节点 = 论文讲给读者的故事的一部分——对研究对象的断言、一条证据、一个定理、或一步结构性论证（动机/机制/边界/含义）。**不是节点**：措辞与定稿句的编辑决定、待办/计划、进度与状态记录、关于叙事树或流程本身的备注、读者读不懂的片段、与他节点实质重复的条目。这些属于 decision 台账、注释（§10）或 todo 提案，进树即噪声（漂移读数会把它们当故事计）。机器面：`narrative audit` 用两个独立检查员（Claude CLI + Codex CLI，不同模型族）逐节点分类，**双方一致**判为非故事且为末端节点者才可移出（`detach_node DROPPED`，disposition_meta 记审查依据），分歧一律留给主控/用户；带下级的非故事节点不自动动。回填后、大改后必跑一次；史官提议新节点时先过同一准入标准。
- **提交说明面向读者（3.0.4 起）**：`commit.message` 是用户在页面"这一段的历史"里可能看到的一句话，用项目语言写"改了什么、为什么"；页面对每段展示的是**从 ops 派生**的改动描述（"改写了标题与正文""移到了「X」下"），message 只在像人话时作为附注出现，机器形态的 message（回填来源、英文流水）只进"技术细节"。
- **文字面向读者，指针进台账（3.0.3 起）**：`title / summary / statement` 是用户在叙事树页面看到、实时问答引用的全部文字，必须是可读叙述（标题可复述、summary 一两句、statement 成段）。节点/台账指针（`→ R-01`、`T-31`）、状态码（`[critical|CLOSED]`、`HARD_STOP`、`verdict=…`）、版本号与工作台速记**不得**出现在这三个字段，一律作为独立字符串放进 `basis_refs`，前缀 `ledger:`（如 `"ledger:→ R-01"`），页面把它们折在"技术细节"里，溯源不丢。责任归属：**史官**（提议时转写）；回填器搬入的工作台原文视为待转写，转写以 `patch_fields` 提交（身份不变、rev_hash 变），原文留在历史。
- `identity_key` 按类型（claim: subject/target/predicate/quantifier/domain/conditions[]/polarity；theorem: formal_hash/hypotheses[]/conclusion；evidence: estimand/population/sample_window/method/artifact_ref；narrative: role_hint/thesis；artifact: ref/kind；decision: decision_ref）。
- **身份不变式**：`identity_key` 规范化 hash 变 ⇒ 默认新 id + lineage。唯一例外 = 专用 op `rekey_identity(id, old_key_hash, new_identity_key, relation="clarifies", basis_ref→此前冻结定义, approval_ref)`，硬条件：node_type 不变 + old_key_hash 匹配 + 显式批准。`patch_fields` **永不得**改 identity_key / node_type / id。不可判时宁可多建 id。
- 三个裁决案例（进 self-test）：①「门槛非常数」→「门槛是程序的泛函」= predicate 变 ⇒ 新 id，`replaces|narrows of C-041`；②一拆二：结构分解 ⇒ 原节点为父、statement 不动、两子新 id；语义替换 ⇒ 旧节点留历史 + `decomposes`，按事实 KILLED/DROPPED；拆分继承 lineage 不继承身份；③证据替换主张不变 ⇒ claim id 不变、rev_hash 变；若新证据 estimand/population/window 变 ⇒ 先查 claim 的 target/domain/conditions，变了必须新 id。

## 2. 树快照

```json
{"schema":"auto-research/narrative-tree-v1","root":"N-0","nodes":{…},
 "role_assignments":{"headline":"C-057","flagship":null,"backbone":"F-03","empirical_core":"E-07","foil":"C-012"},
 "meta":{"venue":"RFS"}}
```
- `role_assignments` 五键**必须始终存在**，值 `node_id|null`；非空角色必须指向当前树中 `narrative=ACTIVE` 的节点；被降/弃/并的节点**同一提交**必须清空或改派其角色。
- `validate_snapshot`：root 存在且无父；非 root 恰一父；children 引用存在且不重复；全节点从 root 可达；`children[]` 与 manifest 的 parent/order 一致；state 矩阵合法（§4）；角色节点 ACTIVE。
- `meta` 只经 `patch_meta` 改；venue 变化机械归 **restructure**。
- 深度不做存储限制；`render --view axis` 默认展开 4 层。
- 规范化 `canonical_json = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",",":"))`；`rev_hash = sha256(canonical(node − children))`；`tree_hash = sha256(canonical({root, role_assignments, meta, manifest:[[id, rev_hash, parent, order]…]}))`。同一 fixture 连续序列化两次 hash 必须完全一致。

## 3. 提交

```json
{"id":"sha256(canonical(commit_without_id))",
 "tree":"<tree_hash>","parents":["…"],"ref":"main",
 "author":{"session":"W017","role":"chronicler|main|user|backfill"},"origin":"live|backfill",
 "at":"…","message":"…",
 "kind":"refine|restructure|overthrow|merge",        // 由 CLI 机械判定；史官只给 proposed_kind
 "cause":"none|forced|unforced|unknown","forced_status":"none|candidate|confirmed",
 "trigger_ref":"…|null","trigger_class":"refutation|null-result|theorem-failure|novelty-collision|contract-incompatibility|review-pressure|user-taste|scope|other|null",
 "counterfactual":"…（confirmed 必填）",
 "approval_refs":{"overthrow":"decision:…|null","forced_cause":"decision:…|null"},
 "ops":[…],"inverse_ops":[…],"flags":["LARGE_RESTRUCTURE","KIND_CORRECTED"],
 "input_manifest_id":"im_…|null（backfill 为 frame_id）"}
```
### 3.1 kind 机械判定
`set_root` 且新 root 的 identity_key hash ≠ 旧 root ⇒ **overthrow**；否则任一 `set_role` 变化 / `patch_meta.venue` 变化 / 承重节点（root 直接子节点或任一角色节点）的 add/detach/move/split/merge ⇒ **restructure**；否则 **refine**；双亲提交 = **merge**。`LARGE_RESTRUCTURE`：一次 ≥3 个角色变化（非阻断）。`origin=backfill` 不改变 kind。
**merge 的 cause**：仅表达字段（title/summary/statement）合并 ⇒ `none/none`；改变角色、根或承重结构的 merge ⇒ 走 §3.2 结构变化矩阵；更换根身份仍需 `approval_refs.overthrow`。

### 3.2 cause / forced_status 唯一矩阵
| 情形 | cause | forced_status |
|---|---|---|
| refine | none | none |
| 结构变化，无合格 trigger | unforced | none |
| 结构变化，trigger 通过四前件但未确认 | unknown | candidate |
| 反事实已写 + `approval_refs.forced_cause` 已签 | forced | confirmed |
| 回填未裁 | unknown | none |
| 回填盲裁非受迫 | unforced | none |
| 回填盲裁受迫 | forced | confirmed |

铁律 `cause=forced ⟺ forced_status=confirmed`。四前件：trigger_ref 存在 / 早于本提交 / 已裁决 / 触及被改节点或其角色。

### 3.3 操作集（树操作；每个 op 应用前由 CLI 从旧快照生成并持久化 `inverse_ops`，revert 只用实存 inverse_ops）
`add_node(node)` · `patch_fields(id, {title,summary,statement,state,state_meta,basis_refs,disposition_meta,lineage})` · `patch_meta(fields)` · `move_node(id,new_parent,index)` · `detach_node(id, narrative_disposition∈{DROPPED,MERGED}, disposition_meta)`（已提交节点禁物理删；**DEMOTED 不可 detach**——降级节点留在树内、可 `move_node` 到附录/支撑子树，保持从 root 可达，供 LEVER_DEBT 读取）· `delete_uncommitted(id)` · `split_node(id, parts[], mode∈{structural,semantic})` · `merge_nodes(ids[], into)` · `set_role(role, id|null)` · `set_root(new_root_id)` · `rekey_identity(...)`。
支线处置**不是树操作**（§5）。

### 3.4 推翻闸（唯一硬闸）
`set_root` 导致 root 身份变化 / root detach / root merge 进新语义节点 / 支线不同 root 提升为 main root ⇒ 要求 `approval_refs.overthrow` 非空且指向 user-approved decision record；否则 exit 1 `OVERTHROW_REQUIRES_USER_APPROVAL`，refs/快照/preview 全不变。

## 4. 状态矩阵与硬规则
- 合法组合：`ACTIVE|DEMOTED|MERGED` × `PENDING|HELD`；`DROPPED` × `PENDING|HELD|KILLED`。`KILLED` 不得 `ACTIVE`。
- `KILLED` ⇒ basis_refs 含合格依据（refutation/null-result/theorem-failure/novelty-collision/contract-incompatibility/external-fact/review-finding-with-evidence）。`HELD` ⇒ basis_refs 非空或指向 user-approved decision record。`DEMOTED`/`MERGED` ⇒ `disposition_meta.target` 必填。
- 未闭环：`DROPPED` 且 `epistemic≠KILLED` 且无裁决 basis。
- 派生事件：`RESTORED`（{DROPPED,DEMOTED}→ACTIVE，记 prior_role/source_commit/reverts_commit?）；`ROLE_REVERSAL`（60 天内无新反驳而降/弃后又复）；**LEVER_DEBT**：X 曾任 headline/flagship/backbone ∧ 当前 HELD+DEMOTED ∧ 降级提交 cause∈{unforced,unknown} ∧ 无 KILLED basis ∧ **replacement 不存在**（不存在 HELD+ACTIVE 的 Y 使 `Y.lineage ∋ {replaces, of:X}` 且 Y 接替 X 原角色）⇒ 当轮上卡 ⚠；**LOST_LEVER**：曾任 headline/flagship ∧ DROPPED ∧ 无裁决依据。
- live bets 投影：`PENDING ∧ (承重 ∨ state_meta.live_bet) ∧ resolution_condition 非空`。

## 5. 支线
- `refs/heads/<name>` 是唯一 head；`branches/registry.json[name] = {created_from:{ref,commit}, disposition_history:[{disposition, basis_refs, reason, by, at, source_commit_id}]}`；当前 disposition = 末条投影；registry **不存 head**。**`branch create` 原子追加第一条 `OPEN` 事件**（末条投影在新支线上因此有定义）。disposition 事件写入是独立 append-only 原子事务，不改树/ref。
- 每 ref 一份 **preview** `working/<ref>.json`（带 `base_commit_id`，可删可重建，非权威）。
- `promote` = merge 策略：无分叉 fast-forward；有分叉 ⇒ 双亲 merge 提交。v2.8 自动合并仅限互不相交的表达字段（title/summary/statement）；parent/order、state、identity、role、detach、split/merge 全进冲突清单，用户裁。
- `kill` 需合格 basis；`drop` 显式；系统永不自动弃。`UNRESOLVED_BRANCH` = OPEN ∧ head ∉ main 祖先 ∧ 无活跃 K## 引用。

## 6. 事务、输入清单与收工闸门

### 6.1 唯一事务
`narrative commit --ref R (--ops-file F | --pending-patch P) --meta-file M` 在一个 journal 事务内写：快照对象 → commit 对象 → ref 更新 → preview 刷新（base=新 commit）→ 角色 memory delta（若给）→ 回执。journal → 临时文件 → fsync → 原子重命名 → 完成标记。中途崩溃：重启 `journal replay`，禁止让模型重生成 ops；命中已有 commit（幂等键）⇒ 补齐缺失 memory/receipt，返回 `ALREADY_COMMITTED_RECEIPT_REPAIRED`。`node …` 子命令只生成/校验 ops；`apply` 只产 preview + `pending_patch_id`（一次性消费）。**唯一写入链**：树快照 / commit / ref 只有这一条写入路径；`turn record-disposition` 不是第二条路径——它只是编排入口，内部调用同一 `narrative commit` 事务（无树变化时只写回执），**回执一律由 CLI 构造**，模型永不提供回执内容。

### 6.2 InputManifest（CLI 生成；模型永不提供 digest）
`{id: im_<sha16>, turn_uuid, seq, frozen_at, base: prev_manifest_id|base_input_snapshot, refs:[{path, sha256, kind, event_id?}], events:[{event_id, type, payload_sha256}], source_delta_digest}`。**确定性哈希**：`source_delta_digest = sha256(canonical({refs: 按 path 升序的 [{path, sha256, kind}], events: 按 event_id 升序的 [{event_id, type, payload_sha256}]}))`；`id = "im_" + sha256(canonical({turn_uuid, seq, base, source_delta_digest}))[:16]`；mtime 仅诊断不入 digest。（tree manifest 的排序：root 深度优先、同父按 order；见 §2。）turn 打开时记 `base_input_snapshot`；manifest n 比 n−1。Input domain = 项目内非派生文件；Derived domain（`.research-os/narrative/`、runtime、card、渲染、回执）不回流。**所有 mutating narrative CLI 写入前必须绑定现有 manifest，或先登记 synthetic input event 再开事务**；manifest = 路径变化 ∪ 显式事件。

### 6.3 turn 与两段关闭
`runtime/turn.json = {turn_uuid, display_id:"W017.T0007", session, opened_at, status:OPEN|CLOSED, base_input_snapshot, manifest_ids[]}`。
- `turn record-disposition --manifest … (--ops-file … --meta-file … | --no-change --reason …)`：编排入口——有树变化时调用 §6.1 同一 `narrative commit` 事务并由 CLI 构造 `NARRATIVE_COMMITTED` 回执；无树变化时只写 `NARRATIVE_REVIEWED_NO_CHANGE` 回执；turn 仍 OPEN。
- `turn finalize`（无参数；Stop hook 调用）：重扫输入 → 冻结后有新 delta ⇒ 开新 manifest 要求处置 → 每个 manifest 恰一个有效回执 ⇒ CLOSED；不能关则 exit 1 并回显 `CHRONICLE_REQUIRED turn=… manifest=…`。`turn finalize --check-only` 仅诊断不关闭。

### 6.4 回执四态（`runtime/receipts/<turn_uuid>.<manifest_id>.json`）
`NO_INPUT_CHANGE`（CLI 自动）/ `ACK_NON_NARRATIVE`（有持久变化但不触及叙事；CLI 自动）/ `NARRATIVE_COMMITTED{commit_ids[]}` / `NARRATIVE_REVIEWED_NO_CHANGE{reason}`（史官审阅后判无树变化；`commit_ids=[]`）。`ESCAPED` 只能是 `incidents/` 记录（`turn escape --reason --user-approved`），不得伪装为正常回执。

### 6.5 narrative_bearing 机器下界（模型只能 false→true）
manifest 触及 `narrative/config.json.bearing_globs`（默认 `*叙事*`, `*narrative*`, `decisions.md`, `*决策*`, `*旗舰*`, `*路线*`, `*裁决*`, `*传递*`, `pro_reviews/**`）/ 任一 narrative CLI 事件 / claim state 变化 / decision record / branch merge·kill·promote / role 变化 ⇒ true。

### 6.6 单写者租约
`runtime/lease.json = {holder_session, holder_token, acquired_at, heartbeat_at, expires_at(15 min)}`。每次写命令自动心跳；第二会话所有写命令拒绝 `PROJECT_LEASE_HELD_BY W017`；过期或 holder 已死 ⇒ `recovery_takeover`（不计普通 force incident）；状态不明且未过期 ⇒ 只读 + 需 `lease take --force --reason`（记 incident）。

### 6.7 SessionStart 顺序（hook 只报警置标志；主线强制第一步执行）
查 OPEN turn → 查旧 holder 存活 → 死则 recovery_takeover → 不明且未过期则只读并要求显式 force → **取得租约后才恢复 turn**（重建史官实例，重放同一 manifest + 幂等键）。会话实例可死，turn 不随会话死。

### 6.8 八条闸门回归路径（self-test 必含）
空输入 / 非叙事输入 / 叙事输入缺回执 / bearing 误报无树变化 / manifest 冻结后新增输入 / commit 后 receipt 前崩溃 / OPEN turn 会话恢复 / 第二写者租约拒绝与 recovery takeover。

### 6.9 宿主实况：Stop 可被绕过
Stop hook 可被 Ctrl+C、`--no-hooks`、进程被杀绕过；Windows 跨会话 PID 判定不可靠。因此 `turn finalize` 是 best-effort；**漏关 turn = 下次 SessionStart 必修的 incident 路径**（§6.7），契约不假设它不发生。

### 6.10 宿主实况：事件登记兜底
模型可能绕过 CLI 直接写文件；auto-compact 会抹掉史官上下文。因此：PostToolUse hook 对命中 bearing_globs 的 Write/Edit 自动登记 synthetic input event（纯路径匹配）；journal replay 永不依赖模型重生成 ops。

## 7. 史官
- 档案 `agents/research-chronicler.md` + 项目记忆 `.research-os/roles/chronicler.memory.json`（≤12 KB）。
- 信封 `CHRONICLE_TURN/v1 = {message_id, turn_uuid, manifest_id, project, bearing_reasons[], source_refs[≤16], objective(固定), constraints, idempotency_key}`（≤8 KiB）。
- 回传固定四字段（≤30 行）：`proposed_ops[] / commit_meta{proposed_kind, cause_proposal, trigger_ref, trigger_class, counterfactual?, message} / flags[] / memory_delta`。**无 card_delta。**CLI 重算 `actual_kind`，不一致以 CLI 为准并在回执记 `KIND_CORRECTED`。
- 幂等键 `sha256(project|turn_uuid|manifest_id|chronicler)`。

## 8. 当前叙事卡（派生）
`card.json = {source_commit_id, narrative_part:{north_star, roles, exclusions[{node, reason, basis_refs}], live_bets[], lever_debt[], lost_levers[], unresolved_branches[]}, control_part:{turn, lease, last_receipt, incidents_open}}`。`narrative_part` 随 main 提交重算，`control_part` 随 turn/lease/receipt 事件重算；**只从 `refs/heads/main` 的已提交快照派生**，禁从 preview 派生。`card.md` ≤120 行；给主线 ≤40 行。

## 9. trajectory（≤40 行，固定预算：Current 4 / Graph 14 / open loops 6 / hotspots 5 / unresolved 5 / history+anomaly 6；溢出只写 `… +N; FULL:<pointer>`，不得挪用其他区块额度）
读数：`structural_commits_N`、`unforced_structural_N`（含 cause=unknown 单列）、`headline_identity_changes_N`、`structural_stability_days`（refine 不重置）、`open_branch_count(+unresolved)`。只报不拦，不做综合分。执行期不得读原始历史 md。

## 10. 注释（辅助层）
`annotations/<node_id>/<annotation_id>.json = {node_id, commit_id, kind: qa|note|review, question, answer?, by, at, supersedes?}`；校验 node_id 存在于 commit_id 快照；创建后不可覆盖，只能追加 superseding。
- **文献联动（3.0.5 起，仅树形结构）**：段落旁的相关研究与每篇文献的三行报告（贡献 / 相对本文的边界 / 本文怎么用它）来自文献台账，规则见 [literature-ledger.md](literature-ledger.md)；台账是读到的关系，裁定仍走研究图。
- **实时问答（3.0.3 起，`narrative serve` / `scripts/narrative_serve.py`）**：仅监听 127.0.0.1 的页面服务；每次加载重新渲染 head 快照；`POST /api/ask {node_id, question}` 把该节点的上下文（主线、路径、正文、下级、同级、历史、既有问答、去编号后的 trajectory）连同固定简报交给 Claude CLI 打印模式（无工具、单轮、cwd=临时目录以免触发项目 hook），答案按 NDJSON 流回页面，落盘为一条 `kind=qa, by=chronicler-live, adjudicated=true` 的注释——与史官手写的问答同一记录、同一规则（不覆盖、只追加）。它写的只有这一条注释，不碰树、不碰 refs。file:// 静态页没有服务时，提问只记为"待回答"并复制到剪贴板，不自动下载任何文件。模型默认 `opus`，项目 `narrative/config.json` 的 `live_qa_model` 可覆盖。

## 11. 冻结标签（权威层）
`tags/<name>.json = {commit_id, kind: frozen|submitted, by, at, note}`；名字不可复用不可移动，同名创建拒绝。无 CERTIFIED/STALE 状态机。

## 12. 回填
- 只吃显式编号版本；每快照生成 Historical Frame `frames/<id>.json = {as_of, source_manifest, root_statement, target, venue, evidence_standard, known_basis_refs[], open_questions[], decision_refs[]}`。
- 处理 S_t→S_{t+1} 只准读时间戳 ≤ S_{t+1} 的材料（文件访问日志可审计）；无当时 trigger ⇒ `cause=unknown`。
- **kind 仍机械判 refine/restructure/overthrow**，来源用 `author.role=backfill, origin=backfill`；不得覆盖 kind。
- 盲裁 `backfill/adjudications/<id>.json = {frame_id, before_commit, after_commit_candidate, blind_decision{cause, forced_status, trigger_ref?}, approval_ref, retrospective_notes[], supersedes?}`；第一遍不显示后来的叙事；旧 adjudication 永不覆盖。无编号承重段落进 `backfill/unmapped.json`，不猜现有 id。

## 13. NarrativePatch（UI 往返）
`{schema:"auto-research/narrative-patch-v1", project, ref, base_commit_id, base_tree_hash, tree_ops[§3.3 子集], todo_proposals[]→K##, generated_at}`。`apply` = base 校验 + preview + `pending_patch_id`；`commit --pending-patch <id>` 一次性消费。Patch ≠ commit；不做三方合并。

## 14. CLI 面
```
narrative init | node add|patch|patch-meta|move|detach|split|merge|set-role|set-root|rekey-identity (生成/校验 ops)
narrative commit --ref … (--ops-file … | --pending-patch …) --meta-file …
narrative branch create <name> --from <ref>[@commit] | promote <b> [--into main] | kill <b> --basis … | drop <b> --reason …
narrative log [--ref] [--graph] | blame <node> | diff <a> <b> | revert <commit> | trajectory [--window 30]
narrative render --view axis|reader|internal|todo|tiers [--html] | card [--md] | annotate … | apply --patch … | tag --freeze <name> --approval …
narrative backfill --sources <dir> | backfill adjudicate --blind | backfill status
narrative validate | journal replay | self-test
turn probe | turn record-disposition … | turn finalize [--check-only] (Stop hook 调无参数版) | turn escape --reason … --user-approved
lease status | lease take [--force --reason …] | role hydrate chronicler | role memory commit --delta-file …
```

## 15. 完成定义（机器可测五断言 + 人工验收门）
1. init → commit A 加 C-001 → commit B 只改 statement ⇒ `refs/heads/main==B`；current tree 取自 B 快照非 preview；diff 恰为一个 `patch_fields(C-001, statement)`；id 不变 rev_hash 变；无行级 diff；validate_snapshot 全过；同一 fixture 两次序列化 hash 一致。
2. 从 main 建 `alt-headline` → 支线一次 restructure → 移除所有活跃 K## → 推进 90 天多次跑 doctor/trajectory ⇒ disposition 恒 OPEN；持续输出 `UNRESOLVED_BRANCH`；零自动 DROPPED。
3. 无 overthrow approval 执行 `set_root` ⇒ exit 1 `OVERTHROW_REQUIRES_USER_APPROVAL`，refs/快照/preview 不变；三次 Stop 检查均同一 `CHRONICLE_REQUIRED`，无回执无 incident；补 approval 后成功且 kind=overthrow。
4. 五例：无 delta ⇒ `NO_INPUT_CHANGE` + finalize 成功；只改非叙事源码 ⇒ `ACK_NON_NARRATIVE` + 成功；改 bearing 路径无回执 ⇒ `CHRONICLE_REQUIRED`；冻结后新增输入 ⇒ 旧 manifest 不得 finalize、须新 manifest；bearing 被改但史官判无变化 ⇒ `NARRATIVE_REVIEWED_NO_CHANGE` 且无伪 commit。终断言：CLOSED turn 每个 manifest 恰一个有效回执。
5. 固定七轮 fixture（null-result forced restructure ×1、review-pressure unforced restructure ×1、HELD+DEMOTED flagship、DROPPED 旧 headline、OPEN live bet、盲裁回填链、user-approved adjudication）⇒ trajectory ≤40 行；出现 forced commit 及 trigger、unforced review restructure、LEVER_DEBT、LOST_LEVER、live bet、unresolved branch；root/headline 读数与 fixture 一致；执行期文件访问日志不含原始 md；adjudication 的 source hashes 与最终 DAG 一致。
- **人工验收门**（不声称机器可证）：用户认参考项目的回填历史为自己经历过的历史。

## 16. 开工顺序（D1 不过不进代码主干）
- **D1**：只改契约 + golden fixtures（矩阵、事务路线、四态回执、真实 kind、validator、三新 op）。验收：hash 稳定；非法 state/孤儿/重复 parent/DEMOTED 角色节点全拒；candidate/confirmed/unforced 无多解。
- **D2 ∥**：A 存储（注册表/validator/hash/refs/atomic commit-from-ops/preview）；B turn runtime（lease/OPEN turn 恢复/base snapshot/manifest 链/回执/finalize/journal replay）；C 验证减重（独立）。验收：第二会话写入稳定被拒；崩溃重启续同一 turn_uuid；commit 后 refs/快照/preview 一致；空与非叙事 manifest 自动正确回执。
- **D3**（依赖 D2-A）：提交语义与派生（kind 判定、矩阵、branch create/promote/kill/drop、root 闸、role、LEVER_DEBT/LOST_LEVER/RESTORED/ROLE_REVERSAL、card narrative_part）。验收：main→A/B→A refine→B kill→A promote 的 log/diff/disposition_history 正确；无批准 root 替换拒；HELD+DEMOTED 前旗舰当轮 LEVER_DEBT；不自动弃。
- **D4**（依赖 D2-A+B、D3）：史官整合（档案/实例/信封/proposed ops/memory_delta/CLI 重算 kind/bearing→commit 或 no-change 回执/crash replay）。验收：bearing 无回执 ⇒ Stop 拒；`NARRATIVE_REVIEWED_NO_CHANGE` 后可收工；"commit 已落盘 receipt 未落盘"崩溃后重放只一个 commit 且回执补齐。
- **D5**（D4 后半并行，依赖 D3 稳定）：历史回填解析器（显式 id、Frame、时间可见性过滤、盲裁队列、unmapped）。验收：读不到晚于 S_{t+1} 的文件；无当时 trigger ⇒ unknown；kind 非 backfill；无编号段落只进 unmapped。
- **D6**（依赖 D5）：参考项目盲裁、提交 DAG、card、trajectory、读数。验收：机械回答改了哪些节点/root 身份换几次/headline 换几次/哪些 confirmed forced/哪些 unforced/LEVER_DEBT 与 LOST_LEVER/unresolved；全程不读原始 md。
- **D7**：停止开发只跑全量验收（三身份案例、五断言、七轮剧本、八路径、一个真实项目工作日；至少一次 `NARRATIVE_REVIEWED_NO_CHANGE`、一次 session recovery、一次 lease 冲突、一次被拦的 root 替换）。只许修 bug，不加 schema/状态/命令。

## 17. 七轮场景在本契约下的机器行为（self-test 固定剧本）
1 正常精炼 ⇒ refine/none。2 收到冷评未改故事 ⇒ bearing=true（pro_reviews）→ 只登记冷评为 review annotation/event而不动树时 ⇒ 史官 `NARRATIVE_REVIEWED_NO_CHANGE`；若因此创建/修改 PENDING live-bet 节点（`resolution_condition`）⇒ 这已是树变化，必须产生 `NARRATIVE_COMMITTED` 的 refine/none 提交。3 冷评压力下反应式换头条 ⇒ restructure，headline/flagship/backbone 同变 ⇒ LARGE_RESTRUCTURE，trigger_class=review-pressure，无实证否定 ⇒ (unforced,none)；若四前件通过 ⇒ (unknown,candidate)。4 仍成立的旗舰被降附录 ⇒ 同一提交 set_role(flagship,null|新) + X→HELD+DEMOTED；restructure/unforced；**当轮 LEVER_DEBT 上卡 ⚠**。5 三周只精炼 ⇒ structural_stability 不重置误导，但 LEVER_DEBT 持续显示。6 救回 ⇒ X→ACTIVE + set_role 派生 RESTORED；60 天内无新反驳 ⇒ ROLE_REVERSAL。7 再冷评 ⇒ 同 2/3 路径，契约层已闭合。
