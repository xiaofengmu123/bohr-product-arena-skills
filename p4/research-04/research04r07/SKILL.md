---
name: research-frontier-trend
description: 面向“研究前沿与趋势分析”的可复核综述 Skill。把给定文献材料组织为时间趋势、主题演化、代表性工作与研究空白四类可检查产物；每条判断回链原文（locator + 逐字 quote），显式区分材料事实、合理推断与无法确定，区分“证据排除的空白”与“检索遗漏”，并对空输入、坏 JSON、缺参数、坏编码、超大文件、PDF 不可读等异常优雅降级。当任务要求文献综述、趋势分析、主题演化追踪、代表性工作选取或研究空白识别时使用；不用于一次性问答、纯润色或单篇论文审稿。
version: 1.1.0
---

# 研究前沿与趋势分析 Skill（鲁棒性变体）

## 1. 角色与何时使用
把“这个方向现在做到哪、往哪走、哪里还空着”变成**可复核的综述产物**，而非一份“检索结果前沿清单”。触发条件：任务要求“文献综述 / 研究前沿 / 趋势分析 / 主题演化 / 选题评估 / 研究空白识别”，或需要交付能逐条回链原文的趋势结论。

四条硬约束（对应评分“实际效果”）：
1. 趋势必须由**可核对的年度分布**支撑，禁止把关键词热度当趋势、把检索前 N 条当前沿。
2. 演化必须说明**何时、因何变化**（出现/合并/分裂/衰退、术语漂移、同名异义）。
3. 代表作必须**覆盖各阶段且口径可说明**，禁止只用总被引排序，须覆盖长尾方法首创工作。
4. 空白必须给出**排除证据**（检索式 + 纳排规则 + 反例排查），禁止把“没检索到”当空白。

本变体额外强调**鲁棒性**（对应“可靠体验”）：任何异常都降级为可读的 partial 结果，不崩溃、不编造。

## 2. 输入契约（运行时）
- 原始输入在 `/input/case.json` 与 `/input/task.md`；科研材料（PDF、文献元数据、正文、摘要、检索结果集）放在 `/input/`（常见子目录 `materials/`），或由 `case.json` 指定路径。
- **先读实际文件内容**，不依据文件名/领域标签臆测；用脚本扫描 `/input/` 全目录发现文件，不假设固定文件名。PDF 由平台 `pdf-text-tool` 或本包 `pdftotext` 解析；解析失败按第 7 节降级，不伪造文本层。
- 文本统一按 `utf-8-sig → utf-8 → gbk → latin-1` 顺序解码，坏编码/BOM/坏 JSON 自动降级；单文本文件 >2MB 跳过并记录；材料 >200 文件按排序确定性采样并记录。
- 工作目录 `/app/work`（中间产物）；最终产物写到 `/app/output/`，其中 `result.json` 必产。

## 3. 输出契约
必须产出 `/app/output/result.json`（UTF-8、合法 JSON）。若 `/input/case.json` 含 `output_contract`，**原样回填其全部字段**（缺值给 `null`/`[]`，不改名、不删未知键），再合并固定字段；无 contract 时仅用固定字段。`case_id` 与 `status` 始终由本 Skill 诚实计算，不被 contract 覆盖。同时产出可编辑主产物 `report.md`、证据映射 `evidence_map.json` 与可复算表 `tables/documents.csv`、`tables/year_distribution.csv`。

固定字段：`case_id`、`status`（`ok | partial | insufficient_evidence | input_error`）、`research_question{question,scope,sub_questions,domain_note}`、`protocol{inclusion_criteria,exclusion_criteria,search_scope,date_definition_note}`、`time_trend{unit_note,distribution[]{year,count,doc_ids},windows,claims[]{claim,support,evidence_ids}}`、`topic_evolution{stage_divisions,term_mapping{canonical,variants,first_year,last_year},events,claims}`、`representative_works{criteria,stages[]{stage,period,works[]{doc_id,title,year,rationale,kind}}}`、`research_gaps{gaps[]{id,type,description,exclusion_evidence,evidence_ids},excluded_not_gaps}`、`evidence_map[]{id,claim,locator,quote,source_file,status,confidence}`、`conflicts{items,resolution}`、`sources`、`uncertainty[]`、`pending_human[]`、`change_log[]{time,action,reason,before,after}`、`limitations[]`、`errors[]`、`degradations[]`、`runtime`、`artifacts`。枚举值：`support/status` 取 `reported|not_reported|inferred`（evidence_map 另含 `source_conflict`）；`kind` 取 `origin|method|milestone|benchmark`；`gap.type` 取 `open_question|method_failure|unresolved_conflict|coverage_absence`。

## 4. 执行流程（总 900 秒、64 次模型请求、16384 tokens/响应）
每步标注输入/输出与预算。**前 3 分钟内必须先落盘一版 result.json 骨架**，此后只做增量改进，任何中断都不留空白产物。

