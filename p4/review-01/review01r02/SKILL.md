---
name: paper-quality-review
description: 论文质量审查 Skill。把“这篇稿子哪里有问题”变成结构固定、可定位、可复核的问题清单：按事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类分面逐条审查，每条给出原文 locator 与逐字 quote、严重度、问题性质、判断依据与修改建议；内置 900 秒/64 请求阶段预算与“前 3 分钟先落盘 partial 骨架”的早落盘机制，输出可复核的 result.json 与 review_report.md。适用于投稿前自审、审稿、结题/可发表性检查。
version: 1.1.0
---

# 论文质量审查 Skill

## 1. 角色与何时使用

本 Skill 对给定论文/科研材料做**逐条、可定位、可复核**的质量审查，交付问题清单而非整篇
评语。适用：投稿前自审、审稿、结题前检查，以及事实/引用/方法/统计/逻辑/规范的一致性核验。

不适用：润色改写、语言翻译、查重比对、投稿排版等事务性工作。

## 2. 输入契约（评测环境事实）

- 原始输入固定为 `/input/case.json` + `/input/task.md`；材料（PDF、DOCX、TXT/MD、图片、
  表格、JSON 等）也在 `/input/` 下。**只以实际文件为事实依据**，不要按文件名/标签猜内容。
- `case.json` 可能含 `case_id`、`task_instruction`、`output_contract`（dict 或 list）；
  键名与文件名不保证固定，脚本扫描发现，不要假设。
- 工作目录 `/app/work`，最终产物写 `/app/output/`（必须含 `result.json`）。
- 预算：**900 秒、64 次模型请求、单次回复 ≤16384 词元**；780 秒必须 finalize。
- 脚本只依赖 Python 标准库；下列命令在 Skill 根目录（评测环境为 `/skills/weekly-skill`）执行。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含（完整字段见 `references/output-schema.md`）：

1. `case_id` 原样保留；`output_contract` 全部字段原样回填（未知键写 `null`/`[]`，不改名不删除）。
2. 信封字段：`status`、`sources`、`uncertainty`、`pending_human`、`errors`、
   `degradations`、`changes`、`change_log`、`runtime`。
3. 主结构：`result.findings[]` + `result.unchecked_items[]` + `verdict_summary`。

`status` 取值：`ok`（六类相关项已查）/ `partial`（部分完成，写明缺口）/
`insufficient_evidence`（材料不可用）/ `input_error`（输入坏 JSON 等）。
单条 finding 形状与判据见 `references/review-checklist.md` 与 `output-schema.md`，
核心字段：`finding_id / facet / severity / issue_type / verdict / claim / location / quote /
rationale / revision_suggestion / confidence / evidence`。主产物 `review_report.md` 由
`build` 生成，逐条列定位/原文/依据/建议，供回到原文复核。

## 4. 执行流程（阶段预算 + 前 3 分钟早落盘铁律）

脚本不替你阅读理解：**必须先读材料、写工作稿，禁止只跑 build 就结束**。命令序列：

| 步骤 | 命令（相对路径） | 输入 | 输出 | 时间预算 |
|---|---|---|---|---|
| 0 早落盘骨架 | `python3 -B scripts/main.py init` | `/input` 全部文件 | `/app/work/index.json`、`draft.json`（含机械预审）、**合法 partial `result.json`** | **0–180 秒（前 3 分钟内必须落盘）** |
| 1 通读 | `python3 -B scripts/main.py show` + `plan` + 读 `case.json`/`task.md`/原文 | 步骤 0 索引 + 原文 | 理解检查项、材料、六类 facet 边界 | 180–420 秒 |
| 2 分面审查 | 亲自推理，改 `/app/work/draft.json` | 原文 + 六类判据 | findings/unchecked/coverage/overall_verdict | 420–660 秒 |
| 3 机械校验 | `python3 -B scripts/main.py check` | `draft.json` + 索引 | `check_report.json`（quote 命中/字段/枚举） | 660–780 秒 |
| 4 成稿 | `python3 -B scripts/main.py build` | `draft.json` + 索引 | `review_report.md` + 最终 `result.json` | 660–780 秒 |
| 5 复检 | `python3 -B scripts/main.py selfcheck` | 最终产物 | `selfcheck` 打印（仅复检，不重做） | 780–900 秒 |

