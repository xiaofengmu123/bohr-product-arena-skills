# 契约与标记规范（Conventions）

本文件定义 result.json 的字段语义、来源/引用标记语法、中间表示 schema。脚本按此生成骨架与校验；Agent 填充时须保持一致。

## 1. result.json 顶层字段

| 字段 | 类型 | 语义 |
|-|-|-|
| `case_id` | string | 来自 case.json 的 id/case_id，缺省用 case 文件名 |
| `status` | string | `in_progress \| partial \| complete \| failed \| insufficient_evidence \| input_error`，与实际完成度一致 |
| `<output_contract 各键>` | 任意 | 原样回填 case.json 的 output_contract；缺省 `deliverable`、`fact_source_map`、`version_limits` |
| `artifacts` | object | 主产物与中间表示的绝对路径 |
| `sources` | array | 每条事实/引用的来源（材料位置 + 文献条目），见下 |
| `uncertainty` | array | 无法确认、冲突、缺失项；可带 `resolved: true` |
| `pending_human` | array | 需人工确认项；可带 `resolved: true` |
| `errors` | array | 运行异常（坏 JSON、编码、工具失败） |
| `degradations` | array | 降级原因说明 |
| `change_log` | array | `{ts, step, note}`，从骨架到成稿的每次改动 |
| `runtime` | object | `{start_ts, end_ts, elapsed_s}` |

**status 诚实性规则**：存在未标 `resolved: true` 的 uncertainty/pending_human 项时，`finalize` 会拒绝把 status 置为 `complete`，自动降为 `partial`。

## 2. 来源标记 `[S:定位]`

正文事实性断言后紧接来源标记，格式 `[S:材料ID.定位]`：

- `材料ID` 为 `material_index.json` 中的 `M1`、`M2`…；
- `定位` 为段落/图表位置（如 `p3`、`Fig2a`）或主张 id（如 `C1`，对应材料 key_claims 里的 `id`）。

每个 `[S:]` 必须能回溯到 `material_index.json` 某材料的一条 key_claim，该 claim 必须含
`locator`（材料内位置）与 `quote`（逐字原文片段）。**quote 必须逐字，不得改写。**

## 3. 引用标记 `[C:键]`

正文句末用 `[C:键]`（如 `[C:Smith2020]`）指向参考文献条目。参考文献区每条定义一行，最小可核格式：

```text
[C:Smith2020] Smith J, et al. Title. Journal Name 2020;41(3):120-130. DOI:10.xxxx/yyyy
```

- 每条正文使用的 `[C:键]` 必须在参考文献区有定义（`evidence_map` 检测悬空引用）。
- 参考文献区定义的条目若正文未使用，记入 `unused_citations`，应删除或改为正文引用。
- 作者/年份/期刊/卷期/DOI 必须与给定材料一致；缺失字段写 `unknown`，**禁止杜撰 DOI**。

## 4. 中间表示 schema

- `contract_meta.json`：`{case_id, input_dir, case_file, task_file, output_contract, task_hints, detected_materials[], warnings[]}`。
- `plan.json`：`{case_id, writing_task, audience, format, language, sections[], figure_plan[], citation_plan[], open_questions[]}`。`sections` 为 `{title, required}`；`check` 据此校验每个必要章节都有 Markdown 标题。
- `material_index.json`：`{case_id, materials[], conflicts[]}`。每条 material：
  `{id, path, kind, label, parse_status, key_claims[], numbers[], citations[], flags[]}`。
  key_claim：`{id, text, locator, quote, numbers[], units[], sample_size, flags[]}`。
  citation：`{key, authors, year, journal, volume, issue, pages, doi, quote}`。
- `figure_model.json`：`{case_id, figures[], tables[]}`。figure：`{id, file_ref, caption, panels[], numbers[], source_material, status}`；numbers 为 `{value, unit, locator, quote}`。
- `evidence_map.json`：`{facts[], citations_used[], citations_defined[], dangling_sources[], dangling_citations[], unused_citations[], ok}`。
- `consistency_report.json`：`{checks[], missing_sections[], figure_conflicts[], status_honest, summary{passed,failed,warnings}}`。

## 5. 脚本调用约定

所有脚本纯标准库、相对路径、幂等、可重跑；同一输入两次运行结果一致（时间戳取 UTC，顺序排序）。

```bash
python3 scripts/main.py init     --input /input --output /app/output --work /app/work
python3 scripts/main.py check    --output /app/output --work /app/work
python3 scripts/main.py finalize --output /app/output --work /app/work --status complete
python3 scripts/selftest.py
```

缺省参数：`--input=/input --output=/app/output --work=/app/work`。
