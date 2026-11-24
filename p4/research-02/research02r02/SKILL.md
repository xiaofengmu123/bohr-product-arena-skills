---
name: lit-review-synthesis
description: 围绕固定研究问题，在多篇真实文献上完成可复核的文献综述：先写明筛选标准，再做主题归纳、证据组织与带逐条回链的综述草稿，显式记录结论冲突与研究空白，并把 900 秒/64 次请求拆成阶段预算、前 3 分钟先落盘结果骨架。当输入含多篇论文或题录、任务要求产出文献综述、系统筛选、证据综合、趋势分析或选题评估时使用；不用于单篇精读、全文翻译或语言润色。
version: 2.0.0
---

# 文献综述合成 Skill（时间预算与早落盘版）

## 1. 角色与触发时机

本 Skill 指挥 Agent 把一批真实文献加工成"可复核"的综述：**筛选 → 主题归纳 → 证据组织 → 草稿生成**四步，每步产物可独立定位，出错时能判断是筛选、归纳还是引用出了错。

- 使用：任务说明含"文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估"，且输入含多篇文献（PDF、题录、记录文件）。
- 不使用：单篇精读、全文翻译、语言润色、投稿选刊、事实层面的学术审查。

## 2. 输入契约（运行时固定）

- 原始输入：`/input/case.json`（研究问题、`output_contract`、材料路径）+ `/input/task.md`。
- 材料可能在 `/input/materials/` 或 `case.json` 里给出路径；**必须读真实文件**，PDF 自行用 `pdftotext` 解析，不得相信"预抽取答案层"或文件名标签。
- 工作目录 `/app/work`（中间产物）；最终产物与可编辑文件写 `/app/output/`。
- 预算：**900 秒、64 次模型请求、每响应 ≤16384 token（含推理）**。

## 3. 输出契约（必须满足）

`/app/output/result.json` 必须存在且为合法 JSON，包含：
1. `case_id`（来自 `case.json`，缺失则用文件名）；
2. `output_contract` 里的**全部字段**：原样保留键名与类型，缺值填 `null`/`[]`，不删未知键、不改名；
3. 追加诚实字段：`status`、`status_reason`、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`。

`status` 取值：`ok`（四步完成且证据可回链）/ `partial`（部分完成，须列缺口）/ `insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。

主产物（写 `/app/output/`）：
- `review.md`：综述草稿，每条实质论断带 `[S#]` 标记；
- `screening_log.json`：筛选标准 + 逐篇纳排决定 + 去重记录；
- `evidence_table.jsonl`：证据行（字段见 §6 与 `references/evidence_spec.md`）；
- `themes.md`：主题与子主题，各自列出支撑文献与边界；
- `conflicts_and_gaps.md`：冲突结论并列呈现 + 有据的研究空白。

## 4. 执行流程（分阶段时间预算，共 900 秒 / 64 请求）

