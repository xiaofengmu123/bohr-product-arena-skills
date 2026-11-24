---
name: research-frontier-evidence
description: 面向“研究前沿与趋势分析”的可复核综述 Skill。把给定文献组织为时间趋势、主题演化、代表性工作与研究空白四类可检查产物；每个结论必须回链原文定位（locator）与逐字引用（quote），并以 evidence_map 的双向索引（结论↔证据，含 quote_hash/span/source_kind/retrieval_note）固化，显式区分材料事实、合理推断与无法确定。当任务要求文献综述、趋势分析、主题演化追踪、代表作选取或研究空白识别时使用；不用于一次性问答、纯润色或单篇论文审稿。
version: 2.0.0
---

# 研究前沿与趋势分析 Skill（证据溯源强化版）

## 1. 角色与何时使用
本 Skill 让 Agent 把“这个方向现在做到哪、往哪走、哪里还空着”变成**可复核的综述产物**，而不是“检索结果前沿清单”。触发条件：任务要求文献综述 / 研究前沿 / 趋势分析 / 主题演化 / 选题评估 / 研究空白识别，或需交付能回到原文逐条核对的趋势结论。

四条硬约束（对应评分“实际效果”）：① 趋势必须由**可核对的年度分布**支撑，禁止把关键词热度当趋势；② 演化必须说明**何时、因何变化**（出现/合并/分裂/衰退、术语漂移、同名异义）；③ 代表作必须**覆盖各阶段且口径可说明**，禁止只按总被引排序，须覆盖长尾方法首创工作；④ 空白必须给出**排除证据**（检索范围 + 纳排规则 + 反例排查），禁止把“没检索到”当空白。

不适用：一次性脚本、单篇论文审稿、纯润色。严禁生成预置答案、伪造评测分数/白板或编造缺失数据；每个案例独立运行，不跨案例复用结论。

## 2. 输入契约（运行时）
- 原始输入在 `/input/case.json` 与 `/input/task.md`；科研材料（论文 PDF、元数据、摘要、检索结果集）由平台放在 `/input/`（常见 `materials/` 子目录）或由 `case.json` 指定路径。
- **先读实际文件内容**，不依据文件名/领域标签臆测；扫描 `/input/` 全目录发现文件，不假设固定文件名。`case.json`/`task.md`/`input.json` 是控制文件，永不进入文献语料。PDF 由平台 `pdf-text-tool` 或本包 `pdftotext`（若存在）解析；解析失败按第 7 节降级，不伪造文本层。
- 文本统一按 `utf-8-sig` 读取，坏编码/BOM/坏 JSON 自动降级（`gbk`/`latin-1`）；单文件 >2MB 跳过、单目录 >200 文件截断并记录，不崩溃。
- 工作目录 `/app/work`（中间产物）；最终产物写到 `/app/output/`，其中 `result.json` 必产。

## 3. 输出契约
必须产出 `/app/output/result.json`（UTF-8、合法 JSON）。若 `/input/case.json` 含 `output_contract`，**原样回填其全部字段到 result.json 顶层**（缺值给 `null`/`[]`，不改名、不删未知键），再合并下列固定字段（同名字段以固定字段为准）；无 contract 时仅用固定字段。同时产出可编辑主产物 `report.md`、证据映射 `evidence_map.json` 与可复算表 `tables/documents.csv`、`tables/year_distribution.csv`、`tables/term_evolution.csv`、`tables/evidence.csv`。

