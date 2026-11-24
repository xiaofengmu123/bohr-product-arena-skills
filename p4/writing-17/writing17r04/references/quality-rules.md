# 质量规则与降级清单

用于抽取、渲染、自检三个阶段的判断依据。任何一条不满足时，如实降级并写入 `version_boundaries` / `uncertainty`，不得编造。

## 1. 五问对齐（对应评分"抓得准/理得清/连得对/证据可追溯/实际有用"）

1. **抓得准**：节点是否为核心概念、研究问题、方法、证据、结论、任务？装饰性词语是否已被排除？
2. **理得清**：根节点唯一？层级是否合理？同级粒度是否一致？有无分类交叉、节点重复、重要信息埋没？
3. **连得对**：每条 `edges` 关系是否在材料中有依据？方向是否正确（因果/依赖/时间/对比/支持/冲突）？
4. **证据可追溯**：关键节点与关系的 `locator + quote` 是否逐字可回溯？是否区分 `fact/summary/inference`？
5. **实际有用**：导图是否帮助理解/规划/沟通/写作？是否在 900 秒、64 次请求内完成且成本合理？

## 2. 结果契约硬性项

- `result.json` 必须含 `case_id`、`status`、题目 output_contract 全部字段，再加 `sources` / `uncertainty` / `change_log`。
- 主产物（`mindmap.md` / `.mmd` / `.svg` / `.json`）必须可打开、可编辑、可复核；SVG 必须能解析为 XML。
- 缺值字段给 `null` / `[]`，不改键名、不删未知键。

## 3. 冲突与不确定处理

- 同一指标两个来源不一致：两者都记录，并在 `version_boundaries.conflicts` 标注 `source_conflict`，写清各自 locator 与 quote。
- 无法从材料确认的数值/引用：写入 `uncertainty.unverified_items` 或 `version_boundaries.pending_human`，使用 `not_reported`，不填虚构值。
- 材料不足导致无法完成某分支：`material_gaps` 逐条列出缺口；整体不足时 `status=insufficient_evidence`。

## 4. 降级策略（缺数据/缺软件/写盘失败）

| 场景 | 处置 |
|-|-|
| 输入目录为空 | 写 `status=insufficient_evidence`，`version_boundaries.errors` 说明，退出码 0 |
| 坏 JSON / 坏编码 | 记录到 `errors` / `degradations`，继续处理其余材料 |
| 缺少渲染工具（无浏览器/无 PDF） | SVG 本身可编辑可渲染；PNG/PDF 预览可用平台 office-renderer 转换，失败则说明 `limitations` |
| 写盘失败 | 保留最小合法 `result.json`，进程不崩溃，`errors` 记录 |
| 节点规模爆炸 | 合并同级节点、控制分支深度 ≤ 5，超限在 `change_log` 说明 |

## 5. 诚实状态（`status`）

- `ok`：契约字段齐全、产物有效、证据可回溯、无未解决的致命缺口。
- `partial`：有可用产物但存在已知缺口/警告（默认的中间状态）。
- `insufficient_evidence`：材料不足以完成导图，不编造内容。
- `input_error`：输入缺失/损坏/不可解析导致无法开工。

状态必须与实际产物一致：`insufficient_evidence` 不得同时宣称已交付完整导图。
