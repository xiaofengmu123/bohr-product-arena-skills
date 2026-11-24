# 自检清单（对应评分维度）

交付前按顺序逐条勾选。脚本侧由 `python3 scripts/selftest.py`（退出码 0）与
`python3 scripts/main.py validate --strict`（退出码 0）共同保证；人工侧逐项核验。

## 实际效果（40 分）

- [ ] 报告目标、听众定位、叙事顺序、逐页重点信息、问答准备形成闭环（`report_goal`/`audience`/`narrative`/`pages`/`qa_prep` 齐全且相互一致）。
- [ ] 每页恰好一条 `single_claim`，未出现摘要/Results 逐段搬页。
- [ ] 页面数字、图表引用与源材料一致，未改写事实（数字有 `extracted_numbers`）。
- [ ] `evidence_strength` 与证据匹配：结果对比用 `high`，机制推测用 `low`。
- [ ] 覆盖论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染六方面。
- [ ] 每个论断可定位：`evidence_map` 每条含 `locator` + 逐字 `quote`；`validate --strict` 无 error。

## 使用增益（25 分）

- [ ] 相同输入、听众、时长设定下比"逐节搬运"减少返工页数。
- [ ] 取舍决策（为何某图不上页、为何调整顺序）写入 `narrative.tradeoffs` 或 `change_log`。
- [ ] 导出修正次数与来源已记录于 `change_log`，非口头自述。

## 创新增益（15 分）

- [ ] 听众适配：`audience` 给出先验、关注点、质疑，并影响页数/术语深度。
- [ ] 页级规划：`pages` 有唯一主张 + 证据 + 图表绑定，非目录骨架。
- [ ] 问答预演：`qa_prep` 覆盖机制、数据、对照、局限四类追问并给可引用页面。
- [ ] 中间表示：`plan.json` 与 `figure_model.json` 已生成，可与最终件逐层复核。

## 可靠体验（10 分）

- [ ] 可编辑文件（PPTX/HTML/ODP）存在且可打开编辑。
- [ ] 渲染预览 PDF/PNG 存在：`validate --strict` 无阻断错误。
- [ ] 不同材料长度与重复运行下仍产出可用页面（脚本确定性布局、标准库）。
- [ ] 缺图/低分辨率/公式丢失/字体缺失/模板冲突/导出失败/超时均已识别并标注。

## 生态贡献（10 分）

- [ ] SKILL.md 含用途、适用条件、调用方式、输入输出、异常处理。
- [ ] 脚本仅标准库、相对路径调用，`--help` 可查用法。
- [ ] references 说明字段定义与检查清单，他人可复用。

## 收尾必查

- [ ] `result.json` 含 `case_id`、output_contract 全字段、`status`/`sources`/`uncertainty`/`pending_human`/`change_log`。
- [ ] `status` 如实为 `complete`/`partial`/`failed`/`insufficient_evidence`/`input_error`，未编造评测分数或运行结果。
- [ ] 前 3 分钟已落盘 `result.json` 骨架，之后仅为增量改进（`change_log` 可见 scaffold → merge → …）。
- [ ] 空输入、坏 JSON、缺软件三类情况各验证一次不崩溃。
