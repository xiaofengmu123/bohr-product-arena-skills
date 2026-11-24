# 提交/交付前自检清单

按评分维度组织，交付前逐条核对。勾选状态必须与 `result.json` 的 `status/errors/degradations/pending_human/limitations` 保持一致。

## 实际效果（40 分）

- [ ] 研究问题已拆成可检验陈述（对象/变量/基线/预期/可证伪条件），四要素齐全。
- [ ] 已有工作比较选对对象，比较矩阵五列（任务/方法/数据/评价/结论）齐全，不可比原因已标注。
- [ ] 创新点逐条有 `type + verdict + covered_by + reason`，场景迁移/结论修正未误判为方法创新。
- [ ] 可行性每个前提有 `reported|assumed|missing|conflicting`，`assumed` 显式标注。
- [ ] 覆盖六类内容：问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白。
- [ ] 检索式、纳排规则、证据表、判断理由可独立复核。
- [ ] 每条关键结论都有 `locator` + 逐字 `quote`，且 quote 能在 locator 处找到。

## 使用增益（25 分）

- [ ] 相比"四平八稳综述"，结论推进到可决策（能回答"增量在哪/能否做成"）。
- [ ] 无需人工补文献、重写创新点、返工可行性论证即可读懂结论与定位。
- [ ] 增益通过可复核记录体现：`result.json`、`evidence_matrix`、`report.md`、五个中间产物五处一致。

## 创新增益（15 分）

- [ ] 中间产物（plan/evidence_index/evidence_model/figure_model/synthesis）构成完整可复核链，`finalize` 只做确定性合并。
- [ ] 问题拆解、比较维度、新颖性判定、可行性核查口径已在 `references/assessment-rules.md` 写明。
- [ ] 新增判断的边界与价值有说明（适用/不适用场景写在 SKILL.md §1）。

## 可靠体验（10 分）

- [ ] 触发路径清楚：读 SKILL.md → `scripts/main.py init` → `discover` → 写 fragment → `record` → `finalize`。
- [ ] 空输入 / 坏 JSON / 坏编码 / 缺参数 / 缺软件 / 写盘失败均有降级，不崩溃、不编造。
- [ ] 重复运行结果一致；`change_log` 记录每一步改动。
- [ ] `python3 -B scripts/main.py --help` 与 `python3 -B scripts/selftest.py` 均退出码 0。

## 生态贡献（10 分）

- [ ] 输入契约、评估口径、依赖、适用范围、限制、维护信息齐全（SKILL.md §2/§3/§8）。
- [ ] 比较矩阵模板、证据表、字段枚举、脚本可被他人复用（脚本只用标准库）。
- [ ] 说明与提交版本一致，没有示例答案冒充正式案例结果。

## 快速命令

```bash
python3 -B scripts/main.py --help                 # 退出码 0
python3 -B scripts/selftest.py                    # 退出码 0
# 正式运行（在 /skills/weekly-skill 下）：
python3 -B scripts/main.py init
python3 -B scripts/main.py discover
python3 -B scripts/main.py record --kind plan      --file /app/work/fragments/01_plan.json
python3 -B scripts/main.py record --kind evidence  --file /app/work/fragments/02_evidence.json
python3 -B scripts/main.py record --kind figure    --file /app/work/fragments/03_figure.json
python3 -B scripts/main.py record --kind synthesis --file /app/work/fragments/04_synthesis.json
python3 -B scripts/main.py finalize
```

## 最小调用示例（示意，非正式案例结果）

输入目录结构：

```text
/input/case.json                     # 含 case_id，可含 output_contract
/input/task.md                       # 任务说明与评分要点
/input/materials/paper-a.md          # 任一可读材料
```

示意片段（`fragments/03_figure.json` 中一条创新判定，用于说明字段，不冒充正式案例）：

```json
{
  "novelty_claims": [
    {
      "claim_id": "n1",
      "statement": "示例：把某方法迁移到新场景（此行为示例占位）",
      "type": "scenario_transfer",
      "verdict": "unverifiable",
      "covered_by": [],
      "reason": "材料未报告该方法在原场景的结果，无法判定是否被覆盖",
      "evidence_ids": []
    }
  ]
}
```

示意输出片段（`/app/output/result.json`，`status` 依材料诚实取值）：

```json
{
  "case_id": "example-case",
  "status": "partial",
  "novelty": { "claims": [] },
  "evidence_matrix": [],
  "sources": [],
  "uncertainty": { "biases": [], "gaps": ["材料未报告"], "undetermined_items": [] },
  "change_log": [{ "time": "2026-01-01T00:00:00Z", "action": "init", "detail": "skeleton" }]
}
```

以上均为字段示意，非正式案例答案；正式结果由 `init/discover/record/finalize` 依据真实输入生成。

