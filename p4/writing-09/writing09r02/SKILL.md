---
name: results-discussion-writing
description: >
  结果与讨论章节（Results & Discussion）写作 Skill。当任务要求依据给定论文、图表、数据表与统计输出，
  产出可直接进入稿件的 Results 与 Discussion 章节稿，或进行改写、校对、语气强度校准、引用核验、双语或
  结构化交付时使用。四条硬标准：读得准（图表数值、单位、误差棒、统计量不被猜）、分得清（结果只述事实、
  讨论承担解释与局限）、说得住（趋势/差异/因果语气与证据强度匹配）、核得了（每个数值回指图表、每条论断
  回指本研究数据或具体文献）。保留逐字来源与修改记录，在 /app/output 交付可复核的 result.json。
  Use for writing or revising Results & Discussion sections from provided data, figures, tables and
  statistical outputs, with per-claim verbatim provenance.
version: 1.0.0
---

# 角色与何时使用

本 Skill 把“数据、图表、统计输出在手，但尚未成稿”的材料转成**可直接投稿的 Results 与 Discussion 章节稿 +
逐条证据溯源 + 版本边界**，而不是写作建议、提纲或通用模板。满足任一即调用：

- 任务含“结果与讨论 / Results / Discussion / 图表读数 / 趋势差异 / 因果边界 / 局限 / 投稿章节”等；
- 要求依据给定材料产出可编辑章节稿、改稿、语气校准、引用或格式结果、双语/结构化产物；
- 要求保留来源、区分“结果 vs 讨论”、交付 result.json。

不适用：纯文献泛读、测试题生成、非写作类数据分析。

# 输入契约（运行时 Linux 固定路径）

- `/input/case.json`：case_id、任务说明、`output_contract`（result.json 必需字段，键名原样保留）、
  材料清单、受众/文体/格式约束。
- `/input/task.md`：题目与评分要求，属事实底稿的一部分。
- `/input/` 下其余文件：材料原文（PDF/文本/表格/图）。**PDF 用平台 pdf-text-tool 解析，本 Skill 只登记
  不读二进制；只依据实际读取到的内容写作，不依据文件名/领域标签补造数据。**
- 工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`（含 result.json 与章节稿）。

先读文件再写；第一步运行：

```bash
python scripts/read_input.py --case /input/case.json --task /input/task.md \
  --input-dir /input --work /app/work
```

产出 `/app/work/input_summary.json`（case_id、output_contract 原样、材料清单、约束与告警）。

# 输出契约（必须满足）

`/app/output/result.json` 必须包含：

1. `case_id`：与 `/input/case.json` 一致；
2. `output_contract` 声明的**全部字段**，键名原样保留、不增删改名；未声明时至少含
   `deliverable`（章节稿内容与文件路径）、`facts_sources`（数字/术语/引用/图表/关键论断的逐条
   locator + 逐字 quote）、`version_boundaries`（修改差异、人工确认项、材料缺口、异常）；
3. 附加字段：`status`（`in_progress|partial|complete|failed|insufficient_evidence`）、
   `sources`（来源定位数组，每项含 `locator`+`quote`）、`uncertainty`（不确定项数组）、
   `pending_human`（需人工确认项）、`change_log`（带时间戳与动作的修改记录）、`limitations`（降级说明）。

章节稿正文写入 `/app/output/`（如 `results_discussion.md`，或 output_contract 指定文件名），
result.json 以路径引用它。章节稿用 `## Results` / `## Discussion`（或中文 `## 结果` / `## 讨论`）分段，
便于自检脚本区分两节。可编辑 Office/幻灯片/图示还必须附真实渲染的 PDF/PNG 预览。

# 执行流程（总预算 900s / 64 次模型请求，16384 token/响应）

**时间纪律：第 1 个动作周期内、最迟 3 分钟内必须调用 build_result.py 落盘一版 result.json 骨架
（status=partial），此后只做增量改进，每次改动立即以 `--patch` 写回，禁止只在会话里“记住”。**

| 阶段 | 时间 | 请求 | 输入 → 输出 |
|---|---|---|---|
| 1 读取与归一化 | ≤60s | 1–2 | 原始输入 → `input_summary.json` |
| 2 落骨架（早落盘） | ≤120s | 1 | `input_summary.json` → `result.json`(partial) |
| 3 证据抽取与图表读数 | ≤180s | 6–10 | 材料 → facts 清单（locator+quote） |
| 4 结果段成稿 | ≤180s | 8–12 | facts → `## Results` |
| 5 讨论段成稿与证据对照 | ≤180s | 8–12 | `## Results` → `## Discussion` |
| 6 自检与回修 | ≤150s | 6–10 | `check_result.py` 报告 → 修复 |
| 7 收尾 | 剩余 | 余量 | 渲染预览、补 uncertainty、置 status |

保留约 20% 时间与请求余量用于异常重试；超时即冻结现状，诚实标注 `partial` 或 `failed`。

# 核心写作规则（读得准 / 分得清 / 说得住 / 核得了）

**读得准（图表→正文）**：每个写进正文的数值必须同时满足——带单位、有效数字与源一致、误差棒含义明确
（SD / SEM / 95%CI / 范围四选一，不混用）、带样本量与统计量（n、检验方法、p 值或效应量、自由度）、
带图/表定位（`Fig 1B`、`Table 2`、`Fig S3`）。多面板写 `Fig 1A/B`，补充材料写 `Fig S*`。
禁止用“大约/似乎/左右”补足不确定数值；读不清的数值进 `uncertainty` 或 `pending_human`。

**分得清（结果 vs 讨论）**：结果段只陈述观察事实——方向、幅度、组间比较、显著性、阴性结果；
不解释原因、不引文献作比较、不写机制。讨论段承担解释、文献对照、机制推测（明确标注“推测”）、
局限。采用**两遍写作法**：先把结果段的事实清单写满，再从事实清单逐条取证写讨论段。

