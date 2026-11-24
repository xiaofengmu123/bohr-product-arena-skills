# 提交/交付前自检清单

按评分维度组织，交付前逐条核对。勾选状态与 `result.json` 的
`status / errors / degradations / pending_human / validation` 保持一致。

## 实际效果（40 分）

- [ ] 研究问题已拆成可检验陈述（对象/变量/基线/预期/可证伪条件）。
- [ ] 已有工作比较选对对象，矩阵字段齐全，不可比原因已标注，未跨文档串值。
- [ ] 创新点逐条有 `type` + `verdict` + `covered_by`；场景迁移/结论修正未误判为方法创新。
- [ ] 可行性每个前提有 `reported|assumed|missing|conflicting`，assumed 显式标注，结论带失败风险。
- [ ] 覆盖六类内容：问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白。
- [ ] 检索式、纳排规则写入 `search_protocol`，证据表与判断理由可独立复核。
- [ ] 每条关键结论都有 `locator` + 逐字 `quote`，且 `build` 的 `quote_check` 均为 `verified`。

## 使用增益（25 分）

- [ ] 相比四平八稳综述，结论推进到可决策（能回答"增量在哪/能否做成"）。
- [ ] `synthesis.conclusions` 非空、`boundary_statement` 写清范围，无需人工返工即可读懂。
- [ ] `change_log`、`evidence_matrix`、`report.md` 三处一致。

## 创新增益（15 分）

- [ ] 结构化四项交付 + 账本式证据回链 + 逐字引文机械校验已具备。
- [ ] 判定口径在 `references/assessment-rules.md` 写明（字段 + 词表）。
- [ ] SKILL.md 第 1 节写明适用/不适用边界，第 4 节写明差异化架构。

## 可靠体验（10 分）

- [ ] 触发路径清楚：`init → scan → evidence/spec → build`，`--help` 退出码 0。
- [ ] 空输入 / 坏 JSON / 缺输入 / 缺软件 / 写盘失败均有降级，不崩溃、不编造。
- [ ] `python scripts/selftest.py` 全部 PASS；同输入两次 `build` 结论一致（除时间戳）。

## 生态贡献（10 分）

- [ ] 输入契约、评估口径、依赖、适用范围、限制、维护信息齐全。
- [ ] 比较矩阵、证据表、账本与脚本可复用（仅标准库）。
- [ ] 说明与提交版本一致，无示例答案冒充正式案例结果。

## 快速命令

```bash
python scripts/main.py init --help     # 退出码 0
python scripts/main.py scan --help     # 退出码 0
python scripts/main.py build --help    # 退出码 0
python scripts/selftest.py             # 全 PASS

# 正式运行（在 /skills/weekly-skill 下，缺省即 /input、/app/output、/app/work）：
python scripts/main.py init
python scripts/main.py scan
# …… 读取材料后逐条 python scripts/main.py evidence ……
# …… 写入 /app/work/spec/*.json，改动用 python scripts/main.py log --msg "..." ……
python scripts/main.py build
```
