---
name: lit-review-synthesis
description: 围绕固定研究问题，在多篇真实文献上完成可复核的文献综述：先定筛选标准，再做主题归纳、证据组织与带逐条回链的综述草稿；冲突、证据空白与方法局限显式写入 uncertainty 与 limitations，缺数据不编造。当输入含多篇论文/题录、任务要求产出综述、系统筛选、趋势分析或选题评估时使用；不用于单篇精读、全文翻译或语言润色。
version: 1.1.0
---

# 文献综述合成 Skill（降级与不确定性强化版）

## 1. 角色与触发时机
把一批真实文献加工成“可复核、可回链、不越界”的综述。四步流水线：筛选 → 主题归纳 → 证据组织 → 草稿；每步产物可独立定位，出错时能判断是筛选、归纳还是引用出了问题。本版强化一条主线：**所有冲突、空白与方法局限都落到结构化的 `uncertainty`/`limitations`，任何缺失标 `not_reported`，绝不越过证据范围补造**。

- 使用：任务要求“文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估”，且输入含多篇文献（PDF 或题录）。
- 不使用：单篇精读、全文翻译、语言润色、投稿选刊、事实层面的学术审查。

## 2. 输入契约（运行时固定）
- 原始输入：`/input/case.json`（研究问题、`output_contract`、材料路径）+ `/input/task.md`。
- 材料可能在 `/input/materials/` 或 `case.json` 里给出路径；**必须读真实文件**，PDF 用 `pdftotext` 自行解析，不采信文件名标签或“预抽取答案层”。
- 工作目录 `/app/work`；最终产物与可编辑文件写 `/app/output/`。
- 预算：**900 秒、64 次模型请求、每响应 ≤16384 token（含推理）**。**前 3 分钟必须已落盘一版合法 `result.json`（status=partial）**，此后只做增量改进，禁止最后一次性倾倒。

## 3. 输出契约（必须满足）
`/app/output/result.json` 必须存在且为合法 JSON：
1. `case_id`（来自 `case.json`，缺失用文件名）；
2. `output_contract` 的**全部字段**：键名与类型原样保留，缺值填 `null`/`[]`，不删未知键、不改名；
3. 追加诚实字段 `status`、`sources`、`uncertainty`、`change_log`，另附 `pending_human`、`errors`、`degradations`、`runtime`。

