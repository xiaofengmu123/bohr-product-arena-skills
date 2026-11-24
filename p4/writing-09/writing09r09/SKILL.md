---
name: results-discussion-writing
description: >
  结果与讨论章节写作 Skill。当任务要求依据给定科研材料（数据表、图、图注、统计输出、文献）
  产出可直接投稿/编辑的结果段与讨论段，或做改写、校对、引用核验、双语/结构化交付时使用。
  核心突破点：分得清（结果段只陈述观察到的事实，讨论段承担解释、文献对照与局限）、
  说得住（趋势/差异/因果表述的语气与证据强度匹配）、核得了（每个数值与论断回溯到
  图表/统计输出/文献），并保留事实来源与修改记录，在 /app/output 交付可复核的 result.json。
  Use for writing Results & Discussion sections from provided data, figures, statistical
  outputs and references, with fact-source traceability and honest boundaries.
version: 1.0.0
---

# 角色与何时使用

本 Skill 把“数据/图表/统计输出/文献”转成“可直接投稿的结果段 + 讨论段 + 证据对照表 + 来源映射”，
不是写作建议、提纲或整篇模板套用。满足任一即调用：

- 任务含“结果与讨论 / Results / Discussion / 结果段 / 讨论段 / 图表读数 / 趋势 / 差异 / 局限”等；
- 要求依据给定材料产出可编辑章节稿、改稿、引用/格式结果或结构化写作产物；
- 要求保留来源、区分“观察到的事实 vs 解释/推测”、交付 result.json。

不适用：纯文献泛读、测试题生成、非写作类数据分析。

# 输入契约（运行时，Linux 固定路径）

- `/input/case.json`：case_id、任务说明、output_contract（result.json 必需字段）、材料清单、受众/文体/格式约束。
- `/input/task.md`：题目与评分要求，属事实底稿的一部分。
- `/input/` 下其余文件：材料原文（PDF/文本/表格等）。PDF 用平台 pdf-text-tool 解析，不读二进制；
  **只依据实际读取到的内容写作，不依据文件名/领域标签补造数据**。
