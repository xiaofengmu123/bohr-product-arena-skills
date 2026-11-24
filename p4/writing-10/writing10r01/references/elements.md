# result.json 字段与规则（elements）

本文件定义 `/app/output/result.json` 的字段、取值与证据溯源规则，供 Agent 与
`scripts/selftest.py`、`scripts/validate_output.py` 共同遵守。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 从 `/input/case.json` 读取并保持原值；缺失记 `unknown` |
| `status` | string | `in_progress`（骨架期）→ `complete` / `partial` / `failed` / `insufficient_evidence` / `input_error` |
| `deliverable` | object | 输出契约之一：目标产物 |
| `fact_source_mapping` | array | 输出契约之一：事实与来源映射 |
| `version_boundary` | object | 输出契约之一：版本与边界 |
| `comment_registry` | array | 意见拆解台账（答得全） |
| `evidence_map` | array | 修改定位与逐字证据（改得准/证得住/对得上） |
| `sources` | array | `{id, type, ref, description}` |
| `uncertainty` | array | `{item, reason, disposition}` |
| `pending_human` | array | 需人工确认项 |
| `change_log` | array | `{ts, action, detail}`，每次落盘/修改追加 |
| `errors` / `degradations` | array | 运行错误与降级，诚实记录 |
| `runtime` | object | 输入目录、材料清单、契约字段、时间戳 |

## 2. 三个输出契约字段

### deliverable（目标产物）
```json
{"path": "/app/output/response_letter.md",
 "manuscript_path": "/app/output/revised_manuscript.md",
 "format": "markdown", "title": "...", "sections": [], "status": "complete"}
```
- `path` 与 `manuscript_path` 必须指向 `/app/output` 下真实存在的可编辑源文件。
- docx/PPT/图表另产真实渲染 PDF/PNG 预览供检查，路径写入 `sources` 或 `sections`。

### fact_source_mapping（事实与来源映射）
数组元素：
```json
{"claim_id": "c1", "claim": "……", "source_ref": "materials/manuscript-baseline.md",
 "location": "materials/manuscript-baseline.md#L34", "quote": "逐字原文",
 "kind": "number|citation|term|fact|judgment", "status": "traced",
 "related_comment_id": "R1"}
```
- 每条数字/术语/引用/图表/关键论断至少一项；`quote` 必须是来源中的**逐字片段**。
- `status` 取值：`traced`（可定位）、`assumption`（显式假设）、`unverified`（无法核验）、
  `not_reported`（材料未报告）、`source_conflict`（两个来源冲突，两者都记录）。

### version_boundary（版本与边界）
```json
{"changes": [{"change_id": "c1", "comment_id": "R1", "before": "...",
  "after": "...", "locator": "...", "status": "landed|pending"}],
 "pending_confirmation": [], "material_gaps": [], "anomalies": []}
```
- `material_gaps`：缺材料先列缺口，再标假设推进。
- `anomalies`：意见互相冲突、意见有误、稿件缺章节、版本不匹配、权限不足等。

## 3. 本版强化的两个结构

### comment_registry（意见拆解台账）
```json
{"comment_id": "R1", "raw_quote": "审稿意见逐字原文",
 "source_locator": "materials/reviewer-comments.md#L5",
 "sub_requirements": [{"id": "R1.1", "type": "method|data|writing|format|ethics",
   "severity": "critical|major|minor", "action": "……", "status": "addressed|partial|declined|deferred"}],
 "decision": "accept|partial-accept|clarify|reject", "tone": "accept|clarify|polite-rebut|appeal",
 "response_summary": "……", "change_ids": ["c1"]}
```
- `sub_requirements` 必须非空：一条意见里的多个子要求都要拆出。
- `decision` 与 `tone` 一一对应：接受→accept，澄清→clarify，礼貌反驳→polite-rebut，
  申诉→appeal；避免冒犯表达、过度承诺、无原则顺从。

### evidence_map（修改定位与逐字证据）
```json
{"change_id": "c1", "claim_id": "c1", "related_comment_id": "R1",
 "decision": "accept", "kind": "change", "source_ref": "materials/manuscript-baseline.md",
 "locator": "materials/manuscript-baseline.md#L34", "anchor": "稳定可检索片段",
 "quote_before": "改前逐字", "quote_after": "改后逐字",
 "status": "traced|assumption|unverified|not_reported", "verified": true}
```
- **硬规则**：每条“已修改”声明必须有 `locator` + `quote_after`（逐字），
  `selftest.py` 会核对 quote 是否真的能在指定文件中找到。
- `anchor` 是不依赖行号、改稿后仍可检索的稳定片段，用于多轮返修中定位不漂移。
- `verified` 表示该定位已被自检脚本核验；未核验时不得写 true。

## 4. 语气与决策词汇
- 语气四档：`accept`（接受）、`clarify`（澄清）、`polite-rebut`（礼貌反驳）、
  `appeal`（申诉）。禁止冒犯、过度承诺（如“完全解决”“保证正确”）、无原则顺从。
- 证据到位状态：`in_place`（已在修改稿落地）、`scheduled`（已列补做计划）、
  `not_feasible`（无法补做，需替代说明）、`literature_only`（仅补文献/讨论）。

## 5. 诚实原则
- 不新增材料外事实；不伪造 DOI、数值、引用、实验或“编辑部已接收”。
- 缺材料→`insufficient_evidence` 或 `partial` 并写明 limits；绝不编造。
- 同一指标两来源不一致→两条都记录并标 `source_conflict`，写入 `pending_human`。
