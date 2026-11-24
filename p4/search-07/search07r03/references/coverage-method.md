# 覆盖率口径与漏检归因方法（coverage-method）

本文件定义 Skill 的命中规则、覆盖率口径、漏检分类与补检停止规则。脚本阈值与本文件保持一致，改阈值必须同步 `scripts/core_impl.py` 开头的常量。

## 1. 覆盖率口径（防止“以结果定义分母”）

- **参考集是分母的唯一来源**：参考集必须来自独立于检索结果的材料（综述、指南、专家清单、已有系统综述或组织方提供的冻结清单），不允许用检索结果自身反推应检文献。
- 参考集在 `run` 阶段被**冻结**：`reference_set.version` 是全部参考记录规范化后的 SHA-256 前 12 位；同一参考集必然产生同一 version，改动任何字段都会使 version 变化。
- **分母 denominator = 参考集中 eligible 记录数**。eligible = 总记录数 − 不可用记录（撤稿/无法获取全文/既无标识符又无标题）。
- **分子 numerator = eligible 记录中命中（hit）的记录数**。
- `coverage_rate = numerator / denominator`；`omission_rate = 1 − coverage_rate`；两者均保留原始浮点值，不做四舍五入掩盖。
- 不可用记录**不进分母**，但逐条保留并标注 `invalid_reason`，不删除、不当作“不相关”划掉。

## 2. 命中判定规则（三级、确定性、可复算）

脚本按如下顺序对“参考记录 vs 检索结果记录”逐条判定，所有比较在规范化后执行（NFKC、小写、去标点、合并空白）：

1. **标识符精确匹配**：DOI/PMID/PMCID/arXiv/ISBN/PII/accession 等任一标识符（规范化后、长度 ≥ 4）完全一致 → `hit`，`matched_by=identifier`。同一标识符对应多个标题冲突时 → `adjudicate`，`matched_by=identifier_conflict`，并记录 `source_conflict`。
2. **标题 + 第一作者 + 年份近似匹配**：标题相似度用 `difflib.SequenceMatcher` 比值，满足 `title_similarity >= TITLE_FUZZY(0.88)` 且年份差 `<= YEAR_TOLERANCE(1)`（缺年份视为不否决）且第一作者姓氏一致（缺作者视为不否决）→ `hit`，`matched_by=title_author_year`。若年份或作者明确不一致 → `adjudicate`，`matched_by=title_disagreement`。
3. **近阈值转人工**：`0.70 <= title_similarity < 0.88` → `adjudicate`，`matched_by=title_near_threshold`。
4. **否则 → `miss`**：保留在 `omissions` 中，不允许删除。

人工只做两类工作：对 `adjudicate`/`miss` 记录裁决归属，以及对漏检记录写归因。逐条比对、去重、覆盖率计算全部由脚本完成，避免人工比对偏差。

## 3. 漏检分类（每条漏检必须可指认“哪条查询/字段/来源/时间窗”）

每条 `omissions` 记录必须给出 `category` + `signals` + `evidence`（locator + 逐字 quote），并落到具体 `query` / `field` / `source` / `time_window`。允许的类别：

| 类别 | 含义 | 判定证据示例 |
|---|---|---|
| `query_term_gap` | 查询词未覆盖同义表达/上位词 | 漏检标题含检索式未出现的术语 |
| `field_restriction` | 检索字段过窄（仅标题/仅摘要） | 命中词只出现在全文或关键词字段 |
| `time_window_truncation` | 时间窗截断 | 漏检年份在窗口外或边界被排除 |
| `source_not_indexed` | 来源库未收录 | 漏检记录不在查询所指向的来源响应中 |
| `identifier_anomaly` | 记录标识异常/冲突/缺失 | DOI 缺位、大小写错位、重复 DOI |
| `metadata_missing` | 元数据缺失导致无法比对 | 无作者、无年份、标题残缺 |
| `unknown` | 材料不足以归因 | 明确标注 `pending_human`，不硬编原因 |

禁止写“数据库覆盖不足”“关键词不好”这类空话；必须落到可执行的修复动作（换字段、换来源、放宽时间窗、补同义词）。

## 4. 补充检索与停止规则（防“补检空转”）

- 每轮补充检索记录：`id`、新 `query`、`fields`、`sources`、`time_window`、`new_hits`（只能是参考集内真实存在、且原先未命中的 ref_id）、`rationale`。
- **只有新增命中才计入增益**：`marginal_gain.before = 补检前漏检数`，`marginal_gain.new_hits = 新增命中数`，`marginal_gain.rate = new_hits / before`。若同义词重跑无新增命中，`new_hits=0`，不得声称遗漏下降。
- **停止规则**：连续一轮 `new_hits=0`，或剩余漏检全部归入 `unknown`/`pending_human`/不可补救类别时停止；残余遗漏逐条写 `residual_omissions` 并说明是否需要人工介入。

## 5. 确定性保证（本版侧重点）

- 结果文件不含墙钟时间、不含随机 ID；`runtime` 只记录固定预算常量。
- 参考集、命中矩阵、漏检、补充检索、证据映射在写出前全部按稳定键排序；JSON 用 `sort_keys=True` 序列化。
- 因此**同一输入目录 + 同一 updates 文件，两次运行产生字节级相同的 result.json**。`scripts/selftest.py` 用双跑比对验证这一点。
- 例外：`/app/work/` 下的中间产物与人工日志可含时间戳，但 `result.json` 本身确定性不依赖它们。

## 6. 方法边界（相对“以结果定义分母”的边界）

- 本方法只做**已知参考集下的覆盖测量与漏检归因**，不做正式系统综述的纳入/排除流程，不做实时在线数据库接入（题目禁止伪造线上覆盖声明）。
- 命中判定是启发式近似匹配：标题相似度在 0.70–0.88 之间的记录一律转人工，不在脚本内硬判，避免误计覆盖。
- 参考集本身的质量（是否完备、是否有偏）不在本 Skill 的判定范围；材料未提供完备参考集时如实标 `insufficient_evidence`，不补造文献、DOI、年份或检索结果。
