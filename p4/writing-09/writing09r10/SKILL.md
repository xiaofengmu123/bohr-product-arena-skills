---
name: results-discussion-writing
description: >
  结果与讨论章节写作 Skill。当任务要求依据给定科研材料（论文正文、数据表、图表、图注、
  统计输出）产出可投稿、可编辑的 Results 与 Discussion 章节稿，或进行图表读数转写、
  趋势与差异表述、讨论证据对照、因果边界与局限改写、双语/结构化交付时使用。核心方法
  是以“claim 台账”（claim → 证据 → 强度 → 位置）为唯一中间表示，强制区分观察/推断/
  推测，并把每个数值逐字回溯到图、表或统计输出。Use for writing Results & Discussion
  sections from provided figures, tables and statistical outputs, with verbatim
  evidence provenance and strength-calibrated wording.
version: 1.1.0
---

# 角色与何时使用

本 Skill 把“数据与图表已经在手”转成“可直接进入论文的 Results 与 Discussion 章节稿 + claim 台账 +
来源映射”，而不是写作建议、提纲或整篇模板套用。满足任一即调用：

- 任务含“结果与讨论 / Results / Discussion / 图表读数 / 趋势与差异 / 讨论证据 / 因果边界 / 局限”等；
- 要求依据给定材料产出可编辑章节稿、改稿、校对、引用核验或结构化写作产物；
- 要求保留来源、区分“观察 vs 解释 vs 推测”、交付 result.json。

不适用：纯文献泛读、测试题生成、非写作类数据分析。

设计取向（与“章节模板”类 Skill 的差异）：本包不预设固定章节数，而是先建立**claim 台账**作为中间表示——
每条正文断言被拆成 `{id, kind, strength, section, locator, quote, unit, value, status}`，
写作、核对、回修都以台账为轴。由此把题目四个硬指标机械化为四条脚本检查：
读得准（数值核对）、分得清（Results/Discussion 边界）、说得住（强度-措辞标定）、核得了（locator+quote 溯源）。

# 输入契约（运行时，Linux 固定路径）

- `/input/case.json`：`case_id`、任务说明、`output_contract`（result.json 必需字段）、材料清单、
  受众/文体/格式约束；可能另有 `organizer-workorder.json`。
- `/input/task.md`：题目与评分要求，属事实底稿的一部分。
- `/input/` 下其余文件：材料原文（PDF/文本/表格/统计输出）。PDF 用平台 `pdf-text-tool` 解析，不读二进制；
  **只依据实际读到的内容写作，不依据文件名/领域标签补造数据**。
