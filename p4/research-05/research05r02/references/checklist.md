# 提交/交付前自检清单（评分维度对照）

逐条核对；勾选状态与 `result.json` 的 `status/errors/degradations/pending_human/limits` 保持一致。

---

## 实际效果（40 分）

- [ ] 研究问题拆成可检验陈述（对象/变量/基线/预期/可证伪条件）。
- [ ] 已有工作比较选对对象，比较矩阵字段齐全，不可比原因已标注。
- [ ] 创新点逐条判覆盖（novel/covered/partially_covered/unverifiable），场景迁移/结论修正未误判为方法创新。
- [ ] 可行性每个前提有 `reported|assumed|missing|conflicting`，assumed 显式标注。
- [ ] 覆盖六类内容：问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白。
- [ ] 检索式、纳排规则、证据表、判断理由可独立复核。
- [ ] 每条关键结论都有 locator + 逐字 quote。

结果判定锚点：

| 交付 | 达到“可决策”的最低标准 |
|---|---|
| research_question | 有 `testable_statement` 且含对象/变量/基线/可证伪条件 |
| prior_work | 至少识别一个真正相近的工作，逐列比较，缺列写 `not_reported` |
| novelty | 每条 claim 有 kind + verdict + covered_by + 证据 |
| feasibility | 五类前提至少逐条过一遍，结论带条件与失败风险 |
| synthesis | 结论与证据范围一致，写出边界与空白 |

---

## 使用增益（25 分）

- [ ] 结论推进到可决策：能回答“增量在哪/能否做成”。
- [ ] 无需人工补文献、重写创新点、返工可行性论证即可读懂结论并定位。
- [ ] 增益可复核：change_log、evidence_matrix、report.md 三处一致。

---

## 创新增益（15 分）

- [ ] 形成基础 Agent 难稳定实现的能力：结构化四项交付 + 证据回链。
- [ ] 判定口径已写进 `references/assessment-rules.md`。
- [ ] 新增判断的边界与价值在 SKILL.md 第 1 节说明。

---

## 可靠体验（10 分）

- [ ] 触发路径清楚：读 SKILL.md → `main.py skeleton` → 依阶段流程执行。
- [ ] 空输入 / 坏 JSON / 缺参数 / 缺软件 / 写盘失败均有降级，不崩溃、不编造。
- [ ] 重复运行结论一致；`change_log` 记录改动。

---

## 生态贡献（10 分）

- [ ] 输入契约、评估口径、依赖、适用范围、限制、维护信息齐全。
- [ ] 比较矩阵、证据表、评分锚点、脚本可复用（只用标准库）。
- [ ] 说明与提交版本一致；示例已标注，未冒充正式案例结果。

---

## 快速命令

```bash
python3 -B scripts/main.py --help          # 退出码 0
python3 -B scripts/main.py selftest        # 三项场景 PASS
# 正式运行（在 /skills/weekly-skill 下）：
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
python3 -B scripts/main.py evidence --input /input --work /app/work --output /app/output
# …… 写入 /app/work/fragments/*.json ……
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
```

---

## 维护与版本信息

| 项 | 内容 |
|---|---|
| 版本 | 1.1.0 |
| 运行依赖 | Python 3.8+ 标准库；`pdftotext`（缺失时降级） |
| 调用入口 | `scripts/main.py`（skeleton/evidence/finalize/bump/selftest） |
| 适用范围 | 立项/投稿前选题评估：问题定义、已有工作比较、创新点识别、可行性判断 |
| 不适用 | 一次性脚本、纯聊天问答、材料为零仍要求给结论 |
| 限制 | 结论止步于给定材料；不做全局完备性/新颖性声明 |
| 已知降级 | PDF 无 `pdftotext` 时标 `text_unavailable`；>2MB 文件跳过；坏编码按 latin-1 降级 |

同一输入重复运行时，`result.json` 中的结论、证据、状态应一致；`runtime.started_at` 为运行元数据，允许随运行时间变化。若需固定复现时间，可在环境变量 `SKILL_RUN_STARTED_AT` 中指定。
