# result.json 输出契约（字段定义）

`/app/output/result.json` 是唯一必须的结构化产物。顶层同时包含 `case_id`、
`output_contract` 的全部字段，以及诚实的状态与审计字段。

## 顶层固定字段（非契约，必须存在）

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 案例标识；取值优先级：`case.json.id` → `armiId` → `challenge_id` → `case_id` → `"unknown"` |
| `status` | string | `complete` / `partial` / `failed` / `insufficient_evidence` / `input_error`，如实标注 |
| `sources` | array | 实际读取的来源清单：`{path, type, bytes, read, status, note}` |
| `uncertainty` | array | 无法判断、材料缺失、证据不足等逐条说明 |
| `pending_human` | array | 需要人工确认的事项 |
| `errors` | array | 输入解析、文件读写等错误（不影响骨架落盘） |
| `degradations` | array | 降级处理记录（超大文件跳过、编码回退等） |
| `change_log` | array | `{timestamp, action, scope}` 增量修改记录 |
| `runtime` | object | `{started_at, generator}`，供审计 |

## output_contract 字段（题目默认契约）

若 `/input/case.json` 含 `output_contract`，以其为准并**原样保留全部键**；缺失键
回填下列默认值，未知键一律保留。

| 字段 | 默认 | 含义 |
|---|---|---|
| `report_goal` | `null` | 可检查的报告目标：听众、时长、页数、验收标准 |
| `audience` | `null` | 听众先验、关注点、可能的质疑 |
| `narrative` | `[]` | 页面序列与取舍理由（非按论文目录搬运） |
| `pages` | `[]` | 页级结构：`{page_index, title, single_claim, evidence_strength, figures, speaker_notes, source_refs}` |
| `editable_deck` | `null` | 可编辑演示文件：`{path, format, template, notes}` |
| `evidence_map` | `[]` | 每页主张 → 来源定位：`{page_index, claim, locator, quote, source_file, confidence}` |
| `qa_prep` | `[]` | 预判追问：`{question, answer_keypoints, referenced_pages, backup_pages}` |
| `rendering` | `null` | 源文件、导出件、字体、模板、修改记录 |

## 证据溯源规则（硬要求）

- 每个关键结论 / 数值 / 图元必须带 `locator`（如 `paper.md#p3` 或 `fig2@page5`）
  与逐字 `quote`；不得改写事实。
- 冲突信息不隐藏：同一指标两个来源不一致时两个都记录并标 `source_conflict`。
- 未读到来源、公式或符号丢失：`evidence_strength` 标 `low`，写入 `uncertainty`，
  禁止补造出处、数值或 DOI。

## 最小调用示例（示意，非正式案例结果）

```bash
# 1) 前 3 分钟内：先落盘一版 partial 骨架（一个请求内完成）
python3 scripts/main.py --input /input --work /app/work --output /app/output \
    --scaffold-only

# 2) 分析材料后：增量合并页面、证据、讲稿、渲染结果
python3 scripts/main.py --input /input --output /app/output \
    --patch /app/work/update-pages.json

# 3) 收尾自检：结构 + 证据 + 产物存在性
python3 scripts/main.py --input /input --output /app/output \
    --sources /app/work/sources.json --validate-only
python3 scripts/selftest.py
```

示意输出（节选，`status` 以实际运行为准）：

```json
{
  "ok": true,
  "case_id": "case-demo",
  "status": "partial",
  "result_path": "/app/output/result.json",
  "validation_errors": 0,
  "validation_warnings": 0
}
```
