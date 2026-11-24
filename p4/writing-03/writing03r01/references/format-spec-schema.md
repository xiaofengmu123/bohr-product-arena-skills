# 格式规格提取与证据映射结构（format-spec-schema）

本文件是 `result.json` 中 `evidence_map` 的规范，也是"证据溯源强化"变体的核心设计。目标：把一次"材料 → 目标模板 → 可交付成品"的适配过程，拆成可逐项回查的记录，每一项都带 `locator`（来源定位）与 `quote`（逐字摘录）。

## 1. 顶层字段

```json
{
  "case_id": "<与 /input/case.json 一致>",
  "status": "partial | complete | failed | insufficient_evidence | input_error",
  "evidence_map": {
    "template_specs":   [],   // 认得准：从模板/规定中抽出的检查项
    "applications":     [],   // 排得对：规格应用到内容单元的记录
    "content_units":    [],   // 转得全：原稿内容单元清单（before/after）
    "export_checks":    []    // 交得出：导出/打开/字体/目录校验
  },
  "sources": [],
  "uncertainty": { ... },
  "pending_human": [],
  "change_log": []
}
```

除上述保留字段外，`result.json` 顶层还必须**原样回填** `case.json` 中 `output_contract` 的全部字段（例如 `deliverable` / `fact_source_map` / `version_boundary`）。`evidence_map` 是 `fact_source_map` 的细化，二者可并存；无 `output_contract` 时用三段式兜底。

## 2. 通用记录头（每条记录都必须具备）

| 字段 | 含义 | 要求 |
|---|---|---|
| `id` | 记录编号 | 稳定唯一，如 `spec-01`、`app-03`、`unit-12` |
| `quote` | 逐字摘录 | 从材料/模板原文逐字复制，不得改写或转述 |
| `locator` | 来源定位 | `文件名#章节/页码/段落`，如 `template.docx#样式清单/第2页` |
| `source_ref` | 来源 id | 指向 `sources[]` 中某个条目 |
| `evidence_level` | 证据强度 | 见 `references/evidence-rubric.md` |
| `uncertainty_note` | 不确定性 | 有具体依据才写，没有就 `null` |

`quote` 必须能在材料全文（或抽取文本）中被定位；定位不到的条目要写进 `uncertainty.limitations`，不得悄悄保留。

## 3. `template_specs`：认得准（规格检查项）

从模板文件、学校/院系规定、投稿须知中抽取**可逐项核对的规格**，不凭经验猜测。字段：

```json
{
  "id": "spec-01",
  "spec_name": "正文字号",
  "spec_value": "12 pt，Times/Liberation Serif",
  "quote": "正文用 12 磅字，西文 Times New Roman，中文宋体",
  "locator": "template.docx#格式说明/第1页",
  "source_ref": "src-template",
  "evidence_level": "template_rule",
  "uncertainty_note": null
}
```

必查维度：栏数、字号、行距、页边距、纸张尺寸、标题层级与编号、图表题注位置与字号、参考文献样式（字段顺序/作者截断/标点/中英文混排）、页眉页脚、导出格式与字体嵌入。模板版本差异或相互冲突的规定，**全部并列列出**并在 `evidence_level=source_conflict` 处注明。

## 4. `applications`：排得对（规格落位记录）

每条记录把一次"规格 → 内容单元"的落位写下来，含 before/after，供他人复核"确实改对了"：

```json
{
  "id": "app-03",
  "spec_ref": "spec-01",
  "unit_ref": "unit-12",
  "before": "正文段落 3：11pt",
  "after": "正文段落 3：12pt",
  "method": "样式绑定到 Body 样式而非直接改字号",
  "locator": "manuscript.docx#正文第3段",
  "quote": "……",
  "evidence_level": "applied_verified"
}
```

标题层级、编号、图表题注与交叉引用、参考文献条目、页眉页脚，都要有对应 `applications` 记录；不能只改外观不改层级/样式绑定。

## 5. `content_units`：转得全（内容单元清单）

转换/套用前后都要清点内容单元，证明"没有删除、合并、改写或图片化"。字段：

```json
{
  "id": "unit-12",
  "unit_type": "heading|paragraph|table|figure|formula|footnote|comment|revision|field|toc|section_break",
  "locator": "source.docx#第3节/第2段",
  "quote": "（内容锚点逐字片段）",
  "before_count": 1,
  "after_count": 1,
  "preserved": true,
  "changed": false,
  "evidence_level": "content_unit"
}
```

公式、脚注、批注、修订、域代码、分节符、目录、嵌入对象是**最易丢失**的单元，必须逐一列项；无法确认是否保留的标 `preserved=null` 并进 `pending_human`。

## 6. `export_checks`：交得出（交付校验）

导出结果的可打开性、字体嵌入、页面尺寸/页边距实测、页码与目录刷新、可编辑源文件保留，每项一条：

```json
{
  "id": "exp-01",
  "check_name": "PDF 可打开",
  "method": "以 %PDF- 魔数 + pdf-render-tool 渲染首帧",
  "result": "pass",
  "quote": "（工具输出的关键行）",
  "locator": "output/rendered.png#首帧",
  "evidence_level": "runtime_check"
}
```

## 7. 复现路径

`id → quote → locator → source_ref → evidence_level → uncertainty_note` 构成一条可独立回查的链。`change_log` 每次落盘追加一条 `{ts, action, detail}`，保证"从输入件到成品"的过程可复现。
