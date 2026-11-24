# 一致性规则与可复用模板

本文件给出脚本所执行规则的判定口径，以及可直接复用的模板。文字由模型填写，脚本按下列规则机械核查。

## 1. 术语一致性规则

- 同概念只能有一个规范写法（canonical）；首次出现写 `全称（Abbreviation，缩写）`，其后只用缩写或全称其一，不混用。
- 脚本 `checks.term_variants` 按"字母数字 token 去掉连字符/下划线/大小写后归一"分组，报告同一 key 的多种表层写法（如 `RNN`/`rnn`、`pre-training`/`pretraining`）。报告中出现即视为待统一候选，由模型裁决后填入术语表。
- 术语表模板（写进 `term_table` 契约字段或 `term_table.md`）：

```json
[{"term": "Recurrent Neural Network", "canonical": "RNN", "variants": ["rnn"], "first_locator": "materials/ch3.md#L1"}]
```

## 2. 编号与交叉引用规则

- 图/表/式/章节编号连续、从 1 起、不重复；正文引用（见图 X / 见表 X / 式 (X)）必须指向已定义的编号。
- 脚本 `checks.numbering_report` 提取定义编号与显式交叉引用，报告 `crossref_*_undefined`、`numbering_*_gap_after`、`numbering_*_starts_at` 三类问题。
- 改动一处编号后，用 `--mode check` 重跑，找出所有受影响段落再统一修改（满足"改动一处后能找出受影响段落"）。

## 3. 引用规则

- 每条论断绑定到具体文献或材料；数字、术语、图表、关键论断逐条可回溯。
- 脚本 `checks.citation_report` 提取 `[n]` 与 `(作者, 年)` 引用，报告缺失编号 `citation_gap_missing_refs`。
- 引用不存在或对不上结论时，不补造：标 `not_found` 并写入 `uncertainty`。

## 4. 证据映射规范

- 每个关键结论/数值/图元必须能指回材料逐字片段，格式：

```json
{"claim": "RNN 模型 F1 达到 0.87",
 "locator": "materials/ch3.md#para3",
 "quote": "rnn 模型在测试集上 F1 达到 0.87"}
```

- `locator` 用 `文件路径#段落号`；`quote` 是逐字引用，不得改写。
- 脚本 `checks.locate_evidence` 可对给定 claim 在材料中做子串定位，辅助人工/模型确认 quote。
- 同一指标两个来源不一致时，两个都记录并标 `source_conflict`。

## 5. 改动记录（change_log）模板

```json
{"time": "2026-11-24T10:00:00+08:00", "action": "rename_term",
 "target": "rnn", "before": "rnn", "after": "RNN",
 "reason": "统一为首次定义的缩写", "locator": "materials/ch3.md#para4"}
```

## 6. 章节规划模板（用于立得住）

对每个待写/改写章节一行：`章节号 | 要回答的问题 | 需要的证据 | 预计篇幅 | 与前后章关系`。写前先填，避免章节重复或断裂。
