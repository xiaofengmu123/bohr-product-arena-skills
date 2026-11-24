---
name: research-frontier-trend-uncertain
description: 面向“研究前沿与趋势分析”的可复核综述 Skill：把给定文献材料组织为时间趋势、主题演化、代表作与研究空白四类可检查产物，并对每条结论施加 reported/inferred/not_reported 三层证据边界与 high/medium/low/unverified 四级置信度；缺数据、缺年份、术语歧义、证据冲突、文献爆炸时按降级阶梯显式写入 limitations/uncertainty/degradations，绝不编造。当任务要求文献综述、趋势分析、主题演化、代表作选取或研究空白识别时使用；不用于一次性问答、纯润色或单篇审稿。
version: 1.1.0
---

# 研究前沿与趋势分析 Skill（降级与不确定性强化版）

## 1. 角色与何时使用

本 Skill 让 Agent 把“这个方向现在做到哪、往哪走、哪里还空着”变成**可复核、可复算、边界显式**的综述产物，而非“检索结果前沿清单”。触发条件：任务要求“文献综述 / 研究前沿 / 趋势分析 / 主题演化 / 选题评估 / 研究空白识别”，或需交付能逐条回到原文核对的趋势结论。

四条硬约束（对应“实际效果”40 分）：

1. 趋势由**可核对的年度分布**支撑；禁止把关键词热度当趋势、把检索前 N 条当前沿。
2. 演化说明**何时、因何变化**（出现/合并/分裂/衰退/术语漂移/同名异义）；禁止把“换了说法”当新兴主题。
3. 代表作**覆盖各阶段且口径可说明**；只用总被引排序为禁止，须覆盖长尾方法首创。
4. 空白必须有**排除证据**（检索式 + 纳排规则 + `counter_example_check`）；禁止把“没检索到”当空白。

本版对四条约束追加一条贯穿性纪律——**每条结论先定证据边界、再给置信度、再登记不确定性**：缺数据不编造，冲突不静默择一，机械候选未经核验一律 `unverified`。完整字段字典见 `references/contract.md`，自检清单见 `references/checklist.md`。

不适用：一次性脚本、单篇论文审稿、语言润色。严禁预置答案、伪造评测分数/白板或编造缺失数据；每个案例独立运行，不跨案例复用结论。

## 2. 输入契约（运行时）

- 原始输入在 `/input/case.json` 与 `/input/task.md`；科研材料（论文 PDF、题录、正文、摘要、检索结果集）由平台放在 `/input/`（常见子目录 `materials/`），或由 `case.json` 指定路径。
- **先读实际文件内容**，不依文件名/领域标签臆测；扫描 `/input/` 全目录发现文件，不假设固定文件名。PDF 由平台 `pdf-text-tool` 或本包脚本 `pdftotext` 解析；解析失败按第 7 节降级，不伪造文本层。
- 文本按 `utf-8-sig` 读取，坏编码/BOM/坏 JSON 自动降级（`gbk`/`latin-1`），单文件 >2MB 跳过并记录；全部由 `scripts/core_impl.py` 处理，不崩溃。
- 工作目录 `/app/work`（中间产物）；最终产物写到 `/app/output/`，其中 `result.json` 必产。

## 3. 输出契约

必产 `/app/output/result.json`（UTF-8、合法 JSON）。若 `/input/case.json` 含 `output_contract`，**原样回填其全部字段**（缺值 `null`/`[]`，不改名、不删未知键），再合并固定字段；无 contract 时仅用固定字段。同时产出 `report.md`、`evidence_map.json`、`tables/documents.csv`、`tables/year_distribution.csv`、`tables/term_mapping.csv`。

固定字段：`case_id`、`status`、`research_question`、`protocol`、`time_trend`、`topic_evolution`、`representative_works`、`research_gaps`、`evidence_map`、`conflicts`、`coverage`、`sources`、`uncertainty`、`pending_human`、`change_log`、`limitations`、`errors`、`degradations`、`runtime`。类型、枚举与逐字段说明以 `references/contract.md` 第 1 节为准，其中：

- `status` 取 `ok | partial | insufficient_evidence | input_error`。
- 每条结论先定三层边界 `reported/inferred/not_reported`，再给四级置信度 `high/medium/low/unverified`（`contract.md` 第 2 节）。
- `evidence_map` 每条含 `locator`（形如 `materials/mat_001.txt#L12-L15`）+ 逐字 `quote`；`quote` 必须是原文逐字，无法定位就改精确引用或置 `status=not_reported` 并写入 `uncertainty`。
- `coverage` 显式区分「证据排除的空白」与「检索遗漏」；`uncertainty` 每条含 `kind/location/impact/mitigation`。

## 4. 执行流程（总 900 秒、64 次模型请求、16384 tokens/响应）

每步标注输入/输出与预算。**前 3 分钟内必须先落盘一版 result.json 骨架**，此后只做增量改进，任何中断都不留空白产物。

