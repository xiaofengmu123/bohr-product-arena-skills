# evidence_map 证据链 schema（本版核心）

每条关键结论（数值、统计量、图元、清洗决策、标注）必须生成一条记录；`evidence verify`
会打开 locator 读取实际文本，把 `quote` 与 `observed` 做逐字比对。

## 1. 记录字段

```json
{
  "id": "ev-001",
  "claim": "sepal_length 均值 = 5.843",
  "claim_type": "value",
  "locator": "iris.csv#r1c1",
  "quote": "5.1",
  "observed": "5.1",
  "kind": "value",
  "derivation": "列 sepal_length 全部 150 行的算术平均",
  "rule": "缺失 0 行；未剔除离群",
  "confidence": "certain",
  "verified": true,
  "conflicts": []
}
```

- `claim_type`：`value | statistic | figure_element | decision | annotation | other`。
- `confidence`：`certain | inferred | unverified`（由证据支持强度决定，不虚标）。
- `verified`：`evidence verify` 的机械复核结果；`true` 表示 quote 逐字出现在 locator 处。
- `observed`：复核时读到的实际文本；未命中时保留实际文本供人工比对，绝不删记录。
- `conflicts`：同一指标存在不一致来源时列出 `{locator, quote, note}`，并标 `verified=false`、
  `confidence=unverified`、`note=source_conflict`。

## 2. locator 语法（相对 /input）

| 形式 | 含义 | 复核时读取 |
|---|---|---|
| `data.csv#L12` | 第 12 行（含表头，1 起） | 该行原文 |
| `data.csv#r12c3` | 第 12 个数据行（不含表头）第 3 列（1 起） | 该 CSV 单元格 |
| `notes.md#p3` | 第 3 个段落（按空行分段，1 起） | 该段原文 |
| `data.db#table=iris&rowid=12` | 表 iris 的 rowid=12 行 | 该行序列化为 JSON |

CSV 按 `utf-8-sig` 优先解码；`r<c>` 中的行号不含表头（第 1 行为表头），列号为 1 起。

## 3. 机械复核语义

- `quote` 是源文件中的**逐字片段**；`evidence verify` 用 `quote in observed` 判定命中，
  `quote == observed.strip()` 为完全相等。
- 未命中：`verified=false`，`observed` 写回实际读取文本，并把原因写入 `check` 的 issues；
  不凭印象改 quote、不删记录。
- 定位失败（文件缺失/坏编码/行越界/SQLite 表不存在）：`verified=false`、`observed=null`，
  记录错误到 `errors` 或 `check` 输出。

## 4. figure_map ↔ evidence_map

`figure_map` 每项 `{artifact, element, evidence_id, text}` 把图件一个视觉元素绑定到一条证据：

```json
{"artifact": "figure1.svg", "element": "x_axis_label", "evidence_id": "ev-004", "text": "Sepal length (cm)"}
```

`check` 会校验：每个 `evidence_id` 必须存在于 `evidence_map`；指向不存在的证据判 FAIL。

## 5. 与 sources 的关系

- `sources` 是契约兼容的简表，每项 `{locator, quote, kind}`（由 `evidence add` 自动同步）。
- `evidence_map` 是细粒度主表，保留派生口径（derivation/rule）、置信度与复核状态。
- 两者指向同一事实时 locator+quote 必须一致；不一致视为 `source_conflict`。
