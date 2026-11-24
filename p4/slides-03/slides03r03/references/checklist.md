# 自检清单（对应评分维度，逐条可勾选）

交付前按顺序勾选；每项给出对应命令或人工检查方式。`status` 必须如实标注，
不得编造评测分数或运行结果。

## 实际效果（40 分）

- [ ] `report_goal`、`audience`、`narrative`、`pages`、`qa_prep` 齐全且形成闭环。
- [ ] 每页恰好一条 `single_claim`，未出现摘要/Results 逐段搬页。
  - 检查：`python3 scripts/evidence_check.py --result /app/output/result.json` 无 `duplicates` 错误。
- [ ] 页面数字、图表引用与源材料一致，未改写事实。
- [ ] 每页 `evidence_strength` 与证据匹配（对比性结果用 high，机制推测用 low）。
- [ ] 覆盖六方面：论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染。
- [ ] 每个论断可在源材料定位。
  - 检查：`python3 scripts/evidence_check.py` 无 `missing locator/quote` 错误。

## 使用增益（25 分）

- [ ] 同一输入、听众、时长下，比“逐节搬运”减少返工页数；取舍写进 `narrative` 或 `change_log`。
- [ ] 导出修正次数与来源已写入 `rendering` 与 `change_log`，非口头自述。
- [ ] 增益可复核：同一案例重跑 `main.py scaffold` 得到相同 `run_id`，两次产物逐字节一致。

## 创新增益（15 分）

- [ ] 听众适配：`audience` 给出先验、关注点、质疑，并影响页数/术语深度。
- [ ] 页级规划：`pages` 每页有唯一主张 + 证据 + 图表绑定，非目录骨架。
- [ ] 问答预演：`qa_prep` 覆盖机制、数据、对照、局限四类追问并给可引用页面。

## 可靠体验（10 分）

- [ ] 可编辑文件（PPTX/HTML/ODP）存在且可打开编辑。
- [ ] 渲染预览 PDF/PNG 存在。
  - 检查：`python3 scripts/main.py check --output /app/output` 无 error。
- [ ] 不同材料长度与重复运行仍产出可用页面。
- [ ] 缺图/低分辨率/公式丢失/字体缺失/模板冲突/导出失败/超时均已识别并写入 `degradations`。

## 生态贡献（10 分）

- [ ] SKILL.md 含用途、适用条件、调用方式、输入输出、异常处理。
- [ ] 脚本仅标准库、相对路径调用；`--help` 可查用法。
  - 检查：`python3 scripts/selftest.py` 中 stdlib 与 `--help` 两项通过。
- [ ] references 说明字段定义与检查清单，他人可复用。

## 收尾必查（确定性）

- [ ] `result.json` 含 `case_id`、契约全字段、`status`/`sources`/`uncertainty`/`pending_human`/`change_log`。
- [ ] `status` 如实为 `complete`/`partial`/`failed`/`insufficient_evidence`/`input_error`。
- [ ] 前 180 秒已落盘骨架，此后仅增量改进。
- [ ] 运行 `python3 scripts/selftest.py` 全部 PASS，同输入两次产物逐字节一致。
