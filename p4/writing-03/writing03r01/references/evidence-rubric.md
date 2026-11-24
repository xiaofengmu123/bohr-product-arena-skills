# 证据强度分级与降级口径（evidence-rubric）

本文件规定 `evidence_level` 的取值、表述与证据强度的对应关系，以及缺数据/缺软件时的降级规则。原则：**结论的表述强度必须与证据强度相称**，不把推测写成事实，不把相关写成因果。

## 1. evidence_level 取值

| 取值 | 含义 | 适用场景 | 表述口径 |
|---|---|---|---|
| `template_rule` | 模板/规定原文明确写出 | 字号、行距、页边距等被模板逐字规定 | 可直接断言"模板要求 X" |
| `content_unit` | 从原稿清点得到的单元存在性 | 段落、图、表、公式、脚注等 | 可断言"存在/保留"，不断言正确性 |
| `applied_verified` | 已执行且经工具核验 | 导出后实测字号/字体/页码通过 | 可断言"已满足 X" |
| `applied_unverified` | 已执行但未核验 | 改了但没跑校验 | 只能说"已设置，待复核" |
| `inferred` | 从材料推断，非原文直陈 | 无明文但可从上下文推出 | 必须加"据……推断/有待确认" |
| `source_conflict` | 两处来源不一致 | 模板版本差异、互相矛盾的规定 | 并列列出，正文写"存在冲突/尚不明确" |
| `not_reported` | 材料未提供 | 找不到该规格或数据 | 写"材料未报告"，进 `pending_human` |
| `unverified` | 来源真实性未核验 | 无 DOI/无原始出处/二手转引 | `sources` 标 `verification=unverified` |

## 2. 逐字摘录（quote）规则

- `quote` 是**逐字复制**，不是摘要、改写或"大意"。中英文标点、大小写、连字符保持原样。
- 定位（locator）用 `文件#章节/页码/段落`；抽取文本时保留页码/段落号。
- 每个关键结论、数字、术语、图表编号、参考文献条目都必须有对应记录；**不新增材料外事实**。
- 一条 `quote` 在 `selftest.py` 中无法在材料根目录定位时，记入 `uncertainty.limitations` 并降级为 `applied_unverified`/`unverified`。

## 3. 表述强度对照（避免夸大）

| 证据 | 只能说 | 不能说 |
|---|---|---|
| 单篇小样本研究 | "该研究初步表明……" | "领域共识表明……" |
| 相关关系 | "A 与 B 相关" | "A 导致 B" |
| 摘要/简介 | "摘要表明……" | 补写正文细节 |
| 无原文的数字 | `not_reported` | 编造或估计 |
| 两处冲突 | "存在争议/尚不明确" | 偏向单边 |

## 4. 降级策略

- **缺材料 / 只有摘要 / 全文不可得**：只写"摘要表明"，不补正文；记入 `uncertainty.limitations`，`status` 标 `partial` 或 `insufficient_evidence`。
- **缺软件（pdf-text-tool / ocr / latex / office-renderer 不可用）**：标准库脚本仍可读纯文本；标注"预览未生成/依赖不可用"，不伪造渲染文件（不生成假 PDF/PNG）。
- **模板缺失或版本不明 / 字体缺失 / 样式冲突 / 转换失败 / 内容无法保真**：逐项写进 `degradations` 或 `uncertainty.material_gaps`，需要人工判断的写进 `pending_human`。
- **无法校验来源真实性**：`sources[]` 该项标 `verification="unverified"`，`change_log` 记录核验路径。
- **硬性失败（缺 case_id、契约字段缺失、JSON 无效）**：`selftest.py` 退出码 2，先修复再继续；修复不了保持 `status=partial/failed` 并如实写 `limitations`。
- **任何情况禁止编造**评测分数、运行结果、文献元数据、科学数据、软件可用状态。

## 5. sources 条目规范

```json
{
  "id": "src-template",
  "type": "template|paper|spec|extracted_text|rendered_preview",
  "title": "模板文件名或文献题名",
  "path": "/input/...",
  "doi": null,
  "accessed": "2026-11-24",
  "locator": "文件名#章节/页码",
  "verification": "verified | unverified"
}
```

`doi`、卷、页等信息只有在同一文献的已获取文本中确实出现时才写；没有就留 `null`，不得从记忆补写。
