# 输出契约与中间表示（IR）说明

本文件定义 `result.json`（最终交付物）与 `plan.json`（中间表示，工作产物）的字段语义。
脚本只用 Python 标准库；所有字段读写均容忍缺失并显式标注，不编造。

## 1. result.json（写入 `/app/output/`）

`result.json` 始终由 `scripts/main.py` 组装，不手写，以保证重复运行一致。

### 1.1 必含字段

| 字段 | 类型 | 语义 |
|-|-|-|
| `case_id` | string | 案例标识，优先 `case.json.id`，其次 `case_id`，再其次 `armiId`；缺失时为 `"unknown"` |
| `status` | string | `complete` / `partial` / `insufficient_evidence` / `input_error` / `failed`，只按实际证据取其一 |
| `sources` | array | 实际读取/发现的来源：`path`、`type`、`read`（是否已读） |
| `uncertainty` | array | 无法判断、材料缺失、证据不足、公式丢失等逐条说明 |
| `pending_human` | array | 需要人工补材料或决策的待办项 |
| `change_log` | array | `timestamp + action + scope` 的增量记录 |
| `runtime` | object | `generator`、`generator_version`、`started`、`elapsed_s`、`issues` |

### 1.2 输出契约字段（output_contract）

若 `/input/case.json` 提供 `output_contract`，则**原样保留其全部字段**（键名不改、未知键不删、缺值填 `null`/`[]`），并在其后追加上述元数据字段。若未提供，使用以下默认契约：

| 字段 | 语义 |
|-|-|
| `report_goal` | 可检查目标：`objective`、`checkable_outcomes[]`、`minutes`、`max_slides` |
| `audience` | 听众：`prior`（先验）、`concerns[]`、`likely_questions[]`、`term_depth` |
| `narrative` | 叙事：`spine`（主线）、`choices[]`（取舍决策及理由）、`excluded[]`（不上页内容及理由） |
| `pages` | 页级结构数组，每页：`page_index`、`title`、`single_claim`（唯一主张）、`evidence_strength`（high/medium/low）、`figures[]`、`speaker_notes`、`source_refs[]` |
| `editable_deck` | `path`、`format`、`template` |
| `evidence_map` | 每页主张 → 来源定位（可由 `pages.source_refs` 确定性导出） |
| `qa_prep` | 问答预演：`category`（mechanism/data/control/limitation/other）、`question`、`answer`、`citable_pages[]` |
| `rendering` | `source_files[]`、`export_files[]`（PDF/PNG 预览）、`fonts[]`、`template`、`change_log[]` |

## 2. 中间表示 plan.json（写入 `/app/work/`）

这是本 Skill 的核心：**内容决策（Agent 负责）与契约合规（脚本负责）解耦**。
Agent 在 `/app/work/plan.json` 增量写入以下字段，`main.py` 每次运行都先校验、再组装：

```json
{
  "case_id": "...",
  "report_goal": { "objective": "...", "checkable_outcomes": ["..."], "minutes": 12, "max_slides": 8 },
  "audience": { "prior": "...", "concerns": ["..."], "likely_questions": ["..."], "term_depth": "medium" },
  "narrative": { "spine": "问题—方法—结果—结论", "choices": [{"decision":"...", "reason":"..."}], "excluded": [{"item":"...", "reason":"..."}] },
  "pages": [
    {
      "page_index": 1,
      "title": "...",
      "single_claim": "本页唯一可讲结论",
      "evidence_strength": "high",
      "figures": [{"ref": "Fig 2", "locator": "paper.pdf#p3"}],
      "speaker_notes": "只展开这一句，不重复页内文字",
      "source_refs": [{"locator": "paper.pdf#p3", "quote": "材料中逐字原文片段"}]
    }
  ],
  "evidence_map": [],
  "qa_prep": [{"category": "limitation", "question": "...", "answer": "...", "citable_pages": [1]}],
  "editable_deck": { "path": "/app/output/deck.pptx", "format": "pptx", "template": "..." },
  "rendering": { "source_files": ["..."], "export_files": ["..."], "fonts": ["..."], "template": "..." },
  "sources": [{"path": "...", "type": "pdf", "read": true}],
  "uncertainty": ["..."],
  "pending_human": ["..."],
  "change_log": [{"timestamp": "...", "action": "...", "scope": "plan.json"}]
}
```

## 3. 证据溯源硬规则

1. 每个 `pages[i].source_refs[j]` 必须同时含 `locator`（定位，如 `paper.pdf#p3` 或 `#Fig2`）与 `quote`（**逐字**引用，未改写）。
2. 缺 `locator` 或 `quote` 记 `error`，`status` 不得为 `complete`（`scripts/main.py` 自动执行）。
3. 同一指标两处来源不一致时，两处都记，并标 `source_conflict: true`，不隐藏。
4. 无来源支撑的论断：`evidence_strength` 标 `low`，写入 `uncertainty`，不得补造出处。
5. `evidence_strength=high` 而主张含“可能/初步/推测/suggest”等措辞时，`main.py` 给出 warning 提醒复核强度标注。

## 4. 校验顺序（validation-first gates）

```
discover 输入 → 落盘 partial 骨架 → 校验 plan schema → 校验证据 locator/quote
           → 强度措辞复核 → 组装 result.json → 校验可编辑件/预览存在 → 定 status
```

任何一关失败都只降级 `status`，绝不伪造字段或分数；`work/validation_report.json` 记录全部 issue 供复核。
