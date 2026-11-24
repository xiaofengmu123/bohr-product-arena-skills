# 自检清单（对应评分维度）

提交前按顺序勾选；每项附对应脚本或人工检查方式。分值维度与题面一致，本文件只做可操作化，不代替实际运行证据。

## 实际效果（40 分）

- [ ] 报告目标、听众定位、叙事顺序、逐页重点信息、问答准备形成闭环。
- [ ] 每页恰好一条 `single_claim`（非空字符串），未出现摘要/Results 逐段搬页。
- [ ] 页面数字、图表引用与源材料一致，未改写事实。
- [ ] `evidence_strength` 与证据强度匹配：结果对比 claims 用 `high`，机制/局限性推测用 `low` 或 `medium`。
- [ ] 覆盖论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染六个方面。
- [ ] 每个论断能定位：`python3 scripts/main.py` 运行后无 `error` 级 issue（看 `work/validation_report.json`）。
- [ ] `evidence_strength=high` 的主张无“可能/初步/推测”类措辞，或有充分复核说明。

## 使用增益（25 分）

- [ ] 相同输入、听众、时长设定下，比“逐节搬运”减少返工页数。
- [ ] 取舍决策（为什么某图不上页、为什么调整顺序）写入 `narrative.choices/excluded` 或 `change_log`，可复核。
- [ ] 导出修正次数与来源已记录在 `rendering.change_log`，非口头自述。

## 创新增益（15 分）

- [ ] 听众适配：`audience` 给出先验、关注点、质疑，并实际影响页数/术语深度/背景比例。
- [ ] 页级规划：`pages` 有唯一主张 + 证据强度 + 图表绑定 + `source_refs`，非目录骨架。
- [ ] 问答预演：`qa_prep` 覆盖机制、数据、对照、局限四类追问，并给出可引用页面。
- [ ] 中间表示（`plan.json`）把“内容决策”与“契约合规”解耦，脚本只做确定性组装与校验。

## 可靠体验（10 分）

- [ ] 可编辑文件（PPTX/HTML/ODP）存在且可打开编辑：`editable_deck.path` 指向真实文件。
- [ ] 渲染预览 PDF/PNG 存在：`rendering.export_files` 非空且文件真实存在。
- [ ] 空输入、坏 JSON、缺图、重复运行均产出合法 `result.json`：`python3 scripts/selftest.py` 全 PASS。
- [ ] 缺图/低分辨率/公式丢失/字体缺失/模板冲突/导出失败/超时均已识别并标注在 `uncertainty` 或 `pending_human`。

## 生态贡献（10 分）

- [ ] SKILL.md 含用途、适用条件、调用方式、输入输出、异常处理。
- [ ] 脚本仅标准库、相对路径调用，`--help` 可查用法。
- [ ] references 说明字段定义、IR 结构与检查清单，他人可复用。

## 收尾必查

- [ ] `result.json` 含 `case_id`、`output_contract` 全字段、`status/sources/uncertainty/pending_human/change_log/runtime`。
- [ ] `status` 如实为 `complete/partial/insufficient_evidence/input_error/failed`，未编造评测分数或运行结果。
- [ ] 前 180 秒已落盘骨架（步骤 1 即运行 `python3 scripts/main.py`），之后仅为增量改进。
