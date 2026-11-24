# 证据溯源与数据契约（evidence contract）

本文档定义 Skill 中所有"可复核"结论必须满足的数据契约，供 Agent 在 `record` 时遵循，也供评审按同一规则复现。

## 1. locator 与逐字 quote

每个关键结论、数值、引用边必须携带证据，形如：

```json
{"locator": "material.pdf#p3", "quote": "We build on the framework introduced by ..."}
```

- `locator`：指向输入材料中的位置。可用 `文件名#页码`、`文件名#段落号`、`case.json#字段名` 等稳定锚点；禁止只有文件名没有定位。
- `quote`：从材料中逐字摘录的片段，不加工、不概括；若材料为扫描件无文本层，标 `not_reported` 并在 `uncertainty` 说明。
- 若一条边/结论有多个证据来源，`evidence` 用数组，逐条留档。

## 2. 引用边方向

统一为 **`source` cites `target`**（source 引用 target）。

- 后向追踪：种子是 `source`，其参考文献是 `target`。
- 前向追踪：施引文献是 `source`，种子是 `target`。
- 外部数据只给被引列表、不给方向时，`relation` 必须为 `direction_unknown`，并写 `uncertainty`，不得猜测。
- 禁止把"被引列表"自动当成方向已知；先确认数据源语义再落边。

## 3. 身份合并规则

合并键优先级与执行规则：

| 层级 | 键 | 行为 |
|---|---|---|
| 强键 | DOI、arXiv | 命中即合并 |
| 中键 | title + 第一作者姓 + 年份 | 命中即合并，但置信度低于强键 |
| 弱键 | title only | **不自动合并**，只记 `uncertainty.title_only_duplicate_candidate` |

合并后必须保留：`merge_evidence.members`（成员 id）、`keys`（各成员用到的键类型与键值）、`canonical`（保留 id）。`finalize` 会把合并前的清单写入 `results.papers_pre_merge`，把旧 id → 规范 id 写入 `results.id_remap`，边表按映射重写，保证可复核。

## 4. 状态机

`status` 只能取四值，且必须与证据一致：

- `ok`：种子身份、扩展、边、证据均完整，无关键缺口；
- `partial`：已完成主要扩展，但仍有可标注的缺口（缺前向记录、限流等）；
- `insufficient_evidence`：材料不足或种子无引文记录，无法完成主要追踪；
- `input_error`：case.json/task.md 缺失或不可解析。

禁止在缺材料时标 `ok`；禁止为凑完整而补造文献或引用。

## 5. 冲突与缺失

- 同一指标两个来源不一致：两者都记录，并在 `uncertainty` 标 `source_conflict`，附两个 locator+quote。
- 字段缺失：写 `not_reported`，不写占位数值（如年份缺失写 `null`，不写 0）。
- 覆盖缺口：某层扩展没做到，写 `uncertainty` 的 `coverage_gap` 项，说明缺哪一层、原因。

## 6. 异常处置（与降级策略对应）

| 场景 | 动作 |
|---|---|
| 空输入目录 | 退出码 0，`status=insufficient_evidence`，列出缺口 |
| 坏 JSON / 坏编码 | 记录 `degradations`，继续处理其余材料 |
| 种子无引文记录 | `pending_human` + `limitations`，不编造边 |
| 同名论文歧义 | `uncertainty.title_only_duplicate_candidate`，不自动合并 |
| 跨库字段冲突 | `uncertainty.source_conflict`，双记 |
| 撤稿与更正 | `paper.retraction`/`correction` 字段 + `pending_human` |
| 接口限流 / 权限不足 | `errors`/`degradations`，已获部分照常交付 |

## 7. 可复核性自查

交付前逐项确认：每条边有 evidence；每个 paper 有稳定 id；每次合并有 members/keys；每次 record 有 change_log；result.json 含全部 output_contract 字段与 case_id。核对脚本：`python3 scripts/main.py finalize --output /app/output`（校验失败仅记 errors，不中断）。