固定字段：`case_id`、`status`（`ok|partial|insufficient_evidence|input_error`）、`research_question{question,scope,sub_questions[],domain_note}`、`protocol{inclusion_criteria[],exclusion_criteria[],search_scope,date_definition_note,dedup_rules[],screening_steps[]}`、`time_trend{unit_note,year_field_note,distribution[]({year,count,doc_ids[]}),windows[],turning_points[],claims[],machine_candidates[]}`、`topic_evolution{stage_divisions[]({stage,period,basis,terms[]}),term_mapping[]({canonical,variants[],first_year,last_year}),events[]({type,term,period,cause,evidence_ids[]}),claims[]}`、`representative_works{criteria[],stages[]({stage,period,works[]({doc_id,title,year,rationale,kind,evidence_ids[]})}),claims[]}`、`research_gaps{gaps[]({id,type,description,exclusion_evidence,evidence_ids[]}),excluded_not_gaps[],claims[]}`、`conflicts{items[],resolution_note}`、`evidence_map`、`sources`、`uncertainty[]`、`pending_human[]`、`change_log[]`、`limitations[]`、`errors[]`、`degradations[]`、`runtime`、`artifacts`。

事件 `type`：`emergence|merge|split|decline|terminology_drift`；空白 `type`：`open_question|method_failure|unresolved_conflict|coverage_absence`；代表作 `kind`：`origin|method|milestone|benchmark`。每条 claim 统一为 `{id,claim,support,evidence_ids[],confidence,rationale?}`，`support`/`confidence` 取 `reported|not_reported|inferred` / `high|medium|low`。

## 4. evidence_map：证据溯源（本版变体）
每条证据 `{id(E001起),doc_id,claim,claim_id,locator,span{start_line,end_line},quote,quote_hash,source_file,source_kind,status,confidence,kind,year,topic_tags[],used_by_claims[],retrieval_note}`。`status` 取 `reported|not_reported|source_conflict|inferred`，`kind` 取 `fact|inference|unknown|conflict`，`quote` 为原文逐字引用，`locator` 形如 `materials/mat_001.txt#L12-L15`。`evidence_map.index` 提供 `by_claim/by_doc/by_status` 反向索引：**结论→证据→原文行**与**原文行→证据→结论**双向可查，不必重开全库。约定：claim 的 `evidence_ids` 必须指向现有 evidence 条目；每条 evidence 的 `used_by_claims` 反向回指。冲突证据两方都记录并标 `source_conflict`。

## 5. 执行流程（总 900 秒、64 次模型请求、16384 tokens/响应）
前 3 分钟必须落盘一版 partial 骨架，此后只做增量改进，任何中断都不留空白产物。脚本路径用相对路径，从包根运行：`python3 -B scripts/main.py --input /input --output /app/output --work /app/work`。

1. **落骨架（0–180 秒，≤4 次请求）**：运行 `scripts/main.py`（入口→`core_impl.run_pipeline`）：扫描 `/input/`、解析 case.json/task.md、回填 output_contract、抽取材料文本到 `/app/work/materials/`、写 `/app/work/manifest.json` 与 `/app/output/result.json` 骨架。产出：可交付 partial 结果 + 材料清单。
2. **机械基线（180–360 秒，≤6 次请求）**：同一脚本确定性抽取：每篇文档年份/标题/被引、年度分布、术语候选、空白候选句（future work/limitation/unknown）、冲突候选句（however/in contrast/versus）、代表作候选。写入 result.json 的 `machine_candidates[]`，全部标 `machine=true`。
3. **四类深度分析（360–720 秒，约 40 次请求）**：按 `time_trend → topic_evolution → representative_works → research_gaps` 顺序，逐篇读材料相关段落，把机械候选核验为结论。每完成一类，把增量写成 `/app/work/patches/<类别>.json`（只含要改的字段与带 `id` 的 claims/items），再运行一次 `scripts/main.py` 合并并复算索引；`deep_merge` 按 `id` 更新/追加，不会覆盖他类。空白项必须写 `exclusion_evidence`（检索式 + 排除条件）。
4. **自检（720–820 秒，≤8 次请求）**：`python3 -B scripts/main.py` 每次收尾都产出 `/app/work/selfcheck.json`；校验契约字段、枚举、四类产物齐全、quote 可定位、无伪造分数。修复结构错误，无法定位的 quote 改为精确引用或置 `status=not_reported` 并写入 uncertainty。
5. **收尾（820–900 秒，≤6 次请求）**：在 patch 中定稿 `status`（用 `ok` 仅在四类产物齐全且关键 claim 均有证据回链、无未核验 machine 候选时）、`uncertainty/pending_human/limitations/change_log`，最后复跑 `scripts/main.py` 并确认 `selfcheck.ok=true`。

