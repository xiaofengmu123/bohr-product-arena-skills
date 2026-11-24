# 契约与证据格式规范（Conventions）

本文档定义 result.json、材料索引、稿件证据标记与 change_log 的精确约定。脚本
（`scripts/main.py`、`scripts/evidence.py`、`scripts/selftest.py`）按本规范读写，
Agent 手工补充时也须遵守，保证产物可被复核。

## 1. result.json 顶层契约

result.json 必须同时包含两类键：

1. **case.json 的 output_contract 全部键**：原样回填、不改名、不删除、不新增同义
   键。未知/暂缺字段一律给 `null`、`[]` 或 `{}`。output_contract 缺省时回退为
   `{"deliverable": null, "fact_source_map": null, "version_limits": null}`。
2. **标准键**（本 Skill 固定提供）：

| 键 | 类型 | 含义 |
|-|-|-|
| `case_id` | string | 来自 case.json 的 id/case_id/challenge_id，缺省用输入文件名 |
| `status` | string | `in_progress \| partial \| complete \| failed`，须与实际完成度一致 |
| `sources` | array | 每条事实/引用的来源：`{ref_id, locator, quote, kind}` |
| `uncertainty` | array | 无法确认 / 冲突 / 缺失 / 需人工确认项 |
| `pending_human` | array | 需人工确认的动作清单，与 uncertainty 对齐 |
| `change_log` | array | 每次改动的 `{ts, step, note, level}` |
| `errors` | array | 运行期错误（坏 JSON、缺文件等），不中断 |
| `degradations` | array | 降级说明（超长、无法解析、缺软件等） |
| `runtime` | object | `skill_version`、`started_at`、`ended_at` 等 |

若 output_contract 的键与标准键同名，保留标准键并在 `degradations` 记录冲突，不覆盖。

## 2. 事实溯源（locator + 逐字 quote）

每个关键数值、单位、样本量、显著性、图注、作者/年份/期刊/DOI，都要有来源条目：

```json
{"ref_id": "materials/exp1.csv", "locator": "row 12", "quote": "n = 48",
 "kind": "numeric", "confidence": "high"}
```

- `locator` 是材料内的位置（段落、行号、单元格、页码），材料级位置写文件相对路径。
- `quote` 必须是材料中的逐字片段；无法逐字截取时写 `[无法逐字截取]` 并说明原因。
- 同一指标两个来源不一致时，两个都记录，`kind` 标 `source_conflict`。

## 3. 稿件证据标记

- 事实性断言用 `[S:来源ref_id]` 标注；`ref_id` 必须能指回 material_index.json 的
  材料 id 或 result.json `sources` 的 `ref_id`。
- 文献引用用 `[C:键]`；参考文献区按行定义：`[C:键]: 作者. 题目. 期刊 年份;卷(期):页. DOI`。
- 缺项/待确认显式标注：`[缺失]`、`[待确认]`、`[冲突待确认]`、`[无法解析]`，并同步写进
  `uncertainty`/`pending_human`，不允许硬填或编造。

## 4. change_log 约定

- 每条 `{ts, step, note, level}`，`ts` 为 UTC ISO-8601，`step` 为
  `init|index|draft|revise|verify|finalize|manual` 之一。
- 脚本改动走 `python3 scripts/main.py log --step ... --note ...`，同时追加到
  `/app/work/change_log.jsonl`（JSON Lines）并合并进 result.json；`finalize` 幂等去重。
- 每次实质修改（补证据、改数值、删错引、加章节）都要留痕，禁止静默覆盖。

## 5. 材料索引（material_index.json）

`scripts/main.py index` 生成骨架，Agent 逐材料补充：

```json
{"id": "materials/exp1.csv", "kind": "text", "status": "ok",
 "text_preview": "...", "detected_numbers": ["48"],
 "key_claims": [{"claim": "...", "locator": "...", "quote": "...", "confidence": "high"}],
 "citations": [{"key": "smith2020", "author": "...", "year": 2020, "journal": "..."}]}
```

`status` 只取 `ok|oversized|unparsable|unsupported_kind|unreadable`；非 `ok` 时
`key_claims` 留空并在 `degradations` 说明，不得凭文件名/领域标签补写内容。

## 6. 引用格式的最小可核格式

文献条目统一为 `[C:键]: 作者. 题目. 期刊 年份;卷(期):页. DOI`。取不到全文时只用给定
元数据；缺失字段写 `unknown`，禁止杜撰 DOI / 卷期 / 页码。
