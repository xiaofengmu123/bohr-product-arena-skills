---
name: library-retrieval-evidence
description: 科研文件智能检索与资料库修复（document library retrieval & repair）：确定性盘点文件/目录/权限，按内容 SHA-256 去重、按归一化主干+内容相似度重建版本候选链，识别符号/硬链接别名，解析行政版本说明与库目录（JSON/CSV/Markdown/BibTeX/RIS/SQLite），恢复对账并重建可检索索引；执行跨文件检索、章节定位、过滤硬约束与证据回链。每个结论带 confidence 置信分级与逐字 quote，缺数据/缺软件时显式写 limitations/uncertainty/degradations，不编造。当需要整理科研资料库、维护元数据与版本关系、回答检索问题并给出可复核定位时使用。
version: 1.1.0
---

# 科研文件智能检索 Skill

## 1. 角色与何时使用

面向"科研文件智能检索 / 资料库修复"：材料分散、命名不一、存在改名副本、修订版、扫描件、别名与断链。本 Skill 把工作分成两层：**确定性脚本层**（盘点、哈希、去重、版本、别名、对账、索引）与**Agent 语义层**（跨文件语义检索、章节定位、过滤硬约束、证据回链）。核心价值是"动手落库 + 留证据 + 诚实标注不确定"，不是只给建议。

适用：文件入库与目录归位、元数据/版本维护、重复与别名识别、缺失/断链恢复、检索问答与证据回链、长期归档交接。不适用：开放网络文献检索、论文综述写作、一次性脚本、无材料时的示例填充。

## 2. 本版设计取舍（降级与不确定性显式化）

相对"只算哈希+给候选"的基线，本版把**置信分级**与**降级码**做成一等公民：

- 每条结论必须带 `confidence`（`certain | high | medium | low | unknown`）与 `evidence_basis`；无证据支持的字段写 `not_reported`，绝不猜测。
- 每个无法完成的计算写进 `degradations`（`code` + `severity` + `action`），并说明跳过的是哪一步，而不是静默填假值。
- `limitations` 显式声明范围与能力缺口，`uncertainty` 逐项说明不确定原因、严重度与补救方向，`pending_human` 列需要人复核的项。
- 确定性脚本只产出候选与机械预筛；语义判定由 Agent 在候选上增量补证据，避免把启发式当事实。

## 3. 输入契约（运行时，只读）

- `/input/case.json`：案例标识、任务说明、检索问题/过滤条件与 `output_contract`（键名可能为 `output_contract`/`output`/`result_schema`/`contract`/`output_schema` 等，脚本自动识别）。
- `/input/task.md`：题目规则、组织约定、字段要求、行政版本说明与完成标准。
- `/input/` 其余文件与目录：科研材料（PDF、Office、扫描件、JSON/CSV/Markdown/BibTeX/RIS/SQLite 等）。**以真实文件内容为准，不依据领域标签补造数据。**

工作目录 `/app/work/`（中间产物），最终产物 `/app/output/`。硬约束：所有源文件只读，不移动、不覆盖、不删除；同一输入重复运行结果一致（遍历排序、哈希稳定、无随机数，仅时间戳变化）。

## 4. 输出契约（`/app/output/result.json`）

必须产出 `result.json`，至少含：

- `case_id`：从 case.json / task.md 识别；无法识别时写 `unknown` 并记入 `uncertainty`。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实标注，不把未完成写成完成。
- 题目 `output_contract` 的**全部字段原样回填**：缺值给 `null`/`[]`，不改名、不删未知键；与保留键（`case_id`/`status`/`sources`/`uncertainty`/`change_log`/`computed`/`errors`/`degradations`/`runtime`/`limitations`/`pending_human`）冲突时改存 `computed._contract_<字段>`。
- `sources`：每个关键结论的证据，形如 `{"item","locator","quote","evidence_type","confidence"}`；`locator` 指向 `相对路径#页码/行号/段落`，`quote` 为逐字原文。
- `uncertainty`：`{"item","reason","severity","evidence_basis","mitigation"}`；冲突不隐藏，标 `source_conflict`。
- `change_log`：`{"time","actor","step","detail"}` 操作时间线。
- 辅助字段 `errors`/`degradations`/`limitations`/`pending_human`/`runtime`；机械证据统一放 `computed`（`files`/`summary`/`duplicate_groups`/`version_candidates`/`version_notes`/`aliases`/`reconciliation`/`answers`/`index`/`artifacts`）。

