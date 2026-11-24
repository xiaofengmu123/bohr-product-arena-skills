---
name: results-discussion-writing
description: >
  结果与讨论章节写作 Skill。当任务要求依据给定科研材料（数据表、图、图注、统计输出、文献）
  产出可投稿的结果段与讨论段，或做图表读数转写、趋势与差异表述、讨论证据对照、因果边界与
  局限说明、双语/结构化交付时使用；必须为每个数值与论断保留来源定位和逐字引用，严格区分
  "结果（只陈述观察）"与"讨论（解释/对照/局限）"，并在 /app/output 交付可复核的 result.json。
  Use for Results & Discussion section writing, figure/table reading, evidence-traceable
  drafting, revision and citation verification grounded in provided research materials.
version: 1.0.0
---

# 角色与何时使用

本 Skill 把"数据与图表已在手"的科研材料转成**可投稿的结果与讨论章节稿 + 逐句来源映射 + 缺口清单**，
不是写作建议、提纲或整篇模板套用。满足任一即调用：

- 任务含"结果 / 讨论 / Results / Discussion / 图表读数 / 趋势 / 差异 / 因果 / 局限"等；
- 要求依据给定材料产出可编辑章节稿、改稿、引用/双语/结构化写作产物；
- 要求保留来源、区分"结果 vs 讨论"、交付 result.json。

不适用：纯文献泛读、测试题生成、非写作类数据分析。

# 输入契约（运行时，Linux 固定路径）

- `/input/case.json`：case_id、任务说明、output_contract（result.json 必需字段）、材料清单、
  受众/文体/格式约束。
- `/input/task.md`：题目与评分要求，属事实底稿的一部分。
- `/input/` 下其余文件：材料原文（PDF/文本/表格/图）。PDF 用平台 pdf-text-tool 解析，不读二进制；
  **只依据实际读到的内容写作，不依据文件名/领域标签补造数据**。
