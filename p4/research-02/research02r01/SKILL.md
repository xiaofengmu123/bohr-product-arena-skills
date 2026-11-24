---
name: evidence-traced-review
description: 围绕固定研究问题，在多篇真实文献上完成可复核的文献综述：先写明筛选标准并记录逐篇纳排决定，再做主题归纳、证据组织与逐句回链的综述草稿，显式列出结论冲突与研究空白。每条实质论断都必须带 locator 与逐字 quote 的证据映射（evidence_map）。当输入含多篇论文/题录、任务要求综述/系统筛选/趋势分析/选题评估时使用；不用于单篇精读、全文翻译、语言润色。
version: 1.0.0
---

# 证据溯源式文献综述 Skill

## 1. 角色与触发时机
本 Skill 把一批真实文献加工成"可复核"的综述，核心四步：筛选 → 主题归纳 → 证据组织 → 草稿生成。关键差异：**每个结论都落在 `evidence_map` 里，逐条携带 `locator`（文件+页/段）+ 逐字 `quote`**，任何实质论断都能反向定位到原文；出错时能定位是"筛选、归纳还是引用"哪一步。

- 使用：任务说明含"文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估"，且输入含多篇文献（PDF、题录、记录文件）。
- 不使用：单篇精读、全文翻译、语言润色、投稿选刊、事实层面的学术审查。

## 2. 输入契约（运行时固定）
- 原始输入：`/input/case.json`（研究问题、`output_contract`、材料路径）+ `/input/task.md`。
- 材料可能在 `/input/materials/` 或 `case.json` 里给出路径；**必须读真实文件**，PDF 用 `pdftotext` 解析；不信任文件名标签或"预抽取答案层"。
- 工作目录 `/app/work`（中间产物）；最终产物与可编辑文件写 `/app/output/`。
- 预算：900 秒、64 次模型请求、每响应 ≤16384 token（含推理）。**前 3 分钟必须已落盘一版合法 `result.json`**，之后只做增量改进。

## 3. 输出契约（必须满足）
`/app/output/result.json` 必须存在且为合法 JSON，包含：
1. `case_id`（来自 `case.json`，缺失则用文件名）；
2. `output_contract` 里的**全部字段**：原样保留键名与类型，缺值填 `null`/`[]`，不删未知键、不改名；
3. 追加诚实字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`，以及 `artifacts`、`errors`、`degradations`、`runtime`。

`status` 取值：`ok`（四步完成且结论均可回链）/ `partial`（部分完成，须列缺口）/ `insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。

主产物（写 `/app/output/`）：
- `review.md`：综述草稿，每句实质论断带 `[S#]` 标记；
- `screening_log.json`：筛选标准（写在纳排决定**之前**）+ 逐篇纳排决定 + 去重记录；
- `evidence_table.jsonl`：逐条证据行（字段见 §5）；
- `evidence_map.json`：结论级证据映射（`[S#]` → locator + quote，完整 schema 见 `references/evidence_contract.md`）；
- `themes.md`：主题与子主题，各自列出支撑文献与边界；
- `conflicts_and_gaps.md`：冲突结论并列 + 有据的研究空白。

