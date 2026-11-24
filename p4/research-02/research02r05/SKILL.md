---
name: lit-review-contract
description: 围绕固定研究问题，在多篇真实文献上完成可复核的文献综述：筛选→主题归纳→证据组织→带逐条回链的草稿，并保证 result.json 逐字段对齐题目 output_contract、记录 change_log。当输入含多篇论文/题录且任务要求产出综述、系统筛选、趋势分析或选题评估时使用；不用于单篇精读、全文翻译或语言润色。
version: 1.0.0
---

# 文献综述合成 Skill（契约完整版）

## 1. 角色与触发时机

把一批真实文献加工成"可复核"的综述，而不是把摘要连起来。核心交付四步：
筛选 → 主题归纳 → 证据组织 → 草稿生成；每步产物可独立定位，出错时能判断
是筛选、归纳还是引用出了问题。

- 使用：任务含"文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估"，
  且输入含多篇文献（PDF、题录、记录文件）。
- 不使用：单篇精读、全文翻译、语言润色、投稿选刊、事实层面的学术审查。

**本版侧重点：输出契约完整**——result.json 逐字段对齐题目 output_contract，
所有键名、嵌套结构与类型缺省值原样保留，并带 change_log 增量审计。

## 2. 输入契约（运行时固定）

- 原始输入：`/input/case.json`（研究问题、`output_contract`、材料路径）+
  `/input/task.md`；材料可能在 `/input/materials/` 或 `case.json` 给出路径。
- **必须读真实文件**：PDF 经 `pdftotext` 自行解析；不得相信"预抽取答案层"、
  文件名标签或错误的领域标签。
- 工作目录 `/app/work`（中间产物）；最终产物与可编辑文件写 `/app/output/`。
- 预算：900 秒、64 次模型请求、每响应 ≤16384 token（含推理）。

## 3. 输出契约（必须满足）

`/app/output/result.json` 必须存在且为合法 JSON，包含：

1. `case_id`（来自 `case.json`，缺失用文件名）；
2. `output_contract` 里的**全部字段**：键名、嵌套结构、类型缺省值原样保留，
   缺值填 `null`/`[]`/`{}`，不删未知键、不改名；
3. 诚实字段：`status`、`sources`、`uncertainty`、`pending_human`、
   `change_log`、`errors`、`degradations`、`runtime`、`artifacts`。

