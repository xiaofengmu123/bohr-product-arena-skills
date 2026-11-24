---
name: lit-review-evidence-synthesis
description: 围绕固定研究问题，在多篇真实文献上完成可复核的文献综述：先定筛选标准并去重，再做证据抽取与主题归纳，最后生成每句可回链、冲突与空白显式的综述草稿。当输入含多篇论文/题录、任务要求产出综述、系统筛选、趋势分析或选题评估时使用；不用于单篇精读、全文翻译、语言润色或事实层面的学术审查。
version: 2.0.0
---

# 文献综述证据合成 Skill

## 1. 角色与触发时机

本 Skill 指挥 Agent 把一批真实文献加工成**可复核**的综述，而不是把摘要连起来。核心交付四步：筛选 → 主题归纳 → 证据组织 → 草稿生成；每步产物独立落盘，出错时可定位是筛选、归纳还是引用的问题。

- 使用：任务说明含“文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估”，且输入含多篇文献（PDF、题录、记录文件）。
- 不使用：单篇精读、全文翻译、语言润色、投稿选刊、事实层面的学术审查。

本版设计取舍：把题目评分维度**逐条**映射到流程与自检项（见 `references/rubric_map.md`），并用校验器做防伪造的逐字回源检查。

## 2. 输入契约（运行时固定）

- 原始输入：`/input/case.json`（研究问题、`output_contract`、材料路径）+ `/input/task.md`。
- 材料可能在 `/input/materials/` 或 `case.json` 给出路径；**必须读真实文件**，PDF 自行解析，不得相信“预抽取答案层”或文件名标签。
- 工作目录 `/app/work`（中间产物）；最终产物与可编辑文件写 `/app/output/`。
- 预算：**900 秒、64 次模型请求、每响应 ≤16384 token（含推理）**。脚本本身不耗模型请求。
- **前 3 分钟必须先落盘一版合法 `result.json`（status=partial）**，之后只做增量改进；任何异常都不留空产物。

## 3. 输出契约（必须满足）

`/app/output/result.json` 必须存在且为合法 JSON，含：

1. `case_id`（来自 `case.json`，缺失则用文件名）；
2. `output_contract` 里的**全部字段**：原样保留键名与类型，缺值填 `null`/`[]`，不删未知键、不改名；
3. 追加诚实字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`，以及 `errors`、`degradations`、`limitations`、`runtime`、`artifacts`。

`status` 取值：`ok`（四步完成且证据可回链）/ `partial`（部分完成，须列缺口）/ `insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。

主产物（写 `/app/output/`）：

- `review.md`：综述草稿，每句实质论断带 `[S#]` 标记；
- `screening_log.json`：筛选标准（写在决定之前）+ 逐篇纳排决定 + 去重记录；
- `evidence_table.jsonl`：证据行（字段与强度见 `references/evidence_schema.md`）；
- `themes.md`：主题与子主题，各自列出支撑 `source_id` 与边界；
- `conflicts_and_gaps.md`：冲突结论并列 + 有据的研究空白。

## 4. 执行流程（每步输入→输出，总 900 秒）

