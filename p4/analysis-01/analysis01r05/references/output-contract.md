# result.json 输出契约与证据溯源规范（references）

本文件规定 `/app/output/result.json` 的完整 schema、与 `output_contract` 的对齐规则、证据溯源格式与自检方法。运行时以 `/input/case.json` 里的 `output_contract` 为唯一 schema 来源。

## 1. result.json 顶层 schema

```json
{
  "case_id": "取自 case.json.case_id",
  "<output_contract 的每一个键>": "原样回填，缺值按类型给 null/[]/{}",
  "status": "ok | partial | insufficient_evidence | input_error",
  "sources": [{"locator": "文件#位置", "quote": "逐字原文", "note": "说明"}],
  "uncertainty": [{"subject": "结论", "detail": "不确定性与依据"}],
  "change_log": [{"time": "…", "step": "…", "action": "…", "why": "…"}],
  "errors": ["可恢复/已处理的错误"],
  "degradations": ["降级说明"],
  "pending_human": ["需人工确认的缺口"],
  "runtime": {"started": "…", "finished": "…", "deterministic": true},
  "artifacts": {"cleaned_data": "…", "quality_report": "…", "…": "…"},
  "contract_audit": {"n_contract_keys": 0, "keys": [], "filled": [], "unfilled": []}
}
```

## 2. output_contract 对齐规则（本版核心）

1. `output_contract` 是动态 schema：**每个键都必须出现在 result.json 顶层**，不改名、不删除、不合并未知键。
2. 缺值回填按值类型推断：列表→`[]`、对象→`{}`、字符串→`""`、其余→`null`（`scripts/core_impl.py::contract_default`）。
3. 契约键若语义对应主产物（关键词含 clean/table/report/profile/evidence/snapshot/change 等），`--phase full` 会把产物路径自动回填；其余键留给 Agent 依任务填写。
4. `contract_audit` 实时反映 `filled`/`unfilled`；**交付标准**：`unfilled` 为空，或对每个未填键在 `uncertainty`/`pending_human` 写明原因（不得静默）。
5. 模板给出的 `output_contract` 只作示例，实际字段以 case.json 为准。

## 3. 证据溯源格式

- 每个关键结论/数值/处理动作都要有 `locator + quote`：
  - `locator`：`materials/<文件名>#L12`、`materials/<文件名>#第3段`、`materials/<文件名>#表2` 等；
  - `quote`：材料里的**逐字**片段（含原始拼写/单位），不得改写；
  - 冲突信息两条都记，并标 `source_conflict`。
- `evidence_map.json` 记录：数据源文件、role（data/dictionary/rules/workorder/case_json/task_md）、编码、大小、sha256、是否降级；供复核者核对快照与源文件一致。
- 不得虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 4. status 语义

| status | 何时使用 |
|---|---|
| `ok` | 全部契约字段已填、产物齐备、数字核对通过、无未决缺口 |
| `partial` | 已完成一部分，存在明确局限（在 `degradations`/`uncertainty` 标明） |
| `insufficient_evidence` | 关键材料缺失，无法形成结论（列 `pending_human` 缺口） |
| `input_error` | 缺 case.json、case.json 无法解析、或输入结构不可用 |

## 5. 自检命令与判定

```bash
cd /skills/weekly-skill
python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work
python -B scripts/selftest.py
```

`selfcheck` 打印 `SELFCHECK_STATUS=pass|warn SELFCHECK_ISSUES=n`，退出码恒为 0。它核对的项：case_id 存在、status 合法、output_contract 全部键在 result.json 中、sources/uncertainty/change_log 为列表且条目含必需键、artifacts 指向的文件真实存在、contract_audit 键数与契约一致。

## 6. 最小调用示例（示意，非正式案例）

以下仅为说明接口的**示意**，不冒充任何正式案例结果；正式案例由平台在 `/input` 提供。

`/input/case.json`（示意）：

```json
{"case_id": "demo-01", "output_contract": {"cleaned_table": null, "quality_report": null, "notes": []}}
```

调用与示意输出：

```bash
cd /skills/weekly-skill
python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
# bootstrap: result.json skeleton written; status=partial
# bootstrap: contract keys=3

python scripts/main.py --phase full --input /input --output /app/output --work /app/work
# full: artifacts written; status=partial
# full: contract filled=2/3
```

示意 `result.json`（节选，字段回填后）：

```json
{
  "case_id": "demo-01",
  "cleaned_table": "/app/output/cleaned_data.csv",
  "quality_report": "/app/output/quality_report.md",
  "notes": [],
  "status": "partial",
  "contract_audit": {"n_contract_keys": 3, "filled": ["cleaned_table", "quality_report", "notes"], "unfilled": []}
}
```

## 7. 确定性约定

- 数据产物（`cleaned_data.csv`、`missing_flags.csv`、`data_profile.json` 的数字、`quality_report.md` 的数字、`evidence_map.json` 的 sha256）**不随运行时间变化**。
- `runtime.started/finished` 与 `change_log[].time` 是元数据，允许跨运行不同；除此之外的字段重复运行必须逐字节一致。
- 脚本不使用随机数、不访问网络、不写任何预制答案。
