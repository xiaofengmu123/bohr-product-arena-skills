# 证据溯源契约（evidence_map 规范）

本文件定义 `evidence_map.json` 的定位语法、条目分类与逐字引用规则。目标：任何评审者都能从一条结论沿 `locator` 找到材料，再用 `quote` 核对原文是否真的支持该结论，而不需要信任 Agent 的转述。

## 1. locator 语法

locator 统一为 `scheme:path#fragment` 形式，scheme 与 fragment 约定如下：

| scheme | 含义 | fragment 示例 |
|---|---|---|
| `task` | 任务文本 `task.md` | `task.md#L23`（行号）或 `task.md#§输出要求`（节标题） |
| `case` | 元信息 `case.json` | `case.json#output_contract` 或 `case.json#case_id` |
| `data` | 表格数据文件 | `data:iris.csv#col=sepal_length#rows=1..50` |
| `db` | SQLite 表 | `db:data.db#table=measurements#col=age#rows=1..1030` |
| `work` | 本 Skill 中间产物 | `work:stats.json#group_tests.sepal_length.p` |

行号区间 `rows=1..50` 表示清洗后的行范围；对原始表则写 `src_rows=`。列名含空格时用 `%20` 转义。

## 2. claim_type 分类（比单一“来源”更细）

| 类型 | 定义 | quote 必须是什么 |
|---|---|---|
| `observation` | 直接从材料读到的原始值/事实 | 数据单元格原文，或任务/字典中的原文句子 |
| `inference` | 由公式/规则从 observation 推出 | 计算所依赖的原始值片段 + method 写明公式 |
| `rule` | 清洗/筛选/分组的规则及依据 | 任务或数据字典中支撑该规则的原文句 |
| `artifact` | 报告段落、表格、图元背后的底层数据 | 该图元对应的 stats.json 原始数值 |
| `conflict` | 两来源对同一指标不一致 | 两个来源各自的原文，全部保留 |
| `gap` | 缺失/未报告/需人工确认 | 材料原文或空值，quote 置空并记 not_reported |

## 3. quote 规则（硬约束）

1. `quote` 必须是材料的**逐字子串**（文本源）或**原始单元格值**（数据源）；不得转述、改写、补字。
2. 数值结论的 quote 用该列前若干原始值（如 3 个，用 `|` 分隔）作锚点，`method` 中写清“全量 n 行均值/中位数”的口径，避免循环自证。
3. 无法取得原文（例如列名与字典对不上、编码损坏）→ `claim_type=gap`、`quote=""`、`confidence=low`，并把问题写进 `result.json.pending_human`。
4. 两来源冲突 → 各建一条 `conflict` 条目，`value` 分别放冲突值，绝不选一个掩盖另一个。

## 4. evidence_map.json schema（版本 1.1）

```json
{
  "schema_version": "1.1",
  "entries": [
    {
      "id": "E-001",
      "conclusion": "一句可核查的结论/数值声明",
      "claim_type": "observation|inference|rule|artifact|conflict|gap",
      "locator": "scheme:path#fragment",
      "quote": "逐字原文子串或原始单元格值；gap 时为 \"\"",
      "quote_source": "quote 取自哪个文件（相对 /input 路径）",
      "method": "公式/规则名/统计口径",
      "confidence": "high|medium|low",
      "value": null
    }
  ]
}
```

## 5. 最小覆盖要求

以下结论**必须**各有一条证据条目，否则 `finalize` 应把 `status` 降为 `partial`：

1. case_id 的取值来源；
2. 主表路径、行数、列数与编码；
3. 每条清洗规则的删行数（`rule`）；
4. 每个检验的统计量、自由度、p 值、效应量（`inference`）；
5. 每个图元（箱线图的四分位、误差棒）对应的数值（`artifact`）；
6. 每个跳过/失败/异常/缺失（`gap`）。

## 6. 评审核对方式

评审按三步核对一条结论：① 打开 `quote_source`，用 `locator` 定位；② 在原文中找到 `quote`（必须逐字匹配）；③ 用 `method` 说明的公式重算 `value`，应等于 `conclusion` 中的数值。三步任一失败即视为证据断裂。
