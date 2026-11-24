# 清洗规则与 plan.json 中间表示约定

本文件是 `SKILL.md` 第 5 节的扩展说明，供 Agent 在执行时对照；规则本身仍以运行时读到的数据字典、单位/QC 规则与工单为准。

## 1. 缺失值四分类（不可互相覆盖）

| 类别 | 含义 | 典型原文 | 处置 |
|---|---|---|---|
| 未检测 not_detected | 仪器/方法未检出该分析物 | `ND`、`未检测`、`not detected` | 保留标记 `ND`，不转为 0 |
| 低于检出限 below_lod | 左删失，真实值低于 LOD，非 0 非缺失 | `<LOD`、`<0.001`、`低于检出限` | 保留 `<LOD` 或原文，不插补 |
| 录入缺失 entry_missing | 空、哨兵值、录入故障 | ``空``、`NA`、`-999`、`—` | 保留为空，计数上报 |
| 真值 0 true_zero | 实测确为 0 | `0`、`0.0` | 转为 `0` |

判定顺序：`entry_missing` → `not_detected` → `below_lod` → `true_zero` → `numeric` → `unrecognized`。`unrecognized` 保留原文、记入异常清单，**绝不静默改写**。

## 2. 异常值与重复记录

- 异常值：只用字典 + 单位/QC 规则给出的物理上下界（如 `range 0-20`）判断；落在界外只**标记**（field_model.anomaly + quality_report），默认不删除、不盖帽，需人工复核。无上下界的字段不适用异常判定，写入 `uncertainty`。
- 重复记录：按真实主键（`sample_id`、时间戳、受试者标识等）做规范化比较（去空格、大小写归一）；完全重复与近似重复都记录，明确保留哪一条及依据；被剔除的行写入 `removed_duplicates.csv`，可回退。

## 3. 编码、单位与精度

- 编码：读取顺序 `utf-8`（含 BOM）→ `gb18030` → `gbk` → `cp1252` → `latin-1`；任何一步降级都写 `degradations`。
- 单位：`plan.json` 记录每个测量字段的 `unit` 与 `convert_to`、`conversion_factor`。仅做质量/体积浓度这类**纯量纲前缀换算**（如 `mg/L` ↔ `μg/mL` 因子 1）；涉及摩尔质量的换算**不猜**，标 `pending_human`。
- 精度：浮点用 `repr` 全精度写出，不做有效位截断；千分位只在明确匹配 `1,234.5` 时剥离；科学计数法保留数值。

## 4. plan.json 结构（中间表示）

```json
{
  "schema_version": "1.1",
  "case_id": "...",
  "data_file": "材料/主表.csv",
  "delimiter": ",",
  "fields": [
    {
      "name": "glucose_mg_L",
      "role": "measure|key|text",
      "unit": "mg/L",
      "convert_to": null,
      "conversion_factor": 1.0,
      "conversion_note": null,
      "entry_missing": ["", "NA", "-999"],
      "not_detected": ["ND", "未检测"],
      "below_lod": ["<LOD"],
      "qc_min": 0.0, "qc_max": 20.0,
      "dedup_key": false,
      "evidence": {"dictionary": [{"locator": "...", "quote": "..."}],
                   "rules": [], "workorder": []}
    }
  ],
  "global": {
    "primary_key_candidates": ["sample_id"],
    "dedup": {"mode": "normalized", "keep": "first_most_complete"},
    "no_imputation": true,
    "no_population_inference": true
  }
}
```

Agent 的关键职责：把字典/规则/工单的解读**写进 `fields[].evidence` 与语义字段**，而不是只写自然语言报告。这样执行与复核都能追踪到原文。

## 5. 数据字典最小模板（供复用）

| 字段名 | 语义 | 单位 | 类型 | 缺失哨兵 | 未检测标记 | 检出限标记 | QC 上下界 | 是否主键 |
|---|---|---|---|---|---|---|---|---|
| sample_id | 样本编号 | - | text | 空 | - | - | - | 是 |
| glucose | 血糖浓度 | mg/L | numeric | -999, 空 | ND | <LOD | 0-20 | 否 |
