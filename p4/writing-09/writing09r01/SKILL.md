---
name: results-discussion-writing
description: >
  结果与讨论章节写作 Skill。当任务要求依据给定科研材料（论文、数据表、图、图注、统计输出、
  参考文献）产出可直接投稿的结果段与讨论段，或进行图表读数转写、趋势与差异表述、讨论证据
  对照、因果边界控制、局限说明、双语/结构化交付时使用。核心约束：结果段只陈述观察事实，
  讨论段承担解释与文献对照；每个数值与论断都必须回溯到 locator + 逐字 quote；区分已完成与
  拟开展，区分相关/预测/因果/推测；在 /app/output 交付可复核的 result.json。Use for writing
  Results & Discussion sections with evidence traceability from provided research materials.
version: 1.1.0
---

# 角色与何时使用

本 Skill 把"数据/图表已在手"的散乱科研材料转成**可投稿的结果段 + 讨论段 + 证据溯源表**，
而不是写作建议或模板套用。满足任一即调用：

- 任务含"结果 / 讨论 / Results / Discussion / 图表读数 / 趋势与差异 / 因果边界 / 局限"等；
- 要求依据给定材料产出可编辑章节稿、改写、校对、引用/格式结果或结构化写作产物；
- 要求保留来源、区分"已完成/拟开展"、交付 result.json。

不适用：纯文献泛读、无材料的事实性问答、非写作类数据分析。

# 输入契约（运行时，Linux 固定路径）

- `/input/case.json`：case_id、任务说明、output_contract（result.json 必需字段）、材料清单、
  受众/文体/格式约束（可能另在 organizer-workorder.json）。
- `/input/task.md`：题目与评分要求，属事实底稿一部分。
- `/input/` 下其余文件：材料原文（PDF/文本/表格/图注/统计输出等）。PDF 用平台
  `pdf-text-tool` / `pdf-render-tool` 解析，不读二进制；**只依据实际读到内容写作，
  不依据文件名/领域标签补造数据**。
