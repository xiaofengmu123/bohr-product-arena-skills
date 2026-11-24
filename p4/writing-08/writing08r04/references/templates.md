# 模板与六要素覆盖表

本文件是 SKILL 正文第 6 节的执行模板，供 Agent 在写初稿时逐项对照。所有模板均为可复用的资源，他人可按此复用；示例数据一律标记为示例，不冒充正式结果。

## 1. 六要素覆盖表（结果完整性）

| 要素 | 必须写出的字段 | 证据要求 | 常见缺口 |
|---|---|---|---|
| 方法步骤 methods_steps | input / operation / output / condition | 每步一个 locator | 多步压成一句；缺判断条件 |
| 实验设置 experiment_setup | hardware / software_versions / environment / random_seed / repetitions / train_val_test_separated | 训练、验证、测试条件各自独立溯源 | train/val/test 条件串台 |
| 参数与来源 parameters | name / value / unit / object / source | 正文数值与参数表逐字一致 | 补数；单位丢失；来源未标 |
| 数据集与划分 dataset | name / version / size / preprocessing / split_ratios / randomization / subset_usage | 版本与规模逐字 | 版本不明却写死 |
| 对照实验 comparison | baseline_name / baseline_version / same_budget / metrics / statistics | 同预算与统计方式显式 | 把比较写成断言 |
| 可复现与限制 reproducibility_limits | code_version / environment / commands / data_access / failure_scenarios / scope | 命令可粘贴执行 | 限制写成套话 |

## 2. 参数表模板

```markdown
| 名称 | 取值 | 单位 | 作用对象 | 来源 | 判定 |
|---|---|---|---|---|---|
| batch_size | 32 | — | 训练循环 | config.yaml#L12 | verified |
| learning_rate | 1e-4 | — | Adam 优化器 | config.yaml#L13 | verified |
| dropout | not_reported | — | 模型 | — | unverified |
```

规则：`取值` 与单位逐字复用材料；无来源写 `not_reported`；`判定` 必填（verified/inferred/unverified/conflict）；正文与表格数值必须一致。

## 3. 证据映射条目模板（evidence_map.json）

```json
{
  "S1": {
    "locator": "materials/config.yaml#L12",
    "quote": "batch_size: 32",
    "verdict": "verified",
    "confidence": "high",
    "uncertainty_note": ""
  },
  "S2": {
    "locator": "materials/paper.pdf#p3",
    "quote": "We set the learning rate to 1e-4.",
    "verdict": "inferred",
    "confidence": "medium",
    "uncertainty_note": "原文为文字描述，数值 1e-4 已与 config.yaml#L13 一致"
  }
}
```

- locator 形如 `materials/<file>#L<line>`（文本）或 `materials/<file>#p<page>`（PDF）。
- quote 必须是逐字片段，不得改写；verdict 与 confidence 必须与正文强度一致。

## 4. 缺口登记模板（gap_register.json）

```json
{
  "six_elements": {
    "dataset": {
      "status": "partial",
      "evidence": ["S4", "S5"],
      "gaps": ["划分比例未见", "数据版本 not_reported"]
    }
  }
}
```

规则：`status` 只取 `found | partial | not_found`；`not_found` 必须进入 `uncertainty` 与 `limitations`，不得静默跳过。

## 5. uncertainty 与 limitations 示例（示例，非正式结果）

```json
{
  "uncertainty": {
    "summary": "GPU 型号与数据集划分比例未见于材料。",
    "unverified_claims": ["S7"],
    "conflicting_claims": [
      {"ids": ["S4", "S9"], "topic": "batch_size", "values": ["32", "64"],
       "sources": ["config.yaml#L12", "run.log#L5"]}
    ],
    "low_confidence_claims": ["S11"],
    "version_gaps": ["torch 版本 not_reported"],
    "seed_gaps": ["验证集随机种子未见于材料"]
  },
  "limitations": [
    {"id": "L1", "scope": "训练可复现性", "condition": "材料未报 GPU 型号与 CUDA 版本",
     "failure_scenario": "读者无法确认同硬件下的运行时行为", "mitigation": "pending_human P3 请作者补充"}
  ]
}
```

## 6. pending_human 条目模板

```json
{"id": "P1", "missing_token": "val_split_ratio",
 "location": "materials/README.md#L20（提到划分但未给比例）",
 "blocking_reason": "影响数据集与划分一节的可复现性，缺比例无法复现验证集规模"}
```
