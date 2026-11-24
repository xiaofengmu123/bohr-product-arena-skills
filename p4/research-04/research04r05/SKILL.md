---
name: research-frontier-contract
description: 面向"研究前沿与趋势分析"的可复核综述 Skill。核心设计是"契约对齐器"：读取 /input/case.json 的 output_contract，逐字段推断类型、映射到分析产物、生成 contract_alignment 对账表，保证 result.json 顶层字段与 output_contract 一一对齐（缺值 null/[]、未知键保留），且每次写盘都经 change_log 留痕。产物含时间趋势、主题演化、代表性工作、研究空白四类可检查结论，每条结论回链原文 locator + 逐字 quote，区分 reported/inferred/not_reported。用于文献综述、趋势分析、主题演化、代表作选取、研究空白识别；不用于一次性问答、纯润色、单篇审稿。
version: 2.0.0
---

# 研究前沿与趋势分析 Skill（契约对齐版）

## 1. 角色与何时使用

本 Skill 把"这个方向现在做到哪、往哪走、哪里还空着"变成**可复核、可复算、契约逐字段对齐**的综述产物，而不是"检索结果前沿清单"。触发条件：任务要求"文献综述 / 研究前沿 / 趋势分析 / 主题演化 / 选题评估 / 研究空白识别"，或需交付能回到原文逐条核对的趋势结论。

四条硬约束（对应"实际效果"40 分）：

1. 趋势由**可核对的年度分布**支撑；禁止把关键词热度当趋势、把检索前 N 条当前沿。
2. 演化说明**何时、因何变化**（出现/合并/分裂/衰退/术语漂移/同名异义）；禁止把"换了说法"当新兴主题。
3. 代表作**覆盖各阶段且口径可说明**；禁止只用总被引排序，须覆盖长尾方法首创。
4. 空白有**排除证据**（检索式 + 纳排规则 + 反例排查）；禁止把"没检索到"当空白。

本版对四条约束叠加一条贯穿性纪律——**输出契约逐字段对齐 + 全程 change_log 留痕**：result.json 的顶层字段集合必须覆盖 output_contract 声明的每一个键，每个键的类型由描述推断并强制转换，缺失映射显式记为 `not_reported`；任何一次写盘（含骨架落盘）都追加一条 change_log。字段字典见 `references/contract.md`，自检清单见 `references/checklist.md`。

不适用：一次性脚本、单篇论文审稿、语言润色。严禁预置答案、伪造评测分数/白板或编造缺失数据；每个案例独立运行，不跨案例复用结论。

## 2. 输入契约（运行时）

- 原始输入在 `/input/case.json` 与 `/input/task.md`；科研材料（论文 PDF、题录、正文、摘要、检索结果集）由平台放在 `/input/`（常见子目录 `materials/`）或由 `case.json` 指定路径。
- **先读实际文件内容**，不依文件名/领域标签臆测；递归扫描 `/input/` 发现文件，不假设固定文件名。`case.json`/`task.md`/`input.json` 是控制文件，永不进入文献语料。PDF 由平台 `pdf-text-tool` 或本包 `pdftotext` 解析，失败按第 7 节降级，不伪造文本层。
- 文本按 `utf-8-sig → utf-8 → gb18030 → latin-1` 降级解码，坏编码/BOM/坏 JSON 记入 `errors`；单文件 >2MB 跳过、材料 >200 文件按路径排序确定性采样，记入 `degradations`。
- 工作目录 `/app/work`（中间产物）；最终产物写到 `/app/output/`，其中 `result.json` 必产。

## 3. 输出契约（本版核心：逐字段对齐）

必产 `/app/output/result.json`（UTF-8、合法 JSON）。构造规则：

