# slides-01 自检与降级细则

本文把评分维度拆成可勾选的检查动作，供执行中与交付前复核；所有失败都要如实写入 `result.json` 的对应字段，不得编造。

## 1. 交付前自检（逐条核对）

### 实际效果（主线 / 正确性 / 完整性 / 可核验）
- [ ] 主线与论文贡献一致，不是按段落顺序搬运；能一句话说出 narrative。
- [ ] 每页一个 claim；role 合法；单页信息密度受控（长句已改写为短语）。
- [ ] 每页 evidence 有 locator + 逐字 quote；数值、单位、方法名、结论表述与论文一致。
- [ ] 引用 Fig./Table/Eq. 编号与 figure_model 对得上；示意图标 `schematic` 不冒充数据。
- [ ] 必需页齐全：问题/背景、方法、关键结果、讨论/限制、结论。
- [ ] `content_map` 覆盖每一页 → 证据定位；源文件 pptx 与导出 pdf 均真实存在。

### 模板与渲染（可编辑 / 版式 / 导出）
- [ ] 遵守模板占位符、字号层级、配色、页脚规则；页数 ≤ 上限。
- [ ] 长标题/多图/大表格已适配，无文本框溢出、无自动缩成看不清的字号。
- [ ] `presentation.pdf` 页数与 pptx 一致；字体、项目符号、中文不崩坏。
- [ ] pptx 可逐页编辑（非图片切片）；同一输入重跑结果一致。

### 讲解备注
- [ ] 每页有 notes；写过渡、强调点、被问时的补充证据；**不重复页面文字**。

## 2. 降级策略（缺什么记什么，不编造）

| 场景 | 处置 | 落点 |
|---|---|---|
| `/input` 缺失 / case.json 坏 | 仍写 result.json，`status=input_error`，`case_id=unknown-case` | `errors` |
| 空输入目录 | 退出码 0，写合法 result，`status=insufficient_evidence` | `uncertainty` |
| 图表无法提取 / 公式为图片 / PDF 加密 | 页级 evidence 标 `not_reported`，不猜数值 | `pending_human` |
| 模板占位符不足 / 字体缺失 / 页数超限 | 缩并页面或降为纯文本版式，说明取舍 | `exceptions` |
| 无 soffice/LibreOffice | 给真实 SVG+PNG 回退，不伪造 pdf | `degradations` |
| 同指标两来源冲突 | 都记录，标 `source_conflict` | `sources` |
| 脚本写盘失败 | 降级到最小合法产物，不崩溃 | `errors` |
| 中途异常 | 已落盘的 partial 结果保留，可继续 | `change_log` |

## 3. 时间与增量纪律

- 总预算 900 秒 / 64 次模型请求；**前 3 分钟必须先落盘 result 骨架**，之后只做增量改进。
- 每 40 秒至少写一次文件；不要连续思考超过 2 分钟。
- 剩 5 分钟必须已全部落盘；先交粗稿再改进。
- 每次改写 result.json 追加 `change_log` 条目，保证可交接、可复核。

## 4. 复跑一致性

- 布局确定性：pptxlib 固定 EMU 尺寸、固定字号、无随机数。
- 同输入两次运行产出同结构 result；脚本 `scripts/selftest.py` 可在任意环境验证 scaffold/discover/validate/build 全链路与空输入、坏 JSON 两条降级路径。
