# 质量检查清单（对应评分维度，逐条可勾选）

交付前按下列清单自检。每条对应题目评分维度，机械可核验的项由 `validate_result.py` 自动执行。

## A. 实际效果（40 分）

- [ ] 已读 case.json 中的任务说明：目标读者、文体、格式约束、材料范围、交付要求。
- [ ] 根节点是材料的真实主题，不是装饰性词或套话。
- [ ] 节点内容忠于材料：每个非根节点都能指回一条 `evidence`（locator + 逐字 quote）。
- [ ] 关系方向正确（`from` 是原因/前提/来源，`to` 是结果/被依赖/结论）。
- [ ] 无材料中不存在的连接；推断类内容已写进 `note` 并标 `note_type: inference`。
- [ ] 关键概念、问题、方法、证据、结论、任务、风险均已覆盖，无重要信息埋没。
- [ ] 层级合理：同层粒度一致、无分类交叉、无节点重复。
- [ ] 产物可打开、可读、可继续编辑：mindmap.md / .mmd / .graphml / .svg 均已生成。

## B. 使用增益（25 分）

- [ ] 已按固定预算执行：总时长 ≤900 秒、模型请求 ≤64 次。
- [ ] 前 3 分钟内已由 `scan_input.py` 落盘 result.json 骨架，后续只做增量改进。
- [ ] 每个大步骤之间运行 `render_mindmap.py` 重新渲染，避免最后一次性返工。
- [ ] 无重复读取同一材料、无重复渲染；一次读入、多次复用。
- [ ] 成本合理：证据逐字摘录由 Agent 完成，布局/格式/校验由脚本完成（减少手工量）。

## C. 创新增益（15 分）

- [ ] 使用固定 JSON spec 分离"抽取/判定"（Agent）与"渲染/校验"（脚本），保证重复运行一致。
- [ ] 关系建模覆盖因果、依赖、对比、时间、引用、支持、冲突七类，超出基础 Agent 的树状罗列。
- [ ] 每条证据区分 fact / summary / inference 三类，避免把概括当事实。
- [ ] 同层粒度控制与节点去重已执行（见 references/mindmap-spec.md 第 1 节）。

## D. 可靠体验（10 分）

- [ ] `scan_input.py` 在空输入/坏 JSON/坏编码时退出码为 0，并写合法 result.json（insufficient_evidence）。
- [ ] 超限文件（>2MB）跳过并记录，不崩溃。
- [ ] 重复运行结果一致（脚本无随机数、布局确定性）。
- [ ] `validate_result.py` 已运行，`hard_issues` 为空；`warnings` 已逐条处置或说明。
- [ ] 权限不足/缺软件时如实写入 `limitations`，不伪造运行状态。

## E. 生态贡献（10 分）

- [ ] SKILL.md 说明齐全：用途、适用条件、调用方式、输入输出、异常处理、时间预算。
- [ ] scripts 只用 Python 标准库，相对路径调用，他人可直接复用。
- [ ] references 讲清节点/关系/证据定义与检查清单。
- [ ] 示意输出均标注"示意"，不冒充正式案例结果。

## 机械自检命令

```bash
python3 scripts/scan_input.py --input /input --output /app/output --work /app/work
python3 scripts/render_mindmap.py --spec /app/work/mindmap_spec.json --work /app/work --output /app/output
python3 scripts/validate_result.py --work /app/work --output /app/output
```