预算纪律：前 3 分钟只保证骨架存在；随后按类别增量；最后 2 分钟只校验与修复，不新增大范围检查。

## 6. 证据回链与结论边界
- 每个趋势判断/演化结论/空白主张的 `claims[]` 条目以 `evidence_ids[]` 指向 `evidence_map.items`；每条 evidence 含 locator + 逐字 quote。直接可引用的写 `support=reported`；需归纳的写 `inferred` 并在 `rationale` 说明归纳依据；查不到写 `not_reported`，不补写。
- 年份口径写在 `protocol.date_definition_note` 与 `time_trend.unit_note`（在线发表/见刊/预印本），并记录年份来源（metadata/题录/页眉/正文）。
- 冲突证据不隐藏、不静默择一：两方都入 `conflicts.items`，给出分辨率或“未决”。

## 7. 自检清单（提交前逐条勾选，对应评分维度）
- [ ] result.json 合法且含 case_id、output_contract 全部字段与第 3 节固定字段（任务完成度/完整性）。
- [ ] 四类产物均非空且可打开：time_trend.distribution 有年度计数、topic_evolution 有阶段与依据、representative_works 有各阶段作品与口径、research_gaps 有排除证据（实际效果/完整性）。
- [ ] 每条非空 quote 为原文逐字引用，selfcheck 无“未定位”硬错误（结果可核验性）。
- [ ] 年份口径在 protocol.date_definition_note 与 time_trend.unit_note 写明（结果正确性）。
- [ ] 代表作不只用被引排序，criteria 含方法首创/里程碑/覆盖阶段的口径（结果正确性）。
- [ ] 空白项均写 exclusion_evidence，`coverage_absence` 与 `open_question` 区分清楚（结果正确性）。
- [ ] 冲突证据均记录于 conflicts，未静默择一（结果完整性）。
- [ ] 无编造 benchmark score、无预置答案；status 与四类产物一致（可靠体验）。
- [ ] 无法确认项标 `not_reported`/`inferred` 或入 `pending_human`，不凭空补结论（结果正确性）。
- [ ] limitations 如实列出缺失材料、未核验类别、解析失败；errors/degradations 有值即说明（异常处理）。
- [ ] change_log 记录骨架落盘与每次增量；重复运行结果一致（可核验性/稳定性）。

## 8. 降级与失败处理
- **检索为空/缺材料**：`status=insufficient_evidence`，research_gaps 写“材料不足，无法区分空白与遗漏”，不编造结论。
- **年份缺失**：distribution 置空、`unit_note` 写“无出版年份元数据，待人工”，`pending_human` 记录；不猜年份。
- **主题严重歧义/同名异义**：term_mapping 记录全部变体，`uncertainty` 说明归并口径，`confidence=low`。
- **文献量爆炸**：脚本按 2MB/200 文件上限采样并记录 `degradations`；报告写明“样本边界”，不宣称全领域覆盖。
- **证据冲突/权限不足/PDF 不可读**：如实标 `source_conflict`/`unchecked`/`degradations`，写入 limitations，不静默择一、不伪造文本层。
- **脚本失败**：selfcheck 报错时优先手工修复；无法修复则保留已落盘骨架并如实标 status/limitations。任何情况下不伪造软件状态、数据、文献或分数；partial 结果必须标明边界。

## 9. 本地自检（可选）
提交前从包根运行 `python3 -B scripts/selftest.py`：依次用“良构案例 / 空输入 / 坏 JSON”三组合成输入跑完整流水线，断言退出码 0、result.json 合法、output_contract 回填、quote 可定位、无伪造分数；三组全 PASS 才可交付。
