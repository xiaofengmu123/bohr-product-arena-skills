---
name: research-frontier-audit
description: 面向“研究前沿与趋势分析”的可复核综述 Skill。把给定文献材料组织为时间趋势、主题演化、代表性工作、研究空白四类可检查产物，并用 plan.json（冻结分析计划）与 figure_model.json（归一化证据图元模型）两个结构化中间表示固定分析口径，使每条结论经 evidence_ids → doc_id/locator → 逐字 quote 回到原文；显式区分材料事实、合理推断与无法确定，区分“证据排除的空白”与“检索遗漏”，诚实标注证据边界。当任务要求文献综述、研究前沿、趋势分析、主题演化追踪、代表作选取或研究空白识别时使用；不用于一次性问答、纯润色或单篇论文审稿。
version: 1.0.0
---

# 研究前沿与趋势分析（可复核 + 中间表示）Skill

## 1. 角色与何时使用

本 Skill 让 Agent 把“这个方向现在做到哪、往哪走、哪里还空着”做成**可复核综述产物**，而非“检索结果前沿清单”。触发条件：任务要求“文献综述 / 研究前沿 / 趋势分析 / 主题演化 / 选题评估 / 研究空白识别”，或需要交付能回到原文逐条核对的趋势结论。

四条硬约束（对应评分“实际效果”40 分）：
1. 趋势必须由**可核对的年度分布**支撑；禁止把关键词热度当趋势、把检索前 N 条当前沿。
2. 演化必须说明**何时、因何变化**（出现/合并/分裂/衰退、术语漂移、同名异义）；禁止把“换了说法”当新兴主题。
3. 代表作必须**覆盖各阶段且口径可说明**，覆盖长尾方法首创工作；禁止只用总被引排序。
4. 空白必须给出**排除证据**（检索范围 + 纳排规则 + 反例排查）；禁止把“没检索到”当空白。

不适用：一次性脚本、单篇论文审稿、语言润色。严禁生成预置答案、伪造评测分数/白板或编造缺失数据；每个案例独立运行，不跨案例复用结论。

## 2. 输入契约（运行时）

- 原始输入在 `/input/case.json` 与 `/input/task.md`；科研材料（论文 PDF、文献元数据、正文、摘要、检索结果集）在 `/input/`（常见子目录 `materials/`）或由 `case.json` 指定路径。
- **先读实际文件内容**，不依据文件名/领域标签臆测；扫描 `/input/` 全目录发现文件，不假设固定文件名。PDF 由平台 `pdf-text-tool` 或本包 `pdftotext` 解析；解析失败按第 8 节降级，不伪造文本层。
- 文本统一按 `utf-8-sig` 读取，坏编码/BOM/坏 JSON 自动降级（`gbk`/`latin-1`）；单文件 >2MB 跳过并记录，不崩溃。
- 工作目录 `/app/work`（中间产物 manifest.json、materials/、selfcheck.json）；最终产物写到 `/app/output/`，其中 `result.json` 必产。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8、合法 JSON）。若 `/input/case.json` 含 `output_contract`，**原样回填其全部字段**（缺值给 `null`/`[]`，不改名、不删未知键），再合并下列固定字段；无 contract 时仅用下列字段。同时产出可编辑主产物 `report.md`、证据映射 `evidence_map.json`、可复算表 `tables/documents.csv`、`tables/year_distribution.csv`，以及两个**结构化中间表示** `plan.json`、`figure_model.json`（见第 5 节）。

