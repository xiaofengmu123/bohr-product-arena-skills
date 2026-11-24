---
name: paper-quality-audit
description: 论文质量审查 Skill。把"这篇稿子哪里有问题"变成结构固定、可定位、可交办的问题清单：按事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类分面逐条审查，每条给出原文定位与逐字引用、严重度、问题性质、判断依据与修改建议；采用"先建来源索引→写草稿→机械校验逐字引用→成稿"的 verify-then-emit 流程，未逐字命中的结论自动降级，输出可复核的 result.json 与 review_report.md。用于投稿前自审、审稿意见、结题/可发表性检查。
version: 2.0.0
---

# 论文质量审查 Skill

## 1. 角色与何时使用

本 Skill 把科研材料（PDF/DOCX/TXT/MD/JSON 等）做**逐条、可定位、可复核**的质量审查，交付
问题清单而非整篇评语。适用：投稿前自审、审稿、结题前检查、事实/引用/方法/统计/逻辑/规范一致性核验。
不适用：润色改写、语言翻译、查重比对、投稿排版等事务性工作。

## 2. 输入契约（评测环境事实）

- 原始输入固定为 `/input/case.json` + `/input/task.md`；科研材料也在 `/input/` 下，文件名不保证固定。
- `case.json` 可能含 `case_id`、`task_instruction`、`output_contract`（dict 或 list）；脚本会扫描发现，不假设键名。
- 工作目录 `/app/work`，最终产物写 `/app/output/`（必须含 `result.json`，可另含 `review_report.md`）。
- 预算：**900 秒、64 次模型请求、单次回复 ≤16384 词元**；780 秒必须 finalize。
- 脚本仅依赖 Python 标准库；`scripts/main.py` 用相对路径调用，不要硬编码安装路径。

## 3. 输出契约

必须产出 `/app/output/result.json`（字段规格见 `references/output-schema.md`）：

