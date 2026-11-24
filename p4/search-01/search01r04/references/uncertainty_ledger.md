# 降级与不确定性账本（Uncertainty Ledger）

本版 Skill 的核心设计取舍：**把「降级与不确定性」从口头约定变成结构化、可复核、并按规则由脚本固化的第一等产物**。目标不是"更多结果"，而是"每条结果都能说清可信度、每条缺口都能说清下一步"。

## 三条硬不变量

1. **证据闸门（no-fabrication gate）**：`results[]` 只接受 `title + (doi|url) + evidence.locator + evidence.quote` 四者齐备的记录。缺任一者，`assemble` 自动把它隔离到 `pending_human`，绝不进入正式结果，也绝不补造。
2. **元数据可信度显式化**：每条结果带 `meta_verified`（元数据是否经第二来源核验）与 `meta_confidence`（0~1）。`meta_verified=false` 时，元数据完整度以 0.5 折算进 score，评分随之下降——**未核验 ≠ 不可用，但必须减分**。
3. **缺口必须带下一步**：`coverage_gaps` 每项含 `aspect / gap / next_query`，说明漏了什么、下一步查什么；只写"有遗漏"而不给下一步，视为不合格。

## uncertainty 四段

| 段 | 内容 | 触发 |
|-|-|-|
| `missing_metadata` | `{field, record_id, title, note}`，逐字段记录缺失 | import 校验发现 authors/year/journal/doi/url/evidence 缺失 |
| `conflicts` | `{field, values[], sources[], resolution="source_conflict"}`，同一指标多来源不一致时全部记录，不擅自取舍 | Agent 核验发现两个来源元数据冲突 |
| `coverage_gaps` | `{aspect, gap, next_query}`，说明未覆盖方向与下一步查询 | 预算耗尽、接口限流、关键词未达主题面 |
| `verification` | `per_database` 聚合：每个库的 trust / records / verified 数 | assemble 时自动统计 |

## 降级阶梯（缺数据/缺软件时逐级下放，不越级）

| 级别 | 条件 | 动作 | 产物 status |
|-|-|-|-|
| 0 全量 | 检索工具可用、元数据完整 | 多轮查询 + 去重 + 评分 | `ok` |
| 1 元数据缺 | 有命中但 doi/authors/year 不全 | 逐字段进 `missing_metadata`，`meta_verified=false`，score 减权 | `partial` |
| 2 接口限流 | paper_search/web_search 限流/权限不足 | 记入 `errors`/`degradations`，改用已有本地材料（materials/PDF）补证据 | `partial` |
| 3 无候选 | 检索为空或全被证据闸门隔离 | `status=insufficient_evidence`，`limitations` 写清缺失与下一步检索建议 | `insufficient_evidence` |
| 4 输入坏 | case.json 坏 JSON / 无 case 文件 | 记 `bad_json`/`no_case_json` 到 errors，仍写合法骨架 | `input_error` |

原则：**每一级都必须先落盘一版合法 result.json，再往下走**；任何中途异常都不会留下空产物。

## 使用方式

```bash
python3 scripts/main.py uncertainty --inline '{"coverage_gaps":[{"aspect":"引文追踪","gap":"未追踪种子文献被引","next_query":"citation chain"}]}' --work /app/work
python3 scripts/main.py assemble --input /input --output /app/output --work /app/work
```

`uncertainty` 子命令按字段追加进工作区 `uncertainty.json`，`assemble` 汇集写入 `result.json.uncertainty`；重复条目由脚本去重，保证幂等。

## 复核清单（人工/评审用）

- [ ] `results[]` 每条均有 `locator + quote`，且 quote 可在 locator 定位处逐字找到。
- [ ] `pending_human` 与 `results` 无交集；无证据记录未混入正式结果。
- [ ] 每条 `coverage_gaps` 都有 `next_query`，不是空话。
- [ ] 每个 `conflicts` 都保留多来源原始值，未擅自定夺。
- [ ] `status` 与 limitations/uncertainty 一致：有 pending/缺元数据时不为 `ok`。