1. **固定分析字段**（`case_id`、`status`、`research_question`、`protocol`、`time_trend`、`topic_evolution`、`representative_works`、`research_gaps`、`evidence_map`、`conflicts`、`sources`、`uncertainty`、`pending_human`、`change_log`、`limitations`、`errors`、`degradations`、`runtime`、`deliverable`、`deliverables`）。
2. **契约字段**：若 `case.json` 含 `output_contract`，逐个读取其键，按 `scripts/core_impl.py::infer_type` 从描述推断 JSON 类型（string/object/array/number/boolean），把该键**原样落到 result.json 顶层**并给出类型合规的值；能映射到分析字段的用分析值，映射不到的置类型缺省值（`""`/`{}`/`[]`/`0`/`false`），不改名、不删未知键。
3. **对账表**：追加 `contract_alignment` 数组，每条 `{contract_key, declared, inferred_type, source, value_status, note}`，`value_status` 取 `filled | empty | not_reported`。对账表是"逐字段对齐"的可核验证据。
4. **留痕**：`change_log` 数组每条 `{time, action, reason, before, after}`，骨架落盘即写入首条，之后每次增量各写一条；`revision` 单调递增。

`status` 取 `ok | partial | insufficient_evidence | input_error`；若 output_contract 声明了不同状态枚举，`contract_alignment` 记录映射关系（`answered→ok` 等），不静默改名。事件 `type`、空白 `type`、代表作 `kind`、证据 `status`、claim `support` 的枚举与完整字段定义见 `references/contract.md`。

同时产出可编辑主产物 `report.md`、证据映射 `evidence_map.json`、可复算表 `tables/documents.csv`、`tables/year_distribution.csv`、`tables/term_frequency.csv`。

## 4. 执行流程（总 900 秒、64 次模型请求、16384 tokens/响应）

每步标注输入/输出与预算。**前 3 分钟内必须先落盘一版 result.json 骨架**，此后只做增量改进，任何中断都不留空白产物。脚本相对路径，从包根（平台默认 `/skills/weekly-skill`）运行，参数缺省 `--input /input --output /app/output --work /app/work`。

1. **发现与落骨架（0–180 秒，≤4 次请求）**：`python3 scripts/main.py prepare`。扫描 `/input/`、解析 case.json/task.md、抽取材料文本到 `/app/work/materials/`、回填并**逐字段对齐** output_contract、写 `/app/work/manifest.json` 与 `/app/output/result.json` 骨架。产出：可交付 partial 结果 + 材料清单 + contract_alignment。
2. **机械基线（180–360 秒，≤6 次请求）**：`python3 scripts/main.py analyze`。确定性抽取每篇文档年份/标题、年度分布、术语频率、空白候选句、冲突候选句、代表作候选，写 result.json 的 `machine_candidates`（标 `machine=true`）并重算对齐。
3. **四类深度分析（360–720 秒，约 40 次请求）**：按 `time_trend → topic_evolution → representative_works → research_gaps` 顺序，逐篇读材料相关段落，把机械候选核验为结论。每完成一类把增量写 `/app/work/patches/<类别>.json`（只含要改的顶层字段），再 `python3 scripts/main.py commit --file <patch>` 合并、重算对齐、追加 change_log。空白项必须写 `exclusion_evidence`（检索式 + 排除条件），区分证据排除与检索遗漏。
4. **自检（720–820 秒，≤8 次请求）**：`python3 scripts/selftest.py`，校验契约字段逐一对齐、枚举、四类产物、quote 可定位、change_log 含骨架落盘、无伪造分数；修复结构错误，无法定位的 quote 改为精确引用或置 `status=not_reported` 并写入 `uncertainty`。
5. **收尾（820–900 秒，≤6 次请求）**：`python3 scripts/main.py finalize` 定稿 status/uncertainty/pending_human/limitations/change_log，复跑 selftest 确认通过；`status=ok` 仅在四类产物齐全且关键 claim 均有证据回链时给出。

预算纪律：前 3 分钟只保证骨架存在；随后按类别增量；最后 2 分钟只校验与修复，不新增大范围检查。

## 5. 证据回链与结论边界

- 每个趋势判断/演化结论/空白主张的 `claims[]` 条目以 `evidence_ids[]` 指向 `evidence_map`；每条证据含 locator（形如 `materials/mat_001.txt#L12-L15`）+ 逐字 quote。
- 材料事实、合理推断、无法确定三类显式区分：直接可引用的写 `support=reported`；需归纳的写 `inferred` 并在 `rationale` 说明；查不到写 `not_reported`，不补写。
- 冲突证据两方都记录并标 `source_conflict`，在 `conflicts` 里给出分辨率或"未决"，不静默择一。