1. `case_id`：原样保留 `case.json` 的 case_id，不得丢失。
2. **`output_contract` 全部字段原样回填**：不改名、不删除，未知键写 `null`/`[]`。
3. 信封字段：`status`、`sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、
   `changes`、`change_log`、`runtime`。
4. 主结构 `result.findings[]` + `result.unchecked_items[]` + `result.not_applicable[]` +
   `result.coverage` + `result.verdict_summary`。

`status` 取值：`ok` / `partial`（写清缺口）/ `insufficient_evidence`（材料不可用）/ `input_error`（坏 JSON 等）。
单条 finding 形状与判据见 `references/output-schema.md` 与 `references/review-checklist.md`。

## 4. 执行流程（分步、每步输入输出、时间预算）

脚本不替你做阅读理解；**必须先读材料、写出草稿，禁止只跑 build 就结束**。命令序列（均在 Skill 根目录）：

| 步骤 | 命令 | 输入 | 输出 | 时间预算 |
|---|---|---|---|---|
| 0 落盘骨架 | `python3 scripts/main.py scan` | `/input` 全部文件 | `/app/work/index.json` + 合法 partial `result.json` | **≤3 分钟（前 3 分钟内必须落盘）** |
| 1 通读 | `python3 scripts/main.py show` + 读 case.json/task.md/材料 | 步骤 0 索引 + 原文 | 理解检查项、材料、六类 facet | 3–8 分钟 |
| 2 分面审查 | 亲自推理，写 `/app/work/draft.json` | 材料原文 + `references/review-checklist.md` | `draft.json`（findings/unchecked/not_applicable/status） | 8–12 分钟 |
| 3 机械校验 | `python3 scripts/main.py verify` | `draft.json` + 索引 | `verify_report.json`（quote 逐字命中/字段/枚举） | 12–13 分钟 |
| 4 成稿 | `python3 scripts/main.py build` | `draft.json` + 索引 | `review_report.md` + 最终 `result.json` | 13–14 分钟 |
| 5 复检 | `python3 scripts/main.py selfcheck` | 最终产物 | `selfcheck.json`（仅复检，不重做） | 可选 |

要点：

- **步骤 0 用 scan 先落盘合法 result.json 骨架**；此后每一步失败都不会留下空产物，之后只做增量改进。
- 步骤 1 必须读 `case.json` 的 `output_contract` 与 `task_instruction`，确认六类 facet 中哪些与本稿相关。
- 步骤 2 是主工作：逐 facet 排查（判据见 `references/review-checklist.md`）；每条 finding 的
  **quote 必须是原文逐字片段**，先用 `show` 抄录 locator 与原文。
- 步骤 3 `verify` 报 `quote not found verbatim` 就回原文重抄，直到无 FAIL。
- 接近 780 秒立即 `build`；未完成部分写进 `unchecked_items`/`pending_human` 并置 `status=partial`。

**本版差异化架构（verify-then-emit）**：`build` 会对每条 quote 做规范化 + 截断 SHA-256 +
子串命中校验；`confirmed` 但未逐字命中的 finding 自动降级为 `to_review` 并写入 `uncertainty`，
从机制上保证"confirmed 必有逐字证据"，这是与"先评后验"方案的关键区别。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] 实际效果：`result.json` 是结构化问题清单，每条 finding 有 facet/severity/issue_type/
      location/quote/rationale/revision_suggestion；不是整篇评语或复述。
- [ ] 结果正确性：每条 quote 已由 `verify` 逐字命中原文；severity/issue_type 与判据一致；无凭空结论。
- [ ] 结果完整性：六类 facet 都处理——有 finding 或列入 `unchecked_items`/`not_applicable` 并说明原因。
- [ ] 可核验性：`case_id`、稿件来源、locator、逐字 quote、quote_hash、判定依据、`sources` 齐全，
      他人能回到原文逐条核对。
- [ ] 使用增益：同一输入重复运行，`findings`/`counts`/`coverage` 结构一致（易变量只进 `runtime`/`change_log`）。
- [ ] 可靠体验：`python3 -B scripts/main.py --help` 退出码 0；空输入/坏 JSON/缺材料/坏 PDF
      均退出码 0 且产出合法 `result.json`。
- [ ] 生态贡献：`SKILL.md` 引用的每个相对路径存在；脚本仅标准库；无 `__pycache__`/`.pyc`；无预制答案。
- [ ] 提交前运行 `python3 -B scripts/selftest.py`，四场景全部 PASS。

## 6. 降级策略（缺数据/缺软件如实标注，不编造）

| 场景 | 处置 | 落点 |
|---|---|---|
| PDF 无文本层/扫描件 | 优先调用平台 PDF 文本工具，把逐页文本（页间 `\f`）存为 `/app/work/text/<source_id>.txt` 后 `scan --rescan`；仍不可读则 `insufficient_evidence`，不猜内容 | `degradations` + `pending_human` |
| 图片/图表无 OCR | 调用 OCR 工具；失败则列缺口，不猜数值 | `pending_human` |
| 参考文献缺失/引用无法核验 | 列 `citation_support` 为未检查，注明"无法核验"，不臆断被引文献主张 | `unchecked_items` + `uncertainty` |
| 原文歧义/两处数值冲突 | 两处都记录并标 `source_conflict`，不擅自取舍 | `uncertainty` |
| 材料超长（单文件 >2MB） | 跳过并记录；不截断后假装全查 | `degradations` |
| 写盘失败/异常 | 顶层捕获、退出码恒 0，保留最小合法 `result.json` | `errors` |
| 缺软件（无渲染/OCR 等） | 如实写 `limitations`，不伪造软件状态、实验数据或评测分数 | `degradations` |

原则：材料不足就写 `insufficient_evidence`/`partial` 并给缺口，**绝不编造数据、引用、DOI 或结论**；
不确定项一律 `to_review` 而非 `confirmed`。

## 7. 参考文件

- `references/review-checklist.md`：六类 facet 检查锚点、严重度/问题性质判据、误报抑制规则。
- `references/output-schema.md`：`result.json` 完整字段规格与 finding 字段说明。
- `scripts/audit_core.py`：核心库（仅标准库）；`scripts/selftest.py`：离线自检（四场景）。
