# 自检清单（对应评分维度）

按顺序逐条勾选；每项给出脚本或人工检查方式。分值与原题评分维度一致。

## 实际效果（40 分）

- [ ] 报告目标、听众定位、叙事顺序、逐页重点信息、问答准备形成闭环。
- [ ] 每页恰好一条 `single_claim`，未出现摘要 / Results 逐段搬页。
- [ ] 页面数字、图表引用与源材料一致，未改写事实。
- [ ] `evidence_strength` 与证据强度匹配（结果对比用 high，机制推测用 low）。
- [ ] 覆盖论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染六方面。
- [ ] 每个论断能在源材料定位：`python3 scripts/main.py evidence-check` 无 error。
- [ ] `evidence_map` 每条证据含 locator（≥2 项）+ 逐字 quote。

## 使用增益（25 分）

- [ ] 同一输入、听众、时长设定下比“逐节搬运”减少返工页数。
- [ ] 取舍决策（为什么某图不上页、为什么调整顺序）写入 `narrative` / `change_log`，可复核。
- [ ] 导出修正次数与来源已记录，非口头自述。

## 创新增益（15 分）

- [ ] 听众适配：`audience` 给出先验、关注点、质疑，并影响页数 / 术语深度。
- [ ] 页级规划：`pages` 有唯一主张 + 证据 + 图表绑定，非目录骨架。
- [ ] 问答预演：`qa_prep` 覆盖机制、数据、对照、局限四类追问并给出可引用页面。
- [ ] 片段级溯源：`evidence_map` 以 fragment 为粒度，支持逐字复核（本版差异点）。

## 可靠体验（10 分）

- [ ] 可编辑文件（PPTX/HTML/ODP）存在且可打开编辑。
- [ ] 渲染预览 PDF/PNG 存在：`python3 scripts/main.py validate` 无 error。
- [ ] 不同材料长度与重复运行下仍产出可用页面（确定性、固定输出路径）。
- [ ] 缺图 / 低分辨率 / 公式丢失 / 字体缺失 / 模板冲突 / 导出失败 / 超时均已识别并标注。

## 生态贡献（10 分）

- [ ] SKILL.md 含用途、适用条件、调用方式、输入输出、异常处理。
- [ ] 脚本仅标准库、相对路径调用，`--help` 可查用法。
- [ ] references 说明字段定义与检查清单，他人可复用。

## 收尾必查

- [ ] `result.json` 含 `case_id`、output_contract 全字段、`status`、主产物、`sources`、`uncertainty`、`pending_human`、`change_log`。
- [ ] `status` 如实为 `complete/partial/failed/insufficient_evidence/input_error`，未编造评测分数或运行结果。
- [ ] 前 180 秒已落盘骨架，之后仅为增量改进。
- [ ] 空输入 / 坏 JSON / 缺软件均降级并记录，不抛异常、不编造数据。

## 自动化验证命令（相对 SKILL.md 根目录）

```bash
# 落盘骨架（步骤 2，前 180 秒内执行）
python3 scripts/main.py scaffold --case /input/case.json --task /input/task.md --out /app/output

# 增量合并（推荐 patch-file，避免命令行拆分大 JSON）
python3 scripts/main.py update --result /app/output/result.json --patch-file /app/work/patch.json

# 证据逐字校验：locator 完备性 + quote 是否在 sources.json 抽取文本中
python3 scripts/main.py evidence-check --result /app/output/result.json --sources /app/work/sources.json

# 结构 + 产物存在性校验（可编辑文件与预览）
python3 scripts/main.py validate --result /app/output/result.json --out /app/output

# 离线自测（合成数据，非正式案例）
python3 scripts/selftest.py
```

判读：`evidence-check` / `validate` 输出 `error_count` 为 0 才可将 `status` 置为 `complete`；任何 error 都应先修复或如实降级。
