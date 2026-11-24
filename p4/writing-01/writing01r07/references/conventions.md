# 契约与规范（Conventions）

本文件定义本 Skill 的输入/输出 schema、证据溯源格式与标记语法。收口前必须对照本文件逐项核验。

## 1. result.json 顶层契约

`/app/output/result.json` 必须含以下键（脚本 `scripts/main.py` 已保证骨架存在，Agent 负责填全）：

| 键 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 来自 `/input/case.json` 的 `id`/`case_id`，缺省用文件名 |
| `output_contract` | object | 原样回显 case.json 的 output_contract（见下） |
| `deliverable` | any | output_contract 字段之一，回显到顶层（缺省为对象） |
| `fact_source_map` | any | 同上 |
| `version_limits` | any | 同上 |
| `status` | string | `in_progress` \| `partial` \| `complete` \| `failed` \| `insufficient_evidence` \| `input_error` |
| `sources` | list | 每条关键结论/事实的来源（locator + 逐字 quote + 文献条目） |
| `uncertainty` | list | 无法确认、冲突、缺失、需人工确认项 |
| `pending_human` | list | 需作者拍板的点（与 uncertainty 中"待人工"部分一致） |
| `change_log` | list | 每次改动：`{ts, step, note}` |
| `errors` | list | 解析/读写失败记录 |
| `degradations` | list | 降级处置记录（超长、非UTF-8、缺工具等） |
| `runtime` | object | 脚本版本与时间戳（可复核性） |
| `artifacts` | object | 主产物文件相对路径 |

**output_contract 回填规则**：以 case.json 的 `output_contract` 为准，**原样保留全部键**、缺值给 `null`、不改名不删未知键；未定义时使用默认三元组 `deliverable`（可编辑 Markdown 稿件）、`fact_source_map`（evidence_map.json）、`version_limits`（改动/人工确认/缺口标注）。

**status 诚实原则**：材料不足写 `insufficient_evidence`，输入损坏写 `input_error`，部分产出写 `partial`，全部完成才写 `complete`，禁止虚标。

## 2. 证据溯源与标记

- 事实性断言挂来源：`[S:材料ID]`，材料ID 必须出现在 `material_index.json` 的 `ref_id` 中。
- 文献引用挂：`[C:键]`，文末参考文献区以 `[C:键] 作者. 题目. 期刊 年份;卷(期):页. DOI` 定义。
- 每条写入 `sources` 的证据至少含：
  - `ref_id`（材料ID）
  - `locator`（定位符，如 `mat-paper-pdf@L3-L5` 或 `paper.pdf#p3`）
  - `quote`（材料中的**逐字片段**，不得改写）
  - `refs`（可选，支撑该论断的 `[C:键]` 列表）
- 冲突信息不隐藏：同一指标两个来源不一致时，两个都记录并标 `[冲突待确认]`，列入 `uncertainty`。
- 缺项显式标注：缺数值/缺图注/缺文献字段写 `[缺失]` 或 `[待确认]`；DOI 缺失写 `unknown`，禁止杜撰。

## 3. 引用格式（最小可核格式）

```
[C:键] 作者. 题目. 期刊 年份;卷(期):页. DOI
```

- 字段缺失写 `unknown`；取不到全文时仅用给定元数据引用。
- 每条 `[C:键]` 必须真正支撑所在句，禁止装饰性、错配引用。
- 一致格式统一由本规范给出，Agent 不必猜测。

## 4. 材料索引 schema（material_index.json）

每个 `ref_id` 对应一个材料文件，含：`source`（绝对路径）、`rel_path`、`size`、`kind`（text/pdf/image/office/unknown）、`parse_status`（indexed/needs_parse/skipped_oversize/unreadable）、`preview`、`key_claims`、`numbers`（自动抽取，含 value/unit/locator/quote）、`citations`（自动抽取 DOI/arXiv）、`flags`、`agent_notes`。

- PDF：`parse_status=needs_parse`，运行时用 `pdftotext` 解析后回填 `key_claims`/`preview`，不得凭文件名补数据。
- 图片/办公文档：同样标 `needs_parse`，用 OCR/office 工具转写后回填。

## 5. 预算与增量原则

- 总预算 900 秒 / 64 次模型请求 / 单次 ≤16384 token（含推理）。
- **前 3 分钟必须先落盘 `status=partial` 的结果骨架**（运行 `scripts/main.py`），之后只做增量改进，任何中途异常都保留已落盘的可复核产物。
- 重复运行结果结构一致（确定性索引：排序遍历、固定正则、固定随机种子）。
