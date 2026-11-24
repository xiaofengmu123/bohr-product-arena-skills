# 证据溯源契约（evidence_map）

## 1. 目的

让 result.json 里每一个关键结论都能被独立复核：从结论反查到源文件中的**逐字片段**，机器可校验，冲突不隐藏。它是比顶层 `sources` 更细的一级结构——`sources` 只记录"这个数来自哪里"，`evidence_map` 记录"这个数如何由哪些逐字片段推导而来"。

## 2. locator 语法

```
<相对路径>[#<锚点>]
```

- 相对路径：相对 `/input` 的文件路径，例如 `materials/iris.csv` 或 `case.json`。
- 锚点：自由文本，建议写法 `row1-150:col sepal_length`、`p3`、`table1`。
- 机器校验只取 `#` 之前的文件部分定位文件；`#` 之后供人类定位。

## 3. quote 规则

- quote 必须是源文件的**逐字节子串**，`evidence verify` 会据此机器校验；校验失败记 `quote_not_found_in_source`。
- 优先引原始单元格值、原始句子；派生值（均值、p 值等）必须在 `derivation` 里写明算式、样本量与随机种子。
- 无法逐字引用（图表像素、SQLite 二进制、PDF 未解析层）标相应 `evidence_kind` 并转 `pending_human`。
- 二进制源（`.sqlite`/`.pdf`）不参与逐字节校验，校验结果记 `binary_source_not_verifiable`，但 locator 仍必须写清表/页/列。

## 4. 字段

`evidence_map` 顶层：

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | 固定 `"1.0"` |
| `claims` | array | 结论列表，每条见下表 |

claim 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `claim_id` | string | 本 claim 的稳定标识（`c001` 等） |
| `claim_type` | string | `statistic`/`figure_element`/`query_result`/`cleaning_rule`/`field_alignment`/`document_quote`/`derived_value`/`other` |
| `conclusion` | string | 该结论的完整表述，含数值、单位、样本量 |
| `confidence` | string | `computed`/`inferred`/`reported` |
| `status` | string | `verified`/`pending_check`/`source_conflict`/`pending_human` |
| `sources` | array | 每条 `{locator, quote, quote_hash, evidence_kind}` |
| `derivation` | object | 计算方法、随机种子、命令行、口径 |
| `source_file` | string | 可选；覆盖 locator 中的文件定位 |

硬规则：每个 claim 至少一条 source；每条 source 必须同时带非空 `locator` 与逐字 `quote`。缺任一者，`validate` 判 fail 并转 `pending_human`。

## 5. 冲突与缺口

- 同一指标两个来源不一致：两条 claim 都登记，各标 `source_conflict`，在 `uncertainty` 里说明差异与处理口径，不隐藏、不合并。
- 材料缺失/无法判断：登记已读到的证据后转 `pending_human`，在 `pending_human` 数组里逐条列出缺口与需要人类确认的动作。

## 6. 校验命令

```bash
# 登记一条结论
python3 -B scripts/main.py evidence add --result /app/output/result.json \
  --claim-json '{"claim_id":"c001","claim_type":"statistic","conclusion":"…","locator":"materials/iris.csv#row1:col0","quote":"5.1"}'
# 机器校验所有 quote 是否为源文件逐字节子串
python3 -B scripts/main.py evidence verify --input /input --result /app/output/result.json --output /app/output
# 校验 result.json 契约完整性与证据完整性
python3 -B scripts/main.py validate --input /input --output /app/output
```

校验产物：`/app/output/evidence_verify_report.json`、`/app/output/validation_report.json`。
