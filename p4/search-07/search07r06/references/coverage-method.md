# 覆盖口径、命中规则与漏检分类（方法规范）

本文件与 `scripts/core_impl.py` 的常量一致；改动脚本时必须同步本文件。阈值常量：
`TITLE_AUTO=0.88`、`TITLE_ADJUDICATE=0.70`、`YEAR_TOLERANCE=1`。

## 1. 覆盖率口径（先冻结，后测量）

- **分母**：独立冻结的参考集 `reference_set.json` 中去重后的唯一记录数（`count`）。参考集来自综述/指南/专家清单/已有系统综述，**不得**由检索结果自身反推。
- **分子**：参考集中被判定为 `hit` 的记录数。
- `coverage_rate = numerator / denominator`；`omission_rate = 1 - coverage_rate`（denominator>0 时，保留 6 位小数，不做四舍五入掩盖）。
- **补检增益**：`gain = 新增命中数 / 补检前漏检数`。新增命中必须是参考集中真实存在、补检前为 `miss` 的记录；`new_hits` 引用不在参考集中的 ref_id 会被剔除并记入 degradations。
- **冻结语义**：reference_set 写盘后其 `version`/`content_hash` 固定；后续只允许追加 `flags`/归因，不改动记录身份，保证分母可复算。

## 2. 命中判定规则（三级）

按优先级依次执行，逐条落在 `match_model.json`：

1. **标识符精确匹配**：DOI/PMID 归一化后（去前缀、去尾点、转小写）完全相等 → `hit`，`matched_by=identifier`，置信度 1.0。
2. **标题+第一作者+年份模糊匹配**：标题相似度 `sim >= TITLE_AUTO(0.88)` 且年份差 `<= YEAR_TOLERANCE(1)`（任一方缺年份视为通过年份条件）且第一作者姓氏一致（任一方缺作者视为通过作者条件）→ `hit`，`matched_by=title_author_year`。
3. **待裁决**：`TITLE_ADJUDICATE(0.70) <= sim < TITLE_AUTO` → `adjudicate`，`matched_by=title_fuzzy`，转人工裁决；低于 0.70 → `miss`。

标题相似度 `sim = max(SequenceMatcher.ratio(归一化标题), 词集 Jaccard(归一化标题))`，取二者较大值，兼顾词序与词集合。归一化：NFKD 去重音、转小写、非字母数字转空格。

作者姓氏归一化：`"Surname, Given"` 取逗号前段，`"Given Surname"` 取末段，去非字母后小写。第一作者不一致时降级为 `adjudicate`（宁可多裁决，不擅自命中）。

## 3. 漏检分类（可操作归因，拒绝空话）

每条 miss 必须给出至少一类归因，并附 query/field/source/time_window 与逐字证据：

| id | 含义 | 补检动作 |
|-|-|-|
| `synonym_uncovered` | 查询词未覆盖同义/上位/下位表达 | 新增同义词查询式并保留旧式对照 |
| `field_too_narrow` | 检索字段限制过窄 | 放宽字段或改全字段组合 |
| `time_window_truncation` | 时间窗截断 | 扩大/去除时间窗，记录前后对照 |
| `source_not_indexed` | 来源库未收录 | 切换或补充来源库，逐库记录差异 |
| `identifier_anomaly` | 标识异常/无标识/冲突 | 以标题+作者+年份复核，冲突并存标 `source_conflict` |
| `retracted_or_unavailable` | 撤稿或全文不可得 | 保留并标注，不纳入可补检命中 |
| `unknown` | 证据不足 | 转 `pending_human`，不猜归属 |

归因证据格式：`evidence_locator`（源文件+路径）+ `evidence_quote`（材料逐字片段）；没有就写 `not_reported`。

## 4. 补充检索触发与停止规则

- 触发：存在 miss 且归因属于可补救类别（synonym_uncovered / field_too_narrow / time_window_truncation / source_not_indexed）。
- 记录：每条补检写 query、field、source、time_window、new_hits（ref_id 列表）。
- 停止：①一轮补检无任何新增命中即停止；②最多 3 轮；③所有残余遗漏均已归因并给出残余解释。
- 禁则：换同义词重跑但 new_hits 为空时，不得声称遗漏下降；`gain` 必须由 merge 从真实命中差计算。

## 5. 中间表示链与回放协议（本版核心）

`result.json` 永远由 `merge` 从以下中间产物派生，不手写主结果：

```text
plan.json ──> reference_set.json ──> match_model.json ──> result.json
        \       search_index.json      (merge 应用 attribution_model.json)
```

每个中间产物带 `content_hash` 或文件 `sha256`；`result.json.pipeline.intermediates` 记录各文件哈希。复核者：

1. 校验 `pipeline.intermediates` 哈希与 `/app/work/` 下文件一致（证明中间态未被事后改动）；
2. 重跑 `python scripts/main.py merge --input /input --output /app/output --work /app/work`，比较覆盖率与 hit_matrix 完全一致。

该设计把“分母来自哪里、命中怎么判、漏检归给谁、补检带来什么”全部落在可检查的中间态，而非一段不可复核的叙述。

## 6. 边界

- 本方法只解决“已知集对齐 + 覆盖率 + 归因 + 补检”，不替代数据库实时检索、全文阅读与系统综述正式纳排。
- 材料缺失时输出 `insufficient_evidence` 并列出缺口，不做示例填充。
- 相对“以结果定义分母”做法：本方法的分母冻结先于匹配；相对“手写主结果”做法：主结果由中间态派生，可回放。
- 符号/缩写请以材料实际字段为准，不按错误领域标签补造数据。