其余可编辑产物（写入 `/app/output/`）：`library_manifest.csv`（文件清单）、`directory_tree.txt`（目录树，空目录标 `[空目录]`）、`dedup_version.json`（重复组/版本候选/版本说明/别名）、`recovery_report.json`（对账+errors+degradations）、`library_index.json`（可检索索引）、`library_manifest.md`（交接报告）、`operation_log.jsonl`（操作日志）。

## 5. 执行流程（总预算 900 秒 / 64 次模型请求）

原则：**先落盘、再分析、后增量改进**；任一阶段失败都不得留下空产物。脚本读写不消耗模型请求；64 次请求优先用于读题、读材料、语义判定与复核。每步完成后用 `--update` 落盘，避免最后一次性写结果丢失进度。

| 步骤 | 输入 | 动作与输出 | 时间预算 |
|---|---|---|---|
| 0 骨架 | case.json + task.md | 立即执行 `python3 -B scripts/main.py --skeleton-only`，写出含 `case_id` + contract 占位 + `status=partial` 的 result.json；同时读 task.md 提取组织规则、字段要求与版本说明 | ≤ 3 分钟（**必须先落盘**） |
| 1 盘点与修复 | /input/ 全目录 | 执行 `python3 -B scripts/main.py`（内部：盘点→哈希→去重→版本→别名→版本说明→恢复对账→索引→机械问答→产物），产出全部可编辑产物并写完整 result.json | ≤ 2 分钟 |
| 2 语义检索与定位 | 清单 + 索引 + 规则 + 原始材料 | 读 case.json 的检索问题/过滤条件，对 PDF 跑 `pdftotext`、扫描件跑 OCR、Office 抽取文本；做跨文件语义匹配、章节/页码/段落定位、过滤硬约束、证据回链；将结论写成 `patch.json`，用 `python3 -B scripts/main.py --update patch.json --result /app/output/result.json` 增量合并 | 剩余约 6–8 分钟 |
| 3 证据与复核 | result.json + 产物 | 为每个关键结论补 `sources`（locator+quote）与 `uncertainty`；执行 `python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json` | ≤ 2 分钟 |
| 4 定稿 | 复核通过 | 将 `status` 改为 `ok`，或按实际缺口保留 `partial`/`insufficient_evidence`/`input_error`；确保所有 contract 字段已填 | 收尾 |

### 5.1 语义检索阶段（步骤 2 的操作要点）

1. **取题**：从 case.json 读 `questions/queries/asks/prompts` 与过滤条件（年份、项目编号、文件类型、实验批次、版本、权限）。条件无法满足时如实写 `filter_unsatisfied`，**不放宽**。
2. **预筛**：用 `computed.index` 与 `computed.answers` 得到候选文件；对候选读原文提取文本（PDF→`pdftotext -layout`，扫描件→OCR，Office→相应提取器）。
3. **定位**：把命中落到 `章节标题 + 页码 + 段落首句`，处理印刷页与 PDF 物理页错位、双栏、跨页表格与图注；定位不能确定时写 `page_uncertain` 并给段落锚点。
4. **回链**：为每条结论生成 `sources`：`locator = 相对路径#页码`，`quote` 为命中段落逐字片段（≤320 字符）。
5. **置信分级**：逐字命中且可复核=high/certain；同义改写命中=medium；只命中文件名或推断=low；无法验证=unknown 并进入 `pending_human`。

## 6. 证据溯源与置信分级（硬要求）

