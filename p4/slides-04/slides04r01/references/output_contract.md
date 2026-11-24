# 输出契约：result.json 字段说明

`/app/output/result.json` 是每个案例最终必须产出的结构化结果。它由两部分组成：

- **题目 output_contract 的全部字段**：若 `/input/case.json` 自带 `output_contract`，则以其键为准原样回填（缺值给 `null`/`[]`，不改名、不删未知键）；否则使用下方三契约字段。
- **本 Skill 的元数据字段**：`case_id` / `status` / `sources` / `uncertainty` / `pending_human` / `errors` / `degradations` / `change_log` / `runtime`。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 输入标识，来自 `case.json.case_id`/`id`/`armiId`，回退到 `task.md` 的 `challenge_id`，最低为 `unknown_case` |
| `status` | string | `ok` \| `partial` \| `insufficient_evidence` \| `input_error`（运行时失败也记 `failed`）。诚实优先 |
| `presentation` | object | 可编辑演示文件契约（见下） |
| `evidence_map` | object | 页级证据映射契约（见下，本 Skill 变体核心） |
| `render_record` | object | 渲染与版本记录契约（见下） |
| `sources` | array | 输入清点：`{rel, path, kind, size_bytes}` |
| `uncertainty` | array | 逐条降级记录 `{code, page?, detail}` |
| `pending_human` | array | 需人工裁定的结论/缺口 `{claim_id?, claim?, reason}` |
| `errors` | array | 运行期错误 `{code, phase?, detail}` |
| `degradations` | array | 降级处置 `{code, detail}` |
| `change_log` | array | `{at, phase, detail, status?}`，幂等去重 |
| `runtime` | object | `started_at / finished_at / phase_order` |

## 1. presentation（可编辑演示文件）

```json
{
  "pptx_path": "/app/output/deck.pptx",
  "pdf_path": "/app/output/deck.pdf",
  "png_paths": ["/app/output/slide1.png"],
  "slide_count": 12,
  "page_plan": [
    {"index": 1, "title": "封面", "claim": "…", "layout": "Title Slide",
     "notes": "讲稿", "refs": ["materials/paper.txt#L3-5"]}
  ]
}
```

- `page_plan` 每页至少含 `{index, title, claim, layout, notes, refs}`。
- 可编辑 Office/slide 产物**必须**同时给真实渲染的 PDF/PNG 预览（`pdf_path` 以 `%PDF-` 开头、`png_paths` 以 `\x89PNG` 开头），否则记 `uncertainty`。

## 2. evidence_map（页级证据映射，schema v2）

本 Skill 的变体重点：证据粒度比「只给 refs」更细，**每条结论都带 locator + 逐字 quote**。

```json
{
  "schema": "evidence-map/v2",
  "pages": [
    {
      "page_index": 2,
      "page_title": "Method",
      "claims": [
        {
          "claim_id": "p2-c1",
          "claim": "We evaluated on the XYZ benchmark with 1,024 samples.",
          "type": "fact",
          "status": "verified",
          "evidence": [
            {
              "locator": {"source": "extracted/paper.txt", "page": 3,
                          "line_start": 45, "line_end": 45},
              "quote": "We evaluated on the XYZ benchmark with 1,024 samples.",
              "verbatim": true,
              "confidence": "exact",
              "matcher": "flexible_whitespace",
              "score": null
            }
          ],
          "notes": null
        }
      ]
    }
  ],
  "pending_human": [],
  "summary": {"pages": 12, "claims_total": 40, "verified": 35,
              "partial": 3, "source_conflict": 1, "pending_human": 1}
}
```

字段语义：

- `claim.type`：`fact`（事实）、`number`（数值）、`figure`（图）、`table`（表）、`method`（方法）、`quote`（引用）。
- `claim.status`：
  - `verified` —— 至少一条 evidence `confidence=exact`；
  - `partial` —— 只有 `fuzzy` 匹配，需人工复核 quote 是否完全对应；
  - `source_conflict` —— 多个来源逐字片段不一致（仅数值型触发），两个都保留；
  - `pending_human` —— 未定位到可核对片段，不编造 quote。
- `evidence.locator`：`{source, page?, line_start, line_end}`；`page` 由换页符 `\f` 推导，无换页符时省略。
- `evidence.quote`：**材料原文逐字片段**（`text[start:end]`），不是对结论的转述。
- `evidence.confidence`：`exact`（容忍换行/多余空白、忽略大小写）或 `fuzzy`（句级词元重叠 ≥0.5）。
- `evidence.matcher`：`flexible_whitespace` / `sentence_overlap`，记录匹配算法，便于复核。

**硬规则**：未定位到的结论写 `pending_human`，`quote` 一律留空，禁止脑补；冲突信息不隐藏，标 `source_conflict` 并保留两个来源。

## 3. render_record（渲染与版本记录）

```json
{
  "source_files": [{"rel": "paper.pdf", "path": "…", "kind": "pdf",
                    "size_bytes": 123456, "sha256": "…"}],
  "template_file": "/input/template.pptx",
  "layouts_used": [{"layout": "Title Slide", "slides": [1],
                    "substitute_for": null}],
  "fonts": [{"font": "Calibri", "role": "body", "fallback": "Arial",
             "status": "embedded_missing"}],
  "exports": [{"path": "…", "format": "pdf", "ok": true}],
  "integrity": {"pptx_zip_ok": true, "slide_count": 12, "masters": [],
                "layouts": [], "notes_slides": [], "media": [],
                "slide_layout_map": {}},
  "media": ["ppt/media/image1.png"],
  "checks": [{"name": "overflow_heuristic", "auto": true, "result": "warn",
              "detail": "…", "hits": []}]
}
```

- `source_files` 与 `template_file` 记录源文件、模板与导出件，支撑他人独立复跑逐页核对。
- `layouts_used` 记录每页实际套用的版式；模板缺所需版式时用最接近版式并在 `substitute_for` 显式声明替代关系，不得谎称「已继承」。
- `fonts` 记录字体与回退；字体缺失不假装存在。
- `integrity` 由 `scripts/main.py --phase validate` 静态生成：pptx zip 完整性、页数、母版/版式/备注、每页版式继承、媒体。
- `checks` 记录确定性检查结果；**静态检查不等于渲染真相**，PDF/PNG 是否与 pptx 一致仍需人工核。

## status 判定（finalize）

- `ok`：pptx + evidence_map（非空）+ PDF 预览三者齐备且路径可打开；
- `partial`：pptx 或 evidence_map 至少其一存在；
- `insufficient_evidence`：无可读输入材料；
- 材料缺失、无法判断、运行失败分别写 `uncertainty` / `pending_human` / `errors`，不混为一谈。
