---
name: results-discussion-writing
description: >
  结果与讨论章节写作 Skill。当任务要求依据给定科研材料（数据表、图、图注、统计输出、
  文献、实验记录）产出可投稿的 Results / Discussion 章节稿，或进行图表读数转写、结果描述、
  趋势与差异表述、讨论证据对照、因果边界控制、局限性说明、校对与来源核验时使用。
  必须区分“结果（只陈述观察事实）”与“讨论（解释、对照、局限）”，为每个数值与关键论断
  保留 locator + 逐字 quote 的来源映射，并把材料缺失、冲突、无法核实与机制推测显式写入
  uncertainty / limitations / pending_human，绝不编造数值、文献或软件状态。
  Use for writing Results & Discussion sections from provided data, tables, figures,
  statistics and references, with traceable evidence and explicit uncertainty handling.
version: 1.0.0
---

# 角色与何时使用

本 Skill 把“数据与图表已在手”的科研材料转成**可编辑、可复核、可直接投稿的结果段与讨论段**，并附带来源映射与边界说明。满足任一即调用：

- 任务含“结果 / 讨论 / 图表读数 / 趋势 / 显著性 / 因果 / 局限 / 结果与讨论章节”等；
- 要求依据给定材料产出可编辑章节稿、改稿、校对、引用或结构化写作产物；
- 要求保留事实来源、区分“已完成/拟开展”或“结果/讨论”，交付 result.json。

不适用：纯文献泛读、非写作类数据分析、测试题生成。核心约束贯穿全程：**读得准、分得清、说得住、核得了**（数值与单位/误差棒含义准确；结果与讨论不互相顶替；语气强度匹配证据；每条数值与论断可回溯）。

# 输入契约（运行时，Linux 固定路径）

- `/input/case.json`：`case_id`、任务说明、`output_contract`（result.json 必需字段）、材料清单、受众/文体/格式约束。
- `/input/task.md`：题目与评分要求，属事实底稿的一部分。
- `/input/` 下其余文件：材料原文（PDF/文本/表格/图/统计输出）。PDF 用平台 `pdf-text-tool` 解析，不读二进制；**只依据实际读取到的内容写作，不依据文件名或领域标签补造数据**。
- 中间产物 `/app/work/`，最终产物 `/app/output/`（含 `result.json`）。

第一步先运行：

```bash
python scripts/read_input.py --case /input/case.json --task /input/task.md --input-dir /input --work /app/work
```

产出 `/app/work/input_summary.json`（含 `case_id`、`output_contract`、材料清单、`degradations`）。

# 输出契约（必须满足）

`/app/output/result.json` 必须包含：

1. `case_id`：与 `/input/case.json` 一致；
2. `output_contract` 声明的**全部字段**，字段名原样保留、不增删改名；若未声明，至少含
   `deliverable`（可编辑章节稿标题、文件清单与摘要）、`facts_sources`（数字/术语/引用/图表/关键论断的逐条来源映射）、
   `version_boundaries`（修改差异、人工确认项、材料缺口、异常、局限）；
3. 信封字段：`status`、`sources`、`uncertainty`、`pending_human`、`limitations`、`change_log`、`runtime`。

`status` 取值：`in_progress | partial | complete | insufficient_evidence | failed`。

章节稿正文写入 `/app/output/`（默认 `manuscript.md`，或 `output_contract` 指定文件名），result.json 以路径引用。可编辑 Office/幻灯片/图示还须附真实渲染的 PDF/PNG 预览；无法渲染时如实写 `limitations`，交付纯文本。

# 证据、强度与不确定性（本版核心）

- **来源映射**：每条 `facts_sources`/`sources` 项必须含 `locator`（`文件:页/段` 或 `表/图编号`）与逐字 `quote`；无法核实给 `status=unverified`，材料未提供给 `status=not_reported`，推断项给 `status=inferred`。
- **语气强度阶梯**：`observed`（已观察事实）→ `statistical`（有检验）→ `directional`（趋势未达显著）→ `comparative`（组间比较）→ `speculative`（机制推测）→ `external`（文献对照）。正文措辞必须与证据等级一致：未达显著不得写成“升高”，相关不得写成“导致”。
- **不确定性类型**：`missing_data` / `unit_ambiguous` / `significance_unstated` / `source_conflict` / `mechanism_speculation` / `causal_boundary` / `truncated` / `tool_unavailable` / `pending_human`。每一项写入 `uncertainty`，需要人工确认的另入 `pending_human`。
- **分节约束**：结果段只陈述观察事实；讨论段才承担解释、文献对照与局限。冲突信息两个来源都记录并标 `source_conflict`，不隐藏。
- 详见 `references/evidence-claim-uncertainty.md` 与 `references/degradation-rules.md`。

# 执行流程（总预算 900 s / 64 次模型请求）

**时间纪律：前 3 分钟内必须用 `build_result.py` 落盘一版 result.json 骨架，之后只做增量改进；每次改动立即写回文件，不得只在会话里“记住”。**

