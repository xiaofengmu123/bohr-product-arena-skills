# 证据溯源与引用核验规则

写作任务的硬要求：**完成产物，也保留来源**。每个关键结论、数值、术语、图元、引用都能指回材料中的逐字片段，他人可独立复核。本规则配合 `scripts/evidence.py` 使用。

## 1. 证据记录格式

每条证据记录含三要素：

```json
{"locator": "materials/paper.md#L3-L5", "quote": "原文逐字片段", "mode": "exact"}
```

- `locator`：文件路径 + 行号区间；能定位到段落/公式/图表编号更佳。
- `quote`：材料中的**逐字**原文片段；不得改字、不得凭记忆改写。
- `mode`：`exact`（逐字命中）/ `ws-normalized`（仅空白差异）/ `unverified`（未命中）。

## 2. 引用核验流程（先核验，再写入正文）

1. 把要写入正文的每条引用写成 `{source, quote}`。
2. 运行 `python -B scripts/evidence.py verify-batch --quotes work/quotes.json --source /input`。
3. 仅 `verified=true` 的引用可进入正文；`verified=false` 的标 `unverified`，写入 `result.json` 的 `uncertainty`，并在正文中以「[待核]」标注，不改写原文献结论。

## 3. 不得虚构（红线）

- 不虚构引用、DOI、页码、数值、结论；找不到就写 `not_reported` 或 `pending_human`。
- 不依据错误的领域标签补造数据；PDF 以原文为准，读实际文件而非按标签猜内容。
- 引用了文献但结论被改写、或引用不存在文献的情况，必须显式标记，不能静默通过。

## 4. 冲突与不确定

- 同一指标两个来源不一致：两个都记录，标 `source_conflict`，不隐藏、不擅自取舍。
- 材料不足：`status=insufficient_evidence`，`limitations` 列出缺口，`pending_human` 列出待补项。
- 推断 vs 事实：推断标 `inference`，事实附 quote；不能把推断当既定结论。

## 5. 修改记录（改得动、可回溯）

`change_record.md` 每行一条改动：

```text
[时间] 动作(增/删/改/合并/重编号) | 目标定位 | 原文 | 改后 | 原因/意见来源
```

- 局部修改只动需要动的地方，保留作者原意与既有表述。
- 章节级改动记到节号；合并多轮意见时逐条落实并记录落实情况。
- 未确认的改动列入 `pending_human`，不冒充已完成。
