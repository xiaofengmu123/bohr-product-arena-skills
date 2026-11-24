---
name: results-discussion-writing
description: >
  学术写作 Skill：把给定的数据、图表、统计输出与文献写成可直接投稿的“结果与讨论”章节稿，
  强制分离结果段与讨论段、按证据强度校准趋势/差异/因果表述、为每个数值与论断建立
  locator+逐字 quote 的可回溯证据映射，并在 /app/output 交付字段逐一对齐 output_contract 的
  result.json（含 append-only change_log 与 limitations/pending_human）。Use for Results &
  Discussion section writing, figure/table reading, claim-strength calibration, citation
  verification, and traceable revision grounded only in the provided research materials.
version: 2.0.0
---

# 角色与何时使用

本 Skill 把“数据已在手、图表已画好”的科研材料转成**可编辑、可提交的结果与讨论章节稿 +
逐条可回溯的证据映射**，而不是写作建议、提纲或空泛模板。满足任一即调用：

- 任务要求写 Results / Discussion 章节、结果段、讨论段，或对已有稿做改写/校对/引用核验；
- 材料含数据表、图、图注、统计输出或文献，要求“读得准、分得清、说得住、核得了”；
- 要求保留来源、区分“结果 vs 讨论”、校准因果边界与局限，或交付结构化 result.json。

不适用：纯文献泛读、非写作类数据分析、无材料可据的命题作文。

# 输入契约（运行时 Linux 固定路径）

- `/input/case.json`：case_id、任务说明、`output_contract`（result.json 必需字段）、材料清单、
  受众/文体/格式/引用约束（可能另在 organizer-workorder.json）。
- `/input/task.md`：题目与评分要求，属事实底稿的一部分。
- `/input/` 其余文件：材料原文（数据表/图/统计输出/文献文本）。PDF 用平台 pdf-text-tool、
  图用 pdf-render-tool/ocr-tool 解析，不读二进制；**只依据实际读到内容写作，不依据文件名
  或领域标签补造数据**。
