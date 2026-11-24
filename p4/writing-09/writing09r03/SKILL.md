---
name: results-discussion-writing
description: >
  结果与讨论章节写作 Skill。当任务要求依据给定科研材料（数据表、图、图注、统计输出、
  原论文）产出可直接投稿的结果段与讨论段，或进行改写、双语/结构化交付、引用与数值核验时
  使用；必须区分“结果（观察）与讨论（解释）”、控制因果边界、逐条溯源数值与论断，并在
  /app/output 交付可复核的 result.json。Use for writing Results and Discussion sections
  grounded in provided figures, tables, and statistical outputs, with evidence traceability.
version: 1.0.0
---

# 角色与何时使用

本 Skill 把“数据已在手、图表已画好”的材料转成**可编辑、可提交的结果段与讨论段**，
并同步交付证据映射，而非写作建议、提纲或整篇模板套用。满足任一即调用：

- 任务含“结果与讨论/Results and Discussion/结果段/讨论段/图表读数/趋势与差异/
  讨论证据/因果边界/局限”等；
- 要求依据给定图表、数据表、统计输出或原论文产出可编辑章节稿、改稿、引用核验或
  双语/结构化写作产物；
- 要求保留来源、区分“已完成/拟开展”、区分“结果 vs 讨论”、交付 result.json。

不适用：纯文献泛读、测试题生成、非写作类数据分析。

# 输入契约（运行时，Linux 固定路径）

- `/input/case.json`：case_id、任务说明、output_contract（result.json 必需字段）、
  受众/文体/格式约束、材料清单（可能另在 organizer-workorder.json）。
- `/input/task.md`：题目与评分要求，属事实底稿的一部分。
- `/input/` 下其余文件：材料原文（PDF/文本/表/图/统计输出）。PDF 用平台
  pdf-text-tool 解析；**只依据实际读取到的内容写作，不依据文件名/领域标签补造数据**。
- 工作目录 `/app/work/`；最终产物 `/app/output/`（含 result.json）。

先读文件再写；第一步运行：

```bash
python scripts/read_input.py --case /input/case.json --task /input/task.md \
  --input-dir /input --work /app/work
```

产出 `/app/work/input_summary.json`（含 case_id、output_contract 字段、材料清单、
逐文件 sha256 与 run_fingerprint）。

# 输出契约（必须满足）

`/app/output/result.json` 必须包含：

1. `case_id`：与 /input/case.json 一致；
2. `output_contract` 声明的**全部字段**，字段名原样保留、不增删改名；若未声明，
   至少含 `deliverable`（章节稿内容与文件路径）、`facts_sources`（逐条来源映射）、
   `version_boundaries`（修改差异、人工确认项、材料缺口、异常）；
3. 附加字段：`status`（`ok|partial|complete|failed|insufficient_evidence|input_error`）、
   `sources`、`uncertainty`、`pending_human`、`change_log`（单调 step + fingerprint，
   不写墙钟时间，保证可复现）。

章节稿正文写入 `/app/output/`（如 `manuscript.md`，或 output_contract 指定文件名），
result.json 以路径引用它。可编辑 Office/幻灯片/图示须附真实渲染的 PDF/PNG 预览；
无法渲染则写 `limitations`，不假装已渲染。

# 执行流程（总预算 900s / 64 次模型请求）

时间纪律：**前 3 分钟内必须先落盘一版 partial 结果骨架**，此后只做增量改进；
每次改动立即写回文件，不只在会话里“记住”。

1. **读取与归一化（≤60s，1–2 次请求）**：运行 read_input.py；通读 case.json/task.md/材料，
   记下受众、文体、格式、output_contract 字段。输入→输出：原始输入 → input_summary.json。
2. **落骨架（≤120s，1 次请求）**：运行
   `python scripts/build_result.py --init --summary /app/work/input_summary.json --result /app/output/result.json`，
   生成含 case_id + 全部必需字段（null 占位）+ status/sources/uncertainty/pending_human/
   change_log 的 result.json。**此步完成即达成“可提交的最小诚实交付”。**
3. **读数与要素映射（≤240s，8–12 次请求）**：按 references/writing-protocol.md 的
   “读得准/分得清/说得住/核得了”把图表证据映射到结果段与讨论段；区分“材料内事实 vs
   推断”、结果 vs 讨论、相关 vs 因果；逐个填实骨架字段（deliverable/facts_sources/
   version_boundaries）。用 `build_result.py --patch` 增量写回。
4. **成稿（≤240s，10–16 次请求）**：写章节稿正文到 /app/output，按图序或问题序组织，
   每条关键数值/论断配 locator + 逐字 quote；讨论段逐条对到本研究数据或具体文献。
