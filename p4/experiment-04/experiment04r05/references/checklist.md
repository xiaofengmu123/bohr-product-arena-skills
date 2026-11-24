# 运行自检与异常处置清单（checklist）

交付前与每次运行后按本清单逐项核对。勾选项对应评分维度；未勾选项即为待修缺口。

## A. 运行前（环境与接口）

- [ ] `python scripts/main.py --help` 退出码 0，四个子命令 inspect/assemble/validate/demo 都在
- [ ] 脚本只用标准库：`python scripts/selftest.py` 的 stdlib 检查全绿
- [ ] `SKILL.md` frontmatter 含 name/description/version，正文 ≤180 行
- [ ] 脚本路径在 SKILL.md 中写为相对路径 `scripts/main.py`、`scripts/selftest.py`

## B. 正常路径（实际效果 / 使用增益 / 输出契约）

- [ ] inspect 在 /app/output 先落盘合法 result.json（骨架），再开始任何分析
- [ ] 每个候选方案填满七类字段，五个维度都有 0–5 评分
- [ ] 成本六项、时间五项已核算；一次性投入与按次消耗分开
- [ ] 每个数值/参数/结论有 locator + 逐字 quote；sources 非空
- [ ] selection 有 chosen、ranking、rationale、rejected（含舍弃理由）、sensitivity
- [ ] result.json 含 case_id 与 output_contract 全部字段；contract_alignment 无 missing（或 missing 已入 pending_human/limitations）
- [ ] change_log 只追加、带 seq 与时间戳；历史记录未被重写

## C. 边界与异常（可靠体验 / 异常处理）

- [ ] 空输入目录：退出码 0，status=input_error，不抛异常
- [ ] 坏 JSON/坏编码：记录 errors/degradations，继续处理其余材料，退出码 0
- [ ] 缺参数：脚本默认 /input、/app/output、/app/work，或用环境变量/`--input --output --work` 覆盖
- [ ] 写盘失败：降级到最小合法产物（result.json 骨架），进程不崩溃
- [ ] 设备不可得 / 耗材超预算 / 参数窗口冲突 / 全部不可行：如实标 partial + pending_human
- [ ] 材料不足：status=insufficient_evidence，列出缺口，不编造方案或章节
- [ ] 重复运行同一输入两次：结果一致（无随机、排序确定性；时间戳除外）

## D. 产物合法性与可复核性（生态贡献）

- [ ] result.json：`json.load` 成功；含 case_id、status、output_contract 全部字段、sources、uncertainty、change_log
- [ ] `comparison_matrix.svg`：XML 可解析（`xml.etree.ElementTree.parse`）
- [ ] `comparison_matrix.csv`：UTF-8 with BOM，表头完整
- [ ] `plan_comparison.md`：可编辑、含表格与选择结论
- [ ] `comparison_matrix.pdf`：以 `%PDF-` 开头、以 `%%EOF` 结尾
- [ ] 无预写正式答案、无伪造评测分数；demo 输出标注为“示例”

## E. 常见失败与处置

| 现象 | 处置 |
|---|---|
| case_pack 里候选方案数为 0 | 检查 case.json 键名；脚本已做递归发现，仍缺则标 insufficient_evidence |
| 脚本算出的成本与报价表不符 | Agent 读报价表后写 `cost_override` + note + evidence 覆盖 |
| 契约字段缺来源 | 填 null/[] 并在 contract_alignment 标 missing、写入 limitations/pending_human |
| 中文在 PDF 里缺失 | PDF 是 ASCII 数值摘要；完整中文矩阵在 SVG/MD |
| analysis.json 缺字段 | assemble 对缺失维度重新归一化权重，缺证据则 pending_human，不崩 |
| 候选全部不可行 | chosen=null 或最高可行者，rationale 说明，status=partial |

## F. 交付前一次通过的最小命令

```bash
python scripts/main.py demo          # 自检：示例跑通并产出全部工件
python scripts/main.py validate /app/output/result.json
python scripts/selftest.py           # 全量：空目录/坏 JSON/缺输入/工件合法/确定性
```

三项都通过、且 A–D 全部勾选后再交付。任何未确认的安全/伦理/设备/专家项保留 pending，不得改写为事实。
