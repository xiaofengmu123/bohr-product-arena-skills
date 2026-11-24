# 证据溯源与核验规则（evidence-map）

本文件定义结果与讨论章节 Skill 的核心数据结构 `evidence_map`，以及"读得准 / 分得清 /
说得住 / 核得了"四问的落表方式。运行时以 `/input/case.json` 声明的 output_contract 为准；
本文件只规定附加字段与校验规则，不改变题目字段。

## 1. evidence_map 条目结构（每条结论都带 locator + 逐字 quote）

```json
{
  "claim_id": "R1",
  "claim": "正文中的结论句（原文）",
  "claim_type": "result_comparison",
  "section": "results",
  "locator": "materials/fig2_stat.md#L3",
  "quote": "材料中的逐字片段（原文，非转述）",
  "value": {"number": "78", "unit": "%", "stat": "mean±SD", "p": "0.012"},
  "confidence": "direct",
  "status": "verified",
  "basis": ["R0"],
  "strength": "moderate",
  "causal_boundary": "correlational"
}
```

- `claim_id`：稳定编号；结果段用 `R*`，讨论段用 `D*`，局限用 `L*`，因果/推测用 `C*`。
- `claim_type` 枚举：`result_fact | result_trend | result_comparison | result_negative |
  discussion_interpretation | discussion_literature | discussion_consistency |
  discussion_inconsistency | discussion_limitation | causal_claim | speculation`。
- `confidence`：`direct`（材料直接给出）/ `inferred`（由材料推断）/ `unverified`（未能核实）。
- `status`：`verified` / `unverified` / `conflict`（同一指标两处不一致时并录两处）。
- `quote` 必须逐字，不能改写；数值、单位、误差棒含义、p 值、置信区间原样保留。
- `basis`（讨论/因果/推测条目必需）：支撑本结论的本研究结果条目编号或文献 locator。
- `strength` 与 `causal_boundary`：讨论/因果/推测条目必需，见下表。

## 2. 语气强度 ↔ 证据强度（说得住）

| evidence strength | 允许的表述强度 | 示例动词 |
|---|---|---|
| strong（多组重复 + 显著检验） | 陈述事实、可下结论 | 表明、证实、显著高于 |
| moderate（单次/样本有限但检验显著） | 陈述观察、谨慎概括 | 提示、支持、倾向于 |
| weak（趋势一致但未达显著/仅高剂量出现） | 只写方向与边界，不下结论 | 呈…趋势但未达显著、仅见于 |
| speculative（机制推测） | 显式标注为推测 | 可能、推测、有待验证 |

禁止的绝对化措辞见 `core_impl.py` 的 `FORBIDDEN_ZH` / `FORBIDDEN_EN` 词表（首次、国际领先、
填补空白、颠覆性、unprecedented、breakthrough 等）。这些词由 `check` 子命令自动扫描。

## 3. 因果边界（分得清、说得住）

- `causal_boundary` 四档：`correlational`（相关）/ `predictive`（预测）/ `causal`（因果，
  须有设计支撑如随机化、干预、对照）/ `speculative`（机制推测）。
- 结果段只写观察事实；"因此导致 / 证明机制"一类因果句只能出现在讨论段，且必须带
  `causal_boundary` 与 `basis`，否则 `check` 判为 hard error。
- 机制推测一律标 `speculative`，不写成既成事实。

## 4. 四问落表与自检

| 四问 | 落表位置 | check 校验 |
|---|---|---|
| 读得准 | `value.number/unit/stat/p` 与 `quote` 原样一致 | 逐字 quote 非空；数值 token 未覆盖时 warn |
| 分得清 | `section` = results / discussion；claim_type 前缀 | 因果/推测条目缺 boundary 判 hard |
| 说得住 | `strength`、`causal_boundary`、禁词扫描 | 禁词自动扫描；讨论条目缺 strength 判 hard |
| 核得了 | `locator` + `quote` + `status` | 缺 locator/quote 判 hard |