- 工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`（含 result.json）。

先读文件再写。第一步运行（相对路径以本 SKILL.md 所在目录 `/skills/weekly-skill` 为基准）：

`python scripts/read_input.py --case /input/case.json --task /input/task.md --input-dir /input --work /app/work`

产出 `/app/work/input_summary.json`（含 case_id、output_contract 字段、材料清单、图表/统计线索）。

# 输出契约（必须满足）

`/app/output/result.json` 必须包含：

1. `case_id`：与 /input/case.json 一致；
2. output_contract 声明的**全部字段**，字段名原样保留、不增删改名；若 case.json 未声明，
   至少含 `deliverable`（章节稿正文内容与文件路径）、`facts_sources`（数字/术语/引用/图表/
   关键论断的逐条来源映射）、`version_boundaries`（修改差异、人工确认项、材料缺口、异常）；
3. 附加字段：`status`（`in_progress|partial|complete|failed`）、`sources`（来源定位数组）、
   `uncertainty`（不确定项数组）、`pending_human`（需人工确认项）、`change_log`（带时间戳与
   动作的修改记录数组）。

章节稿正文写入 `/app/output/`（如 manuscript.md，或 output_contract 指定文件名），result.json
以路径引用它。可编辑 Office/幻灯片/图示必须附真实渲染的 PDF/PNG 预览。

# 执行流程（总预算 900s / 64 次模型请求，16384 token/响应）

时间纪律：**前 3 分钟内必须落盘一版 partial result.json 骨架**，此后只做增量改进；
每次改动立即写回文件，不得只在会话里"记住"。

1. 读取与归一化（≤60s，1–2 次请求）：运行 read_input.py；通读 case.json/task.md/材料，记下受众、
   文体、格式、output_contract 字段、图表/统计线索。输入→输出：原始输入 → input_summary.json。
2. 落骨架（≤120s，1 次请求）：运行
   `python scripts/build_result.py --summary /app/work/input_summary.json --output-dir /app/output`，
   生成含 case_id + 全部必需字段（占位）+ status/sources/uncertainty/change_log 的 result.json，
   status=partial。**此步完成即达成"可提交的最小诚实交付"。**
3. 图表/数据抽取与逐句映射（≤240s，8–12 次请求）：按 references/evidence-and-degradation.md 为每张
   图/表填写读数卡（图号、面板、坐标轴与单位、刻度、误差棒含义、n、统计量）；把证据映射到章节；
   区分"材料内事实 vs 推断"、结果 vs 讨论。输入→输出：input_summary.json → 填充后的 result.json 骨架。
4. 成稿（≤240s，10–16 次请求）：写章节稿正文到 /app/output，用
   `python scripts/build_result.py --result /app/output/result.json --patch /app/work/patch.json`
   增量填实 deliverable/facts_sources/version_boundaries；每句可回溯到材料位置（文件+页码/段落/图面板）。
5. 自检与回修（≤180s，6–10 次请求）：运行
   `python scripts/selftest.py --result /app/output/result.json --summary /app/work/input_summary.json`；
   按下方自检清单逐项修复，把结论写回 change_log。
6. 收尾（剩余时间）：补渲染预览、写 uncertainty/pending_human、置 status=complete 或 partial。

保留约 20% 请求与时间余量用于异常重试；超时即冻结现状，诚实标注 partial/failed。

# 核心写作规则（读得准 / 分得清 / 说得住 / 核得了）

- 分得清（结果 vs 讨论）：结果段只陈述观察（方向、幅度、显著性、统计量），不解释、不引文献对照；
  讨论段承担解释、文献对照、机制推测与局限。结果段禁用"表明/提示/揭示"等解释性动词。
- 读得准（图表读数）：正文数值必须来自材料内实际读数，写清单位、有效数字、误差棒含义（SD/SE/CI）、
  坐标刻度、多面板与补充材料编号；无依据不换算、不估算、不四舍五入到材料未给的精度。
- 说得住（语气校准）：p<0.05 且材料明确写"显著"才写"显著"；方向一致但未达显著写"趋势"并注明未达显著；
  "仅在高剂量组出现"保留边界；相关≠因果，机制推测显式标注"推测/可能"。
- 核得了（证据溯源）：每个数值/术语/引用/关键论断在 facts_sources 中给 `{claim, locator, quote, status}`，
  quote 为材料逐字片段；未核实标 `unverified`，不伪造 DOI/URL。

# 自检清单（逐条勾选，括号为评分维度与分值）

- [ ] 任务完成度【实际效果15】：交付可编辑、可投稿的结果段+讨论段，而非建议/提纲/模板。
- [ ] 结果正确性【实际效果15】：每个数值/单位/误差棒/统计量/推断强度与原始图表一致，无材料外新增事实。
- [ ] 结果完整性【实际效果5】：图表、阴性结果、不一致文献与局限被如实交代。
- [ ] 结果可核验性【实际效果5】：正文数值与论断能被独立复核（facts_sources 逐条回溯）。
- [ ] 质量增益【使用增益15】：相比基础 Agent，图表读数、结果/讨论切分、语气校准更稳。
- [ ] 效率增益【使用增益5】：脚本归一化输入、自动落骨架、自动回检，减少起草/核对/返工往返。
- [ ] 增益可验证性【使用增益5】：用相同输入下的"基础 vs 加载 Skill"对照验证改善，不凭空自述。
- [ ] 新增能力【创新增益5】：图表读数卡、语气校准表、结果/讨论边界规则是基础 Agent 不具备的稳定能力。
- [ ] 方法改进【创新增益5】：读图→列发现→写结果→核证据→写讨论由脚本强制执行，非口头约束。
- [ ] 创新证据【创新增益5】：用边界案例（趋势未达显著、仅高剂量组、相关≠因果）说明改进点价值。
- [ ] 调用与易用性【可靠体验3】：Agent 按本 SKILL.md 可触发并完成一次完整章节写作。
- [ ] 运行稳定性【可靠体验4】：换图/换学科/换数据量后仍产出可用章节（脚本容错 BOM/坏 JSON/空输入）。
- [ ] 异常处理【可靠体验3】：缺图/缺统计量/单位不明/引用找不到/读数不清被识别并如实标注。
- [ ] 文档完整性【生态贡献3】：使用说明、依赖、路径、限制、维护信息齐全。
- [ ] 资源可复用性【生态贡献4】：语气校准表、读数卡模板、检查清单、脚本可被他人直接复用。
- [ ] 分享推广价值【生态贡献3】：沉淀可分享的案例经验与真实写作价值，非一次性脚本。
- [ ] 结构合法【全部】：result.json 含 case_id + 全部 output_contract 字段 + status/sources/uncertainty/change_log，可解析。
- [ ] 未编造【全部】：status/sources/uncertainty/change_log 与真实执行一致，未写任何评测分数或伪造运行结果。

# 降级策略（禁止编造）

- 材料缺失/字段不明：在 uncertainty 或 version_boundaries 标"缺失/无法判断"，字段留空或写 null，
  不补造数值、文献、作者、政策口径。
- 引用无法核实：保留原文位置并标 `unverified`，不伪造 DOI/URL。
- 软件/工具不可用（无 pdftotext、office 渲染等）：如实写 limitations，交付纯文本/结构化产物，
  不假装已渲染。
- 输入超长/跨度过大：截取关键段落并在 uncertainty 说明"已截断/未覆盖"。
- 运行失败：status=failed，change_log 记录失败原因与已完成中间产物，result.json 仍须落盘。
- 空输入/无材料：status=insufficient_evidence 或 partial，列出缺口，不用示例填充。
- 详见 `references/evidence-and-degradation.md`。

# 脚本与资源（相对路径，从 SKILL.md 所在目录执行）

- `scripts/read_input.py`：读取并归一化输入，产出 input_summary.json。
- `scripts/build_result.py`：生成/合并 result.json 骨架；`--patch` 增量更新。
- `scripts/selftest.py`：自检（结构 + 一致性 + 禁词 + 来源回检）；`--self-test` 跑端到端 plumbing 自测。
- `references/scoring-dimensions.md`：评分维度→流程→自检项对齐表。
- `references/evidence-and-degradation.md`：证据溯源、结果/讨论边界、语气校准与降级规则。

全部只用 Python 标准库；示例：`python scripts/selftest.py --help`。
提交前在干净目录跑一次 `python scripts/selftest.py --self-test` 验证三脚本可独立运行。
