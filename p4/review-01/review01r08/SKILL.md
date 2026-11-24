---
name: paper-quality-review
description: 论文质量审查 Skill。把“这篇稿子哪里有问题”变成结构固定、可定位、可复核的问题清单：按事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类分面逐条审查，每条给出原文定位与逐字引用、严重度、问题性质、判断依据与修改建议，并回填题目 output_contract 全部字段；输出 result.json 与 review_report.md。当需要投稿前自审、审稿意见、结题/可发表性检查时使用。
version: 1.1.0
---

# 论文质量审查 Skill

## 1. 角色与何时使用

对给定论文/科研材料做**逐条、可定位、可复核**的质量审查，交付问题清单而非整篇评语。适用于投稿前自审、审稿、结题前检查、事实/引用/方法/统计/逻辑/规范的一致性核验。不适用于润色改写、语言翻译、查重比对、投稿排版。

## 2. 输入契约（评测环境事实）

- 原始输入固定为 `/input/case.json` + `/input/task.md`；科研材料（PDF、DOCX、TXT/MD、图片、表格、JSON）也在 `/input/` 下。
- `case.json` 可能含 `case_id`、`task_instruction`、`output_contract`（dict 或 list）；键名与文件不保证固定，脚本会扫描发现，不要假设文件名。
- 工作目录 `/app/work`，最终产物写 `/app/output/`（必须含 `result.json`）。
- 预算：**900 秒、64 次模型请求、单次回复 ≤16384 词元**；780 秒必须 finalize。
- 脚本只用 Python 标准库；从 Skill 根目录用相对路径调用（`python3 scripts/main.py ...`）。

## 3. 输出契约

必须产出 `/app/output/result.json`：

