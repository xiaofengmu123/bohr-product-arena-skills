# 自检清单（对应评分维度）

按顺序逐条勾选；括号内给出脚本或人工检查方式。分值与题面一致。

## 实际效果（40 分）

- [ ] report_goal / audience / narrative / pages / qa_prep 形成闭环（读 result.json）
- [ ] 每页恰好一条 single_claim，无摘要/Results 逐段搬页
- [ ] 页面数字、图表引用与源材料一致，未改写事实
- [ ] evidence_strength / confidence 与证据强度匹配
- [ ] 六方面齐全：主线 / 页结构 / 图表引用 / 讲解逻辑 / 模板约束 / 导出渲染
- [ ] 每个论断可定位：`python3 scripts/evidence_check.py` 无 error

## 使用增益（25 分）

- [ ] 取舍决策（为什么某图不上页、为什么调整顺序）写入 narrative / change_log
- [ ] 导出修正次数与来源已记录，非口头自述
- [ ] 相同输入下比“逐节搬运”减少返工页数

## 创新增益（15 分）

- [ ] audience 给出先验/关注点/质疑，并影响页数与术语深度
- [ ] pages 有唯一主张 + 证据 + 图表绑定，非目录骨架
- [ ] qa_prep 覆盖机制/数据/对照/局限四类追问并给出可引用页面

## 可靠体验（10 分）

- [ ] 可编辑文件（PPTX/HTML/ODP）存在且可打开编辑
- [ ] PDF/PNG 预览存在：`python3 scripts/selftest.py` 无阻断项
- [ ] 不同材料长度与重复运行下仍产出可用页面
- [ ] 缺图/低分辨率/公式丢失/字体缺失/模板冲突/导出失败/超时均已写入 limitations

## 生态贡献（10 分）

- [ ] SKILL.md 含用途/适用条件/调用方式/输入输出/异常处理
- [ ] 脚本仅标准库、相对路径调用，`--help` 可查用法
- [ ] references 说明字段定义与检查清单，他人可复用

## 收尾必查

- [ ] result.json 含 case_id、output_contract 全字段、status / sources / uncertainty / pending_human / change_log
- [ ] status 如实为 complete/partial/insufficient_evidence/input_error/failed，未编造评测分数或运行结果
- [ ] 前 180 秒已落盘骨架，之后仅为增量改进
