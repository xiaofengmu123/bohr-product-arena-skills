# 证据溯源 Schema（evidence_map.json，v2.0）

本文件定义 `evidence_map.json` 的结构、locator 语法与逐字 quote 核验规则。目标：每个关键结论都能指回材料的逐字片段，且能被 `--phase selfcheck` 自动复核。

## 1. 顶层结构

```json
{
  "schema_version": "2.0",
  "case_id": "challenge-…",
  "assertions": [ … ],
  "field_index": { "conc_mgL": ["A0001", "A0004"] },
  "counts": { "total": 12, "by_kind": {…}, "by_field": {…} }
}
```

## 2. 单条断言（assertion）

```json
{
  "id": "A0001",
  "kind": "missing_classification | field_summary | duplicate | outlier | conversion | field_semantics | report_number | source_conflict",
  "field": "conc_mgL",
  "record": { "row_id": 3 },
  "assertion": "该单元格为低于检出限（左删失），不是缺失也不是 0。",
  "evidence": {
    "locator": "table.csv::r3::c[conc_mgL]",
    "quote": "<0.01",
    "material": "table.csv",
    "material_sha256": "…"
  },
  "rule_ref": "references/cleaning-rules.md#missing-classes",
  "basis": [ { "locator": "qc_rules.txt::line12", "quote": "LOD=0.01 mg/L" } ],
  "confidence": "high | medium | low",
  "uncertainty": "…",
  "change_log_id": "C002"
}
```

字段说明：

- `assertion`：一句话结论（可读、可核验）。
- `evidence`：该结论的唯一来源。`locator` 定位位置，`quote` 是**逐字原文**，`material` 是被引文件名，`material_sha256` 是可复核的原文指纹。
- `basis[]`：支撑证据的次级来源（规则/字典原文）；冲突时把两条 `basis` 并列，并在 `kind` 或 `assertion` 标 `source_conflict`。
- `confidence`：`high`（可直接验证）/ `medium`（合理推断）/ `low`（依赖 Agent 判断或材料不完整）。

## 3. locator 语法（行列均 1 基）

| 场景 | 语法 | 示例 |
|---|---|---|
| 单元格 | `材料::r行::c[列名]` | `table.csv::r3::c[conc_mgL]` |
| 字段级 | `材料::field::列名` | `table.csv::field::conc_mgL` |
| 重复组 | `材料::duplicate-group::键片段` | `table.csv::duplicate-group::S-01` |
| 规则/字典行 | `材料::line行号` | `qc_rules.txt::line12` |
| 段落定位 | `材料::p段落` | `notes.md::p3` |

## 4. 逐字 quote 核验规则

1. `quote` 必须是**材料原文的连续子串**，不得转述、改写、四舍五入或重新排版；数值 `12.3` 与 `12.30` 不是同一个 quote。
2. 归一化仅允许：`\r\n`/`\r` → `\n`，以及 quote 首尾空白裁剪。不允许大小写折叠、全半角转换、空白折叠后仍称"逐字"。
3. 单元格为空的结论：没有逐字文本可引，改用字段级 `field_summary`（quote=列名，列名本身是原文），不得伪造空串为 quote。
4. 派生的数字（如换算结果、缺失率）不是原文 quote，必须用 `report_number`/`conversion` 断言并附计算规则，其来源指向 `cleaned_data.csv` 与 `data_profile.json`，不得标注为材料原文。
5. 核验失败的处理：`--phase selfcheck` 把核不过的断言列入 `degradations`，Agent 必须修正 quote 或把 `confidence` 降级并写明理由；**不得假装已核验**。

## 5. 冲突并列

同一指标两个来源不一致时，两条证据都保留，各自 locator+quote，并在断言 `kind` 标记 `source_conflict`；`assertion` 写明"来源 A 与来源 B 不一致，均保留"。不允许静默取一。

## 6. 与 result.json 的关系

- `result.json.sources`：每份材料一条，`locator` + 首行逐字 `quote` + `note`，用于快速定位。
- `result.json.change_log`：`time/step/action`，每条可选的 `change_log_id` 被 assertion 引用，形成"动作↔证据"双向链。
- `evidence_map.json` 是细粒度断言层；两者编号不冲突（`A*` vs `C*`）。
