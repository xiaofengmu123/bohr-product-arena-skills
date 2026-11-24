# 自检清单（逐条可勾选，对应评分维度）

交付前逐条打勾；任一条不满足都先在 result.json 的 `uncertainty/degradations` 中说明，
不掩盖、不编造。

## A. 可核验性（结果可核验性 / 证据溯源）

- [ ] `result.json` 合法 JSON，含 `case_id` + output_contract 全部字段 + `status/sources/uncertainty/change_log`
- [ ] 每个关键数值/图元都有 `locator`（如 `data.csv#L2`）+ 逐字 `quote`
- [ ] 冲突信息并存并标 `source_conflict`；缺失标 `not_reported` 或 `pending_human`
- [ ] `change_log` 记录首次落盘与每次增量改进（ts/seq/step/action/files）

## B. 正确性（图上元素与源数据一致）

- [ ] 图上数值 = 源数据重算值（误差线、均值、比例、色标范围逐一核对）
- [ ] 轴类型与刻度、单位、图例与系列对应无误
- [ ] 误差线含义（SD/SEM/CI）与 n 写明；显著性给 p 或记号定义
- [ ] 对数轴/截断基线显式标注，不误导读数

## C. 完成度与完整性（覆盖任务要求全部图表要素）

- [ ] 坐标轴标目与刻度、单位、图例、误差线/显著性、色标范围、图注、面板编号齐备
- [ ] 交付绘图代码（`work/plot_*.py`）+ 图注 + 数据字典
- [ ] 结果与数据字典、规则、任务一致

## D. 发表级导出

- [ ] 导出 `SVG` + `PDF`（字体嵌入）+ `PNG`（≥300 dpi）
- [ ] 尺寸匹配栏宽（单栏 3.4 in），字号 ≥7pt，线宽 ≥0.5pt
- [ ] 中文/特殊符号无乱码（缺 CJK 字体已改英文并记录 degradation）

## E. 复现性与确定性（本版重点）

- [ ] 固定随机种子 `seed=42`，类别排序/JSON 排序确定，布局参数显式
- [ ] `main.py manifest` 已写 `determinism.input_fingerprint / output_fingerprint`
- [ ] 同一输入两次运行，统计与图形字节级一致（`scripts/selftest.py` 通过）
- [ ] `main.py check --require-contract` 全部 PASS

## F. 异常处理与健壮性

- [ ] 缺失/异常/重复/离群均已显式说明，无静默删除或填充
- [ ] 查询失败、无法解释、样本量不足、类别过多均写入 `uncertainty/degradations`
- [ ] 空输入/坏 JSON/坏编码也产出合法 `result.json` 且退出码 0

## G. 调用与易用性

- [ ] `python3 scripts/main.py --help` 退出码 0
- [ ] `python3 scripts/selftest.py` 全部 PASS
- [ ] 他人可按 SKILL.md 完成一次完整出图，路径均为相对路径
