# 学术翻译与双语写作规则（bilingual_rules）

本文件是 SKILL.md 第 5 节的展开，Agent 在 P2 翻译阶段逐条执行；脚本 `selftest.py` 只负责事后校验，不替代这些规则。

## 1. 术语稳：术语表锁定

- 术语表格式（写入 `/app/work/glossary.json`）：

```json
{
  "terms": [
    {"canonical": "机器学习", "variants": ["machine learning", "ML"], "note": "正文/图题统一用 canonical"},
    {"canonical": "均方根误差", "variants": ["root-mean-square error", "RMSE"]}
  ]
}
```

- `canonical` 是全篇唯一允许出现的译法（摘要、正文、图表题注、附录均一致）；`variants` 记录原文同义/缩写写法，仅用于回溯与自检，不得作为译文再出现。
- 同一英文词在不同学科译法不同时，按学科义项建独立条目并在 `note` 标注适用域，例如 `"resonance"` 物理译"共振"、化学译"共振/共鸣"需据上下文锁定其一。
- 出现术语表外的领域术语时：先抽词 → 定 canonical → 追加进 `glossary.json` → 再写译文；不得先写后补。
- 自检口径：`selftest.py` 对 deliverables 扫描，同一术语若出现多个 spelling（canonical 与 variants 并存）即报 warning。

## 2. 数字实：数字、单位、口径原样

- 数字、单位、量级、范围、年份、样本量、百分数、统计口径（如 P 值、置信区间、n=）一律原样落地，不四舍五入、不换算单位、不"顺手取整"。
- 中英文数字表达差异显式换算式并记录到 `fact_source_map`：`12 million = 1200 万`、`3.5 billion = 35 亿`；原文有效数字位数保留（`0.050` 不等于 `0.05`）。
- 范围写法保留原结构：`10–15（含端点）` 与 `10 to <15` 不同，按原文边界逐字转写。
- 单位换算只在题目明确要求时进行，且换算结果与换算式同时写入 `version_boundary.modifications`。
- 自检口径：`selftest.py` 的数字比对是启发式，只报 warning，不替代人工核对；含万/亿、单位换算、有效数字的差异须在 `human_confirm` 列出。

## 3. 对得齐：句段级双语对照

- 推荐对照格式（每句段一行）：

```markdown
| locator | 原文句段 | 译文句段 | 备注 |
|-|-|-|-|
| 2:3 | The results suggest... | 结果表明…… | suggest 保留为"表明/提示"，不升格 |
```

- 段落顺序、章节标题层级、图表编号、公式编号、题注、脚注、参考文献条目全部原位保留，不重排、不合并、不拆分。
- 公式不译内部符号：符号、上下标、希腊字母、运算关系原样；仅翻译公式外的说明文字，并把符号变更记入 `fact_source_map`（kind=symbol）。
- 表格按行列对应转写；表头、单位、表注逐格对应。

## 4. 边界清：语义强度与归属

- 模糊限制语强度逐级保留：`demonstrate/show`（证实/表明）> `suggest/indicate`（提示/表明）> `may/could/might`（可能/或可）；不得把低强度升格为高强度，也不得把断言降为猜测。
- 否定范围按原文：`not all patients responded` ≠ `no patient responded`；`partially`、`most`、`some`、`in general` 等量词/限定词逐字对应。
- 因果表述按原文：`associated with` 译"相关"而非"导致"；`caused by` 译"由……所致"。
- 增译/省译显式标记：结构需要而补的连词、主语用 `【增】`，删去的冗余冠词用 `【省】`，并写入 `version_boundary.modifications`。
- 引用与归属完整保留：作者—年份、编号、上标、方括号、`et al.` 按目标语与目标格式映射，不增删作者、不改变归属；找不到原文条目时记 `uncertainty`，不杜撰。

## 5. 确定性规则

- 遍历 `/input/` 与 `/app/output/` 文件均按字典序；同一判断只落到 glossary.json 或 fact_source_map。
- 禁止随机抽样、随机措辞、随机编号、随机选择译法；译法唯一来源是 glossary.json 的 canonical。
- 两次运行差异只允许出现在时间戳字段（change_log.time、runtime.created_at、runtime.duration_sec），正文与映射内容必须一致；`selftest.py` 输出的 `deterministic_signature` 已剔除时间字段，可直接用于核对。
