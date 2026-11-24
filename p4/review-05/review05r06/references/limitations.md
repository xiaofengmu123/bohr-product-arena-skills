# 降级策略、自检与局限

## 1. 降级策略

| 场景 | 处置 | result 字段 |
|---|---|---|
| 空输入目录（无 case.json、无材料） | 退出码 0，`status=input_error`，不抛异常 | `errors` |
| case.json 缺失或不可解析 | `case_id="unknown"`，`status=input_error`，继续写合法骨架 | `errors` |
| PDF 无文本层（pdftotext / pdf-text-tool 均失败） | 记录失败，不猜内容；相关结论标 `pending_human` | `degradations`、`uncertainty` |
| 坏编码 / 坏 JSON / 单文件 >2MB | 逐文件降级，跳过并记录，其余继续处理 | `errors`、`degradations` |
| 缺 `output_contract` | 用标准 schema，顶层写 `contract_missing=true` | `output_contract` |
| 涉及受限/敏感数据 | 只标注位置与类型，不复述、不扩散 | `pending_human` |
| 外部文献/指南核验工具缺失 | 不伪造核验结果；标"外部存在性未核验" | `pending_human`、`uncertainty` |

任何情况下 `result.json` 都必须保持合法 JSON；先落盘 `partial` 骨架，之后只做增量改进。**禁止**编造评测分数、运行结果、文献元数据、条款号或期刊要求。

## 2. 交付前自检（干净环境跑一遍）

```bash
python3 -B scripts/main.py run --input ./demo/input --output ./demo/output --work ./demo/work
python3 -B scripts/validate.py --input ./demo/input --output ./demo/output --work ./demo/work
```

- `run` 退出码 0，且 `demo/output/result.json` 与 `demo/output/report.md` 均生成。
- `validate.py` 无 `[FAIL]` 输出（`[WARN]` 可接受，但交付正式案例前应逐条处理）。
- 同一输入重复运行，`evidence.json` 的段落 ID 与观察 ID 一致（确定性）。
- 空目录、坏 JSON、缺 PDF 文本三种异常各跑一次，确认均不崩溃且如实写状态。

## 3. 可复跑性保证

- 脚本只用标准库；段落与观察 ID 由遍历顺序确定，无随机性。
- 裁决口径写在 `references/facets.md`；同一证据、同一规则下结论应一致。
- 每条 finding 的 `quote` 由 `validate.py` 回查证据账本；查不到即 `[FAIL]`，须修正引文或标记材料无文本层。

## 4. 已知边界（不覆盖）

- 不做论文润色、代写、代投稿；不重新分析数据；不设计实验方案。
- 不提供法务或伦理委员会的正式意见。
- 不自行重算统计量；只检查统计报告是否交代完整、自洽。
- 对图片像素级造假、代码运行级复现、外部文献真实性，仅当运行环境提供相应工具且已实际核验时才下结论，否则一律 `pending_human`。
