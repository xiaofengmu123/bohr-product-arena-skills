# 三层证据图规范（evidence-provenance-v3）

本文件是 `evidence_map.json` 的权威结构定义与写作模板。与平面 `[S#] → quote` 映射相比，本版把证据拆成 **claim → fact → evidence** 三层，使每条结论都能独立复核，并显式区分"材料逐字事实"与"作者推理"。

## 1. 三层模型

```text
claims（句子层）──1:N──> facts（原子断言层）──1:N──> evidence（出处层）
   S12                   F007                        {locator, quote, ...}
```

- **claim**：`manuscript.md` 里一个事实性句子，用 `[S#]` 锚定。
- **fact**：句子里拆出的原子断言（一个数值、一个参数、一个数据集名、一条命令）。
- **evidence**：该断言的逐字出处，可多条（多来源并列，冲突时全部保留）。

## 2. 字段定义

### claims（数组）

| 字段 | 类型 | 说明 |
|---|---|---|
| `sentence_ref` | str | `S1`、`S12`…与正文 `[S#]` 一致 |
| `text` | str | 句子全文（去除 `[S#]` 标记） |
| `fact_ids` | list[str] | 该句依赖的 fact |
| `coverage` | str | `full`：每处事实都有 fact；`partial`：部分成分无出处 |

### facts（数组）

| 字段 | 类型 | 说明 |
|---|---|---|
| `fact_id` | str | `F001`…全局唯一 |
| `category` | str | 六类之一：`method_step / experiment_setup / parameter / dataset / baseline / reproducibility_limitation` |
| `claim` | str | 原子断言文本 |
| `kind` | str | `verbatim`（材料逐字）/ `numeric`（数值）/ `derived`（换算）/ `inference`（作者推理） |
| `evidence` | list | 出处数组，见下 |
| `status` | str | `verified / partial / gap / conflict / pending_human` |
| `normalized` | obj? | 数值类：`{value, unit, raw, precision}`，`raw` 保留原文串 |
| `conflicts` | list? | 冲突出处：`[{locator, quote, description}]` |

### evidence（对象数组，属于 fact）

| 字段 | 类型 | 说明 |
|---|---|---|
| `locator` | str | `materials/<file>#L<line>` 或 `<paper>.pdf#p<page>` |
| `quote` | str | 材料逐字片段，不改字、不改单位、不改有效数字 |
| `quote_type` | str | `verbatim / page_snippet / table_cell / figure_caption` |
| `match_method` | str | `exact`（逐字）/ `normalized`（仅空白/大小写归一）/ `derived`（换算）/ `paraphrase`（改写，需谨慎） |
| `confidence` | str | `high / medium / low` |
| `note` | str? | 补充说明（换算方式、歧义、页码近似等） |

### 索引

- `claim_to_fact`：`{ "S12": ["F007", "F008"] }`
- `fact_to_claim`：`{ "F007": ["S12"] }`
- `locator_to_fact`：`{ "materials/config.yaml#L3": ["F007"] }`（可选，便于快速核对）

## 3. 最小示例

```json
{
  "schema": "evidence-provenance-v3",
  "claims": [
    {"sentence_ref": "S12", "text": "模型采用 Adam 优化器，学习率 1e-4。",
     "fact_ids": ["F007"], "coverage": "full"}
  ],
  "facts": [
    {"fact_id": "F007", "category": "parameter",
     "claim": "learning rate = 1e-4",
     "kind": "verbatim",
     "evidence": [
       {"locator": "materials/config.yaml#L3", "quote": "lr: 1e-4",
        "quote_type": "verbatim", "match_method": "exact", "confidence": "high"}
     ],
     "status": "verified",
     "normalized": {"value": 0.0001, "unit": null, "raw": "1e-4", "precision": null}}
  ],
  "claim_to_fact": {"S12": ["F007"]},
  "fact_to_claim": {"F007": ["S12"]}
}
```

## 4. kind 判定规则（防"补数"的关键）

| kind | 何时使用 | 例子 |
|---|---|---|
| `verbatim` | 材料出现原文，照抄 | 配置 `lr: 1e-4` |
| `numeric` | 材料出现数值但需要归入参数表 | 日志 `loss 0.0321` |
| `derived` | 由材料数值换算（单位/比例） | 百分比→小数，需在 note 写换算 |
| `inference` | 作者从材料推断，材料未明说 | "因此设备可能为 8×V100"——绝不写成材料事实 |

正文里 `inference` 类结论必须加限定词（"可能/推测"），且 `status=pending_human` 交由作者确认。

## 5. 六类要素覆盖表

| 要素 | 必须覆盖 | 常见缺口 |
|---|---|---|
| 方法步骤 | 输入→操作→输出→判断条件，逐条编号 | 多步压成一句、缺停止条件 |
| 实验设置 | train/val/test 分开；硬件、软件版本、环境、种子、重复次数 | 条件串台、种子缺失 |
| 参数与来源 | 名称、取值、单位、作用对象、来源（配置/日志/作者确认） | 正文与表不一致、来源缺失 |
| 数据集与划分 | 名称、版本、规模、预处理、划分比例、随机方式、子集用途 | 版本不明未标 `not_reported` |
| 对照实验 | 基线名称+版本、是否同预算、评价指标、统计方式 | 比较成为断言而非可比结论 |
| 复现与限制 | 代码版本、环境、命令、数据获取方式；限制指向具体失效场景 | 限制写成套话 |

## 6. 参数表模板

| 参数 | 取值 | 单位 | 作用对象 | 来源（locator） |
|---|---|---|---|---|
| learning_rate | 1e-4 | — | Adam 优化器 | `materials/config.yaml#L3` |
| batch_size | 32 | 样本 | 训练 | `materials/config.yaml#L4` |
| seed | 42 | — | 全部随机过程 | `materials/config.yaml#L5` |

无来源或数值缺失时写 `not_reported`，并在 `pending_human` 追问，不得留空装作已知。

## 7. 冲突与缺口标注

- `source_conflict`：同一指标两个来源不一致时，`status=conflict`，`evidence` 保留两个出处，`conflicts[]` 写清分歧。
- `MATERIAL GAP`：正文该写但材料没有，段落内显式标注，`status=gap`。
- `pending_human`：需作者确认的项，写入 `result.json.pending_human` 与对应 fact 的 `status`。
- `not_reported`：材料未给版本/种子/环境等，正文写该词，不编数值。
