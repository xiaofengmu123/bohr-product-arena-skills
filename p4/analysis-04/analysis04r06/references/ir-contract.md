# 中间表示契约（IR Contract）

本 Skill 与"直接写分析代码"的根本区别：**先把计划、图表、结论写成声明式 JSON（IR），再由确定性脚本执行与核对**。IR 是 result.json 的"计算依据"，两者必须一致。所有 IR 文件写入 `/app/work/`，主产物引用它们。

## 1. plan.json（分析计划）
```json
{
  "schema_version": 1,
  "case_id": "case-...",
  "assets": [
    {"locator": "materials/data.csv", "kind": "tabular", "role_hint": "data", "size_bytes": 1234}
  ],
  "fields": [
    {
      "asset": "materials/data.csv",
      "column": "density",
      "canonical": "density",
      "aliases": ["density", "密度"],
      "unit": "kg/m3",
      "role": "target",
      "dtype": "numeric",
      "n_missing": 3,
      "status": "aligned"
    }
  ],
  "rules": [
    {
      "id": "R1",
      "applies_to": "materials/data.csv",
      "action": "drop_exact_duplicates",
      "rationale": "完全相同的重复行按去重处理，保留首次出现",
      "quote": "重复记录没去重就做了分组统计",
      "evidence": {"locator": "materials/dict.md#L4", "quote": "…"}
    }
  ],
  "steps": [
    {
      "id": "step-01",
      "description": "清洗数据",
      "status": "done",
      "input": ["materials/data.csv"],
      "command": "python3 /app/work/clean.py",
      "output": ["/app/output/cleaned.csv"],
      "checks": ["行数去重前 1030 -> 去重后 1027"]
    }
  ],
  "decisions": [
    {"id": "D1", "question": "缺失值是否删除", "status": "pending_human", "note": "字典未说明"}
  ],
  "status": "partial"
}
```
- `fields.status` 只能是 `candidate | aligned | dropped | pending_human`。别名对齐后 `canonical` 填写规范列名，`unit` 从字典逐字取。
- `rules` 每条必须带 `quote`（字典原文）与 `rationale`（为什么这样做）；口径显式化是结果正确性的前提。
- `steps.checks` 写"可机器核对的行数/口径断言"，不要写"看起来对"。

## 2. facts.json（结论清单，逐字溯源）
```json
{
  "schema_version": 1,
  "facts": [
    {
      "id": "F1",
      "label": "group A 的 density 均值",
      "value": 2.41,
      "unit": "kg/m3",
      "derivation": {
        "formula": "mean(density where group=='A')",
        "inputs": ["materials/data.csv@density", "materials/data.csv@group"],
        "rule_id": "R2",
        "n": 510,
        "missing_excluded": 2
      },
      "evidence": {"locator": "materials/dict.md#L2", "quote": "density：密度，单位 kg/m3"},
      "check": "与金标准一致 / 与 profile 行数一致",
      "status": "verified | unverified | conflict"
    }
  ]
}
```
- 每个关键数值必须写 `derivation`：公式 + 输入列 + 规则 + 参与计算的 n 与排除的缺失数。这直接封堵"缺失当 0"与"未去重分组"两类静默错误。
- `evidence.quote` 必须是材料原文的逐字片段；`status=conflict` 用于两个来源不一致，两个都记录并标 `source_conflict`。
- result.json 的 `sources` 从 facts 的 `evidence` 汇总生成，保持 locator + quote 一一对应。

## 3. figure_model.json（声明式图表规格）
```json
{
  "schema_version": 1,
  "title": "density 与 strength 的关系",
  "output": "scatter.svg",
  "width": 720,
  "height": 480,
  "data": {"source": "/app/output/cleaned.csv"},
  "marks": [
    {
      "type": "scatter",
      "x": {"field": "density", "title": "密度", "unit": "kg/m3"},
      "y": {"field": "strength", "title": "强度", "unit": "MPa"},
      "color": {"field": "batch", "title": "批次"},
      "size": {"field": "n"}
    }
  ],
  "legend": {"show": true},
  "evidence": {"locator": "materials/dict.md#L3", "quote": "x 轴密度，y 轴强度"}
}
```
- `data.source` 只指数据文件与列名，**不嵌入具体数字**；数字由渲染器实时读取，改数据不重画即错不了。
- 支持的 mark：`scatter`、`line`、`bar`；`line` 按 x 升序连线。
- 渲染命令：`python3 -B scripts/main.py render-figure --model /app/work/figure_model.json --output /app/output`。产物：SVG + `figure_render.json`（含 `model_sha256`、`data_sha256`、`n_points`、evidence）。确定性：同一模型与数据重跑哈希一致。

## 4. 追溯规则
1. 结论 → facts.json 的 fact → derivation（公式+列+规则）与 evidence（locator+quote）。
2. 图表 → figure_model.json 的 marks/axes/evidence；图元单位必须等于字典单位。
3. 规则 → plan.json 的 rules，每条带 quote；改规则即改 IR 并记 change_log。
4. 冲突不隐藏：同指标两个来源不一致，两个都记并标 `source_conflict`。
5. 不虚构：缺失写 `not_reported`，无法判断写 `pending_human`，两者都不当作数值参与计算。