固定字段：
- `case_id`：取 case.json 的 `case_id`/`id`/`challenge_id`，缺失取 task.md 的 `challenge_id`，再无则 `"unknown"`。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实反映完成度。
- `research_question`：`{question, scope, sub_questions[], domain_note}`，问题拆解为可核对子问题。
- `protocol`：`{inclusion_criteria[], exclusion_criteria[], search_scope, date_definition_note}`——纳排规则与年份口径（在线发表/见刊/预印本）。
- `time_trend`：`{unit_note, distribution[]({year,count,doc_ids[]}), windows[], claims[]({claim,support,evidence_ids[]})}`；`support` 取 `reported|not_reported|inferred`。
- `topic_evolution`：`{stage_divisions[]({stage,period,basis,terms[]}), term_mapping[]({canonical,variants[],first_year,last_year}), events[]({type,term,period,cause,evidence_ids[]}), claims[]}`；`type` 取 `emergence|merge|split|decline|terminology_drift`。
- `representative_works`：`{criteria[], stages[]({stage,period,works[]({doc_id,title,year,rationale,kind})})}`；`kind` 取 `origin|method|milestone|benchmark`。
- `research_gaps`：`{gaps[]({id,type,description,exclusion_evidence,evidence_ids[]}), excluded_not_gaps[]({candidate,reason})}`；`type` 取 `open_question|method_failure|unresolved_conflict|coverage_absence`。
- `evidence_map`：每条 `{id(E001起),claim,locator,quote,source_file,status,confidence}`；`status` 取 `reported|not_reported|source_conflict|inferred`，`quote` 为原文逐字引用。
- `conflicts`：冲突项 `{items[], resolution}`；冲突不隐藏、不静默择一。
- `artifacts`：本案例主产物与中间表示的文件路径清单。
- `sources`、`uncertainty[]`、`pending_human[]`、`change_log[]({time,action,reason,before,after})`、`limitations[]`、`errors[]`、`degradations[]`、`runtime`。

## 4. 执行流程（总 900 秒、64 次模型请求、16384 tokens/响应）

每步标注输入/输出与预算。**前 3 分钟内必须先落盘一版 result.json 骨架**（`prepare` 阶段内置），此后只做增量改进，任何中断都不留空白产物。

1. **发现与落骨架（0–180 秒，≤4 次请求）**：`python3 scripts/main.py`，扫描 `/input/`、解析 case.json/task.md、抽取材料文本到 `/app/work/materials/`、回填 output_contract、写 `/app/output/plan.json`、`/app/work/manifest.json` 与 `/app/output/result.json` 骨架。产出：可交付 partial 结果 + 材料清单 + 冻结分析计划。
2. **机械基线（180–360 秒，≤6 次请求）**：`python3 scripts/analyze.py`，确定性抽取：每篇文档的年份/标题/被引、年度分布表、术语频率与首末年份、空白/冲突候选句、代表作候选，写 `/app/output/figure_model.json` 与 `/app/output/evidence_map.json`、`tables/*.csv`、`report.md`，全部标注 `machine=true`。
3. **四类深度分析（360–720 秒，约 40 次请求）**：按 `time_trend → topic_evolution → representative_works → research_gaps` 顺序，逐篇读材料相关段落，把机械候选核验为结论（更新 `support`/`status`/`confidence`，去 `machine` 标记或注明未核验）。每完成一类即重写 result.json 并记 change_log。空白项必须写 `exclusion_evidence`，区分证据排除与检索遗漏。
4. **自检（720–820 秒，≤8 次请求）**：`python3 scripts/selfcheck.py`，校验契约字段、枚举、四类产物齐全、quote 可定位、无伪造分数；修复结构错误，无法定位的 quote 改为精确引用或置 `status=not_reported` 并写入 uncertainty。
5. **收尾（820–900 秒，≤6 次请求）**：定稿 status/uncertainty/pending_human/limitations/change_log，复跑 selfcheck 确认通过；`status=ok` 仅在四类产物齐全且关键 claim 均有证据回链时给出。

预算纪律：前 3 分钟只保证骨架存在；随后按类别增量；最后 2 分钟只校验与修复，不新增大范围检查。

## 5. 中间表示与可复核（本版核心设计）

1. **plan.json（冻结分析计划）**：阶段 1 写一次后**只读不改**。含研究问题拆解、protocol（纳排/检索范围/年份口径）、术语归并规则、时间窗划分规则与预算检查点。任何后续更改必须写 change_log 并说明理由——计划本身成为可审计对象。
2. **figure_model.json（证据图元模型）**：阶段 2 机械生成，把原始材料归一为 `documents[]`（doc_id + source_file + locator + year/year_source + title + citation_count）、`term_index[]`（canonical + variants + first_year/last_year + doc_ids）、`candidates[]`（future_work/limitation/unknown/conflict/gap 候选句 + locator + quote）、`windows[]`。它是原料与结论之间的**唯一桥接**：result.json 每个 claim 的 `evidence_ids[]` → `evidence_map` → `doc_id + locator`，而 figure_model.json 把 `doc_id + locator` 确定性地映射回文件与行号。
3. **复算纪律**：doc_id 按相对路径排序稳定分配；所有机械值带 `machine` 标记与 `source_file`/`locator`；重复运行产生逐字节相同的 figure_model.json、evidence_map.json 与 tables（确定性排序、无随机、无时间戳）；result.json 的 runtime/change_log 为运行痕迹，允许时间戳差异。selfcheck 对非空 quote 做**原文逐字定位**，定位失败即 FAIL。

