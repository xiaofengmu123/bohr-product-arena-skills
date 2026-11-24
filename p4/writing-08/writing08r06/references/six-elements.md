# 六类要素覆盖表与模板

本文件供 Agent 在写作阶段参照。核心不是填空，而是**从材料抽取 → 对齐正文 → 发现缺口并追问**。每个事实性句子必须挂 `[S#]`，并写入 `/app/output/evidence_map.json`。

## 1. 六类要素覆盖表（结果完整性）

| # | 要素 | 必须写出的内容 | 缺口标注 |
|-|-|-|-|
| 1 | 方法步骤 | 每步：输入→操作→输出→判断条件；多步不得压成一句 | 缺判断条件写 `pending_human` |
| 2 | 实验设置 | 训练/验证/测试分开；硬件、软件版本、运行环境、随机种子、重复次数 | 版本不明写 `not_reported` |
| 3 | 参数与来源 | 参数表含名称、取值、单位、作用对象、来源；正文数值与表对齐 | 无来源写 `pending_human` |
| 4 | 数据集与划分 | 名称、版本、规模、预处理、划分比例、随机方式、各子集用途 | 版本不明写 `not_reported` |
| 5 | 对照实验 | 基线名称与版本、是否同预算、评价指标、统计方式 | 不可比只写可复核的比较，不下断言 |
| 6 | 可复现与限制 | 复现清单（代码版本、环境、命令、数据获取方式）；限制落到具体失效场景与适用范围 | 套话限制删除，改写具体场景 |

## 2. 参数表模板（parameters_sources）

正文参数表每一行必须能回溯到 source。示例结构（示例，非正式案例）：

```markdown
| 参数 | 取值 | 单位 | 作用对象 | 来源 |
|---|---|---|---|---|
| learning_rate | 1e-4 | — | Adam 优化器 | config.yaml#L12 [S1] |
| batch_size | 32 | 样本/步 | 训练循环 | train.py#L40 [S2] |
```

规则：
- 取值、单位、有效数字与材料逐字一致，不改写。
- 材料没有的数值写 `not_reported`，并加一条 `pending_human`。
- 正文叙述中的数值必须与表中一致；不一致立即 `source_conflict` 或修正。

## 3. 证据映射模板（evidence_map.json）

```json
{
  "S1": {
    "locator": "config.yaml#L12",
    "quote": "lr: 1e-4",
    "type": "parameter",
    "role": "正文参数表 learning_rate 行的来源"
  }
}
```

规则：
- locator 形如 `materials/<file>#L<line>` 或 `paper.pdf#p<page>`；PDF 按页，文本按行。
- quote 必须是材料原文的逐字片段，不概括、不转写。
- `role` 说明该证据支撑正文的哪一处，使复核者能双向定位。

## 4. 数值保全（preserved_facts.json）

```json
{
  "facts": [
    {
      "value": "1e-4", "unit": null,
      "statistic": null,
      "quote": "lr: 1e-4",
      "locator": "config.yaml#L12",
      "type": "parameter"
    }
  ]
}
```

规则：数值、单位、统计量（mean±sd、n、p 值）逐字复用；限定词强度（显著/可能）不升不降。

## 5. 中间表示（本包的设计取舍）

本包与"填空式模板"的差异在于引入三个结构化中间产物，使"抽取—对齐—追问"可复核、可增量：

| 中间产物 | 路径 | 作用 |
|-|-|-|
| `plan.json` | `/app/work/plan.json` | 六类要素计划：每类含 required_fields、status、evidence_ids、gaps；图模型 figure_model；预算追踪 budget |
| `fact_model.json` | `/app/work/fact_model.json` | 候选事实模型：type/name/value/unit/applies_to/locator/quote/confidence/status，与 conflicts/gaps 分开 |
| `validation_report.json` | `/app/work/validation_report.json` | 契约/证据/路径的逐项校验结果，收口依据 |

要素状态机：`planned → drafted → verified → gap`。收口前所有要素必须达到 `verified`，否则标 `gap` 并写入 `pending_human`。

## 6. 图模型（figure_model，仅任务要求图时使用）

每个图/表在 `plan.json.figure_model` 里登记：

```json
{
  "id": "fig1",
  "kind": "plot",
  "caption": "……",
  "data_source": "results.csv#L1-L20",
  "axis": {"x": "…", "y": "…"},
  "status": "planned"
}
```