## 6. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] result.json 合法且顶层字段覆盖 output_contract 全部键（类型合规），contract_alignment 对账完整（任务完成度/完整性）。
- [ ] 四类产物非空且可打开：年度计数、演化阶段与依据、各阶段代表作与口径、空白排除证据（实际效果/完整性）。
- [ ] 每条非空 quote 为原文逐字引用，selftest 无"未定位"硬错误（结果可核验性）。
- [ ] 年份口径写于 `protocol.date_definition_note` 与 `time_trend.unit_note`（结果正确性）。
- [ ] 代表作不只用被引排序，criteria 含方法首创/里程碑/覆盖阶段（结果正确性）。
- [ ] 空白项均写 exclusion_evidence，`coverage_absence` 与 `open_question` 区分清楚（结果正确性）。
- [ ] 冲突证据均记录于 conflicts，未静默择一（结果完整性）。
- [ ] 无编造 benchmark score、无预置答案；status 与四类产物一致（可靠体验）。
- [ ] 无法确认项标 `not_reported`/`inferred` 或入 `pending_human`，不凭空补结论（结果正确性）。
- [ ] limitations 如实列出缺失材料、未核验类别、解析失败；errors/degradations 有值即说明（异常处理）。
- [ ] change_log 记录骨架落盘与每次增量，revision 单调递增；重复运行结果一致（可核验性/稳定性）。

## 7. 降级与失败处理

- **检索为空/缺材料**：`status=insufficient_evidence`，`research_gaps` 写"材料不足，无法区分空白与遗漏"，不编造结论。
- **年份缺失**：`distribution` 置空、`unit_note` 写"无出版年份元数据，待人工"，`pending_human` 记录；不猜年份。
- **主题严重歧义/同名异义**：`term_mapping` 记录全部变体，`uncertainty` 说明归并口径，`confidence=low`。
- **文献量爆炸**：脚本按 2MB/200 文件上限确定性采样并记 `degradations`；`coverage.sample_boundary` 写明"样本边界"，不宣称全领域覆盖。
- **证据冲突/权限不足/PDF 不可读/缺 pdftotext**：如实标 `source_conflict`/`unchecked`/`degradations`，写入 `limitations`，不静默择一、不伪造文本层、不假装软件可用。
- **契约无法解析**：output_contract 缺失或非 dict 时，仍产出固定字段的合法 partial 结果，`contract_alignment` 置空并记 `errors`，不因此崩溃。
- **脚本失败**：selftest 报 FAIL 时优先手工修复；无法修复则保留已落盘骨架并如实标 status/limitations。任何情况下不伪造软件状态、数据、文献或分数；partial 结果必须标明边界。

## 8. 脚本与最小调用示例

- `scripts/main.py`：`prepare | analyze | commit --file <patch> | finalize | --version`，纯标准库。
- `scripts/core_impl.py`：I/O 容错、契约类型推断与对齐、机械抽取、原子写盘与 change_log。
- `scripts/selftest.py`：提交前自检，退出码 0/1，写 `/app/work/selftest.json`；`--demo` 跑良构/空输入/坏 JSON 三组合成输入。

最小调用（示意接口，非正式结果）：

```
python3 scripts/main.py prepare
python3 scripts/main.py analyze
python3 scripts/main.py commit --file /app/work/patches/time_trend.json
python3 scripts/main.py finalize
python3 scripts/selftest.py
```

示意骨架（示例，非案例结果）：`{"case_id":"<case>","status":"partial","research_question":null,"protocol":{"inclusion_criteria":[],"exclusion_criteria":[],"search_scope":null,"date_definition_note":null},"time_trend":{"unit_note":null,"distribution":[],"windows":[],"claims":[]},"topic_evolution":{"stage_divisions":[],"term_mapping":[],"events":[],"claims":[]},"representative_works":{"criteria":[],"stages":[]},"research_gaps":{"gaps":[],"excluded_not_gaps":[]},"evidence_map":[],"conflicts":[],"sources":[],"uncertainty":[],"pending_human":[],"change_log":[],"limitations":[],"errors":[],"degradations":[],"runtime":{},"contract_alignment":[]}`。
