---
name: results-discussion-writing
description: >
  结果与讨论章节写作 Skill。当任务要求依据给定科研材料（数据表、图、图注、统计输出、
  文献）产出可投稿/可继续编辑的结果（Results）段与讨论（Discussion）段，或进行改写、
  校对、引用核验、双语/结构化交付时使用；必须保留事实来源，区分“观察事实 vs 解释推测”
  与“结果段 vs 讨论段”，并交付段落→证据（locator+逐字 quote）可回溯的 result.json。
  核心突破：证据链可回溯的图表→正文转写（读得准 + 核得了），辅以语气-证据强度匹配。
  Use for writing Results & Discussion sections grounded in provided research
  materials (data tables, figures, captions, statistical outputs, references),
  with verifiable evidence mapping and honest uncertainty handling.
version: 1.0.0
---

# 角色与何时使用

本 Skill 让 Agent 把给定的数据、图表、统计输出与文献材料写成可直接投稿或继续编辑的
结果段与讨论段，并交付“段落 → 证据”三级可回溯的 result.json。核心突破方向是
**证据链可回溯的图表→正文转写（读得准 + 核得了）**，同时以规则保障结果/讨论分界
（分得清）与语气-证据强度匹配（说得住）。满足任一即调用：

- 任务要求依据给定材料产出结果段与讨论段、改稿、校对、引用核验或结构化交付；
- 任务含“图表读数 / 趋势与差异 / 组间比较 / 显著性 / 讨论证据 / 因果边界 / 局限”等要求；
- 要求保留事实来源、区分观察事实与解释推测、交付 result.json。

不适用：纯文献泛读、测试题生成、非写作类数据分析。

# 输入契约（运行时，Linux 固定路径）

- `/input/case.json`：case_id、任务说明、output_contract（result.json 必需字段）、
  材料清单、受众/文体/格式约束（可能另在 organizer-workorder.json）。
- `/input/task.md`：题目与评分要求，属事实底稿的一部分。
- `/input/` 下其余文件：材料原文（PDF/文本/表格/统计输出等）。PDF/图片用平台
  pdf-text-tool/ocr-tool 解析，不读二进制；**只依据实际读取到的内容写作，不依据
  文件名/领域标签补造数据**。
