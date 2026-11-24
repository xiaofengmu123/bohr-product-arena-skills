# 理由码字典与判据规则引擎

本文件是 `scripts/core_impl.py` 中 `REASON_CODES` 与“判据规则”的说明文档，供标注者与第三人复核时对照。脚本只校验码值是否合法，不替你作出科学判断。

## 1. 理由码字典（逐条判定必须引用其中之一）

| 理由码 | 类型 | 含义 |
|---|---|---|
| `in_meets_criteria` | include | 符合全部纳入标准 |
| `in_population_outcome` | include | 研究对象与结局均符合标准 |
| `ex_not_population` | exclude | 研究对象不符 |
| `ex_not_intervention` | exclude | 干预/暴露不符 |
| `ex_not_outcome` | exclude | 结局/指标不符 |
| `ex_not_design` | exclude | 研究设计不符 |
| `ex_not_language` | exclude | 语言不符 |
| `ex_not_timeframe` | exclude | 超出时间范围 |
| `ex_duplicate` | exclude | 重复记录（已在去重环节合并） |
| `ex_not_relevant` | exclude | 与研究问题无关 |
| `ex_retracted` | exclude | 已撤稿 |
| `ex_no_abstract` | exclude | 仅有标题/无摘要且无法判断 |
| `uncertain_insufficient_info` | uncertain | 信息不足 |
| `uncertain_partial_overlap` | uncertain | 研究对象部分重叠 |
| `uncertain_abstract_only` | uncertain | 仅摘要无法定 |
| `uncertain_dual_conflict` | uncertain | 双人分歧未决 |

规则：

- 所有 `uncertain` 码都必须同时置 `needs_human=true`（除非仲裁已给出结论）。
- `ex_duplicate` 只用于“确认为同一研究的副本”，不同研究不得用该码。
- 排除不能只写“不相关”；必须用上述具体码，并附逐字 `evidence_quote`。

## 2. 判据规则引擎（标准版本管理）

纳入/排除标准被写成结构化规则，每条有稳定 `id`，逐条判定通过 `rule_ids`
回指规则，从而支持标准改版后对已筛记录回溯重判（不漂移）。

```json
{
  "criteria_version": "v1",
  "criteria_rules": [
    {"id": "I1", "type": "inclusion", "clause": "研究对象为…", "note": "对应标准第1条"},
    {"id": "E1", "type": "exclusion", "clause": "排除仅摘要且信息不足者", "note": "对应标准第2条"}
  ]
}
```

- `criteria_version` 一旦定版即冻结；若标准中途修订，必须新起版本号并重判受影响记录。
- 每条 `screening.records[*].rule_ids` 列出命中/违反的规则 id，使“判据 → 结论”可复核。

## 3. 证据片段校验规则

`evidence_quote` 必须能在该记录的 `title` 或 `abstract` 中逐字命中。脚本用
“Unicode NFKD 规范化 + 大小写折叠 + 空白折叠”后做子串匹配；不满足会写入
`degradations` 并置 `evidence_verified=false`。禁止从正文/标签/领域名补造片段。
