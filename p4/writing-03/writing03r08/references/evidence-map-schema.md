# 证据映射与版本边界规范

本文件定义 `result.json` 中 `fact_source_map` 与 `version_boundary` 的记录结构、证据强度口径与常见失败类型。核心原则：**每条实质论断/改动都能指回材料中的逐字片段，证据强度与表述相称，冲突不隐藏。**

## fact_source_map 条目结构

每条记录至少含以下字段（字段名可按 case.json 的 output_contract 微调，但语义须保留）：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` / `claim_id` / `unit_id` | 是 | 唯一编号，如 `cu-001`、`c12` |
| `type` / `unit_type` | 是 | `claim`（论断）、`style_map`（样式映射）、`content_unit`（内容单元）、`ref_entry`（参考文献条目） |
| `quote` | 是 | 材料中的**逐字摘录**，经 `verify` 可在材料文本中定位 |
| `locator` | 是 | 来源定位，如 `manuscript.docx#p3`、`template.pdf#sec2` |
| `source_ref` | 是 | 指回 `sources` 清单的 `id` |
| `evidence_level` | 是 | 见下口径 |
| `transform` | 否 | `keep` / `mapped` / `renumbered` / `reflowed` / `rewritten` |
| `verified` | 否 | 布尔；`verify` 后可自动校验 `quote` 是否可定位 |

## 证据强度口径（evidence_level）

| 级别 | 适用 | 表述要求 |
|---|---|---|
| `direct_observation` | 模板/材料中可直接读到的规格值 | 直接陈述，不降级 |
| `inference` | 由材料推断的结论 | 用"据此推断/表明" |
| `convention` | 依据样式规范（如 GB/T 7714）的重排 | 标注依据的规范名 |
| `unverified` | 无法在材料中证实的项 | 写入 `uncertainty.claims_requiring_confirmation` |

禁止把"相关"写成"因果"、把"单一样式样例"写成"模板规范"、把"摘要信息"写成"正文细节"。强度不足时直接标 `unverified`，不靠弱化措辞掩盖。

## version_boundary 结构

```json
{
  "diff": [
    {"unit_id": "cu-003", "before": "…", "after": "…", "reason": "按模板样式映射", "quote": "…", "locator": "…"}
  ],
  "unresolved": ["…"],
  "material_gaps": ["…"],
  "limitations": ["…"]
}
```

- `diff`：每个非 `keep` 的内容改动都要记录改前/改后/理由，保证"可继续编辑、可逐条复核"。
- `unresolved`：无法判断、需人工确认的项，同时列入 `pending_human`。
- `material_gaps`：材料缺失（如无模板文件、只有摘要、字体缺失）时如实记录。
- `limitations`：运行受限（缺软件、超时、请求耗尽）时记录，不伪造渲染或运行结果。

## 常见失败类型（自查用）

1. **摘录无法定位**：`quote` 与材料原文有出入（改写、错字、换行被截断）。必须保持逐字一致；`verify` 会报告未定位项。
2. **定位过粗**：`locator` 只写文件名不写页/段，导致无法独立复核。至少写到"文件#节/页"。
3. **新增材料外事实**：正文出现材料未提供的数据/结论且无 `quote` 支撑。必须删除或标 `unverified`。
4. **冲突单边化**：同一字段两个来源不一致时只保留一个。必须并列进 `uncertainty.conflicting_evidence`。
5. **占位符残留**：`TODO`/`待补`/`此处填写` 未清除。`verify` 会硬性报错。
6. **状态不诚实**：材料不足仍标 `complete`。应标 `partial` 并在 `limitations` 说明。

## sources 清单条目

```json
{"id": "src-1", "type": "template|manuscript|regulation|standard|pdf",
 "title": "…", "path": "…", "doi": null, "accessed": "2026-11-24",
 "locator": "…", "verification": "verified|unverified"}
```

DOI、卷、页码只写"从该材料原文读到的值"；未读到就留 `null`，不得凭记忆补全。
