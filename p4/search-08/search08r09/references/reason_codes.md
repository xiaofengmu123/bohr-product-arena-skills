# 理由码字典与协议模板

## 1. 理由码字典（decision + reason_code）

`decision ∈ {include, exclude, uncertain}`；`reason_code` 为可回溯判据。

| reason_code | 含义 | 判据落点 |
|---|---|---|
| `include` | 符合全部纳入标准 | 标题/摘要逐字片段 |
| `ex_population` | 研究对象/人群不符 | 人群描述片段 |
| `ex_intervention` | 干预/暴露不符 | 干预描述片段 |
| `ex_outcome` | 结局指标不符 | 结局描述片段 |
| `ex_design` | 研究设计类型不符 | 设计描述片段 |
| `ex_language` | 语言不符 | 语言字段 |
| `ex_duplicate` | 同一研究副本 | DOI/标题 |
| `ex_not_primary` | 非目标研究类型（评论/编辑/会议摘要） | 文献类型字段 |
| `ex_no_data` | 无可判断的标题/摘要数据 | `not_reported` |
| `uncertain` | 证据不足，暂不能判定 | 缺摘要/边界重叠 |
| `pending_human` | 必须由研究者最终确认 | 冲突僵持/仅摘要 |

边界处置：仅摘要、语言不符、对象部分重叠 → `uncertain` 或对应 `ex_*` + `pending_human`，
并注明 `not_reported`；**不得**用「不相关」裸排除，必须落到具体判据片段。

## 2. 协议模板（protocol.json）

```json
{
  "case_id": "来自 case.json",
  "research_question": "研究问题（PICO/PICOS 拆解）",
  "search_query": "检索式原文 + 数据库/字段/时间范围/语种/检索日期",
  "inclusion_criteria": ["纳入标准 1", "纳入标准 2"],
  "exclusion_criteria": ["排除标准 1", "排除标准 2"],
  "criteria_version": "1.0（定版即冻结，改版给新版本号）"
}
```

## 3. 审计日志格式（audit_log.json）

各环节记录输入/输出条数并闭合：

```json
{
  "audit": {
    "retrieval":    {"input": N, "output": N, "closed": true},
    "deduplication":{"input": N, "merged": M, "output": N-M, "closed": true},
    "screening":    {"input": N-M, "included": I, "excluded": E, "pending": P,
                     "closed": (N-M)==(I+E+P)},
    "closed": true
  }
}
```

闭合等式：**检索数 − 去重合并数 == 唯一数 == 纳入 + 排除 + 待定**。任一环节不闭合，
先修 `judgments.json` 再重跑 `finalize`，不改脚本掩盖问题。

## 4. 去重判据

- 精确合并：`doi` 规范化相等 **或** 标题规范化相等 → 合并为同一研究（keeper 取有 DOI 或
  摘要更完整者），保留 `merge_reason`（`same_doi` / `same_title` / `same_title_doi_differs`）。
- 近似标记：标题相似度 0.85–0.95 → `possible_duplicate`（`action=manual_review`），
  **不自动合并**，防误并不同研究。
