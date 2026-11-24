# 四项交付判定口径与证据字段规范

## 0. 证据字段（所有交付共用）

- `source_id`：来自 `result.json.sources` 的稳定 id（`src-<sha1(相对路径)前10位>`）。
- `locator`：`/input` 内相对路径 + 行号，如 `materials/paper.md#L12`；PDF 以 pdftotext 抽取文本为准（存 `/app/work/text/<rel>.pdf.txt`），行号指抽取文本的行。
- `quote`：材料原文逐字摘录；不得改写、不得翻译后冒充原文。无引用写 `not_reported`，并同步写入 `pending_human`。
- `claim`：本条判断要支撑的结论陈述。
- `claim_type`：`fact`（材料原文直接陈述）/ `inference`（基于材料的合理推断，须给理由）/ `unknown`（材料未覆盖）。
- `status`：`ok`（证据可定位）/ `weak`（证据间接）/ `missing`（无证据）。

## 1. 研究问题定义（research_question）

- `testable_statement`：一句可检验陈述，必须同时含**研究对象、关键变量、比较基线、预期结论、可证伪条件**五要素。
- 判定口径：五要素齐全且相互自洽 → `status=complete`；缺要素 → `partial` 并逐项列进 `gaps`；材料完全无法支撑 → `missing`。
- 可证伪条件必须写明"观察到什么会被否定"，不得写成"有待进一步研究"。

## 2. 已有工作比较（prior_work）

- `comparison_matrix` 每行至少含 `task/method/data/evaluation/conclusion` 五字段；字段值只允许来自该文献自身，**禁止跨文档串值**。
- `closest_work`：标出真正最相近的工作；判定依据是该工作与本问题的任务+方法重叠最大，而非引用次数或标题相似。
- `non_comparable_reasons`：逐条写明无法直接比较的原因（数据口径不同/任务不同/评价方式不同/报告不全）。

## 3. 创新点识别（novelty）

- 每条 claim 必须标注类型：`方法创新/数据创新/场景迁移/结论修正`。
- `verdict` 量表：`novel`（未被已有工作覆盖）/ `partially_covered`（部分重叠）/ `covered`（已被覆盖）/ `unverifiable`（材料不足无法判定）。
- `covered_by`：指向覆盖它的已有工作（`prior_work` 中的 work 名或 source_id）；`novel` 时可为空但必须写理由。
- **误判防护**：场景迁移（仅换应用对象而方法未变）不得标 `方法创新`；结论修正（仅解释差异/纠正既往结论）不得标 `方法创新`。

## 4. 可行性判断（feasibility）

- `preconditions` 逐条核查五个维度：`数据/算力/实验条件/时间窗口/人员能力`，每项给出 `statement` 与 `status`。
- `status` 四态：`reported`（材料明确给出）/ `assumed`（无出处，按常识假设，必须显式标注）/ `missing`（无信息）/ `conflicting`（材料内部矛盾）。
- `conditional_conclusion`：带条件的结论，形式为"在前提 A、B 成立且假设 C 可接受时，方案可行；否则存在失败风险"。
- `failure_risks`：列出被否定或缺失前提对应的具体失败模式。

## 5. 检索协议（search_protocol）

- 必含：`query_terms`（检索词）、`inclusion_criteria`（纳入标准）、`exclusion_criteria`（排除标准）、`databases`（检索范围）、`screening_notes`（筛选说明）。
- 口径：检索范围只登记实际检查过的材料；"检索不到"只写"本材料集内未见"，不写"尚无人研究"。

## 6. 冲突证据（conflicts）

- 同一指标/结论出现相反证据时，双方都登记（`side_a`/`side_b`），并写 `resolution`（取舍理由：以更直接证据/更大样本/更新数据/更强设计为准）。
- 禁止只保留支持性证据；无法取舍时 `resolution` 写 `unresolved` 并进 `uncertainty.unresolved`。

## 7. 边界与不确定性（synthesis + uncertainty）

- `synthesis.boundary_statement`：只陈述到证据范围为止，明确"本结论不适用于哪些对象/条件"。
- `uncertainty`：登记 `biases`（选择偏倚/来源偏倚）、`gaps`（缺材料）、`unresolved`（无法确定项）。
- 材料缺失、无法判断、运行失败分别说明，不越过证据范围扩展结论。
