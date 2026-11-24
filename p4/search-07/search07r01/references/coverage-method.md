# 覆盖率口径、命中规则与漏检分类（证据溯源强化版）

本文与 `scripts/core_impl.py` 中的阈值保持一致；脚本改动须同步更新本文。
所有阈值与分类必须能以逐字证据（locator + quote）复算，不依赖脚本内部猜测。

## 1. 覆盖率口径（分母必须独立冻结）

- **分母** = 独立冻结参考集规模（`reference_set.total`）。参考集来自综述、指南、专家清单或已有系统综述，**不是**检索结果自身；严禁"以结果定义分母"。
- **分子** = 逐条判定为 `hit` 的参考集记录数（`coverage.numerator`）。
- `coverage_rate = numerator / denominator`，`omission_rate = 1 - coverage_rate`，均保留 6 位小数写入，不四舍五入掩盖真实值。
- `reference_set.version` 形如 `frozen-YYYYMMDD-<hash8>`，冻结后同一输入重复运行结果一致（确定性：固定排序、无随机采样）。

## 2. 命中规则（三级，从强到弱）

1. **标识符精确匹配** `identifier:doi|pmid|arxiv`：DOI 去 URL 前缀并小写归一，arXiv 去版本后缀，PMID 取数字串；任一相等即 `hit`。
2. **标题+作者+年份模糊匹配** `title_fuzzy`：标题归一（NFKC、小写、去冠词、去非字母数字）后 `SequenceMatcher.ratio()` ≥ `TITLE_FUZZY=0.88`，且年份差 ≤ `YEAR_TOLERANCE=1`（作者可缺失）；记 `hit`。
3. **人工裁决区间** `adjudicate`：`0.70 ≤ ratio < 0.88`，标记待人工裁决（merge 时以 `--updates` 写入决策）。
4. 低于 `0.70` 或无标题可比 → `miss`（保留统计，绝不"当不相关删除"）。

阈值常量：`TITLE_FUZZY=0.88`、`TITLE_ADJUDICATION=0.70`、`YEAR_TOLERANCE=1`。

## 3. 漏检分类（每条必须归因到具体查询式/字段/来源/时间窗）

- `query_term_gap`：查询词未覆盖同义/变体/缩写表达。证据须引该记录的标题/摘要逐字片段与缺失的同义表达。
- `field_too_narrow`：检索字段过窄（如仅题名、未含摘要/全文）。证据须引原查询的字段设置。
- `time_window_truncation`：时间窗截断。证据须引该记录的出版年份与原查询时间窗。
- `source_not_indexed`：来源库未收录。证据须引来源库返回中无该标识符/标题的片段。
- `identifier_anomaly`：记录标识异常/冲突/缺失（如 DOI 冲突、无标识、撤稿）。冲突并存并标 `source_conflict`，撤稿标 `retracted_or_unavailable`。
- `unattributed`：尚无法归因，转 `pending_human`。

归因不写"数据库覆盖不足/关键词不好"这类空话；必须落到**哪一条查询、哪个字段、哪个来源、哪个时间窗**。

## 4. 证据溯源（本版强化点）

- 每条结论/数值/图元必须能指回材料的**逐字片段**：`locator`（`file` + `json path` / `offset`）+ `quote`（逐字，≤320 字符）。
- `hit_matrix` 每条含 `evidence`；`omissions` 每条含 `evidence` + `attribution.evidence`；最终 `evidence_map` 汇总为 `{evidence_id, claim, locator, quote, verbatim:true, status}`。
- 缺失证据写 `not_reported`，绝不补造 DOI、年份、命中或检索结果；无法确认标 `pending_human`。

## 5. 补检与停止规则

- 补充检索须记录新查询式、字段、来源、时间窗；`new_hits` 只能列**材料中真实存在**、且原先未命中的记录。
- `marginal_gain` 以补检前漏检数为分母：`omission_reduction = (pre - post) / pre`。
- 停止条件：连续两次补充检索无新增命中（`new_hits` 为空）即停；残余遗漏逐条说明是否可补救及所需条件。
- 严禁"换同义词重跑却无新增命中，仍报遗漏下降"。

## 6. 输入发现（先发现，再假设）

- 不假设固定文件名/键名。`inspect` 盘点 `/input` 下全部文件并识别：`case.json`（案例标识 + 可能 `output_contract`）、`task.md`、参考集（文件名含 reference/ref/gold/known/included/seed/relevant）、检索结果（crossref/europepmc/pubmed/publisher/result/search/query/hits）。
- Crossref 信封取 `message.items`，EuropePMC 取 `resultList.result`；泛型包裹键 `items/results/records/data/hits/documents/references`。
- PDF 由预装 `pdf-text-tool` 抽取为 `/app/work/<name>.txt` 侧车文件后，脚本再读文本；脚本不越权解析 PDF。
- 文本一律 `utf-8-sig` 读入并容错（BOM/坏编码/坏 JSON 降级到 `errors`/`degradations`）；单文件 >2MB 跳过并记录。

## 7. 复用边界

本方法针对"已知集 → 算覆盖 → 列漏检 → 做补检"的闭环；不替代数据库接入、全文阅读或系统综述正式纳排。参考集模板（`reference_set.json` 结构）、命中矩阵（`hit_matrix.csv`）、漏检分类表与 `coverage_report.md` 范式均可迁移到新主题，只需替换主题词与来源。
