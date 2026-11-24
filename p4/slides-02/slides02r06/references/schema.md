# 结构化中间表示 schema（schema）

本文件定义 `traceable-slide-builder` 产出的结构化 JSON 的字段与语义，用于第三方独立复核与复用。所有中间产物位于 `/app/work/`，最终结果 `/app/output/result.json`。`locator` 统一采用 `<文件名>#<页码|行号>` 或 `<文件名>#<章节>` 的定位写法。

## 1. plan.json（页计划）

```json
{
  "schema_version": "1.0",
  "title": "汇报标题或 null",
  "pages": [
    {
      "index": 1,
      "role": "title|motivation|problem|method|result|analysis|limitation|conclusion|appendix",
      "claim": "本页一句话核心主张",
      "bullets": ["要点1", "要点2"],
      "figures": ["Fig 3", "Table 1"],
      "word_budget": 60,
      "notes": "讲稿备注（可选，成稿后可并入 speaker_notes）",
      "evidence": ["e001", "e002"]
    }
  ]
}
```

| 字段 | 必需 | 说明 |
|---|---|---|
| `index` | 是 | 页序号，1 起；`record --kind plan` 按 index 幂等 upsert |
| `role` | 是 | 叙事角色，限定上述 8 类，见 SKILL.md 第 5 节 |
| `claim` / `bullets` | 至少其一 | 页面主张与要点，二者必须至少有一项非空 |
| `figures` | 否 | 引用的图/表编号，必须能在 figure_model 找到 |
| `evidence` | 否 | 指向 evidence_map 的证据 id |
| `word_budget` | 否 | 字数预算，防超页 |

## 2. figure_model.json（图/表清单）

```json
{
  "schema_version": "1.0",
  "redraw_policy": "only redraw when source numbers are verbatim-quoted; otherwise crop original or cite-only",
  "figures": [
    {
      "id": "f001",
      "ref": "Fig 3",
      "locator": "paper.pdf#p7",
      "caption_quote": "Fig. 3: ...（源文逐字）",
      "caption_unavailable": false,
      "kind": "line|bar|image|table|schematic|micrograph",
      "data_binding": "redraw|crop_cite|bitmap_no_data",
      "extracted_numbers": [
        {"label": "accuracy", "value": "0.87", "quote": "accuracy of 0.87"}
      ],
      "used_in_pages": [4]
    }
  ]
}
```

`data_binding` 三态语义：
- `redraw`：源文数值有逐字 quote，可重绘（仅此态允许重绘曲线/图表）。
- `crop_cite`：裁剪原图并引用图号/页码，不重绘、不补数值。
- `bitmap_no_data`：纯位图无法取数，只引用原图定位。

规则：`extracted_numbers` 中每个数值必须有 `quote`；无 quote 的数值不得进入页面主张。

## 3. evidence_map.json（页级证据）

```json
{
  "schema_version": "1.0",
  "evidence": [
    {
      "id": "e001",
      "page": 4,
      "role": "result",
      "kind": "number|fact|quote|citation",
      "claim": "模型达到 87% 准确率",
      "loc": "paper.pdf#p7",
      "quote": "...achieved an accuracy of 87%...",
      "status": "verified|pending|conflict"
    }
  ]
}
```

`quote` 必须为源文逐字片段（空白可归一化）；`status` 中 `verified` 表示 `check-quotes` 已通过，`conflict` 表示同一指标多个来源不一致（全部保留并标注）。

## 4. case_summary.json（输入盘点）

含 `schema_version/case_id/contract_keys/input_files/constraints/extraction_notes`。`constraints` 收拢 `audience/time_budget_min/slide_count/template/language`，缺省为 null。

## 5. self_check.json（终检）

含 `schema_version/generated/checks/error_count/warning_count/passed`。每项 `check` 形如 `result.json`、`intermediate:<where>`、`artifact:pptx`、`artifact:pdf:<name>`、`verbatim-quote`；`status` 为 `pass|warn|fail`。`passed=false` 时 `main.py validate` 退出码为 1。

## 6. result.json（交付契约）

顶层 = `case_id` + `output_contract` 全部字段（原样回填）+ 保留字段：

| 保留字段 | 类型 | 说明 |
|---|---|---|
| `status` | string | `partial|complete|failed`，诚实标注 |
| `sources` | array | 由 evidence_map/figure_model 推导，含 `ref/loc/kind/used_in` |
| `uncertainty` | object | 未决项与局限 |
| `pending_human` | array | 需人工确认的缺口 |
| `errors` / `degradations` | array | 运行时错误与降级 |
| `change_log` | array | 时间排序的 `t/step/action/status/reason` |
| `runtime` | object | 运行时间戳等 |

常见交付字段 `presentation_file/preview_files/speaker_notes/page_plan/evidence_map` 以实际 `output_contract` 为准；`figure_model` 为本包额外附加的可复核字段。

## 7. 已知边界

- 本 Skill 不实现科研绘图算法与 PPT 软件本身；重绘仅在数据可逐字复核时进行。
- 脚本只依赖 Python 标准库；PPTX 生成由运行时已装工具（python-pptx/office-renderer）完成，脚本只做 zip/pdf 结构校验与中间产物校验。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试。