`status` 取值：`ok`（四步完成且证据可回链）/ `partial`（部分完成，须列缺口）/
`insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。
字段级规范见 `references/contract_spec.md`。

主产物（写 `/app/output/`）：

- `review.md`：综述草稿，每条实质论断带 `[S#]` 标记；
- `screening_log.json`：筛选标准 + 逐篇纳排决定 + 去重记录；
- `evidence_table.jsonl`：证据行（字段见 `references/evidence_schema.md`）；
- `themes.md`：主题与子主题，各自列出支撑文献与边界；
- `conflicts_and_gaps.md`：结论冲突并列呈现 + 有据的研究空白。

## 4. 执行流程（每步输入→输出，共 900 秒）

**Phase 0（0–2 分钟）——先落盘骨架（硬性要求）**
输入：`/input`。执行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`。
输出：合法 `result.json`（`status=partial` 或按材料诚实取值、全部契约字段、
空 sources/uncertainty、初始 change_log）。**前 3 分钟内必须已落盘一版合法
result.json**，此后任何异常都不会留下空产物；之后只做增量改进，不整体重写。

**Phase 1（2–6 分钟）——盘点与筛选**
输入：`work/inventory.json`。去重（SHA-256 内容哈希 + 题名/DOI 近似匹配），
PDF 经 `pdftotext` 抽取（main.py 已自动执行，失败记入 `degradations`）。
输出：`screening_log.json`——纳入/排除标准写在逐篇决定**之前**。

**Phase 2（6–10 分钟）——证据抽取与主题归纳**
输入：抽取文本。逐篇抽取可比较字段（研究对象/方法/样本量/主要结果/结论/
局限/未报告项），写 `evidence_table.jsonl`，每行含 `locator` + 逐字 `quote`。
输出：证据表 + `themes.md`。主题从证据聚类生成（方法、对象、结论分歧），
**不套**背景/方法/挑战/展望的通用框架。

**Phase 3（10–14 分钟）——草稿 + 冲突 + 空白**
输入：证据表。写 `review.md`：每段对应一组证据；冲突结论并列并标 `disputed`；
空白落到具体证据缺口。输出：`review.md`、`conflicts_and_gaps.md`。

**Phase 4（14–15 分钟）——自检与定稿**
执行 `python3 -B scripts/validate_result.py --output /app/output --case /input/case.json --work /app/work`。
修正 FAIL 项，把 `status` 置 `ok`（或如实保持 `partial`/`insufficient_evidence`）。
每个 Phase 结束都向 result.json 追加 change_log 并写盘，禁止最后一次性倾倒。

## 5. 证据与写作规则

- 筛选先于写作；纳入/排除标准含四要素：研究对象、纳入条件、排除条件、去重规则。
- 证据行字段：`evidence_id`、`source_id`、`locator`、`quote`（逐字原文）、
  `claim`、`strength`、`field`、`notes`。
- strength 四级：`established` / `suggestive` / `disputed` / `not_reported`。
- 措辞按 strength：established→"表明/证实"；suggestive→"提示/初步表明"；
  disputed→"存在争议"；not_reported→"未见报告"。**"未报告"≠"不存在"**。
- 引用：正文 `[S#]` 指向 evidence_table 行；文末附 sources 表（真实来源，
  含元数据与定位）。
- 禁止：虚构文献/DOI/数值；跨文档串值；把相关写成因果；把摘要当全文证据。

## 6. 自检清单（对应评分维度）

- [ ] 四步全跑完、产物齐全、结论与研究问题和证据范围一致（任务完成度）
- [ ] 每条实质论断可指回 `source_id`+`locator`+`quote`，引述强度与原文一致（正确性）
- [ ] 覆盖输入文献集的主要主题、方法路线、正反证据与分歧，空白有据（完整性）
- [ ] 文献清单、筛选记录、证据表、引用定位可独立复核（可核验性）
- [ ] result.json 含 `case_id`+全部契约字段+诚实字段，逐字段对齐 output_contract（易用性）
- [ ] change_log 记录 Phase 0 骨架与每次状态变更（审计）
- [ ] 缺全文/元数据不全/文献重复/结论冲突/检索为空/超上下文均有处置（异常处理）
- [ ] 脚本只用标准库、重复运行结果一致（运行稳定性）
- [ ] `python3 -B scripts/selftest.py` 退出码 0（提交前）

## 7. 降级策略（诚实标注，禁止编造）

- 仅有摘要/无全文 → 标 `abstract_only`，结论降为 `suggestive`/`not_reported`。
- 元数据不全 → 写 `not_reported`；同指标两源不一致 → 都记录并标 `source_conflict`。
- 缺软件（如 `pdftotext`）→ 记入 `degradations`，用已抽取文本继续，不伪造解析。
- 材料不足以回答问题 → `status=insufficient_evidence`，在 `uncertainty`/
  `pending_human` 列出缺口，不生成示例填充。
- 缺字段/未知字段 → 保留键并填 `null`/`[]`/`{}`，记入 `errors`/`degradations`；
  **绝不编造评测分数或运行结果**。

## 8. 最小调用示例（示意，非正式结果）

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
# ... Agent 在 /app/work 下完成筛选、证据抽取、草稿（每 Phase 增量写盘）...
python3 -B scripts/validate_result.py --output /app/output --case /input/case.json --work /app/work
python3 -B scripts/selftest.py          # 提交前自检：空输入/坏 JSON/正常输入三态
```

（若环境无 `python3`，用 `python -B`；脚本路径相对本 Skill 根目录。）
