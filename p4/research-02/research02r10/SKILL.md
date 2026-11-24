---
name: lit-review-synthesis
description: 围绕固定研究问题，在多篇真实文献上完成可复核的文献综述：先定筛选标准，再做主题归纳、证据组织与带逐条回链的综述草稿，显式记录冲突与研究空白。当输入含多篇论文/题录、任务要求产出综述、系统筛选、趋势分析或选题评估时使用；不用于单篇精读、全文翻译或语言润色。
version: 2.0.0
---

# 文献综述合成 Skill

## 1. 角色与触发时机
本 Skill 指挥 Agent 把一批真实文献加工成"可复核"的综述，而不是把摘要连起来。核心交付四步：筛选 → 主题归纳 → 证据组织 → 草稿生成；每步产物可独立定位，出错时能判断是筛选、归纳还是引用出了问题。

- 使用：任务说明含"文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估"，且输入含多篇文献（PDF、题录、记录文件）。
- 不使用：单篇精读、全文翻译、语言润色、投稿选刊、事实层面的学术审查。

## 2. 输入契约（运行时固定）
- 原始输入：`/input/case.json`（研究问题、`output_contract`、材料路径）+ `/input/task.md`。
- 材料可能在 `/input/materials/` 或 `case.json` 里给出路径；**必须读真实文件**，PDF 自行解析，不得相信"预抽取答案层"或文件名标签。
- 工作目录 `/app/work`（中间表示 IR）；最终产物与可编辑文件写 `/app/output/`。
- 预算：**900 秒、64 次模型请求、每响应 ≤16384 token（含推理）**。**前 3 分钟必须已落盘一版合法 result.json**，之后只做增量改进。

## 3. 输出契约（必须满足）
`/app/output/result.json` 必须存在且为合法 JSON，包含：
1. `case_id`（来自 `case.json`，缺失则用文件名）；
2. `output_contract` 里的**全部字段**：原样保留键名与类型，缺值填 `null`/`[]`，不删未知键、不改名；
3. 追加诚实字段：`status`、`artifacts`（主产物路径）、`sources`、`uncertainty`、`pending_human`、`degradations`、`errors`、`change_log`。

