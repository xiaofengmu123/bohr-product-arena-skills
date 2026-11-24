# 降级与不确定性细则（本版变体重点）

目标：图面、result.json 与材料三者可逐项互查；证据不足时不糊，宁可 `null`/`pending_human`。

## 状态判定阶梯

1. `completed`：四要素（question/method/results/conclusion）齐全；每个画出的实体/关系/数据/图例项都有 locator+逐字 quote；SVG 可解析且 PNG/PDF 真实产出。
2. `partial`：满足以下任一——四要素缺一、某图元无证据、某导出件 `preview_not_rendered`、存在歧义或来源冲突；须在 `limitations`/`uncertainty` 说明，仍可交付可编辑 SVG。
3. `failed`：无 `case.json` 或全部材料缺失；写 `status=failed` 骨架、退出码 1、报告阻塞并停止后续依赖步骤。

判定由 `scripts/main.py` 的校验结果驱动，但最终 status 由 agent 依事实定稿，不得为了"好看"写成 `completed`。

## 缺数据处置表

| 场景 | 动作 |
|-|-|
| 数值缺失 | 字段 `null` + `uncertainty` 一条；不估计 |
| 实体/方向歧义 | 拆分 id，或改无向连线（`directed:false`）并在 `uncertainty` 注明 |
| 来源冲突 | 两处都记、标 `source_conflict`，图取其一并说明 |
| 需人工判断 | 写入 `pending_human`（`current_value:null`），不改造事实 |
| 渲染器/字体缺失 | 保留 SVG；导出记 `preview_not_rendered`；尝试平台 office-renderer / pdf-render-tool / browser-automation；字体替换写 `limitations` |
| PDF 无法解析 | 写 `limitations`（PDF 未解析），改用平台 pdf-text-tool 后重跑 |
| 坏 JSON / 坏编码 | 捕获写入 `limitations`，继续处理其余材料，不崩溃 |
| 无 case.json | `status=failed` 骨架 + limitation，退出 1 |
| 同一策略连续失败 2 次 | 停止重试，记 limitation，转 `partial` |

## 不确定性条目格式

`uncertainty` 每项：`{item, reason, impact}`。`item` 指向受影响字段（如 `data_mapping[2].value`）；`reason` 说明为什么不确定（缺证据/冲突/歧义）；`impact` 说明对图面或结论的影响（如"该柱高暂按 null 不绘制"）。

## 禁止事项（逐字）

- 禁止编造数值、DOI、引用、渲染结果或"已完成"状态。
- 禁止把估算值写成实测值；估算必须标 `confidence=low` 且进 `uncertainty`。
- 禁止删除题目 `output_contract` 中的未知键；缺值写 `null`。
- 禁止用预写答案冒充正式案例结果；示例产物必须标注为示例。