## 5. 一致性回检清单（对应评分维度"实际效果/使用增益"）

- [ ] 结果段每个数值都能指回一张图 / 表 / 统计输出（locator + quote）。
- [ ] 讨论段每条解释都有 `basis`：要么指回本研究结果，要么指回具体文献（locator + quote）。
- [ ] 不一致文献如实交代，标 `discussion_inconsistency`，不隐藏不折叠。
- [ ] 阴性结果 / 未达显著 / 仅在高剂量出现，原文保留边界，不放大为结论。
- [ ] 局限写到方法、样本、测量、统计层面，不只写泛泛自评。
- [ ] 每个 `claim_type` 与 `strength`、`causal_boundary` 自洽。
- [ ] 未核实条目标 `unverified`，冲突条目标 `conflict`，缺数据写入 `uncertainty`。

`scripts/main.py check` 强制执行结构校验与 evidence_map 校验；本清单供 Agent 逐条勾选并
写入 `change_log`。

## 6. 图表读数协议（读得准）

从图、图注、数据表、统计输出转写数值时逐项核对，写进 `value`，并把图注原句放进 `quote`：

1. **数值与单位**：数值原文照抄；单位按材料原样（不要擅自换算，除非材料给出换算关系）。
2. **误差棒含义**：先确认图注或方法是否写明是 SD / SEM / 95% CI / 四分位；未写明时
   `value.stat="unreported"`，写入 uncertainty，**不默认成 SD**。
3. **有效数字**：正文数值与图表保持一致，不额外增加精度（如 `p=0.03` 不写成 `p=0.0300`）。
4. **坐标刻度**：注意对数轴、截断轴、双 y 轴；跨轴读数要注明。
5. **多面板与补充材料**：逐面板编号（如 Fig.2b、Fig.S3c），locator 精确到面板，不混写。
6. **统计量**：n、检验方法（t / ANOVA / 非参数）、自由度、效应量、p 值、CI 逐项核对；
   缺失项写 `null`，不补造。

示例（示意，非正式案例结果）：

```json
{
  "claim_id": "R2",
  "claim": "Fig.2b 中，处理组 24 h 存活率为 78%±5%（mean±SD, n=12），高于对照组的 52%±6%（p=0.012, 两独立样本 t 检验）",
  "claim_type": "result_comparison",
  "section": "results",
  "locator": "materials/fig2_stat.md#L3",
  "quote": "treated 78 +/- 5 %, control 52 +/- 6 %, p = 0.012, n = 12",
  "value": {"number": "78", "unit": "%", "stat": "mean±SD", "n": "12", "p": "0.012", "test": "two-sample t"},
  "confidence": "direct",
  "status": "verified",
  "basis": []
}
```

## 7. 结果段 / 讨论段最小模板

**结果段（只陈述观察事实，不解释，按图序或按问题组织）**

```
[段] 围绕 Fig.X / Table.Y，陈述观察到的数值、方向、幅度与检验结果（与证据条目 R* 一一对应）。
     - 描述句：在 [条件] 下，[组] 的 [指标] 为 [数值]（[单位], [误差], n=[n]）。
     - 比较句：[A] 与 [B] 相比 [方向+幅度]（p=[p], [检验方法]）。
     - 边界句：其余组间差异未见统计学显著（p>0.05）；[某趋势] 仅在高剂量组出现。
```

**讨论段（承担解释、文献对照、因果边界与局限，每条论断带 basis）**

```
[段1 总结] 本研究观察到 [核心发现]，与 [文献] 的 [结果] 一致/不一致（basis: R* / ref）。
[段2 解释] 这一差异可能源于 [机制/方法/样本]（speculative，basis: R*）。
[段3 边界] 本研究为 [相关/预测/因果]，机制推断仅为推测（causal_boundary）。
[段4 局限] 局限在 [方法/样本/测量/统计] 层面；[未覆盖内容] 留待后续。
```

结果段禁止出现"证明、导致、机制、因此解释"等解释性动词；讨论段禁止凭空引用或堆文献不讲关系。
