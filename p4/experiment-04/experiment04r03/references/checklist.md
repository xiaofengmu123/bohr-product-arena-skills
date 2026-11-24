# 运行自检与异常处置清单

交付前与每次运行后逐项核对；未勾选项即为待修缺口。勾选列对应评分维度，不要留到交付后再补。

## A. 运行前（环境与接口）

- [ ] `python scripts/main.py --help` 退出码 0，四个子命令（inspect/assemble/validate/demo）都在
- [ ] 脚本只用标准库：检查 `scripts/` 下 `import`/`from` 无第三方依赖
- [ ] `SKILL.md` frontmatter 含 name/description/version，正文 ≤180 行
- [ ] 脚本路径在 SKILL.md 中写为相对路径 `scripts/main.py`、`scripts/selftest.py`

## B. 正常路径（对应：实际效果 / 使用增益）

- [ ] inspect 在 /app/output 先落盘合法 result.json 骨架，再开始任何分析
- [ ] 每个候选方案填满七类字段（assumptions/variables/controls/steps/parameters/resources/interpretation_criteria）
- [ ] 五个维度 goal_fit/risk/cost/feasibility/evidence 都有 0–5 评分
- [ ] 成本六项（机时/耗材/人力/准备/排期/返工）与时间已核算；一次性投入与按次消耗分开
- [ ] 每个数值/参数/结论有 locator + 逐字 quote；sources 非空且去重排序
- [ ] selection 有 chosen、ranking、rationale、rejected（含舍弃理由）、sensitivity
- [ ] comparison_matrix 中所有方案同一套行，无“各写各的”情况

## C. 边界与异常（对应：可靠体验 / 异常处理）

- [ ] 空输入目录：退出码 0，status=input_error，不抛异常
- [ ] 坏 JSON / 坏编码：记录 errors/degradations，退出码 0
- [ ] 缺参数：默认 /input、/app/output、/app/work，或用 `--input/--output/--work`、环境变量覆盖
- [ ] 写盘失败：降级到最小合法产物（result.json 骨架），进程不崩溃
- [ ] 设备不可得 / 超预算 / 参数窗口冲突 / 全部不可行：如实标 partial + pending_human
- [ ] 材料不足：status=insufficient_evidence，列出缺口，不编造方案或章节

## D. 确定性与产物合法性（对应：生态贡献）

- [ ] `python scripts/selftest.py` 全部 PASS；同输入两次产物字节一致
- [ ] result.json：`json.load` 成功；含 case_id、status、output_contract 全部字段、sources、uncertainty、change_log
- [ ] comparison_matrix.svg：XML 可解析（`xml.etree.ElementTree.parse`）
- [ ] comparison_matrix.csv：UTF-8 with BOM，表头完整
- [ ] comparison_matrix.pdf：以 `%PDF-` 开头、以 `%%EOF` 结尾（ASCII 数值摘要）
- [ ] plan_comparison.md：可编辑、含表格与选择结论
- [ ] 无预写正式答案、无伪造评测分数；demo 输出标注为示例

## E. 常见失败与处置

| 现象 | 处置 |
|---|---|
| case_pack 里候选方案数为 0 | 检查 case.json 键名；脚本已递归发现，仍缺则标 insufficient_evidence |
| 脚本算出的成本与报价表不符 | Agent 读报价表后写 `cost_override` + note + evidence 覆盖 |
| 中文在 PDF 里缺失 | PDF 为 ASCII 数值摘要；完整中文矩阵在 SVG/MD |
| analysis.json 缺字段 | assemble 对缺失维度重新归一化权重，缺证据则 pending_human，不崩 |
| 两次运行产物不同 | 检查是否在 analysis.json/change_log 里写了墙钟时间；应使用 run_id 或输入自带日期 |

## F. 交付前一次通过的最小命令

```bash
python scripts/main.py demo
python scripts/selftest.py
python scripts/main.py validate /app/output/result.json
```

三项都通过、且 A–D 全部勾选后再交付。任何未确认的安全/伦理/设备/专家项保留 pending，不得改写为事实。
