# 约定与 Schema（conventions）

## 1. result.json 顶层 schema

| 键 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 取自 case.json 的 id/case_id，缺省用文件名 |
| `status` | string | `in_progress \| partial \| complete \| failed`，诚实反映完成度 |
| `deliverable` | string\|list\|null | 主产物路径；早落盘阶段为 null，成稿后填 `/app/output/manuscript.md` |
| `fact_source_map` | list | 每条：`{"locator","quote","claim","used_in"}`，locator+quote 逐字可回溯 |
| `version_limits` | object\|string\|null | 修改差异、材料缺口、异常、人工确认项等版本与边界说明 |
| `sources` | list | 事实/引用的来源条目：材料位置 + 文献条目 |
| `uncertainty` | list | 无法确认、冲突、缺失、需人工确认项 |
| `pending_human` | list | 需人裁决的具体待办项（比 uncertainty 更可执行） |
| `change_log` | list | 每次改动的 `{"ts","step","note"}`，从骨架到成稿全程留痕 |
| `errors` / `degradations` | list | 运行错误与降级说明（脚本自动写入，Agent 补充） |

`output_contract` 若在 case.json 中出现，其**全部键必须原样回填**，不改名、不删除未知键。

## 2. material_index.json schema（P1 产出）

```json
{
  "materials": [
    {
      "id": "M1",
      "path": "materials/study.md",
      "type": "md|pdf|txt|csv|docx|image|table",
      "parse_status": "ok|failed|skipped",
      "locators": [
        {"locator": "materials/study.md#p3", "quote": "逐字片段",
         "claims": ["该处支撑的论断"]}
      ],
      "numbers": [
        {"value": "42.3", "unit": "mg", "locator": "materials/study.md#p3",
         "quote": "逐字片段", "sig": "P=0.03"}
      ],
      "citations": [
        {"key": "smith2020", "author": "Smith J", "year": 2020,
         "journal": "J Example", "volume": "12", "pages": "34-56",
         "doi": "unknown", "quote": "文献条目原文"}
      ],
      "flags": ["missing_caption", "conflict", "unparsable"]
    }
  ]
}
```

## 3. 定位符与标记约定

- `[S:x]`：来源标记。x 为材料 id（如 `M1`）或 locator（如 `materials/study.md#p3`），
  必须能在 material_index 中命中，否则 evidence_map.py 报「悬空来源」。
- `[C:key]`：引用标记。key 在文末参考文献区以 `[C:key] 作者. 题目. 期刊 年份;卷(期):页. DOI` 定义，
  正文中出现即引用，缺 DOI/卷期写 `unknown`。
- `[缺失]` / `[待确认]` / `[冲突待确认]` / `[无法解析]`：缺项与不确定性标记，必须同步写入
  result.json 的 uncertainty/pending_human。
- 定位原则：每个关键结论/数值/图元都要给 locator + **逐字 quote**；不允许只有结论没有出处。

## 4. 最小可核引用格式

```
[C:smith2020] Smith J, et al. Title of the work. Journal of Example 2020;12(1):34-56. DOI unknown
```

作者/年份/期刊/卷期/DOI 仅使用材料中给出的值；取不到就写 `unknown`，禁止自行补造。

## 5. 状态与诚实性

- `partial`：早落盘骨架、材料缺失/解析失败、章节不完整时使用；
- `complete`：所有 output_contract 字段齐备、稿件完整、无未解释的缺失/冲突时使用；
- `failed`：无法产出任何有效主产物时使用（仍要写 result.json 说明原因）。
- 时间到点但未完成 → 一律 `partial`，写入未完成原因，不得虚标 `complete`。

## 6. 材料读取健壮性

- 文本按 `utf-8-sig` 优先读取，BOM/坏编码逐级降级，坏 JSON 不崩溃。
- 单文件 >2MB 跳过并在 uncertainty/degradations 记录，不硬读爆内存。
- 固定随机种子、确定性排版，保证同一输入重复运行结果一致。