- 工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`（含 result.json）。

先读文件再写。第一步运行（≤1 次请求）：

```bash
python scripts/main.py build --case /input/case.json --task /input/task.md --input-dir /input --work /app/work --output /app/output
```

产出 `/app/work/input_summary.json`（case_id、output_contract 字段、材料清单）与
`/app/work/contract.json`，并落盘一版 result.json 骨架。

# 输出契约（必须满足）

`/app/output/result.json` 必须包含：

1. `case_id`：与 /input/case.json 一致；
2. `output_contract` 声明的**全部字段**：字段名原样保留、不增删改名；若未声明，至少含
   `deliverable`（可编辑章节稿内容与文件路径）、`facts_sources`（数字/术语/引用/图表/关键
   论断的逐条来源映射）、`version_boundaries`（修改差异、人工确认项、材料缺口、异常）；
3. 附加字段：`status`（`in_progress|partial|complete|failed`）、`sources`（来源定位数组，
   每条含 locator+quote+kind+status）、`uncertainty`（不确定项）、`pending_human`（人工确认项）、
   `change_log`（append-only 修改记录）、`limitations`（降级说明）、`runtime`（起止与耗时）。

`change_log` 每条含 `seq/ts/actor/action/target/before/after/reason`；每次对正文、证据映射或
result.json 结构做实质修改都要追加一条，自检回修也要回写。章节稿正文写入 `/app/output/`
（如 manuscript.md，或 output_contract 指定文件名），result.json 以路径引用它。可编辑
Office/幻灯片/图示还须附真实渲染的 PDF/PNG 预览；无法渲染时写 `limitations`，不假装已渲染。

# 执行流程（总预算 900s / 64 次模型请求）

时间纪律：**前 3 分钟内必须落盘一版 result.json 骨架**（`status=in_progress`，全部契约字段
占位齐全），此后只做增量改进；每次改动立即写回文件，不得只在会话里“记住”。

1. **读取与归一化（≤60s，1–2 次）**：运行 `main.py build`；通读 case.json/task.md/材料，
   记下受众、文体、格式、`output_contract` 字段、图表与统计量清单。
   输入→输出：原始输入 → input_summary.json。
2. **落骨架（≤120s，1 次）**：`main.py build` 已生成含 case_id + 全部必需字段（null 占位）+
   status/sources/uncertainty/pending_human/change_log/limitations/runtime 的 result.json。
   **此步完成即达成“可提交的最小诚实交付”。**
3. **要素规划与映射（≤240s，8–12 次）**：按写作七要素（图表事实清单/结果段/讨论段/语气
   校准/证据映射/局限与人工确认/版本边界）把材料证据映射到章节；区分“材料内事实 vs 推断”
   与“结果 vs 讨论”；逐个填实骨架字段。输入→输出：input_summary.json → 填充后的 result.json。
4. **成稿（≤240s，10–16 次）**：写结果段与讨论段正文到 /app/output，填充 deliverable/
   facts_sources/version_boundaries；每段可回溯到 locator（文件+图/表号/页码）。
5. **自检与回修（≤180s，6–10 次）**：运行
   `python scripts/main.py validate --result /app/output/result.json --contract /app/work/contract.json`；
   按下方清单逐项修复，把结论写回 change_log。
6. **收尾（剩余时间）**：补渲染预览、写 uncertainty（未确认项）与 limitations、置
   status=complete 或 partial。

保留约 20% 请求与时间余量用于异常重试；超时即冻结现状，诚实标注 partial/failed。

# 自检清单（逐条可勾选，括号内为对应评分维度）

- [ ] 任务完成度【实际效果40】：交付可编辑、可提交的结果段与讨论段正文 + 证据映射，而非建议/提纲/模板。
- [ ] 结果正确性【实际效果40】：每个数值/单位/误差棒含义/统计量都能在材料原文找到，无材料外新增事实。
- [ ] 结果完整性【实际效果40】：图表、阴性结果、不一致文献与局限如实交代；七要素齐全且衔接。
- [ ] 结果可核验性【实际效果40】：正文数值→论断→证据（locator+quote）三级对应，可独立复核。
- [ ] 一致性·对得上【使用增益25】：结果段↔讨论段↔图表↔局限逐条对应；趋势/差异方向、幅度、p、n 与图表一致。
- [ ] 效率【使用增益25】：先脚本归一化输入、自动生成骨架、自动回检，减少起草-核对-返工往返。
- [ ] 来源可溯·查得到【使用增益25】：facts_sources/sources 覆盖关键论断，quote 为原文，区分 verified/unverified/pending_human。
- [ ] 方法改进【创新增益15】：图表事实清单→语气校准→结果/讨论分离→证据回检→禁词扫描由脚本强制执行。
- [ ] 无空话修饰·说得准【创新增益15】：全文无“首次/国际领先/填补空白/颠覆性”等无据措辞（有据则给 locator+quote）。
- [ ] 调用稳定【可靠体验10】：长材料/多图/重复运行下按脚本产出口径一致的稿件与 result.json。
- [ ] 异常已处理【可靠体验10】：材料缺失/冲突/超长/引用无法核实/误差棒不明/格式冲突已写入 uncertainty/pending_human/limitations。
- [ ] 资源可复用【生态贡献10】：SKILL.md、写作七要素、检查清单、降级规则、自检脚本齐全，他人可按说明独立使用。
- [ ] 结构合法【全部】：result.json 含 case_id + 全部 output_contract 字段 + status/sources/uncertainty/pending_human/change_log/limitations/runtime，可解析。
- [ ] 未编造【全部】：status/sources/uncertainty/change_log 与真实执行一致，未写任何评测分数或伪造运行结果。

# 降级策略（禁止编造）

- 材料缺失/字段不明：在 uncertainty 或 version_boundaries 标注“缺失/无法判断”，字段留空或写
  null，不补造数值、文献、作者、统计结果。
- 图表读数不清/误差棒含义不明：标 `read_uncertainty` + `pending_human`，正文用“约”，不给伪精确值。
- 引用无法核实：保留原文位置并标 `unverified`，不伪造 DOI/URL。
- 软件/工具不可用（无 pdftotext、office 渲染、latex）：如实写 limitations，交付纯文本/结构化
  产物，不假装已渲染。
- 输入超长/跨度过大：截取关键段落，并在 uncertainty 说明“已截断/未覆盖”。
- 运行失败：status=failed，change_log 记录失败原因与已完成中间产物，result.json 仍须落盘。
- 详见 `references/degradation-and-verification.md`。

# 脚本与资源（相对路径，从 SKILL.md 所在目录执行）

- `scripts/main.py`：`build` 归一化输入并落骨架；`validate` 回检契约与一致性；`selftest` 自检。
- `scripts/core_impl.py`：契约提取、骨架构建、change_log、禁词扫描、证据核验、sigfig 辅助。
- `scripts/selftest.py`：空目录/坏 JSON/缺契约/禁词/字段缺失五种自检，退出码 0 即通过。
- `references/results-discussion-checklist.md`：写作七要素、语气阶梯、证据映射 schema、一致性检查表。
- `references/manuscript-structure.md`：deliverable 规范骨架、结果/讨论组织、局限四层、交付前核对顺序。
- `references/degradation-and-verification.md`：降级、status 取值、change_log 语义、核验规则。

全部只用 Python 标准库；示例：`python scripts/main.py --help`。

# 最小调用示例（示意，非正式案例结果）

示意输入（非正式案例）：`/input/case.json` 含 `case_id="demo-rdw-1"` 与
`output_contract={"deliverable":..., "facts_sources":[...], "version_boundaries":{...}}`。
按上述六步执行后，`/app/output/result.json` 示意结构（标注为示例）：

```json
{"case_id":"demo-rdw-1",
 "deliverable":{"file":"manuscript.md"},
 "facts_sources":[{"claim_id":"R2-1","locator":"fig2.pdf#panel-B","quote":"viability 62 ± 5% (n=3)","kind":"figure","status":"verified"}],
 "version_boundaries":{"human_confirm":["误差棒含义待确认"],"gaps":[]},
 "status":"complete","sources":[...],"uncertainty":[],"pending_human":[],
 "change_log":[{"seq":0,"ts":"...","action":"skeleton","target":"result.json","reason":"..."}],
 "limitations":[],"runtime":{...}}
```