**说得住（语气强度匹配证据）**：语气动词强度必须等于证据强度，见下表。

| 证据强度 | 允许的表述 |
|---|---|
| 显著差异且有检验 | “显著升高/降低（p=…, n=…）” |
| 方向一致但未达显著 | “呈…趋势，但未达统计学显著（p=…）” |
| 仅在部分组出现 | “仅在高剂量组观察到…（n=…，检验…）” |
| 相关 | “相关/关联（r=…, p=…）”；预测：给模型指标 |
| 因果 | 仅随机对照/干预/因果推断设计可用“导致/引起”，否则写“相关，不足以推断因果” |

**核得了（逐条溯源）**：`facts_sources`（或 output_contract 等价字段）每条给
`locator`（`<文件名>#<页码/图号/段落>`）+ 逐字 `quote` + `used_in`（正文哪句用了它）。
每个数值回指图表/表/统计输出；每条讨论论断回指本研究数据或具体文献。文献引用只写材料中真实出现的
条目，不补 DOI/页码/作者。详见 `references/results-discussion-rules.md`。

# 自检清单（逐条可勾选，括号内为对应评分维度）

- [ ] 任务完成度【实际效果·完成度15】：交付可编辑、可投稿的 Results 与 Discussion 章节稿，非建议/提纲/模板。
- [ ] 结果正确性【实际效果·正确性15】：每个数值/单位/误差棒/统计量与原始图表一致，无材料外新增事实。
- [ ] 结果完整性【实际效果·完整性5】：图表、阴性结果、不一致文献与局限均被如实交代。
- [ ] 结果可核验性【实际效果·核验5】：正文数值与讨论论断可独立复核（locator+quote 覆盖关键论断）。
- [ ] 一致性·对得上【使用增益·质量15】：结果段只述事实、讨论段承担解释；语气与证据强度匹配。
- [ ] 效率【使用增益·效率5】：脚本归一化→早落盘→自动回检，减少起草与返工往返。
- [ ] 来源可溯·查得到【使用增益·可验证5】：facts_sources 覆盖关键论断，区分“本研究数据 vs 外部文献”。
- [ ] 方法改进【创新增益·方法5】：读得准/分得清/说得住/核得了四关由脚本强制回检。
- [ ] 无空话修饰·说得准【创新增益·能力/证据】：全文无“首次/国际领先/填补空白/颠覆性”等无据措辞（材料原句引用除外）。
- [ ] 调用稳定【可靠体验·稳定4】：长材料/多图表/重复运行下按脚本产出口径一致的稿件与 result.json。
- [ ] 异常已处理【可靠体验·异常3】：缺图/缺统计量/单位不明/引用找不到/图表读数不清已写入 uncertainty、pending_human 或 limitations。
- [ ] 资源可复用【生态贡献·资源4】：SKILL.md、规则、检查清单、降级规则齐全，他人可独立使用。
- [ ] 结构合法【全部】：result.json 含 case_id + 全部 output_contract 字段 + status/sources/uncertainty/change_log，可解析。
- [ ] 未编造【全部】：status/sources/uncertainty/change_log 与真实执行一致，未写评测分数或伪造运行结果。

# 降级策略（禁止编造）

- 缺数据/缺图/缺统计量/单位不明：在 `uncertainty`、`pending_human` 或 `version_boundaries` 标注
  “缺失/无法判断”，字段留空或写 null，**不补造数值、文献、作者、政策口径**。
- 图表读数不清：保留可读部分，标注 `chart_reading_unclear`，不猜刻度与误差棒。
- 引用找不到：保留材料原文位置并标 `unverified`，不伪造 DOI/URL。
- 软件/工具不可用（无 pdftotext、office 渲染等）：如实写 `limitations`，交付纯文本/结构化产物，不假装已渲染。
- 输入超长/跨度过大：截取关键段落，并在 uncertainty 说明“已截断/未覆盖”。
- 运行失败：`status=failed`，`change_log` 记录失败原因与已完成中间产物，result.json 仍须落盘。
- 详见 `references/degradation-and-verification.md`。

# 脚本与资源（相对路径，从 SKILL.md 所在目录执行）

- `scripts/read_input.py`：读取并归一化输入，产出 input_summary.json。
- `scripts/build_result.py`：生成/合并 result.json 骨架；`--patch` 增量更新（早落盘）。
- `scripts/check_result.py`：校验结构 + 一致性/禁词/来源回检；`--strict` 把告警视为失败、`--selftest` 自检。
- `scripts/main.py`：一键编排 read→build→check；`--selftest` 端到端自检（临时演示夹具，非正式案例）。
- `references/results-discussion-rules.md`：读得准/分得清/说得住/核得了规则与语气校准表。
- `references/degradation-and-verification.md`：降级矩阵、校验规则与 status 语义。

全部只用 Python 标准库；示例：`python scripts/build_result.py --help`。

# 最小调用示例（示意，非正式案例结果）

示意输入 `/input/case.json`：`{"case_id":"demo","output_contract":{"deliverable":null,"facts_sources":null,"version_boundaries":null}}`。
执行 `python scripts/main.py --case /input/case.json --task /input/task.md --input-dir /input --work /app/work --output /app/output`
后，`/app/output/result.json` 示意结构（示意，非正式案例）：

```json
{"case_id":"demo","deliverable":{"file":"results_discussion.md"},"facts_sources":[{"locator":"materials/data.txt#L3","quote":"...","used_in":"## Results"}],"version_boundaries":{},"status":"complete","sources":[],"uncertainty":[],"pending_human":[],"change_log":[],"limitations":[]}
```