- 工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`（含 result.json）。

第一步运行：`python scripts/main.py read`，产出 `/app/work/input_summary.json`（含 case_id、
output_contract 字段、材料清单、需解析的 PDF 列表、读入告警）。

# 输出契约（必须满足）

`/app/output/result.json` 必须包含：

1. `case_id`：与 `/input/case.json` 一致；
2. `output_contract` 声明的**全部字段**，字段名原样保留、不增删改名；若 case.json 未声明，至少含：
   - `deliverable`：可编辑章节稿/结构化产物的内容与文件路径；
   - `facts_sources`：数字/术语/引用/图表/关键论断的逐条来源映射（locator + verbatim quote + strength）；
   - `version_boundaries`：修改差异、人工确认项、材料缺口、异常；
3. 附加字段：`status`（`partial|complete|failed|insufficient_evidence`）、`sources`（来源定位数组）、
   `uncertainty`（不确定项数组）、`pending_human`（待人工确认数组）、`change_log`（带时间戳与动作的修改记录数组）、
   `limitations`（软件/数据缺失）、`runtime`（起止时间与步骤）。

章节稿正文写入 `/app/output/manuscript.md`（或 output_contract 指定文件名），result.json 以路径引用它。
可编辑 Office/幻灯片/图示必须另附真实渲染的 PDF/PNG 预览；无法渲染时在 `limitations` 如实说明，不假装已渲染。

# 执行流程（总预算 900s / 64 次模型请求 / 单回复 ≤16384 token）

时间纪律：**前 3 分钟内必须先落盘一版 result.json 骨架（status=partial）**，此后只做增量改进；
每次改动立即写回文件，不得只在会话里“记住”。

1. **读取与归一化（≤60s，1–2 次请求）**：`python scripts/main.py read`；通读 case.json/task.md/材料，
   记下受众、文体、格式、output_contract 字段。输入→输出：原始输入 → `input_summary.json`。
2. **落骨架（≤120s，1 次请求）**：`python scripts/main.py skeleton`，生成含 case_id + 全部必需字段
   （null 占位）+ status/sources/uncertainty/pending_human/change_log 的 result.json。
   **此步完成即达成“可提交的最小诚实交付”。**
3. **建 claim 台账（≤240s，8–12 次请求）**：`python scripts/main.py claim` 自动抽取材料中的数值候选，
   落盘 `work/claims.json`；随后按“观察/推断/推测”标定 strength、指定 results|discussion 归属、
   补 locator+verbatim quote。输入→输出：材料 → `claims.json`（claim 台账）。
4. **成稿（≤240s，10–16 次请求）**：`python scripts/main.py draft` 由台账生成 `manuscript.md` 骨架；
   Agent 补全正文，再 `python scripts/main.py merge` 把台账写回 result.json 的 `facts_sources`。
5. **自检与回修（≤180s，6–10 次请求）**：`python scripts/main.py check`；按下方自检清单逐项修复，
   把结论写回 change_log。
6. **收尾（剩余时间）**：补渲染预览、写 uncertainty/pending_human、置 status=complete 或 partial。

保留约 20% 请求与时间余量用于异常重试；超时即冻结现状，诚实标注 partial/failed。

# 自检清单（逐条勾选，括号内为对应评分维度）

- [ ] 任务完成度【实际效果40】：交付“可编辑的 Results 与 Discussion 章节稿 + claim 台账”，而非建议/提纲/模板。
- [ ] 结果正确性【实际效果40】：每个数值/单位/误差棒/统计量都能在材料原文定位，无材料外新增事实。
- [ ] 结果完整性【实际效果40】：观察、阴性结果、不一致文献、局限均如实交代，厚度匹配任务要求。
- [ ] 结果可核验性【实际效果40】：正文断言 → claim 台账 → locator+verbatim quote 三级对应。
- [ ] 一致性·说得住【使用增益25】：趋势/组间差异/因果表述的措辞强度与证据强度匹配，无“小波动写成大结论”。
- [ ] 分得清【使用增益25】：Results 只写观察，Discussion 承担解释、文献对照与局限，两节不顶替。
- [ ] 效率【使用增益25】：脚本归一化输入、生成骨架、自动抽数、自动回检，减少起草-核对-返工往返。
- [ ] 来源可溯·查得到【使用增益25】：facts_sources 覆盖关键论断，逐字 quote，区分“已完成/拟开展”。
- [ ] 方法改进【创新增益15】：claim 台账 + 数值核对 + 边界检查 + 强度标定 + 评审人视角自检由脚本强制执行。
- [ ] 无空话修饰·说得准【创新增益15】：全文无“首次/国际领先/填补空白/颠覆性”等无据措辞。
- [ ] 调用稳定【可靠体验10】：换图/换学科/换数据量后按脚本产出口径一致的结果。
- [ ] 异常已处理【可靠体验10】：材料缺失/冲突/超长/引用无法核实/格式冲突写入 uncertainty 或 limitations。
- [ ] 资源可复用【生态贡献10】：SKILL.md、claim 台账 schema、强度标定表、检查清单、降级规则齐全。
- [ ] 结构合法【全部】：result.json 含 case_id + 全部 output_contract 字段 + status/sources/uncertainty/pending_human/change_log，可解析。
- [ ] 未编造【全部】：status/sources/uncertainty/change_log 与真实执行一致，未写任何评测分数或伪造运行结果。

# 降级策略（禁止编造）

- 材料缺失/字段不明：在 uncertainty 或 version_boundaries 标注“缺失/无法判断”，字段留空或写 null，不补造数值、文献、作者、政策口径。
- 引用无法核实：保留原文位置并标 `unverified`，不伪造 DOI/URL。
- 软件/工具不可用（无 pdftotext、office 渲染等）：如实写 limitations，交付纯文本/结构化产物，不假装已渲染。
- 输入超长/跨度过大：截取关键段落，并在 uncertainty 说明“已截断/未覆盖”。
- 运行失败：status=failed，change_log 记录失败原因与已完成中间产物，result.json 仍须落盘。
- 详见 `references/claim-strength-and-boundaries.md` 与 `references/checklist-and-degradation.md`。

# 脚本与资源（相对路径，从 SKILL.md 所在目录执行）

- `scripts/main.py`：子命令入口。`read` 归一化输入；`skeleton` 生成 result.json 骨架；`claim` 抽数建台账；
  `draft` 由台账生成章节骨架；`merge` 把台账写回 result.json；`check` 结构+一致性+禁词+来源回检；
  `selftest` 跑自检。默认无参执行 `read → skeleton → check` 最小安全链路。
- `scripts/core.py`：共享实现（标准库、确定性、坏 JSON/坏编码降级）。
- `scripts/selftest.py`：空目录/坏 JSON/缺输入三态自检，退出码 0 表示通过。
- `references/claim-strength-and-boundaries.md`：claim 台账 schema、强度-措辞标定表、Results/Discussion 边界与因果语言规则。
- `references/checklist-and-degradation.md`：逐条检查清单与降级规则。

全部只用 Python 标准库；示例：`python scripts/main.py --help`。

# 最小调用示例（示意，非正式案例结果）

示意输入（非正式案例）：`/input/case.json` 含 `case_id="demo-case"` 与
`output_contract={"deliverable":..., "facts_sources":[...], "version_boundaries":{...}}`。
按上述流程执行后，`/app/output/result.json` 示意结构：
`{"case_id":"demo-case","deliverable":{"file":"manuscript.md"},"facts_sources":[...],` +
`"version_boundaries":{},"status":"complete","sources":[...],"uncertainty":[],"pending_human":[],` +
`"change_log":[...],"limitations":[],"runtime":{...}}`。
