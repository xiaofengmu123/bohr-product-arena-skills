# 覆盖率口径、命中规则与漏检分类方法

本文档与 `scripts/core_impl.py` 中的阈值必须保持一致。若修改脚本阈值，须同步修改本文档（见 `references/checklist.md` 的可复用一致性检查项）。

## 1. 覆盖率口径（分母来自独立参考集）

- **参考集（denominator 的来源）**：从给定材料（综述、指南、专家清单、已有系统综述或组织方提供的参考记录）整理出的“已知相关文献”集合，**独立于任何一次检索结果**。不得用检索结果自身反推应检文献。
- **冻结**：参考集一经整理即写入 `reference_set.version`（内容哈希前 12 位）并固定；后续补检只能追加说明，不得改动冻结版本。
- **分母**：`coverage.denominator` = 参考集中状态为 `assessable`（可评估）的记录数。
- **排除项**（单独计数、不并入分母，理由逐条保留）：
  - `unavailable`：无法获取全文/权限受限；
  - `retracted`：撤稿或撤回；
  - `conflict`：同一标识符在多个来源元数据冲突。
- **分子**：`coverage.numerator` = 在检索结果中命中（高/中置信度）的 assessable 记录数。
- **覆盖率**：`coverage_rate = numerator / denominator`（分母为 0 时记 `null`，不写 0 冒充）。`omission_rate = 1 - coverage_rate`。
- **不确定区间**：命中判定存在低置信度（title-only 模糊匹配）时，`coverage.uncertainty` 给出 `coverage_lower`（只算高置信度命中）与 `coverage_upper`（高+低置信度命中）。区间非零即表示存在需要人工裁决的判定边界，**禁止用单点覆盖率掩盖裁决不确定性**。

## 2. 命中判定规则（逐级，先到先得）

对每条参考记录与全部检索结果比对，取最优候选：

1. **标识符精确匹配**：DOI（规范化：去 `doi:`/`https://doi.org/` 前缀、小写、去尾标点）、PMID、PMCID、arXiv ID 任一精确相等 → `hit`，`matched_by=identifier`，`confidence=high`。
2. **标题+第一作者+年份近似匹配**：标题相似度 ≥ `title_fuzzy=0.88` 且（第一作者姓氏相等 **或** 年份相差 ≤ `year_tolerance=1`）→ `hit`，`matched_by=title_author_year`，`confidence=high`（相似度 ≥ 0.95）或 `medium`。
3. **标题单独模糊匹配**：标题相似度 ≥ `title_adjudication=0.70` → `adjudicate`，`matched_by=title_only_fuzzy`，`confidence=low`，转人工裁决。
4. 其余 → `miss`，`matched_by=none`，`confidence=none`。

标题相似度用 `difflib.SequenceMatcher.ratio()` 对规范化标题（NFKD 去重音、小写、去 HTML、去非字母数字/CJK 字符、折叠空白）计算。标识符冲突或来源元数据不一致 → 并存并标 `source_conflict`，不擅自抹平。

## 3. 漏检分类（可操作，禁止“数据库覆盖不足”这类空话）

每条 `miss` 必须归因到**具体**的查询式/字段/来源/时间窗，类别与判定证据：

| 类别 | 含义 | 判定证据要求 |
|-|-|-|
| `query_term_miss` | 查询词未覆盖同义表达/变体 | 指出漏检文献的实际标题词与所用查询词 |
| `field_restriction` | 检索字段限制过窄（如仅 title 未含 abstract） | 指出字段设置与漏检词出现位置 |
| `time_window_truncation` | 时间窗截断 | 指出漏检文献年份与窗口边界 |
| `source_not_indexed` | 来源库未收录该记录 | 指出具体来源库名与证据 |
| `identifier_anomaly` | 记录标识异常/缺失/冲突 | 指出缺失或冲突的标识符 |
| `unavailable` | 无法获取全文/权限受限 | 权限或全文状态证据 |
| `retracted_or_withdrawn` | 已撤稿/撤回 | 撤稿声明 locator + quote |

每条漏检字段：`ref_id`、`category`、`query_id`（对应哪次查询）、`field`、`source`、`time_window`、`evidence`（`{locator, quote}`）。

## 4. 补充检索与停止规则

- 依据漏检类别设计**新查询式或新来源**，逐条记录 `supplementary_searches`：`query`、`source`、`fields`、`time_window`、`new_hits`（新增命中的 ref_id 列表）、`evidence`。
- **新增命中必须来自材料中真实存在、且原先未命中的记录**；禁止虚构命中，禁止“换同义词重跑无新增却报遗漏下降”。
- `marginal_gain` 以**补检前漏检数**为分母：`gain_rate = new_hits / before_omissions`。
- **停止规则**：连续一次补检 `new_hits` 为 0，或漏检类别全部属于不可补救（`unavailable`/`retracted_or_withdrawn`/`source_not_indexed` 且无替代来源），即停止并进入残余遗漏说明。残余遗漏逐条给 `remediable`（是否可补救）与 `remedy_needed`（需要什么）。

## 5. 溯源与诚实

- 每个关键结论/数值/命中/漏检归因都带 `locator`（`文件::位置`）与逐字 `quote`；缺失写 `not_reported`。
- 不编造 DOI、年份、命中、检索结果、来源库状态或评测分数。
- 材料不足 → `status=insufficient_evidence` 并列出缺口；坏 JSON/坏编码 → 记 `errors`/`degradations` 后继续处理其余材料；缺软件（如无 pdftotext）→ 记 `limitations`，不假装提取了 PDF 文本。

## 6. 与“以结果定义分母”的边界

本方法的分母来自**独立冻结参考集**，因此覆盖率可以低于 1，遗漏可被统计与归因；而“以检索结果自身反推应检文献”的分母随结果变化，覆盖率恒近满分、遗漏无法统计。本 Skill 只做已知集覆盖测量，不替代数据库实时接入、全文阅读或系统综述的正式纳排流程。