- 工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`（含 result.json）。

第一步运行：
`python scripts/main.py init --case /input/case.json --task /input/task.md --input-dir /input --work /app/work --out /app/output`
产出 `/app/work/input_summary.json` 与 `/app/output/result.json`（partial 骨架）。

# 输出契约（必须满足）

`/app/output/result.json` 必须包含：

1. `case_id`：与 /input/case.json 一致；
2. `output_contract` 声明的**全部字段**，字段名原样保留、不增删改名；若 case.json 未声明，至少含
   `deliverable`（可编辑章节稿内容与文件路径）、`facts_sources`（数字/术语/引用/图表/关键论断的逐条来源映射）、
   `version_boundaries`（修改差异、人工确认项、材料缺口、异常）；
3. 附加字段：`status`（`in_progress|partial|complete|failed`）、`sources`（来源定位数组）、
   `uncertainty`（不确定项数组）、`pending_human`（需人工确认项）、`change_log`（带时间戳与动作的修改记录数组）、
   `errors`、`degradations`。

章节稿正文写入 `/app/output/`（如 manuscript.md，或 output_contract 指定文件名），result.json 以路径引用它。
可编辑 Office/幻灯片/图示还必须附真实渲染的 PDF/PNG 预览。

# 执行流程（总预算 900s / 64 次模型请求）

时间纪律：**前 3 分钟内必须落盘一版 result.json 骨架**（init 即产出 partial），此后只做增量改进；
每次改动立即写回文件，不得只在会话里“记住”。

1. **读取与归一化（≤60s，1–2 次请求）**：运行 init；通读 case.json/task.md/材料，记下受众、文体、
   格式、output_contract 字段。输入→输出：原始输入 → input_summary.json。
2. **落骨架（≤120s，1 次请求）**：init 生成含 case_id + 全部必需字段（null/[] 占位）+ status/sources/
   uncertainty/pending_human/change_log 的 result.json，status=partial。**此步完成即达成可提交的最小诚实交付。**
3. **证据映射（≤240s，8–12 次请求）**：按“四查”（读得准/分得清/说得住/核得了）把材料证据映射到结果段
   与讨论段；区分“材料内事实 vs 推断”、观察 vs 解释；逐个填实骨架字段。输入→输出：input_summary.json →
   填充后的 result.json。
4. **成稿（≤240s，10–16 次请求）**：写 manuscript.md 到 /app/output，填充 deliverable/facts_sources/
   version_boundaries；每段可回溯到材料位置（文件+页码/段落，locator + 逐字 quote）。
5. **自检与回修（≤180s，6–10 次请求）**：运行
   `python scripts/main.py check --result /app/output/result.json --summary /app/work/input_summary.json --out /app/output`；
   按自检清单逐项修复，把结论写回 change_log。
6. **收尾（剩余时间）**：补渲染预览、写 uncertainty/pending_human、置 status=complete 或 partial。

保留约 20% 请求与时间余量用于异常重试；超时即冻结现状，诚实标注 partial/failed。

# 自检清单（逐条勾选，括号内为对应评分维度）

- [ ] 任务完成度【实际效果40】：交付“可直接投稿的结果段 + 讨论段 + 证据对照表”，而非建议/提纲/模板。
- [ ] 结果正确性【实际效果40】：每个数值/单位/误差棒/统计量/引用都能在材料中找到原文位置，无材料外新增事实。
- [ ] 结果完整性【实际效果40】：图表、阴性结果、不一致文献与局限被如实交代，结果与讨论两节不互相顶替。
- [ ] 结果可核验性【实际效果40】：段落→要素→证据三级对应，第三方能判断某句该不该这么写。
- [ ] 一致性·对得上【使用增益25】：数值↔图表↔正文↔论断逐条对应；趋势/差异/因果表述与证据强度匹配。
- [ ] 效率【使用增益25】：init 归一化 + 自动落骨架 + check 回检，减少从散乱材料到初稿的往返。
- [ ] 来源可溯·查得到【使用增益25】：facts_sources 覆盖关键论断，逐条带 locator + 逐字 quote。
- [ ] 方法改进【创新增益15】：读图→列发现→写结果→核证据→写讨论由“四查”与脚本强制分步执行。
- [ ] 无空话修饰·说得准【创新增益15】：无“首次/国际领先/填补空白/颠覆性”等无据措辞，因果表述有边界。
- [ ] 调用稳定【可靠体验10】：长材料/多图表/重复运行下按脚本产出口径一致的稿件与 result.json。
- [ ] 异常已处理【可靠体验10】：材料缺失/冲突/超长/引用无法核实/单位不明已写入 version_boundaries 或 uncertainty。
- [ ] 资源可复用【生态贡献10】：SKILL.md、四查规则、语气分级、降级规则齐全，他人可独立使用。
- [ ] 结构合法【全部】：result.json 含 case_id + 全部 output_contract 字段 + status/sources/uncertainty/pending_human/change_log，可解析。
- [ ] 未编造【全部】：status/sources/uncertainty/change_log 与真实执行一致，未写任何评测分数或伪造运行结果。

# 降级策略（禁止编造）

- 材料缺失/字段不明：在 uncertainty 或 version_boundaries 标注“缺失/无法判断”，字段留空或写 null，不补造数值、文献、作者、单位。
- 图表读数不清/单位不明：标 `unverified` 并写 pending_human，不猜数值。
- 引用无法核实：保留原文位置并标 `unverified`，不伪造 DOI/URL。
- 软件/工具不可用（无 pdf-text-tool、office 渲染等）：如实写 degradations，交付纯文本/结构化产物，不假装已渲染。
- 输入超长/跨度过大：截取关键段落，并在 uncertainty 说明“已截断/未覆盖”。
- 运行失败：status=failed，change_log 记录失败原因与已完成中间产物，result.json 仍须落盘。
- 详见 `references/rules.md`。

# 脚本与资源（相对路径，从 SKILL.md 所在目录执行）

- `scripts/main.py init`：读取并归一化输入，产出 input_summary.json + partial result.json 骨架。
- `scripts/main.py check`：校验 result.json 结构 + 四查一致性/禁词/证据溯源/语气分级，产出 check_report.json。
- `scripts/selftest.py`：空目录/坏 JSON/最小 demo 自检，退出码 0 且产物合法。
- `references/rules.md`：四查规则、语气分级与降级/核验规则。

全部只用 Python 标准库；示例：`python scripts/main.py --help`。

# 最小调用示例（示意，非正式案例结果）

示意输入（非正式案例）：`/input/case.json` 含 `case_id="demo-case"` 与
`output_contract={"deliverable":..., "facts_sources":[...], "version_boundaries":{...}}`。
按上述六步执行后，`/app/output/result.json` 示意结构：
`{"case_id":"demo-case","deliverable":{"file":"manuscript.md"},"facts_sources":[...],` +
`"version_boundaries":{},"status":"partial","sources":[...],"uncertainty":[],"pending_human":[],` +
`"change_log":[...],"errors":[],"degradations":[]}`。
