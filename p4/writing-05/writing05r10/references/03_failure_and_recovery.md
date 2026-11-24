# 失败类型识别与恢复口径

本文件把"可靠体验"维度要求的异常场景固化为可复用处置路径。每个场景给出：
识别特征 → 处置动作 → status 与记录位置。原则：**如实标注，不补造**。

## 1. 材料/输入类

| 场景 | 识别特征 | 处置 | status | 记录 |
|---|---|---|---|---|
| 空输入目录 | 无 case.json | 写合法 result.json，`insufficient_evidence` | insufficient_evidence | errors |
| case.json 坏 JSON | 解析失败 | 捕获并记录，尝试 `utf-8-sig`/`latin-1` 兜底 | input_error | errors/degradations |
| 坏编码 | UnicodeDecodeError | 依次降级编码重读 | partial | degradations |
| 无材料正文 | case.json 无路径且无内嵌内容 | 只写已验证部分，不生成成稿 | insufficient_evidence | limitations |
| 材料超大/截断 | 单文件 >2MB 或文本截断 | 跳过或标注截断点，不据此下结论 | partial | limitations |

## 2. 文献可得性类

| 场景 | 处置 | 记录 |
|---|---|---|
| 文献不可得（只有题录无全文） | `metadata_status=unverified`，`type` 标 `unknown`，不写正文级论断 | uncertainty |
| 仅有摘要 | 论断只标 `abstract-only` 支撑范围，不冒充全文结论 | limitations |
| 摘要与原文矛盾 | 两处都记录，`support=conflict`，标 `pending_human` | pending_human |
| 二手转引 | `type=secondary`，写清核验路径，不改写为直接引用 | uncertainty |
| 疑似撤稿/版本差异 | 记录版本与日期，`metadata_status=unverified` | uncertainty |

## 3. 证据冲突与歧义类

- 同一指标两来源结论相反：两方都入 `sources`，论断 `support=conflict`，写
  `pending_human` 说明需人工裁决；不得只保留有利一方。
- 材料内部歧义：保留多种解读写入 `uncertainty`，标注"待人工确认"。
- 图表/公式不可读：不猜内容，记 `limitations`；可写"该处有公式/图但无法读取"。

## 4. 软件/格式类

- 缺 `pdftotext`：用标准库/直接读文本文件兜底；仍失败则 `degradations` 记录。
- 格式不支持（docx/xlsx/pptx 无解析库）：记录不可读，若题目要求 Office 产物则
  生成可编辑源文件并注明预览缺失；不伪造 PDF/PNG。
- 权限不足：立即停止依赖步骤，`errors` 记录，`status=partial/failed`。

## 5. 一致性红线

- 数字/术语/引用无法回溯来源：不写进 `deliverable`；确需保留则记 `uncertainty`。
- 不编造评测分数、运行结果、基准对比、隐藏测试；结果只来自本次实际运行。
- 不按文件名/领域标签补造数据；以实际读到的文件内容为准。

## 6. 恢复顺序（用于重试）

1. 先落盘当前 partial（`main.py --scaffold` / 直接更新 claim_graph.json）。
2. 定位第一个硬 error，按本表处置。
3. 重跑 `main.py --finalize` 重渲染；重复运行不覆盖非 in_progress 结果。
4. 仍失败则保持 `partial/failed`，绝不把失败重写为 complete。
