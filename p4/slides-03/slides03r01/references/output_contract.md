# 输出契约字段定义

`result.json` 顶层字段。`case.json` 若给出 `output_contract`，其全部键原样保留；未给出的键按下方默认契约以 `null` / `[]` 占位，不得编造。

## 标识与状态

- `case_id`（string）：优先取 `case.json.id`，其次 `armiId`，再次 `case_id` / `challenge_id`；缺失则 `null`。
- `status`（string）：`complete`（主产物齐备且自检无 error）、`partial`（有可用部分但有明确缺口）、`failed`（关键步骤失败）、`insufficient_evidence`（材料不足）、`input_error`（输入不可解析）。
- `primary_artifact`（string|null）：主产物路径（可编辑 deck 或主结果文件）。
- `output_contract`（object）：从 `case.json` 原样复制的契约对象，便于复核。

## 内容组织字段

- `report_goal`（string|object|null）：可检查目标，含目标、听众、时长、页数。
- `audience`（object|null）：`prior`（先验）、`concern`（关注点）、`challenge`（可能质疑）。
- `narrative`（string|object|null）：页面序列与取舍理由；必须解释顺序与取舍，不得照搬论文目录。
- `pages`（array）：每页一个对象：
  - `page_index`（int）、`title`（string）、`single_claim`（string，恰好一条唯一主张）。
  - `evidence_strength`（`high|medium|low`）。
  - `figures`（array）、`speaker_notes`（string）、`source_refs`（array）。
- `editable_deck`（object|null）：`path`、`format`、`template`。
- `qa_prep`（array）：每项 `question`、`answer`、`page`（可引用页码）。
- `rendering`（object|null）：`source`、`preview`（PDF/PNG 路径）、`fonts`、`template`、`notes`。

## 证据映射（片段级，本版强化）

- `evidence_map`（array）：每项：
  - `page_index`（int）、`claim`（string，页级论断全文）。
  - `evidence`（array）：每项一个片段证据：
    - `fragment`（string，论断中被该证据支撑的最小片段）。
    - `source_ref`（string，材料文件名）。
    - `locator`（object）：`type`、`file`、`page`、`section`、`figure_or_table`，至少填两项。
    - `quote`（string，逐字原文，不缩写、不转述）。
    - `quote_offset`（int|null，quote 在抽取文本中的字符起点）。
    - `strength`（`high|medium|low`）。
    - `status`（`verified|conflict|missing|source_conflict`）。

## 运行记录

- `sources`（array）：实际读取的来源，每项 `path`、`type`、`read`、`pages`。
- `uncertainty`（array）：无法判断、材料缺失、证据不足，逐条说明。
- `pending_human`（array）：需人工确认或补料的事项。
- `errors`（array）：运行错误；`degradations`（array）：降级说明。
- `change_log`（array）：每项 `timestamp`、`action`、`scope`，增量记录。
- `runtime`（object）：`tool`、`version`、`budget_seconds`、`budget_requests`。

## 状态判定规则

- `complete`：主产物、预览、evidence_map、qa_prep 齐全，且 `evidence-check` 与 `validate` 均无 error。
- 其余任一关键项缺失或存在 error，如实标 `partial` / `failed` / `insufficient_evidence`，并在对应数组写清原因。

## 最小合法结果（示意结构，合成数据，非正式案例）

下面是一个结构完整的最小示例，仅用于说明字段形态；数字与 quote 均为合成占位，不得作为正式案例答案。

```json
{
  "case_id": "synthetic-case-001",
  "status": "complete",
  "output_contract": {"report_goal": null},
  "report_goal": {"goal": "让评审认可结论与证据强度", "audience": "评审", "duration_min": 12, "pages": 8},
  "audience": {"prior": "同领域但非子方向", "concern": "对照是否公平", "challenge": "样本量是否足够"},
  "narrative": "结论先行，再按证据链展开：问题 1 页、方法 1 页、结果对照 3 页、局限 1 页；舍去次要消融。",
  "pages": [
    {
      "page_index": 2,
      "title": "主结果",
      "single_claim": "本方法在 held-out 集上较 Baseline 提升 12%",
      "evidence_strength": "high",
      "figures": ["Table 1"],
      "speaker_notes": "强调同口径、同数据划分，引用 Table 1 第一列。",
      "source_refs": ["paper.pdf#p3"]
    }
  ],
  "editable_deck": {"path": "deck.html", "format": "html", "template": "plain"},
  "evidence_map": [
    {
      "page_index": 2,
      "claim": "本方法在 held-out 集上较 Baseline 提升 12%",
      "evidence": [
        {
          "fragment": "提升 12%",
          "source_ref": "paper.pdf",
          "locator": {"type": "page", "file": "paper.pdf", "page": 3, "section": "Results", "figure_or_table": "Table 1"},
          "quote": "Our method improved accuracy by 12% on the held-out set.",
          "quote_offset": 1280,
          "strength": "high",
          "status": "verified"
        }
      ]
    }
  ],
  "qa_prep": [{"question": "与哪些 baseline 比较？", "answer": "与 A、B 两个 baseline 在同划分下比较。", "page": 2}],
  "rendering": {"source": "deck.html", "preview": "deck.pdf", "fonts": "system", "template": "plain", "notes": []},
  "primary_artifact": "deck.html",
  "sources": [{"path": "/input/paper.pdf", "type": "pdf", "read": true, "pages": 12}],
  "uncertainty": [],
  "pending_human": [],
  "errors": [],
  "degradations": [],
  "change_log": [{"timestamp": "2026-11-23T00:00:00Z", "action": "scaffold", "scope": "init"}],
  "runtime": {"tool": "evidence-traced-talk-organizer", "version": "1.0.0", "budget_seconds": 900, "budget_requests": 64}
}
```
