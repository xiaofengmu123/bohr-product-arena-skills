# 契约与脚本接口（contracts）

本文件把 SKILL.md 里的输入/输出契约、时间预算表和脚本调用接口写成机器可复核的规范。运行时 SKILL 根目录为 `/skills/weekly-skill/`，脚本用相对路径调用：`python3 scripts/xxx.py`。

## 1. 运行时目录

| 变量 | 默认值 | 含义 |
|-|-|-|
| `INPUT_DIR` | `/input` | 原始 `case.json`、`task.md`、论文 PDF/图/表/母版 `.pptx` |
| `WORK_DIR` | `/app/work` | 中间产物：`plan.json`、`case_summary.json`、提取文本、渲染图 |
| `OUTPUT_DIR` | `/app/output` | 最终产物：`result.json`、可编辑演示件、PDF/PNG 预览 |

脚本可通过 `--input/--work/--output` 覆盖，也可用上述环境变量；不依赖当前工作目录。

## 2. result.json 顶层结构

```json
{
  "case_id": "<来自 case.json，字符串>",
  "status": "partial | complete | failed | insufficient_evidence | input_error",
  "presentation_file": null,
  "preview_files": [],
  "speaker_notes": null,
  "page_plan": {},
  "evidence_map": [],
  "version_records": [],
  "sources": [],
  "uncertainty": {"unresolved": [], "missing_artifacts": [], "notes": ""},
  "pending_human": [],
  "change_log": [{"t": "", "step": "", "action": "", "status": "", "reason": ""}],
  "runtime": {"elapsed_s": 0, "requests_used": 0, "scaffold_t": "", "budget_s": 900, "budget_requests": 64},
  "errors": [],
  "degradations": [],
  "self_check": null
}
```

规则：
- `case_id` 必须取自 `case.json`；缺失时写 `"unknown"` 并进 `degradations`/`uncertainty`，不得猜造。
- `output_contract` 声明的字段**原样回填**，不改名、不删未知键；值类型未知时先给 `null`，再增量填。
- 保留字段 `status/sources/uncertainty/pending_human/change_log/runtime/errors/degradations` 恒存在。
- `change_log` 按 `t` 升序；每条含 step/action/status/reason，供第三方复跑复核。
- `sources` 每项建议含 `ref/loc/kind/quote/used_in`；`quote` 必须是原文逐字片段。

## 3. 时间预算与早落盘（本 Skill 的核心设计取舍）

总预算 900 秒、64 次模型请求、单响应 16384 token（含推理）。

| 阶段 | 截止秒 | 请求预算 | 交付物 |
|-|-|-|-|
| 0 scaffold 落骨架 | 60 | ≤2 | `result.json` partial 骨架 + `work/plan.json` |
| 1 inventory 盘点材料 | 180 | ≤4 | 材料清单、正文文本、图页/图注清单 |
| 2 plan 主线与页计划 | 300 | ≤8 | `page_plan`（每页叙事角色/主张/图引用） |
| 3 draft 成稿 | 540 | ≤20 | 可编辑演示件 + 讲稿备注 + `evidence_map` |
| 4 render 渲染修复 | 720 | ≤18 | PDF/PNG 预览 + 溢出/字体修复 |
| 5 finalize 终检交付 | 900 | ≤12 | `self_check.json` + 最终 status |

**硬规则**：`scaffold.py` 必须在阶段 0（≤60s，最迟 180s 前）落盘一版合法 `result.json`，含 `case_id`、全部契约字段（空值）+ `status=partial`；此后只做增量改进，除字段损坏外不得清空重来。`runtime.scaffold_t` 是这条规则的审计痕迹，`selftest.py check` 会校验。

请求计数器是辅助记账（Agent 自报 `record.py budget`），平台原生 trace 才是权威数据；两者冲突时以平台为准并记入 `uncertainty`。

## 4. 脚本接口（相对 SKILL 根目录）

```bash
python3 scripts/scaffold.py                 # 早落盘：写 result.json 骨架 + plan.json + case_summary.json
python3 scripts/record.py change --step inventory --action "list files" --reason "..."
python3 scripts/record.py source --ref "Fig 3" --loc "论文 p.7" --kind figure --quote "原文逐字" --used-in "4,5"
python3 scripts/record.py evidence --page 4 --role result --claim "..." --refs "Fig 3" --loc "p.7" --quote "..."
python3 scripts/record.py version --name v1 --note "..." --files "deck.pptx,preview.pdf"
python3 scripts/record.py contract --field presentation_file --value deck.pptx
python3 scripts/record.py budget --step draft --requests 6 --elapsed 300
python3 scripts/record.py status --status partial --note "..."
python3 scripts/selftest.py check            # 写 self_check.json，有必查项失败则退出码 1
python3 scripts/selftest.py pptx /app/output/deck.pptx   # 可渲染性代理检查（zip/页数/占位/溢出）
python3 scripts/selftest.py finalize --status complete    # 定稿 + 内嵌 self_check
python3 scripts/selftest.py selftest         # 空目录/坏 JSON 之外的自举冒烟测试
```

`record.py` 是增量追加/合并，绝不重写整个文件；`selftest.py check` 区分**必查项**（决定退出码）与**提示项**：partial 允许空值，complete 才强制要求主文件存在、`page_plan/evidence_map/preview_files` 非空。

## 5. 最小调用示例（示意，非正式案例）

```bash
export INPUT_DIR=/input WORK_DIR=/app/work OUTPUT_DIR=/app/output
python3 scripts/scaffold.py
python3 scripts/record.py contract --field presentation_file --value deck.pptx
python3 scripts/record.py evidence --page 1 --role title --claim "研究问题：..." --loc "论文 §1"
python3 scripts/selftest.py finalize --status partial
```

示意输出（`result.json` 片段）：
```json
{"case_id": "demo-001", "status": "partial", "presentation_file": "deck.pptx", "evidence_map": []}
```

以上仅为接口示例，不得当作任何正式案例的答案。
