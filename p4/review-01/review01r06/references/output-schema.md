# result.json 与中间产物 Schema

本文件定义 `scripts/main.py` 产物的完整字段规格。实现与本文档不一致时以
`scripts/core_impl.py` 中的枚举为准；本文档用于让他人复核字段语义。

## 1. result.json（最终产物，/app/output/result.json）

顶层字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 原样保留 `case.json` 的 case_id（或 id/task_id） |
| （契约字段） | 任意 | `case.json.output_contract` 的全部字段原样回填，不改名不删除；未知键给 `null`/`[]` |
| `status` | enum | `ok`/`partial`/`insufficient_evidence`/`input_error` |
| `sources` | list | 稿件来源：`{source_id, path, kind, readable, paragraph_count, char_count, problems}` |
| `uncertainty` | list | `{id, finding_id, type, detail}`；type 含 `source_conflict`/`ambiguous`/`not_reported`/`unverifiable` |
| `pending_human` | list | 需人工确认/补 OCR 的条目（字符串或对象） |
| `errors` | list | 解析/校验错误记录 |
| `degradations` | list | 降级处理记录（超长、无文本层、缺软件等） |
| `changes` | list | `{field, from, to, reason}` 契约键与信封键冲突等变更 |
| `change_log` | list | `{ts, step, detail}`；step ∈ init/plan/draft/check/build/selfcheck |
| `runtime` | object | `{started_at, finished_at, seconds, finalized}` 易变量只放这里 |
| `result` | object | 审查主结构，见下 |

`status` 取值：
- `ok`：六类相关 facet 均已查（有 finding 或明确 na）。
- `partial`：部分完成，`unchecked_items`/`pending_human` 写明缺口。
- `insufficient_evidence`：材料不可用（无材料/全部不可读）。
- `input_error`：case.json 损坏/坏 JSON。

`result` 子结构：

```json
{
  "verdict_summary": {
    "overall_verdict": "undetermined",
    "summary": "总评",
    "counts": {"critical":0,"major":0,"minor":0,"suggestion":0,"total":0},
    "coverage": {"fact_check":"checked", "...":"na|unchecked"}
  },
  "findings": [ /* finding[] */ ],
  "unchecked_items": [ {"facet":"...","status":"unchecked|na","reason":"..."} ],
  "evidence_map": [ /* evidence[] */ ]
}
```

## 2. finding 单条形状

```json
{
  "finding_id": "F01",
  "facet": "fact_check|citation_support|method_design|statistics_report|argument_chain|academic_norms",
  "severity": "critical|major|minor|suggestion",
  "issue_type": "factual_error|insufficient_evidence|standards_issue|suggestion",
  "verdict": "confirmed|to_review|not_checked",
  "claim": "本文论断/问题描述",
  "location": "p3#para2 或 para7",
  "quote": "逐字原文（可跨行，空白已归一化）",
  "rationale": "判断依据",
  "revision_suggestion": "可执行修改建议",
  "confidence": "high|medium|low",
  "evidence": [ {"source_id":"D1","locator":"...","quote":"..."} ]
}
```

可选字段：`cited_source_claim`（citation_support 核验到被引文献主张时填写）、
`acceptable_range`（主观判断项的可接受范围）。

校验规则（`check`/`selfcheck` 执行）：
- 必填字段非空；facet/severity/issue_type/verdict/confidence 必须落在枚举内。
- `quote` 与每条 `evidence.quote` 必须空白归一化后逐字子串命中 `/input` 材料文本。
- `finding_id` 全局唯一；`evidence.quote` 缺失 source_id/quote 报错。

## 3. 中间产物（可复核设计）

| 文件 | 内容 | 用途 |
|---|---|---|
| `work/index.json` | 材料索引：`materials[]`、`paras[]`（带 source_id/locator/seq/text）、`para_texts[]` | show/check/build 的定位与逐字命中基准 |
| `work/plan.json` | 分面审查计划：每 facet 的 `applicable`(yes/no/maybe)、`rationale`、`anchors`、预算与误报护栏 | 在阅读前把“查什么/怎么查”固定下来，保证重复运行结构一致 |
| `work/draft.json` | 分面审查工作稿：`verdict_summary`、`findings[]`、`unchecked_items[]` | 主工作产物，Agent 逐面写入 finding |
| `work/check_report.json` | 机械校验：quote 命中、字段枚举、facet 覆盖缺口 | 修正 draft 的反馈回路 |
| `work/evidence_map.json` | 证据映射：`evidence_id`→(finding_id, source_id, locator, quote, hit) | 每条结论回到原文逐字核对 |
| `work/runstate.json` | `started_at` + `change_log` | 运行轨迹与版本记录 |

`plan.json` 的分面适用性在 `init` 用关键词做初猜，Agent 读完材料后在 plan.json 定稿
为 `yes`/`no` 并写 rationale；`check` 据此判定覆盖完整性。这是本 Skill 相对“直接写
评语”的关键取舍：**先有可复核的审查计划与中间产物，再生成最终报告**。

## 4. locator 约定

- PDF：`p{page}#para{该页段序}`，如 `p3#para2`。
- 文本/MD/DOCX：`para{全局段序}`，如 `para7`。
- `evidence.source_id` 用 `D1`、`D2`… 对应 `index.materials`。
- quote 允许跨行不跨段、不跨源；空白折叠后必须逐字出现。
