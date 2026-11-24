# 运行自检与异常处置清单（checklist）

交付前与每次运行后按本清单逐项核对。勾选项与评分维度对应；未勾选项即为待修缺口。

## A. 运行前（环境与接口）

- [ ] `python scripts/main.py --help` 退出码 0，inspect/assemble/validate/selftest/demo 都在
- [ ] 脚本只用标准库（selftest 第 1 项自动验证）
- [ ] SKILL.md frontmatter 含 name/description/version，正文 ≤180 行
- [ ] 脚本路径在 SKILL.md 中写为相对路径 `scripts/main.py`

## B. 正常路径（对应：实际效果 / 使用增益）

- [ ] inspect 在 /app/output 先落盘合法 result.json 骨架，再开始任何分析
- [ ] 每个候选填满七类字段，五个维度都有 0–5 评分
- [ ] 成本六项、时间五项已核算；一次性投入与按次消耗分开
- [ ] 每个数值/参数/结论有 locator + 逐字 quote；sources 非空
- [ ] selection 有 chosen/ranking/rationale/rejected（含舍弃理由）/sensitivity
- [ ] comparison_matrix 中所有方案同一套行，无「各写各的」

## C. 边界与异常（对应：可靠体验 / 异常处理）

- [ ] 空输入目录：退出码 0，status=input_error，不抛异常
- [ ] 坏 JSON/坏编码：记录 errors/degradations，继续处理其余材料，退出码 0
- [ ] 缺参数：脚本默认 /input、/app/output、/app/work，或用环境变量覆盖
- [ ] 写盘失败：降级到最小合法产物（result.json 骨架），进程不崩溃
- [ ] 设备不可得 / 耗材超预算 / 参数窗口冲突 / 全部不可行：如实标 partial + pending_human
- [ ] 材料不足：status=insufficient_evidence，列出缺口，不编造方案或章节
- [ ] 重复运行同一输入两次：结果一致（脚本无随机、排序确定性）

## D. 产物合法性与可复核性（对应：生态贡献）

- [ ] result.json：`json.load` 成功；含 case_id、status、output_contract 全部字段、sources、uncertainty、change_log
- [ ] `comparison_matrix.svg`：XML 可解析（`xml.etree.ElementTree.parse`）
- [ ] `comparison_matrix.csv`：UTF-8 with BOM，表头完整
- [ ] `plan_comparison.md`：可编辑、含矩阵表与选择结论
- [ ] `comparison_matrix.pdf`：以 `%PDF-` 开头、以 `%%EOF` 结尾
- [ ] 无预写正式答案、无伪造评测分数；demo 输出标注为「示例」

## E. 常见失败与处置

| 现象 | 处置 |
|---|---|
| case_pack 里候选方案数为 0 | 检查 case.json 键名；脚本已做递归发现，仍缺则标 insufficient_evidence |
| 脚本算出的成本与报价表不符 | Agent 读报价后写 `cost.total` + `method=manual_override` + note+evidence 覆盖 |
| analysis.json 缺字段 | assemble 对缺失维度重新归一化权重，缺证据则 pending_human，不崩 |
| 结果状态不应是 ok | 检查 confirmations 是否仍有 pending、选中方案是否缺 evidence |

## F. 交付前一次通过的最小命令

```bash
python scripts/main.py selftest
python scripts/main.py demo
python scripts/main.py validate /app/output/result.json
```

三项都通过、且 A–D 全部勾选后再交付。任何未确认的安全/伦理/设备/专家项保留 pending，不得改写为事实。
