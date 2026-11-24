# 降级、局限性与不确定性台账规范（本版重点）

本 Skill 的差异化取舍：**结论的可信度由证据等级门控，缺证据即降级，绝不
编造**。所有关键结论、数值、图元都进入如下台账，最终 `status` 由
`scripts/main.py check --auto-status` 按证据自动给出上限。

## 1. 证据等级（sources[].level）

| level | 含义 | 是否允许进入“主结论” |
|-|-|-|
| `verified` | 有 `locator` + 逐字 `quote`，直接来自源材料 | 是 |
| `inferred` | 由已记录规则从 verified 数据导出（须写明规则） | 是，须标注 |
| `assumed` | 采用默认口径（如 95% CI、IQR 离群判定） | 仅作补充，记 uncertainty |
| `missing` | 无证据来源 | 否，记 `pending_human` |

规则：**图上每个数值/轴类型/刻度/单位/图例/误差线/显著性/色标范围，
都必须能指回 `sources` 中一条 verified/inferred 记录**；指不回的图元要么
删除，要么在 uncertainty 声明 `pending_human`。冲突来源并存两条并标
`source_conflict`，不得静默选一个。

## 2. 局限台账（limitations[]）

`kind` 取值：`data_quality`（缺失/异常/重复）、`inference`（统计假设不成立）、
`rendering`（字体/导出失败）、`environment`（缺库/权限）、`scope`（样本量/
外推边界）。

`level` 取值与对 `status` 的影响：

| level | 含义 | status 上限 |
|-|-|-|
| `info` | 已处理，不影响结论 | 无影响 |
| `warning` | 有影响但已说明 | 仍可 `ok`（须在 uncertainty 说明） |
| `missing` | 关键数据/能力缺失 | ≤`partial` |
| `critical` | 结论可靠性受损 | ≤`partial` |
| `blocked` | 无法继续（可恢复） | `partial`；不可恢复 → `insufficient_evidence` |

## 3. 不确定性（uncertainty[]）

每个 `uncertainty` 条目写 `{message, reason, level, pending_human}`。
`pending_human=true` 且 `level∈{missing,critical}` 时，`status` 不得超过
`partial`。`pending_human` 缺口要写清“缺什么、为什么影响结论、需要谁补”，
不得用空话掩盖。

## 4. 降级阶梯（缺数据/缺软件时逐级退，不伪装）

1. 完整流水线：清洗表 + 统计 + 图件（SVG/PDF/PNG）+ 图注 + 绘图代码；
2. 缺渲染库/字体：纯文本统计 + 数据表 + 图型推荐与图注草稿，`limitations`
   记 `rendering`/`environment`；
3. 缺数据：仅交付 result.json（`insufficient_evidence`）+ 缺口清单，不生成
   任何示例图；`sources` 为空，`pending_human` 列出全部缺口；
4. 输入坏 JSON/坏编码/空目录：`status=input_error` 或
   `insufficient_evidence`，进程退出码仍为 0。

## 5. 诚实状态自动降级

`check --auto-status` 计算证据允许的最高 `status`：

- 有 `fatal` error → `input_error`
- 无任何 `artifacts` → `insufficient_evidence`
- 存在 `missing/critical` limitation 或 `pending_human` 缺口 → `partial`
- 否则 → `ok`

提交前必须跑一次 `check --require-contract --auto-status --fail`，确认无
FAIL 且 `status` 不高于证据上限；发现 FAIL 补齐证据或如实降级，禁止
手写 `status="ok"` 绕过门控。

## 6. 自检核对表（对应评分维度）

- [ ] result.json 合法且含 `case_id` + 全部契约字段 + status/sources/uncertainty/pending_human/limitations/change_log
- [ ] 每个关键数值/图元有 locator + 逐字 quote；冲突并存标 source_conflict
- [ ] 图上数值/轴类型/刻度/单位/图例/误差线含义/显著性/色标范围与源数据一致
- [ ] 覆盖任务要求全部图表要素，交付绘图代码与图注
- [ ] 导出 SVG+PDF+≥300dpi PNG，尺寸匹配栏宽，中文无乱码
- [ ] 重跑两次统计与图形一致（固定随机种子/排序/布局）
- [ ] 缺失/异常/失败均显式说明，无编造
- [ ] 空输入/坏 JSON/缺参数也产出合法 result.json 且退出码 0
