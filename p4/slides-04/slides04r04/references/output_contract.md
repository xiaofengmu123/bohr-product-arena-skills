# result.json 输出契约（全字段说明）

本文件定义 `/app/output/result.json` 的结构。**原则：先写合法骨架，再增量填充；
任何字段缺失时用 `null` / `[]` 占位，不得删除未知键，不得编造数值。**

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 从 `/input/case.json` 提取的案例标识；取不到时为 `null` 并降级 `input_error` |
| `status` | string | 是 | `ok` / `partial` / `insufficient_evidence` / `input_error` / `failed` / `in_progress` |
| `sources` | string[] | 是 | 全部实际读取的源文件路径（论文、模板、图、提取文本） |
| `uncertainty` | object[] | 是 | 逐条限制/偏差，见下；未满足项必须写这里，不得假装通过 |
| `pending_human` | object[] | 是 | 需要人工补充/裁决的事项（如"有引文缺定位"） |
| `change_log` | object[] | 是 | 时间戳 + 动作 + 原因；骨架阶段的 `init` 条目必须保留 |
| `degradations` | object[] | 否 | 降级记录（坏编码、超大文件跳过等） |
| `errors` | object[] | 否 | 致命/可恢复错误摘要；有错误则 `status` 不得为 `ok` |
| `runtime` | object | 否 | `elapsed_seconds` / `model_requests` / `pages_reworked`，供对照而非自述 |

## 契约字段（题目固定三件套）

### 1. `presentation` — 可编辑演示文件

```json
{
  "pptx_path": "/app/output/deck.pptx",
  "pdf_path": "/app/output/deck.pdf",
  "png_paths": ["/app/output/slide-01.png", "..."],
  "slide_count": 12,
  "page_plan": [
    {"index": 1, "title": "...", "claim": "...", "layout": "...",
     "notes": "讲稿备注", "refs": [{"locator": "...", "quote": "..."}]}
  ]
}
```

- `pptx_path` 必须指向真实存在的可编辑文件；`pdf_path` / `png_paths` 是真实渲染预览。
- 可编辑 Office/slide 产物**必须**同时给出 PDF/PNG 预览；导出失败时路径置 `null` 并记 `E9_EXPORT_FAILED`。
- `page_plan` 是逐页主张与讲稿，是 `evidence_map` 的输入。

### 2. `evidence_map` — 页级证据映射

每页一项；每个 `ref` 必须带 `locator`（`源文件#页码` 或 `源文件:行号`）与**逐字 `quote`**。

```json
[
  {
    "page": 1,
    "claim": "该研究用 X 方法在 Y 数据上取得 Z 结果",
    "refs": [
      {"locator": "paper.txt#p3", "quote": "原文逐字片段",
       "line": 12, "match": "exact", "evidence_status": "verified_quote"}
    ],
    "evidence_status": "verified_quote"
  }
]
```

`evidence_status` 取值：

- `verified_quote`：quote 在 locator 指向的提取文本中**精确**命中。
- `partial_quote`：本页部分 ref 精确命中、部分未命中。
- `inferred`：指向了来源但未提供逐字 quote（模型推断，需人工复核）。
- `not_reported`：无法定位，不得改写事实。
- `source_conflict`：多个来源对同一指标给出不一致值，全部记录。

### 3. `render_record` — 渲染与版本记录

```json
{
  "source_files": ["/input/materials/paper.pdf"],
  "template_file": "/input/materials/template.pptx",
  "layouts_used": [{"slide": 1, "layout": "slideLayout3.xml", "placeholder_inherited": true}],
  "fonts": [{"requested": "Times New Roman", "available": false, "fallback": "SimSun"}],
  "exports": [{"type": "pptx", "path": "...", "ok": true},
              {"type": "pdf", "path": "...", "ok": true},
              {"type": "png", "path": "...", "ok": true}],
  "integrity": {"pptx": {...}, "pdf": {...}, "pngs": [...]},
  "media": [{"name": "ppt/media/image1.png", "bytes": 12345}]
}
```

- `layouts_used` 由 `scripts/main.py validate` 从 pptx XML 关系与占位符真实读出；
  `placeholder_inherited=false` 会写入 `uncertainty` 的 `E6_PLACEHOLDER_MISSING`。
- 模板缺所需 layout 时，用最接近的 layout 显式命名并记录替代关系，不得谎称"已继承"。
- 字体缺失时用 font-discovery 查可用字体并记录 `fallback`，不得假装字体存在。

## uncertainty 条目

```json
{"code": "E10_QUOTE_NOT_LOCATED", "page": 3, "detail": "...",
 "confidence": "low", "auto_checkable": true}
```

代码清单见 `references/checklist.md`。`confidence ∈ high/medium/low`；
`auto_checkable=true` 表示确定性脚本可判，`false` 表示只能人工核验。

## 状态与诚实降级

- 材料不足 → `insufficient_evidence`，不得编造数据或结论。
- 主产物缺失但证据/来源存在 → `partial`；输入不可解析 → `input_error`；脚本崩溃 → `failed`。
- `seal` 只会把 `in_progress` 推进或把状态**降级**，不会把不完整结果升为 `ok`。
- 部分结果也要完整落盘：骨架阶段就写好所有字段，后续只做增量填充。

> 下例仅为字段示意，标注为**示例**，不是任何正式案例结果。
