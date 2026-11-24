# 自检清单与异常处置

## 交付前逐条自检（对应评分维度）

- [ ] 产出的是**完整可直接使用的改稿**，而非建议、提纲或片段润色（任务完成度 15）
- [ ] 数字、单位、口径、样本量、图表编号、引用与源文一致，无源文不支持的新结论（正确性 15）
- [ ] 关键结论、数据、限定条件、方法要点、图表引用全部保留，删的是冗余而非证据（完整性 5）
- [ ] 每处删改有 change_log（before/after/reason），可逐段复核（可核验性 5）
- [ ] 受众、体裁、篇幅符合 case.json/task.md 约束（质量增益）
- [ ] result.json 含 case_id、status、sources、uncertainty、change_log 及 output_contract 全字段（可靠性）
- [ ] `python3 -B scripts/check.py --output /app/output` 退出码 0（可靠性）
- [ ] 同一输入重复运行结果一致，脚本相对路径可执行（稳定性）
- [ ] 空输入 / 坏 JSON / 坏编码三种情况各跑一次不崩溃，状态诚实（异常处理）

## 三种必测异常

| 场景 | 期望 |
|---|---|
| 空输入目录 | 退出码 0，result.json status=insufficient_evidence，列出缺口，不抛异常 |
| 坏 JSON / 坏编码 | 捕获并记入 errors/degradations，继续处理其余材料 |
| 缺参数 | --input/--output/--work 缺省指向 /input、/app/output、/app/work |

## 自检命令

```bash
python3 -B scripts/main.py --help                      # 期望退出码 0
python3 -B scripts/main.py --mode skeleton             # 先落骨架
python3 -B scripts/check.py --output /app/output       # 期望 ok=true
python3 -B scripts/main.py --mode finalize             # 增量更新为终稿
```

## 降级规则（禁止编造）

- 缺源文/缺关键数据 → `insufficient_evidence` + `pending_human`，不补造数据或结论。
- 数据自相矛盾 → 两处都记录并标 `source_conflict`。
- 体裁与篇幅冲突、涉密/未发表材料 → 记 `limitations`，给最小合规产物。
- 缺软件 / PDF 解析失败 → 记 `degradations`，继续处理其余材料。
- 同一策略失败两次即停止重复，改走降级路径并如实标注。
