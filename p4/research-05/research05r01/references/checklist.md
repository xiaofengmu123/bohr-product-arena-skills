# 提交/交付前自检清单（checklist）

按评分维度组织，交付前逐条勾选。勾选状态与 `result.json` 的
`status/errors/degradations/pending_human` 保持一致；勾了但 `result.json` 无对应
证据，视为未完成。

## 实际效果（40 分）

- [ ] 研究问题已拆成可检验表述：`testable_statement` 含对象/变量/基线/可证伪条件。
- [ ] 已有工作比较选对对象：`comparison_matrix` 字段齐全，`incomparable_reason` 已标注。
- [ ] 创新点逐条判定覆盖：`type` 正确分类，场景迁移/结论修正未误判为方法创新。
- [ ] 可行性每个前提有 `reported|assumed|missing|conflicting`，`assumed` 显式标注。
- [ ] 覆盖六类内容：问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白。
- [ ] 检索式、纳排规则、证据表、判断理由可独立复核。
- [ ] 每条关键结论在 `evidence_map.claims` 有 `locator` + 逐字 `quote`。

## 使用增益（25 分）

- [ ] 结论推进到可决策：能回答"增量在哪/能否做成"，非四平八稳综述。
- [ ] 无需人工补文献、重写创新点、返工可行性即可读懂结论与定位。
- [ ] 增益可复核：`change_log`、`evidence_map`、`report.md` 三处一致。

## 创新增益（15 分）

- [ ] 结构化四项交付 + claims 型 `evidence_map` 单一事实源，基础 Agent 难以稳定实现。
- [ ] 问题拆解、比较维度、新颖性判定、可行性核查口径已写入 `references/assessment-rules.md`。
- [ ] 边界与价值有说明（适用/不适用场景写在 SKILL.md 第 1 节）。

## 可靠体验（10 分）

- [ ] 触发路径清楚：读 SKILL.md → `skeleton.py` → `evidence.py` → fragments → `finalize.py`。
- [ ] 空输入 / 坏 JSON / 缺参数 / 缺软件 / 写盘失败均有降级，不崩溃、不编造。
- [ ] 重复运行结论一致；`change_log` 记录每步改动；`status` 与 `errors/degradations` 一致。

## 生态贡献（10 分）

- [ ] 输入契约、评估口径、依赖、适用范围、限制、维护信息齐全。
- [ ] 比较矩阵模板、证据字段、脚本可复用（只用标准库）。
- [ ] 说明与提交版本一致，没有示例答案冒充正式案例结果。

## 快速命令

```bash
python3 -B scripts/skeleton.py --help      # 退出码 0
python3 -B scripts/evidence.py --help      # 退出码 0
python3 -B scripts/finalize.py --help      # 退出码 0
python3 -B scripts/selftest.py             # 自检，应 4/4 passed
# 正式运行（在 /skills/weekly-skill 下）：
python3 -B scripts/skeleton.py --input /input --output /app/output --work /app/work
python3 -B scripts/evidence.py --input /input --work /app/work --output /app/output
# …… Agent 写入 /app/work/fragments/*.json ……
python3 -B scripts/finalize.py --input /input --output /app/output --work /app/work
```
