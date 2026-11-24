# 运行自检与异常处置清单（checklist）

交付前与每次运行后按本清单逐项核对。勾选项对应评分维度；未勾选项即为待修缺口。

## A. 运行前（环境与接口）

- [ ] `python scripts/main.py --help` 退出码 0，五个子命令（inspect/init-models/assemble/validate/demo）都在
- [ ] 脚本只用标准库：`grep -R "import " scripts/` 无第三方依赖
- [ ] `SKILL.md` frontmatter 含 name/description/version，正文 ≤180 行
- [ ] 脚本路径在 SKILL.md 中写为相对路径 `scripts/main.py`

## B. 正常路径（对应：实际效果 / 使用增益）

- [ ] inspect 在 /app/output 先落盘合法 result.json（骨架），再开始分析
- [ ] 每个候选方案七类字段全填、五个维度都有 0–5 评分
- [ ] 成本六项、时间四项已核算；一次性投入与按次消耗分开
- [ ] 每个数值/参数/结论挂 `evidence_registry`（locator + 逐字 quote），无孤儿证据
- [ ] selection 有 chosen、ranking、rationale、rejected（含舍弃理由）、sensitivity
- [ ] comparison_matrix 所有方案同一套行，无“各写各的”

## C. 边界与异常（对应：可靠体验 / 异常处理）

- [ ] 空输入目录：退出码 0，status=input_error，不抛异常
- [ ] 坏 JSON/坏编码：记录 errors/degradations，继续处理其余材料，退出码 0
- [ ] 缺参数：脚本默认 /input、/app/output、/app/work，或用环境变量覆盖
- [ ] 写盘失败：降级到最小合法产物（result.json 骨架），进程不崩溃
- [ ] 设备不可得 / 耗材超预算 / 参数窗口冲突 / 全部不可行：如实标 partial + pending_human
- [ ] 材料不足：status=insufficient_evidence，列缺口，不编造方案或章节
- [ ] 重复运行同一输入两次：result.json 哈希一致（无随机、排序确定）

## D. 产物合法性与可复核性（对应：生态贡献）

- [ ] result.json：`json.load` 成功；含 case_id、status、output_contract 全部字段、sources、uncertainty、change_log
- [ ] `comparison_matrix.svg`：`xml.etree.ElementTree.parse` 可解析
- [ ] `comparison_matrix.csv`：UTF-8 with BOM，表头完整
- [ ] `plan_comparison.md`：可编辑、含表格与选择结论
- [ ] `comparison_matrix.pdf`：以 `%PDF-` 开头、以 `%%EOF` 结尾
- [ ] 三层 IR（case_pack/plans_model/comparison_model）与 result.json 一致；`intermediate_artifacts` 含路径与 SHA256
- [ ] 无预写正式答案、无伪造评测分数；demo 输出标注为“示例”

## E. 常见失败与处置

| 现象 | 处置 |
|---|---|
| case_pack 候选数为 0 | 检查 case.json 键名；脚本已递归发现，仍缺则标 insufficient_evidence |
| 脚本算出的成本与报价表不符 | 读报价表后写 plans_model 的 cost.lines + note，或 method=override + total，附 evidence_ref |
| 中文在 PDF 里缺失 | PDF 是 ASCII 数值摘要；完整中文矩阵在 SVG/MD，需要中文 PDF 用平台 latex/office 工具另渲染 |
| 证据引用无法解析 | 检查 evidence_refs 与 evidence_registry.id 是否一致；validate 会报 orphan/unresolved |
| 权重维度缺失 | assemble 自动重新归一化；维度评分缺失时按可用维度重算 |

## F. 交付前一次通过的最小命令

```bash
python scripts/main.py demo          # 自检：示例跑通并产出全部工件
python scripts/main.py validate /app/output/result.json
python scripts/selftest.py           # 硬断言：demo/确定性/空输入/坏 JSON/契约
```

三项都通过、且 A–D 全部勾选后再交付。任何未确认的安全/伦理/设备/专家项保留 pending，不得改写为事实。
