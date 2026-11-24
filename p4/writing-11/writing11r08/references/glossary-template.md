# 术语表模板与事实映射字段说明

## 1. 术语表模板（glossary.json）

术语表是“术语稳”的落盘依据：从原文抽取领域术语、缩写、上下位词，锁定全篇译法（含图表题注），并处理同一英文词在不同学科下的译法差异。`check_consistency.py --glossary` 会扫描译文正文，若同一术语的多个变体并存则报 `term_variant_collision`。

```json
{
  "terms": [
    {
      "canonical": "均方根误差",
      "variants": ["root mean square error", "RMSE"],
      "domain": "statistics",
      "note": "全篇统一用 canonical；图题注亦同"
    },
    {
      "canonical": "p值",
      "variants": ["p-value", "p value", "P值"],
      "domain": "statistics",
      "note": "斜体与大小写不参与变体判定，由人工在排版阶段统一"
    }
  ]
}
```

规则：
- `canonical` 为全文统一译法（中文或英文视语向而定）；
- `variants` 列出允许出现的同义/缩写写法；正文同时出现多个 variant（或 canonical 与 variant 并存）会报 warning，需统一或显式说明；
- `domain` 可选，用于区分同词异译（如 "cell" 在生物学为“细胞”、在材料学为“单元/电池”），发现歧义时写入 `pending_human`。

## 2. fact_source_map 条目字段（“数字实/边界清”的依据）

每条数字、单位、引用、图表编号、关键论断都应有一条记录，且 `quote` 必须能在原始材料中逐字定位（`check_consistency.py --result` 会校验）：

```json
{
  "item": "样本量 n=128",
  "kind": "number",
  "value": "128",
  "target_value": "128",
  "source": "/input/paper.pdf",
  "locator": "paper.pdf 第 3 页, 方法段",
  "quote": "a total of 128 participants were enrolled",
  "note": "与译文一致；未做四舍五入"
}
```

- `kind` 取 `term|number|unit|citation|figure|claim|alignment`；
- `locator` 用“文件:页码/段落”或“文件:行号”，供人工按位置复核；
- `quote` 必须是原文逐字片段（可含空白归一化），不新增材料外事实；
- 推断或换算（如 万/亿↔million/billion）在 `note` 显式标注“换算/推断”，与原文事实区分。

## 3. version_boundary 字段说明

```json
{
  "modifications": [
    {"what": "统一 RMSE 为“均方根误差”", "why": "术语表锁定", "before_after": "RMSE → 均方根误差"}
  ],
  "human_confirm": [
    {"item": "同词异译", "options": ["细胞", "单元"], "recommended": "细胞"}
  ],
  "material_gaps": ["缺少参考文献全文，无法核验文献 7 的卷号"],
  "anomalies": [],
  "editable": true
}
```

- `modifications` 记录“改了什么、为什么改、差异”，保证局部改动可回溯、不扩大波及范围；
- `human_confirm` 记录需人工决策项（附 options 与 recommended）；
- `material_gaps` 记录材料缺口；`anomalies` 记录异常；`editable` 表示产物是否可继续编辑。

## 4. 双语对照稿布局约定（“对得齐”）

对照稿用 Markdown 表格或相邻块呈现，每句段一行，保持段落顺序与图表编号原位：

```markdown
| # | 原文 | 译文 |
|-|-|-|
| S1 | The model has 1.5 million parameters. | 该模型具有 150 万参数。 |
| S2 | See Figure 1 and Table 2. | 见图 1 和表 2。 |
```

公式、表格、题注、脚注与参考文献条目必须完整保留在原位；对长段落按句切分并记录分段边界，分段后重新校验编号与交叉引用。

## 5. 中英数字表达差异对照（“数字实”的换算口径）

`check_consistency.py` 的 `extract_numbers` 会把两侧归一化到同一数量级后再比对，因此 `1.5 million` 与 `150 万` 视为命中。翻译时按下列口径落地，不做“顺手改成整数”：

| 英文 | 中文 | 归一化值 | 说明 |
|-|-|-|-|
| thousand | 千 | 1e3 | 英文多用阿拉伯数字，中文可写“千” |
| million | 百万 / 万 | 1e6 | `1.5 million` = `150 万`，不可写成 `15 万` |
| billion | 十亿 / 亿 | 1e9 | `2.5 billion` = `25 亿` |
| trillion | 万亿 | 1e12 | `1 trillion` = `1 万亿` |
| 3,200 | 3200 / 3 200 | 3200 | 千分位逗号在中文中通常省略 |
| 12.34% | 12.34% | P:12.34 | 百分号两侧保留，有效数字不增减 |

单位换算（如 `1 in = 25.4 mm`、`°F→°C`）只有在原文给出换算关系或任务明确要求时才做，且必须在 `fact_source_map` 的 `note` 标注“换算”，并把原始值保留在 `value` 字段。

## 6. 学术风格控制（避免逐字直译与语域漂移）

翻译时控制语域与句式：不把学术表述改成宣传口吻或口语化表达，也不逐字直译造成冗长生硬。可检查的口径：

- 保留被动与无人称（“结果表明”而非“我们发现”），除非原文明确用第一人称；
- 保留模糊限制语的强度：`may/suggest/indicate` 用“可能/表明/提示”，`confirm/demonstrate` 用“证实/证明”，不升格也不降格；
- 保留否定范围与限定条件：`not all`、`only when`、`unless` 的辖域原样落地；
- 不增译结论、不删科学判断；增译/省译在 `fact_source_map` 或 `version_boundary.modifications` 标注原因；
- 同一概念在摘要、正文、图题注、附录用同一译法（见术语表），避免每次重新意译。


