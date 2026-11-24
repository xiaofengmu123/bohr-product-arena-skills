# 自检清单与降级处理

逐条可勾选；对应评分维度（实际效果 40 / 使用增益 25 / 创新增益 15 / 可靠体验 10 / 生态贡献 10）。

## A. 前置检查（可靠体验 · 调用与易用性）

- [ ] 已用 `scripts/input_scan.py` 扫描 `/input`，确认 `case.json`、`task.md` 与科研材料路径。
- [ ] 已从 `case.json`/`task.md` 提取：受众、体裁、格式、篇幅（字数）四项约束；缺项已记为 `not_specified`。
- [ ] 材料中的 PDF 已用平台 PDF 文本工具读取（不使用文件名/标签臆测内容）。

## B. 执行检查（实际效果 · 完成度/完整性）

- [ ] 产出的是**可直接使用的完整稿件**，不是建议、提纲或片段润色。
- [ ] 满足题目指定的体裁、受众与格式，篇幅在给定上限内。
- [ ] 保住了关键结论、数据、限定条件、方法要点与图表引用；删除的是冗余而非证据。

## C. 事实与来源检查（实际效果 · 正确性/可核验性）

- [ ] 数值、单位、比较基准、样本量、统计口径、图表编号、引用编号与源文逐字一致。
- [ ] 未新增源文没有支持的新结论；论断强度与源文匹配（相关≠因果、初步≠定论）。
- [ ] 每条关键事实都有 `locator + quote`，可逐条回溯；冲突信息已双记录并标 `source_conflict`。
- [ ] 缩写首次展开；同一概念全文统一说法。

## D. 版本与边界检查（实际效果 · 可核验性 + 可靠体验 · 异常处理）

- [ ] `change_log` 逐段记录删改（位置、前后文、理由、类型）。
- [ ] `version_boundary.pending_human` / `.gaps` / `.conflicts` 已填；材料不足或无法判断已明确标注，未编造。
- [ ] 识别并处置：源文缺引用、数据自相矛盾、体裁与篇幅冲突、材料涉密或未发表等情况。

## E. 输出契约检查（可靠体验 · 运行稳定性）

- [ ] `result.json` 含 `case_id` + 题目 output_contract 全部字段 + `status/sources/uncertainty/change_log`。
- [ ] `status` 取值合法（`ok | partial | insufficient_evidence | input_error`）且诚实。
- [ ] 已运行 `scripts/verify_output.py`，无 FAIL（warning 逐一核对后保留或修正）。
- [ ] 可编辑产物（`.md`/`.docx` 等）已落盘；Office/幻灯片/图表类产物另附真实 PDF/PNG 预览。

## F. 增益自评（使用增益 + 创新增益）

- [ ] 用术语表锁定 + 三级取舍 + 论断强度分级 + 逐段对照，而非一次性自由发挥。
- [ ] 关键数字与术语来自 `facts_inventory` 交叉核对，而非模型记忆。

## 降级与失败处理

| 场景 | 处理 | status |
|---|---|---|
| 空输入 / 无科研材料 | 不编造，列出缺口，产出最小合法结果 | `insufficient_evidence` |
| 坏 JSON / 坏编码 / PDF 无文本 | 记录到 `errors`/`degradations`，继续处理其余材料 | `partial` |
| 软件缺失（pdftotext/Office 渲染不可用） | 记录到 `degradations`，改用文本/降级产物，不冒充成功 | `partial` |
| 材料冲突、无法判断 | 双记录 + `source_conflict`，写入 `pending_human` | `partial` |
| 写盘失败 | 重试到 `/app/output` 的临时名后原子替换；仍失败则保底写出最小 `result.json` | `input_error` |
| 预算即将耗尽 | 立即落盘当前 `result.json` 与稿件，标 `partial` 并注明未完成项 | `partial` |

约束：**总 900 秒 / 64 次模型请求 / 单次响应 16384 token**。前 3 分钟内必须先落盘一版结果骨架（`build_skeleton.py`），之后只做增量改进，任何异常都不会留下不完整产物。
