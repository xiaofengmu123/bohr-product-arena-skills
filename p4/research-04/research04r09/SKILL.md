---
name: frontier-trend-review
description: 面向“研究前沿与趋势分析”的可复核综述 Skill。把给定文献组织为时间趋势、主题演化、代表性工作、研究空白四类可检查产物，每条结论回链原文（locator + 逐字 quote），显式区分材料事实、合理推断与无法确定，并区分“证据排除的空白”与“检索遗漏”，诚实标注证据边界。当任务要求文献综述、研究前沿、趋势分析、主题演化、代表性工作选取或研究空白识别时使用；不用于一次性问答、纯润色或单篇论文审稿。
version: 1.0.0
---

# 研究前沿与趋势分析 Skill

## 1. 角色与何时使用

本 Skill 把“这个方向现在做到哪、往哪走、哪里还空着”变成**可复核的综述产物**，而不是一份“检索结果前沿清单”。触发条件：任务要求文献综述、研究前沿、趋势分析、主题演化、选题评估或研究空白识别。

四条硬约束（对应评分“实际效果”）：
1. 趋势必须由**可核对的年度分布**支撑；禁止把关键词热度当趋势、把检索前 N 条当前沿。
2. 演化必须说明**何时、因何变化**（出现/合并/分裂/衰退、术语漂移、同名异义）；禁止把“换了说法”当新兴主题。
3. 代表作必须**覆盖各阶段且口径可说明**；禁止只用总被引排序，须覆盖长尾方法首创工作。
4. 空白必须给出**排除证据**（检索范围 + 纳排规则 + 反例排查）；禁止把“没检索到”当空白。

不适用：一次性脚本、单篇论文审稿、语言润色。严禁生成预置答案、伪造评测分数或编造缺失数据；每个案例独立运行，不跨案例复用结论。

## 2. 输入契约（运行时）

- 原始输入在 `/input/case.json` 与 `/input/task.md`；科研材料（论文 PDF、正文、摘要、检索结果集）由平台放在 `/input/`（常见子目录 `materials/`），或由 `case.json` 指定路径。
- **先读实际文件内容**，不依据文件名或领域标签臆测；扫描 `/input/` 全目录发现文件，不假设固定文件名。PDF 由平台 `pdf-text-tool` 解析（脚本内也尝试 `pdftotext`），失败按第 6 节降级，不伪造文本层。
- 文本统一按 `utf-8-sig` 读取，坏编码/BOM/坏 JSON 自动降级（`gbk`/`latin-1`）；单文件 >2MB 跳过并记录。
- 中间产物写 `/app/work`，最终产物写 `/app/output/`，其中 `result.json` 必产。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8、合法 JSON）。若 `/input/case.json` 含 `output_contract`，**原样回填其全部字段**（缺值给 `null`/`[]`，不改名、不删未知键），再合并下列固定字段。同时产出可编辑主产物 `report.md`、证据映射 `evidence_map.json` 与可复算表 `tables/documents.csv`、`tables/year_distribution.csv`。

固定字段（详见 `references/contract.md` 的完整结构）：
- `case_id`：取 case.json 的 `case_id`/`id`/`challenge_id`，缺省 task.md 的 `challenge_id`，再无 `"unknown"`。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实反映完成度。
- `research_question` / `protocol`：问题拆解为可核对子问题；纳排规则与年份口径（在线发表/见刊/预印本）。
- `time_trend`：年度分布 + 时间窗 + 逐条判断；`support` 取 `reported|not_reported|inferred`。
- `topic_evolution`：阶段划分、术语归并（canonical/variants/首末年份）、演化事件（emergence/merge/split/decline/terminology_drift）。
- `representative_works`：选取口径 + 各阶段作品；`kind` 取 `origin|method|milestone|benchmark`。
- `research_gaps`：空白项均带排除证据；`type` 取 `open_question|method_failure|unresolved_conflict|coverage_absence`。
- `evidence_map`：每条 `{id(E001起),claim,locator,quote,source_file,status,confidence}`；`status` 取 `reported|not_reported|source_conflict|inferred`，`quote` 为原文逐字引用。
- `conflicts`、`sources`、`uncertainty[]`、`pending_human[]`、`change_log[]`、`limitations[]`、`errors[]`、`degradations[]`、`runtime`。

