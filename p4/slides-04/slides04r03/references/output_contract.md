# result.json 输出契约字段说明

`/app/output/result.json` 是唯一正式结果入口。字段分三组：身份与状态、题目契约、运维记录。全部字段在骨架阶段先存在（空值/null/[]），后续增量填充；序列化采用 `sort_keys=True`（canonical JSON），保证同输入复跑一致。

## 一、身份与状态

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 从 case.json 的 `case_id`/`id`/`armiId`/`challenge_id` 提取，缺失为 `unknown-case` |
| `status` | string | `ok \| partial \| failed \| insufficient_evidence \| in_progress`；骨架阶段为 `in_progress` |
| `sources` | object | `{input, case_json, task_md, materials[]}`；materials 每项 `{path,type,size,sha256_8}` |
| `uncertainty` | array | 每项 `{code, page?, detail}`；未满足项/缺口在此逐条声明 |
| `change_log` | array | 每项 `{at, event, detail}`；`at` 用 `--now` 注入，记录骨架创建与合并 |
| `errors` / `degradations` / `pending_human` | array | 兼容字段：崩溃、降级、需人工裁定分别记录 |

## 二、题目契约字段（无显式 output_contract 时的内置三字段）

### `presentation`（可编辑演示文件）

```json
{
  "pptx_path": "/app/output/slides.pptx",
  "pdf_path": "/app/output/slides.pdf",
  "png_paths": ["/app/output/page1.png"],
  "slide_count": 10,
  "page_plan": []
}
```

- `page_plan[]` 每页：`{index, title, claim, layout, notes, refs}`。
  - `claim`：该页一句话主张，必须能指回源材料。
  - `layout`：模板版式名；缺所需版式时写替代关系（如 `"layout":"content", "fallback_for":"section_header"`）。
  - `notes`：讲稿备注，与 pptx 备注页一致。
  - `refs`：`[{locator, quote}]`，与 `evidence_map` 同源。

### `evidence_map`（页级证据映射）

```json
[
  {
    "page": 1,
    "claim": "……",
    "evidence_status": "located | partially_located | not_reported | source_conflict",
    "refs": [
      {"locator": "materials/paper.pdf#p3 或 extracted/paper.txt:42", "quote": "原文逐字片段"}
    ]
  }
]
```

- 硬要求：每条 `refs[]` 必须含**非空 `locator`** 与**非空逐字 `quote`**；缺失由 `validate_deck.py` 记入 `render_record.evidence_issues`。
- 同一指标多来源不一致时，两个都记录并标 `source_conflict`；不虚构引用、DOI、数值。

### `render_record`（渲染与版本记录）

```json
{
  "source_files": [], "template_file": null,
  "layouts_used": [], "fonts": [], "exports": [],
  "integrity": null, "media": [],
  "overflow_candidates": [], "evidence_issues": [], "issues": [],
  "generated_at": "…"
}
```

- `layouts_used[]`：`{layout_num, layout_name, slides[]}`，由 slide rels→slideLayout 反查得到，证明继承真实发生。
- `fonts[]`：slide/theme XML 中收集的 typeface（去重排序）。
- `exports[]`：`{kind, path, status}`，覆盖 pptx/pdf/png。
- `integrity`：`{pptx_zip, pdf_magic, png_magic}`；`pptx_zip` 用 `zipfile.testzip()`。
- `media[]`：`{name, size}`；`overflow_candidates[]`：溢出启发式（仅提示，不替代渲染）。

## 三、确定性约定

1. 时间戳一律经 `--now` 注入；生产默认当前 UTC，自检用 `fixed` 哨兵。
2. 所有 JSON 用 `sort_keys=True, ensure_ascii=False, indent=2` 写出并带末尾换行。
3. `result.json` 不内嵌自身输出路径；`sources` 只引用输入路径，保证不同输出目录两次运行字节一致。
4. 脚本不使用随机数；需要稳定顺序处显式 `sorted()`。

## 四、状态取值语义

- `ok`：三契约字段齐全且渲染件存在，无未解决硬性问题。
- `partial`：有产物但存在缺口（缺版式/缺字体/图源失效等），`uncertainty` 非空。
- `failed`：无法生成主产物；`insufficient_evidence`：材料不足无法支撑页面主张。
- 禁止用 `ok` 掩盖缺失；禁止编造渲染结果、字体状态或评测分数。
