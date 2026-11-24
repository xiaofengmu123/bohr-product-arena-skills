---
name: deterministic-lit-review
description: 围绕固定研究问题，在多篇真实文献上完成可复核、可复现的文献综述：先落盘确定性骨架，再依次做筛选、主题归纳、证据回链与带逐条 [S#] 引用的综述草稿，并用 scripts/selftest.py 逐项自检；显式记录冲突、研究空白与不确定性。当输入含多篇论文/题录，且任务要求产出综述、系统筛选、证据综合、趋势分析或选题评估时使用；不用于单篇精读、全文翻译、语言润色或投稿选刊。
version: 2.0.0
---

# 文献综述合成 Skill（自检与确定性强化版）

## 1. 角色与触发时机
本 Skill 指挥 Agent 把一批真实文献加工成"可复核、可复现"的综述，而不是把摘要连起来。核心交付五步：落盘骨架 → 筛选 → 主题归纳 → 证据组织 → 草稿生成；每步产物独立定位，出错时能判断是筛选、归纳还是引用出了问题。

- 使用：任务说明含"文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估"，且输入含多篇文献（PDF、题录、记录文件）。
- 不使用：单篇精读、全文翻译、语言润色、投稿选刊、事实层面的学术审查。

## 2. 输入契约（运行时固定）
- 原始输入：`/input/case.json`（研究问题、`output_contract`、材料路径）与 `/input/task.md`。
- 材料可能在 `/input/materials/` 或 `case.json` 给出的路径里；**必须读真实文件**，PDF 自行解析，不得相信"预抽取答案层"或文件名标签。
- 工作目录 `/app/work`（中间产物）；最终产物与可编辑文件写 `/app/output/`。
- 预算：900 秒、64 次模型请求、每响应 ≤16384 token（含推理）。**前 3 分钟必须已落盘一版合法 result.json 骨架**，之后只做增量改进。
- 脚本路径相对本 Skill 根目录（平台挂载于 `/skills/weekly-skill`）。若当前目录不是 Skill 根，改用 `/skills/weekly-skill/scripts/...`；环境无 `python3` 时用 `python -B`。

## 3. 输出契约（必须满足）
`/app/output/result.json` 必须存在且为合法 JSON，包含：
1. `case_id`（来自 `case.json`，缺失则用文件名）；
2. `output_contract` 里的**全部字段**：原样保留键名与类型，缺值填 `null`/`[]`，不删未知键、不改名；
3. 追加诚实字段：`status`、`artifacts`、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`。

`status` 取值：`ok`（五步完成且每条实质论断可回链）/ `partial`（部分完成，须在 `uncertainty`/`change_log` 列缺口）/ `insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。

主产物（写 `/app/output/`）：
- `review.md`：综述草稿，每句实质论断带 `[S#]` 标记；
- `screening_log.json`：筛选标准（写在筛选决定**之前**）+ 逐篇纳排决定 + 去重记录；
- `evidence_table.jsonl`：证据行（字段见 §5 与 `references/evidence_schema.md`）；
- `themes.md`：主题与子主题，各自列出支撑文献与边界；
- `conflicts_and_gaps.md`：结论冲突并列呈现 + 有据的研究空白；
- `manifest.json`：各产物 SHA-256，供 `selftest.py` 复核（脚本生成）。

## 4. 执行流程（每步输入→输出，共 900 秒）
**Phase 0（0–3 分钟）——先落盘骨架**
执行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`。
产出：合法 `result.json`（status=partial，含全部 contract 字段与骨架产物）、`manifest.json`、`/app/work/inventory.json`、`/app/work/extracted/*.txt`（PDF 经 `pdftotext` 抽取，失败记入 `degradations`）。此后任何异常都不会留下空产物。

**Phase 1（3–7 分钟）——盘点与筛选**
输入：材料文件。扫描并去重（按 DOI/题名/内容哈希），写筛选标准**先于**逐篇决定。
输出：`screening_log.json`（纳入/排除标准 + 逐篇决定 + 去重记录）。

**Phase 2（7–10 分钟）——证据抽取与主题归纳**
输入：抽取文本。逐篇抽取可比较字段（研究对象/方法/样本量/主要结果/结论/局限/未报告项），写 `evidence_table.jsonl`。
输出：证据表 + `themes.md`。主题从证据聚类生成（方法、对象、结论分歧），**不套**背景/方法/挑战/展望的通用框架。

**Phase 3（10–14 分钟）——草稿 + 冲突 + 空白**
输入：证据表。写 `review.md`：每段对应一组证据；冲突结论并列并标 `disputed`；空白落到具体证据缺口。
输出：`review.md`、`conflicts_and_gaps.md`。

**Phase 4（14–15 分钟）——自检与定稿**
执行 `python3 -B scripts/selftest.py --input /input --output /app/output`。逐项修正 FAIL，然后按证据范围把 `status` 置 `ok`（或如实保持 `partial`/`insufficient_evidence`）。每次修正后重跑 selftest，直到无 FAIL；无法消除的 FAIL 必须写入 `errors`/`pending_human` 并相应降低 status。
每个 Phase 结束都增量写盘，禁止最后一次性倾倒。

## 5. 证据与写作规则
- **筛选先于写作**：纳入/排除标准含四要素——研究对象、纳入条件、排除条件、去重规则；每条筛选决定可回溯。
- **证据行字段**：`evidence_id`、`source_id`、`locator`（文件+页码/段落）、`quote`（逐字原文）、`claim`（该证据支撑的论断）、`strength`、`field`（比较字段）、`notes`。
- **strength 四级**：`established`（多源一致且方法充分）、`suggestive`（单源或方法有限）、`disputed`（存在冲突）、`not_reported`。
- **措辞按 strength**：established→"表明/证实"；suggestive→"提示/初步表明"；disputed→"存在争议"；not_reported→"未见报告"。"未报告"≠"不存在"。
- **引用**：正文 `[S#]` 指向 evidence_table 行；文末附 sources 表（真实来源，含元数据与定位）。
- **禁止**：虚构文献/DOI/数值；跨文档串值（把 A 文数值填进 B 文）；把相关写成因果；把摘要当全文证据。

## 6. 自检（对应评分维度）
运行 `python3 -B scripts/selftest.py --input /input --output /app/output`，逐项 PASS/FAIL。完整清单见 `references/checklist.md`；提交前至少确认以下项：
- [ ] result.json 合法，含 case_id + 全部 contract 字段 + 追加诚实字段（调用易用性 / 异常处理）
- [ ] status 与产物完备度一致，缺全文/缺数据/冲突/检索为空均有标注（异常处理 / 结果完整性）
- [ ] 每条实质论断 `[S#]` 可解析回 evidence_table 行，行含 source_id+locator+quote（结果正确性 / 可核验性）
- [ ] 每条证据行的 source_id 可解析回 sources 或 screening_log（结果可核验性）
- [ ] 筛选标准写在逐篇决定之前，去重可追溯（任务完成度 / 可核验性）
- [ ] strength 用词与原文强度一致，无伪造 DOI/数值（结果正确性）
- [ ] `manifest.json` 哈希与当前产物一致，`--determinism` 两次骨架字节级一致（运行稳定性）

## 7. 降级策略（诚实标注，禁止编造）
- 仅有摘要/无全文 → 该篇标 `abstract_only`，其结论降为 `suggestive` 或 `not_reported`，不补齐正文数据。
- 元数据不全 → 写 `not_reported`；同一指标两源不一致 → 都记录并标 `source_conflict`，不静默择一。
- 缺软件（如 `pdftotext`）→ 记入 `degradations`，用已抽取文本继续，**不伪造解析结果**。
- 材料不足以回答问题 → `status=insufficient_evidence`，在 `uncertainty`/`pending_human` 列出缺口，不生成示例填充。
- 缺字段/未知字段 → 保留键并填 `null`/`[]`，记入 `errors`/`degradations`；**绝不编造评测分数或运行结果**。

## 8. 确定性保证
- 同一 `/input` 内容两次运行，脚本生成的骨架产物字节级一致：`run_id` 由输入内容哈希派生（不用墙钟时间），文件发现与列表均按稳定键排序，JSON 采用 `sort_keys` 规范序列化，仅当字节变化才重写文件。
- Agent 写产物须遵守：sources 按 `source_id` 排序；evidence 行按 `evidence_id` 排序；change_log 用递增 `seq`；正文不嵌入墙钟时间戳。
- 验证：`python3 -B scripts/selftest.py --input /input --output /app/output --determinism` 会把骨架跑两遍并断言 result.json 字节一致。

## 9. 最小调用示例（示意，非正式结果）
```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
# ... Agent 在 /app/work 下完成筛选、证据抽取、草稿 ...
python3 -B scripts/selftest.py --input /input --output /app/output
```
