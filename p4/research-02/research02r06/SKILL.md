---
name: lit-review-synthesis
description: 在固定研究问题上，把多篇真实文献加工成可复核的文献综述：以模型优先的中间表示（plan.json 承载筛选协议与主题模型，claims.json 承载论断-证据图）驱动筛选、主题归纳、证据回链与草稿生成，显式记录冲突与研究空白。当输入含多篇论文/题录、任务要求产出综述、系统筛选、趋势分析或选题评估时使用；不用于单篇精读、全文翻译或语言润色。
version: 2.0.0
---

# 文献综述合成 Skill（模型优先版）

## 1. 角色与触发时机
本 Skill 指挥 Agent 把一批真实文献加工成"可复核、可逆向定位"的综述，而不是把摘要连起来。与常见做法不同，本版先建结构化中间模型，再由模型生成文字：`plan.json` 是筛选协议与主题模型的事实来源，`claims.json` 是"论断→证据"图，`review.md` 只是该模型的可读投影。出错时能判断是筛选、归纳、证据还是引述环节的问题。

- 使用：任务说明含"文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估"，且输入含多篇文献（PDF、题录或记录文件）。
- 不使用：单篇精读、全文翻译、语言润色、投稿选刊、事实层面的学术审查。

## 2. 输入契约（运行时固定）
- 原始输入：`/input/case.json`（研究问题、`output_contract`、材料路径）+ `/input/task.md`。
- 材料可能在 `/input/materials/` 或 `case.json` 中给出路径；**必须读真实文件**，PDF 用 `pdftotext` 自行抽取，不轻信文件名标签或"预抽取答案层"。
- 工作目录 `/app/work`（中间产物与抽取文本）；最终产物与可编辑文件写 `/app/output/`。
- 预算：900 秒、64 次模型请求、每响应 ≤16384 token（含推理）。**前 3 分钟必须已落盘一版合法 result.json**，之后只做增量改进，禁止最后一次性倾倒。

## 3. 输出契约（必须满足）
`/app/output/result.json` 必须存在且为合法 JSON，包含：
1. `case_id`（来自 `case.json`，缺失则用文件名）；
2. `output_contract` 里的**全部字段**：原样保留键名与类型，缺值填 `null`/`[]`，不删未知键、不改名；
3. 追加诚实字段：`status`、`artifacts`、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`。

`status` 取值：`ok`（四步完成且证据可回链）/ `partial`（部分完成，须列缺口）/ `insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。

主产物（写 `/app/output/`）：
- `plan.json`：研究问题、筛选协议（纳入/排除/去重规则，**先于筛选决定**）、主题模型与覆盖缺口；
- `claims.json`：论断图，每条论断带 `claim_id`、强度、支撑/反驳的证据引用；
- `screening_log.json`：筛选标准 + 逐篇纳排决定 + 去重记录；
- `evidence_table.jsonl`：证据行（字段见 §6）；
- `review.md`：综述草稿，每句实质论断带 `[C#]` 标记；
- `themes.md`、`conflicts_and_gaps.md`：主题与冲突/空白的可读投影。

## 4. 中间模型（本版核心，替代"直接写综述"）
- `plan.json` 是唯一权威的**筛选协议与主题模型**：筛选标准在决定之前冻结；主题只从证据字段（对象/方法/结果/分歧）自下而上聚类，不套背景-方法-挑战-展望的通用框架。
- `claims.json` 是唯一权威的**论断-证据图**：每条 claim 必须引用 ≥1 条 evidence 行；`review.md` 中的 `[C#]` 必须能在 claims.json 中找到，反向亦然。
- 任何文字段落不得引入模型之外的实质论断；写段落前先把 claim 写入 claims.json。

