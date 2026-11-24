# 自检清单（对应评分维度）

按顺序逐条勾选；每项同时给出脚本或人工检查方式。

## 实际效果（40 分）

- [ ] `report_goal`、`audience`、`narrative`、`pages`、`qa_prep` 齐全且形成闭环。
- [ ] 每页恰好一条 `single_claim`，未出现摘要/Results 逐段搬页。
- [ ] 每页数字、图表引用与源材料一致，未改写事实。
- [ ] `evidence_strength` 与证据强度匹配（结果对比用 high，机制推测用 low）。
- [ ] 覆盖论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染六方面。
- [ ] 每个论断能定位到源材料：`python3 scripts/main.py evidence` 无 error。

## 使用增益（25 分）

- [ ] 同一输入、听众、时长设定下比「逐节搬运」减少返工页数。
- [ ] 取舍决策（为什么某图不上页、为什么调整顺序）写入 `narrative` 或 `change_log`，可复核。
- [ ] 导出修正次数与来源已记录，非口头自述。

## 创新增益（15 分）

- [ ] 听众适配：`audience` 给出先验、关注点、质疑，并影响页数/术语深度。
- [ ] 页级规划：`pages` 有唯一主张 + 证据 + 图表绑定，非目录骨架。
- [ ] 问答预演：`qa_prep` 覆盖机制、数据、对照、局限四类追问并给出可引用页面。

## 可靠体验（10 分）

- [ ] 可编辑文件（PPTX/HTML/ODP）存在且可打开编辑。
- [ ] 渲染预览 PDF/PNG 存在：`python3 scripts/main.py validate` 无 error。
- [ ] 不同材料长度与重复运行下仍产出可用页面。
- [ ] 缺图/低分辨率/公式丢失/字体缺失/模板冲突/导出失败/超时均已识别并标注。

## 生态贡献（10 分）

- [ ] SKILL.md 含用途、适用条件、调用方式、输入输出、异常处理。
- [ ] 脚本仅标准库、相对路径调用，`--help` 可查用法。
- [ ] references 说明字段定义与检查清单，他人可复用。

## 收尾必查

- [ ] `result.json` 含 `case_id`、`output_contract` 全字段、`status`/`sources`/`uncertainty`/`change_log`。
- [ ] `status` 如实为 `complete`/`partial`/`failed`，未编造评测分数或运行结果。
- [ ] 前 180 秒已落盘骨架，之后仅为增量改进。