1. **落骨架（0–180 秒，≤4 请求）**：`python3 scripts/main.py`（缺参数可用默认值，未知参数仅告警）。扫描 `/input/`、解析 case.json/task.md、回填 output_contract、抽取材料文本、写 `/app/output/result.json` 骨架与 `/app/work/manifest.json`。产出：可交付 partial 结果 + 材料清单。
2. **机械基线（180–360 秒，≤6 请求）**：同一脚本确定性抽取年份/标题/术语/空白候选句/冲突候选句/代表作候选，写 `report.md`、`evidence_map.json`、`tables/*.csv`，全部候选标 `machine=true`。
3. **四类深度分析（360–720 秒，约 40 请求）**：按 `time_trend → topic_evolution → representative_works → research_gaps` 顺序逐篇读材料相关段落，把机械候选核验为结论（`support`/`status`/`confidence` 逐条更新，去 `machine` 标记或注明未核验）。每完成一类即重写 result.json 并记 change_log。空白项必须写 `exclusion_evidence`。
4. **自检（720–820 秒，≤8 请求）**：`python3 scripts/selftest.py --result /app/output/result.json`，校验契约字段、枚举、四类产物齐全、quote 可定位；修复结构错误，无法定位的 quote 改为精确引用或置 `status=not_reported` 并写入 uncertainty。
5. **收尾（820–900 秒，≤6 请求）**：定稿 status/uncertainty/pending_human/limitations/change_log，复跑自检确认通过；`status=ok` 仅在四类产物齐全且关键 claim 均有证据回链时给出。

预算纪律：前 3 分钟只保证骨架存在；随后按类别增量；最后 2 分钟只校验与修复。

## 5. 证据回链与结论边界
- 每个趋势/演化/空白主张的 `claims[]` 条目以 `evidence_ids[]` 指向 `evidence_map`；每条含 locator（形如 `materials/mat_001.txt#L12-L15`）+ 逐字 quote。
- 三类显式区分：直接可引用的写 `support=reported`；需归纳的写 `inferred` 并说明依据；查不到写 `not_reported`，不补写。
- 冲突证据两方都记录并标 `source_conflict`，在 `conflicts` 里给分辨率或“未决”，不静默择一。

## 6. 自检清单（提交前逐条勾选，对应评分维度）
- [ ] result.json 合法且含 case_id、output_contract 全部字段与第 3 节固定字段（完成度/完整性）。
- [ ] 四类产物非空可打开：distribution 有年度计数、topic_evolution 有阶段与依据、representative_works 有各阶段作品与口径、research_gaps 有排除证据（实际效果）。
- [ ] 每条非空 quote 为原文逐字引用，selftest 无“未定位”硬错误（可核验性）。
- [ ] 年份口径写于 `protocol.date_definition_note` 与 `time_trend.unit_note`（在线发表/见刊/预印本）（正确性）。
- [ ] 代表作不只用被引排序，criteria 含方法首创/里程碑/覆盖阶段（正确性）。
- [ ] 空白项均写 exclusion_evidence，`coverage_absence` 与 `open_question` 区分清楚（正确性）。
- [ ] 冲突证据均记录于 conflicts，未静默择一（完整性）。
- [ ] 无编造 benchmark score、无预置答案；status 与四类产物一致（可靠体验）。
- [ ] 空输入/坏 JSON/缺参数/超大文件四类异常均退出码 0 且落盘合法 result.json（鲁棒性）。
- [ ] 无法确认项标 `not_reported`/`inferred` 或入 `pending_human`（正确性）。
- [ ] limitations 如实列出缺失材料、未核验类别、解析失败；errors/degradations 有值即说明（异常处理）。
- [ ] change_log 记录骨架落盘与每次修改；重复运行结果一致（可核验性/稳定性）。

## 7. 降级与失败处理（鲁棒性矩阵）
- **空输入/缺材料**：`status=insufficient_evidence`，四类产物留空，limitations 写“材料不足，无法区分空白与遗漏”，不编造结论。
- **坏 JSON / 坏编码**：捕获并写入 `errors`/`degradations`，继续处理其余材料；case.json 不可解析且无回退 id 时 `status=input_error`。
- **缺参数/未知参数**：`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`；未知参数忽略并告警，不崩溃。
- **超大文件/文献爆炸**：>2MB 跳过、>200 文件确定性采样，均记 `degradations`；报告写明“样本边界”。
- **PDF 不可读 / 缺 pdftotext**：标 `degradations`，文本置空，不伪造文本层，不静默跳过。
- **写盘失败**：原子写（tmp+replace）失败时降级到 work 目录最小合法产物，进程不崩溃。
- **证据冲突**：两方都记，标 `source_conflict`；无法归并的主题写 `uncertainty`，`confidence=low`。
- 任何情况下不伪造软件状态、数据、文献或分数；partial 结果必须标明边界。脚本失败时读 `/app/work/selfcheck.json` 定位 FAIL 项，手工修复后复跑 `scripts/selftest.py`。

## 8. 本版设计取舍（鲁棒性变体）
与常见“先分析后写结果”的流程不同，本包把**先落盘、后增量**作为硬约束：`scripts/main.py` 第一步就写合法 result.json 骨架，后续阶段只覆盖字段。三个脚本只用标准库、路径全部相对、遍历全部排序（重复运行结果一致）。`scripts/selftest.py --smoke` 用合成输入（空目录、坏 JSON、超大文件、混合编码、缺材料）自证优雅降级——这是本变体相对参考包的实质差异，不声称通用性、不写预制答案。
