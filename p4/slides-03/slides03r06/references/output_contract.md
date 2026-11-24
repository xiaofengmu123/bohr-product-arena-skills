# 输出契约与中间表示规范

本文定义本 Skill 在 `/app/output/result.json` 的交付契约，以及两个结构化中间表示
`plan.json`、`figure_model.json` 的字段语义。脚本实现见 `scripts/core_impl.py`；
所有字段均须"未产出留空、显式标 `null` 或 `[]`，不得编造"。

## 1. result.json（最终交付契约）

`result.json` 必须包含三类字段：

### 1.1 标识与状态

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 案例标识；优先 `case.json.id`，其次 `case_id`、`challenge_id`、`armiId`；无法解析时为 `"unknown"`。 |
| `status` | string | 诚实状态，取值 `complete` / `partial` / `failed` / `insufficient_evidence` / `input_error`。 |
| `runtime` | object | `started_at`、`budget_seconds=900`、`budget_requests=64`、`tokens_per_response=16384`；由脚本记录，不伪造结果。 |

### 1.2 题目 output_contract 字段（默认契约）

当 `case.json` 未提供 `output_contract` 时，使用以下默认字段；若提供了
`output_contract`（dict 或字段名列表），则**原样保留其全部键**，不改名、不删未知键。

| 字段 | 说明 |
|-|-|
| `report_goal` | 可检查的报告目标、听众、时长、页数。 |
| `audience` | 听众先验、关注点、可能质疑、术语深度。 |
| `narrative` | 页面序列与取舍理由（`arc` + `tradeoffs`），非按论文目录搬运。 |
| `pages` | 页级结构数组，每页含 `page_index`、`title`、`single_claim`、`evidence_strength`、`figures`、`speaker_notes`、`source_refs`。 |
| `editable_deck` | 可编辑演示文件：`path`、`format`、`template`。 |
| `evidence_map` | 每页主张 → 来源定位（locator + 逐字 quote），未改写事实。 |
| `qa_prep` | 机制/数据/对照/局限四类追问的预判回答与可引用页面。 |
| `rendering` | 源文件、导出件（PDF/PNG 预览）、字体、模板、修改记录、`figure_model_summary`。 |

### 1.3 运行时元信息

| 字段 | 说明 |
|-|-|
| `sources` | 实际读取的来源清单：`path`、`kind`、`read`、`used`。 |
| `uncertainty` | 无法判断、材料缺失、证据不足，逐条说明。 |
| `pending_human` | 需要人工确认的事项（如版权、公式重排、模板冲突）。 |
| `errors` / `degradations` | 读取/解析错误与降级原因。 |
| `change_log` | 时间戳 + 动作 + 影响范围的增量记录。 |
| `contract_fields` | scaffold 时实际采用的 output_contract 键列表（自描述字段，供 validate 复核"契约键是否齐备"）。 |

## 2. plan.json（叙事规划层）

中间表示之一，供人工或 Agent 逐页复核后再合并。示例：

```json
{
  "case_id": "demo-case-1",
  "report_goal": {
    "goal": "让评委认可方法新颖性",
    "audience": "同行评审",
    "duration_min": 15,
    "page_cap": 12
  },
  "audience": {
    "prior": "熟悉统计方法，不熟悉本领域材料",
    "concerns": ["样本量", "对照设计"],
    "term_depth": "术语只解释一次"
  },
  "narrative": {"arc": ["问题", "方法", "结果", "结论"], "tradeoffs": ["略去机制推测页"]},
  "pages": [
    {
      "page_index": 1,
      "title": "研究问题",
      "single_claim": "一句话可讲结论",
      "evidence_strength": "high|medium|low",
      "figures": ["fig1"],
      "speaker_notes": "讲解要点，不与页面文字重复",
      "source_refs": [{"kind": "text|figure|table", "locator": "paper.pdf#p1", "quote": "逐字摘录"}]
    }
  ],
  "qa_prep": [
    {"category": "method|data|control|limitation", "question": "…", "answer": "…", "page_index": 2}
  ]
}
```

规则：每页恰好一条 `single_claim`；`evidence_strength` 与证据匹配（结果对比用
`high`，机制推测用 `low`）；每个关键结论必须在 `source_refs` 中给 `locator` 与
逐字 `quote`，缺失时写入 `uncertainty`，不补造出处。

## 3. figure_model.json（图表事实模型层）

对每个真正上页的图/表/公式建立事实卡，作为证据映射的第二来源：

```json
{
  "case_id": "demo-case-1",
  "figures": [
    {
      "id": "fig1",
      "kind": "figure|table|equation|diagram",
      "locator": "paper.pdf#p3 Figure 1",
      "quote": "逐字摘录的图注或关键数值句",
      "extracted_numbers": [{"label": "n", "value": "30", "unit": null}],
      "caption": "图注",
      "availability": "ok|missing|low_resolution|formula_lost|font_issue",
      "pages_used": [1],
      "notes": ""
    }
  ],
  "unresolved": [{"id": "fig5", "reason": "源 PDF 缺图，无法核验"}]
}
```

`availability` 为 `missing` / `low_resolution` / `formula_lost` / `font_issue` 时，
页面须注明"图缺失/不可用"，并记入 `rendering` 与 `uncertainty`，不生成伪造图。

## 4. 合并与校验

`scripts/main.py update` 把 `plan.json` + `figure_model.json` 合并回
`result.json`：`pages`/`report_goal`/`audience`/`narrative`/`qa_prep` 取自 plan，
`evidence_map` 由 plan 的 `source_refs` 与 figure_model 的 `pages_used` 共同推导
（仅当 locator 与 quote 同时非空才收录）。`scripts/main.py validate` 检查契约
完整性与证据溯源，`--strict` 用于最终交付前的硬门槛。
