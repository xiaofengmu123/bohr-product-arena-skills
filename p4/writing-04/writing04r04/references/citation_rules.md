# 语义规则：引用定位、匹配、核验与不确定性标注

本文件是 Agent 在确定性脚本之上完成语义判断时的操作规范。脚本
（`scripts/main.py` + `scripts/core_impl.py`）负责解析、去重、编号重排、
格式转换与一致性检查；本文件规定**必须由 Agent 完成、脚本不得臆造**的
语义结论，以及如何把缺口显式写回 `evidence_map.json` 与 `result.json`。

## 1. 引用定位（“挂得对”）

1. 逐句划分稿件，标记“论断句”——即携带可被证伪的事实/数值/结论/比较
   的句子，而不是背景铺陈或过渡句。
2. 引用标记放在**该论断句的句末标点之前**（顺序编码制），或紧邻支撑的
   词/短语之后；不得整段末尾或图表注附近“一挂了之”。
3. 一句多引用 `[a,b]`；多句共用一条引用时，每条论断句都要有自己的标记，
   不允许用“前文已引”替代。
4. 每条插入动作写进 `change_log`：`{locator, action, before, after, reason}`。

## 2. 论断—文献匹配（“对得上”）

对每条引用逐句回答三点，并写回 `evidence_map.json` 对应条目的
`claim` 与 `match_evidence`：

- **主题匹配**：论断的变量/对象是否在文献标题或摘要中出现。
- **结论匹配**：论断的方向（正/负/无效/量级）是否与文献结论一致。
- **范围匹配**：样本、物种、时间窗、方法是否可支撑该论断。

匹配依据必须引用**文献库中的逐字片段**（`locator + quote`），不允许凭
印象断言。无法判定时写 `pending_human`，并把 `verification.status` 设为
`not_verified` 或 `pending_agent`，**不得改为 `verified`**。

## 3. 来源核验（不编造）

- DOI/元数据核验依赖运行时检索工具或原文比对；脚本不做网络请求。
- 无检索结果 ≠ 条目伪造，只写 `not_verified` 与 `pending_human`。
- 同一指标在两个来源不一致时，两处都记录并标 `source_conflict`。
- 缺失字段写 `not_reported`，用 `[字段缺失]` 占位，**绝不补造卷期页、
  年份、DOI 或页码**。

## 4. 去重确认（“收得净”）

脚本用 `DOI → 规范化题名+第一作者+年份` 判据自动合并，并输出
`merge_evidence`（含 `field_conflicts`）。Agent 的职责：

- 核对 `field_conflicts` 的每一项，人工裁决后更新条目。
- 对“同名作者不同文献”的疑似误合并，判据不足以区分时**不合并**，
  标 `ambiguous_entry` 并加入 `pending_human`。
- 预印本与正式版、中英文题名等，只有 DOI 或等价强证据一致才合并，
  否则保留两条并标 `source_conflict`。

## 5. 格式转换（“编得顺”）

- 顺序编码制默认按**首次出现顺序**重排；正文标记、文末列表、图表注、
  附录引用使用同一套编号（脚本已做正文↔列表一致性检查）。
- GB/T 7714—2015 与 APA 的字段映射见脚本 `format_gbt7714` /
  `format_apa`；中英文混排时标点按目标规范，保留 DOI 与文献类型标识。
- 转换中无法确定的字段标 `[字段缺失]`/`(n.d.)`，不猜测。

## 6. 不确定性与降级标注（本包重点）

所有不能确证的事实/引用/数值必须显式落到 `result.json`，四类载体各司其职：

| 字段 | 语义 | 示例 |
|---|---|---|
| `uncertainty` | 可定位、可消解的具体不确定项 | 某条引用的论断匹配未核验 |
| `limitations` | 方法/输入/软件造成的系统性边界 | `.pdf` 稿件无运行时渲染器 |
| `degradations` | 被跳过或降级处理的输入 | `.xlsx` 文献库未转换 |
| `pending_human` | 必须人工裁决才能推进的项 | 字段冲突、疑似误合并 |

每项 `uncertainty` 结构固定为：

```json
{"id": "...", "kind": "...", "locator": "...", "claim": "...",
 "detail": "...", "severity": "high|medium|low", "resolution": "..."}
```

`kind` 取值约定：`unverified_claim_match`、`merge_field_conflict`、
`ambiguous_entry`、`unverified_doi`、`missing_metadata`、`source_conflict`、
`unsupported_format`、`truncated_file`、`encoding_loss`。

`severity=high` 的项必须同时出现在 `pending_human`。**只有**当所有引用都
`verified` 且无未决 high/medium 项时，`status` 才允许设为 `ok`；其余一律
`partial`，材料不足用 `insufficient_evidence`，输入损坏用 `input_error`。