1. **读取与归一化（≤60 s，1–2 次请求）**：运行 `read_input.py`；通读 case.json/task.md/材料，记下受众、文体、格式、`output_contract` 字段。输入→输出：原始输入 → `input_summary.json`。
2. **落骨架（≤120 s，1 次请求）**：运行 `build_result.py`，生成含 `case_id` + 全部必需字段（null/空占位）+ 信封字段的 result.json。**此步即达成“可提交的最小诚实交付”。**
3. **证据抽取与映射（≤240 s，8–12 次请求）**：从数据表/图/图注/统计输出抽取数值（值、单位、误差棒含义、有效数字、统计量），逐条填 `facts_sources`；区分材料内事实 vs 推断。输入→输出：`input_summary.json` → 填充后的 result.json。
4. **成稿（≤240 s，10–16 次请求）**：写 `manuscript.md`（`## Results` 与 `## Discussion` 明确分节），用 `--patch`/`--set`/`--log` 增量回填 `deliverable`/`facts_sources`/`version_boundaries`，每段可回溯到材料位置。
5. **自检与回修（≤180 s，6–10 次请求）**：运行 `check_result.py`，按自检清单逐项修复，结论写回 `change_log`。
6. **收尾（剩余时间）**：补渲染预览、填 `uncertainty`/`pending_human`/`limitations`，置 `status=complete` 或 `partial`。

保留约 20% 请求与时间余量用于异常重试；超时即冻结现状，诚实标注 `partial`/`failed`。所有字段回填统一走 `build_result.py`，避免手改导致结构破坏。

# 自检清单（逐条勾选，括号内对应评分维度）

- [ ] 任务完成度【实际效果40】：交付“可编辑结果段+讨论段+来源映射”，而非提纲/模板/建议。
- [ ] 结果正确性【实际效果40】：每个数值/单位/误差棒含义/统计量/推断强度与原始数据一致。
- [ ] 结果完整性【实际效果40】：图表、阴性结果、不一致文献、局限被如实交代。
- [ ] 结果可核验性【实际效果40】：段落→数值→证据（locator+quote）三级对应，第三方可独立复核。
- [ ] 质量/效率增益【使用增益25】：图表读数→列发现→写结果→核证据→写讨论的流程减少返工，增益可用对照记录验证。
- [ ] 来源可溯·查得到【使用增益25】：`facts_sources` 覆盖关键论断，区分“已完成/拟开展”、`verified/unverified/inferred`。
- [ ] 方法改进【创新增益15】：读得准/分得清/说得住/核得了四关由脚本强制执行，非只靠提示词。
- [ ] 无空话修饰·说得准【创新增益15】：全文无“首次/国际领先/填补空白/颠覆性”等无据措辞（`check_result.py` 扫描）。
- [ ] 调用稳定【可靠体验10】：长材料/多图表/重复运行下脚本产出口径一致的稿件与 result.json。
- [ ] 异常已处理【可靠体验10】：材料缺失/冲突/超长/引用无法核实/软件缺失已写入 `uncertainty`/`limitations`。
- [ ] 资源可复用【生态贡献10】：SKILL.md、证据规则、降级规则、脚本齐全，他人可按说明独立使用。
- [ ] 结构合法【全部】：result.json 含 `case_id` + 全部 `output_contract` 字段 + 信封字段，可解析。
- [ ] 未编造【全部】：status/sources/uncertainty/change_log 与真实执行一致，未伪造评测分数或运行结果。

# 降级策略（禁止编造）

- 材料缺失/字段不明：在 `uncertainty`/`version_boundaries` 标注“缺失/无法判断”，字段留空或写 `null`，不补造数值、文献、作者、政策口径。
- 引用无法核实：保留原文位置并标 `unverified`，不伪造 DOI/URL。
- 软件/工具不可用（无 pdftotext、office 渲染、字体等）：如实写 `limitations`，交付纯文本/结构化产物，不假装已渲染。
- 输入超长/跨度过大：截取关键段落并在 `uncertainty` 说明“已截断/未覆盖”。
- 运行失败：`status=failed`，`change_log` 记录失败原因与已完成中间产物，result.json 仍须落盘。
- 冲突信息：两个来源都记录并标 `source_conflict`。详见 `references/degradation-rules.md`。

# 脚本与资源（相对路径，从 SKILL.md 所在目录执行）

- `scripts/read_input.py`：读取并归一化输入，产出 `input_summary.json`。
- `scripts/build_result.py`：生成/合并 result.json 骨架；`--patch` 增量更新、`--set` 设字段、`--log` 追加变更。
- `scripts/check_result.py`：校验结构 + 一致性/禁词/来源回检；`--selftest` 运行内置自检。
- `references/evidence-claim-uncertainty.md`：证据溯源、语气强度阶梯与不确定性分类。
- `references/degradation-rules.md`：降级决策表与已完成/拟开展、结果/讨论标记规则。

全部只用 Python 标准库；示例：`python scripts/build_result.py --help`。

# 最小调用示例（示意，非正式案例结果）

示意输入：`/input/case.json` 含 `case_id="demo-case"` 与 `output_contract={"deliverable":..., "facts_sources":[...], "version_boundaries":{...}}`。

```bash
python scripts/read_input.py --case /input/case.json --task /input/task.md --input-dir /input --work /app/work
python scripts/build_result.py --summary /app/work/input_summary.json --output-dir /app/output
# ...抽取证据、成稿后增量回填...
python scripts/build_result.py --summary /app/work/input_summary.json --output-dir /app/output \
    --patch /app/work/patch.json --set status=complete --log "deliverable filled"
python scripts/check_result.py --result /app/output/result.json --summary /app/work/input_summary.json
```

示意结果结构（示例）：`{"case_id":"demo-case","deliverable":{"file":"manuscript.md"},"facts_sources":[{"claim":"...","locator":"fig1","quote":"..."}], "version_boundaries":{},"status":"complete","sources":[...],"uncertainty":[],"pending_human":[],"limitations":[],"change_log":[...]}`。
