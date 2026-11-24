# 证据溯源与定位规范（bilingual-scholarly-translator）

本规范约定"每个关键结论都可复核"的最小做法：**locator（定位）+ 逐字 quote（引文）**。
运行脚本只做确定性提取，不代替人工判断；结论强度由证据本身决定。

## 1. Locator 格式

统一用 `path#页/段` 或 `seg:<id>` 两级定位，二者可叠加：

- 材料原文：`/input/paper.pdf#p3`（PDF 页码）或 `/input/paper.pdf#p3¶2`（段落）。
- 台账段：`seg:src1-7`，指 `work/ledger.json` 里 `segments` 中 `id=src1-7` 的句段。
- 引用冲突：同一指标两个来源不一致时，两条都写，并标 `source_conflict`，
  例：`note: "n=12 (paper.pdf#p3) vs n=14 (supp.pdf#p2)，source_conflict"`。

## 2. 逐字 Quote 规则

- `quote` 必须是原文**逐字**片段，不做改写、不合并、不删中间内容。
- 数字/单位/术语/引用必须保留原貌：`37.5 ± 0.2 °C`、`(Smith et al., 2020)`、
  `[1,2]` 不得"顺手规整"。
- 无法定位到原文的结论，`quote` 写空字符串，`note` 写 `not_reported` 或
  `pending_human`，禁止补造。

## 3. fact_source_map 条目模板

```json
{
  "item": "准确率提升量",
  "kind": "number|term|symbol|citation|figure|claim",
  "value": "87.3% → 92.1%",
  "source": "/input/paper.pdf#p3",
  "quote": "improves accuracy from 87.3% to 92.1%",
  "note": "原文直译，未四舍五入"
}
```

`kind` 取值：`number`（数字/单位/范围/年份）、`term`（术语/缩写）、
`symbol`（公式/符号）、`citation`（引用）、`figure`（图表/题注）、
`claim`（关键论断）。推断项 `note` 必须写 `推断` 或 `待核`。

## 4. 句段对齐锚约定

译文可编辑文件中，每个译出句段前写锚：

```markdown
<!-- seg:src1-7 src:/input/paper.pdf#p3 -->
机器学习将准确率从 87.3% 提高到 92.1%……
```

- `seg:<id>` 必须与 `ledger.segments[].id` 完全一致，供 `verify` 做对齐检查。
- 句段合并/拆分时，用多个 `seg:` 锚指向同一译文块，并在
  `version_boundary.modifications` 记录 `what`/`why`。
- 公式、表格、题注、脚注、参考文献条目不允许因对齐而丢失；若目标语言无对应
  结构，保留原文并登记 `material_gaps`。

## 5. 中间表示（ledger.json）字段说明

`scripts/main.py extract` 生成，是翻译与校验的**单一事实源**：

- `segments[]`：`id`/`locator`/`kind`/`quote`/`order`，kind 取
  `paragraph|table|figure|formula|footnote|reference|caption`。
- `numbers[]`：`raw`/`core`/`unit`/`percent`/`magnitude`/`kind`/`locator`。
- `terms[]`：`source`/`expansion`/`variants`/`canonical_target`/`locator`。
- `citations[]`：`raw`/`style`（numeric|author-year|narrative）/`key`/`locator`。
- `semantic_markers[]`：`type`（negation|hedge|causal|scope|degree）/
  `count`/`markers`/`locator`。

`extract` 的术语/引用/语义标记识别是**启发式**（正则），用于把人工注意力引导到
高价值位置；`canonical_target` 等字段由 Agent 在读材料后填写，脚本只做核对不做
"自动翻译"。

## 6. 降级与诚实标注（不编造红线）

| 场景 | 处置 |
|-|-|
| 材料缺失/无法判断 | `uncertainty` + `version_boundary.material_gaps` 登记，`status=partial` |
| 引用找不到原文/结论与文献冲突 | 标 `存疑引用`/`结论疑与文献冲突`，保留原意，不擅自改写删除 |
| 缺渲染软件 | 交付可编辑文本，`sources`/`uncertainty` 注明"未渲染预览，原因：缺 X" |
| 超长文本截断 | 分段处理并记录段落边界；编号/锚在分段后重新 `verify` |
| 术语表冲突 | `verify` warning 后统一 `canonical_target`，无法判定交 `human_confirm` |
| 运行失败 | 保留骨架，`status=failed`，`anomalies` 记录失败原因与时间 |

**红线**：不编造评测分数、运行结果、文献、数据或渲染产物；不确定就写
"未确认/not_reported"并保留来源证据。