1. `case_id`：原样保留 `case.json` 的 case_id，不得丢失。
2. **`output_contract` 全部字段原样回填**：不改名、不删除；与信封内已有真实值同名的键（`findings`/`unchecked_items`/`verdict`/`review_notes`）自动镜像真实值，其余未知键写 `null`/`[]`。
3. 追加信封字段：`status`、`sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、`limitations`、`changes`、`change_log`、`runtime`。
4. 审查主结构 `result.findings[]` + `result.unchecked_items[]` + `verdict_summary` + `coverage`。

`status` 取值：`ok`（相关 facet 已查并有结果）/ `partial`（部分完成，写明缺口）/ `insufficient_evidence`（材料不可用）/ `input_error`（输入坏 JSON 等）。

单条 finding 形状（判据见 `references/review-checklist.md`）：

```json
{
  "finding_id": "F01",
  "facet": "fact_check|citation_support|method_design|statistics_report|argument_chain|academic_norms",
  "severity": "critical|major|minor|suggestion",
  "issue_type": "factual_error|insufficient_evidence|standards_issue|suggestion",
  "verdict": "confirmed|to_review|not_checked",
  "claim": "本文论断/问题描述",
  "location": "para7 或 p3#para2",
  "quote": "逐字原文（可跨行不跨段）",
  "rationale": "判断依据",
  "revision_suggestion": "可执行修改建议",
  "confidence": "high|medium|low",
  "evidence": [{"source_id": "D1", "locator": "...", "quote": "..."}]
}
```

主产物 `review_report.md` 由 `build` 生成，逐条列 finding 与定位/引用，供他人回原文复核。

## 4. 执行流程（分步骤、每步输入输出、时间预算）

脚本不替你阅读理解；**必须先读材料、写出工作稿，禁止只跑 build 就结束**。命令序列：

| 步骤 | 命令 | 输入 | 输出 | 时间预算 |
|---|---|---|---|---|
| 0 落盘骨架 | `python3 scripts/main.py init` | `/input` 全部文件 | `/app/work/index.json` + 合法 `partial result.json` | **≤3 分钟（前 3 分钟内必须落盘）** |
| 1 通读 | `show` + 读 `case.json`/`task.md`/材料原文 | 步骤 0 索引 + 原文 | 理解检查项、材料、六类 facet | 3–10 分钟 |
| 2 分面审查 | 亲自推理，写 `/app/work/draft.json` | 材料原文 + 六类判据 | `draft.json`（findings/unchecked/verdict_summary） | 10–13 分钟 |
| 3 机械校验 | `python3 scripts/main.py check` | `draft.json` + 索引 | `check_report.json`（quote 命中/locator/字段/枚举/覆盖） | 13–14 分钟 |
| 4 成稿 | `python3 scripts/main.py build` | `draft.json` + 索引 | `review_report.md` + 最终 `result.json` | 14–15 分钟 |
| 5 复检 | `python3 scripts/main.py selfcheck` | 最终产物 | `selfcheck.json`（含 score_alignment） | 可选 |

要点：

- **步骤 0 用 init 先落盘一版合法 result.json 骨架**；此后每一步失败都不会留下空产物。之后只做增量改进。
- 步骤 1 必须读 `case.json` 的 `output_contract` 与 `task_instruction`，确认六类 facet 中哪些与本稿相关。
- 步骤 2 是主工作：逐 facet 排查，每发现一条问题写一条 finding；**quote 必须是原文逐字片段**，用 `show` 抄录。
- 步骤 3 `check` 报 `quote not found verbatim` 或 `locator_ok: false` 就回原文重抄，直到无 FAIL；报 `missing_facets` 就补 finding 或列 `unchecked_items`。
- 接近 780 秒立即 `build`，未完成部分写进 `unchecked_items`/`pending_human` 并置 `status=partial`。

## 5. 评分维度对齐（本版核心设计）

题目 5 大评分维度（实际效果 40 / 使用增益 25 / 创新增益 15 / 可靠体验 10 / 生态贡献 10）已逐条映射到上述流程与自检项 S1–S16，完整表见 `references/scoring-alignment.md`。执行时每步同时满足对应维度；`selfcheck` 输出按维度汇总的 `score_alignment`。增益不自我宣称，由平台固定案例对照验证。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] **S1 实际效果·任务完成度**：`result.json` 是结构化问题清单，每条 finding 有 facet/severity/issue_type/location/quote/rationale/revision；不是整篇评语或复述。
- [ ] **S2 实际效果·结果正确性**：每条 quote 已在 `check` 中逐字命中原文，locator 指向含该 quote 的段落；severity/issue_type 与判据一致；无凭空结论。
- [ ] **S3 实际效果·结果完整性**：六类 facet 都处理——有 finding 或列入 `unchecked_items`（并说明原因）。
- [ ] **S4 实际效果·可核验性**：`case_id`、稿件来源、locator、逐字 quote、判定依据、`sources` 齐全，他人能回原文逐条核对。
- [ ] **S5 使用增益·质量增益**：`verdict_summary.counts` 与 `facet_counts` 存在，结构固定可比对。
- [ ] **S6 使用增益·效率增益**：`runtime.phases` 记录了 init→build 的骨架先行、增量改进轨迹。
- [ ] **S7 使用增益·增益可验证性**：同一输入重复运行 `findings`/`counts`/`coverage` 结构一致（易变量只进 `runtime`）。
- [ ] **S8 创新增益·新增能力**：逐条可定位到原句（location+quote），分面审查、引用主张对照。
- [ ] **S9 创新增益·方法改进**：severity/issue_type/confidence 枚举合法，误报抑制规则已执行。
- [ ] **S10 创新增益·创新证据**：不伪造分数与白板，limitations 如实标注，对照留给平台。
- [ ] **S11 可靠体验·易用性**：`--help` 退出码 0；SKILL.md 用相对路径调用。
- [ ] **S12 可靠体验·稳定性**：不同篇幅/学科/排版可运行；result.json 必填键齐备。
- [ ] **S13 可靠体验·异常处理**：空输入/坏 JSON/缺材料/坏 PDF/扫描件/超长均退出码 0 且产出合法 `result.json`。
- [ ] **S14 生态贡献·文档完整性**：SKILL.md frontmatter 含 name/description/version，输入/输出/流程/预算/降级齐全。
- [ ] **S15 生态贡献·资源可复用性**：脚本仅标准库；references 齐全；无 `__pycache__`/`.pyc`；无预制答案。
- [ ] **S16 生态贡献·分享推广**：SKILL.md 引用的每个相对路径存在，他人可独立复现。

提交前跑一次 `python3 scripts/selftest.py`（合成案例，非正式案例）确认 10/10 通过。

## 7. 降级策略（缺数据/缺软件必须如实标注，不编造）

| 场景 | 处置 | 落点 |
|---|---|---|
| PDF 无文本层/扫描件 | 优先调用平台 pdf-text-tool，逐页文本（页间 `\f`）存为 `/app/work/text/<source_id>.txt` 后重跑 `init`；仍不可读则 `insufficient_evidence`，不猜内容 | `degradations` + `pending_human` |
| 图片/图表无 OCR | 调用 ocr-tool；失败则列缺口，不猜数值 | `pending_human` |
| 参考文献缺失/引用无法核验 | 列 `citation_support` 为未检查，注明“无法核验”，不臆断被引文献主张 | `unchecked_items` + `uncertainty` |
| 原文歧义/两处数值冲突 | 两处都记录并标 `source_conflict`，不擅自取舍 | `uncertainty` |
| 材料超长（单文件 >2MB） | 跳过并记录；不截断后假装全查 | `degradations` |
| 写盘失败/异常 | 顶层捕获、退出码恒 0，保留最小合法 `result.json` | `errors` |
| 缺软件（无渲染/OCR 等） | 如实写 `limitations`，不伪造软件状态；不伪造实验数据与评测分数 | `degradations` |

原则：材料不足就写 `insufficient_evidence`/`partial` 并给缺口，**绝不编造数据、引用、DOI 或结论**；不确定项一律 `to_review` 而非 `confirmed`。

## 8. 参考文件

- `references/review-checklist.md`：六类 facet 的检查锚点、严重度/问题性质判据、误报抑制规则。
- `references/scoring-alignment.md`：题目评分维度逐条到流程与自检项 S1–S16 的映射。
- `scripts/main.py`：init/show/check/build/selfcheck 五种子命令；`scripts/core_impl.py`：抽取与校验；`scripts/selftest.py`：提交前自检。
