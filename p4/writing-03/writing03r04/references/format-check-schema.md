# 检查项与内容单元记录结构

本文档规范 `result.json` 中格式适配相关记录的字段与口径，供执行与复核使用。

## 1. 两类记录

- `format_specs`（模板规格/检查项）：从模板文件与格式要求中抽取的、可逐项核对的规格，对应题目“认得准”。
- `content_units`（内容单元）：稿件正文、图表、公式、脚注、批注、修订、域代码、目录等单元在适配前后的状态，对应题目“转得全”。
- `fact_source_map`（事实与来源映射）：数字、术语、引用、图表、关键论断的来源或修改记录，是 `output_contract` 三段式兜底的必填字段。

## 2. format_specs 条目结构

```json
{
  "spec_id": "SPEC-001",
  "aspect": "heading_level | page_setup | figure_caption | table_caption | reference_style | export_format | font | numbering | cross_reference",
  "spec_text": "一级标题：黑体三号、居中、段前段后各一行",
  "spec_source": {"locator": "template.docx#styles.xml", "quote": "……逐字摘录……", "source_ref": "s1"},
  "applied_value": "已应用：黑体 16pt、居中",
  "verification": "measured | asserted | unverified",
  "status": "pass | fail | partial | not_applicable | pending_human",
  "evidence": {"locator": "output.pdf#p1", "quote": "……实测描述……", "method": "font-discovery / pdf-render-tool"}
}
```

## 3. content_units 条目结构

```json
{
  "unit_id": "CU-001",
  "unit_type": "body_text | figure | table | formula | footnote | comment | revision | field_code | toc | section_break | embedded_object",
  "before": "原始内容摘要或定位",
  "after": "适配后内容摘要或定位",
  "action": "kept | reformatted | renumbered | moved | rewritten | rasterized | dropped",
  "justification": "改写/图片化/删除的理由与依据",
  "status": "pass | fail | partial | pending_human"
}
```

`action` 为 `rewritten/rasterized/dropped` 时必须填写 `justification`，不得静默处理。

## 4. fact_source_map 条目结构

```json
{
  "item_id": "FMT-001",
  "item_type": "number | term | citation | figure | table | claim",
  "check": "该数字/引用/论断对应的检查描述",
  "spec_source": {"locator": "paper.pdf#p3", "quote": "……逐字摘录……", "source_ref": "s1"},
  "applied_value": "适配后保留/修改的值",
  "verification": "measured | asserted | unverified",
  "status": "pass | fail | partial | pending_human"
}
```

## 5. 核验口径

- `measured`：经工具实测（font-discovery、pdf-render-tool、office-renderer 渲染后检查、PDF 文本抽取等）得到的值。
- `asserted`：有依据但未实测的推断，如从模板样式定义推断标题字体。
- `unverified`：无法核验，必须同时进入 `uncertainty.not_verifiable` 或 `pending_human`，只写“待实测”，不写虚假数值。

## 6. locator 规范

`locator` 用 `文件路径#页码或段落` 形式，如 `materials/paper.md#p3`、`template.docx#styles.xml`；逐字 `quote` 必须与材料全文一致（经 `verify_contract.py` 去空白后核对）。材料内定位不到时，不得伪造 locator。

## 7. 冲突与未决

同一规格出现版本差异或相互冲突的规定，全部写入 `uncertainty.conflicting_specs`，正文标“存在冲突/尚不明确”，不在记录中静默选取其一。
