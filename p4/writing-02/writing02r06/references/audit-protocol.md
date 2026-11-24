# 中间表示与可复核协议

本 Skill 的核心设计取舍：在“材料 → 成品”之间插入两层**结构化、机器可复核的
中间产物**，把“没改坏/讲得清”从事后回读变成可运行的校验。所有中间产物都在
`/app/work/` 下，均写入最终 `result.json` 的 `sources` 供第三方复核。

## 1. 中间产物

### plan.json —— 分析计划与不变量注册表

Agent 读完材料后先写这一份，再动任何一句：

```json
{
  "schema_version": "1.0",
  "case_id": "...",
  "target_lang": "en", "genre": "journal", "audience_notes": "...",
  "style_rules": ["minimum edit", "..."],
  "sections": [{"id": "s1", "locator": "materials/draft.md", "kind": "abstract"}],
  "invariants": [
    {"id": "inv-1", "kind": "unit", "quote": "3.5 mmHg", "tokens": ["3.5mmHg"],
     "locator": "materials/draft.md#L1", "strength_class": "hard"}
  ],
  "terminology": [{"concept": "...", "canonical": "...", "variants": ["..."],
                   "first_occurrence": "draft.md#L1"}],
  "gaps": [], "conflicts": [], "uncertainty": [], "change_log": []
}
```

不变量是“不能被润色改掉的东西”的显式清单：数字、单位、引用、图表指向、限定
强度、量词、否定、条件、因果-相关、术语。每个不变量带 `locator`（指向材料
段落）和逐字 `quote`（可核验）。

### ledger.json —— 逐句编辑台账

每一句一个 `unit`，记录原文、改文、修改类型、理由、触碰的不变量，以及
**不变量守恒证明**：

```json
{
  "units": [{
    "id": "u-1", "locator": "materials/draft.md#L1",
    "original": "...", "revised": "...",
    "edit_type": "grammar", "reason": "...",
    "touched_invariants": ["inv-1", "inv-6"],
    "conservation": {
      "inv-6": {"after_class": "moderate", "verdict": "preserved", "detail": "suggest 保留"}
    },
    "requires_human": false, "human_note": null
  }]
}
```

### check_report.json —— 确定性校验报告

`scripts/main.py verify` 生成，含 `errors`、`warnings`、`stats`。任何 hard token
缺失、任何不变量未被覆盖或缺少 locator/quote、任何限定强度静默变化都是
`error`/`warning`。

## 2. 复核流程（第三方如何判断“该不该改”）

1. 打开 `manuscript.md` 的“逐句修改表”，逐条看原文—改文—类型—理由。
2. 对每处改动，按 `touched_invariants` 跳到 `plan.json` 看该句受哪些不变量约束。
3. 打开 `check_report.json`，确认该 unit 的每个不变量 `verdict == preserved`
   或已给出 `explicit_reason`。
4. 打开 `result.json` 的 `fact_source_map`，逐条按 `locator + quote` 回到材料原文。
5. 有 `pending_human` / `source_conflict` / `MATERIAL GAP` 的项目，视为未定，不作定论。

## 3. 确定性保证

- 校验逻辑无随机数、无网络、无模型调用；同一输入两次运行结论一致。
- 硬不变量用归一化子串匹配；限定强度用五档序比较；术语用全稿禁用变体回扫。
- `runtime` 只作测量元数据，不影响任何判定结论。

## 4. 与“写建议/整段重写”的边界

- 没有 `plan.invariants` 的不变量注册表就改稿，视为越界。
- 没有 `ledger.units` 的逐句台账就交稿，视为不可复核。
- 这两层中间产物缺失时，`finalize` 只回退原文并标 `insufficient_evidence`/`partial`。
