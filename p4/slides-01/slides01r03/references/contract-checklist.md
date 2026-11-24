# 输出契约与证据映射细则

## result.json 字段（原样回填 + 信封）

- 契约字段：以 `/input/case.json` 的 `output_contract` 为准，缺值给 `null`/`[]`/`{}`，不改名、不删未知键。默认七字段：`case_id, status, editable_source, export_artifact, content_map, compliance_checks, exceptions`。
- 信封字段固定追加：`sources, uncertainty, pending_human, errors, degradations, change_log, changes, runtime`。`changes` 是 `change_log` 的别名镜像，两处保持一致。
- `status` 取值与依据：
  - `ok`：draft 有效、pptx 有效、PDF 页数一致、无 errors；
  - `partial`：已交付可编辑产物但存在未决项（缺 PDF/未核验 quote 等）；
  - `insufficient_evidence`：材料不足以产出主线；
  - `input_error`：case.json 缺失/坏 JSON。

## draft.json 页级计划（Agent 手写，脚本校验）

```json
{
  "title": "Deck 标题",
  "template": "materials/template.pptx",
  "audience": "…", "duration_min": 15, "slide_limit": 12,
  "pages": [
    {
      "role": "title|background|method|result|discussion|limitation|conclusion|other",
      "claim": "本页唯一主张（一句）",
      "title": "页标题",
      "bullets": ["≤5 条短语"],
      "layout": "可选的模板版式名，如 Title and Content",
      "evidence": [
        {"source_id": "paper.pdf", "locator": "p3 / §3.2 / Fig.2",
         "quote": "材料中逐字原句", "kind": "figure|table|section|data|formula"}
      ],
      "figures": [{"ref": "Fig.2", "path": "materials/fig2.png", "caption": "…"}]
    }
  ]
}
```

规则：
- 每页一个 claim，role 合法；主线必需页齐全（问题→方法→证据→结论→限制）。
- evidence 的 `quote` 必须逐字取自材料；抽取原文存入 `/app/work/sources/<source_id>.txt` 供脚本核对（空白归一化后子串匹配）。
- 示意图、他人结果、本论文结果必须用 `kind` 与 `locator` 区分，不得张冠李戴。
- 数字、单位、方法名、结论表述搬运时不得改写。

## 模板继承（真继承，非伪继承）

`pptx_core.build_presentation_parts` 打开模板 zip 后：复制 slideMasters / slideLayouts / theme / media 等原样字节，仅替换 slide 部分，新 slide 通过 relationship 指向原版式，并把文字放入版式的 title/body/picture 占位符几何位置。判断：`editable_source.inherited_template == true`，且新 slide 的 `p:sldLayout` 关系存在。

## PDF 预览

`.pptx → .pdf` 由环境 office-renderer（LibreOffice headless）完成，脚本只校验：`%PDF-` 头、页数 == slides。无渲染器时交付可编辑 `.pptx` + 真实 SVG/PNG 预览并写 exceptions，PDF `pages=0`。
