# 契约与约定（Conventions）

本文定义 result.json、evidence_map、material_index、稿件标记与引用格式的规范。脚本按此读取与校验；agent 按此写入。

## 1. result.json schema

顶层键（`case_id`、output_contract 全字段为必填，其余为 Skill 附加）：

```json
{
  "case_id": "challenge-xxx",
  "status": "partial",
  "deliverable": "manuscript.md",
  "fact_source_map": {},
  "version_limits": {},
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "change_log": [],
  "errors": [],
  "degradations": [],
  "outputs": [],
  "runtime": {"started_at": "", "finished_at": "", "steps": []}
}
```

- `status` 取值：`in_progress | partial | complete | insufficient_evidence | input_error | failed`。有缺项/冲突/未解析材料时不得 `complete`。
- `sources` 元素（证据）：`{"id", "claim", "ref_id", "locator", "quote", "cite_key", "status"}`；`status` 取值 `confirmed | conflict | missing | pending | not_reported`。
- `uncertainty` 元素：`{"kind", "message", "refs", "resolution"}`；`kind` 取值 `missing | conflict | unparseable | to_confirm | limitation`。`limitations` 是必填的降级说明汇总。
- `change_log` 元素：`{"ts", "step", "note"}`，`ts` 为 ISO8601 UTC。
- output_contract 全字段**原样回填**：不改名、不删未知键；标量缺值给 `null`，空容器保留 `{}`/`[]`。若 contract 字段与 Skill 标准键同名，标准键语义优先，并写入 `degradations` 说明。

## 2. material_index.json schema

```json
{
  "case_id": "challenge-xxx",
  "generated_at": "",
  "materials": [
    {
      "ref_id": "S1",
      "path": "materials/paper.pdf",
      "type": "pdf",
      "size": 12345,
      "status": "unindexed|parsed|unparseable",
      "key_claims": [],
      "numbers": [],
      "citations": [],
      "flags": []
    }
  ],
  "literature": []
}
```

- `key_claims`/`numbers` 元素：`{"text", "locator", "quote", "unit"}`。
- `citations` 元素：`{"cite_key", "authors", "year", "journal", "volume", "issue", "pages", "doi"}`；缺失字段写 `null`，禁止杜撰 DOI。
- `flags` 元素：`{"kind", "message"}`，`kind` 取值 `missing | conflict | unparseable | to_confirm | too_long`。
- `literature` 是文献条目主表；稿件文末的 `[C:键]` 定义应与之一致。

## 3. evidence_map.json schema

```json
{
  "case_id": "challenge-xxx",
  "entries": [
    {
      "id": "E1",
      "claim": "摘要/正文/图注中的具体事实句",
      "ref_id": "S2",
      "locator": "materials/paper.pdf#p3 或 materials/note.md#L12",
      "quote": "材料中的逐字片段",
      "cite_key": "C1",
      "status": "confirmed"
    }
  ]
}
```

每个关键结论/数值/图元都要能指回 `ref_id` + `locator` + 逐字 `quote`。冲突信息不隐藏：同一指标两个来源不一致时两条都记，`status=conflict`。

## 4. 稿件内标记词汇

| 标记 | 含义 |
|---|---|
| `[S:材料ID]` | 该句事实来自某材料，如 `[S:S2]` |
| `[C:键]` | 该句论断由某文献支撑，如 `[C:C1]` |
| `[缺失]` | 材料缺失该要素（如图注） |
| `[待确认]` | 材料未给出、需人工确认 |
| `[冲突待确认]` | 材料间冲突，需人工取舍 |
| `[无法解析]` | 材料无法解析，未使用其内容 |
| `[未报告]` | 文献/材料未报告该字段 |

## 5. 引用最小可核格式

```text
[C:键] 作者. 题目. 期刊 年份;卷(期):页. DOI
```

字段缺失写 `unknown`，不得补造。每条 `[C:键]` 必须既在文末定义、又在 `literature` 或 evidence_map 中出现。

## 6. locator 约定

- PDF：`materials/paper.pdf#p3`（页号）；文本：`materials/note.md#L12`（行号）；表格：`materials/data.csv#row5`。
- locator 必须能定位到材料中的实际片段；无法定位时写 `not_reported` 并计入 `uncertainty`。

## 7. 章节模板（通用骨架，非预写答案）

按学科与题目要求的体例选择，逐节填充材料事实，不凭空补写；缺节标 `[缺失]`：

1. 摘要（Abstract）：目的、方法、结果、结论各一句，只陈述材料支持的事实。
2. 引言（Introduction）：背景与缺口，每个论断挂 `[C:键]`。
3. 方法（Methods）：对象/样本、步骤、统计方法；样本量、单位、误差范围逐字来自材料。
4. 结果（Results）：按图表顺序叙述，每个数值挂 `[S:材料ID]`，避免把趋势说成因果。
5. 讨论（Discussion）：解释结果、对比文献、局限（limitations 必写，含材料缺口）。
6. 结论（Conclusion）：与结果一致，不夸大。
7. 参考文献（References）：每条 `[C:键]` 用最小可核格式定义。
8. 数据可得性声明（Data availability）：未报告处写 `not_reported`。

