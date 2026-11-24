# slides-01 字段契约与中间表示

本文给出 `result.json` 与三个中间产物（`plan.json` / `figure_model.json` / `draft.json`）的字段定义。运行时以 `/input/case.json` 的 `output_contract` 为准，本文是补齐与复用说明。

## 1. result.json（交付信封）

字段分三类，全部写进 `/app/output/result.json`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 原样回填 `/input/case.json` |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error` |
| *output_contract 全字段* | 任意 | 原样回填，缺值给 `null`/`[]`，不改名不删键 |
| `editable_source` | object | `{path, format, slides, valid, check, source_ir}`，指向真实 pptx |
| `export_artifact` | object | `{pdf:{path, pages}}` 或 `{svg, png}` 回退 |
| `content_map` | array | 每页 → evidence 定位的映射 |
| `sources` | array | 输入材料清单 |
| `uncertainty` | array | 读不出/拿不准的点 |
| `pending_human` | array | 需人确认的项 |
| `errors` | array | 运行错误（不崩溃，记录后继续） |
| `degradations` | array | 降级项 |
| `change_log` / `changes` | array | `{t, action, detail}` 增量改动记录 |
| `runtime` | object | `{seconds, finished_at}` |

## 2. plan.json（页级计划，最先写）

```json
{
  "case_id": "…", "title": "…", "audience": "…",
  "minutes": 15, "slide_limit": 10,
  "narrative": "一句话主线：问题→方法→证据→结论",
  "pages": [
    {
      "id": "p1",
      "role": "title|background|method|result|discussion|limitation|conclusion|other",
      "claim": "本页主张（一句话，可陈述）",
      "purpose": "该页在主线中的位置/为什么存在",
      "bullet_points": ["改写后的短语（非整段搬家）"],
      "evidence": [
        {"type": "figure|table|equation|text|data",
         "label": "Fig. 2", "locator": "paper.pdf#p3 或 paper.md#sec3.2",
         "quote": "材料中逐字命中的原文"}
      ],
      "transition": "与下一页的衔接",
      "notes": "讲解备注（不重复页面文字）",
      "footer": "可选页脚"
    }
  ]
}
```

约束：`id` 唯一；`role` 在枚举内；`claim`/`bullet_points` 非空；每个 `evidence` 必须有 `locator` + 逐字 `quote`；页数 ≤ `slide_limit`。

## 3. figure_model.json（图表模型，独立于页计划）

先登记论文中真实存在的图/表/公式，再让页计划引用它，防止"讲到第三页引用不存在的图"。

```json
{
  "figures": [
    {"label": "Fig. 1", "kind": "data|schematic|photo",
     "caption": "图注", "locator": "paper.pdf#p3", "quote": "逐字图注原文",
     "used": true}
  ],
  "tables":  [{"label": "Table 1", "kind": "table", "locator": "…", "quote": "…", "used": false}],
  "equations": [{"label": "Eq. 1", "kind": "equation", "locator": "…", "quote": "…", "used": true}]
}
```

`main.py --step validate` 会做交叉校验：页计划里 `type=figure/table/equation` 且带 `label` 的引用，必须命中 figure_model 中的 label；`used=true` 的图表应被至少一页引用。

## 4. draft.json（成稿，可直接进 pptx）

```json
{
  "case_id": "…",
  "slides": [
    {
      "title": "页标题",
      "body": [{"text": "…", "bold": true, "level": 0, "size": 1800}],
      "notes": "讲解备注",
      "footer": "可选"
    }
  ]
}
```

若只有 `plan.json` 没有 `draft.json`，`main.py --step build` 会从 plan 自动生成草稿（claim 置顶加粗 + bullet_points），但仍建议显式写 draft 以控制层级与字号。

## 5. evidence 记录规范

- `locator`：`<材料相对路径>#<页/节>`，如 `materials/paper.pdf#p3`、`materials/notes.md#sec3.2`。
- `quote`：必须逐字命中材料原文，不转写、不改数值/单位/方法名。
- 读不出 → 在页级 evidence 写 `not_reported`，并同步进 `uncertainty`/`pending_human`；**不猜数值**。
- 冲突 → 两个来源都记录，`type` 前加 `source_conflict` 标记。