**Phase 0（0–3 分钟，≤6 请求）——先落盘骨架**
执行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`。
输出：合法 `result.json`（`status=partial`、全部契约字段、空 sources/uncertainty/change_log）+ `plan.json` 时间预算计划。**此后任何异常都不会留下空产物。**

**Phase 1（3–6 分钟，≤12 请求）——盘点与筛选**
输入：材料文件。扫描去重（按 DOI/题名/SHA-256），PDF 经 `pdftotext` 抽取（失败记入 `degradations`）。
输出：`/app/work/inventory.json`、`/app/work/extracted/*.txt`、`screening_log.json`（纳入/排除标准写在筛选决定**之前**）。

**Phase 2（6–10 分钟，≤24 请求）——证据抽取与主题归纳**
逐篇抽取可比较字段（研究对象/方法/样本量/主要结果/结论/局限/未报告项），写 `evidence_table.jsonl`。
输出：证据表 + `themes.md`。主题从证据聚类生成（方法、对象、结论分歧），**不套**背景/方法/挑战/展望的通用框架。

**Phase 3（10–14 分钟，≤20 请求）——草稿 + 冲突 + 空白**
写 `review.md`（每段对应一组证据）；冲突结论并列标 `disputed`；空白落到具体证据缺口。
输出：`review.md`、`conflicts_and_gaps.md`。

**Phase 4（14–15 分钟，≤2 请求）——自检与定稿**
执行 `python3 -B scripts/validate_result.py --output /app/output --case /input/case.json`。修正后置 `status=ok`（或如实保持 `partial`/`insufficient_evidence`）。

**预算护栏（硬规则）**
- 每个 Phase 开始/结束各写一次检查点到 `/app/work/plan.json`，记录剩余预算；
- 每次模型请求前先查 `plan.json`：若已用 ≥62 次请求或距 900 秒不足 30 秒，停止新分析，直接跑 `validate_result.py` 并以现有产物定稿；
- 前 3 分钟必须已有一版合法 `result.json`；之后任何失败只追加 `change_log`/`errors`，不覆盖骨架。

## 5. 自检清单（对应评分维度，逐条可勾选）

- [ ] 四步全跑完、产物齐全、结论与研究问题和证据范围一致（任务完成度）
- [ ] 每条实质论断可指回 `source_id`+`locator`+`quote`，引述强度与原文一致（结果正确性）
- [ ] 覆盖输入文献集主要主题、方法路线、正反证据与分歧，空白有据（结果完整性）
- [ ] 文献清单、筛选记录、证据表、引用定位可独立复核（结果可核验性）
- [ ] `result.json` 含 `case_id`+全部契约字段+`status/sources/uncertainty/change_log`（调用易用性）
- [ ] 缺全文/元数据不全/文献重复/结论冲突/检索为空/超上下文均有处置（异常处理）
- [ ] 脚本只用标准库、重复运行结果一致（运行稳定性）
- [ ] 交付前执行 `python3 -B scripts/selftest.py` 全部 PASS（离线自检）

## 6. 证据与写作规则

- **筛选先于写作**：纳入/排除标准含四要素——研究对象、纳入条件、排除条件、去重规则；每条决定可回溯。
- **证据行字段**：`evidence_id`、`source_id`、`locator`（文件+页码/段落）、`quote`（逐字原文）、`claim`（该证据支撑的论断）、`strength`、`field`（比较字段）、`notes`。
- **strength 四级**：`established`（多源一致且方法充分）、`suggestive`（单源或方法有限）、`disputed`（存在冲突）、`not_reported`。
- **措辞按 strength**：established→"表明/证实"；suggestive→"提示/初步表明"；disputed→"存在争议"；not_reported→"未见报告"。**"未报告"≠"不存在"**。
- **引用**：正文 `[S#]` 指向 evidence_table 行；文末附 sources 表（真实来源，含元数据与定位）。
- **禁止**：虚构文献/DOI/数值；跨文档串值（把 A 文数值填进 B 文）；把相关写成因果；把摘要当全文证据。

## 7. 降级策略（诚实标注，禁止编造）

- 仅有摘要/无全文 → 该篇标 `abstract_only`，结论降为 `suggestive` 或 `not_reported`，不补齐正文数据。
- 元数据不全 → 写 `not_reported`；同一指标两源不一致 → 都记录并标 `source_conflict`，不静默择一。
- 缺软件（如 `pdftotext`）→ 记入 `degradations`，用已抽取文本继续，**不伪造解析结果**。
- 材料不足以回答问题 → `status=insufficient_evidence`，在 `uncertainty`/`pending_human` 列缺口，不生成示例填充。
- 缺字段/未知字段 → 保留键并填 `null`/`[]`，记入 `errors`/`degradations`；**绝不编造评测分数或运行结果**。

## 8. 最小调用示例（示意，非正式结果）

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
# ... Agent 按 §4 阶段在 /app/work 与 /app/output 增量填充产物 ...
python3 -B scripts/validate_result.py --output /app/output --case /input/case.json
python3 -B scripts/selftest.py     # 交付前离线自检
```
单篇 PDF 重抽取：`python3 -B scripts/main.py --help` 查看参数；重抽取由 main.py 自动完成。
（若环境无 `python3`，用 `python -B`；脚本调用路径相对本 Skill 根目录。）

示意输出（**示例，非正式结果**）：

```json
{
  "case_id": "example-001",
  "research_question": "…",
  "screening_log": [],
  "evidence_table": [],
  "review_draft": null,
  "status": "partial",
  "status_reason": "Phase 0 骨架已落盘，实质分析待完成",
  "sources": [],
  "uncertainty": [],
  "change_log": [{"ts": "…", "phase": "phase0", "action": "skeleton_written"}]
}
```

## 9. 本版设计取舍（相对基线）

1. **时间预算显式化**：900 秒/64 请求拆成六阶段硬截止与请求上限，写入 `plan.json` 而非散落说明；
2. **检查点先于实质分析**：Phase 0 先写 `plan.json` 与 partial 骨架，阶段推进追加检查点，剩余预算可审计；
3. **预算护栏**：逼近预算上限时强制收敛到自检+定稿，防止超时留下空产物；
4. **契约自检闭环**：`selftest.py` 离线跑通空输入/坏契约/证据缺 quote 三类异常，交付前可验证。
