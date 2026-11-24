# result.json 契约回填与最小示例

本文件定义 `/app/output/result.json` 的构成规则，重点是 **output_contract 逐键对齐** 与 `change_log` 合规。

## 1. 顶层键

result.json 顶层同时包含两类键：

1. **output_contract 原键**：来自 `/input/case.json → output_contract`（dict），逐键原样回填、不改名、不删除；嵌套 dict 保留结构、叶子 `null`，list 给 `[]`，标量给 `null`。Agent 在 `work/answers.json → output` 中给出的值覆盖对应叶子。
2. **框架键**：`case_id`、`status`、`answers`、`library`、`sources`、`uncertainty`、`pending_human`、`change_log`、`evidence_map`、`contract_fidelity`、`errors`、`degradations`、`failures_recovery`、`runtime`。

`case_id` 取自 case.json 的 `case_id / id / challenge_id`（首个非空）。`status ∈ {ok, partial, insufficient_evidence, input_error}` 必须诚实：骨架期为 `partial`，证据核对失败或不可核验为 `partial`，缺 case.json 为 `input_error`，无材料且无答案时为 `insufficient_evidence`。

## 2. change_log 每条字段

```json
{"step": "scaffold|finalize|...", "ts": "2026-..-..T..:..:..Z",
 "action": "read_inputs|write_skeleton|merge|set_status|...",
 "status": "ok|degraded|skip|...", "detail": "一句话"}
```

scaffold 先写入 2 条（read_inputs、write_skeleton）；finalize 在其后追加 merge、set_status，形成完整操作链。不得删除历史条目。

## 3. contract_fidelity 审计

对每个 output_contract 键逐条记录 `key / in_contract / in_result / filled_by_agent / source / contract_type / result_type`，并汇总 `total_keys / preserved_keys / filled_by_agent / echoed / missing_keys / reserved_collisions`。复核标准：`preserved_keys == total_keys`、`missing_keys == []`、`reserved_collisions == []`（契约键若与框架键同名，框架值优先并在 reserved_collisions 明示）。

## 4. evidence 逐条字段（finalize 后）

```json
{"file": "materials/paper.md", "sha256": "<64hex>",
 "locator": "materials/paper.md#L12-L15", "quote": "原文逐字片段",
 "confidence": "certain", "verified": true,
 "verify_reference": {"method": "line-range", "note": "substring_match_normalized"}}
```

`verified` 三值：`true`（命中）、`false`（文本可读但未命中，定位/引用有误）、`null`（文件缺失/无 pdftotext/超限，不可核验）。`false` 或 `null` 会使最终 `status` 回到 `partial` 并写入 `degradations`。

## 5. 最小调用示例（示意，非正式案例结果）

```bash
python3 scripts/main.py --action scaffold --input /input --output /app/output --work /app/work
python3 scripts/main.py --action inventory --input /input --output /app/output --work /app/work
# Agent 依据 materials 与题目逐问写出 /app/work/answers.json
python3 scripts/main.py --action finalize --input /input --output /app/output --work /app/work
python3 scripts/main.py --action selftest --input /input --output /app/output --work /app/work
```

`answers.json` 结构：

```json
{"output": {"<output_contract 键>": "<该键的值>"},
 "answers": [{"question_id": "q1", "question": "...", "answer": "...",
   "evidence": [{"file": "...", "sha256": "...", "locator": "...", "quote": "...", "confidence": "certain"}],
   "uncertainty": "certain", "missing": null}],
 "uncertainty": [{"level": "conflict", "item": "...", "reason": "两来源不一致", "sources": ["a","b"]}],
 "pending_human": []}
```