## 4. 执行流程与预算

总预算 **900 秒、64 次模型请求、16384 tokens/响应**。**前 3 分钟必须先落盘一版 result.json 骨架**，此后只做增量改进，任何中断都不留空白产物。

1. **发现与落骨架（0–180 秒，≤4 次请求）**：`python3 scripts/main.py prepare`。输入：`/input/case.json`、`/input/task.md`、`/input/` 全部材料。输出：`/app/work/manifest.json`、`/app/work/materials/*.txt`、`/app/output/result.json` 骨架（含 case_id 与 output_contract 回填）。
2. **机械基线（180–360 秒，≤6 次请求）**：`python3 scripts/main.py analyze`。输入：manifest + 材料文本。输出：年度分布、术语首末年份、空白/冲突候选句、代表作候选，写入 result.json、report.md、evidence_map.json、tables/*.csv，标注 `machine=true`。
3. **四类深度分析（360–720 秒，约 40 次请求）**：按 `time_trend → topic_evolution → representative_works → research_gaps` 顺序逐篇核验候选为结论，更新 support/status/confidence 并去 `machine` 标记或注明未核验。每完成一类即重写 result.json 并记 change_log。空白项必须写 `exclusion_evidence`。
4. **自检（720–820 秒，≤8 次请求）**：`python3 scripts/selftest.py`。输入：result.json + 材料文本。输出：`/app/work/selftest.json`；校验契约、枚举、四类产物、quote 可定位、无伪造分数；无法定位的 quote 改为精确引用或置 `status=not_reported` 并写入 uncertainty。
5. **收尾（820–900 秒，≤6 次请求）**：定稿 status/uncertainty/pending_human/limitations/change_log，复跑 selftest；`status=ok` 仅在四类产物齐全且关键 claim 均有证据回链时给出。

## 5. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] result.json 合法，含 case_id、output_contract 全部字段与固定字段（任务完成度/完整性）。
- [ ] 四类产物齐备：time_trend.distribution 有年度计数、topic_evolution 有阶段与依据、representative_works 有各阶段作品与口径、research_gaps 有排除证据（实际效果/完整性）。
- [ ] 年份口径写在 protocol.date_definition_note 与 time_trend.unit_note（结果正确性）。
- [ ] 趋势判断由年度分布支撑，离散波动与拐点已区分（结果正确性）。
- [ ] 主题演化写清阶段划分依据（何时、因何），术语漂移/同名异义已标注（结果正确性）。
- [ ] 代表作覆盖各阶段且 criteria 可说明，不只按被引排序，含长尾方法首创（结果正确性）。
- [ ] 空白项均写 exclusion_evidence，coverage_absence 与 open_question 区分（结果正确性）。
- [ ] 每条 claim 经 evidence_ids 回链 evidence_map；非空 quote 为原文逐字，selftest 无未定位硬错误（结果可核验性）。
- [ ] 冲突证据均记录于 conflicts，未静默择一（结果完整性）。
- [ ] 无编造 benchmark 分数、无预置答案；status 与四类产物一致（可靠体验）。
- [ ] 无法确认项标 not_reported/inferred 或入 pending_human，不凭空补结论（结果正确性）。
- [ ] limitations 如实列出缺失材料、未核验类别、解析失败；errors/degradations 有值即说明（异常处理）。
- [ ] change_log 记录骨架落盘与每次修改；重复运行结果一致（可核验性/稳定性）。

## 6. 降级与失败处理

- 检索为空/缺材料：`status=insufficient_evidence`，research_gaps 写“材料不足，无法区分空白与遗漏”。
- 年份缺失：distribution 置空、unit_note 写“无出版年份元数据，待人工”，pending_human 记录；不猜年份。
- 主题严重歧义/同名异义：term_mapping 记录全部变体，uncertainty 说明归并口径，confidence=low。
- 文献量爆炸：按 2MB/200 文件上限采样并记录 degradations，报告写明样本边界，不宣称全领域覆盖。
- 证据冲突/权限不足/PDF 不可读：如实标 source_conflict/unchecked/degradations，写入 limitations，不静默择一、不伪造文本层。
- 脚本失败：selftest 报错先手工修复；无法修复则保留已落盘骨架并如实标 status/limitations。任何情况不伪造软件状态、数据、文献或分数。