- 工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`（含 result.json）。

第一步运行（从 SKILL.md 所在目录，即 `/skills/weekly-skill`，执行）：

```bash
python scripts/main.py run --input-dir /input --work /app/work --out /app/output/result.json
```

产出 `/app/work/input_summary.json`（case_id、output_contract 字段、材料清单）与
`/app/output/result.json` 骨架。

# 输出契约（必须满足）

`/app/output/result.json` 必须包含：

1. `case_id`：与 /input/case.json 一致；
2. `output_contract` 声明的**全部字段**，字段名原样保留、不增删改名；若未声明，
   至少含 `deliverable`（可编辑稿件内容与文件路径）、`facts_sources`（数字/术语/
   引用/图表/关键论断的逐条来源映射）、`version_boundaries`（修改差异、人工确认项、
   材料缺口、异常）；
3. 附加字段：`status`（`in_progress|partial|complete|failed|insufficient_evidence|
   input_error`）、`sources`（来源定位数组）、`uncertainty`（不确定项数组）、
   `pending_human`（待人工确认数组）、`change_log`（带时间戳与动作的修改记录数组）、
   `degradations`、`errors`、`runtime`。

稿件正文写入 `/app/output/`（如 manuscript.md，或 output_contract 指定文件名），
result.json 以路径引用它。可编辑 Office/幻灯片/图示还必须附真实渲染的 PDF/PNG 预览。

# 执行流程（总预算 900s / 64 次模型请求）

时间纪律：**前 3 分钟内必须落盘一版 result.json 骨架**，此后只做增量改进；每次改动
立即写回文件，不得只在会话里“记住”。保留约 20% 时间与请求余量用于异常重试。

1. **读取与归一化（≤60s，1–2 次请求）**：`main.py run`（或 `main.py read`）；通读
   case.json/task.md/材料，记下受众、文体、格式、output_contract 字段与材料清单。
   输入→输出：原始输入 → /app/work/input_summary.json。
2. **落骨架（≤120s，1 次请求）**：`main.py build --summary /app/work/input_summary.json
   --out /app/output/result.json`，生成 case_id + 全部必需字段（null/[] 占位）+
   status/sources/uncertainty/pending_human/change_log 的 result.json。
   **此步完成即达成“可提交的最小诚实交付”。**
3. **证据抽取与映射（≤240s，8–12 次请求）**：对每张图/表/统计输出用
   `main.py quote --file <材料> --locator <定位>` 抽取逐字 quote，逐条填入
   facts_sources；区分“观察事实 vs 解释推测”“结果段 vs 讨论段证据”。
   输入→输出：input_summary.json → 填充后的 result.json（`build --patch` 或直接写）。
4. **成稿（≤240s，10–16 次请求）**：写结果段与讨论段到 /app/output，填充
   deliverable/facts_sources/version_boundaries；每段可回溯到材料位置。
5. **自检与回修（≤180s，6–10 次请求）**：`main.py check --result … --summary …`；
   按下方自检清单逐项修复，把结论写回 change_log。
6. **收尾（剩余时间）**：补渲染预览、写 uncertainty（未确认项）、置
   status=complete 或 partial。

# 自检清单（逐条勾选，括号内为对应评分维度）

- [ ] 任务完成度【实际效果40】：交付可编辑、可投稿的结果段与讨论段，而非提纲/建议/模板。
- [ ] 结果正确性【实际效果40】：每个数值/单位/误差棒含义/统计量/推断强度与材料一致，无材料外新增事实。
- [ ] 结果完整性【实际效果40】：图表、阴性结果、不一致文献与局限被如实交代。
- [ ] 结果可核验性【实际效果40】：正文数值与讨论论断能通过 locator+逐字 quote 独立复核。
- [ ] 一致性·说得住【使用增益25】：结果段↔图/表↔讨论论断逐条对应，语气与证据强度匹配。
- [ ] 效率【使用增益25】：脚本先归一化、自动落骨架、自动回检，减少起草/核对/返工往返。
- [ ] 来源可溯·查得到【使用增益25】：facts_sources 覆盖关键数值与论断，区分观察事实与解释推测。
- [ ] 方法改进【创新增益15】：图表读数→证据抽取→结果/讨论分界→语气回检由脚本强制执行。
- [ ] 无空话修饰·说得准【创新增益15】：全文无“首次/国际领先/填补空白/颠覆性”等无据措辞。
- [ ] 调用稳定【可靠体验10】：空输入/坏JSON/坏编码/缺参数/超大文件/重复运行均不崩溃（selftest.py 可验）。
- [ ] 异常已处理【可靠体验10】：材料缺失/冲突/超长/引用无法核实/格式冲突已写入 version_boundaries 或 uncertainty。
- [ ] 资源可复用【生态贡献10】：SKILL.md、写作规则、降级清单、自检脚本齐全，他人可按说明独立使用。
- [ ] 结构合法【全部】：result.json 含 case_id + 全部 output_contract 字段 + status/sources/uncertainty/pending_human/change_log，可解析。
- [ ] 未编造【全部】：status/sources/uncertainty/change_log 与真实执行一致，未写任何评测分数或伪造运行结果。

# 降级策略（禁止编造）

- 材料缺失/字段不明：在 uncertainty 或 version_boundaries.gaps 标注“缺失/无法判断”，
  字段留空或写 null，**不补造数值、文献、作者、政策口径**。
- 引用无法核实：保留原文位置并标 `unverified`，不伪造 DOI/URL。
- PDF/图片解析失败：标 `not_reported`，不按文件名猜内容。
- 软件/工具不可用（无 pdftotext、office 渲染等）：如实写 limitations，交付纯文本/
  结构化产物，不假装已渲染。
- 输入超长/单文件>2MB：脚本自动跳过或截断，并在 degradations 说明“已截断/未覆盖”。
- 运行失败：status=failed，change_log 记录失败原因与已完成中间产物，result.json 仍须落盘。
- 详见 `references/degradation-and-verification.md`。

# 脚本与资源（相对路径，从 SKILL.md 所在目录执行）

- `scripts/main.py`：`read`/`build`/`check`/`quote`/`run` 五子命令，纯 Python 标准库。
- `scripts/selftest.py`：鲁棒性自检（空输入、坏JSON、坏编码、缺参数、超大文件、重复运行、逐字 quote）。
- `references/results-discussion-rules.md`：结果/讨论分界、语气强度、因果边界、局限四层、图表读数、证据溯源规则。
- `references/degradation-and-verification.md`：状态语义、降级策略、locator 约定、核验规则与 result.json 字段规范。

全部只用 Python 标准库；示例：`python scripts/main.py --help`。

# 最小调用示例（示意，非正式案例结果）

示意输入：`/input/case.json` 含 `case_id="demo-case"` 与
`output_contract={"deliverable":…,"facts_sources":[…],"version_boundaries":{…}}`；
`/input/` 含材料 results.txt、table2.md。

```bash
python scripts/main.py run --input-dir /input --work /app/work --out /app/output/result.json
python scripts/main.py quote --file /input/table2.md --locator "Table 2"
```

示意输出（示意，非正式案例结果）：
`{"case_id":"demo-case","deliverable":{"file":"manuscript.md"},…,
"facts_sources":[{"claim":"…","locator":"table2.md#Table 2","quote":"…",
"kind":"statistic","status":"verified"}],"version_boundaries":{…},
"status":"partial","sources":[…],"uncertainty":[…],"pending_human":[],
"change_log":[…],"degradations":[],"errors":[]}`