- 每个关键结论/数值/重复/版本判定都需 `sources` 条目；`quote` 必须是材料逐字原文，不得改写、不得虚构哈希/DOI/页码。
- 同一指标多来源不一致时，都记录并标 `source_conflict`，在 `uncertainty` 说明取舍。
- 冲突不隐藏、缺失不补齐：缺失写 `not_reported` 或放 `pending_human`。
- 每个结论带 `confidence`，并说明 `evidence_basis`（如 `content_sha256`、`text_similarity`、`pdftotext_quote`、`ocr_quote`、`filename_heuristic`）。

## 7. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] **实际效果**：已产出真实清单/目录树/版本记录/恢复报告/索引与检索回链，而非仅建议；落库文件与源文件一一对应，哈希一致。
- [ ] **实际效果**：检索命中落在原文段落并给出页码/章节/段落首句与逐字 quote；过滤条件作为硬约束执行，未满足时如实报告。
- [ ] **可核验**：`library_manifest.csv`、`operation_log.jsonl`、`dedup_version.json`、`recovery_report.json`、`library_index.json` 均存在，他人可按记录复跑并核对到同一段。
- [ ] **使用增益**：去重按内容哈希而非文件名；版本按归一化主干+内容差异给候选并标 `heuristic`；机械问答自动给出证据定位，减少人工翻页/比对。
- [ ] **创新增益**：别名识别（符号链接含断链+硬链接组）、行政版本说明解析、恢复对账（matches/mismatches/missing/orphaned）与索引重建均已落盘；检索—定位—过滤—回链四步闭环。
- [ ] **可靠体验**：空目录、坏 JSON、坏编码、缺权限、超大文件、无文本层扫描件均不崩溃，逐条记录进 `errors`/`degradations`。
- [ ] **可靠体验**：`result.json` 通过 `--check`；`status` 诚实，`case_id` 非空，contract 字段齐全；缺 case.json 时 `status=input_error`，空材料时 `status=insufficient_evidence`。
- [ ] **文档**：`SKILL.md` frontmatter 含 name/description/version；脚本用相对路径 `scripts/main.py`/`scripts/selftest.py`；调用示例已标"示例"，未冒充正式案例结果。

## 8. 降级策略（缺数据/缺软件，不编造）

- **缺数据/缺软件**：如实写 `status=insufficient_evidence` 或 `partial`，在 `errors`/`degradations`/`uncertainty`/`limitations` 中说明；**禁止编造**文件内容、哈希、元数据或运行结果。
- **输出契约缺失**：脚本仍产出仅含元数据字段的合法 result.json，并在 `uncertainty` 标 `output_contract` 缺失。
- **超大文件/符号链接/不可读/非常规文件**：跳过内容哈希，保留路径、大小、权限、链接目标，记录 `too_large`/`symlink`/`unreadable`/`non_regular`。
- **SQLite 打不开/坏表**：记录 `sqlite_open_error`/`sqlite_query_error`，不崩溃。
- **无文本层扫描件**：记录 `no_text_layer`，用 OCR 尝试，仍失败则写 `not_reported` 并进入 `pending_human`，不编造可读文本。
- **写盘失败**：保留已落盘骨架，退出码 2，不崩溃；重启后可续跑 `--update`。
- **空输入目录**：退出码 0，`status=insufficient_evidence`；**缺 case.json**：`status=input_error`，均不抛异常。

## 9. 边界

只解决"整理 + 证据 + 版本 + 别名 + 恢复对账 + 索引 + 检索问答"这一类任务；不做通用文件系统、不做存储系统底层权限建设、不做开放网络文献检索与综述写作；没有真实材料时不生成示例答案。

## 最小调用示例（示例，非正式案例结果）

```bash
# 骨架（前 3 分钟内必须完成）
python3 -B scripts/main.py --skeleton-only

# 完整确定性盘点（默认读 /input，写 /app/output 与 /app/work）
python3 -B scripts/main.py

# Agent 语义结论增量合并
python3 -B scripts/main.py --update patch.json --result /app/output/result.json

# 复核
python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json

# 包内自检（本机交付前）
python3 -B scripts/selftest.py
# 示意输出: SELFTEST PASS: 30 / FAIL: 0
```