1. **发现与落骨架（0–180 秒，≤4 次请求）**：`python3 scripts/main.py prepare`。扫描 `/input/`、解析 `case.json`/`task.md`、抽取材料文本到 `/app/work/materials/`、回填 `output_contract`、写 `/app/work/manifest.json` 与 `/app/output/result.json` 骨架。产出：可交付 partial 结果 + 材料清单。若脚本路径不可见，用绝对路径 `/skills/weekly-skill/scripts/main.py`。
2. **机械基线（180–360 秒，≤6 次请求）**：`python3 scripts/main.py analyze`。确定性抽取：每篇文档的年份/标题/被引、年度分布表、术语频率与首末年份、空白候选句（future work/limitation/unknown）、冲突候选句（however/in contrast/versus）、代表作候选。产出 `result.json` + `report.md` + `evidence_map.json` + `tables/*.csv`，全部标注 `provenance="machine"`、`confidence="unverified"`。
3. **四类深度分析（360–720 秒，约 40 次请求）**：按 `time_trend → topic_evolution → representative_works → research_gaps` 顺序，逐篇读材料相关段落，把机械候选核验为结论（更新 `support/status/confidence/provenance`，去 `unverified` 或注明未核验）。每完成一类即重写 `result.json` 并记 `change_log`。空白项必须写 `exclusion_evidence` + `counter_example_check`，区分证据排除与检索遗漏。
4. **自检（720–820 秒，≤8 次请求）**：`python3 scripts/selftest.py`，校验契约字段、枚举、四类产物齐全、quote 可定位、无伪造分数；修复结构错误，无法定位的 quote 改为精确引用或置 `status=not_reported` 并写入 `uncertainty`。
5. **收尾（820–900 秒，≤6 次请求）**：定稿 `status/uncertainty/pending_human/limitations/change_log`，复跑 `selftest.py` 确认通过；`status=ok` 仅在四类产物齐全且关键 claim 均有证据回链时给出。

预算纪律：前 3 分钟只保证骨架存在；随后按类别增量；最后 2 分钟只校验与修复，不新增大范围检查。脚本重复运行结果一致（`selftest.py --demo` 验证）。

## 5. 证据回链与结论边界

- 每个趋势判断/演化结论/空白主张的 `claims[]` 条目以 `evidence_ids[]` 指向 `evidence_map`；`evidence_map` 每条含 `locator` + 逐字 `quote`。
- 材料事实、合理推断、无法确定三类显式区分：直接可引用的写 `reported`；需要归纳的写 `inferred` 并在 `claim`/`rationale` 说明归纳路径；查不到写 `not_reported`，不补写。
- 置信度与边界一致：`reported` 通常 `high/medium`，`inferred` 通常 `medium/low`，`not_reported` 为 `unverified`。
- 冲突证据两方都记录并标 `source_conflict`，在 `conflicts` 里给出分辨率或“未决”，不静默择一。

## 6. 自检清单（提交前逐条勾选）

见 `references/checklist.md` 第 1 节，核心条目：

- [ ] result.json 合法且含 case_id、output_contract 全部字段与第 3 节固定字段。
- [ ] 四类产物非空且可打开：年度计数、演化阶段与依据、各阶段代表作与口径、空白排除证据。
- [ ] 每条非空 quote 为原文逐字，`selftest.py` 无“未定位”硬错误。
- [ ] 年份口径写于 `protocol.date_definition_note` 与 `time_trend.unit_note`。
- [ ] 代表作不只用被引排序，criteria 含方法首创/里程碑/覆盖阶段。
- [ ] 空白项均写 `exclusion_evidence` + `counter_example_check`，`coverage_absence` 与 `open_question` 区分清楚。
- [ ] 冲突证据均记录于 conflicts；每条 uncertainty 有 kind/location/impact/mitigation。
- [ ] 无编造 benchmark score、无预置答案；status 与四类产物一致。
- [ ] 无法确认项标 `not_reported`/`inferred` 或入 `pending_human`；limitations 如实列出缺失材料、未核验类别、解析失败。
- [ ] change_log 记录骨架落盘与每次修改；`selftest.py --demo` 四场景 PASS（重复运行一致）。

## 7. 降级与失败处理

降级状态机见 `references/contract.md` 第 4 节；每个触发器的强制动作缺一即为漏写：

- **检索为空/缺材料**：`status=insufficient_evidence`，`research_gaps` 写“材料不足，无法区分空白与遗漏”，不编造结论。
- **年份缺失**：`distribution` 置空、`unit_note` 写“无出版年份元数据，待人工”，`pending_human` 记录；不猜年份。
- **主题严重歧义/同名异义**：`term_mapping` 记录全部变体，`uncertainty` 记 `term_polysemy`，`confidence=low`。
- **文献量爆炸**：脚本按 2MB/200 文件上限采样并记 `degradations`；`coverage.sample_boundary` 写明“样本边界”，不宣称全领域覆盖。
- **证据冲突/权限不足/PDF 不可读**：如实标 `source_conflict`/`unchecked`/`degradations`，写入 `limitations`，不静默择一、不伪造文本层。
- **脚本失败**：读 `/app/work/selfcheck.json` 定位 FAIL 项，优先手工修复；无法修复则保留已落盘骨架并如实标 status/limitations。任何情况下不伪造软件状态、数据、文献或分数；partial 结果必须标明边界。
