# 证据溯源协议（locator + quote）

科研清洗的每一个关键结论都必须能指回材料的**逐字片段**，而不是“我判断是这样”。
本协议规定证据如何记录、冲突如何标注、缺证如何诚实降级。

## 1. locator 的写法

`locator` 是“能定位到原文片段”的稳定路径，推荐用 `文件名#锚点` 或行列定位：

- 材料文本：`materials/data_dictionary.md#字段定义-conc` 或 `materials/rules.txt#L12`
- 表格：`samples.csv#sheet0!B3`（或 `samples.csv#R0006/conc_mg_l`）
- 任务：`task.md#缺失值处置`
- PDF 解析产物：`papers/xxx.pdf.txt#p3`

规则：locator 必须能由第三方按它找到同一片段；找不到的写法（如“某处”“大概”）不合格。

## 2. quote 的写法

`quote` 是原文的**逐字**摘录（可含必要的省略号，但不得改写措辞、不得翻译后冒充原文）。
每个 `sources` 条目：

```json
{"locator": "materials/rules.txt#L12",
 "quote": "conc 有效范围 0–100 μg/mL，超限标记为异常",
 "note": "用于字段 conc 的异常上界"}
```

## 3. evidence_map 与 change_map 的分工

- `evidence_map.json`：结论级证据。每个关键结论/数值/图元一条 `{locator, quote, note}`。
- `change_map.csv`：单元格级审计。每行一条变更：`row_id, column, original_value,
  cleaned_value, action, reason, rule_locator, quote`。`quote` 指向该变更依据的原文。

两者共同保证：任何清洗动作都可被“按记录复跑并逐字段核对”。

## 4. 冲突信息

同一指标两个来源不一致时，**两个都记录**，并加 `note: "source_conflict"`，在
`uncertainty` 中写明冲突与未决点，不得只保留“看起来更合理”的那一个。

## 5. 缺证与不编造

- 找不到原文依据的结论，写 `not_reported` 或 `pending_human`，不写数值。
- 无法解析的 PDF/图片，如实记录解析失败（`errors`/`degradations`），不按标签臆测内容。
- 报告数字必须由脚本从清洗表重算；凡自述的“增益/分数/金标准”无运行证据支撑的，
  一律视为未证实，不得写入正式结论。
