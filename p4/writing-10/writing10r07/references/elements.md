# 字段与台账 Schema（审稿回复与论文修改）

本文件定义 `result.json` 与 `revision_ledger.json` 的字段语义，供 Agent 与
`scripts/validate_output.py`、`scripts/selftest.py` 共同使用。三条 output-contract
字段（`deliverable`、`fact_source_mapping`、`version_boundary`）为题目原样要求，
必须全部出现，未知键不得改名或删除。

## result.json 顶层字段

| 字段 | 类型 | 必需 | 语义 |
|-|-|-|-|
| `case_id` | string | 是 | 从 `/input/case.json` 读取并保持原值；找不到时写 `unknown` 并记 degradation |
| `status` | string | 是 | `in_progress \| complete \| partial \| insufficient_evidence \| input_error \| failed` |
| `deliverable` | object | 是 | 目标产物：可编辑修订稿 + 回复信 + 台账 + 证据清单，各含 path/status |
| `fact_source_mapping` | array | 是 | 每条 `{claim, source_ref, location, quote, status}`；数字/术语/引用/关键论断逐条回溯 |
| `version_boundary` | object | 是 | `{changes[], pending_human[], material_gaps[], anomalies[]}` |
| `sources` | array | 是 | `{id, type, ref, description}`；`ref` 用 `/input` 内相对路径或可复核定位 |
| `uncertainty` | array | 是 | `{item, reason, disposition}` |
| `pending_human` | array | 否 | 需作者/编辑人工确认的事项（建议合并进 version_boundary.pending_human） |
| `change_log` | array | 是 | `{ts, action, detail}`，每次落盘/修改追加一条 |
| `errors` | array | 否 | 运行期错误（坏 JSON、坏编码、写盘失败等） |
| `degradations` | array | 否 | 降级记录（超大文件跳过、缺文件、无法解析等） |

## deliverable.components（四个可编辑产物）

| 组件 | 建议路径 | 完成标准 |
|-|-|-|
| `revision_manuscript` | `/app/output/revision_manuscript.md` | 在指定基线内真实落修改，可继续编辑 |
| `response_letter` | `/app/output/response_letter.md` | 逐条回应，语气分级，每条可回指台账 |
| `revision_ledger` | `/app/output/revision_ledger.json` | 意见—改动—证据三栏台账，全部字段可复核 |
| `evidence_list` | `/app/output/evidence_list.md` | 补实验/补数据/补文献/重分析清单，含到位状态与替代说明 |

Office/PPT/图表类产物须另产真实 PDF/PNG 预览并写入 `deliverable.previews`；
本固定案例为 markdown 基线，通常无需 Office 预览。

## fact_source_mapping 条目

`status ∈ {traced, assumption, unverified, source_conflict}`：

- `traced`：能在 `/input` 材料中找到 `location`（如 `materials/reviewer-comments.md#L3`）与逐字 `quote`。
- `assumption`：材料未给、按显式假设推进，必须在正文标注为假设。
- `unverified`：PDF 无法解析或未联网核验的来源身份。
- `source_conflict`：同一指标两处不一致时，两处都记录并标冲突。

## revision_ledger 条目（核心方法：三栏台账）

每条意见一行，`decision ∈ {accept, partial, reject}`，`tone ∈ {accept, clarify,
polite_refute, appeal}`：

| 字段 | 说明 |
|-|-|
| `comment_id` / `comment_quote` / `comment_anchor` | 意见编号、逐字摘录、行号锚点（来自 scaffold） |
| `sub_requirements` | 一条意见内的多个子要求，逐项给处理结论 |
| `type` / `severity` | `method\|data\|writing\|format\|ethics` / `critical\|major\|minor` |
| `decision` / `rationale` | 采纳/部分采纳/不采纳及理由 |
| `action` | 具体动作（不得是"已修改"空话），落到位 |
| `manuscript_location` | 修订稿内的定位：`L<n>` / `section n` / `#标题` / 可检索原文片段 |
| `evidence` | `{status, source, fallback}`；`status ∈ {done, partial, not_done, impossible}`，非 done 必给 fallback |
| `tone` | 语气强度分级 |
| `verification` | `checked \| unchecked`，selftest 据此复核定位 |
| `cross_references` | 与其他意见的依赖/冲突关系 |

"对得上"由 `selftest.py` 复核：回复信引用的每条 accept/partial 意见都能在台账找到，
台账的 `manuscript_location` 都能在修订稿中找到。