## 4. 执行流程（每步输入→输出，共 900 秒 / 64 请求）
**Phase 0（0–3 分钟）——先落盘骨架**
输入：`/input`。执行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`。
输出：合法 `result.json`（`status=partial`、全部 contract 字段、空 `sources/uncertainty/pending_human/change_log`）、`work/inventory.json`、`work/extracted/*.txt`、筛选与证据空骨架。此后任何异常都不会留下空产物；本步只花 1 次请求，其余预算留给分析。

**Phase 1（3–7 分钟）——盘点与筛选**
输入：材料文件。扫描去重（按 DOI/题名/内容哈希）；仅有摘要无全文的标 `abstract_only`。
输出：`screening_log.json`。纳入/排除标准含四要素（研究对象、纳入条件、排除条件、去重规则），必须写在逐条纳排决定之前。

**Phase 2（7–11 分钟）——证据抽取与主题归纳**
输入：抽取文本。逐篇抽取可比较字段（研究对象/方法/样本量/主要结果/结论/局限/未报告项），写 `evidence_table.jsonl`；再把证据按结论聚合，写 `evidence_map.json`。
输出：证据表 + 证据映射 + `themes.md`。主题从证据聚类（方法、对象、结论分歧）生长，不套背景/方法/挑战/展望的通用框架。

**Phase 3（11–14 分钟）——草稿 + 冲突 + 空白**
输入：`evidence_map.json`。写 `review.md`：每段对应一组结论；冲突并列并标 `disputed`；空白落到具体证据缺口（哪些问题无任何文献覆盖）。
输出：`review.md`、`conflicts_and_gaps.md`。

**Phase 4（14–15 分钟）——自检与定稿**
执行 `python3 -B scripts/validate_result.py --output /app/output --case /input/case.json`。修正问题后把 `status` 置 `ok`（或如实保持 `partial`/`insufficient_evidence`）。
每个 Phase 结束都增量写盘，禁止最后一次性倾倒；同一文件不重复全量重读；剩余请求留给修复与复核。

## 5. 证据与写作规则（溯源强化）
- **筛选先于写作**：纳入/排除标准四要素——研究对象、纳入条件、排除条件、去重规则；每条筛选决定可回溯。
- **证据行字段**：`evidence_id`、`source_id`、`locator`（`文件#页码:段落`）、`quote`（逐字原文）、`claim`（该证据支撑的论断）、`strength`、`field`（比较字段）、`notes`。
- **结论映射字段**：`claim_id`（即 `[S#]`）、`text`、`claim_type`（`fact|inference|disputed|gap`）、`strength`、`evidence_ids`、`quotes`（每条含 `source_id`+`locator`+`quote`）、`note`（综合/冲突/空白的依据）。
- **strength 四级**：`established`（多源一致且方法充分）、`suggestive`（单源或方法有限）、`disputed`（存在冲突）、`gap`（无文献覆盖）。措辞对应：established→"表明/证实"；suggestive→"提示/初步表明"；disputed→"存在争议"；gap→"未见报告"。**"未报告"≠"不存在"**。
- **硬约束**：`review.md` 里每个 `[S#]` 必须在 `evidence_map` 有对应 claim，且该 claim 至少一条 quote 的 `locator` 与 `quote` 均非空；quote 必须逐字取自原文，不得改写。
- **禁止**：虚构文献/DOI/数值；跨文档串值；把相关写成因果；把摘要当全文证据。
- **主题归纳方法**：先按比较字段（方法路线/研究对象/结局）对 evidence_table 聚类，主题名取该簇的主导证据轴；每个主题写"支撑文献 + 边界 + 与相邻主题的区分"；重叠时并入证据更集中的一方；被排除文献若反复出现独立证据轴，则视为遗漏并补建主题。
- **去重次序**：DOI（从 bib/ris/jsonl 记录解析）→ 题名归一化（去标点、小写、去空格）→ 内容 SHA-256；同内容不同语言/版本按内容哈希归并并注明。
- **记录文件语义**：当案例以题录/记录（bib/ris/jsonl，含摘要）为证据主体时，每条记录视为一个 source，locator 用 `#entry:<键>`；无全文即 `abstract_only`，其结论至多 `suggestive`；若记录与 PDF 并存，记录用于元数据与去重，PDF 用于逐字 quote。

## 6. 自检清单（逐条可勾选，对应评分维度）
- [ ] 四步全跑完、产物齐全、结论与研究问题和证据范围一致（任务完成度 15 分）
- [ ] 每条实质论断指回 `source_id`+`locator`+`quote`，引述强度与原文一致（结果正确性 15 分）
- [ ] 覆盖输入文献集的主要主题、方法路线、正反证据与结论分歧，空白有据（结果完整性 5 分）
- [ ] 文献清单、筛选记录、证据表、结论映射、引用定位可独立复核（结果可核验性 5 分）
- [ ] `result.json` 含 `case_id`+全部 contract 字段+`status/sources/uncertainty/pending_human/change_log`（调用易用性）
- [ ] 缺全文/元数据不全/文献重复/结论冲突/检索为空/超上下文均有处置（异常处理）
- [ ] 脚本只用标准库、重复运行结果一致（运行稳定性）

## 7. 降级策略（诚实标注，禁止编造）
- 仅有摘要/无全文 → 该篇标 `abstract_only`，其结论降为 `suggestive` 或 `gap`，不补齐正文数据。
- 元数据不全 → 写 `not_reported`；同一指标两源不一致 → 都记录并标 `source_conflict`，不静默择一。
- 缺软件（如 `pdftotext`）→ 记入 `degradations`，用已抽取文本继续，不伪造解析结果。
- 材料不足以回答问题 → `status=insufficient_evidence`，在 `uncertainty`/`pending_human` 列出缺口，不生成示例填充。
- 缺字段/未知字段 → 保留键并填 `null`/`[]`，记入 `errors`/`degradations`；绝不编造评测分数或运行结果。

## 8. 最小调用示例（示意，非正式结果）
```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
# Agent 在 /app/work 下完成筛选、证据抽取、证据映射、草稿……
python3 -B scripts/validate_result.py --output /app/output --case /input/case.json
```
本地自检：`python3 -B scripts/selftest.py`（覆盖空输入、坏 JSON、最小契约三场景）。
（若无 `python3` 用 `python -B`；脚本路径相对本 Skill 根目录；重跑 `main.py` 会幂等地重新抽取 PDF。）
