# Deck IR 规格（slides-01 中间表示）

Deck IR 是本 Skill 的核心设计：一份**渲染无关**的 JSON，把“论文内容组织”与“幻灯片渲染”解耦。
内容正确性在渲染前由 `core.validate_ir` 单独校验，渲染层（`build.py`）不参与内容判断。

## 完整示例（示意，非正式答案）

```json
{
  "schema_version": "1.0",
  "meta": {
    "title": "论文标题",
    "audience": "组会 15 分钟",
    "talk_minutes": 12,
    "slide_cap": 12,
    "template": "template.pptx"
  },
  "spine": {
    "research_question": "论文要回答的问题",
    "gap": "已有方法的缺口",
    "approach_claim": "本文的核心做法主张",
    "key_evidence": ["支撑结论的关键证据一句话"],
    "conclusion": "论文结论",
    "limitations": ["局限 1"]
  },
  "figure_index": [
    {"id": "Figure 2", "source_id": "paper.pdf", "locator": "p4",
     "caption_quote": "图注逐字片段"}
  ],
  "slides": [
    {
      "index": 1,
      "role": "result",
      "claim": "图 2 显示方法 A 在 42% 样本上优于基线",
      "bullets": ["A vs 基线的核心差异", "42% 这一数值来自 Table 1"],
      "figure_ref": "Figure 2",
      "notes": {
        "transition": "从方法页过渡到结果页",
        "emphasis": "强调 42% 与统计显著性",
        "q_backup": "被问样本量时给出补充证据"
      },
      "evidence": [
        {"source_id": "paper.pdf", "locator": "p4 Figure 2 图注",
         "quote": "逐字命中的原文片段，含数字与单位"}
      ]
    }
  ]
}
```

## 校验顺序（与旧流水线不同的关键取舍）

1. **契约骨架**：先落盘合法 `result.json`（`--skeleton`），任何中断都不空手。
2. **证据/图表交叉校验**（渲染前）：`--verify-quotes` 用 `work/materials.txt` 逐字核验 quote；
   并核对 `slides[].figure_ref/table_ref` 必须命中 `figure_index`，且该编号必须真实出现在材料文本中。
3. **IR 模式校验**：字段、role 枚举、页数上限、要点密度、含数值主张必须带证据。
4. **渲染**：IR → pptx（模板真继承）或 SVG+PNG+PDF 降级。
5. **渲染后核验**：`slides` 数 == PDF 页数，写入 `compliance_checks`。

## 与评分维度的对应

| 评分维度 | IR/脚本落点 |
|---|---|
| 主线提取（非段落搬运） | `spine` 四要素 + 页序约束 |
| 页结构与信息分层 | 一页一 claim；bullets ≤8、每条 ≤300 字符 |
| 图表引用与证据保留 | `figure_index` + `figure_ref` 交叉校验 + 逐字 quote |
| 讲解逻辑与备注 | `notes.transition/emphasis/q_backup`（与页面文字分离） |
| 模板约束与版面适配 | `build.render_pptx` 用模板 master/layout 的 `add_slide(layout)`，只填占位符 |
| 导出渲染与可编辑性 | 真继承 pptx + soffice 转 PDF + 页数核验；无软件时 SVG/PNG/PDF 降级并如实标注 |

## 字段速查

- `role`：title / background / method / result / discussion / limitation / conclusion / other。
- `evidence.quote`：必须逐字命中材料；冲突两源都记并标 `source_conflict`；未报告写 `not_reported`。
- `locator`：论文定位，如 `paper.pdf#p4`、`paper.md#p3` 或 `sec 3.2`。
- `source_id`：材料相对路径，如 `paper.pdf`、`materials/notes.md`。
