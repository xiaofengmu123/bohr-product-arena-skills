# 覆盖率与漏检方法规范

本文定义本 Skill 的覆盖率口径、命中判定规则、漏检分类体系与补检停止规则。
脚本中的阈值与本文件一致，改脚本阈值时必须同步改本文件。

## 1. 覆盖率口径

- **分母（denominator）**：冻结参考集中具有 `ref_id` 的记录数。参考集必须来自
  独立于检索结果的来源（综述/指南/专家清单/已有系统综述），**禁止用检索结果
  自身反推应检文献**。
- **分子（numerator）**：参考集中被任一检索结果命中的记录数。
- `coverage_rate = numerator / denominator`，`omission_rate = 1 - coverage_rate`。
  两者均保留 4 位小数，但 `denominator`/`numerator` 同时原样输出，可手工复算，
  不靠四舍五入掩盖差异。
- **冻结版本**：`reference_set.version` 是参考集规范化记录（id+title+identifier）
  的 SHA-1 前 12 位，`frozen_at` 记录冻结时刻。参考集一旦构建即视为冻结，后续
  补检只更新命中状态，不改动参考集成员。

## 2. 命中判定规则（逐条、脚本化）

对每个参考集记录，依次执行：

1. **标识符精确匹配**（`doi` 规范化后完全相等，忽略大小写、`doi.org/` 前缀、
   末尾标点）→ `hit`，`matched_by=identifier`。
2. **标题 + 第一作者 + 年份模糊匹配**：标题相似度 ≥ `TITLE_FUZZY`（0.88）且
   （年份相差 ≤ `YEAR_TOLERANCE`（1）或第一作者规范化后相等）→ `hit`，
   `matched_by=title+meta`。
3. **标题近似但不足以命中**：标题相似度 ∈ [0.70, 0.88) → `adjudicate`，转人工
   裁决。
4. 其余 → `miss`。

标题相似度 = 规范化标题（NFKC 小写、仅保留字母数字、折叠空白）后的
`difflib.SequenceMatcher.ratio()`。归一化保留 Unicode 字母数字（含中文标题），
不做 ASCII 折叠，避免把非英文标题错判为无关。

标识符冲突、撤稿、无法获取全文、无标识符的记录单独打标，不猜归属：

- `no_identifier`：无 DOI/PMID 等标识符；
- `retracted_or_unavailable`：撤稿或全文不可得；
- `duplicate_of:<ref_id>`：与某条记录重复；
- `source_conflict`：同一规范化标识符指向不同标题。

## 3. 漏检分类体系（可操作归因，禁止泛泛）

每条 `miss`/`adjudicate` 记录归入以下之一，并附 `signals` 与 `evidence`
（locator + 逐字 quote）。若无法归类，标 `unattributed`，不得写"数据库覆盖不足"
或"关键词不好"这类空话：

| 类别 | 判定信号（示例） |
|---|---|
| `query_synonym_gap` | 命中候选的标题与参考集标题存在同义表达差异，某条查询式未覆盖该表达 |
| `field_too_narrow` | 检索字段限定（如 title-only）导致记录未进入结果 |
| `time_window_truncation` | 记录年份落在查询时间窗之外 |
| `source_not_indexed` | 记录所属来源库（Crossref/EuropePMC/publisher）未收录该记录 |
| `identifier_anomaly` | 记录标识符缺失/冲突/错误，导致无法精确匹配 |
| `record_quality` | 记录元数据残缺、编码损坏或字段缺失 |

## 4. 补充检索与停止规则

- 依据漏检类别设计**新查询式/新字段/新来源/新时间窗**，逐条写入
  `supplementary_searches`，并只统计材料中真实存在、且原先未命中的新增命中。
  禁止虚构命中、禁止"换同义词重跑无新增却报下降"。
- `marginal_gain = 新增命中数 / 补检前漏检数`，同时输出 `new_hit_ref_ids`
  与 `residual_omissions`（残余遗漏的规模与原因）。
- **停止规则**：当一轮补检的 `new_hits` 为 0，或剩余漏检全部落入
  `source_not_indexed`/`identifier_anomaly`（无法靠改查询解决），即停止，并把
  残余遗漏写入 `residual_omissions` 与 `pending_human`。

## 5. 证据溯源

- `evidence_map`：每项含 `locator`（`文件名#jsonpath 或 页号/行号`）与逐字
  `quote`，指向材料原文片段。所有结论/数值必须能指回源材料；缺失写
  `not_reported`，不补造 DOI、年份或检索结果。
- 冲突信息并存并标 `source_conflict`，不抹平。

## 6. 边界

本方法不替代数据库实时接入、全文阅读或系统综述的正式纳排流程；不声明"全球
覆盖"或"最新文献"，只对给定参考集与给定检索材料做可复算的覆盖—漏检—补检闭环。