**Phase 0（0–2 分钟）——确定性脚手架**
执行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`（无 `python3` 则用 `python -B`）。
输出：合法 `result.json`（`status=partial`，全部 contract 字段回填）、`work/inventory.json`、`work/dedup.json`、`work/contract.json`、各主产物空骨架、PDF 抽取文本（`work/extracted/*.txt`）。0 次模型请求。

**Phase 1（2–6 分钟）——盘点与筛选（≤15 请求）**
输入：材料文件。确认去重（DOI/题名/内容哈希）并把纳入/排除标准写在**筛选决定之前**。
输出：`screening_log.json` 的 `screening_criteria`（四要素）+ `decisions`，逐篇决定可回溯。

**Phase 2（6–10 分钟）——证据抽取与主题归纳（≤25 请求）**
输入：抽取文本。逐篇抽取可比较字段（研究对象/方法/样本量/主要结果/结论/局限/未报告项），写 `evidence_table.jsonl`。
输出：证据表 + `themes.md`。主题从证据聚类（方法、对象、结论分歧）生成，**不套**背景/方法/挑战/展望通用框架。

**Phase 3（10–14 分钟）——草稿 + 冲突 + 空白（≤15 请求）**
输入：证据表。写 `review.md`：每段对应一组证据；冲突结论并列标 `disputed`；空白落到具体证据缺口。
输出：`review.md`、`conflicts_and_gaps.md`，并把 contract 字段（`review_draft` 等）回填进 `result.json`。

**Phase 4（14–15 分钟）——自检与定稿（≤8 请求）**
执行 `python3 -B scripts/validate_result.py --output /app/output --case /input/case.json --work /app/work`。修正 `[ERROR]` 后把 `status` 置 `ok`（或如实保持 `partial`/`insufficient_evidence`/`input_error`）。每个 Phase 结束都增量写盘，禁止最后一次性倾倒。

## 5. 评分维度 → 流程 → 自检项映射

详见 `references/rubric_map.md`。要点：实际效果 40 分靠四步产物与回链（C1–C4）；使用增益 25 分靠“先标准、逐条回链、措辞分级”省人工（C5）；创新增益 15 分靠防伪造回源与评分显式映射（C6）；可靠体验 10 分靠确定性脚本与诚实降级（C7）；生态贡献 10 分靠字段规范与可复跑脚本（C8）。

## 6. 证据与写作规则

- **筛选先于写作**：`screening_criteria` 四要素（研究对象、纳入、排除、去重规则）先写；每条决定可回溯。
- **证据行字段**：`evidence_id`、`source_id`、`locator`、`quote`、`claim`、`strength`、`field`、`notes`（详见 `references/evidence_schema.md`）。
- **strength 四级**：`established`（多源一致且方法充分）/ `suggestive`（单源或方法有限）/ `disputed`（存在冲突）/ `not_reported`（未见报告）。
- **措辞按 strength**：established→“表明/证实”；suggestive→“提示/初步表明”；disputed→“存在争议”；not_reported→“未见报告”。**“未报告”≠“不存在”**。
- **引用**：正文 `[S#]` 指向 `evidence_table.jsonl` 行；文末附 sources 表（真实来源，含元数据与 locator）。
- **禁止**：虚构文献/DOI/数值；跨文档串值；把相关写成因果；把摘要当全文证据；改写 quote 使其“看起来”被支持。

## 7. 自检清单（逐条勾选，对应评分维度）

- [ ] C1 四步全跑完、产物齐全、结论与研究问题和证据范围一致（任务完成度 15 分）
- [ ] C2 每条实质论断可指回 `source_id`+`locator`+逐字 `quote`，`validate_result.py` 无 `[ERROR]`（结果正确性 15 分）
- [ ] C3 覆盖主要主题、方法路线、正反证据与分歧，空白有据（结果完整性 5 分）
- [ ] C4 文献清单、筛选记录、证据表、引用定位、change_log 可独立复核（可核验性 5 分）
- [ ] C5 先写标准再写作、逐条回链、措辞分级，减少人工补引用/重排主题/核对文献（使用增益 25 分）
- [ ] C6 评分维度映射真实存在 + 防伪造回源校验已通过（创新增益 15 分）
- [ ] C7 `main.py --help` 退出码 0；空输入/坏 JSON/缺软件不崩溃且如实标注（可靠体验 10 分）
- [ ] C8 `SKILL.md` + `references/` 字段规范齐备，`selftest.py` 可复跑（生态贡献 10 分）

## 8. 降级策略（诚实标注，禁止编造）

- 仅摘要/无全文 → 该篇标 `abstract_only`，结论降为 `suggestive` 或 `not_reported`，不补齐正文数据。
- 元数据不全 → 写 `not_reported`；同一指标两源不一致 → 都记录并标 `source_conflict`，不静默择一。
- 缺软件（如 `pdftotext`）→ 记入 `degradations`，用已抽取文本继续，**不伪造解析结果**。
- 材料不足 → `status=insufficient_evidence`，在 `uncertainty`/`pending_human` 列缺口，不生成示例填充。
- 缺字段/未知字段 → 保留键并填 `null`/`[]`，记入 `errors`/`degradations`；**绝不编造评测分数或运行结果**。
- 三类诚实字段语义：`errors`=I/O 失败；`degradations`=工具缺失；`limitations`=科学层面的证据边界（如单源、样本小、仅摘要）。

## 9. 脚本调用（相对本 Skill 根目录）

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work   # Phase 0 脚手架
python3 -B scripts/main.py --help                                                 # 退出码 0
python3 -B scripts/validate_result.py --output /app/output --case /input/case.json --work /app/work
python3 -B scripts/selftest.py                                                    # 6 项端到端自检
```

单篇 PDF 重抽取：`pdftotext -enc UTF-8 <in.pdf> <out.txt>`。脚本只依赖 Python 标准库；重复运行结果一致（时间戳受 `SKILL_NOW` 控制，便于复现）。

## 10. 边界

- 不做“通用大而全”的 Skill：只解决“多文献 → 可复核综述”这一类任务。
- 没有真实材料时输出“材料不足”，不用示例填充；自检夹具仅在 `selftest.py` 运行时于临时目录生成，不随包分发。
