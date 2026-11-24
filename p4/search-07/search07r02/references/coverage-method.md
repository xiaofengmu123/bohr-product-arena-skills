# 覆盖率口径、命中规则与漏检分类（方法说明）

本文档与 `scripts/core_impl.py` 的阈值严格一致；修改脚本阈值时必须同步修改本文。

## 1. 覆盖率口径（分母独立冻结）

- 分母 = **独立冻结参考集**的唯一记录数（`reference_set.unique_count`）。参考集只能来自综述、指南、专家清单或既有系统综述等**独立材料**，绝不来自检索结果自身。
- 分子 = 命中记录数（`status ∈ hit | hit_supplement | hit_adjudicated`）。
- `coverage_rate = numerator / denominator`，`omission_rate = 1 - coverage_rate`；均保留 6 位小数，不四舍五入掩盖。
- 冻结版本 = 对规范化后的参考集记录做 SHA-256，形如 `sha256:<前16位>`；同一材料重复运行版本一致。
- 去重键优先级：`doi`（canonical）→ `pmid` → `title|first_author_surname|year` → 内容哈希。
- 撤稿/无法获取全文/无标识记录**保留在分母内**，仅在 `record_flags` 标注，不在命中前删除——"没找到"不是"不相关"。

## 2. 命中判定规则（三级，逐条记录 `matched_by`）

| 优先级 | 规则 | 阈值 | 结果 |
|---|---|---|---|
| 1 | 标识符精确匹配 | doi（规范化小写、去 `https://doi.org/` 前缀）或 pmid 完全相等 | `hit` / `identifier` |
| 2 | 标题 + 第一作者 + 年份模糊 | `title_fuzzy=0.88`，`year_tolerance=1`；作者状态为 match/unknown/absent 且年份满足 | `hit` / `title_author_year` |
| 3 | 标题近似候选 | `title_adjudication=0.70` | `adjudicate`（转人工裁决） |
| 兜底 | 均不满足 | — | `miss` |

- 标题相似度用 `difflib.SequenceMatcher` 对归一化标题（NFKC、小写、去标点、塌缩空白）计算。
- 作者仅比较第一作者姓氏（`family/surname/last` 或姓在前格式）；一侧缺失时按 unknown 处理，不因作者缺失误判未命中。
- 年份从日期字符串中提取 4 位年份；两侧都缺时不因年份误判。

## 3. 漏检分类（可操作归因，禁止写"数据库覆盖不足/关键词不好"）

每条未命中必须落到下列之一，并附 `signals`（具体查询式/字段/来源/时间窗）与 `evidence`（locator + 逐字 quote）：

| category | 含义 | 典型信号 |
|---|---|---|
| `query_terms` | 查询词未覆盖同义/变体表达 | 具体缺哪些词、同义表达 |
| `field_restriction` | 检索字段限制过窄 | 限定 title 而漏掉 abstract/keyword |
| `time_window` | 时间窗截断 | 出版年早于检索下界 |
| `source_coverage` | 来源库未收录 | 具体库名、收录范围 |
| `identifier_anomaly` | 记录标识异常 | DOI/PMID 缺失、冲突、拼写错误 |
| `metadata_missing` | 记录缺标题/作者/年份 | 无法参与匹配 |
| `unattributable` | 材料不足以归因 | 明确写证据缺口 |

## 4. 补充检索与停止规则

- 新查询必须依据遗漏类别设计（不能只换同义词重跑原查询），并记录 `query / field / source / time_window`。
- 新增命中必须满足三点才计入：① 该 ref_id 在补检前处于 `miss` 状态；② 该 ref_id 存在于冻结参考集；③ `source_locator + quote` 能在材料中逐字核验。任一不满足则记录到 `errors/degradations`，不计入命中——禁止虚构。
- `marginal_gain = 新增唯一命中数 / 补检前遗漏数`；补检后仍漏的进入 `residual_omissions` 并逐条说明是否可补救。
- 停止条件：连续一次补检新增唯一命中为 0，或所有漏检已归因且残余遗漏均判为不可补救（`unattributable`/来源不可达）。

## 5. updates 文件 schema（`merge --updates` 的输入）

```json
{
  "adjudications": [
    {"ref_id": "R003", "decision": "hit|miss", "matched_by": "manual_title_match", "reason": "..."}
  ],
  "attributions": [
    {"ref_id": "R004", "category": "source_coverage", "signals": ["..."],
     "evidence": {"locator": "reference_set.json", "quote": "逐字片段"}}
  ],
  "supplementary_searches": [
    {"id": "supp1", "query": "...", "field": "title_abstract", "source": "results2.json",
     "time_window": "no_limit",
     "new_hits": [{"ref_id": "R004", "source_locator": "results2.json", "quote": "逐字片段"}]}
  ]
}
```

- `locator` 为 `/input` 内文件的相对路径（可带 `#record:N`），脚本按相对 `/input` 解析；`quote` 必须与文件原文逐字一致，脚本做子串核验，失败标 `evidence_unverified` 但仍保留供人工复核。

## 6. 边界（相对"以结果定义分母"的做法）

本方法的分母来自独立冻结参考集，因此覆盖率的上界是"参考集被检索命中多少"，而非"检索结果里有多少相关"。当材料中检索结果多于参考集、或参考集未覆盖题目要求的全部已知文献时，本方法不声称"全局查全率"，只在 `limitations` 中说明参考集来源与边界。参考集之外的新文献可在 `search_sessions` 中保留，但不计入覆盖率分母。