## 6. 证据回链与结论边界

- 每个趋势判断/演化结论/空白主张的 `claims[]` 以 `evidence_ids[]` 指向 `evidence_map`；`evidence_map` 每条含 locator（形如 `materials/mat_001.txt#L12-L15`）+ 逐字 quote。
- 材料事实、合理推断、无法确定三类显式区分：可直接引用的写 `support=reported`；需归纳的写 `inferred` 并在 rationale/evidence 说明；查不到写 `not_reported`，不补写。
- 冲突证据两方都记录并标 `source_conflict`，在 `conflicts` 里给出分辨率或“未决”。

## 7. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] result.json 合法且含 case_id、output_contract 全部字段与第 3 节固定字段（任务完成度/完整性）。
- [ ] 四类产物均非空且可打开：time_trend.distribution 有年度计数、topic_evolution 有阶段与依据、representative_works 有各阶段作品与口径、research_gaps 有排除证据（实际效果/完整性）。
- [ ] plan.json 已冻结，figure_model.json 覆盖全部文档且 doc_id/locator 可反查（结果可核验性/创新）。
- [ ] 每条非空 quote 为原文逐字引用，selfcheck 无“未定位”硬错误（结果可核验性）。
- [ ] 年份口径写于 protocol.date_definition_note 与 time_trend.unit_note（在线发表/见刊/预印本）（结果正确性）。
- [ ] 代表作不只用被引排序，criteria 含方法首创/里程碑/覆盖阶段的口径（结果正确性）。
- [ ] 空白项均写 exclusion_evidence，`coverage_absence` 与 `open_question` 区分清楚（结果正确性）。
- [ ] 冲突证据均记录于 conflicts，未静默择一（结果完整性）。
- [ ] 无编造 benchmark score、无预置答案；status 与四类产物一致（可靠体验）。
- [ ] 无法确认项标 `not_reported`/`inferred` 或入 `pending_human`，不凭空补结论（结果正确性）。
- [ ] limitations 如实列出缺失材料、未核验类别、解析失败；errors/degradations 有值即说明（异常处理）。
- [ ] change_log 记录骨架落盘与每次修改；重复运行结果一致（可核验性/稳定性）。

## 8. 降级与失败处理

- **检索为空/缺材料**：`status=insufficient_evidence`，`research_gaps` 写“材料不足，无法区分空白与遗漏”，不编造结论。
- **年份缺失**：`distribution` 置空、`unit_note` 写“无出版年份元数据，待人工”，`pending_human` 记录；不猜年份。
- **主题严重歧义/同名异义**：term_mapping 记录全部变体，`uncertainty` 说明归并口径，`confidence=low`。
- **文献量爆炸**：脚本按 2MB/200 文件上限采样并记录 `degradations`；报告写明“样本边界”，不宣称全领域覆盖。
- **证据冲突/权限不足/PDF 不可读**：如实标 `source_conflict`/`unchecked`/`degradations`，写入 limitations，不静默择一、不伪造文本层。
- **脚本失败**：selfcheck 报错时优先手工修复；无法修复则保留已落盘骨架并如实标 status/limitations。任何情况下不伪造软件状态、数据、文献或分数；partial 结果必须标明边界。

## 9. 文件清单

- `SKILL.md`（本文件）：角色、输入/输出契约、执行流程、中间表示、自检、降级。
- `scripts/main.py`：阶段 1 发现与落骨架（骨架 + plan.json + manifest.json）。
- `scripts/analyze.py`：阶段 2 机械基线（figure_model.json + tables + evidence_map + report）。
- `scripts/selfcheck.py`：阶段 4 自检（契约字段/枚举/quote 定位/四类产物/change_log）。
- `references/contract.md`：result.json、plan.json、figure_model.json 的字段字典与枚举。
- `references/checklist.md`：自检清单与评分维度映射。