`status` 取值：`ok`（四步完成且证据可回链）/ `partial`（部分完成，须列缺口）/ `insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。

主产物（写 `/app/output/`）：
- `review.md`：综述草稿，正文标记 `[E#]` 指向 evidence_table 行；
- `screening_log.json`：筛选标准 + 逐篇纳排决定 + 去重记录；
- `evidence_table.jsonl`：证据行（字段见 §5）；
- `themes.md`：主题与子主题，各自列出支撑文献与边界；
- `conflicts_and_gaps.md`：结论冲突并列呈现 + 有据的研究空白。

## 4. 执行流程（每步输入→输出，共 900 秒）
**Phase 0（0–2 分钟）——先落盘骨架**
执行 `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`。
脚本完成：契约解析、材料扫描与 SHA-256 去重、PDF 经 `pdftotext` 抽取、写 8 个 IR 片段、写 `result.json`（`status=partial/insufficient_evidence`）。此后任何异常都不会留下空产物。

**Phase 1（2–6 分钟）——盘点与筛选**
输入：`/app/work/inventory.json` 与 `extracted/*.txt`。Agent 核验去重、补元数据、写 `screening.json`（纳入/排除标准写在逐篇决定**之前**）。

**Phase 2（6–10 分钟）——证据抽取与主题归纳**
逐篇抽取可比较字段（研究对象/方法/样本量/主要结果/结论/局限/未报告项），追加到 `evidence.jsonl`；写 `themes.json`。主题从证据聚类生成（方法、对象、结论分歧），**不套**背景/方法/挑战/展望通用框架。

**Phase 3（10–14 分钟）——草稿 + 冲突 + 空白**
写 `claims.json`（每句一个 claim，含 `evidence_ids`）与 `conflicts_gaps.json`。冲突结论并列并标 `disputed`；空白落到具体证据缺口（哪些问题无任何文献覆盖）。

**Phase 4（14–15 分钟）——渲染、自检与定稿**
执行 `python3 -B scripts/main.py render --work /app/work --output /app/output`，再执行 `python3 -B scripts/main.py check --work /app/work --output /app/output`。修正后把 `meta.json` 的 `status` 置 `ok`（或如实保持 `partial`/`insufficient_evidence`）。
每个 Phase 结束都运行 `render` 增量写盘，禁止最后一次性倾倒。

## 5. 证据与写作规则
- **筛选先于写作**：纳入/排除标准含四要素——研究对象、纳入条件、排除条件、去重规则；每条筛选决定可回溯。
- **证据行字段**：`evidence_id`、`source_id`、`locator`（文件+页码/段落）、`quote`（逐字原文）、`claim`（该证据支撑的论断）、`strength`、`field`（比较字段）、`notes`。
- **strength 四级**：`established`（多源一致且方法充分）、`suggestive`（单源或方法有限）、`disputed`（存在冲突）、`not_reported`。
- **措辞按 strength**：established→"表明/证实"；suggestive→"提示/初步表明"；disputed→"存在争议"；not_reported→"未见报告"。**"未报告"≠"不存在"**。
- **引用**：`review.md` 的 `[E#]` 由渲染器按 `claims.json` 的 `evidence_ids` 自动追加并做存在性校验，因此不会出现"句子通顺但引用不支持"的脱节；文末附来源与证据索引表。
- **禁止**：虚构文献/DOI/数值；跨文档串值；把相关写成因果；把摘要当全文证据。

## 6. 自检清单（对应评分维度）
- [ ] 四步全跑完、产物齐全、结论与研究问题和证据范围一致（任务完成度）
- [ ] 每条实质论断可指回 `source_id`+`locator`+`quote`，引述强度与原文一致（结果正确性）
- [ ] 覆盖输入文献集的主要主题、方法路线、正反证据与分歧，空白有据（结果完整性）
- [ ] 文献清单、筛选记录、证据表、引用定位可独立复核（结果可核验性）
- [ ] `result.json` 含 `case_id`+全部 contract 字段+`status/sources/uncertainty/change_log`（调用易用性）
- [ ] 缺全文/元数据不全/文献重复/结论冲突/检索为空/超上下文均有处置（异常处理）
- [ ] 脚本只用标准库、`check` 退出码 0（运行稳定性）
- [ ] `python3 -B scripts/selftest.py` 全部 PASS（打包自检）

## 7. 降级策略（诚实标注，禁止编造）
- 仅有摘要/无全文 → 该篇标 `abstract_only`（或 `fulltext_status=not_extracted`），其结论降为 `suggestive` 或 `not_reported`，不补齐正文数据。
- 元数据不全 → 写 `not_reported`；同一指标两源不一致 → 都记录并标 `source_conflict`，不静默择一。
- 缺软件（如 `pdftotext`）→ 记入 `degradations`，用 Agent 已读到的文本继续，**不伪造解析结果**。
- 材料不足以回答问题 → `status=insufficient_evidence`，在 `uncertainty`/`pending_human` 列出缺口，不生成示例填充。
- 缺字段/未知字段 → 保留键并填 `null`/`[]`，记入 `errors`/`degradations`；**绝不编造评测分数或运行结果**。

## 8. 脚本说明
- `scripts/main.py`：薄 CLI（scaffold / render / check / extract）。
- `scripts/core_impl.py`：确定性核心（IR、契约解析、抽取、渲染、校验）。
- `scripts/selftest.py`：临时目录自检（正常/空输入/坏 case.json 三场景）。
- 路径用相对路径；若环境无 `python3`，用 `python -B`。

## 9. 最小调用示例（示意，非正式结果）
```bash
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
# Agent 在 /app/work 下增量填 IR 片段（screening/evidence/themes/claims/conflicts_gaps/meta）……
python3 -B scripts/main.py render --work /app/work --output /app/output
python3 -B scripts/main.py check  --work /app/work --output /app/output
```
单篇 PDF 重抽取：`python3 -B scripts/main.py extract --pdf <in.pdf> --out <out.txt>`。

## 10. 边界
本 Skill 只解决"多篇文献 → 可复核综述"这一类任务；不承担单篇精读、翻译、润色、选刊与学术审查。无真实材料时输出"材料不足"而非示例填充。交付前先在不属于作者的环境跑一遍 `selftest.py`。