要点：

- **步骤 0 铁律：先 `init` 落盘一版合法 result.json 骨架**。`init` 自带确定性“机械预审”
  （结构章节缺失、同符号数值冲突、引用标记缺失），让骨架一落地就含真实、可定位、保守
  （`to_review`）的线索；之后每一步失败都不会留下空产物，只做增量改进。
- 步骤 1 必读 `case.json` 的 `output_contract` 与 `task_instruction`，确认六类 facet 中
  哪些与本稿相关；`plan` 随时查看剩余时间/当前阶段/分面覆盖。
- 步骤 2 是主工作：逐 facet 排查，每发现一条写一条 finding；**quote 必须原文逐字**，
  可先用 `show` 抄录；覆盖声明写进 `draft.json.coverage`（`checked/n/a/not_checked`），
  机械预审的 `mechanical=true` 线索复核确认后才能改 verdict/severity。
- 步骤 3 `check` 报 `quote not found verbatim` 就回原文重抄，直到无 ERROR。
- 接近 780 秒立即 `build`；未完成部分写 `unchecked_items`/`pending_human`，置 `status=partial`。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] **实际效果**：`result.json` 是结构化问题清单，每条 finding 有 facet/severity/issue_type/
      location/quote/rationale/revision；不是整篇评语或复述。
- [ ] **结果正确性**：每条 quote 已在 `check` 中逐字命中原文；severity/issue_type 与判据一致；无凭空结论。
- [ ] **结果完整性**：六类 facet 都处理——有 finding，或 coverage=`n/a`，或列 `unchecked_items` 并说明原因。
- [ ] **可核验性**：`case_id`、稿件来源、locator、逐字 quote、判定依据、`sources` 齐全，他人能逐条回到原文核对。
- [ ] **使用增益**：同一输入重复运行，`findings`/`counts`/`coverage` 结构一致（易变量只进 `runtime`）。
- [ ] **可靠体验**：`python3 -B scripts/main.py --help` 退出码 0；空输入/坏 JSON/缺材料/坏 PDF 均退出码 0 且产出合法 `result.json`。
- [ ] **生态贡献**：`SKILL.md` 引用的每个相对路径存在；脚本仅标准库；无 `__pycache__`/`.pyc`；无预制答案。
- [ ] **本地自检**：提交前跑 `python3 -B scripts/selftest.py`（不触碰 `/input`，不预写测试答案），应 8/8 通过。

## 6. 降级策略（缺数据/缺软件必须如实标注，绝不编造）

| 场景 | 处置 | 落点 |
|---|---|---|
| PDF 无文本层/扫描件 | 优先调用平台 pdf-text-tool，把逐页文本（页间 `\f`）存 `/app/work/text/<source_id>.txt` 后 `init`（或 `init --rescan`）；仍不可读则 `insufficient_evidence`，不猜内容 | `degradations` + `pending_human` |
| 图片/图表无 OCR | 调用 OCR 工具；失败则列缺口，不猜数值 | `pending_human` |
| 参考文献缺失/无法核验 | 列 `citation_support` 为未检查，注明“无法核验”，**不臆断被引文献主张** | `unchecked_items` + `uncertainty` |
| 原文歧义/两处数值冲突 | 两处都记录并标 `source_conflict`，不擅自取舍 | `uncertainty` |
| 材料超长（单文件 >2MB） | 跳过并记录；不截断后假装全查 | `degradations` |
| 写盘失败/异常 | 顶层捕获、退出码恒 0，保留最小合法 `result.json` | `errors` |
| 缺软件（无渲染/OCR 等） | 如实写 `limitations`，**不伪造软件状态、实验数据或评测分数** | `degradations` |

原则：材料不足就写 `insufficient_evidence`/`partial` 并给缺口；不确定项一律 `to_review`
而非 `confirmed`；模型请求数、运行时长等由平台记录，`runtime` 中置 `null`，不自行编造。

## 7. 参考文件

- `references/review-checklist.md`：六类 facet 检查锚点、severity/issue_type/verdict 判据、误报抑制规则、coverage 标注。
- `references/output-schema.md`：`result.json` 完整字段规格与 finding 字段说明。
- `scripts/core.py`：抽取/机械预审/校验/组装的确定性实现；`scripts/selftest.py`：作者侧端到端自检。
