# 证据与计算契约（evidence & computation contract）

本文档定义 `findings.jsonl` 中每条审查项的字段、locator 语法、quote 规则、computation 白名单，以及 `assemble.py` 的输入文件。目标是让“每个结论都可回查到材料原文”，且换人复跑得到可比较结论。

## 1. locator 语法

| 形式 | 含义 |
|---|---|
| `case.json#/plans/0/steps/1` | JSON Pointer（根对象下路径） |
| `case.json#/contracts/equipment/washer` | JSON Pointer，指向对象/数组亦可 |
| `task.md#L9-L27` / `task.md#L9` | 行区间 / 单行 |
| `task.md#H:评分维度概要` | 标题到下一同级/更高级标题的整节 |
| `materials/x.md#L4` | 其他文本文件行定位 |
| `sources/x.pdf#p3` | PDF 第 3 页（1 起） |

JSON 键含 `/` 用 `~1`、含 `~` 用 `~0` 转义（JSON Pointer 规范）。指针优先，`plans[0].steps[2]` 括号形式也接受，会被转换为指针。

## 2. quote 规则（逐字，非转写）

- `quote` 必须是材料中的**逐字原文**；空白可归一化（连续空白→单个空格），不做措辞改写。
- 长度 ≥3 字符，或为纯数字（如 `30`、`500`、`-6`）。
- 过长时截取决定性子句并给 locator；不得用省略号拼贴两个不相邻片段。
- 取原文用 `python3 scripts/main.py show --locator "…"`；检索用 `grep --pattern "关键词"`。
- 回查机制：`assemble.py` 对每条 `(locator, quote)` 用空白归一化子串回查原文，未命中标记 `verified=false` 并记入 `errors`；`selfcheck.py` 再次独立复算。

## 3. findings.jsonl 字段

每行一个 JSON 对象：

```json
{
  "finding_id": "F-<维度缩写>-<序号>",
  "dimension": "assumptions_variables | controls | equipment_materials | time_cost | parameter_ranges | risks_exceptions | ethics_safety",
  "claim": "一句话结论，>=10 字，落到具体章节/参数/清单行",
  "verdict": "ok | conditional | blocked | unconfirmed | missing | conflict",
  "check_basis": "plan_stated | plan_silent | contract | domain | computed | source",
  "applies_to": ["v1", "v2"],
  "evidence": [{"locator": "…", "quote": "逐字原文", "role": "supports | contradicts | defines | context"}],
  "computation": {"id": "A-…", "kind": "duration_total|cost_total|parameter_range|sample_sufficiency|computed",
                  "formula": "…", "operands": {"x": 1}, "result": 1, "unit": "min|CNY|bool|n",
                  "ref": "可选：引用 casepack 候选 T-v1/C-v1-…/P-v1-…/S-v1"},
  "conditions": ["逐条可执行"],
  "blockers": ["缺什么"],
  "resource_state": "已具备 | 可外协 | 需采购 | 不具备 | 未知",
  "action": {"owner": "谁", "by": "何时", "acceptance": "验收标准", "ask": "确认什么"},
  "confidence": "high | medium | low",
  "needs_expert": false,
  "notes": []
}
```

约定：`conditional` 必填 `conditions`；`blocked` 必填 `blockers`；`unconfirmed` 必填 `needs_expert=true` 或 `action.owner/ask`；所有 `ok/conditional/blocked/conflict` 必带 evidence。`applies_to` 缺省按 `plan_version` 推断。

## 4. computation 白名单

允许的运算：`+ - * / ** %`、`sum() min() max() abs() round() ceil() floor() sqrt()`、链式比较（`min <= value <= max`）、`and/or`。变量名来自 `operands` 的键。不允许自定义函数、下标、循环。

- 复算规则：数值结果按 `abs(declared - computed) <= 1e-6 + 1e-9*abs(computed)` 判定；布尔结果直接相等。
- `ref` 可引用 `casepack.json` 的候选（`T-*` 时长、`C-*` 成本、`P-*` 参数域、`S-*` 样本量）；引用后仍会复算，不直接采信候选。
- 单位必须给：`min`、`h`、`CNY/USD/EUR`、`n`、`bool`、`°C`、`rpm` 等。

## 5. assemble.py 的可选输入文件（均在 /app/work）

| 文件 | 用途 |
|---|---|
| `findings.jsonl` | 必需；审查项（可多文件/目录） |
| `contract_values.json` | 契约字段的直接取值 `{"<字段>": 值}` |
| `conclusion.json` | `{"verdict","summary","conditions","blocks","recommended_plan","status"}` |
| `comparison.json` | `{"recommended","rationale","criteria",["per_plan":{"v2":{"note":"…"}}]}` |
| `plan_notes.json` | 覆盖 `experiment_plan` 字段（`research_question/assumptions/variables/controls/steps/parameters/resources/interpretation_criteria`） |
| `extras.json` | `{"uncertainty":[],"pending_human":[],"limitations":[],"errors":[],"confirmation":{},"degradations":[]}` |

不提供这些文件时，`assemble.py` 用 casepack 自动推导并标记 `auto_derived`/`unfilled`。

## 6. 常见失败与修复

| 现象 | 原因 | 修复 |
|---|---|---|
| `quote 未通过原文回查` | 转写/拼贴/截断错位 | `main.py show --locator "…"` 取原文重贴 |
| `locator 无法解析` | 指针/页码漂移 | `main.py check --locators …` 逐条校验 |
| `computation 求值失败` | 公式引用了 operands 之外的变量 | 公式只用 operands 键名，或用 `ref` 引用候选 |
| `conditional 缺 conditions` | 只写“建议确认” | 补成可执行条件 + owner + 验收 |
| `status=ok 但有 FAIL` | 状态虚报 | 按自检降级为 `partial` |

修复循环：改 `findings.jsonl` → 重跑 `assemble.py` → `selfcheck.py --strict` 至 FAIL=0。

## 7. 结构一致性保证

- `result.json` 的契约字段先于其他字段；`contract_binding` 记录每个字段来源（`auto/contract_values/auto_empty/unfilled`）。
- `evidence_map.json`：`items` 按 `(locator, quote)` 去重，`findings` 回指所有引用它的审查项；`quote_verification.failed` 必须为 0。
- `review_report.md` 由 result 与 evidence_map 机械生成，保证包含全部 `finding_id` 与 `locator`，可逐条人工复核。
- 确定性：同一输入重跑，除 `runtime` 时间戳外，内容与排序一致。