- 工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`（含 result.json）。

先读文件再写。第一步运行：
`python scripts/main.py read --input /input --work /app/work`
产出 `/app/work/input_summary.json`（含 case_id、output_contract 字段、材料清单、errors）。

# 输出契约（必须满足）

`/app/output/result.json` 必须包含：

1. `case_id`：与 `/input/case.json` 一致；
2. `output_contract` 声明的**全部字段**，字段名原样保留、不增删改名；若未声明，至少含
   `deliverable`（可编辑章节稿内容与文件路径）、`facts_sources`（数字/术语/引用/图表/关键
   论断的逐条来源映射）、`version_boundaries`（修改差异、人工确认项、材料缺口、异常）；
3. 附加字段：`status`（`complete|partial|failed|insufficient_evidence|input_error`）、
   `sources`、`uncertainty`、`pending_human`、`change_log`、`evidence_map`。

章节稿正文写入 `/app/output/`（如 `results_discussion.md`，或 output_contract 指定文件名），
result.json 以路径引用它。可编辑 Office/幻灯片/图示必须附真实渲染的 PDF/PNG 预览。

**evidence_map 结构（本版强化：每条结论都带 locator + 逐字 quote）**：条目含 `claim_id`、
`claim`、`claim_type`、`section`、`locator`、`quote`（逐字）、`value`（数值/单位/统计量/p 值）、
`confidence`、`status`；讨论/因果/推测条目额外要求 `basis`、`strength`、`causal_boundary`。
完整 schema 与四问落表见 `references/evidence-map-and-checklist.md`。

# 执行流程（总预算 900s / 64 次模型请求，单响应 ≤16384 token）

时间纪律：**前 3 分钟内必须落盘一版 result.json 骨架**，此后只做增量改进；每次改动立即
写回文件，不得只在会话里"记住"。

1. **读取与归一化（≤60s，1–2 次请求）**：`python scripts/main.py read`；通读
   case.json / task.md / 材料，记下受众、文体、格式、output_contract 字段。输入→输出：
   `/input` → `/app/work/input_summary.json`。
2. **落骨架（≤120s，1 次请求）**：`python scripts/main.py skeleton`（或 `run`），生成含
   case_id + 全部必需字段（null/空占位）+ status + sources/uncertainty/change_log/
   evidence_map 的 result.json。**此步完成即达成"可提交的最小诚实交付"。**
3. **读图表与要素规划（≤240s，8–12 次请求）**：把图表数值、单位、误差棒含义、统计量、
   坐标刻度读入 evidence_map 的 `value`；区分"结果段事实 vs 讨论段解释"、已完成/拟开展；
   逐条填实骨架。输入→输出：input_summary.json → 填充后的 result.json 骨架。
4. **成稿（≤240s，10–16 次请求）**：写章节稿正文到 `/app/output`，填充 deliverable/
   facts_sources/version_boundaries/evidence_map；每个数值与论断可回溯到 locator + 逐字 quote。
5. **自检与回修（≤180s，6–10 次请求）**：`python scripts/main.py check --summary
   /app/work/input_summary.json`；按自检清单逐项修复，结论写回 change_log。
6. **收尾（剩余时间）**：补渲染预览、写 uncertainty/pending_human（未确认项），
   置 status=complete 或 partial。

保留约 20% 请求与时间余量用于异常重试；超时即冻结现状，诚实标注 partial/failed。

# 自检清单（逐条勾选，括号内为对应评分维度）

- [ ] 任务完成度【实际效果40】：交付可编辑的结果段 + 讨论段 + 证据溯源表，而非建议/提纲。
- [ ] 结果正确性【实际效果40】：每个数值/单位/误差棒/统计量/推断强度与原始图表一致，无材料外新增事实。
- [ ] 结果完整性【实际效果40】：图表、阴性结果、不一致文献与局限如实交代；结果/讨论两节不互相顶替。
- [ ] 结果可核验性【实际效果40】：段落→结论→证据三级对应，每条结论有 locator + 逐字 quote。
- [ ] 一致性·对得上【使用增益25】：讨论解释↔结果数据↔文献逐条对应，语气强度匹配证据强度。
- [ ] 效率【使用增益25】：脚本归一化输入→自动落骨架→自动回检，减少起草、核对、返工往返。
- [ ] 来源可溯·查得到【使用增益25】：facts_sources + evidence_map 覆盖关键论断，区分已完成/拟开展。
- [ ] 方法改进【创新增益15】：图表读数→结果/讨论分离→证据溯源→因果边界→禁词回检由脚本强制执行。
- [ ] 无空话修饰·说得准【创新增益15】：全文无"首次/国际领先/填补空白/颠覆性"等无据措辞。
- [ ] 调用稳定【可靠体验10】：换图/换学科/换数据量后按脚本产出口径一致的稿件与 result.json。
- [ ] 异常已处理【可靠体验10】：缺图/缺统计量/单位不明/引用找不到/读数不清已写入 uncertainty。
- [ ] 资源可复用【生态贡献10】：SKILL.md、evidence_map schema、检查清单、降级规则齐全，可独立使用。
- [ ] 结构合法【全部】：result.json 含 case_id + 全部 output_contract 字段 + status/sources/
  uncertainty/pending_human/change_log/evidence_map，可解析。
- [ ] 未编造【全部】：status/sources/uncertainty/change_log 与真实执行一致，未写任何评测分数或伪造运行结果。

# 降级策略（禁止编造）

- 材料缺失/字段不明：写入 uncertainty 或 version_boundaries 标注"缺失/无法判断"，字段留空
  或写 null，**不补造数值、文献、作者、政策口径**。
- 引用无法核实：保留原文位置并标 `unverified`，不伪造 DOI/URL。
- 软件/工具不可用（无 pdftotext、office 渲染等）：如实写 limitations，交付纯文本/结构化产物，
  不假装已渲染。
- 输入超长：截取关键段落并在 uncertainty 说明"已截断/未覆盖"。
- 运行失败：status=failed，change_log 记录失败原因与已完成中间产物，result.json 仍须落盘。
- 详见 `references/degradation-and-verification.md`。

# 脚本与资源（相对路径，从 SKILL.md 所在目录执行）

- `scripts/main.py`：子命令 `read | skeleton | patch | check | run`，纯标准库。
- `scripts/core_impl.py`：输入归一化、骨架/合并、校验实现（main.py 导入）。
- `scripts/selftest.py`：空输入/坏 JSON/坏编码/证据校验/禁词六组自检。
- `references/evidence-map-and-checklist.md`：evidence_map 全 schema 与四问落表、一致性清单。
- `references/degradation-and-verification.md`：降级、已完成/拟开展标记与核验规则。

# 最小调用示例（示意，非正式案例结果）

示意输入：`/input/case.json` 含 `case_id="demo"` 与
`output_contract={"deliverable":..., "facts_sources":[...], "version_boundaries":{...}}`。

```bash
python scripts/main.py run --input /input --out /app/output --work /app/work
# ... 增量填充 result.json（模型写 patch.json 后）
python scripts/main.py patch --out /app/output --patch /app/work/patch.json
python scripts/main.py check --out /app/output --summary /app/work/input_summary.json
python scripts/selftest.py
```

示意结构（示例，非正式案例结果）：
`{"case_id":"demo","deliverable":{"file":"results_discussion.md"},"facts_sources":[...],` +
`"version_boundaries":{},"status":"partial","sources":[...],"uncertainty":[],` +
`"pending_human":[],"change_log":[...],"evidence_map":[{"claim_id":"R1","locator":"...","quote":"..."}]}`。
