# 中间表示（IR）规范与归约规则

本 Skill 的核心设计：不直接由材料写 `.tex`，而是先落三个结构化 IR，再由
`scripts/ir.py reduce` **确定性归约**出 `/app/output/result.json`。IR 是 result.json
的唯一事实来源，第三方可脱离 Agent 逐字段复核。

三个 IR 都带 `schema_version`（当前 `1.0.0`），用 `scripts/ir.py validate` 校验。

## 1. plan.json — 执行规划

```json
{
  "schema_version": "1.0.0",
  "case_id": "...",
  "task": {
    "genre": "paper_section|rewrite|proofread|template_adapt|reference_fix|formula_fix|other",
    "template": "目标模板或 cls 名称",
    "audience": "读者/期刊/学位",
    "language": "zh|en|bilingual",
    "contract_keys": ["output_contract 的全部键，原样"],
    "constraints": ["格式/字数/风格约束"],
    "required_fields": ["title","author","abstract","keywords","bibstyle",...]
  },
  "materials": [{"path": "materials/xxx", "role": "source|template|bib|figure"}],
  "steps": [{"id":"P0","name":"bootstrap","deadline_sec":60,"status":"pending","note":""}],
  "decisions": [{"id":"D1","choice":"...","reason":"...","alternatives":["..."]}],
  "open_questions": ["待人工确认的问题"]
}
```

**用途**：`genre/template/required_fields` 决定成稿方向；`decisions` 记录每个取舍的
理由与备选（评分项「方法改进/创新证据」）；`steps.status` 记录进度与时间预算（900 秒
分配），保证前 3 分钟已落盘骨架后只做增量改进。

## 2. fact_pool.json — 事实池（证据溯源）

```json
{
  "schema_version": "1.0.0",
  "case_id": "...",
  "facts": [
    {
      "id": "F001",
      "claim": "材料的原句或数值断言",
      "category": "number|unit|citation|figure|formula|claim|term|other",
      "status": "confirmed|conflict|pending_human|not_reported",
      "sources": [{"locator": "materials/paper.md#L12", "quote": "逐字片段"}],
      "used_in": ["main.tex#L34"],
      "note": ""
    }
  ],
  "conflicts": [{"id":"C1","facts":["F001","F002"],"note":"两处来源不一致"}]
}
```

**规则**：
- 每个关键数字/单位/引用/公式/论断必须有一条 fact，带 `locator + 逐字 quote`；材料
  没有的数值写 `not_reported` 或 `pending_human`，绝不补造。
- 同一指标两个来源不一致时，两条 fact 都保留，并用 `conflicts` 关联。
- `fact-add` 按 `id` 幂等替换，保证重复运行一致。

## 3. compile_report.json — 编译验证

```json
{
  "schema_version": "1.0.0",
  "case_id": "...",
  "engine": {"distribution":"xelatex","version":"...","command":"xelatex -halt-on-error main.tex"},
  "passes": [
    {"pass":1,"engine":"xelatex","exit_code":0,"error_count":0,
     "root_causes":[],"warnings":[],"log":"..."}
  ],
  "root_causes": [{"id":"RC01","category":"missing_package","detail":"amsmath",
                    "file_line":"main.tex:3","count":1,"samples":["..."]}],
  "refcheck": {"undefined_refs":[],"undefined_cites":[],"unused_labels":[]},
  "final_status": "compiled|not_compiled|degraded",
  "limitations": [{"kind":"engine_missing","note":"..."}]
}
```

**错误分类**（`classify-log` 同款正则）：missing_package / undefined_control_sequence /
math_error / argument_error / file_not_found / capacity_error / font_error /
package_error / emergency_stop 等；warning 分类 undefined_reference /
undefined_citation / overfull / underfull / multiply_defined / float_lost。
`root_causes` 按 (category, detail) 聚合去重，避免停在第一条报错。

## 4. 归约规则（reduce：IR -> result.json）

| result.json 字段 | 来源 |
|---|---|
| `sources` | fact_pool 中每条 fact 的 sources，附 fact_id/category/status |
| `uncertainty` | fact 状态为 conflict/pending_human/not_reported，及 conflicts/open_questions |
| `pending_human` | uncertainty 中待人工项的去重摘要 |
| `errors` | compile_report.root_causes 的 id |
| `degradations` | compile_report.limitations |
| `status` | contract 完成度 + compile_report.final_status 共同判定：全填且 compiled=ok；零填=insufficient_evidence；否则 partial |
| `runtime.ir_status` | `reduced` + schema_version + 归约时间戳 |

**可复核性**：改任一 IR 后重跑 `reduce`，result.json 确定性更新；评审可只读 IR 与
`.tex`，自行复现同一份 result.json。若 IR 写失败，`main.py finalize` 仍能产出最小
合法 result.json，并标 `ir_degraded`。

## 5. 复现示例（示例，非正式案例）

假设材料 `materials/paper.md` 第 12 行有 `"the yield was 92.3%"`：

```bash
python3 scripts/ir.py fact-add --work /app/work --id F001 \
  --claim "yield was 92.3%" --category number --status confirmed \
  --locator "materials/paper.md#L12" --quote "the yield was 92.3%"
python3 scripts/ir.py fact-use --work /app/work --id F001 --locator "main.tex#L34"
python3 scripts/ir.py reduce --input /input --output /app/output --work /app/work
```

reduce 后 result.json 的 `sources` 会包含该 locator 与逐字 quote；删除该 fact 重跑
reduce，对应 sources 条目即消失——归约可复现、可反查。
