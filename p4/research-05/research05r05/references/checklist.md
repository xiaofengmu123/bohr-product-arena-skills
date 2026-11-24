# 提交/交付前自检清单（对应评分维度）

按评分维度组织，交付前逐条核对。勾选状态与 `result.json` 的 `status/errors/degradations/pending_human` 保持一致。运行 `python3 -B scripts/selftest.py` 可先验证脚本管线与输出契约。

## 实际效果（40 分）

- [ ] 研究问题已拆成可检验陈述（对象/变量/基线/预期/可证伪条件）。
- [ ] 已有工作比较选对对象，比较矩阵字段齐全，不可比原因已标注。
- [ ] 创新点逐条判定覆盖，场景迁移/结论修正未误判为方法创新。
- [ ] 可行性每个前提有 `reported|assumed|missing|conflicting`，assumed 显式标注。
- [ ] 覆盖六类内容：问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白。
- [ ] 检索式、纳排规则、证据表、判断理由可独立复核。
- [ ] 每条关键结论都有 `locator` + 逐字 `quote`。

## 使用增益（25 分）

- [ ] 相比“四平八稳综述”，结论已推进到可决策（能回答“增量在哪/能否做成”）。
- [ ] 无需人工补文献、重写创新点、返工可行性论证即可读懂结论与定位。
- [ ] 增益通过可复核记录体现（change_log、evidence_matrix、report.md 三处一致）。

## 创新增益（15 分）

- [ ] 形成基础 Agent 难以稳定实现的能力：结构化四项交付 + 证据回链。
- [ ] 问题拆解、比较维度、新颖性判定、可行性核查口径已在 `references/assessment-rules.md` 写明。
- [ ] 新增判断的边界与价值有说明（适用/不适用场景写在 SKILL.md 第 1 节）。
- [ ] `contract_alignment` 逐字段对齐 output_contract，`change_log` 记录骨架/证据/收尾每步改动（本版强化方向）。

## 可靠体验（10 分）

- [ ] 触发路径清楚：读 SKILL.md → 跑 `scripts/main.py skeleton` → 依流程执行。
- [ ] 空输入 / 坏 JSON / 缺参数 / 缺软件 / 写盘失败均有降级，不崩溃、不编造。
- [ ] 重复运行结果一致；`change_log` 记录每一步改动。

## 生态贡献（10 分）

- [ ] 输入契约、评估口径、依赖、适用范围、限制、维护信息齐全。
- [ ] 比较矩阵、证据表、评分锚点、脚本可被他人复用（脚本只用标准库）。
- [ ] 说明与提交版本一致，没有示例答案冒充正式案例结果。

## 快速命令

```bash
python3 -B scripts/main.py --help          # 退出码 0
python3 -B scripts/selftest.py             # 自检管线 + 契约断言
# 正式运行（在 /skills/weekly-skill 下）：
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
python3 -B scripts/main.py evidence --input /input --output /app/output --work /app/work
# …… 写入 /app/work/fragments/*.json ……
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
```