`status`：`ok`（四步完成且证据可回链）/ `partial`（部分完成，列缺口）/ `insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。

`uncertainty` 为对象（本版核心）：`conflicts`（并列呈现的结论冲突）、`gaps`（无文献覆盖的具体证据缺口）、`limitations`（方法/材料局限，逐条带类型）、`unverifiable`（无法核实项）、`not_reported`（未报告字段）。

主产物（写 `/app/output/`，文件名固定）：
- `review.md`：综述草稿，每条实质论断带 `[S#]` 回链标记；
- `screening_log.json`：筛选标准（写在纳排决定**之前**）+ 逐篇决定 + 去重记录；
- `evidence_table.jsonl`：证据行（字段见 §5）；
- `themes.md`：主题/子主题 + 支撑文献 + 边界；
- `conflicts_and_gaps.md`：冲突并列 + 有据空白 + 局限清单。

`change_log` 每项 `{ts, step, action, detail}`；每个 Phase 结束追加一条，可追溯“哪一步改了什么”。

## 4. 执行流程（每步输入→输出，共 900 秒）
**Phase 0（0–2 分钟）——先落盘骨架**
执行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`（无 `python3` 用 `python -B`）。
输出：合法 `result.json`（status=partial、全部契约字段、空 `sources/uncertainty/change_log`）+ `work/inventory.json` + `work/extracted/*.txt`。此步保证任何后续异常都不留空产物。

**Phase 1（2–6 分钟）——盘点与筛选**
读 `inventory.json` 与题录（.bib/.ris/.json/.csv），补全元数据；按 SHA-256/DOI/题名去重；**先写筛选标准**（研究对象、纳入、排除、去重规则），再逐篇做纳入/排除决定。
输出：`/app/output/screening_log.json`。

**Phase 2（6–10 分钟）——证据抽取与主题归纳**
逐篇抽可比较字段（对象/方法/样本量/主要结果/结论/局限/未报告项）写 `evidence_table.jsonl`；主题从证据聚类生成（按方法、对象、结论分歧），**不套**“背景/方法/挑战/展望”通用框架。
输出：`/app/output/evidence_table.jsonl` + `themes.md`。

**Phase 3（10–14 分钟）——草稿 + 冲突 + 空白**
按证据表写 `review.md`：每段对应一组证据；冲突结论并列标 `disputed`；空白落到具体证据缺口；局限与未报告项写入 `uncertainty`/`limitations`。
输出：`review.md`、`conflicts_and_gaps.md`，并增量更新 `result.json` 的 `sources/uncertainty`。

**Phase 4（14–15 分钟）——自检与定稿**
执行 `python3 -B scripts/selftest.py --check --output /app/output --case /input/case.json`。修正问题后把 `status` 置 `ok`（或如实保持 `partial`/`insufficient_evidence`）。每个 Phase 结束都增量写盘。

## 5. 证据与写作规则
- **筛选先于写作**：纳入/排除标准含四要素——研究对象、纳入条件、排除条件、去重规则；每条决定可回溯。
- **证据行字段**：`evidence_id`、`source_id`、`locator`（文件+页码/段落）、`quote`（逐字原文）、`claim`（支撑论断）、`strength`、`field`、`notes`。
- **strength 四级**：`established`（多源一致且方法充分）/ `suggestive`（单源或方法有限）/ `disputed`（有冲突）/ `not_reported`。
- **措辞按 strength**：established→“表明/证实”；suggestive→“提示/初步表明”；disputed→“存在争议”；not_reported→“未见报告”。**“未见报告”≠“不存在”**。
- **回链**：正文 `[S#]` 指向 evidence_table 行；文末 sources 表（真实来源+元数据+定位）。
- **禁止**：虚构文献/DOI/数值；跨文档串值；把相关写成因果；把摘要当全文证据；把领域标签当事实依据。

## 6. 降级与不确定性策略（本版重点，诚实标注，禁止编造）
每条局限写 `uncertainty.limitations` 并记 `degradations`，类型见 `references/evidence-spec.md` §3：`abstract_only`（仅摘要→结论降为 `suggestive`/`not_reported`，不补正文数据）、`no_full_text`、`metadata_incomplete`、`source_conflict`（两源不一致都记录，不静默择一）、`tool_missing`、`encoding_fallback`、`out_of_scope`、`duplicate_removed`。
- 材料不足以回答问题 → `status=insufficient_evidence`，在 `uncertainty.gaps` + `pending_human` 列出缺口，不生成示例填充。
- 缺字段/未知字段 → 保留键填 `null`/`[]`，记入 `errors`/`degradations`；**绝不编造评测分数或运行结果**。
- 结论冲突 → `uncertainty.conflicts` 并列呈现来源与分歧点，不抹平。

## 7. 自检清单（对应评分维度，逐条勾选）
- [ ] 四步全跑完、产物齐全，结论与研究问题和证据范围一致（任务完成度）
- [ ] 每条实质论断可指回 `source_id`+`locator`+`quote`，引述强度与原文一致（结果正确性）
- [ ] 覆盖主要主题、方法路线、正反证据与分歧，空白与局限有据（完整性）
- [ ] 文献清单、筛选记录、证据表、引用定位可独立复核（可核验性）
- [ ] `result.json` 含 `case_id`+全部契约字段+`status/sources/uncertainty/change_log`（调用易用性）
- [ ] 缺全文/元数据不全/重复/冲突/检索为空/超上下文均有处置（异常处理）
- [ ] 脚本只用标准库、重复运行结果一致（运行稳定性）
- [ ] 每条 `uncertainty.limitations` 都有类型与受影响来源，不写空泛套话（本版强化项）

## 8. 最小调用示例（示意，非正式结果）
```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
# Agent 在 /app/work 与 /app/output 下完成筛选、证据抽取、草稿
python3 -B scripts/selftest.py --check --output /app/output --case /input/case.json
python3 -B scripts/selftest.py --self   # 合成场景自检
```
Phase 0 已由 `main.py` 完成 PDF 抽取，无需单独脚本。

## 9. 边界
- 只解决“多文献综述”一类任务，不做通用大而全 Skill。
- 没有真实材料时输出“材料不足”，不用示例填充。
- 交付前在不同环境各跑一次 `scripts/selftest.py --self`。
