# 运行自检与异常处置清单

交付前与每次运行后逐项核对。勾选项对应评分维度；未勾选即待修缺口。

## A. 运行前（环境与接口）

- [ ] `python scripts/main.py --help` 退出码 0，子命令 inspect / assemble / validate / demo / progress 齐备
- [ ] 脚本仅用标准库：`python scripts/selftest.py` 通过，无第三方 import
- [ ] `SKILL.md` frontmatter 含 name / description / version，正文 ≤180 行
- [ ] 脚本路径在 SKILL.md 中为相对路径 `scripts/main.py`

## B. 正常路径（实际效果 / 使用增益）

- [ ] `inspect` 先落盘合法 `result.json` 骨架（前 3 分钟），再开始分析
- [ ] 每个候选方案填满七类字段，五维均有 0–5 评分
- [ ] 成本六项、时间五项可核算；一次性投入与按次消耗分开
- [ ] 每个数值/参数/结论有 locator + 逐字 quote；sources 非空
- [ ] selection 有 chosen / ranking / rationale / rejected（含舍弃理由）/ sensitivity
- [ ] comparison_matrix 所有方案同一套行，无各写各的

## C. 边界与异常（可靠体验 / 异常处理）

- [ ] 空输入目录：退出码 0，status=input_error，不抛异常
- [ ] 坏 JSON/坏编码：记 errors/degradations，继续处理其余材料，退出码 0
- [ ] 缺参数：默认 /input、/app/output、/app/work，或用 --input/--output/--work 覆盖
- [ ] 设备不可得 / 超预算 / 参数窗口冲突 / 全部不可行：标 partial + pending_human
- [ ] 材料不足：status=insufficient_evidence，列缺口，不编造方案
- [ ] 重复运行同一输入：稳定核心一致（无随机、排序确定；仅时间戳/耗时元数据可变）

## D. 产物合法性与可复核性（生态贡献）

- [ ] result.json：`json.load` 成功；含 case_id、status、output_contract 全部字段、sources、uncertainty、change_log
- [ ] comparison_matrix.svg：`xml.etree.ElementTree.parse` 通过
- [ ] comparison_matrix.csv：UTF-8 with BOM，表头完整
- [ ] plan_comparison.md：可编辑，含表格与选择结论
- [ ] comparison_matrix.pdf：以 `%PDF-` 开头、含 `%%EOF`
- [ ] 无预写正式答案、无伪造评测分数；demo 输出标注为“示例”

## E. 常见失败与处置

| 现象 | 处置 |
|---|---|
| case_pack 候选数为 0 | 检查 case.json 键名；脚本已递归发现，仍缺则标 insufficient_evidence |
| 脚本算术与报价表不符 | Agent 读报价表后写 cost_override/time_override + note + evidence 覆盖 |
| PDF 中文缺失 | PDF 为 ASCII 数值摘要；完整中文矩阵在 SVG/MD/CSV |
| analysis.json 缺字段 | assemble 对缺失维度重新归一化权重，缺证据记 pending_human，不崩 |

## F. 交付前最小命令

```bash
python scripts/main.py demo
python scripts/main.py validate /app/output/result.json
python scripts/selftest.py
```

三项全过、A–D 勾选后再交付。任何未确认的安全/伦理/设备/专家项保留 pending，不得改写为事实。