5. **自检与回修（≤180s，6–10 次请求）**：运行
   `python scripts/selftest.py check --result /app/output/result.json --summary /app/work/input_summary.json --output-dir /app/output`
   与 `selftest.py determinism`；按下方清单修复，把结论写回 change_log。
6. **收尾（剩余时间）**：补渲染预览、写 uncertainty（未确认项）、置 status=complete 或 partial。

保留约 20% 请求与时间余量用于异常重试；超时即冻结现状，诚实标注 partial/failed。

# 自检清单（逐条勾选，括号内为评分维度）

- [ ] 任务完成度【实际效果40】：交付“可编辑结果段+讨论段”，而非建议/提纲/模板。
- [ ] 结果正确性【实际效果40】：数值/单位/误差棒/n/p 与原始图表一致，无材料外新增事实。
- [ ] 结果完整性【实际效果40】：阴性结果、不一致文献、局限如实交代。
- [ ] 结果可核验性【实际效果40】：正文数值与论断可独立复核（段落→事实→证据三级对应）。
- [ ] 读得准/分得清/说得住/核得了【使用增益25】：四项硬问题逐条落实。
- [ ] 质量增益【使用增益25】：相比无 Skill 的基础稿，数值更准、因果边界更严、更少空话。
- [ ] 效率增益【使用增益25】：read_input→build_result→selftest 减少起草/核对/返工往返。
- [ ] 增益可验证【使用增益25】：同一输入重复运行产物字节一致（determinism 自检通过）。
- [ ] 新增能力【创新增益15】：禁词扫描、claim-strength 阶梯、因果边界控制由脚本强制。
- [ ] 方法改进【创新增益15】：读数→列发现→写结果→核证据→写讨论由脚本串联并自检。
- [ ] 无空话修饰·说得准【创新增益15】：全文无“首次/国际领先/填补空白/颠覆性”等无据措辞。
- [ ] 调用稳定【可靠体验10】：换图/换学科/换数据量后仍产出可用章节，脚本可重复运行。
- [ ] 异常已处理【可靠体验10】：缺图/缺统计量/单位不明/引用找不到/读数不清已写入
      uncertainty 或 version_boundaries。
- [ ] 资源可复用【生态贡献10】：SKILL.md、writing-protocol、确定性规则、脚本齐全，
      他人可按说明独立使用。
- [ ] 结构合法【全部】：result.json 含 case_id + 全部 output_contract 字段 +
      status/sources/uncertainty/pending_human/change_log，可解析。
- [ ] 未编造【全部】：status/sources/uncertainty/change_log 与真实执行一致，未写任何
      评测分数或伪造运行结果。

# 降级策略（禁止编造）

- 材料缺失/字段不明：在 uncertainty 或 version_boundaries 标“缺失/无法判断”，
  字段留空或写 null，**不补造数值、文献、作者、政策口径**。
- 引用无法核实：保留原文位置并标 `unverified`，不伪造 DOI/URL。
- 软件/工具不可用（无 pdftotext、office 渲染等）：如实写 limitations，
  交付纯文本/结构化产物，不假装已渲染。
- 输入超长/跨度过大：截取关键段落，并在 uncertainty 说明“已截断/未覆盖”。
- 运行失败：status=failed，change_log 记录失败原因与已完成中间产物，result.json 仍须落盘。
- 详见 `references/determinism-and-degradation.md`。

# 脚本与资源（相对路径，从 SKILL.md 所在目录执行）

- `scripts/read_input.py`：归一化输入，产出确定性 input_summary.json。
- `scripts/build_result.py`：`--init` 生成骨架、`--patch` 增量合并，确定性序列化。
- `scripts/selftest.py`：`check` 校验结构/禁词/来源回检，`determinism` 验证重复运行一致。
- `references/writing-protocol.md`：读得准/分得清/说得住/核得了 + 证据强度阶梯。
- `references/determinism-and-degradation.md`：确定性规则、降级矩阵、提交前最小自检。

全部只用 Python 标准库；示例：`python scripts/build_result.py --help`。

# 最小调用示例（示意，非正式案例结果）

示意输入（非正式案例）：`/input/case.json` 含 `case_id="demo-case"` 与
`output_contract={"deliverable":…, "facts_sources":[…], "version_boundaries":{…}}`。
按上述六步执行后，`/app/output/result.json` 示意结构：

```json
{"case_id":"demo-case","deliverable":{"file":"manuscript.md"},"facts_sources":[],
 "version_boundaries":{},"status":"complete","sources":[],"uncertainty":[],
 "pending_human":[],"change_log":[{"step":0,"action":"skeleton_created","fingerprint":"…"}]}
```