## 5. 执行流程（每步输入→输出，共 900 秒）
**Phase 0（0–3 分钟）——落盘骨架（硬性）**
执行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`。
输入：`/input`。输出：合法 `result.json`（`status=partial`、全部 contract 字段、空 sources/uncertainty/change_log）+ 空 `plan.json`/`claims.json`/`screening_log.json`/`evidence_table.jsonl`。此后任何异常都不会留下空产物。

**Phase 1（3–6 分钟）——筛选协议与盘点**
输入：材料文件。在 `plan.json` 写入纳入/排除/去重规则并冻结，然后逐篇决定；去重按 SHA-256/DOI/题名。PDF 经 `pdftotext` 抽取（`main.py` 已尝试，失败记 `degradations`）。
输出：`plan.json`（协议冻结）+ `screening_log.json`（决定在标准之后）。

**Phase 2（6–10 分钟）——证据抽取与主题归纳**
输入：抽取文本。逐篇抽取可比较字段（研究对象/方法/样本量/主要结果/结论/局限/未报告项），写 `evidence_table.jsonl`；据证据聚类生成 `plan.json` 的 theme_model。
输出：`evidence_table.jsonl` + `plan.json`（theme_model）+ `themes.md`。

**Phase 3（10–14 分钟）——论断图与草稿**
输入：证据表。把每条实质论断写成 `claims.json` 的 claim（带强度与证据引用），再由 claim 生成 `review.md`；冲突并列标 `disputed`；空白落到"无任何文献覆盖"的具体证据缺口。
输出：`claims.json`、`review.md`、`conflicts_and_gaps.md`。

**Phase 4（14–15 分钟）——自检与定稿**
执行 `python3 -B scripts/validate.py --output /app/output --case /input/case.json`。修正后把 `status` 置 `ok`（或如实保持 `partial`/`insufficient_evidence`）。每个 Phase 结束都增量写盘。

## 6. 证据与写作规则
- **筛选先于写作**：纳入/排除标准含四要素——研究对象、纳入条件、排除条件、去重规则；每条筛选决定可回溯。
- **证据行字段**：`evidence_id`、`source_id`、`locator`（文件+页码/段落）、`quote`（逐字原文）、`claim_ids`、`field`、`strength`、`notes`。
- **strength 四级**：`established`（多源一致且方法充分）、`suggestive`（单源或方法有限）、`disputed`（存在冲突）、`not_reported`。
- **措辞按 strength**：established→"表明/证实"；suggestive→"提示/初步表明"；disputed→"存在争议"；not_reported→"未见报告"。**"未报告"≠"不存在"**。
- **引用**：正文 `[C#]` 指向 claims.json，claim 的 evidence_refs 指向 evidence_table 行；文末附 sources 表（真实来源，含元数据与定位）。
- **禁止**：虚构文献/DOI/数值；跨文档串值；把相关写成因果；把摘要当全文证据。

## 7. 自检清单（对应评分维度）
- [ ] 四步全跑完、产物齐全、结论与研究问题和证据范围一致（任务完成度 15 分）
- [ ] 每条 claim 可指回 evidence 行的 source_id+locator+quote，强度与原文一致（结果正确性 15 分）
- [ ] 覆盖输入文献集的主要主题、方法路线、正反证据与分歧，空白有据（结果完整性 5 分）
- [ ] 文献清单、筛选记录、证据表、claim 图、引用定位可独立复核（可核验性 5 分）
- [ ] result.json 含 case_id+全部 contract 字段+status/sources/uncertainty/change_log（易用性）
- [ ] 缺全文/元数据不全/重复/冲突/检索为空/超上下文均有处置（异常处理）
- [ ] 脚本只用标准库、重复运行字节一致（稳定性）
- [ ] 每 Phase 落盘；前 3 分钟有合法 result.json（时间契约）

## 8. 降级策略（诚实标注，禁止编造）
- 仅有摘要/无全文 → 该篇标 `abstract_only`，结论降为 `suggestive`/`not_reported`，不补齐正文。
- 元数据不全 → 写 `not_reported`；两源不一致 → 都记录并标 `source_conflict`。
- 缺 `pdftotext` → 记入 `degradations`，用已抽取文本继续，**不伪造解析结果**。
- 材料不足以回答问题 → `status=insufficient_evidence`，`uncertainty`/`pending_human` 列出缺口。
- 缺字段/未知字段 → 保留键填 `null`/`[]`，记入 `errors`/`degradations`；**绝不编造评测分数或运行结果**。

## 9. 最小调用示例（示意，非正式结果）
```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
# Agent 在 /app/work 下完成筛选、证据抽取、claims 图与草稿……
python3 -B scripts/validate.py --output /app/output --case /input/case.json
python3 -B scripts/selftest.py   # 空目录/坏 JSON/演示输入三种自检
```
（若环境无 `python3`，用 `python -B`；脚本调用路径相对本 Skill 根目录。PDF 抽取已由 main.py 完成。）

详细字段定义见 `references/evidence_schema.md`，评分维度对照与降级表见 `references/checklist.md`。
