# 运行自检与异常处置清单（checklist）

本清单与 `scripts/selftest.py` 配套：脚本逐条自动验证，Agent 在交付前与每次运行后逐条人工勾选。勾选项对应评分维度；未勾选项即为待修缺口。

## A. 运行前（环境与接口，对应：可靠体验）

- [ ] `python scripts/main.py --help` 退出码 0，五个子命令 inspect / assemble / validate / extract / demo 都在
- [ ] 脚本只用标准库：`scripts/` 下 `import` 无第三方依赖
- [ ] `SKILL.md` frontmatter 含 name / description / version，正文 ≤180 行
- [ ] SKILL.md 中脚本调用路径写相对路径 `scripts/main.py`，无绝对路径
- [ ] `python scripts/selftest.py` 全部 PASS

## B. 正常路径（对应：实际效果 / 使用增益）

- [ ] `inspect` 先落盘合法 `result.json`（骨架）再开始任何分析；中途失败不留空产物
- [ ] 每个候选方案填满七类字段（假设/变量/对照/步骤/参数/资源/判读标准），五维都有 0–5 评分
- [ ] 成本六项（机时/耗材/人力/样品准备/排期/返工）与时间四项（准备/执行/排期/返工）可核算，一次性投入与按次消耗分开
- [ ] 每个数值/参数/结论挂 locator + 逐字 quote；sources 非空；冲突来源标 `source_conflict`
- [ ] selection 含 chosen / ranking / rationale / rejected（含舍弃理由）/ sensitivity
- [ ] comparison_matrix 所有方案同一套行，无“各写各的”情况

## C. 边界与异常（对应：可靠体验 / 异常处理，本版强化方向）

| 场景 | 要求 |
|---|---|
| 空输入目录 | 退出码 0，写合法 result.json，status=input_error，不抛异常 |
| 缺 case.json | status=input_error，不编造案例 |
| 坏 JSON / 坏编码 | 记录到 errors/degradations，继续处理其余材料，退出码 0 |
| 超大文件（>2MB） | 跳过并记录 `oversized_file_skipped`，退出码 0 |
| 缺参数/未知参数 | 缺省 `/input`、`/app/output`、`/app/work`，可用 `--input/--output/--work` 或环境变量覆盖 |
| 写盘失败 | 降级到 `/app/work` 下的最小合法产物，进程不崩溃 |
| 设备不可得 / 耗材超预算 / 参数窗口冲突 / 全部不可行 | 如实标 partial + pending_human，chosen 可空 |
| 材料不足 | status=insufficient_evidence，列出缺口，不编造方案或章节 |
| 重复运行同一输入两次 | 结果一致（排序键稳定、无随机、无墙钟时间戳进入 assemble 产物） |

## D. 产物合法性与可复核性（对应：生态贡献）

- [ ] `result.json`：`json.load` 成功；含 case_id、status、output_contract 全部字段、sources、uncertainty、pending_human、change_log、limitations
- [ ] `comparison_matrix.csv`：UTF-8 with BOM，表头完整
- [ ] `comparison_matrix.svg`：XML 可解析（`xml.etree.ElementTree.parse`）
- [ ] `comparison_matrix.pdf`：以 `%PDF-` 开头、以 `%%EOF` 结尾
- [ ] `plan_comparison.md`：可编辑、含表格与选择结论
- [ ] 无预写正式答案、无伪造评测分数；demo 输出标注为“示例”

## E. 常见失败与处置

| 现象 | 处置 |
|---|---|
| case_pack 里候选方案数为 0 | 检查 case.json 键名；脚本已递归发现 `candidates/plans/options/候选方案`，仍缺则标 insufficient_evidence |
| 脚本算出的成本与报价表不符 | Agent 读报价表后写 `cost_override` + note + evidence 覆盖，不编造数字 |
| 中文在 PDF 里缺失 | PDF 是 ASCII 数值摘要；完整中文矩阵在 SVG/MD；需要中文 PDF 用平台 latex/office 工具另行渲染 |
| analysis.json 缺字段 | assemble 对缺失维度重新归一化权重，缺证据则 pending_human，不崩 |
| PDF 无法提取文本 | 记录 `pdftotext_unavailable`，改用平台 pdf-text-tool / OCR，结果保留在 materials 摘录 |

## F. 交付前一次通过的最小命令

```bash
python scripts/main.py demo          # 自检：示例跑通并产出全部工件
python scripts/main.py validate /app/output/result.json
python scripts/selftest.py           # 鲁棒性全量自检
```

三项都通过、且 A–D 全部勾选后再交付。任何未确认的安全/伦理/设备/专家项保留 pending，不得改写为事实。