规则：图的数据来源必须指向 locator；无法确定数据来源的图一律标 `pending_human`，不画假图。图产物要同时给可编辑源（SVG）与真实渲染（PDF/PNG）预览。

## 7. 诚实与冲突规则

- 冲突信息不隐藏：同一指标两个来源不一致，两条都记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` / `not_reported` / `MATERIAL GAP`。
- `status` 诚实：`ok`（成稿齐全且证据齐）> `partial`（缺少量人工确认）> `insufficient_evidence`（材料不足）> `input_error`（输入无法解析）。

## 8. 要素抽取的候选事实类型（inventory 阶段产出）

`fact_model.json` 中每项候选事实含 `type`，Agent 按类型归入六类要素：

| type | 含义 | 归入要素 | 复核要点 |
|-|-|-|-|
| metric | 数值+单位 | 参数/实验设置 | 单位与有效数字逐字保留 |
| seed | 随机种子 | 实验设置 | 区分 train/val/test 是否共用种子 |
| learning_rate / batch_size / epoch | 训练超参 | 参数与来源 | 与配置文件核对 |
| version | 版本号 | 参数/数据集/基线 | 软件与数据集版本分开记录 |
| split_ratio | 三路划分比例 | 数据集与划分 | 确认随机方式与各子集用途 |
| path | 文件路径 | 复现清单 | 区分代码/数据/配置路径 |
| command | 命令行 | 复现清单 | 完整保留，不改写 |

候选事实只是定位提示，**必须逐条回到原文核实**后才进入成稿；核实失败的候选移入 `gaps`。

## 9. 最小调用示例（示意，非正式案例）

以下演示接口与完整流程，**示意输出已标明为示例，不冒充任何正式案例结果**。

输入 `/input/case.json`（示意）：

```json
{
  "id": "demo-case",
  "output_contract": {
    "manuscript": "string",
    "evidence_map": "object",
    "status": "string"
  }
}
```

步骤（示意）：

```bash
python3 scripts/main.py skeleton --input /input --output /app/output --work /app/work
python3 scripts/main.py inventory --input /input --output /app/output --work /app/work
# ……Agent 写 manuscript.md、evidence_map.json、preserved_facts.json……（示意阶段省略）
python3 scripts/main.py validate --input /input --output /app/output --work /app/work
```

skeleton 后的 `/app/output/result.json` 顶层字段（示意）：

```json
{
  "manuscript": null,
  "evidence_map": null,
  "status": "partial",
  "case_id": "demo-case",
  "artifacts": [],
  "sources": [],
  "uncertainty": {},
  "change_log": [],
  "pending_human": [],
  "runtime": { "budget_s": 900, "model_requests_budget": 64 }
}
```

最终成稿完成后（示意），`status` 依实况更新为 `ok` 或 `partial`，`manuscript` 写入可编辑稿件路径。

## 10. 追问清单模板（pending_human）

需要交作者确认时，每条按下面结构写，避免空泛：

```json
{
  "id": "Q1",
  "element": "experiment_setup",
  "locator": "config.yaml#L12",
  "question": "训练与验证是否共用 seed=42？",
  "why": "日志未出现 val seed，无法确认条件一致性"
}
```

规则：每条追问必须指向具体 locator 与具体缺口，不含"请补充细节"式空话。

## 11. 正文—表格对齐校验规则（结果正确性）

第 4 步增量改进时逐条执行，防止"正文一个数、表格另一个数"：

1. 把正文中出现的关键数值列成 `(数值, 单位, 语义)` 三元组。
2. 与参数表逐行比对：数值、单位、有效数字三者全等才算对齐；语义相同但数值不同标 `source_conflict`。
3. train/val/test 三套条件分别成表或分列，禁止合并成一行导致条件串台。
4. 每个数值的语义（如"batch_size 作用于训练循环"）必须在 `preserved_facts.json` 有对应 quote。
5. 无法从材料确认语义的数值，语义写 `not_reported` 并进 `pending_human`。

## 12. 证据双向定位（可核验性）

复核者应能从两个方向查证：

- **正向**：正文 `[S#]` → `evidence_map.json["S#"]` → locator → 材料原文。
- **反向**：`fact_model.json` 某候选 → 是否被采用 → 正文哪一句（通过 `evidence_map` 的 `role` 字段）→ 是否核实。

任一向断裂都说明可核验性不足，收口前必须补齐 `role` 或删去未核实的候选。

