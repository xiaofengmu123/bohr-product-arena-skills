# result.json 输出契约与事件日志格式

本文件定义 `scripts/main.py compile` 生成的 `/app/output/result.json` 与中间
事件日志 `/app/work/journal.jsonl`。字段语义以本文为准；`scripts/selftest.py`
会对关键不变式做机器校验。

## 1. 事件日志（append-only，唯一事实源）

`/app/work/journal.jsonl` 每行一个 JSON 对象，**只追加、不覆写**。允许的操作：

| op | 字段 | 含义 |
|---|---|---|
| `init` | `case_id`, `contract`, `inputs`, `ts` | 初始化：案例标识、output_contract 回显、材料清单 |
| `finding` | `finding`（对象）, `ts` | 按 `finding.id` upsert 一条审查结论 |
| `set` | `key`(`status`|`verdict`), `value`, `ts` | 设置顶层状态 |
| `meta` | `key`, `items`（列表或对象）, `mode`(`merge`|`replace`), `ts` | 向列表字段追加条目 |

`meta` 允许的 `key`：`sources`、`uncertainty`、`pending_human`、`limitations`、
`errors`、`degradations`、`unchecked_items`、`sections`、`phases`。`compile`
把日志**确定性重放**为 result.json；同一日志重复 compile 得到逐字节一致的结果
（脚本无随机数，findings 按 id 排序，meta 按事件顺序去重合并）。

Agent 既可用 `scripts/main.py add/set/meta` 写入事件，也可直接用编辑器向
`journal.jsonl` 追加一行合法 JSON（等价）。

## 2. result.json 顶层字段

```json
{
  "case_id": "…",
  "status": "ok|partial|insufficient_evidence|input_error",
  "summary": { "verdict": "…", "counts": {…}, "coverage": {…}, "notes": "" },
  "review_report": { "findings": […], "unchecked_items": […] },
  "evidence": { "evidence_map": {…} },
  "output_contract": {…},
  "sources": […], "uncertainty": […], "pending_human": […],
  "limitations": […], "errors": […], "degradations": […],
  "change_log": […], "runtime": {…}
}
```

- `status`：`ok`（完成）、`partial`（有可复核的部分产物）、`insufficient_evidence`
  （材料不足）、`input_error`（输入不可读）。
- `summary.verdict`：`pass|issues_found|needs_review|unchecked|insufficient`。
- `summary.counts`：`total/critical/major/minor/suggestion` 及 `by_category`、
  `by_finding_type`。
- `summary.coverage`：`categories_checked`、`categories_unchecked`（七类中的缺口）、
  `sections`（方法/结果/讨论等章节的覆盖记录，可空）。
- `output_contract`：若 `/input/case.json` 含 `output_contract`，**原样回填全部键**
  （不改名、不删未知键、不填值）；同时把这些键并入顶层、标准字段优先。
- `sources`：实际核验过的来源；未取得写 `not_verified`。
- `errors`：机器校验（缺键、非法枚举、quote 非逐字）+ Agent 自记错误，二者合并。
- `change_log`：由日志自动生成（`seq/op/subject/at`），完整可审计。
- `runtime`：平台预算常量（`requests=64, seconds=900`）+ `phases`（阶段记录）+
  `corpus`（逐字回查说明）。不包含易变的墙钟时间，保证确定性。

## 3. finding 对象

必填七键：`id/category/locator/quote/verdict/severity/suggestion`；其余可选。

```json
{
  "id": "F001",
  "category": "citation_support",
  "finding_type": "unsupported_claim",
  "severity": "major",
  "verdict": "unsupported",
  "locator": "p.3, para 2",
  "quote": "逐字原文",
  "claim": "对被审断言的转述",
  "basis": "判定依据",
  "source_ref": "DOI/URL/参考文献编号 或 not_verified",
  "suggestion": "修改建议",
  "confidence": "high|medium|low",
  "review_status": "reviewed|needs_review",
  "quote_verified": true
}
```

- `category` ∈ 七类 canonical key（见 `checklist.md`）。
- `severity` ∈ `critical|major|minor|suggestion`。
- `verdict`（引用支持）∈ `fully_supported|partially_supported|unsupported|undeterminable`。
- `finding_type` ∈ `checklist.md` §3 的九类。
- `quote_verified` 由 `compile` 计算：quote 是否逐字（空白折叠后）出现在抽取文本
  语料中；无抽取语料时为 `null`（未核验，不算通过）。用 `...`/`…` 跳过词句时，
  逐段匹配。
- `quote` 非逐字 → `errors` 记 `quote_not_verbatim`；必须修 quote 或改为 `needs_review`。

## 4. evidence_map

```json
"evidence": { "evidence_map": {
  "F001": { "locator": "…", "quote": "…", "basis": "…", "source_ref": "…",
            "quote_verified": true }
}}
```

每条 finding 一份，键为 finding id；人工复核只需按 `locator + quote` 回查原文。

## 5. 最小合法产物与降级

- 空材料：`status=insufficient_evidence`、`verdict=insufficient`，`limitations`
  列出缺口，`findings=[]`。
- 坏 JSON / 坏编码：记录到 `errors`/`degradations`，跳过该文件，继续读其它材料。
- 缺 `pdftotext` / 扫描版 PDF（抽出空文本）：该 PDF 标 `needs_ocr`，走 OCR 工具；
  无法识别则不判结论。
- 只有摘要/原文取不到：相关结论 `undeterminable`/`needs_review`，`source_ref`
  写 `not_verified`。
- 任何"缺失"写 `not_reported`/`not_verified`，禁止虚构引用、DOI、数值、来源或
  撤稿状态。
